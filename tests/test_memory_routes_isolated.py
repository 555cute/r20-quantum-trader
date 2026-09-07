"""Real HTTP/ASGI contract tests, NOT an entire production-app integration test.

app.py imports config (reads .env/secrets), constructs AdminAuthStore, and its
lifespan starts workers. Instead compile its unchanged memory route decorators,
request models, auth functions and session middleware into a fresh FastAPI app.
Only refresh_settings/audit_record are mocked; auth uses a real temporary SQLite
AdminAuthStore. No production config, app import, lifespan or trading services.
"""
import ast
import builtins
from contextvars import ContextVar
import hmac
import io
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI, Header, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field
from r20_backend.admin_auth import AdminAuthStore
from scripts import evolution_shield as shield

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'r20_backend' / 'app.py'
SAFE = '【合理经验】4H多头回踩均线支撑时开多'
OPERATIONS = ('add', 'delete', 'replace', 'toggle', 'rollback')


class MemoryRouteTests(unittest.IsolatedAsyncioTestCase):
    def start_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    async def asyncSetUp(self):
        # Read source only, never execute module-level imports/initializers.
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in {'WORKSPACE_DIR': self.root, 'DATA_DIR': self.root,
                            'STRUCTURED_MEMORY_FILE': self.root / 'authority.json',
                            'AI_MEMORY_MD_FILE': self.root / 'display.md'}.items():
            self.start_patch(patch.object(shield, name, value))
        # No network connections; event-loop local socketpair remains available.
        for target in ('socket.socket.connect', 'socket.socket.connect_ex',
                       'socket.create_connection', 'urllib.request.urlopen'):
            self.start_patch(patch(target, side_effect=AssertionError('network forbidden')))
        def guard(original):
            def checked(file, *args, **kwargs):
                if not isinstance(file, int):
                    path = Path(file).resolve()
                    # Runtime file IO is restricted to synthetic fixtures. Python
                    # import machinery is not replaced and may read library code.
                    if not path.is_relative_to(self.root):
                        raise AssertionError(f'non-fixture file IO: {path}')
                return original(file, *args, **kwargs)
            return checked
        self.start_patch(patch('builtins.open', guard(builtins.open)))
        self.start_patch(patch('io.open', guard(io.open)))
        self.start_patch(patch('sqlite3.connect', guard(sqlite3.connect)))
        self.store = AdminAuthStore(self.root / 'admin.db')
        self.store.create_user('tester', 'SyntheticPassword123', 'admin')
        self.token = self.store.login('tester', 'SyntheticPassword123')['session_token']
        self.app = FastAPI()
        names = {'MemoryItemRequest', 'MemoryUpdateAllRequest',
                 'require_admin_token', 'current_admin', 'require_admin_header',
                 'admin_session_context', '_memory_service_call', 'get_admin_memory',
                 'add_admin_memory_item', 'delete_admin_memory_item',
                 'update_admin_memory_all', 'toggle_admin_memory_lesson',
                 'rollback_admin_memory_lessons'}
        nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual({n.name for n in nodes}, names)
        self.scope = dict(app=self.app, BaseModel=BaseModel, Field=Field, Header=Header,
                          HTTPException=HTTPException, Request=Request, Any=Any, hmac=hmac,
                          REQUEST_SESSION=ContextVar('isolated_session', default=''),
                          admin_auth=self.store, settings=SimpleNamespace(admin_token='', setup_token=''),
                          refresh_settings=Mock(), audit_record=Mock())
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.scope)
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url='http://isolated.test')
        self.addAsyncCleanup(self.client.aclose)
        shield.STRUCTURED_MEMORY_FILE.write_text('[]', encoding="utf-8")
        shield.admin_mutate('add', texts=[SAFE], expected_version=shield.read_memory_snapshot()['version'])
        self.lesson_id = shield.load_structured_memory()[0]['id']

    def snapshot(self):
        p = shield.STRUCTURED_MEMORY_FILE
        return p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino

    async def get_view(self):
        response = await self.client.get('/api/v1/admin/memory', headers={'X-R20-Session': self.token})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('no-store', response.headers['cache-control'])
        return response.json()

    async def mutate(self, operation, version=None, token=True):
        base = '/api/v1/admin/memory'
        method, url, body = {
            'add': ('POST', base, {'text': SAFE + '等待量能确认'}),
            'replace': ('PUT', base, {'items': [SAFE + '等待量能确认']}),
            'delete': ('DELETE', base + '/0', None),
            'toggle': ('POST', base + '/toggle/' + self.lesson_id, None),
            'rollback': ('POST', base + '/rollback', None),
        }[operation]
        params = {'lesson_id': self.lesson_id} if operation == 'delete' else {}
        if version is not None:
            if body is not None:
                body['expected_version'] = version
            else:
                params['expected_version'] = version
        return await self.client.request(method, url, json=body, params=params,
                                         headers={'X-R20-Session': self.token} if token else {})

    async def test_empty_get_is_pure_and_does_not_fallback(self):
        shield.STRUCTURED_MEMORY_FILE.write_text('[]', encoding="utf-8")
        shield.AI_MEMORY_MD_FILE.write_text('- stale legacy', encoding="utf-8")
        before = self.snapshot()
        def memory_files():
            # SQLite session validation can create WAL/SHM; exclude only auth DB files.
            return {p for p in self.root.iterdir() if p.name not in {'admin.db', 'admin.db-wal', 'admin.db-shm'}}
        files = memory_files()
        with patch.object(shield, '_commit', side_effect=AssertionError('GET wrote memory')), patch.object(shield, '_memory_lock', side_effect=AssertionError('GET took write lock')):
            first = await self.get_view()
            second = await self.get_view()
        self.assertEqual(first, second)
        self.assertEqual(first['items'], [])
        self.assertEqual(first['structured_lessons'], [])
        self.assertEqual(first['raw'], '')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(memory_files(), files)
        self.scope['audit_record'].assert_not_called()
        # Auth may update last_seen_at in the temporary DB; pure read means memory.

    async def test_get_rejects_anonymous_and_invalid_session(self):
        for headers in ({}, {'X-R20-Session': 'invalid'}):
            response = await self.client.get('/api/v1/admin/memory', headers=headers)
            self.assertEqual(response.status_code, 401)

    async def test_revoked_session_cannot_write(self):
        self.store.logout(self.token)
        before = self.snapshot()
        response = await self.mutate('rollback', shield.read_memory_snapshot()['version'])
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.snapshot(), before)


def contract_case(operation, case):
    async def test(self):
        version = (await self.get_view())['version']
        if case == 'stale':
            shield.toggle_lesson(self.lesson_id, expected_version=version)
        before = self.snapshot()
        if case == 'anonymous':
            response = await self.mutate(operation, version, token=False)
            expected = 401
        elif case == 'missing':
            response = await self.mutate(operation)
            expected = 428
        else:
            response = await self.mutate(operation, version)
            expected = 409 if case == 'stale' else 200
        self.assertEqual(response.status_code, expected, response.text)
        if expected != 200:
            self.assertEqual(self.snapshot(), before)
            self.scope['audit_record'].assert_not_called()
        else:
            current = await self.get_view()
            self.assertNotEqual(current['version'], version)
            self.assertNotEqual(self.snapshot(), before)
            self.scope['audit_record'].assert_called_once()
            # A replay of the same successful request must not overwrite data.
            after = self.snapshot()
            replay = await self.mutate(operation, version)
            self.assertEqual(replay.status_code, 409)
            self.assertEqual(self.snapshot(), after)
    return test


for operation in OPERATIONS:
    for case in ('anonymous', 'missing', 'stale', 'success'):
        setattr(MemoryRouteTests, f'test_{operation}_{case}', contract_case(operation, case))


if __name__ == '__main__':
    unittest.main()

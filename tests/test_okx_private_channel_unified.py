"""Private consumers share the actual V5 HTTP boundary, never mocked consumers."""
import base64
import hashlib
import hmac
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import r20_backend.app as api
import r20_backend.config as config
import r20_backend.okx_client as client_module
import r20_backend.okx_trade_service as trade
import r20_gateway.secrets as secrets
from r20_backend.admin_auth import AdminAuthStore
from scripts import okx_rest as rest, okx_runtime as runtime
from tests.config_sandbox import isolate_config


class UnifiedPrivateChannelTests(unittest.TestCase):
    def setUp(self):
        self.root = isolate_config(self)
        self.env_file = self.root / '.env'
        self.patch(runtime, 'ROOT', self.root)
        self.patch(config, 'ROOT', self.root)
        self.patch(secrets, 'load_secrets', lambda: {})
        self.patch(config, 'load_encrypted_secrets', lambda: None)
        self.patch(runtime, '_FROZEN_ENVIRONMENT', None)
        self.patch(trade, '_INTENTS', {})
        self.patch(api, 'audit_record', lambda *a, **k: None)
        p = patch.dict(os.environ, {}, clear=True)
        p.start(); self.addCleanup(p.stop)
        before = vars(config.settings).copy()
        self.addCleanup(lambda: vars(config.settings).update(before))
        auth = AdminAuthStore(self.root / 'auth.db')
        auth.initialize_from_legacy('FakeAdminPassword123')
        self.patch(api, 'admin_auth', auth)
        self.client = TestClient(api.app)
        self.addCleanup(self.client.close)
        self.configure({})
        login = self.client.post('/api/v1/admin/auth/login', json={
            'username': 'admin', 'password': 'FakeAdminPassword123'})
        self.assertEqual(login.status_code, 200, login.text)
        self.headers = {'X-R20-Session': login.json()['session_token']}
        self.requests = []
        self.net = self.patch(rest, 'urlopen', self.http)
        self.public = self.patch(client_module, 'urlopen', side_effect=AssertionError('private leak'))
        self.consumer = client_module.OKXClient()  # deliberately survives hot switches

    def patch(self, module, name, value=None, **kwargs):
        p = patch.object(module, name, **kwargs) if kwargs else patch.object(module, name, value)
        result = p.start(); self.addCleanup(p.stop)
        return result

    def configure(self, keys):
        self.env_file.write_text('\n'.join(f'{k}={v}' for k, v in {
            'R20_MANUAL_CLOSE_ENABLED': '1', **keys}.items()))

    @staticmethod
    def keys(mode='demo', tag='A', legacy=False):
        prefix = 'OKX' if legacy else 'OKX_' + mode.upper()
        return {'R20_OKX_ENV': mode, **{
            prefix + '_' + field: tag + field
            for field in ('API_KEY', 'SECRET_KEY', 'PASSPHRASE')}}

    def http(self, request, timeout=None):
        self.requests.append(request)
        return io.BytesIO(b'{"code":"0","data":[]}')

    def assert_signed(self, request, env):
        headers = dict((k.lower(), v) for k, v in request.header_items())
        split = urlsplit(request.full_url)
        self.assertEqual(split.netloc, 'www.okx.com')
        path = split.path + ('?' + split.query if split.query else '')
        message = headers['ok-access-timestamp'] + request.method + path + (request.data or b'').decode()
        expected = base64.b64encode(hmac.new(env.secret_key.encode(), message.encode(), hashlib.sha256).digest()).decode()
        self.assertEqual(headers['ok-access-sign'], expected)
        self.assertEqual(headers['ok-access-key'], env.api_key)
        self.assertEqual(headers['ok-access-passphrase'], env.passphrase)
        self.assertEqual(headers.get('x-simulated-trading'), '1' if env.simulated else None)

    def test_missing_partial_groups_fail_closed_all_entrypoints(self):
        for keys in ({}, {'OKX_DEMO_API_KEY': 'partial'},
                     {**self.keys(legacy=True), 'OKX_DEMO_API_KEY': 'partial'}):
            self.configure(keys)
            for call in (self.consumer.balance, self.consumer.positions,
                         lambda: self.consumer.close_position('BTC-USDT-SWAP', 'long'),
                         trade.account_snapshot, lambda: trade.fast_close_confirmed('unused', 'unused')):
                with self.assertRaises(rest.OKXNotConfigured):
                    call()
            for path in ('/api/v1/account/positions', '/api/v1/admin/okx/account-snapshot'):
                response = self.client.get(path, headers=self.headers)
                self.assertEqual(response.status_code, 503, response.text)
            response = self.client.post('/api/v1/admin/positions/close', headers=self.headers, json={
                'close_token': 'x' * 32, 'confirmation': 'CLOSE DEMO BTC-USDT-SWAP LONG 1',
                'admin_password': 'FakeAdminPassword123'})
            self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.requests, [])
        self.public.assert_not_called()

    def test_hot_switch_live_demo_legacy_share_real_signature(self):
        for mode, tag, legacy in (('demo', 'A', False), ('live', 'B', False), ('demo', 'C', True)):
            self.configure(self.keys(mode, tag, legacy))
            env = runtime.current_environment()
            self.requests.clear()
            self.consumer.balance(); self.consumer.positions()
            self.consumer.close_position('BTC-USDT-SWAP', 'long')
            trade.account_snapshot()
            response = self.client.get('/api/v1/account/positions', headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(self.requests), 6)
            for request in self.requests:
                self.assert_signed(request, env)
            self.assertIs(json.loads(self.requests[2].data)['autoCxl'], True)
        self.public.assert_not_called()

    def test_snapshot_and_entire_close_capture_one_environment(self):
        self.configure(self.keys())
        env = runtime.current_environment()
        position = {'instId': 'BTC-USDT-SWAP', 'posSide': 'long', 'pos': '1', 'posId': 'P', 'mgnMode': 'cross'}
        closed = False
        def http(request, timeout=None):
            nonlocal closed
            self.requests.append(request)
            self.assert_signed(request, env)
            # Rotate mode AND all credentials after every request. Consumers
            # must retain their immutable env throughout this operation.
            self.configure(self.keys('live', 'ROTATED'))
            path = urlsplit(request.full_url).path
            if path.endswith('/positions'):
                rows = [] if closed else [position]
            elif path.endswith('/orders-pending'):
                rows = [{'ordId': 'O', 'posSide': 'long'}]
            elif path.endswith('/orders-algo-pending'):
                rows = [{'algoId': 'A', 'instId': position['instId'], 'posSide': 'long'}]
            else:
                rows = [{'sCode': '0'}]
            if path.endswith('/close-position'):
                self.assertIs(json.loads(request.data)['autoCxl'], True)
                closed = True
            if path.endswith('/cancel-algos'):
                self.assertEqual(json.loads(request.data), [{'algoId': 'A', 'instId': position['instId']}])
            return io.BytesIO(json.dumps({'code': '0', 'data': rows}).encode())
        with patch.object(rest, 'urlopen', side_effect=http), patch.object(trade.time, 'sleep'):
            snapshot = trade.account_snapshot()
            p = snapshot['positions'][0]
            self.configure(self.keys())
            result = trade.fast_close_confirmed(p['close_token'], p['close_confirmation'])
            self.assertEqual(result['status'], 'confirmed_closed')
            self.assertEqual(result['canceled_entry_orders'], ['O', 'algo:A'])
            count = len(self.requests)
            self.configure(self.keys())
            with self.assertRaises(ValueError):
                trade.fast_close_confirmed(p['close_token'], p['close_confirmation'])
            self.assertEqual(len(self.requests), count)
        self.public.assert_not_called()

    def test_public_reads_unsigned_without_key(self):
        with patch.object(client_module, 'urlopen', side_effect=lambda req, **kw: (
                self.requests.append(req) or io.BytesIO(b'{"code":"0","data":[]}'))):
            self.consumer.ticker('BTC-USDT-SWAP')
            self.consumer.candles('BTC-USDT-SWAP')
            self.consumer.instruments()
        self.assertEqual(len(self.requests), 3)
        for request in self.requests:
            self.assertFalse(any(k.lower().startswith('ok-access') for k, _ in request.header_items()))

    def test_business_failure_preserved_by_delegating_facade(self):
        self.configure(self.keys())
        with patch.object(rest, 'urlopen', return_value=io.BytesIO(
                b'{"code":"0","data":[{"sCode":"51000","sMsg":"rejected"}]}')):
            with self.assertRaisesRegex(RuntimeError, '51000.*rejected'):
                trade._request('POST', '/api/v5/trade/cancel-order', {'instId': 'BTC-USDT-SWAP', 'ordId': 'O'})


if __name__ == '__main__':
    unittest.main()

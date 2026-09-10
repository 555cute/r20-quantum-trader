"""Readiness at the real TestClient boundary; fake config and zero external IO."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import r20_backend.app as api
import r20_backend.config as config
import r20_backend.okx_trade_service as trade
import r20_gateway.secrets as secrets
import scripts.okx_runtime as runtime
import scripts.okx_rest as rest
from r20_backend.admin_auth import AdminAuthStore
from tests.config_sandbox import isolate_config


class OKXReadinessContractTests(unittest.TestCase):
    def setUp(self):
        root = isolate_config(self)
        self.root = root
        self.env_file = root / '.env'
        self.patch(runtime, 'ROOT', root)
        self.patch(config, 'ROOT', root)
        self.patch(secrets, 'load_secrets', lambda: {})
        self.patch(config, 'load_encrypted_secrets', lambda: None)
        self.patch(api, 'settings', config.settings)
        self.patch(api, 'audit_record', lambda *args, **kwargs: None)
        self.patch(trade, '_INTENTS', {})
        self.patch(runtime, '_FROZEN_ENVIRONMENT', None)
        clean_env = patch.dict(os.environ, {'R20_MANUAL_CLOSE_ENABLED': '1'}, clear=True)
        clean_env.start()
        self.addCleanup(clean_env.stop)
        settings_before = vars(config.settings).copy()
        self.addCleanup(lambda: vars(config.settings).update(settings_before))
        self.http = self.patch(rest, 'urlopen', side_effect=AssertionError('private HTTP forbidden'))
        self.http_std = self.patch(trade.urllib.request, 'urlopen', side_effect=AssertionError('HTTP forbidden'))
        self.patch('socket', 'create_connection', side_effect=AssertionError('external connection forbidden'))
        auth = AdminAuthStore(root / 'auth.db')
        auth.initialize_from_legacy('FakeAdminPassword123')
        self.patch(api, 'admin_auth', auth)
        self.client = TestClient(api.app)  # no lifespan/background workers
        self.addCleanup(self.client.close)
        self.configure({})
        result = self.client.post('/api/v1/admin/auth/login', json={
            'username': 'admin', 'password': 'FakeAdminPassword123'})
        self.assertEqual(result.status_code, 200, result.text)
        self.headers = {'X-R20-Session': result.json()['session_token']}

    def patch(self, module, name, value=None, **kwargs):
        if isinstance(module, str):
            import importlib
            module = importlib.import_module(module)
        p = patch.object(module, name, **kwargs) if kwargs else patch.object(module, name, value)
        result = p.start()
        self.addCleanup(p.stop)
        return result

    def configure(self, values):
        self.env_file.write_text('\n'.join(f'{k}={v}' for k, v in values.items()), encoding='utf-8')

    @staticmethod
    def trio(prefix, tag='FAKE'):
        return {f'{prefix}_{key}': f'{tag}-{key}' for key in ('API_KEY', 'SECRET_KEY', 'PASSPHRASE')}

    def readiness(self):
        response = self.client.get('/api/v1/admin/okx/runtime', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('no-store', response.headers['cache-control'])
        self.http.assert_not_called()
        self.http_std.assert_not_called()
        return response.json()

    def test_demo_live_legacy_and_partial_use_same_selected_group(self):
        for mode in ('demo', 'live'):
            cases = [({}, False), (self.trio('OKX_' + mode.upper()), True),
                     (self.trio('OKX'), True),
                     ({**self.trio('OKX'), f'OKX_{mode.upper()}_API_KEY': 'partial'}, False),
                     ({**self.trio('OKX_' + mode.upper()), **self.trio('OKX')}, True)]
            for keys, ready in cases:
                with self.subTest(mode=mode, keys=list(keys)):
                    self.configure({'R20_OKX_ENV': mode, **keys})
                    selected = runtime.selected_environment()
                    state = self.readiness()
                    self.assertEqual(selected.configured, ready)
                    self.assertEqual(state['mode_configured'], selected.configured)
                    self.assertEqual(state['status'], 'READY' if ready else 'NOT_READY')
                    self.assertEqual(state['fingerprint'], selected.fingerprint)
                    self.assertEqual(state['environment'], selected.mode)
                    self.assertEqual('not_ready_reason' in state, not ready)

    def test_missing_or_partial_close_returns_503_without_consuming_intent(self):
        for mode in ('demo', 'live'):
            for keys in ({}, {**self.trio('OKX'), f'OKX_{mode.upper()}_SECRET_KEY': 'partial'}):
                with self.subTest(mode=mode, partial=bool(keys)):
                    self.configure({'R20_OKX_ENV': mode, **keys})
                    token = 'fake-close-intent-token-1234567890'
                    intent = {'sentinel': 'must remain untouched'}
                    trade._INTENTS[token] = intent
                    response = self.client.post('/api/v1/admin/positions/close', headers=self.headers,
                        json={'close_token': token, 'confirmation': 'CLOSE DEMO BTC-USDT-SWAP LONG 1',
                              'admin_password': 'FakeAdminPassword123'})
                    self.assertEqual(response.status_code, 503, response.text)
                    self.assertIn('API Key', response.json()['detail'])
                    self.assertIs(trade._INTENTS[token], intent)
                    snapshot = self.client.get('/api/v1/admin/okx/account-snapshot', headers=self.headers)
                    self.assertEqual(snapshot.status_code, 503, snapshot.text)
                    self.http.assert_not_called()
                    self.http_std.assert_not_called()

    def test_partial_group_never_borrows_legacy_fields(self):
        for mode in ('demo', 'live'):
            for field in ('API_KEY', 'SECRET_KEY', 'PASSPHRASE'):
                values = {'R20_OKX_ENV': mode, **self.trio('OKX'),
                          f'OKX_{mode.upper()}_{field}': 'partial'}
                selected = runtime.selected_environment(values)
                self.assertFalse(selected.configured)
                self.assertNotIn('FAKE', selected.api_key + selected.secret_key + selected.passphrase)
        self.assertFalse(runtime.selected_environment({}).configured)

    def test_no_cached_readiness_after_group_or_secret_change(self):
        keys = self.trio('OKX_DEMO')
        self.configure({'R20_OKX_ENV': 'demo', **keys})
        ready = self.readiness()
        self.assertTrue(ready['mode_configured'])
        # Same API key and fingerprint, but secret removed: must immediately block.
        self.configure({'R20_OKX_ENV': 'demo', **keys, 'OKX_DEMO_SECRET_KEY': ''})
        missing = self.readiness()
        self.assertEqual(ready['fingerprint'], missing['fingerprint'])
        self.assertFalse(missing['mode_configured'])
        self.configure({'R20_OKX_ENV': 'live', **self.trio('OKX_LIVE', 'NEW')})
        self.assertEqual(self.readiness()['environment'], 'live')
        self.configure({'R20_OKX_ENV': 'live', **self.trio('OKX', 'LEGACY')})
        self.assertTrue(self.readiness()['mode_configured'])

    def test_frozen_group_wins_over_changed_configuration(self):
        frozen = runtime.freeze_environment({'R20_OKX_ENV': 'demo', **self.trio('OKX')})
        self.configure({'R20_OKX_ENV': 'live'})
        state = self.readiness()
        self.assertEqual(state['environment'], frozen.mode)
        self.assertEqual(state['mode_configured'], frozen.configured)
        self.assertEqual(state['fingerprint'], frozen.fingerprint)
        runtime.unfreeze_environment()
        self.assertEqual(self.readiness()['status'], 'NOT_READY')


if __name__ == '__main__':
    unittest.main()

from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import r20_backend.app as app_module
import r20_backend.backup_store as backups
import scripts.prompt_library as prompts
from r20_backend.admin_auth import AdminAuthStore


class ControlPlaneV2ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); root=Path(self.temp.name)
        self.original_auth=app_module.admin_auth; self.original_prompt=prompts.LIBRARY_FILE; self.original_backup=backups.CONFIG_FILE
        app_module.admin_auth=AdminAuthStore(root/"admin.db"); app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        prompts.LIBRARY_FILE=root/"prompt_library.json"; backups.CONFIG_FILE=root/"backup_methods.json"
        self.client=TestClient(app_module.app)
        response=self.client.post('/api/v1/admin/auth/login',json={'username':'admin','password':'InitialAdmin123456'})
        self.headers={'X-R20-Session':response.json()['session_token']}

    def tearDown(self):
        app_module.admin_auth=self.original_auth; prompts.LIBRARY_FILE=self.original_prompt; backups.CONFIG_FILE=self.original_backup; self.temp.cleanup()

    def test_snapshot_missing_key_is_503(self):
        from scripts.okx_rest import OKXNotConfigured
        message = "NOT READY: 请在后台配置 DEMO API Key"
        with patch.object(app_module, 'okx_account_snapshot', side_effect=OKXNotConfigured(message)):
            response = self.client.get('/api/v1/admin/okx/account-snapshot', headers=self.headers)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()['detail'], message)

    def test_notification_diagnose_does_not_send(self):
        with patch.object(app_module,'diagnose_channel',return_value={'status':'ready','detail':'ok'}) as diagnose, patch.object(app_module,'test_channel') as send:
            response=self.client.post('/api/v1/admin/notifications/diagnose',headers=self.headers,json={'channel':'telegram'})
        self.assertEqual(response.status_code,200,response.text); self.assertFalse(response.json()['sent']); diagnose.assert_called_once(); send.assert_not_called()

    def test_notification_send_requires_exact_confirmation(self):
        response=self.client.post('/api/v1/admin/notifications/test',headers=self.headers,json={'channel':'telegram','confirmation':'SEND TEST'})
        self.assertEqual(response.status_code,400)

    def test_simple_backup_local_roundtrip_preserves_advanced(self):
        job=backups._default_job(); job['compression_level']=9; job['exclude'].append('private/**'); backups.save_backup_config({'version':2,'jobs':[job]})
        response=self.client.put('/api/v1/admin/backups/simple',headers=self.headers,json={'enabled':True,'schedule_time':'03:15','destination':'local','retention':5})
        self.assertEqual(response.status_code,200,response.text)
        saved=backups.get_job('nightly-default'); self.assertEqual(saved['compression_level'],9); self.assertIn('private/**',saved['exclude']); self.assertEqual(saved['schedule_times'],['03:15'])
        target=next(x for x in saved['targets'] if x['enabled']); self.assertEqual((target['type'],target['retention']),('local',5))

    def test_prompt_library_returns_module_views(self):
        response=self.client.get('/api/v1/admin/prompt-library',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text); payload=response.json()
        self.assertIn('pipelines',payload); self.assertTrue(payload['profiles'][0]['pipeline_views']['trading_system'])

    def test_fast_close_empty_password_returns_readable_422(self):
        response=self.client.post('/api/v1/admin/positions/close',headers=self.headers,json={'close_token':'x'*30,'admin_password':'','confirmation':'CLOSE DEMO BTC-USDT-SWAP LONG 1'})
        self.assertEqual(response.status_code,422); self.assertEqual(response.json()['detail'][0]['loc'][-1],'admin_password')


if __name__=='__main__': unittest.main()

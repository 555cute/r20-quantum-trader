"""多所凭证端点与注册表档位/凭证函数测试（凭证写口全部打桩，零触碰生产密钥库）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import r20_backend.app as app_module
from r20_backend import exchanges as ex
from r20_backend.admin_auth import AdminAuthStore


class MultiExchangeApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = app_module.admin_auth
        app_module.admin_auth = AdminAuthStore(Path(self.temp.name) / "admin.db")
        app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        self.client = TestClient(app_module.app)
        self._patches = [
            patch.object(app_module, "require_admin_header", lambda t=None: None),
            patch.object(app_module, "require_superadmin", lambda s=None: {"username": "tester"}),
            patch.object(app_module, "save_secrets", lambda values: None),
            patch.object(app_module, "audit_record", lambda *a, **k: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        app_module.admin_auth = self.original
        self.temp.cleanup()

    def test_status_never_leaks_secret_values(self):
        with patch.object(ex, "venue_credentials", lambda v: ("AK", "SK") if v == "gate" else ("", "")), \
                patch.object(ex, "venue_testnet_enabled", lambda v: v == "binance"):
            r = self.client.get("/api/v1/admin/multi-exchange")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.text
        self.assertNotIn("\"AK\"", body)   # 状态接口绝不回显密钥值
        data = r.json()
        self.assertTrue(data["venues"]["gate"]["has_api_key"])
        self.assertTrue(data["venues"]["gate"]["has_secret"])
        self.assertFalse(data["venues"]["binance"]["has_api_key"])
        self.assertTrue(data["venues"]["binance"]["testnet"])
        self.assertNotIn("okx", data["venues"])  # OKX 凭证面板独立存在，避免双写入口

    def test_put_stores_secrets_and_flips_testnet(self):
        saved: dict = {}
        env_writes: dict = {}
        cleared = {"n": 0}
        with patch.object(app_module, "save_secrets", lambda v: saved.update(v)), \
                patch.object(app_module, "update_env", lambda v: env_writes.update(v)), \
                patch.object(ex, "clear_instances", lambda: cleared.__setitem__("n", cleared["n"] + 1)), \
                patch.object(app_module, "refresh_settings", lambda: None):
            r = self.client.put("/api/v1/admin/multi-exchange", json={
                "binance_api_key": "BN_KEY ", "binance_secret_key": "BN_SEC",
                "gate_testnet": False, "gate_api_key": "   "})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(saved, {"BINANCE_API_KEY": "BN_KEY", "BINANCE_SECRET_KEY": "BN_SEC"})
        self.assertEqual(env_writes, {"R20_GATE_TESTNET": "0"})   # 空串=不保存；未提供=不写
        self.assertEqual(cleared["n"], 1)

    def test_put_requires_real_superadmin_session(self):
        for p in self._patches[1:2]:  # 停掉 require_superadmin 打桩，走真实 RBAC
            p.stop()
        r = self.client.put("/api/v1/admin/multi-exchange", json={"gate_testnet": True})
        self.assertIn(r.status_code, (401, 403))


class RegistryTestnetAndCredentialsTests(unittest.TestCase):
    def tearDown(self):
        ex.clear_instances()

    def test_testnet_flag_switches_base_url(self):
        with patch.dict(os.environ, {"R20_BINANCE_TESTNET": "1"}):
            ex.clear_instances()
            self.assertEqual(ex.get_adapter("binance").base_url, "https://demo-fapi.binance.com")
        with patch.dict(os.environ, {"R20_BINANCE_TESTNET": "0"}):
            ex.clear_instances()
            self.assertEqual(ex.get_adapter("binance").base_url, "https://fapi.binance.com")
        # gate 未声明开关时保持实盘
        ex.clear_instances()
        self.assertEqual(ex.get_adapter("gate").base_url, "https://api.gateio.ws")

    def test_venue_credentials_read_from_secret_store(self):
        import r20_gateway.secrets as gw_secrets
        with patch.object(gw_secrets, "load_secrets",
                          lambda: {"GATE_API_KEY": "gk", "GATE_SECRET_KEY": "gs"}):
            self.assertEqual(ex.venue_credentials("gate"), ("gk", "gs"))
            self.assertEqual(ex.venue_credentials("binance"), ("", ""))

    def test_secret_keys_whitelist_contains_venue_keys(self):
        from r20_gateway.secrets import SECRET_KEYS
        for k in ("BINANCE_API_KEY", "BINANCE_SECRET_KEY", "GATE_API_KEY", "GATE_SECRET_KEY"):
            self.assertIn(k, SECRET_KEYS)

    def test_managed_env_keys_registered(self):
        from r20_backend.settings_store import MANAGED_KEYS
        self.assertIn("R20_BINANCE_TESTNET", MANAGED_KEYS)
        self.assertIn("R20_GATE_TESTNET", MANAGED_KEYS)


if __name__ == "__main__":
    unittest.main()

"""多所凭证端点与注册表档位/凭证函数测试（凭证写口全部打桩，零触碰生产密钥库）。"""
from __future__ import annotations

import json
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

    def test_gate_execution_toggle_requires_exact_phrase(self):
        # 错误短语 → 400 且完全不落 env
        env_writes: dict = {}
        with patch.object(app_module, "update_env", lambda v: env_writes.update(v)), \
                patch.object(app_module, "save_secrets", lambda v: None), \
                patch.object(app_module, "refresh_settings", lambda: None):
            r = self.client.put("/api/v1/admin/multi-exchange", json={
                "gate_execution": True, "confirmation": "open gate"})
            self.assertEqual(r.status_code, 400)
            self.assertEqual(env_writes, {})
            # 正确短语 → 落 env
            r2 = self.client.put("/api/v1/admin/multi-exchange", json={
                "gate_execution": True, "confirmation": "OPEN GATE EXECUTION"})
            self.assertEqual(r2.status_code, 200, r2.text)
            self.assertEqual(env_writes, {"R20_GATE_EXECUTION": "1"})

    def test_binance_execution_toggle_requires_exact_phrase(self):
        # US-005: 币安开闸必须精确短语 OPEN BINANCE EXECUTION
        env_writes: dict = {}
        with patch.object(app_module, "update_env", lambda v: env_writes.update(v)), \
                patch.object(app_module, "save_secrets", lambda v: None), \
                patch.object(app_module, "refresh_settings", lambda: None):
            r = self.client.put("/api/v1/admin/multi-exchange", json={
                "binance_execution": True, "confirmation": "open binance"})
            self.assertEqual(r.status_code, 400)
            self.assertEqual(env_writes, {})

            r2 = self.client.put("/api/v1/admin/multi-exchange", json={
                "binance_execution": True, "confirmation": "OPEN BINANCE EXECUTION"})
            self.assertEqual(r2.status_code, 200, r2.text)
            self.assertEqual(env_writes, {"R20_BINANCE_EXECUTION": "1"})

    def test_execution_status_field_exposed_in_get(self):
        with patch.object(ex, "venue_credentials", lambda v: ("", "")), \
                patch.object(ex, "venue_testnet_enabled", lambda v: False), \
                patch.object(ex, "execution_open", lambda v: v == "gate"):
            data = self.client.get("/api/v1/admin/multi-exchange").json()
        self.assertTrue(data["venues"]["gate"]["execution_open"])
        self.assertFalse(data["venues"]["binance"]["execution_open"])


class MultiExchangeRbacTests(MultiExchangeApiTests):
    """RBAC 真实路径（不打桩）：读端点须登录态、写端点须超管会话。

    注：原 Gate 试验田状态端点（lab-status）已随试验田整体退役删除，本类只保留
    仍然存在的两个端点的鉴权回归；逐条继承父类用例属有意设计（同一 setUp 复用）。
    """

    def test_get_requires_admin_header(self):
        for p in self._patches[:1]:  # 停掉 require_admin_header 打桩走真实 RBAC
            p.stop()
        r = self.client.get("/api/v1/admin/multi-exchange")
        self.assertIn(r.status_code, (401, 403))

    def test_put_requires_real_superadmin_session(self):
        for p in self._patches[1:2]:  # 停掉 require_superadmin 打桩，走真实 RBAC
            p.stop()
        r = self.client.put("/api/v1/admin/multi-exchange", json={"gate_testnet": True})
        self.assertIn(r.status_code, (401, 403))


class RegistryTestnetAndCredentialsTests(unittest.TestCase):
    def setUp(self):
        # 封闭三律：宿主 .env 的 R20_GATE_TESTNET=1 会在 import 期进 os.environ，
        # 「未声明开关时保持实盘」的断言必须排除 ambient 旗标（各用例自设旗标用 patch.dict 不受影响）。
        self._flag_guard = patch.dict(os.environ, {
            k: "0" for k in list(os.environ)
            if k.startswith(("R20_BINANCE_TESTNET", "R20_GATE_TESTNET",
                             "R20_OKX_ENV", "R20_OKX_TESTNET"))}, clear=False)
        self._flag_guard.start()
        self.addCleanup(self._flag_guard.stop)

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
        for k in (
            "BINANCE_API_KEY", "BINANCE_SECRET_KEY",
            "BINANCE_LIVE_API_KEY", "BINANCE_LIVE_SECRET_KEY",
            "BINANCE_DEMO_API_KEY", "BINANCE_DEMO_SECRET_KEY",
            "GATE_API_KEY", "GATE_SECRET_KEY",
            "GATE_LIVE_API_KEY", "GATE_LIVE_SECRET_KEY",
            "GATE_DEMO_API_KEY", "GATE_DEMO_SECRET_KEY",
            "OKX_LIVE_API_KEY", "OKX_LIVE_SECRET_KEY", "OKX_LIVE_PASSPHRASE",
            "OKX_DEMO_API_KEY", "OKX_DEMO_SECRET_KEY", "OKX_DEMO_PASSPHRASE",
        ):
            self.assertIn(k, SECRET_KEYS)

    def test_managed_env_keys_registered(self):
        from r20_backend.settings_store import MANAGED_KEYS
        for k in (
            "R20_BINANCE_TESTNET", "R20_GATE_TESTNET",
            "BINANCE_LIVE_API_KEY", "BINANCE_DEMO_API_KEY",
            "GATE_LIVE_API_KEY", "GATE_DEMO_API_KEY",
        ):
            self.assertIn(k, MANAGED_KEYS)


class SixAccountCredentialsAndDiagnosticsTests(MultiExchangeApiTests):
    """US-003：三所 6 账户独立凭证加密存储与诊断接口测试（封闭三律：零出网）。"""

    def test_put_stores_independent_6_account_credentials(self):
        saved: dict = {}
        cleared = {"n": 0}
        with patch.object(app_module, "save_secrets", lambda v: saved.update(v)), \
                patch.object(ex, "clear_instances", lambda: cleared.__setitem__("n", cleared["n"] + 1)), \
                patch.object(app_module, "update_env", lambda v: None), \
                patch.object(app_module, "refresh_settings", lambda: None):
            r = self.client.put("/api/v1/admin/multi-exchange", json={
                "binance_live_api_key": "BN_L_K",
                "binance_live_secret_key": "BN_L_S",
                "binance_demo_api_key": "BN_D_K",
                "binance_demo_secret_key": "BN_D_S",
                "gate_live_api_key": "GT_L_K",
                "gate_live_secret_key": "GT_L_S",
                "gate_demo_api_key": "GT_D_K",
                "gate_demo_secret_key": "GT_D_S",
                "okx_live_api_key": "OKX_L_K",
                "okx_live_secret_key": "OKX_L_S",
                "okx_live_passphrase": "OKX_L_P",
                "okx_demo_api_key": "OKX_D_K",
                "okx_demo_secret_key": "OKX_D_S",
                "okx_demo_passphrase": "OKX_D_P",
            })
        self.assertEqual(r.status_code, 200, r.text)
        expected_keys = {
            "BINANCE_LIVE_API_KEY": "BN_L_K",
            "BINANCE_LIVE_SECRET_KEY": "BN_L_S",
            "BINANCE_DEMO_API_KEY": "BN_D_K",
            "BINANCE_DEMO_SECRET_KEY": "BN_D_S",
            "GATE_LIVE_API_KEY": "GT_L_K",
            "GATE_LIVE_SECRET_KEY": "GT_L_S",
            "GATE_DEMO_API_KEY": "GT_D_K",
            "GATE_DEMO_SECRET_KEY": "GT_D_S",
            "OKX_LIVE_API_KEY": "OKX_L_K",
            "OKX_LIVE_SECRET_KEY": "OKX_L_S",
            "OKX_LIVE_PASSPHRASE": "OKX_L_P",
            "OKX_DEMO_API_KEY": "OKX_D_K",
            "OKX_DEMO_SECRET_KEY": "OKX_D_S",
            "OKX_DEMO_PASSPHRASE": "OKX_D_P",
        }
        for k, v in expected_keys.items():
            self.assertEqual(saved.get(k), v, f"Key mismatch for {k}")
        self.assertGreaterEqual(cleared["n"], 1)

    def test_venue_credentials_isolation_and_fallback(self):
        import r20_gateway.secrets as gw_secrets
        # 1. 独立配置时精确分流
        store_sample = {
            "BINANCE_LIVE_API_KEY": "bn_live_k",
            "BINANCE_LIVE_SECRET_KEY": "bn_live_s",
            "BINANCE_DEMO_API_KEY": "bn_demo_k",
            "BINANCE_DEMO_SECRET_KEY": "bn_demo_s",
            "GATE_LIVE_API_KEY": "gt_live_k",
            "GATE_LIVE_SECRET_KEY": "gt_live_s",
            "GATE_DEMO_API_KEY": "gt_demo_k",
            "GATE_DEMO_SECRET_KEY": "gt_demo_s",
            "OKX_LIVE_API_KEY": "okx_live_k",
            "OKX_LIVE_SECRET_KEY": "okx_live_s",
            "OKX_LIVE_PASSPHRASE": "okx_live_p",
            "OKX_DEMO_API_KEY": "okx_demo_k",
            "OKX_DEMO_SECRET_KEY": "okx_demo_s",
            "OKX_DEMO_PASSPHRASE": "okx_demo_p",
        }
        with patch.object(gw_secrets, "load_secrets", lambda: store_sample):
            self.assertEqual(ex.venue_credentials("binance", "live"), ("bn_live_k", "bn_live_s"))
            self.assertEqual(ex.venue_credentials("binance", "demo"), ("bn_demo_k", "bn_demo_s"))
            self.assertEqual(ex.venue_credentials("gate", "live"), ("gt_live_k", "gt_live_s"))
            self.assertEqual(ex.venue_credentials("gate", "sandbox"), ("gt_demo_k", "gt_demo_s"))
            self.assertEqual(ex.venue_credentials("okx", "live"), ("okx_live_k", "okx_live_s"))
            self.assertEqual(ex.venue_credentials("okx", "demo"), ("okx_demo_k", "okx_demo_s"))
            self.assertEqual(ex.venue_passphrase("okx", "live"), "okx_live_p")
            self.assertEqual(ex.venue_passphrase("okx", "demo"), "okx_demo_p")

        # 2. 未配独立凭证时优雅回退通用凭证
        store_fallback = {
            "BINANCE_API_KEY": "bn_legacy_k",
            "BINANCE_SECRET_KEY": "bn_legacy_s",
            "GATE_API_KEY": "gt_legacy_k",
            "GATE_SECRET_KEY": "gt_legacy_s",
            "OKX_API_KEY": "okx_legacy_k",
            "OKX_SECRET_KEY": "okx_legacy_s",
            "OKX_PASSPHRASE": "okx_legacy_p",
        }
        with patch.object(gw_secrets, "load_secrets", lambda: store_fallback):
            self.assertEqual(ex.venue_credentials("binance", "demo"), ("bn_legacy_k", "bn_legacy_s"))
            self.assertEqual(ex.venue_credentials("binance", "live"), ("bn_legacy_k", "bn_legacy_s"))
            self.assertEqual(ex.venue_credentials("gate", "sandbox"), ("gt_legacy_k", "gt_legacy_s"))
            self.assertEqual(ex.venue_credentials("okx", "demo"), ("okx_legacy_k", "okx_legacy_s"))
            self.assertEqual(ex.venue_passphrase("okx", "demo"), "okx_legacy_p")

    def test_status_endpoint_reports_6_account_readiness(self):
        import r20_gateway.secrets as gw_secrets
        store = {
            "BINANCE_LIVE_API_KEY": "K",
            "BINANCE_LIVE_SECRET_KEY": "S",
            "GATE_DEMO_API_KEY": "K",
            "GATE_DEMO_SECRET_KEY": "S",
            "OKX_DEMO_API_KEY": "K",
            "OKX_DEMO_SECRET_KEY": "S",
            "OKX_DEMO_PASSPHRASE": "P",
        }
        with patch.object(gw_secrets, "load_secrets", lambda: store):
            r = self.client.get("/api/v1/admin/multi-exchange")
            self.assertEqual(r.status_code, 200)
            data = r.json()
            self.assertIn("accounts_status", data)
            acc = data["accounts_status"]
            self.assertTrue(acc["binance"]["live"]["has_api_key"])
            self.assertFalse(acc["binance"]["demo"]["has_api_key"])
            self.assertTrue(acc["gate"]["demo"]["has_api_key"])
            self.assertFalse(acc["gate"]["live"]["has_api_key"])
            self.assertTrue(acc["okx"]["demo"]["has_api_key"])
            self.assertTrue(acc["okx"]["demo"]["has_passphrase"])
            self.assertFalse(acc["okx"]["live"]["has_api_key"])

    def test_diagnostics_public_ping_fallback_when_no_credentials(self):
        from r20_backend.exchanges import diagnostics
        mock_http = lambda url, **k: (200, {"serverTime": 1720000000000}, {})
        with patch.object(diagnostics, "_default_http_call", mock_http):
            res = diagnostics.diagnose_venue_connection(
                venue="binance", environment="demo", api_key="", secret_key=""
            )
            self.assertTrue(res["ok"])
            self.assertFalse(res["authenticated"])
            self.assertEqual(res["mode"], "public_fallback")
            self.assertIn("公共网络连通正常", res["message"])

    def test_diagnostics_verify_credentials_before_saving_success(self):
        from r20_backend.exchanges import diagnostics

        # 1. OKX 鉴权成功 (code: "0")
        okx_call = lambda url, **k: (200, {"code": "0", "data": [{"totalEq": "1000"}]}, {})
        res = diagnostics.diagnose_venue_connection(
            venue="okx", environment="demo", api_key="test_k", secret_key="test_s", passphrase="p",
            http_client=okx_call
        )
        self.assertTrue(res["ok"])
        self.assertTrue(res["authenticated"])
        self.assertEqual(res["mode"], "authenticated")
        self.assertIn("OKX DEMO 凭证鉴权成功", res["message"])

        # 2. Binance 鉴权成功
        bn_call = lambda url, **k: (200, {"totalWalletBalance": "5000", "canTrade": True}, {})
        res_bn = diagnostics.diagnose_venue_connection(
            venue="binance", environment="live", api_key="test_k", secret_key="test_s",
            http_client=bn_call
        )
        self.assertTrue(res_bn["ok"])
        self.assertTrue(res_bn["authenticated"])
        self.assertIn("Binance LIVE 凭证鉴权成功", res_bn["message"])

        # 3. Gate 鉴权成功
        gt_call = lambda url, **k: (200, {"currency": "USDT", "total": "2000"}, {})
        res_gt = diagnostics.diagnose_venue_connection(
            venue="gate", environment="sandbox", api_key="test_k", secret_key="test_s",
            http_client=gt_call
        )
        self.assertTrue(res_gt["ok"])
        self.assertTrue(res_gt["authenticated"])
        self.assertIn("Gate SANDBOX 凭证鉴权成功", res_gt["message"])

    def test_diagnostics_verify_credentials_auth_failure_handling(self):
        from r20_backend.exchanges import diagnostics

        # OKX 业务码非0
        okx_fail = lambda url, **k: (200, {"code": "50111", "msg": "API key doesn't exist"}, {})
        res = diagnostics.diagnose_venue_connection(
            venue="okx", environment="live", api_key="bad_k", secret_key="bad_s", passphrase="p",
            http_client=okx_fail
        )
        self.assertFalse(res["ok"])
        self.assertEqual(res["mode"], "auth_failed")
        self.assertIn("50111", res["message"])

        # Gate HTTP 401
        gate_fail = lambda url, **k: (401, {"label": "INVALID_KEY", "message": "Invalid API key"}, {})
        res_gt = diagnostics.diagnose_venue_connection(
            venue="gate", environment="live", api_key="bad_k", secret_key="bad_s",
            http_client=gate_fail
        )
        self.assertFalse(res_gt["ok"])
        self.assertEqual(res_gt["mode"], "auth_failed")
        self.assertIn("INVALID_KEY", res_gt["message"])

    def test_diagnostics_endpoint_via_http_api(self):
        from r20_backend.exchanges import diagnostics
        mock_ok = lambda *a, **k: {
            "ok": True, "venue": "binance", "environment": "live",
            "authenticated": True, "mode": "authenticated",
            "latency_ms": 12, "message": "Binance 鉴权成功", "details": {}
        }
        with patch.object(diagnostics, "diagnose_venue_connection", mock_ok):
            r = self.client.post("/api/v1/admin/multi-exchange/test-connection", json={
                "venue": "binance", "environment": "live",
                "api_key": "mock_k", "secret_key": "mock_s",
            })
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["ok"])
            self.assertTrue(r.json()["authenticated"])

    def test_diagnostics_endpoint_requires_auth(self):
        for p in self._patches[:1]:  # 停掉 require_admin_header 打桩
            p.stop()
        r = self.client.post("/api/v1/admin/multi-exchange/test-connection", json={
            "venue": "gate", "environment": "live"
        })
        self.assertIn(r.status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()

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

    def test_execution_status_field_exposed_in_get(self):
        with patch.object(ex, "venue_credentials", lambda v: ("", "")), \
                patch.object(ex, "venue_testnet_enabled", lambda v: False), \
                patch.object(ex, "execution_open", lambda v: v == "gate"):
            data = self.client.get("/api/v1/admin/multi-exchange").json()
        self.assertTrue(data["venues"]["gate"]["execution_open"])
        self.assertFalse(data["venues"]["binance"]["execution_open"])


class LabStatusEndpointTests(MultiExchangeApiTests):
    """US-006：试验田状态端点（DATA_DIR 钉临时目录 + routing/exchanges 全打桩）。

    注：曾因与 US-009 追加的同名类互相遮蔽而整类不执行，故名单独化；
    gates 断言为端点最终 6 键契约（AC 键名 + US-009 别名键，双键同值防漂移）。
    """

    def _pool(self, **over):
        p = {"assets": ["BTC"], "margin_per_trade_usdt": 50.0, "max_open": 2,
             "min_confidence": 80.0, "dry_run": True}
        p.update(over)
        return p

    def test_structure_and_gates(self):
        import r20_backend.exchanges.routing_policy as rp
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tp = Path(tmp.name) / "gate_lab_trackers.json"
        tp.write_text(json.dumps({
            "BTC": {"mode": "dry", "side": "long", "contracts": 15, "size_signed": 15,
                    "entry_px": 79000.0, "tp_px": 85000.0, "sl_px": 77000.0,
                    "tp_id": "t1", "sl_id": "s1", "margin_usdt": 40.0},
            "bad_row": "not-a-dict"}), encoding="utf-8")
        (Path(tmp.name) / "gate_lab_ledger.json").write_text(json.dumps(
            [{"asset": "SOL", "close_ts": i, "reason": "reconcile_no_position"} for i in range(7)]),
            encoding="utf-8")
        with patch.object(app_module, "DATA_DIR", Path(tmp.name)), \
                patch.object(rp, "load_gate_pool", lambda: self._pool()), \
                patch.object(rp, "effective_mode", lambda: "dry_run"), \
                patch.object(ex, "execution_open", lambda v: False), \
                patch.object(ex, "venue_credentials", lambda v: ("k", "s")):
            r = self.client.get("/api/v1/admin/multi-exchange/lab-status")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["mode"], "dry_run")
        self.assertEqual(data["gates"], {"pool_nonempty": True, "execution_open": False,
                                         "credentials_ready": True, "dry_run_off": False,
                                         "execution_switch": False, "credentials": True})
        self.assertEqual(len(data["positions"]), 1)   # 脏行剔除
        self.assertEqual(data["positions"][0]["asset"], "BTC")
        self.assertEqual(len(data["recent_ledger"]), 5)          # 只取最近 5 笔
        self.assertEqual(data["recent_ledger"][0]["close_ts"], 6)  # 最新在前
        self.assertEqual(data["pool"]["assets"], ["BTC"])

    def test_corrupted_files_degrade_to_empty_200(self):
        import r20_backend.exchanges.routing_policy as rp
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "gate_lab_trackers.json").write_text("{not json", encoding="utf-8")
        (Path(tmp.name) / "gate_lab_ledger.json").write_text("[{", encoding="utf-8")
        with patch.object(app_module, "DATA_DIR", Path(tmp.name)), \
                patch.object(rp, "load_gate_pool", lambda: self._pool(assets=[], dry_run=True)), \
                patch.object(rp, "effective_mode", lambda: "off"), \
                patch.object(ex, "execution_open", lambda v: False), \
                patch.object(ex, "venue_credentials", lambda v: ("", "")):
            r = self.client.get("/api/v1/admin/multi-exchange/lab-status")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["positions"], [])
        self.assertEqual(data["recent_ledger"], [])
        self.assertEqual(data["mode"], "off")
        self.assertFalse(data["gates"]["credentials_ready"])

    def test_requires_admin_header(self):
        for p in self._patches[:1]:  # 停掉 require_admin_header 打桩走真实 RBAC
            p.stop()
        r = self.client.get("/api/v1/admin/multi-exchange/lab-status")
        self.assertIn(r.status_code, (401, 403))

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


class LabStatusTests(unittest.TestCase):
    """US-006 GET /api/v1/admin/multi-exchange/lab-status（零交易所触网、文件全临时）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.orig = app_module.admin_auth
        app_module.admin_auth = AdminAuthStore(Path(self.temp.name) / "admin.db")
        app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        self.client = TestClient(app_module.app)
        self.data_dir = Path(self.temp.name)
        self.patches = [
            patch.object(app_module, "require_admin_header", lambda t=None: None),
            patch.object(app_module, "DATA_DIR", self.data_dir),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        app_module.admin_auth = self.orig
        self.temp.cleanup()

    def _mock_policy(self, mode="dry_run", pool=None, exec_open=False, creds=("", "")):
        from r20_backend.exchanges import routing_policy
        pool = pool if pool is not None else {"assets": ["BTC"], "dry_run": True,
                                              "margin_per_trade_usdt": 50.0,
                                              "max_open": 2, "min_confidence": 80.0}
        return [
            patch.object(routing_policy, "effective_mode", lambda: mode),
            patch.object(routing_policy, "load_gate_pool", lambda: dict(pool)),
            # 端点在函数体内 `from r20_backend.exchanges import execution_open...`——
            # 每次调用按包属性解析，桩必须打在包别名上（打 registry.* 不生效=静默
            # 走真实 env/密钥库，测试封闭性红线）。
            patch.object(ex, "execution_open", lambda v: exec_open),
            patch.object(ex, "venue_credentials", lambda v: creds),
        ]

    def test_requires_admin_auth(self):
        self.patches[0].stop()   # 撤鉴权桩 → 真实 RBAC
        r = self.client.get("/api/v1/admin/multi-exchange/lab-status")
        self.assertIn(r.status_code, (401, 403))
        self.patches[0].start()

    def test_dry_mode_full_shape(self):
        import json as _json
        (self.data_dir / "gate_lab_trackers.json").write_text(_json.dumps({
            "BTC": {"mode": "dry", "asset": "BTC", "side": "long", "contracts": 15,
                    "entry_px": 79000.0, "tp_px": 85000.0, "sl_px": 77000.0,
                    "margin_usdt": 40.0}}))
        (self.data_dir / "gate_lab_ledger.json").write_text(_json.dumps(
            [{"asset": s, "reason": "reconcile_no_position", "entry_px": 1, "contracts": 2,
              "close_ts": 1788999999} for s in ("A", "B", "C", "D", "E", "F")]))
        ps = self._mock_policy(mode="dry_run", exec_open=False, creds=("", ""))
        for p in ps:
            p.start()
        try:
            r = self.client.get("/api/v1/admin/multi-exchange/lab-status")
        finally:
            for p in ps:
                p.stop()
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()
        self.assertEqual(d["mode"], "dry_run")
        self.assertEqual(d["gates"], {
            "pool_nonempty": True, "execution_open": False,
            "credentials_ready": False, "dry_run_off": False,
            "execution_switch": False, "credentials": False})
        self.assertEqual(d["trackers"][0]["asset"], "BTC")
        self.assertEqual(len(d["ledger_tail"]), 5)          # 只回最近 5 笔
        self.assertEqual(d["ledger_tail"][0]["asset"], "F")  # 最新在前
        self.assertEqual(d["ledger_tail"][-1]["asset"], "B")
        self.assertNotIn("error", d)
        # US-009 别名键与 AC 键同值（防双源漂移）
        self.assertEqual(d["positions"], d["trackers"])
        self.assertEqual(d["gates"]["execution_switch"], d["gates"]["execution_open"])
        self.assertEqual(d["gates"]["credentials"], d["gates"]["credentials_ready"])

    def test_live_mode_all_gates_on(self):
        ps = self._mock_policy(mode="live",
                               pool={"assets": ["BTC", "ETH"], "dry_run": False,
                                     "margin_per_trade_usdt": 20.0, "max_open": 1,
                                     "min_confidence": 85.0},
                               exec_open=True, creds=("k", "s"))
        for p in ps:
            p.start()
        try:
            d = self.client.get("/api/v1/admin/multi-exchange/lab-status").json()
        finally:
            for p in ps:
                p.stop()
        self.assertEqual(d["mode"], "live")
        self.assertTrue(all(d["gates"].values()))
        self.assertEqual(d["pool"]["assets"], ["BTC", "ETH"])
        self.assertEqual(d["trackers"], [])                 # 文件不存在=空非error
        self.assertEqual(d["ledger_tail"], [])
        self.assertNotIn("error", d)

    def test_corrupt_files_report_error_gracefully(self):
        (self.data_dir / "gate_lab_trackers.json").write_text("{not json!!")
        ps = self._mock_policy(mode="off", pool={"assets": [], "dry_run": True,
                                                 "margin_per_trade_usdt": 50.0,
                                                 "max_open": 2, "min_confidence": 80.0})
        for p in ps:
            p.start()
        try:
            r = self.client.get("/api/v1/admin/multi-exchange/lab-status")
        finally:
            for p in ps:
                p.stop()
        self.assertEqual(r.status_code, 200)                # 坏文件不 500
        d = r.json()
        self.assertIn("error", d)
        self.assertEqual(d["trackers"], [])


if __name__ == "__main__":
    unittest.main()

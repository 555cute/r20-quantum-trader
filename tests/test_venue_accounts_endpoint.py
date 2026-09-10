"""US-005 三所账户对称只读端点契约测试（全 mock、零网络、零真实凭证）。

钉住契约：
- 三态 fail-closed：无凭证 → unavailable；档位不符 → unavailable 且不发 HTTP；
  读取异常 → degraded——所有未知数值字段一律 None（绝不填 0 冒充）。
- 零写调用：OKX/Gate 私有读取仅 GET；无凭证路径不触碰任何 HTTP 边界函数。
- 凭证不回显：响应文本不含 key/secret 字面值。
- 不聚合：顶层与逐所键面严格限定，无任何合计字段；demo/live 互不串数据。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import r20_backend.app as app_module
from r20_backend.admin_auth import AdminAuthStore
import scripts.okx_rest as okx_rest
import scripts.okx_runtime as okx_runtime
import r20_backend.exchanges as exchanges_pkg

CONTRACT_KEYS = {"status", "equity", "available", "positions_count",
                 "open_orders_count", "last_sync_ts", "reason"}


class _StubGateAdapter:
    def __init__(self, rows=None, positions=None, raises=None):
        self._raises = raises
        self._rows = rows if rows is not None else [{"id": "101"}]
        self._positions = positions if positions is not None else [{"size_signed": 3}]
        self.calls: list[tuple] = []

    def account_snapshot(self):
        self._boom()
        return {"equity_usdt": 500.0, "available_usdt": 450.0}

    def positions(self):
        self._boom()
        return self._positions

    def signed_request(self, method, path, params=None, body=None, timeout=15.0):
        self._boom()
        self.calls.append((method, path))
        return self._rows

    def _boom(self):
        if self._raises:
            raise self._raises


class VenueAccountsEndpointTests(unittest.TestCase):
    def setUp(self):
        from tests.config_sandbox import isolate_config
        isolate_config(self)
        self.temp = tempfile.TemporaryDirectory()
        self.original = app_module.admin_auth
        app_module.admin_auth = AdminAuthStore(Path(self.temp.name) / "admin.db")
        app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        self.client = TestClient(app_module.app)
        r = self.client.post("/api/v1/admin/auth/login",
                             json={"username": "admin", "password": "InitialAdmin123456"})
        self.assertEqual(r.status_code, 200, r.text)
        self.auth = {"X-R20-Session": r.json()["session_token"]}
        # 全 HTTP 边界哨兵：任何真实出网调用即炸（封闭三律·律①）
        self.net_sentry = Mock(side_effect=AssertionError("FORBIDDEN real HTTP"))
        patcher = patch.object(okx_rest, "urlopen", self.net_sentry)
        patcher.start()
        self.addCleanup(patcher.stop)
        gate_mod = __import__("r20_backend.exchanges.gate", fromlist=["gate"])
        p2 = patch.object(gate_mod, "urlopen", self.net_sentry)
        p2.start()
        self.addCleanup(p2.stop)

    def tearDown(self):
        app_module.admin_auth = self.original
        self.temp.cleanup()

    # ---------- 鉴权与参数 ----------

    def test_login_required_401(self):
        r = self.client.get("/api/v1/venue_accounts?environment=demo")
        self.assertEqual(r.status_code, 401)

    def test_rejects_unknown_environment_400(self):
        r = self.client.get("/api/v1/venue_accounts?environment=testnet", headers=self.auth)
        self.assertEqual(r.status_code, 400)
        r = self.client.get("/api/v1/venue_accounts", headers=self.auth)  # 缺省=demo 合法
        self.assertEqual(r.status_code, 200)

    # ---------- 响应形状：键面严格、无聚合 ----------

    def test_shape_no_aggregate_fields(self):
        with patch.object(okx_runtime, "current_environment",
                          return_value=SimpleNamespace(mode="demo", configured=False)):
            with patch.object(exchanges_pkg, "venue_credentials", return_value=("", "")):
                r = self.client.get("/api/v1/venue_accounts?environment=demo", headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(set(body), {"environment", "venues", "captured_at_ms"})
        self.assertEqual(set(body["venues"]), {"okx", "gate", "binance"})
        for venue, card in body["venues"].items():
            self.assertEqual(set(card), CONTRACT_KEYS, venue)
        # 任何合计字段不得存在（顶层与 venues 内均禁）
        flat = json.dumps(body).lower()
        for banned in ("total_eq", "total_equity", "sum", "aggregate"):
            self.assertNotIn(banned, flat)

    # ---------- 态一：无凭证 → unavailable 且零 HTTP ----------

    def test_no_credentials_all_unknown_zero_http(self):
        ga = Mock(side_effect=AssertionError("FORBIDDEN get_adapter without credentials"))
        req = Mock()
        with patch.object(okx_runtime, "current_environment",
                          return_value=SimpleNamespace(mode="demo", configured=False)):
            with patch.object(exchanges_pkg, "venue_credentials", return_value=("", "")):
                with patch.object(exchanges_pkg, "get_adapter", ga), \
                     patch.object(okx_rest, "request", req):
                    r = self.client.get("/api/v1/venue_accounts?environment=demo", headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text)
        v = r.json()["venues"]
        req.assert_not_called()
        ga.assert_not_called()
        self.net_sentry.assert_not_called()
        for key in ("okx", "gate"):
            self.assertEqual(v[key]["status"], "unavailable", key)
            for f in ("equity", "available", "positions_count", "open_orders_count", "last_sync_ts"):
                self.assertIsNone(v[key][f], f"{key}.{f} 未知必须为 None 不填 0")
            self.assertTrue(len(v[key]["reason"]) > 4, f"{key} 缺人话 reason")
        self.assertEqual(v["binance"]["status"], "not_implemented")
        self.assertIsNone(v["binance"]["equity"])
        self.assertIn("未实装", v["binance"]["reason"])

    # ---------- 态一·b：档位不符 → 拒跨档读取、零 HTTP ----------

    def test_okx_refuses_cross_tier_read(self):
        req = Mock()
        with patch.object(okx_runtime, "current_environment",
                          return_value=SimpleNamespace(mode="demo", configured=True)):
            with patch.object(exchanges_pkg, "venue_credentials", return_value=("", "")), \
                 patch.object(okx_rest, "request", req):
                r = self.client.get("/api/v1/venue_accounts?environment=live", headers=self.auth)
        self.assertEqual(r.status_code, 200)
        card = r.json()["venues"]["okx"]
        self.assertEqual(card["status"], "unavailable")
        self.assertIn("不符", card["reason"])
        req.assert_not_called()
        self.net_sentry.assert_not_called()

    # ---------- 态二：demo 有凭证 → ready 真数 ----------

    def _okx_rows(self, method, path, params=None, *, env=None, timeout=20):
        if path == "/api/v5/account/balance":
            return [{"totalEq": "1000.5", "details": [{"ccy": "USDT", "availEq": "800.25"}]}]
        if path == "/api/v5/account/positions":
            return [{"pos": "0.1"}, {"pos": "0"}]
        if path == "/api/v5/trade/orders-pending":
            return [{"ordId": "1"}, {"ordId": "2"}]
        raise AssertionError(f"unexpected OKX path {path}")

    def test_demo_ready_numbers_and_get_only(self):
        stub = _StubGateAdapter()
        ga = Mock(return_value=stub)
        req = Mock(side_effect=self._okx_rows)
        with patch.object(okx_runtime, "current_environment",
                          return_value=SimpleNamespace(mode="demo", configured=True)):
            with patch.object(exchanges_pkg, "venue_credentials", return_value=("k", "s")):
                with patch.object(exchanges_pkg, "get_adapter", ga), \
                     patch.object(okx_rest, "request", req):
                    r = self.client.get("/api/v1/venue_accounts?environment=demo", headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text)
        v = r.json()["venues"]
        self.assertEqual(v["okx"]["status"], "ready")
        self.assertEqual(v["okx"]["equity"], 1000.5)
        self.assertEqual(v["okx"]["available"], 800.25)
        self.assertEqual(v["okx"]["positions_count"], 1)
        self.assertEqual(v["okx"]["open_orders_count"], 2)
        self.assertEqual(v["gate"]["status"], "ready")
        self.assertEqual(v["gate"]["equity"], 500.0)
        self.assertEqual(v["gate"]["positions_count"], 1)
        self.assertEqual(v["gate"]["open_orders_count"], 1)
        self.assertEqual(v["binance"]["status"], "not_implemented")
        # 只读铁律：全部私有调用 method=GET；无下单/写路径
        for call in req.call_args_list:
            self.assertEqual(call.args[0], "GET")
        for method, path in stub.calls:
            self.assertEqual(method, "GET")
            self.assertNotIn("/orders?", path)  # 只查 open 列表，非提交
        # gate demo → sandbox 档（env_profiles 钉死择优域），绝不裸 live
        ga.assert_called_once_with("gate", environment="sandbox")
        # 凭证零回显
        text = json.dumps(v)
        self.assertNotIn('"k"', text)
        self.assertNotIn('"s"', text)

    # ---------- 态三：部分未知（Gate 读炸 → degraded，其余不受累） ----------

    def test_partial_unknown_other_venues_unaffected(self):
        stub = _StubGateAdapter(raises=ConnectionError("sandbox timeout"))
        ga = Mock(return_value=stub)
        req = Mock(side_effect=self._okx_rows)
        with patch.object(okx_runtime, "current_environment",
                          return_value=SimpleNamespace(mode="demo", configured=True)):
            with patch.object(exchanges_pkg, "venue_credentials", return_value=("k", "s")):
                with patch.object(exchanges_pkg, "get_adapter", ga), \
                     patch.object(okx_rest, "request", req):
                    r = self.client.get("/api/v1/venue_accounts?environment=demo", headers=self.auth)
        v = r.json()["venues"]
        self.assertEqual(v["gate"]["status"], "degraded")
        self.assertIn("timeout", v["gate"]["reason"])
        for f in ("equity", "available", "positions_count", "open_orders_count"):
            self.assertIsNone(v["gate"][f])
        self.assertEqual(v["okx"]["status"], "ready")  # 单所故障不拖垮其余
        self.net_sentry.assert_not_called()

    def test_gate_capability_error_maps_unavailable(self):
        from r20_backend.exchanges.base import ExchangeCapabilityError
        ga = Mock(side_effect=ExchangeCapabilityError("Gate 沙盒档位不可用：探测全失败"))
        with patch.object(okx_runtime, "current_environment",
                          return_value=SimpleNamespace(mode="demo", configured=False)):
            with patch.object(exchanges_pkg, "venue_credentials", return_value=("k", "s")):
                with patch.object(exchanges_pkg, "get_adapter", ga):
                    r = self.client.get("/api/v1/venue_accounts?environment=demo", headers=self.auth)
        card = r.json()["venues"]["gate"]
        self.assertEqual(card["status"], "unavailable")
        self.assertIn("探测全失败", card["reason"])
        self.assertIsNone(card["equity"])

    # ---------- 两环境互不串数据 ----------

    def test_live_request_uses_live_profile(self):
        stub = _StubGateAdapter()
        ga = Mock(return_value=stub)
        with patch.object(okx_runtime, "current_environment",
                          return_value=SimpleNamespace(mode="live", configured=False)):
            with patch.object(exchanges_pkg, "venue_credentials", return_value=("k", "s")):
                with patch.object(exchanges_pkg, "get_adapter", ga):
                    r = self.client.get("/api/v1/venue_accounts?environment=live", headers=self.auth)
        self.assertEqual(r.json()["environment"], "live")
        ga.assert_called_once_with("gate", environment="live")
        card = r.json()["venues"]["okx"]
        self.assertEqual(card["status"], "unavailable")  # live 无 Key → 不发 HTTP
        self.net_sentry.assert_not_called()


if __name__ == "__main__":
    unittest.main()

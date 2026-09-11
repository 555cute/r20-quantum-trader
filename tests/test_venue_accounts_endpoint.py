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

# 封闭三律：dashboard.app 在模块导入时即启动 2s 周期后台刷新线程（update_cache_cycle
# → okx_rest 真调 OKX 私有面；r20_backend startup 还会二次点火）。测试进程一次性，
# 进程内永久钉死循环体为 no-op——不恢复，杜绝任何点火路径的真实出网。
import dashboard.app as _dashboard_app
_dashboard_app.stop_dashboard_background_worker()
_dashboard_app.update_cache_cycle = lambda *a, **k: None

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


class _StubBinanceAdapter:
    def __init__(self, rows=None, positions=None, raises=None):
        self._raises = raises
        self._rows = rows if rows is not None else [{"orderId": "201"}]
        self._positions = positions if positions is not None else [{"size_signed": 1.5}]

    def account_snapshot(self):
        if self._raises:
            raise self._raises
        return {"equity_usdt": 800.0, "available_usdt": 750.0}

    def positions(self):
        if self._raises:
            raise self._raises
        return list(self._positions)

    def open_orders(self, symbol=None):
        if self._raises:
            raise self._raises
        return list(self._rows)


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
        bn_mod = __import__("r20_backend.exchanges.binance", fromlist=["binance"])
        p3 = patch.object(bn_mod, "urlopen", self.net_sentry)
        p3.start()
        self.addCleanup(p3.stop)

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

    # ---------- 响应形状：键面严格、US-006 组合风险聚合 ----------

    def test_shape_and_portfolio_summary(self):
        with patch.object(okx_runtime, "current_environment",
                          return_value=SimpleNamespace(mode="demo", configured=False)):
            with patch.object(exchanges_pkg, "venue_credentials", return_value=("", "")):
                r = self.client.get("/api/v1/venue_accounts?environment=demo", headers=self.auth)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(set(body), {"environment", "venues", "portfolio_summary", "captured_at_ms"})
        self.assertEqual(set(body["venues"]), {"okx", "gate", "binance"})
        for venue, card in body["venues"].items():
            self.assertEqual(set(card), CONTRACT_KEYS, venue)
        summary = body["portfolio_summary"]
        self.assertEqual(summary["environment"], "demo")
        self.assertEqual(summary["total_equity"], 0.0)
        self.assertEqual(summary["active_venues_count"], 0)

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
        for key in ("okx", "gate", "binance"):
            self.assertEqual(v[key]["status"], "unavailable", key)
            for f in ("equity", "available", "positions_count", "open_orders_count", "last_sync_ts"):
                self.assertIsNone(v[key][f], f"{key}.{f} 未知必须为 None 不填 0")
            self.assertTrue(len(v[key]["reason"]) > 4, f"{key} 缺人话 reason")

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
        gate_stub = _StubGateAdapter()
        binance_stub = _StubBinanceAdapter()
        def _get_adapter(venue, environment=None):
            if venue == "gate":
                return gate_stub
            elif venue == "binance":
                return binance_stub
            raise ValueError(f"unknown {venue}")
        ga = Mock(side_effect=_get_adapter)
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
        self.assertEqual(v["binance"]["status"], "ready")
        self.assertEqual(v["binance"]["equity"], 800.0)
        self.assertEqual(v["binance"]["positions_count"], 1)
        self.assertEqual(v["binance"]["open_orders_count"], 1)
        # 只读铁律：全部私有调用 method=GET；无下单/写路径
        for call in req.call_args_list:
            self.assertEqual(call.args[0], "GET")
        for method, path in gate_stub.calls:
            self.assertEqual(method, "GET")
            self.assertNotIn("/orders?", path)  # 只查 open 列表，非提交
        # gate demo → sandbox 档；binance demo → demo 档
        ga.assert_any_call("gate", environment="sandbox")
        ga.assert_any_call("binance", environment="demo")
        # 凭证零回显
        text = json.dumps(v)
        self.assertNotIn('"k"', text)
        self.assertNotIn('"s"', text)

        # US-006: 组合风险与权益聚合断言
        summary = r.json()["portfolio_summary"]
        self.assertEqual(summary["environment"], "demo")
        self.assertAlmostEqual(summary["total_equity"], 2300.5)  # 1000.5 + 500.0 + 800.0
        self.assertAlmostEqual(summary["total_available"], 2000.25)  # 800.25 + 450.0 + 750.0
        self.assertEqual(summary["active_venues_count"], 3)
        self.assertEqual(set(summary["reporting_venues"]), {"okx", "gate", "binance"})
        dist = summary["asset_distribution"]
        self.assertAlmostEqual(dist["okx"]["share_pct"], round(1000.5 / 2300.5 * 100, 2))
        self.assertAlmostEqual(dist["gate"]["share_pct"], round(500.0 / 2300.5 * 100, 2))
        self.assertAlmostEqual(dist["binance"]["share_pct"], round(800.0 / 2300.5 * 100, 2))

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
        gate_stub = _StubGateAdapter()
        binance_stub = _StubBinanceAdapter()
        def _get_adapter(venue, environment=None):
            return gate_stub if venue == "gate" else binance_stub
        ga = Mock(side_effect=_get_adapter)
        with patch.object(okx_runtime, "current_environment",
                          return_value=SimpleNamespace(mode="live", configured=False)):
            with patch.object(exchanges_pkg, "venue_credentials", return_value=("k", "s")):
                with patch.object(exchanges_pkg, "get_adapter", ga):
                    r = self.client.get("/api/v1/venue_accounts?environment=live", headers=self.auth)
        self.assertEqual(r.json()["environment"], "live")
        ga.assert_any_call("gate", environment="live")
        ga.assert_any_call("binance", environment="live")
        card = r.json()["venues"]["okx"]
        self.assertEqual(card["status"], "unavailable")  # live 无 Key → 不发 HTTP
        self.net_sentry.assert_not_called()


if __name__ == "__main__":
    unittest.main()

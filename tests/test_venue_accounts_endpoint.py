"""US-005 三所账户对称只读端点测试（全 mock HTTP 边界，零真实网络，零生产凭证）。

契约钉：
- 三态 fail-closed：unavailable / not_implemented / degraded 均以 None 表达未知，绝不 0 冒充；
- 未配置凭证路径零 HTTP 出网（OKX/Gateway 请求构造器调用计数为 0）；
- 凭证值绝不出现在响应 JSON；
- 响应无跨所/跨环境聚合合计字段（demo/live 永不加总）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import r20_backend.app as app_module
from r20_backend.admin_auth import AdminAuthStore
from r20_backend.exchanges import registry as ex_registry
from scripts import okx_rest
from scripts.okx_runtime import OKXEnvironment


def _env(mode: str, configured: bool) -> OKXEnvironment:
    k = "OKXAK-TEST" if configured else ""
    return OKXEnvironment(mode, k, "sec" if configured else "", "pp" if configured else "")


class _GateStub:
    def __init__(self, open_orders_exc: bool = False, positions_bad: bool = False):
        self.calls = []
        self._open_exc = open_orders_exc
        self._positions_bad = positions_bad

    def account_snapshot(self):
        self.calls.append(("accounts",))
        return {"venue": "gate", "equity_usdt": 1234.5, "available_usdt": 800.25,
                "currency": "USDT"}

    def positions(self):
        self.calls.append(("positions",))
        return [] if self._positions_bad is False else None

    def signed_request(self, method, path, params=None, body=None, timeout=15.0):
        self.calls.append((method, path))
        if self._open_exc:
            raise RuntimeError("gate orders endpoint down")
        return [{"id": "1"}, {"id": "2"}, {"id": "3"}]


class VenueAccountsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = app_module.admin_auth
        app_module.admin_auth = AdminAuthStore(Path(self.temp.name) / "admin.db")
        app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        self.client = TestClient(app_module.app)
        self._patches = [
            patch.object(app_module, "require_admin_header", lambda t=None: None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        app_module.admin_auth = self.original
        self.temp.cleanup()

    # ---------------- 三态矩阵（默认 demo，全未知凭证） ----------------
    def test_three_state_matrix_all_unavailable_no_zero(self):
        req_calls = []
        gate_calls = []
        with patch("scripts.okx_runtime.current_environment", lambda: _env("demo", False)), \
                patch.object(okx_rest, "request", lambda *a, **k: req_calls.append(a) or []), \
                patch.object(ex_registry, "venue_credentials", lambda v: ("", "")), \
                patch.object(ex_registry, "get_adapter", lambda *a, **k: gate_calls.append(a)):
            r = self.client.get("/api/v1/venue_accounts")
        self.assertEqual(r.status_code, 200, r.text)
        v = r.json()["venues"]
        self.assertEqual(set(v), {"okx", "binance", "gate"})
        # 未知 ≠ 0：数值字段必须 None
        for venue in ("okx", "binance", "gate"):
            for f in ("equity", "available", "positions_count", "open_orders_count"):
                self.assertIsNone(v[venue][f], f"{venue}.{f} 未知时必须 None，不得填 0")
        self.assertEqual(v["okx"]["status"], "unavailable")
        self.assertEqual(v["gate"]["status"], "unavailable")
        self.assertEqual(v["binance"]["status"], "not_implemented")
        self.assertIn("未配置", v["okx"]["reason"])
        self.assertIn("未实装", v["binance"]["reason"])
        # 凭证缺失路径零 HTTP 出网
        self.assertEqual(req_calls, [])
        self.assertEqual(gate_calls, [])

    # ---------------- environment 校验 ----------------
    def test_environment_validation(self):
        with patch("scripts.okx_runtime.current_environment", lambda: _env("demo", False)), \
                patch.object(ex_registry, "venue_credentials", lambda v: ("", "")):
            r = self.client.get("/api/v1/venue_accounts", params={"environment": "prod"})
            self.assertEqual(r.status_code, 400)
            r2 = self.client.get("/api/v1/venue_accounts", params={"environment": "LIVE"})
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(r2.json()["environment"], "live")

    # ---------------- 登录态：未打桩 require_admin_header 时 401 ----------------
    def test_login_required(self):
        self._patches[0].stop()   # 恢复真实鉴权
        try:
            r = self.client.get("/api/v1/venue_accounts")
        finally:
            self._patches[0].start()
        self.assertEqual(r.status_code, 401)

    # ---------------- OKX ready + 精确只读请求三查 ----------------
    def test_okx_ready_shape(self):
        seen = []

        def fake_request(method, path, params=None, env=None, timeout=20):
            seen.append((method, path))
            if path.endswith("balance"):
                return [{"totalEq": "2000.5", "availBal": "1500.25", "details": []}]
            if path.endswith("positions"):
                return [{"pos": "0.1"}, {"pos": "0"}, {"pos": "-2"}]
            return [{"ordId": "a"}, {"ordId": "b"}]

        with patch("scripts.okx_runtime.current_environment", lambda: _env("demo", True)), \
                patch.object(okx_rest, "request", fake_request), \
                patch.object(ex_registry, "venue_credentials", lambda v: ("", "")):
            r = self.client.get("/api/v1/venue_accounts")
        v = r.json()["venues"]["okx"]
        self.assertEqual(v["status"], "ready")
        self.assertEqual(v["equity"], 2000.5)
        self.assertEqual(v["available"], 1500.25)
        self.assertEqual(v["positions_count"], 2)     # pos=0 剔除
        self.assertEqual(v["open_orders_count"], 2)
        self.assertEqual(seen, [("GET", "/api/v5/account/balance"),
                                ("GET", "/api/v5/account/positions"),
                                ("GET", "/api/v5/trade/orders-pending")])

    # ---------------- OKX 档位不一致：拒绝跨环境读数、不发 HTTP ----------------
    def test_okx_environment_mismatch_no_http(self):
        calls = []
        with patch("scripts.okx_runtime.current_environment", lambda: _env("live", True)), \
                patch.object(okx_rest, "request", lambda *a, **k: calls.append(a) or []), \
                patch.object(ex_registry, "venue_credentials", lambda v: ("", "")):
            r = self.client.get("/api/v1/venue_accounts", params={"environment": "demo"})
        v = r.json()["venues"]["okx"]
        self.assertEqual(v["status"], "unavailable")
        self.assertIn("不一致", v["reason"])
        self.assertEqual(calls, [])

    # ---------------- Gate ready：demo→sandbox 档位映射 + live 直通 ----------------
    def test_gate_ready_and_env_mapping(self):
        taken = []

        def fake_get_adapter(venue, environment=None):
            taken.append((venue, environment))
            return _GateStub()

        with patch("scripts.okx_runtime.current_environment", lambda: _env("demo", False)), \
                patch.object(ex_registry, "venue_credentials", lambda v: ("GATEAK-TEST", "GATESEC-TEST")), \
                patch.object(ex_registry, "get_adapter", fake_get_adapter):
            d = self.client.get("/api/v1/venue_accounts").json()
            self.client.get("/api/v1/venue_accounts", params={"environment": "live"}).json()
        self.assertIn(("gate", "sandbox"), taken)
        self.assertIn(("gate", "live"), taken)
        g = d["venues"]["gate"]
        self.assertEqual(g["status"], "ready")
        self.assertEqual(g["equity"], 1234.5)
        self.assertEqual(g["available"], 800.25)
        self.assertEqual(g["positions_count"], 0)
        self.assertEqual(g["open_orders_count"], 3)
        # 凭证值绝不回显
        body = json.dumps(d, ensure_ascii=False)
        self.assertNotIn("GATEAK-TEST", body)
        self.assertNotIn("GATESEC-TEST", body)

    # ---------------- Gate 部分失败 → degraded，挂单 None 不填 0 ----------------
    def test_gate_partial_failure_degraded(self):
        with patch("scripts.okx_runtime.current_environment", lambda: _env("demo", False)), \
                patch.object(ex_registry, "venue_credentials", lambda v: ("AK", "SK")), \
                patch.object(ex_registry, "get_adapter", lambda *a, **k: _GateStub(open_orders_exc=True)):
            r = self.client.get("/api/v1/venue_accounts")
        g = r.json()["venues"]["gate"]
        self.assertEqual(g["status"], "degraded")
        self.assertIsNone(g["open_orders_count"])
        self.assertEqual(g["equity"], 1234.5)   # 已读到的子项保留

    # ---------------- 响应无聚合合计字段（demo/live 永不加总） ----------------
    def test_no_aggregate_fields(self):
        with patch("scripts.okx_runtime.current_environment", lambda: _env("demo", False)), \
                patch.object(ex_registry, "venue_credentials", lambda v: ("", "")):
            top = self.client.get("/api/v1/venue_accounts").json()
        self.assertEqual(set(top), {"environment", "captured_at_ms", "venues"})
        flat = json.dumps(top)
        for banned in ("total", "sum", "aggregate"):
            self.assertNotIn(banned, flat.lower())


if __name__ == "__main__":
    unittest.main()

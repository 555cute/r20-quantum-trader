"""三所读账户：**跨档拒绝**、状态词分明、零值即未知（第二百二十二刀）。

| 语义 | 口径 |
|---|---|
| ★ 跨档拒绝 | OKX 只读卡在「系统档位」与「请求档位」不符时**直接拒绝**并说明理由（**防止实盘/模拟混线**；档位切换属后台动作）——且**不发起任何请求** |
| ★ 状态词分明 | `unavailable`（不可用：未配置/凭证失效/跨档/账户面不可用）与 `degraded`（读到了但降级：读取或解析失败）是**两个词**，不混用 |
| ★ 零值即未知 | `equity <= 0 ⇒ None`（0 权益不是「零资产」，而是读不到）|
| 计数口径 | `positions_count` **只数非零仓位**（幽灵仓不算）；挂单数取挂单列表长度 |
| ★ 档位轴凭证 | Gate 取凭证按**档位轴**（审计 C7）：否则只有分层键时**误报未配置**，或反放行 generic LIVE 键**打错主机** |

⚠️ 边界：本刀覆盖 OKX 与 Gate 两所；**Binance 那一段尚未读**，不声称。
"""

import unittest
from unittest.mock import MagicMock, patch

from r20_backend.routers import exchanges as R


class _Env:
    def __init__(self, mode="demo", configured=True):
        self.mode = mode
        self.configured = configured


class OkxReaderTest(unittest.TestCase):
    def _patch_env(self, env):
        p = patch("scripts.okx_runtime.current_environment", return_value=env)
        p.start()
        self.addCleanup(p.stop)

    def test_cross_tier_read_is_refused_without_any_request(self):
        """★ 跨档必须拒绝：只读卡不得用另一档的凭证去打实盘/模拟。"""
        self._patch_env(_Env(mode="demo", configured=True))
        req = MagicMock()
        with patch("scripts.okx_rest.request", req):
            out = R._venue_accounts_okx("live")
        self.assertEqual(out["status"], "unavailable")
        self.assertIn("跨档", out["reason"])
        self.assertFalse(req.called, "拒绝跨档时**不得发起任何请求**")
        self.assertIsNone(out["equity"], "未知账户的字段一律 None")

    def test_unconfigured_is_unavailable_and_sends_nothing(self):
        self._patch_env(_Env(mode="demo", configured=False))
        req = MagicMock()
        with patch("scripts.okx_rest.request", req):
            out = R._venue_accounts_okx("demo")
        self.assertEqual(out["status"], "unavailable")
        self.assertIn("未发起任何请求", out["reason"])
        self.assertFalse(req.called)

    def test_read_failure_is_degraded_but_missing_credentials_is_unavailable(self):
        """★ 两个状态词不可混：**读失败 = degraded**；**凭证失效 = unavailable**。"""
        from scripts.okx_rest import OKXNotConfigured
        self._patch_env(_Env())
        with patch("scripts.okx_rest.request", side_effect=RuntimeError("网关超时")):
            degraded = R._venue_accounts_okx("demo")
        self.assertEqual(degraded["status"], "degraded")
        self.assertIn("RuntimeError", degraded["reason"])
        with patch("scripts.okx_rest.request", side_effect=OKXNotConfigured("key 过期")):
            unavailable = R._venue_accounts_okx("demo")
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertIn("凭证失效", unavailable["reason"])

    def test_successful_read_prefers_usdt_detail_and_never_reports_zero_equity(self):
        self._patch_env(_Env())
        bal = [{"totalEq": "9999", "details": [
            {"ccy": "USDT", "eq": "0", "cashBal": "0", "availEq": "12.5"},
            {"ccy": "BTC", "eq": "1"}]}]
        pos = [{"pos": "2"}, {"pos": "0"}, {"pos": "-1.5"}]
        pend = [{"ordId": "1"}, {"ordId": "2"}]

        def _req(method, path, params=None, env=None):
            return bal if "balance" in path else (pos if "positions" in path else pend)
        with patch("scripts.okx_rest.request", side_effect=_req):
            out = R._venue_accounts_okx("demo")
        self.assertEqual(out["status"], "ready")
        self.assertIsNone(out["equity"], "0 权益 ⇒ None（不是「账户是空的」）")
        self.assertEqual(out["available"], 12.5, "可用额取 USDT 明细的 availEq")
        self.assertEqual(out["positions_count"], 2, "只数非零仓位（零仓行不算）")
        self.assertEqual(out["open_orders_count"], 2)
        self.assertIn("last_sync_ms", out)
        self.assertGreater(out["last_sync_ms"], 1_600_000_000_000,
                           "毫秒量级 ⇒ 与字段名 last_sync_ms **相符**（名字即语义）")

    def test_parse_failure_is_degraded_not_a_crash(self):
        self._patch_env(_Env())
        with patch("scripts.okx_rest.request", return_value=[{"details": "不是列表"}]):
            out = R._venue_accounts_okx("demo")
        self.assertEqual(out["status"], "degraded", "解析失败是降级，不是异常冒泡")
        self.assertIn("解析失败", out["reason"])


class GateReaderTest(unittest.TestCase):
    def test_credentials_are_taken_on_the_tier_axis(self):
        """★ 审计 C7：按**档位轴**取凭证（demo ⇒ sandbox 档），否则会误报未配置／打错主机。"""
        seen = {}

        def _cred(venue, env):
            seen["args"] = (venue, env)
            return ("K", "S")

        p = patch("r20_backend.exchanges.venue_credentials", side_effect=_cred)
        p.start()
        self.addCleanup(p.stop)
        p2 = patch("r20_backend.close_intent.adapter_environment",
                   side_effect=lambda v, e: "sandbox" if e == "demo" else "live")
        p2.start()
        self.addCleanup(p2.stop)
        adapter = MagicMock()
        adapter.account_snapshot.return_value = {"total": "1"}
        adapter.positions.return_value = []
        adapter.signed_request.return_value = []
        with patch("r20_backend.exchanges.get_adapter", return_value=adapter) as ga:
            R._venue_accounts_gate("demo")
        self.assertEqual(seen["args"], ("gate", "sandbox"),
                         "凭证必须按档位轴（demo ⇒ sandbox）取")
        self.assertEqual(ga.call_args.kwargs.get("environment"), "sandbox",
                         "适配器也起在 sandbox 档")

    def test_missing_credentials_short_circuit_before_any_adapter(self):
        p = patch("r20_backend.exchanges.venue_credentials", return_value=("", ""))
        p.start()
        self.addCleanup(p.stop)
        p2 = patch("r20_backend.close_intent.adapter_environment",
                   side_effect=lambda v, e: e)
        p2.start()
        self.addCleanup(p2.stop)
        with patch("r20_backend.exchanges.get_adapter") as ga:
            out = R._venue_accounts_gate("demo")
        self.assertEqual(out["status"], "unavailable")
        self.assertFalse(ga.called, "未配置时不得起适配器（更不得发请求）")

    def test_capability_error_is_unavailable_and_other_errors_are_degraded(self):
        from r20_backend.exchanges import ExchangeCapabilityError
        p = patch("r20_backend.exchanges.venue_credentials", return_value=("K", "S"))
        p.start()
        self.addCleanup(p.stop)
        p2 = patch("r20_backend.close_intent.adapter_environment",
                   side_effect=lambda v, e: e)
        p2.start()
        self.addCleanup(p2.stop)
        with patch("r20_backend.exchanges.get_adapter",
                   side_effect=ExchangeCapabilityError("无此能力")):
            out = R._venue_accounts_gate("demo")
        self.assertEqual(out["status"], "unavailable")
        self.assertIn("账户面不可用", out["reason"])
        with patch("r20_backend.exchanges.get_adapter", side_effect=RuntimeError("网络断")):
            out2 = R._venue_accounts_gate("demo")
        self.assertEqual(out2["status"], "degraded")
        self.assertIn("网络断", out2["reason"])


if __name__ == "__main__":
    unittest.main()

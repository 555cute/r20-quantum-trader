"""读账户的第二组口径：**第三态 `not_implemented`**、成功回执与**一处零值不一致**（第二百二十三刀）。

| 语义 | 口径 |
|---|---|
| ★ 第三态 | Binance 先探能力表：`supports_account=False` ⇒ **`not_implemented`**（**未实装**），而不是「不可用」也不是「降级」——三种事实三种词 |
| 能力表本身读不到 | ⇒ `degraded`（**能区分**：是探测失败，不是「未实装」）|
| 成功回执 | `status=ready`，`reason` 里写明**档位**（Gate 用 `sandbox/live`，Binance 用 `demo/live`）；`open_orders_count` 遇到**非列表**返回 `0`（不炸）|
| ★ **零值不一致** | ⚠️ Gate / Binance 的成功回执用 `float(x or 0)` ⇒ **读不到就是 0**；而 OKX 是 `equity <= 0 ⇒ None`。**同一张面板上的三个所，对「读不到」的表示法不一致** —— 列待议（只钉现状）|

★ 三种状态词现在齐了：`ready` / `unavailable`（不可用）/ `degraded`（读到了但降级）/ `not_implemented`（未实装）。

⚠️ 边界：`admin_multi_exchange` 的读写与测试连接**尚未读**，不在本刀范围。
"""

import unittest
from unittest.mock import MagicMock, patch

from r20_backend.routers import exchanges as R


class _Env:
    def __init__(self, mode="demo", configured=True):
        self.mode = mode
        self.configured = configured


def _binance_adapter(supports):
    class _Caps:
        supports_account = supports

    class _Adapter:
        capabilities = _Caps
    return _Adapter


class OkxRemainingBranchesTest(unittest.TestCase):
    def test_environment_resolution_failure_is_unavailable_with_the_exception_name(self):
        with patch("scripts.okx_runtime.current_environment",
                   side_effect=RuntimeError("配置读不动")):
            out = R._venue_accounts_okx("demo")
        self.assertEqual(out["status"], "unavailable")
        self.assertIn("环境解析失败", out["reason"])
        self.assertIn("RuntimeError", out["reason"], "带上异常类型名便于定位")

    def test_total_equity_fallback_when_there_is_no_usdt_detail(self):
        """没有 USDT 明细时退到账户级 `totalEq`（仍按「>0 才算读到」处理）。"""
        with patch("scripts.okx_runtime.current_environment", return_value=_Env()):
            def _req(method, path, params=None, env=None):
                if "balance" in path:
                    return [{"totalEq": "321.5", "details": [{"ccy": "BTC", "eq": "1"}]}]
                if "positions" in path:
                    return []
                return []
            with patch("scripts.okx_rest.request", side_effect=_req):
                out = R._venue_accounts_okx("demo")
        self.assertEqual(out["equity"], 321.5, "退到 totalEq")
        self.assertEqual(out["available"], 321.5, "无可用额字段时可用额 = 权益")
        self.assertEqual((out["positions_count"], out["open_orders_count"]), (0, 0))


class GateReadyTest(unittest.TestCase):
    def _run(self, acct, open_rows):
        p = patch("r20_backend.exchanges.venue_credentials", return_value=("K", "S"))
        p.start()
        self.addCleanup(p.stop)
        p2 = patch("r20_backend.close_intent.adapter_environment", side_effect=lambda v, e: e)
        p2.start()
        self.addCleanup(p2.stop)
        ad = MagicMock()
        ad.account_snapshot.return_value = acct
        ad.positions.return_value = [{"size_signed": "1"}, {"size_signed": "0"}]
        ad.signed_request.return_value = open_rows
        with patch("r20_backend.exchanges.get_adapter", return_value=ad):
            return R._venue_accounts_gate("demo")

    def test_ready_receipt_names_the_tier_and_counts_only_nonzero_positions(self):
        out = self._run({"equity_usdt": "500", "available_usdt": "400"}, [{"o": 1}])
        self.assertEqual(out["status"], "ready")
        self.assertEqual((out["equity"], out["available"]), (500.0, 400.0))
        self.assertEqual(out["positions_count"], 1, "零仓行不算")
        self.assertEqual(out["open_orders_count"], 1)
        # ⚠️ 我原以为回执写的是请求参数名（demo）—— 错：写的是**适配器档位** sandbox。
        # 这正是 `_LISTING_ENV_MAP` 里各所叫法不同那件事的回显。
        self.assertIn("sandbox", out["reason"], "回执写明**适配器档位**（gate 的演示档叫 sandbox）")
        self.assertNotIn("demo", out["reason"],
                         "不写请求参数名 —— 面板要显示的是真实档位")

    def test_non_list_open_orders_count_as_zero_instead_of_crashing(self):
        out = self._run({"equity_usdt": "1", "available_usdt": "1"}, None)
        self.assertEqual(out["open_orders_count"], 0, "非列表 ⇒ 0（不炸）")

    def test_missing_equity_becomes_zero_not_none(self):
        """⚠️ **实测不一致（列待议）**：Gate/Binance 读不到权益 ⇒ **0**；OKX ⇒ **None**。

        同一张面板上三个所对「读不到」的表示法不一致 ⇒ 下游若按 0 渲染，会把
        「读不到」显示成「账户是空的」（正是「读不到 ≠ 没有」要防的事）。本刀只钉现状。
        """
        out = self._run({}, [])
        self.assertEqual(out["equity"], 0.0)
        self.assertEqual(out["available"], 0.0)
        self.assertNotIsInstance(out["equity"], type(None))
        with patch("scripts.okx_runtime.current_environment", return_value=_Env()):
            with patch("scripts.okx_rest.request", return_value=[{"totalEq": "0"}]):
                okx = R._venue_accounts_okx("demo")
        self.assertIsNone(okx["equity"], "对照：OKX 同一情形给 None")


class BinanceReaderTest(unittest.TestCase):
    def test_capability_probe_failure_is_degraded_not_not_implemented(self):
        """★ 两种事实两种词：**探测失败** = degraded；**探测到未实装** = not_implemented。"""
        class _Caps:
            @property
            def supports_account(self):
                raise RuntimeError("能力表读不到")

        class _Adapter:
            capabilities = _Caps()
        # ⚠️ 不能用 MagicMock(side_effect=...)：`MagicMock().capabilities` 会被**自动创建**成
        # 真值 Mock，根本不抛，于是探测"成功"了 —— 夹具骗了自己。这里用**属性真的会抛**的类型。
        with patch("r20_backend.exchanges.binance.BinanceAdapter", new=_Adapter):
            out = R._venue_accounts_binance("demo")
        self.assertEqual(out["status"], "degraded")
        self.assertIn("能力表读取失败", out["reason"])

    def test_unsupported_account_surface_is_not_implemented(self):
        fake = _binance_adapter(False)
        with patch("r20_backend.exchanges.binance.BinanceAdapter", new=fake):
            out = R._venue_accounts_binance("demo")
        self.assertEqual(out["status"], "not_implemented")
        self.assertIn("未实装", out["reason"])
        self.assertIn("supports_account", out["reason"], "把判据本身写进原因")
        self.assertIsNone(out["equity"], "未实装同样不给 0")

    def test_unconfigured_credentials_short_circuit(self):
        fake = _binance_adapter(True)
        with patch("r20_backend.exchanges.binance.BinanceAdapter", new=fake), \
             patch("r20_backend.exchanges.venue_credentials", return_value=("", "")), \
             patch("r20_backend.exchanges.get_adapter") as ga:
            out = R._venue_accounts_binance("live")
        self.assertEqual(out["status"], "unavailable")
        self.assertIn("未发起任何请求", out["reason"])
        self.assertFalse(ga.called)

    def test_ready_receipt_names_the_binance_tier(self):
        fake = _binance_adapter(True)
        ad = MagicMock()
        ad.account_snapshot.return_value = {"equity_usdt": "88", "available_usdt": "77"}
        ad.positions.return_value = [{"size_signed": "2"}, {"size_signed": "0"}]
        ad.open_orders.return_value = [{"o": 1}, {"o": 2}]
        with patch("r20_backend.exchanges.binance.BinanceAdapter", new=fake), \
             patch("r20_backend.exchanges.venue_credentials", return_value=("K", "S")), \
             patch("r20_backend.exchanges.get_adapter", return_value=ad) as ga:
            out = R._venue_accounts_binance("live")
        self.assertEqual(out["status"], "ready")
        self.assertEqual(ga.call_args.kwargs.get("environment"), "live")
        self.assertIn("Binance live", out["reason"])
        self.assertEqual(out["positions_count"], 1)
        self.assertEqual(out["open_orders_count"], 2)


if __name__ == "__main__":
    unittest.main()

"""Offline isolated test for final order quote verification in submit_protected_limit_order.
US-002: the place path travels scripts.okx_rest (V5 direct-signed REST) — patched at
the ai_factor_trader module binding; zero real CLI, zero network, zero exchange ops.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure scripts directory is in sys.path so okx_runtime can be imported
scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.ai_factor_trader as aft


class SubmitProtectedLimitOrderTests(unittest.TestCase):
    def setUp(self):
        # US-007 接线后 submit 会先走 listing gate（真实 urlopen）并落意图文件——
        # 本文件只测风控逻辑，统一封死两条新缝（律①/③）：
        import tempfile
        from r20_backend.exchanges import listing as _listing
        patcher = patch.object(
            _listing, "ensure_contract_listed",
            lambda venue, environment, contract: _listing.ListingCheck(
                ok=True, reason=None, checked_at="", source="cache"))
        patcher.start()
        self.addCleanup(patcher.stop)
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        ip = patch.object(aft, "OPEN_INTENT_FILE", tmp.name)
        ip.start()
        self.addCleanup(ip.stop)
        # 审计④后 submit 统一单次读现价（demo rescale+幻觉锚共用）——本文件封第三缝，
        # 现价=被测价，锚恒平；divergence 用例自带 patch 会嵌套覆盖。
        tp_ = patch.object(aft, "fetch_ticker",
                           lambda inst_id=None, **kw: {"last": "100.0"})
        tp_.start()
        self.addCleanup(tp_.stop)
        # default hedge wire mapping (no network); net-mode cases override
        wp = patch("scripts.okx_pos_mode.wire_pos_side",
                   lambda side, endpoint="order", env=None: (
                       None if str(side).lower() in ("net", "") else str(side).lower()))
        wp.start()
        self.addCleanup(wp.stop)

    @patch("scripts.ai_factor_trader.okx_rest")
    @patch("scripts.ai_factor_trader.current_environment")
    def test_submit_protected_limit_order_core_rejection(self, mock_env, mock_rest):
        # Environment is real / not simulated to test raw effective prices
        env_obj = MagicMock()
        env_obj.simulated = False
        mock_env.return_value = env_obj

        # 1. Invalid Geometry (Long: sl > px)
        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 120.0, 105.0)
        self.assertFalse(ok)
        self.assertIn("买多几何不合法", reason)

        # 2. Insufficient RR (Long: RR = 1.0 < 2.0)
        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 110.0, 90.0)
        self.assertFalse(ok)
        self.assertIn("盈亏比不足 2.0", reason)

        # 3. Non-finite value (NaN / Inf)
        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, float("nan"), 130.0, 90.0)
        self.assertFalse(ok)
        self.assertIn("有限数值", reason)

        # Core rejections must never touch the exchange channel.
        mock_rest.place_order.assert_not_called()

        # 4. Valid quote proceeds to the REST place with attached TP/SL legs
        # long_short_mode: wire posSide must be long; net_mode: must omit (not long/short)
        mock_rest.place_order.return_value = [{"ordId": "ord_mock_12345", "sCode": "0"}]
        with patch("scripts.okx_pos_mode.wire_pos_side", lambda side, endpoint="order", env=None: "long"):
            ok, order_id = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 125.0, 90.0)
        self.assertTrue(ok)
        self.assertEqual(order_id, "ord_mock_12345")
        mock_rest.place_order.assert_called_once()
        kwargs = mock_rest.place_order.call_args.kwargs
        self.assertEqual(kwargs.get("attach_tp"), 125.0)
        self.assertEqual(kwargs.get("attach_sl"), 90.0)
        self.assertEqual(kwargs.get("pos_side"), "long")  # hedge wire only
        self.assertEqual(kwargs.get("td_mode"), "cross")
        self.assertEqual(kwargs.get("ord_type"), "limit")
        self.assertEqual(mock_rest.place_order.call_args.args[:2], ("BTC-USDT-SWAP", "buy"))

        # 4b. net_mode wire must NOT carry posSide=long/short
        mock_rest.place_order.reset_mock()
        mock_rest.place_order.return_value = [{"ordId": "ord_net_1", "sCode": "0"}]
        with patch("scripts.okx_pos_mode.wire_pos_side", lambda side, endpoint="order", env=None: None):
            ok_net, _oid = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 125.0, 90.0)
        self.assertTrue(ok_net)
        kwargs_net = mock_rest.place_order.call_args.kwargs
        self.assertTrue(kwargs_net.get("pos_side") in (None, ""),
                        f"net_mode 不得发送 posSide=long/short，收到 {kwargs_net.get('pos_side')!r}")
        mock_rest.place_order.reset_mock()

        # 5. Accepted response without an ordId fails closed (never blind trust)
        mock_rest.place_order.return_value = []
        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 125.0, 90.0)
        self.assertFalse(ok)
        self.assertIn("verifiable order id", reason)

        # 6. REST transport/business failure surfaces as the rejection reason
        mock_rest.place_order.side_effect = RuntimeError("OKX 51001: down")
        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 125.0, 90.0)
        self.assertFalse(ok)
        self.assertIn("51001", reason)

    @patch("scripts.ai_factor_trader.okx_rest")
    @patch("scripts.ai_factor_trader.current_environment")
    def test_demo_ticker_divergence_uses_market_data_service_not_cli(self, mock_env, mock_rest):
        # Simulated env + demo ticker 10% below quote -> effective prices rescale, then place with attach legs
        env_obj = MagicMock()
        env_obj.simulated = True
        mock_env.return_value = env_obj
        mock_rest.place_order.return_value = [{"ordId": "ord_demo_1"}]
        import scripts.ai_factor_trader as _aft
        with patch.object(_aft, "fetch_ticker", return_value={"last": "100.0"}) as tick:
            ok, order_id = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 110.0, 140.0, 95.0)
        self.assertTrue(ok)
        tick.assert_called_once_with("BTC-USDT-SWAP")
        kwargs = mock_rest.place_order.call_args.kwargs
        self.assertAlmostEqual(float(kwargs.get("px")), 100.0, delta=0.5)
        mock_rest.place_order.assert_called_once()

    def test_take_profit_width_clamping(self):
        """测试止盈宽度平滑收窄：防止 AI 规划过远无法触及的天际线止盈单。"""
        from scripts.trader.brackets import clamp_take_profit_width

        # 1. 超过最大 R:R 上限（默认 3.5:1）：entry=100, sl=90 (risk=10), tp=160 (原 R:R=6.0)
        # 应被收窄到 100 + 10 * 3.5 = 135.0 (R:R=3.5)
        clamped_long = clamp_take_profit_width(
            is_long=True, limit_px=100.0, sl_px=90.0, tp_px=160.0, atr=0.0, prec=2)
        self.assertEqual(clamped_long, 135.0)

        # 2. 空头方向对称：entry=100, sl=110 (risk=10), tp=40 (原 R:R=6.0)
        # 应被收窄到 100 - 10 * 3.5 = 65.0 (R:R=3.5)
        clamped_short = clamp_take_profit_width(
            is_long=False, limit_px=100.0, sl_px=110.0, tp_px=40.0, atr=0.0, prec=2)
        self.assertEqual(clamped_short, 65.0)

        # 3. 验证 clamp_take_profit_width 函数的 ATR 与 R:R 联动：
        # entry=100, sl=90 (risk=10), tp=130 (R:R=3.0 <= 3.5)
        # 若 atr=2.0，max_tp_atr=3.5 -> max_tp_dist = min(2.0*3.5=7.0, 35.0) -> 但底线为 min_rr (10*2=20)
        # 故 allowed_max = 20.0 -> tp 被收窄到 120.0
        clamped_tp = clamp_take_profit_width(
            is_long=True, limit_px=100.0, sl_px=90.0, tp_px=130.0, atr=2.0, prec=2)
        self.assertEqual(clamped_tp, 120.0)

        # 4. 正常区间内的 TP 原样放行：entry=100, sl=90 (risk=10), tp=125 (R:R=2.5)，atr=8.0 (8*3.5=28 > 25)
        normal_tp = clamp_take_profit_width(
            is_long=True, limit_px=100.0, sl_px=90.0, tp_px=125.0, atr=8.0, prec=2)
        self.assertEqual(normal_tp, 125.0)


if __name__ == "__main__":
    unittest.main()

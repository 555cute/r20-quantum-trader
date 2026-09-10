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
scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.ai_factor_trader as aft


class SubmitProtectedLimitOrderTests(unittest.TestCase):
    @patch("scripts.ai_factor_trader.okx_rest")
    @patch("scripts.ai_factor_trader.selected_environment")
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
        mock_rest.place_order.return_value = [{"ordId": "ord_mock_12345", "sCode": "0"}]
        ok, order_id = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 125.0, 90.0)
        self.assertTrue(ok)
        self.assertEqual(order_id, "ord_mock_12345")
        mock_rest.place_order.assert_called_once()
        kwargs = mock_rest.place_order.call_args.kwargs
        self.assertEqual(kwargs.get("attach_tp"), 125.0)
        self.assertEqual(kwargs.get("attach_sl"), 90.0)
        self.assertEqual(kwargs.get("pos_side"), "long")
        self.assertEqual(kwargs.get("td_mode"), "cross")
        self.assertEqual(kwargs.get("ord_type"), "limit")
        self.assertEqual(mock_rest.place_order.call_args.args[:2], ("BTC-USDT-SWAP", "buy"))

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
    @patch("scripts.ai_factor_trader.selected_environment")
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


if __name__ == "__main__":
    unittest.main()

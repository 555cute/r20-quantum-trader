"""Offline isolated test for final order quote verification in submit_protected_limit_order.
Zero real CLI, zero network, zero real exchange operations.
US-002: the order path now goes through scripts.okx_rest.place_order (V5 signed REST);
these tests bind at that function boundary — closed by law ①/② (no subprocess anywhere).
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
    @patch("scripts.ai_factor_trader.place_order")
    @patch("scripts.ai_factor_trader.selected_environment")
    def test_submit_protected_limit_order_core_rejection(self, mock_env, mock_place):
        # Environment is real / not simulated to test raw effective prices
        env_obj = MagicMock()
        env_obj.simulated = False
        mock_env.return_value = env_obj

        # 1. Invalid Geometry (Long: sl > px)
        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 120.0, 105.0)
        self.assertFalse(ok)
        self.assertIn("买多几何不合法", reason)
        mock_place.assert_not_called()

        # 2. Insufficient RR (Long: RR = 1.0 < 2.0)
        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 110.0, 90.0)
        self.assertFalse(ok)
        self.assertIn("盈亏比不足 2.0", reason)
        mock_place.assert_not_called()

        # 3. Non-finite value (NaN / Inf)
        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, float("nan"), 130.0, 90.0)
        self.assertFalse(ok)
        self.assertIn("有限数值", reason)
        mock_place.assert_not_called()

        # 4. Valid quote passes core verification and proceeds to the signed REST order
        mock_place.return_value = [{"ordId": "ord_mock_12345"}]
        ok, order_id = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 125.0, 90.0)
        self.assertTrue(ok)
        self.assertEqual(order_id, "ord_mock_12345")
        mock_place.assert_called_once()
        call = mock_place.call_args
        self.assertEqual(call.args, ("BTC-USDT-SWAP", "buy", 1))
        self.assertEqual(call.kwargs["pos_side"], "long")
        self.assertEqual(call.kwargs["td_mode"], "cross")
        self.assertEqual(call.kwargs["ord_type"], "limit")
        self.assertEqual(call.kwargs["px"], 100.0)
        self.assertEqual(call.kwargs["attach_tp"], 125.0)
        self.assertEqual(call.kwargs["attach_sl"], 90.0)

    @patch("scripts.ai_factor_trader.place_order")
    @patch("scripts.ai_factor_trader.selected_environment")
    def test_submit_protected_limit_order_rejects_missing_order_id(self, mock_env, mock_place):
        env_obj = MagicMock()
        env_obj.simulated = False
        mock_env.return_value = env_obj
        # Exchange accepted the call but returned no verifiable id -> hard failure.
        mock_place.return_value = [{"sCode": "0"}]
        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 125.0, 90.0)
        self.assertFalse(ok)
        self.assertIn("verifiable order id", reason)

    @patch("scripts.ai_factor_trader.place_order")
    @patch("scripts.ai_factor_trader.selected_environment")
    def test_submit_protected_limit_order_propagates_exchange_error(self, mock_env, mock_place):
        env_obj = MagicMock()
        env_obj.simulated = False
        mock_env.return_value = env_obj
        # Business errors surface as RuntimeError from the REST layer with code+msg.
        mock_place.side_effect = RuntimeError("OKX 51121: params invalid")
        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 125.0, 90.0)
        self.assertFalse(ok)
        self.assertIn("51121", reason)


if __name__ == "__main__":
    unittest.main()

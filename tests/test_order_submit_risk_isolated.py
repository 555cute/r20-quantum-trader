"""Offline isolated test for final order quote verification in submit_protected_limit_order.
Zero real CLI, zero network, zero real exchange operations.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.ai_factor_trader as aft


class SubmitProtectedLimitOrderTests(unittest.TestCase):
    @patch("scripts.ai_factor_trader.get_exchange")
    @patch("scripts.ai_factor_trader.selected_environment")
    def test_submit_protected_limit_order_core_rejection(self, mock_env, mock_get_exchange):
        env_obj = MagicMock()
        env_obj.simulated = False
        mock_env.return_value = env_obj
        exchange = MagicMock()
        exchange.place_protected_limit_order.return_value = {"ordId": "ord_mock_12345"}
        mock_get_exchange.return_value = exchange

        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 120.0, 105.0)
        self.assertFalse(ok)
        self.assertIn("买多几何不合法", reason)
        exchange.place_protected_limit_order.assert_not_called()

        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 110.0, 90.0)
        self.assertFalse(ok)
        self.assertIn("盈亏比不足 2.0", reason)
        exchange.place_protected_limit_order.assert_not_called()

        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, float("nan"), 130.0, 90.0)
        self.assertFalse(ok)
        self.assertIn("有限数值", reason)
        exchange.place_protected_limit_order.assert_not_called()

        ok, order_id = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", 1, 100.0, 125.0, 90.0)
        self.assertTrue(ok)
        self.assertEqual(order_id, "ord_mock_12345")
        exchange.place_protected_limit_order.assert_called_once()

    @patch("scripts.ai_factor_trader.get_exchange")
    @patch("scripts.ai_factor_trader.selected_environment")
    def test_unknown_place_response_is_uncertain_not_unfilled(self, mock_env, mock_get_exchange):
        env_obj = MagicMock()
        env_obj.simulated = False
        mock_env.return_value = env_obj
        exchange = MagicMock()
        exchange.place_protected_limit_order.side_effect = RuntimeError("upstream timeout")
        mock_get_exchange.return_value = exchange

        ok, reason = aft.submit_protected_limit_order("BTC-USDT-SWAP", "buy", "long", "0.001", 100.0, 125.0, 90.0)
        self.assertFalse(ok)
        self.assertTrue(aft.is_uncertain_submit(reason))
        self.assertIn("timeout", reason)


if __name__ == "__main__":
    unittest.main()

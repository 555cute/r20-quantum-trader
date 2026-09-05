"""Tests for Universal Execution Router and Exchange Order Adapters."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from r20_backend.execution_router import ExecutionRouter
from r20_backend.exchanges import BinanceAdapter, GateAdapter, OKXAdapter


class ExecutionRouterTests(unittest.TestCase):
    def test_router_rejects_non_finite_or_inverted_geometry(self):
        # 1. Reject invalid geometry (Long stop loss above entry)
        bad_long = {
            "action": "BUY_LONG",
            "entry_price": 80000.0,
            "stop_loss_price": 81000.0,
            "take_profit_price": 85000.0,
            "margin_usdt": 100.0,
        }
        ok, msg, _ = ExecutionRouter.execute_decision("BTC", bad_long, exchange_id="binance")
        self.assertFalse(ok)
        self.assertIn("物理级风控拦截", msg)

        # 2. Reject R:R < 2.0
        low_rr_long = {
            "action": "BUY_LONG",
            "entry_price": 80000.0,
            "stop_loss_price": 79000.0,
            "take_profit_price": 81000.0,  # RR = 1.0 < 2.0
            "margin_usdt": 100.0,
        }
        ok, msg, _ = ExecutionRouter.execute_decision("BTC", low_rr_long, exchange_id="binance")
        self.assertFalse(ok)
        self.assertIn("盈亏比不足 2.0", msg)

    def test_binance_adapter_order_formatting_and_signature(self):
        adapter = BinanceAdapter(api_key="test_key", api_secret="test_secret")
        params = {"symbol": "BTCUSDT", "side": "BUY", "timestamp": 1788600000000}
        signed = adapter._sign_params(params)
        self.assertIn("signature=", signed)
        self.assertIn("symbol=BTCUSDT", signed)

    def test_gate_adapter_signature_generation(self):
        adapter = GateAdapter(api_key="gate_key", api_secret="gate_secret")
        headers = adapter._sign_gate("POST", "/api/v4/futures/usdt/orders", body="{}")
        self.assertEqual(headers["KEY"], "gate_key")
        self.assertIn("SIGN", headers)
        self.assertIn("Timestamp", headers)

    @patch("r20_backend.exchanges.BinanceAdapter.place_limit_order_with_sl_tp")
    def test_valid_llm_decision_dispatches_to_binance(self, mock_bn_place):
        mock_bn_place.return_value = (True, "bn_order_12345", {"quantity": 0.005})
        valid_long = {
            "action": "BUY_LONG",
            "entry_price": 80000.0,
            "stop_loss_price": 78000.0,
            "take_profit_price": 85000.0,  # RR = (85000-80000)/(80000-78000) = 2.5
            "margin_usdt": 150.0,
            "leverage": 3,
        }
        ok, ord_id, meta = ExecutionRouter.execute_decision("BTC", valid_long, exchange_id="binance")
        self.assertTrue(ok)
        self.assertEqual(ord_id, "bn_order_12345")
        self.assertEqual(meta["exchange"], "binance")
        self.assertGreaterEqual(meta["rr"], 2.0)


if __name__ == "__main__":
    unittest.main()

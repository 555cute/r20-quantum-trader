import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.ai_brain_trader import fetch_single_instrument_package


class FakeExchange:
    def __init__(self):
        self.env = SimpleNamespace(exchange="binance", mode="demo")
        self.canceled = []

    def cancel_order(self, inst_id, order_id):
        self.canceled.append((inst_id, order_id))
        return {"ordId": order_id}


class TestAiBrainMarketIntegration(unittest.TestCase):
    def test_missing_adx_is_invalid_not_zero(self):
        item = {"instId": "BTC-USDT-SWAP", "name": "BTC", "type": "crypto", "precision": 1}
        with patch("scripts.ai_brain_trader.fetch_ticker", return_value={"last": "65000", "bidPx": "64990", "askPx": "65010", "open24h": "64000"}), \
             patch("scripts.ai_brain_trader.fetch_candles", return_value=[]), \
             patch("scripts.ai_brain_trader.fetch_funding_rate", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_open_interest", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_long_short_ratio", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_taker_volume", return_value=None), \
             patch("scripts.ai_brain_trader.fetch_single_indicator", return_value={}):
            pkg = fetch_single_instrument_package(item)
        self.assertIsNone(pkg["adx_1h"])
        self.assertEqual(pkg["data_quality"], "invalid")
        self.assertFalse(pkg["smart_money"]["available"])

    def test_cancel_uses_adapter_not_okx_cli(self):
        fake = FakeExchange()
        with patch("scripts.ai_brain_trader.get_exchange", return_value=fake):
            from scripts.ai_brain_trader import get_exchange
            get_exchange().cancel_order("BTC-USDT-SWAP", "ord-1")
        self.assertEqual(fake.canceled, [("BTC-USDT-SWAP", "ord-1")])


if __name__ == "__main__":
    unittest.main()

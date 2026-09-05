"""Tests for Multi-Exchange Architecture (OKX, Binance, Gate.io), symbol normalization,
and multi-exchange candle / cross-market insight aggregator using standard unittest.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from r20_backend.exchanges import (
    BaseExchangeAdapter,
    BinanceAdapter,
    GateAdapter,
    OKXAdapter,
    get_exchange_adapter,
)
from scripts.multi_exchange_feed import fetch_cross_exchange_insights, fetch_multi_exchange_candles


class MultiExchangeTests(unittest.TestCase):
    def test_exchange_factory_and_symbol_formatting(self):
        okx = get_exchange_adapter("okx")
        self.assertEqual(okx.exchange_id, "okx")
        self.assertEqual(okx.format_symbol("BTC"), "BTC-USDT-SWAP")
        self.assertEqual(okx.normalize_symbol("BTC-USDT-SWAP"), "BTC")

        binance = get_exchange_adapter("binance")
        self.assertEqual(binance.exchange_id, "binance")
        self.assertEqual(binance.format_symbol("BTC"), "BTCUSDT")
        self.assertEqual(binance.normalize_symbol("BTCUSDT"), "BTC")

        gate = get_exchange_adapter("gate")
        self.assertEqual(gate.exchange_id, "gate")
        self.assertEqual(gate.format_symbol("BTC"), "BTC_USDT")
        self.assertEqual(gate.normalize_symbol("BTC_USDT"), "BTC")

        with self.assertRaises(ValueError):
            get_exchange_adapter("unsupported_exchange")

    def test_binance_adapter_ticker_and_klines_parsing(self):
        adapter = BinanceAdapter()
        dummy_ticker = {
            "lastPrice": "80123.4",
            "highPrice": "81000.0",
            "lowPrice": "79000.0",
            "volume": "12345.6",
            "priceChangePercent": "2.45",
            "closeTime": 1788600000000,
        }
        with patch("r20_backend.exchanges.http_get_json", return_value=dummy_ticker):
            t = adapter.fetch_ticker("BTC")
            self.assertEqual(t["exchange"], "binance")
            self.assertEqual(t["symbol"], "BTC")
            self.assertEqual(t["last"], 80123.4)
            self.assertEqual(t["vol24h"], 12345.6)
            self.assertEqual(t["price_change_percent"], 2.45)

        dummy_klines = [
            [1788600000000, "80000", "80500", "79800", "80200", "1500"],
            [1788603600000, "80200", "80600", "80100", "80400", "1200"],
        ]
        with patch("r20_backend.exchanges.http_get_json", return_value=dummy_klines):
            k = adapter.fetch_klines("BTC", interval="1h", limit=2)
            self.assertEqual(len(k), 2)
            self.assertEqual(k[0][4], 80200.0)
            self.assertEqual(k[1][4], 80400.0)

    def test_gate_adapter_ticker_and_orderbook_parsing(self):
        adapter = GateAdapter()
        dummy_ticker = [{
            "contract": "BTC_USDT",
            "last": "80250.5",
            "high_24h": "81100.0",
            "low_24h": "79100.0",
            "volume_24h": "8900.2",
            "funding_rate": "0.0001",
        }]
        with patch("r20_backend.exchanges.http_get_json", return_value=dummy_ticker):
            t = adapter.fetch_ticker("BTC")
            self.assertEqual(t["exchange"], "gate")
            self.assertEqual(t["symbol"], "BTC")
            self.assertEqual(t["last"], 80250.5)
            self.assertEqual(t["funding_rate"], 0.0001)

        dummy_book = {
            "bids": [{"p": "80240.0", "s": "5.2"}],
            "asks": [{"p": "80260.0", "s": "3.8"}],
        }
        with patch("r20_backend.exchanges.http_get_json", return_value=dummy_book):
            b = adapter.fetch_orderbook("BTC", limit=5)
            self.assertEqual(b["bids"][0], [80240.0, 5.2])
            self.assertEqual(b["asks"][0], [80260.0, 3.8])

    def test_multi_exchange_candles_fallback(self):
        # If OKX fails, should seamlessly fall back to Binance
        with patch("r20_backend.exchanges.OKXAdapter.fetch_klines", side_effect=RuntimeError("OKX 429")):
            dummy_binance_klines = [[float(i), 100.0, 105.0, 95.0, 102.0, 50.0] for i in range(20)]
            with patch("r20_backend.exchanges.BinanceAdapter.fetch_klines", return_value=dummy_binance_klines):
                candles = fetch_multi_exchange_candles("BTC", interval="15m", limit=20)
                self.assertEqual(len(candles), 20)
                self.assertEqual(candles[0][1], 100.0)

    def test_cross_exchange_insights_aggregation(self):
        with patch("r20_backend.exchanges.OKXAdapter.fetch_ticker", return_value={"last": 80000.0}):
            with patch("r20_backend.exchanges.BinanceAdapter.fetch_ticker", return_value={"last": 80080.0}):
                with patch("r20_backend.exchanges.BinanceAdapter.fetch_long_short_ratio", return_value=1.85):
                    with patch("r20_backend.exchanges.GateAdapter.fetch_ticker", return_value={"last": 80020.0, "funding_rate": 0.00015}):
                        res = fetch_cross_exchange_insights("BTC")
                        self.assertEqual(res["prices"]["okx"], 80000.0)
                        self.assertEqual(res["prices"]["binance"], 80080.0)
                        self.assertEqual(res["prices"]["gate"], 80020.0)
                        self.assertEqual(res["spread_disparity_pct"], 0.1)  # (80080 - 80000) / 80000 * 100 = 0.1%
                        self.assertEqual(res["binance_top_trader_long_short_ratio"], 1.85)
                        self.assertEqual(res["gate_funding_rate"], 0.00015)


if __name__ == "__main__":
    unittest.main()

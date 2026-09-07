import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.market_data_service import (
    fetch_ticker,
    fetch_tickers_bulk,
    fetch_orderbook_depth,
    fetch_indicators_batch,
    fetch_single_indicator,
    fetch_candles,
    fetch_funding_rate,
    fetch_open_interest,
    fetch_long_short_ratio,
    fetch_taker_volume,
    format_oi_usd,
)


def _closed_candle(ts, o, h, l, c, v):
    return [str(ts), str(o), str(h), str(l), str(c), str(v), str(v * o), str(v * o), "1"]


def _trend_candles(count=80, start=100.0):
    rows = []
    price = start
    for i in range(count):
        o = price
        h = price + 2.0
        l = price - 0.3
        c = price + 1.5
        rows.append(_closed_candle(1_700_000_000_000 + i * 3_600_000, o, h, l, c, 10.0 + i))
        price = c
    return list(reversed(rows))


class FakeExchange:
    def __init__(self, exchange="binance"):
        self.env = SimpleNamespace(exchange=exchange, mode="demo")
        self.calls = []
        self._ticker = {
            "instId": "BTC-USDT-SWAP",
            "last": "65000",
            "bidPx": "64990",
            "askPx": "65010",
            "open24h": "64000",
            "high24h": "66000",
            "low24h": "63000",
            "vol24h": "100",
            "volCcy24h": "6500000",
            "ts": "1",
        }
        self._candles = _trend_candles()
        self._oi = {"oi": "100", "oiCcy": "100", "oiUsd": "6500000", "ts": "1"}

    def ticker(self, inst_id):
        self.calls.append(("ticker", inst_id))
        return dict(self._ticker, instId=inst_id)

    def tickers(self):
        self.calls.append(("tickers",))
        return {"BTC-USDT-SWAP": self.ticker("BTC-USDT-SWAP"), "ETH-USDT-SWAP": self.ticker("ETH-USDT-SWAP")}

    def candles(self, inst_id, bar="15m", limit=100):
        self.calls.append(("candles", inst_id, bar, limit))
        return list(self._candles[:limit])

    def orderbook(self, inst_id, sz=5):
        self.calls.append(("orderbook", inst_id, sz))
        return {"bids": [["65000", "1"]], "asks": [["65010", "1"]]}

    def funding_rate(self, inst_id):
        self.calls.append(("funding_rate", inst_id))
        return 0.01

    def open_interest(self, inst_id):
        self.calls.append(("open_interest", inst_id))
        return dict(self._oi)

    def long_short_ratio(self, inst_id):
        self.calls.append(("long_short_ratio", inst_id))
        return None

    def taker_volume(self, inst_id):
        self.calls.append(("taker_volume", inst_id))
        return None


class TestMarketDataServiceOffline(unittest.TestCase):
    def setUp(self):
        self.exchange = FakeExchange()
        self.patcher = patch("scripts.market_data_service._get_exchange", return_value=self.exchange)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_ticker_and_bulk_use_same_exchange(self):
        ticker = fetch_ticker("BTC-USDT-SWAP")
        self.assertEqual(ticker["last"], "65000")
        bulk = fetch_tickers_bulk("SWAP")
        self.assertIn("BTC-USDT-SWAP", bulk)
        self.assertTrue(any(call[0] == "ticker" for call in self.exchange.calls))
        self.assertTrue(any(call[0] == "tickers" for call in self.exchange.calls))

    def test_orderbook_and_funding_and_oi(self):
        ob = fetch_orderbook_depth("BTC-USDT-SWAP", sz=5)
        self.assertEqual(ob["bids"][0][0], "65000")
        self.assertEqual(fetch_funding_rate("BTC-USDT-SWAP"), 0.01)
        self.assertEqual(fetch_open_interest("BTC-USDT-SWAP")["oi"], "100")

    def test_binance_enrichment_stays_unavailable(self):
        self.assertIsNone(fetch_long_short_ratio("BTC-USDT-SWAP"))
        self.assertIsNone(fetch_taker_volume("BTC-USDT-SWAP"))

    def test_indicators_from_real_candles(self):
        inds = fetch_indicators_batch("BTC-USDT-SWAP", ["adx", "kdj", "bbwidth", "cmf"], bar="1H")
        self.assertIn("ADX", inds)
        self.assertIn("adx", inds["ADX"])
        self.assertGreater(float(inds["ADX"]["adx"]), 0)
        self.assertIn("KDJ", inds)
        self.assertIn("BBWIDTH", inds)
        self.assertIn("CMF", inds)

    def test_adx_missing_candles_fail_closed_not_zero(self):
        self.exchange._candles = [_closed_candle(1, 1, 1, 1, 1, 1)]
        inds = fetch_indicators_batch("BTC-USDT-SWAP", ["adx"], bar="1H")
        self.assertNotIn("ADX", inds)
        self.assertEqual(fetch_single_indicator("BTC-USDT-SWAP", "ADX", bar="1H"), {})

    def test_candles_empty_on_adapter_failure(self):
        def boom(*args, **kwargs):
            raise RuntimeError("offline")
        self.exchange.candles = boom
        self.assertEqual(fetch_candles("BTC-USDT-SWAP"), [])

    def test_format_oi_uses_base_times_price_not_quote_times_price(self):
        text = format_oi_usd({"oi": "2", "oiCcy": "2"}, last_price=65000)
        self.assertIn("万", text)


if __name__ == "__main__":
    unittest.main()

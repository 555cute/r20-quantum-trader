import unittest
from scripts.market_data_service import (
    fetch_ticker,
    fetch_tickers_bulk,
    fetch_orderbook_depth,
    fetch_indicators_batch,
    fetch_single_indicator,
    fetch_candles,
    fetch_funding_rate,
)


class TestMarketDataService(unittest.TestCase):
    def test_fetch_ticker(self):
        ticker = fetch_ticker("BTC-USDT-SWAP")
        self.assertIsNotNone(ticker)
        self.assertIn("last", ticker)
        self.assertGreater(float(ticker["last"]), 0)

    def test_fetch_tickers_bulk(self):
        tickers = fetch_tickers_bulk("SWAP")
        self.assertIsInstance(tickers, dict)
        self.assertIn("BTC-USDT-SWAP", tickers)
        self.assertIn("ETH-USDT-SWAP", tickers)

    def test_fetch_orderbook_depth(self):
        ob = fetch_orderbook_depth("BTC-USDT-SWAP", sz=5)
        self.assertIsNotNone(ob)
        self.assertIn("bids", ob)
        self.assertIn("asks", ob)
        self.assertGreaterEqual(len(ob["bids"]), 1)

    def test_fetch_indicators_batch(self):
        inds = fetch_indicators_batch("BTC-USDT-SWAP", ["adx", "kdj", "bbwidth", "cmf"], bar="1H")
        self.assertIsInstance(inds, dict)
        self.assertIn("ADX", inds)
        self.assertIn("adx", inds["ADX"])

    def test_fetch_candles(self):
        candles = fetch_candles("BTC-USDT-SWAP", bar="15m", limit=10)
        self.assertIsInstance(candles, list)
        self.assertGreaterEqual(len(candles), 1)

    def test_fetch_funding_rate(self):
        fr = fetch_funding_rate("BTC-USDT-SWAP")
        self.assertIsNotNone(fr)
        self.assertIsInstance(fr, float)


if __name__ == "__main__":
    unittest.main()

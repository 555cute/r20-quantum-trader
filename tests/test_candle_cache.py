"""Process candle cache: reuse inside one venue, never across DEMO/LIVE."""
from __future__ import annotations

import threading
import unittest

from r20_exchange import candle_cache


class CandleCacheTests(unittest.TestCase):
    def setUp(self):
        candle_cache.clear()

    def tearDown(self):
        candle_cache.clear()

    def test_shorter_limit_reuses_longer_pull(self):
        calls = []

        def fetch(pull):
            calls.append(pull)
            return [[str(i)] for i in range(pull)]

        first = candle_cache.get_or_fetch("https://demo-fapi.binance.com", "BTC-USDT-SWAP", "15m", 24, fetch)
        second = candle_cache.get_or_fetch("https://demo-fapi.binance.com", "BTC-USDT-SWAP", "15m", 45, fetch)
        self.assertEqual(len(first), 24)
        self.assertEqual(len(second), 45)
        self.assertEqual(calls, [80])

    def test_empty_result_is_not_cached(self):
        calls = []

        def fetch(pull):
            calls.append(pull)
            return []

        self.assertEqual(candle_cache.get_or_fetch("https://demo-fapi.binance.com", "ETH-USDT-SWAP", "1H", 24, fetch), [])
        self.assertEqual(candle_cache.get_or_fetch("https://demo-fapi.binance.com", "ETH-USDT-SWAP", "1H", 24, fetch), [])
        self.assertEqual(len(calls), 2)

    def test_demo_and_live_venues_do_not_share_entries(self):
        def demo_fetch(pull):
            return [["demo", str(pull)]]

        def live_fetch(pull):
            return [["live", str(pull)]]

        demo = candle_cache.get_or_fetch("https://demo-fapi.binance.com", "BTC-USDT-SWAP", "15m", 24, demo_fetch)
        live = candle_cache.get_or_fetch("https://fapi.binance.com", "BTC-USDT-SWAP", "15m", 24, live_fetch)
        self.assertEqual(demo[0][0], "demo")
        self.assertEqual(live[0][0], "live")

    def test_inflight_coalesces_one_upstream_pull(self):
        calls = []
        started = threading.Event()
        release = threading.Event()

        def fetch(pull):
            calls.append(pull)
            started.set()
            release.wait(timeout=2)
            return [[str(pull)]]

        results = []

        def worker():
            results.append(
                candle_cache.get_or_fetch("https://demo-fapi.binance.com", "SOL-USDT-SWAP", "1H", 24, fetch)
            )

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        self.assertTrue(started.wait(timeout=2))
        second.start()
        release.set()
        first.join(timeout=2)
        second.join(timeout=2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(results, [[["80"]], [["80"]]])


if __name__ == "__main__":
    unittest.main()

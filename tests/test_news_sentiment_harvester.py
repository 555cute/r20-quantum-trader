"""Unit tests for the upgraded OKX & Jin10 News & Sentiment Harvester."""

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import scripts.news_sentiment_harvester as harvester


class TestNewsSentimentHarvester(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_news_")
        self.cache_file = os.path.join(self.tmp_dir, "news_sentiment.json")
        self.cb_file = os.path.join(self.tmp_dir, "circuit_breaker.json")
        self._patch_cache = patch.object(harvester, "NEWS_CACHE_FILE", self.cache_file)
        self._patch_cb = patch.object(harvester, "CIRCUIT_BREAKER_FILE", self.cb_file)
        self._patch_cache.start()
        self._patch_cb.start()

    def tearDown(self):
        self._patch_cache.stop()
        self._patch_cb.stop()
        for f in (self.cache_file, self.cb_file):
            if os.path.exists(f):
                os.remove(f)
        if os.path.exists(self.tmp_dir):
            os.rmdir(self.tmp_dir)

    def test_classify_importance(self):
        high = harvester._classify_importance("USDT 出现严重脱锚危机，市场暴跌", "")
        self.assertEqual(high, "high")

        mid = harvester._classify_importance("美联储宣布降息50个基点，CPI超预期", "")
        self.assertEqual(mid, "mid")

        low = harvester._classify_importance("某社区召开日常研讨会", "")
        self.assertEqual(low, "low")

    def test_extract_coins(self):
        coins = harvester._extract_coins("比特币突破新高，以太坊跟随上涨", "", ["BTC", "ETH", "SOL"])
        self.assertIn("BTC", coins)
        self.assertIn("ETH", coins)
        self.assertNotIn("SOL", coins)

    def test_fetch_okx_announcements_parsing(self):
        fake_resp = {
            "code": "0",
            "data": [
                {
                    "details": [
                        {
                            "title": "OKX to list NEWCOIN X-Perp",
                            "url": "https://www.okx.com/help/newcoin",
                            "pTime": "1789138000000",
                            "annType": "announcements-new-listings",
                        }
                    ]
                }
            ],
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_resp).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            items = harvester.fetch_okx_announcements(limit=5)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["title"], "OKX to list NEWCOIN X-Perp")
            self.assertIn("OKX官方", items[0]["platforms"])

    def test_fetch_jin10_macro_news_parsing(self):
        fake_jin10 = {
            "all": {
                "daily": {
                    "news": [
                        {"id": 1001, "title": "美国8月核心CPI月率超预期"}
                    ],
                    "updated_at": "2026-09-11 20:00:00",
                }
            }
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_jin10).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            items = harvester.fetch_jin10_macro_news(limit=5)
            self.assertTrue(len(items) >= 1)
            self.assertIn("金十数据", items[0]["platforms"])
            self.assertEqual(items[0]["title"], "美国8月核心CPI月率超预期")

    def test_fetch_okx_rubik_sentiment_parsing(self):
        fake_rubik = {
            "code": "0",
            "data": [["1789138000000", "1.50"]],
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_rubik).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = harvester.fetch_okx_rubik_sentiment("BTC")
            self.assertIsNotNone(res)
            self.assertEqual(res["ccy"], "BTC")
            self.assertEqual(res["label"], "bullish")
            self.assertEqual(res["long_short_ratio"], "1.50")
            self.assertEqual(res["bullish_ratio"], "60.0%")
            self.assertEqual(res["bearish_ratio"], "40.0%")

    def test_full_harvester_pipeline(self):
        fake_ann = [{
            "id": "okx-1",
            "title": "OKX系统维护正常完成",
            "summary": "OKX官方通告",
            "time": "2026-09-11 20:00:00",
            "cTime": "1789138000000",
            "url": "https://okx.com",
            "platforms": ["OKX官方"],
            "importance": "low",
        }]
        fake_j10 = [{
            "id": "jin10-1",
            "title": "美联储主席就通胀发表讲话",
            "summary": "金十数据要闻",
            "time": "2026-09-11 20:05:00",
            "cTime": "1789138300000",
            "url": "https://jin10.com",
            "platforms": ["金十数据"],
            "importance": "mid",
        }]
        fake_rubik = {
            "ccy": "BTC",
            "label": "bullish",
            "bullish_ratio": "58.8%",
            "bearish_ratio": "41.2%",
            "bullish_pct": "58.8%",
            "bearish_pct": "41.2%",
            "long_short_ratio": "1.43",
            "bull_cnt": 58,
            "bear_cnt": 41,
            "neutral_cnt": 0,
            "mentions": 100,
            "sentiment_factor_score": 0.30,
        }

        with patch.object(harvester, "fetch_okx_announcements", return_value=fake_ann), \
             patch.object(harvester, "fetch_jin10_macro_news", return_value=fake_j10), \
             patch.object(harvester, "fetch_okx_rubik_sentiment", return_value=fake_rubik), \
             patch.object(harvester, "load_instruments", return_value=[{"name": "BTC", "instId": "BTC-USDT-SWAP"}]):

            payload = harvester.fetch_and_analyze_news_sentiment()

            self.assertTrue(payload["source_available"])
            self.assertIn("OKX官方公告", payload["source_reason"])
            self.assertIn("金十数据", payload["source_reason"])
            self.assertEqual(len(payload["latest_news"]), 2)
            self.assertIn("BTC", payload["coins_sentiment"])
            self.assertEqual(payload["coins_sentiment"]["BTC"]["label"], "bullish")
            self.assertTrue(os.path.exists(self.cache_file))


if __name__ == "__main__":
    unittest.main()

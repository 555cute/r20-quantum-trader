"""Unit tests for the upgraded OKX & Jin10 News & Sentiment Harvester."""

import json
import os
import shutil
import tempfile
import time
import importlib
import unittest
from pathlib import Path
from unittest.mock import patch
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

    def test_fetch_crypto_rss_news_parsing(self):
        fake_rss_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
            <item>
                <title>Ethereum Layer-2 ecosystem records massive transaction volume</title>
                <link>https://cointelegraph.com/news/eth-l2</link>
                <description>Ethereum Layer-2 networks saw huge surge in activity.</description>
                <pubDate>Fri, 18 Sep 2026 12:00:00 +0000</pubDate>
            </item>
        </channel></rss>"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_rss_xml
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            items = harvester.fetch_crypto_rss_news(limit=5)
            self.assertTrue(len(items) >= 1)
            self.assertIn("Cointelegraph", items[0]["platforms"])
            self.assertIn("ETH", items[0]["coins"])
            self.assertIn("Ethereum", items[0]["title"])

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
            # 2026-09-16：Rubik 是「账户多空比」端点，与「新闻提及篇数」无关，
            # 此处**不得**再产出 mentions（旧版硬编码 100 → 前台每个币都显示「100 篇」）。
            self.assertNotIn("mentions", res)

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
            "title": "美联储主席就通胀发表讲话，比特币应声回落",
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
            "sentiment_factor_score": 0.30,
        }

        with patch.object(harvester, "fetch_okx_announcements", return_value=fake_ann), \
             patch.object(harvester, "fetch_crypto_rss_news", return_value=[]), \
             patch.object(harvester, "fetch_jin10_macro_news", return_value=fake_j10), \
             patch.object(harvester, "fetch_okx_rubik_sentiment", return_value=fake_rubik), \
             patch.object(harvester, "load_instruments", return_value=[{"name": "BTC", "instId": "BTC-USDT-SWAP"}]):

            payload = harvester.fetch_and_analyze_news_sentiment()

            self.assertTrue(payload["source_available"])
            # 2026-09-16：OKX 官方运营公告不再进展示流（仍参与黑天鹅正则体检）。
            self.assertNotIn("OKX官方公告", payload["source_reason"])
            self.assertIn("金十数据", payload["source_reason"])
            self.assertEqual(len(payload["latest_news"]), 1)
            self.assertNotIn("OKX官方", payload["latest_news"][0]["platforms"])
            self.assertIn("BTC", payload["coins_sentiment"])
            self.assertEqual(payload["coins_sentiment"]["BTC"]["label"], "bullish")
            # mentions 是**本轮实际入流快讯**的真实提及数（标题含「比特币」→ BTC 命中 1 条），
            # 不再是硬编码的 100。
            self.assertEqual(payload["coins_sentiment"]["BTC"]["mentions"], 1)
            self.assertTrue(os.path.exists(self.cache_file))

    def test_okx_announcements_still_scan_for_black_swan(self):
        """公告虽不进展示流，但**必须**继续参与黑天鹅体检（安全网不动）。"""
        cbs = tempfile.mkdtemp(prefix="test_news_cb_")
        cb_file = os.path.join(cbs, "circuit_breaker.json")
        ann = [{
            "id": "okx-cb",
            "title": "OKX 紧急公告：暂停全部提现以进行安全审计",
            "summary": "OKX官方通告",
            "time": "2026-09-11 20:00:00",
            "cTime": str(int(time.time() * 1000)),
            "url": "https://okx.com",
            "platforms": ["OKX官方"],
            "importance": "high",
        }]
        with patch.object(harvester, "CIRCUIT_BREAKER_FILE", cb_file), \
             patch.object(harvester, "fetch_okx_announcements", return_value=ann), \
             patch.object(harvester, "fetch_crypto_rss_news", return_value=[]), \
             patch.object(harvester, "fetch_jin10_macro_news", return_value=[]), \
             patch.object(harvester, "fetch_okx_rubik_sentiment", return_value=None), \
             patch.object(harvester, "load_instruments", return_value=[{"name": "BTC", "instId": "BTC-USDT-SWAP"}]):
            payload = harvester.fetch_and_analyze_news_sentiment()
            # 公告不进展示流……
            self.assertEqual(payload["latest_news"], [])
            # ……但熔断被触发（安全网保留）
            self.assertTrue(payload["circuit_breaker"].get("active"))
        shutil.rmtree(cbs, ignore_errors=True)


class CircuitBreakerUnknownStateTest(unittest.TestCase):
    """熔断文件读不出来 ⇒ 提示词/载荷必须说"不可判"，不能报平安（第一百四十六刀）。

    缺陷：`news_sentiment_harvester.is_circuit_breaker_active` 是权威判定器的**第二份实现**
    （只服务展示），其 `except: pass` 让"文件损坏"返回 `(False, {})` = "没在熔断"，
    于是 `macro_sentiment` 会写成"偏多震荡/偏空承压"—— 借"读不到"报了平安；
    而权威模块对同一情形是 **True（安全暂停开仓）**。展示侧不得比交易侧更乐观。
    """

    def setUp(self):
        import tempfile
        from pathlib import Path as _P
        self.tmp = tempfile.TemporaryDirectory(prefix="harvester-cb-")
        self.addCleanup(self.tmp.cleanup)
        self.cb_file = _P(self.tmp.name) / "circuit_breaker.json"

    def _run(self, content=None):
        mod = importlib.import_module("scripts.news_sentiment_harvester")
        with patch.object(mod, "CIRCUIT_BREAKER_FILE", str(self.cb_file)):
            if content is not None:
                self.cb_file.write_text(content, encoding="utf-8")
            return mod.is_circuit_breaker_active()

    def test_corrupt_file_is_unknown_not_calm(self):
        active, info = self._run('{"active": tru')
        self.assertFalse(active)
        self.assertTrue(info.get("unknown"), "损坏必须标成不可判（不是'没在熔断'）")
        self.assertIn("损坏", info.get("reason", ""))

    def test_missing_file_stays_legitimately_inactive(self):
        active, info = self._run()
        self.assertFalse(active)
        self.assertFalse(info.get("unknown"), "从未写过熔断文件是合法空态，不应算不可判")

    def test_active_breaker_still_reported_active(self):
        import json as _json
        import time as _time
        content = _json.dumps({"active": True, "expires_at_ts": _time.time() + 600})
        active, info = self._run(content)
        self.assertTrue(active)
        self.assertEqual(info.get("active"), True)

    def test_expired_breaker_is_not_active_and_not_unknown(self):
        import json as _json
        import time as _time
        content = _json.dumps({"active": True, "expires_at_ts": _time.time() - 10})
        active, info = self._run(content)
        self.assertFalse(active)
        self.assertFalse(info.get("unknown"))

    def test_callers_branch_on_unknown_before_claiming_calm(self):
        """源码钉：两处"报平安"分支都必须先排除 unknown（含陈旧回退路径）。"""
        src = (Path(__file__).resolve().parents[2] / "scripts"
               / "news_sentiment_harvester.py").read_text(encoding="utf-8")
        self.assertIn('elif cb_info.get("unknown"):', src)
        self.assertIn("熔断状态不可判", src)
        self.assertIn('if not cb_active and not cb_info.get("unknown"):', src,
                      "陈旧回退路径也必须排除不可判（否则仍会写'偏多震荡'）")

if __name__ == "__main__":
    unittest.main()
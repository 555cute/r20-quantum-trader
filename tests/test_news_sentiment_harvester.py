"""Unit tests for the upgraded Crypto-Flash & OKX & Jin10 News & Sentiment Harvester."""

import datetime
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

    def test_fetch_crypto_flash_news_parsing(self):
        """US-002 加密快讯流：金色财经实时快讯开放流解析——【】标题提取、正文去前缀、
        无标题取首句、空白条目丢弃、北京时间与影响等级/币种识别。"""
        fake_flash = {
            "list": [
                {
                    "date": "2026-09-12",
                    "lives": [
                        {
                            "id": 9001,
                            "created_at": 1789138000,
                            "content": "【比特币突破12万美元创历史新高】巨鲸地址大额增持BTC，以太坊ETH同步走强。",
                        },
                        {
                            "id": 9002,
                            "created_at": 1789138060,
                            "content": "某交易所因遭黑客攻击已暂停提币，用户资产被盗。",
                        },
                        {"id": 9003, "created_at": 1789138120, "content": "   "},
                    ],
                },
                {"date": "2026-09-11", "lives": []},
            ]
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_flash).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            items = harvester.fetch_crypto_flash_news(limit=25)

        # 请求必须打到 coinmeta 实时快讯开放流
        called_url = mock_open.call_args[0][0].full_url
        self.assertIn("api.coinmeta.info/live/list", called_url)

        # 空白正文条目被丢弃，共 2 条
        self.assertEqual(len(items), 2)

        first = items[0]
        self.assertEqual(first["id"], "crypto-9001")
        # 【...】 提取为标题，正文移除标题前缀
        self.assertEqual(first["title"], "比特币突破12万美元创历史新高")
        self.assertNotIn("【", first["summary"])
        self.assertEqual(first["summary"], "巨鲸地址大额增持BTC，以太坊ETH同步走强。")
        self.assertEqual(first["platforms"], ["加密快讯"])
        self.assertEqual(first["url"], "https://www.jinse.cn")
        # 毫秒时间戳与北京时间展示串
        self.assertEqual(first["cTime"], "1789138000000")
        tz_bj = datetime.timezone(datetime.timedelta(hours=8))
        self.assertEqual(
            first["time"],
            datetime.datetime.fromtimestamp(1789138000, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S"),
        )
        # 币种识别与影响等级（创历史新高 → mid，非盲目 high）
        self.assertIn("BTC", first["coins"])
        self.assertIn("ETH", first["coins"])
        self.assertEqual(first["importance"], "mid")

        second = items[1]
        # 无【】时取首句为标题
        self.assertEqual(second["title"], "某交易所因遭黑客攻击已暂停提币，用户资产被盗")
        self.assertEqual(second["importance"], "high")
        self.assertEqual(second["id"], "crypto-9002")

        # limit 截断生效
        with patch("urllib.request.urlopen", return_value=mock_resp):
            limited = harvester.fetch_crypto_flash_news(limit=1)
        self.assertEqual(len(limited), 1)

        # 抓取异常必须 fail-soft：返回空列表而非抛出
        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            self.assertEqual(harvester.fetch_crypto_flash_news(limit=5), [])

    def test_fetch_okx_announcements_parsing(self):
        """US-001 降噪：发新币/赚币理财类公告必须被剔除，下架与维护类安全公告必须保留。"""
        fake_resp = {
            "code": "0",
            "data": [
                {
                    "details": [
                        {
                            "title": "欧易关于 ORCLUSD X-合约（X-Perp）正式上线的公告",
                            "url": "https://www.okx.com/help/newcoin",
                            "pTime": "1789138000000",
                            "annType": "announcements-new-listings",
                        },
                        {
                            "title": "Spark USDT (X Layer) 链上赚币产品上线",
                            "url": "https://www.okx.com/help/earn",
                            "pTime": "1789138000001",
                            "annType": "announcements-earn-and-loan",
                        },
                        {
                            "title": "欧易关于 ICXUSDT 永续合约下线的公告",
                            "url": "https://www.okx.com/help/icx",
                            "pTime": "1789138000002",
                            "annType": "announcements-delistings",
                        },
                        {
                            "title": "欧易关于系统升级维护的公告",
                            "url": "https://www.okx.com/help/maintenance",
                            "pTime": "1789138000003",
                            "annType": "公告",
                        },
                    ]
                }
            ],
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_resp).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            items = harvester.fetch_okx_announcements(limit=10)
            titles = [it["title"] for it in items]
            # 发新币与赚币理财类噪音必须被彻底过滤
            self.assertNotIn("欧易关于 ORCLUSD X-合约（X-Perp）正式上线的公告", titles)
            self.assertNotIn("Spark USDT (X Layer) 链上赚币产品上线", titles)
            # 下架风险与系统维护类安全公告必须保留
            self.assertEqual(len(items), 2)
            self.assertIn("欧易关于 ICXUSDT 永续合约下线的公告", titles)
            self.assertIn("欧易关于系统升级维护的公告", titles)
            self.assertIn("OKX官方", items[0]["platforms"])

    def test_okx_ann_is_actionable_english(self):
        """英文标题兜底：list/launch 类剔除，delist/maintenance 类保留（\b 边界不误伤 delist）。"""
        self.assertFalse(harvester._okx_ann_is_actionable("OKX to list NEWCOIN X-Perp", "announcements-new-listings"))
        self.assertFalse(harvester._okx_ann_is_actionable("OKX Launches Tokenized Stocks Trading", "公告"))
        self.assertTrue(harvester._okx_ann_is_actionable("OKX to delist perpetual futures for ICXUSDT", "announcements-delistings"))
        self.assertTrue(harvester._okx_ann_is_actionable("Scheduled System Maintenance Notice", "公告"))
        self.assertTrue(harvester._okx_ann_is_actionable("Notice on Adjustment of Margin and Position Limits", "公告"))
        self.assertFalse(harvester._okx_ann_is_actionable("欧易上线全新理财产品赚币活动", "公告"))

    def test_okx_ann_is_actionable_verifier_cases(self):
        """verifier 反馈用例：白名单栏目泛化噪音词不得误杀（规则a）、
        出入金暂停强安全词优先于噪音词（规则b）、明确发新币上线指示词剔除。"""
        # 规则a：栏目在白名单内，标题含"充值/借贷/上线"等泛化噪音词也必须保留
        self.assertTrue(harvester._okx_ann_is_actionable(
            "关于暂停 BTC 充值的公告", "announcements-system-maintenance"))
        self.assertTrue(harvester._okx_ann_is_actionable(
            "关于借贷业务风控参数调整的公告", "announcements-risk"))
        self.assertTrue(harvester._okx_ann_is_actionable(
            "关于下架部分上线时间较短合约的公告", "announcements-delistings"))

        # 规则b：栏目未知时，出入金暂停强安全词优先于泛化噪音词（提币黑天鹅安全词）
        self.assertTrue(harvester._okx_ann_is_actionable(
            "欧易关于暂停全部提现的公告", "公告"))
        self.assertTrue(harvester._okx_ann_is_actionable(
            "欧易关于停止提币与充值的公告", "公告"))

        # 明确的发新币/新合约上线指示词：无论栏目未知还是营销栏目，一律剔除
        self.assertFalse(harvester._okx_ann_is_actionable(
            "欧易关于 ORCLUSD X-合约（X-Perp）正式上线的公告", "公告"))
        self.assertFalse(harvester._okx_ann_is_actionable(
            "欧易关于 ORCLUSD X-合约（X-Perp）正式上线的公告", "announcements-new-listings"))

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
        fake_crypto = [{
            "id": "crypto-9001",
            "title": "比特币突破12万美元创历史新高",
            "summary": "巨鲸地址大额增持BTC。",
            "time": "2026-09-11 20:06:40",
            "cTime": "1789138600000",
            "url": "https://www.jinse.cn",
            "platforms": ["加密快讯"],
            "coins": ["BTC"],
            "importance": "mid",
        }]
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

        with patch.object(harvester, "fetch_crypto_flash_news", return_value=fake_crypto) as mock_crypto, \
             patch.object(harvester, "fetch_okx_announcements", return_value=fake_ann), \
             patch.object(harvester, "fetch_jin10_macro_news", return_value=fake_j10), \
             patch.object(harvester, "fetch_okx_rubik_sentiment", return_value=fake_rubik), \
             patch.object(harvester, "load_instruments", return_value=[{"name": "BTC", "instId": "BTC-USDT-SWAP"}]):

            payload = harvester.fetch_and_analyze_news_sentiment()

            # 三源聚合并存：加密快讯流为第一优先数据源
            mock_crypto.assert_called_once_with(limit=25)
            self.assertTrue(payload["source_available"])
            self.assertIn("加密货币快讯", payload["source_reason"])
            self.assertIn("金十数据", payload["source_reason"])
            self.assertEqual(len(payload["latest_news"]), 3)
            # 按 cTime 倒序：加密快讯最新，排在最前
            self.assertEqual(payload["latest_news"][0]["id"], "crypto-9001")
            self.assertEqual(payload["latest_news"][0]["platforms"], ["加密快讯"])
            self.assertIn("BTC", payload["coins_sentiment"])
            self.assertEqual(payload["coins_sentiment"]["BTC"]["label"], "bullish")
            self.assertTrue(os.path.exists(self.cache_file))


if __name__ == "__main__":
    unittest.main()

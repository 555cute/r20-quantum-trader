"""Offline news harvester regressions. Network and notifications are forbidden."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from unittest import TestCase
from unittest.mock import Mock, patch

import scripts.news_sentiment_harvester as harvester

ROOT = Path(__file__).resolve().parents[1]
PROD_CACHE = ROOT / "data" / "news_sentiment.json"
PROD_CB = ROOT / "data" / "circuit_breaker.json"
FROZEN_TS = 1_700_000_000.0
SWAN_TITLE = "币安暂停全部提现并发生严重挤兑"


class _FakeHTTPResponse:
    def __init__(self, body, status=200):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.code = status

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _cms(catalogs):
    return json.dumps({"code": "000000", "data": {"catalogs": catalogs}})


def _okx_proc(payload) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["okx"], 0, stdout=json.dumps(payload), stderr="")


def _snapshot(path: Path):
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


class NewsHarvesterTests(TestCase):
    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def setUp(self):
        self._prod_cache = _snapshot(PROD_CACHE)
        self._prod_cb = _snapshot(PROD_CB)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.cache = self.data / "news_sentiment.json"
        self.cb = self.data / "circuit_breaker.json"

        self.start_patch(patch.object(harvester, "DATA_DIR", str(self.data)))
        self.start_patch(patch.object(harvester, "NEWS_CACHE_FILE", str(self.cache)))
        self.start_patch(patch.object(harvester, "CIRCUIT_BREAKER_FILE", str(self.cb)))
        self.start_patch(patch.object(
            harvester,
            "load_instruments",
            return_value=[{"name": "BTC"}, {"name": "ETH"}],
        ))
        self.selected = self.start_patch(patch.object(
            harvester, "selected_sources", return_value=("okx",)
        ))
        self.start_patch(patch.object(
            harvester.subprocess, "run",
            side_effect=AssertionError("subprocess forbidden"),
        ))
        self.start_patch(patch.object(
            harvester.urllib.request, "urlopen",
            side_effect=AssertionError("HTTP forbidden"),
        ))
        self.start_patch(patch.object(harvester.time, "sleep", return_value=None))
        self.start_patch(patch(
            "socket.create_connection",
            side_effect=AssertionError("network forbidden"),
        ))

        self.notify = Mock()
        qq = ModuleType("qq_notifier")
        qq.notify_circuit_breaker = self.notify
        self._prev_qq = sys.modules.get("qq_notifier")
        sys.modules["qq_notifier"] = qq
        self.addCleanup(self._restore_qq)

    def tearDown(self):
        self.assertEqual(_snapshot(PROD_CACHE), self._prod_cache)
        self.assertEqual(_snapshot(PROD_CB), self._prod_cb)
        self.assertTrue(str(self.cache.resolve()).startswith(str(self.root.resolve())))

    def _restore_qq(self):
        if self._prev_qq is None:
            sys.modules.pop("qq_notifier", None)
        else:
            sys.modules["qq_notifier"] = self._prev_qq

    def _seed_cache(self, payload: dict) -> None:
        self.cache.write_text(json.dumps(payload), encoding="utf-8")

    def _binance_urlopen(self, body, media=None):
        media_bodies = media or {}

        def urlopen(req, timeout=None, **kwargs):
            url = getattr(req, "full_url", str(req))
            parsed = urllib.parse.urlsplit(url)
            self.assertEqual(parsed.scheme, "https")
            headers = {key.lower(): value for key, value in req.header_items()}
            self.assertNotIn("authorization", headers)
            self.assertNotIn("x-mbx-apikey", headers)
            self.assertEqual(timeout, harvester.BINANCE_HTTP_TIMEOUT)
            if url == harvester.BINANCE_CMS_URL:
                self.assertEqual(parsed.hostname, "www.binance.com")
                return _FakeHTTPResponse(body)
            allowed = {feed["url"]: feed for feed in harvester.BINANCE_MEDIA_FEEDS}
            self.assertIn(url, allowed)
            self.assertEqual(parsed.hostname, allowed[url]["host"])
            payload = media_bodies.get(url, "<rss version='2.0'><channel></channel></rss>")
            return _FakeHTTPResponse(payload)

        return urlopen


    def test_binance_only_skips_okx_cli_and_filters_catalogs(self):
        self.selected.return_value = ("binance",)
        now_ms = int(FROZEN_TS * 1000)
        catalogs = [
            {
                "catalogId": 48,
                "catalogName": "New Cryptocurrency Listing",
                "articles": [
                    {
                        "id": 1001,
                        "code": "btc-list-ok",
                        "title": "<b>BTC</b> will be listed",
                        "releaseDate": now_ms - 60_000,
                    },
                    {
                        "id": 1002,
                        "code": "all-major",
                        "title": "ALL tokens upgraded",
                        "releaseDate": now_ms - 30_000,
                    },
                ],
            },
            {
                "catalogId": 93,
                "catalogName": "Activities",
                "articles": [{
                    "id": 9301,
                    "code": "promo-btc",
                    "title": "BTC airdrop carnival",
                    "releaseDate": now_ms - 10_000,
                }],
            },
            {
                "catalogId": 128,
                "catalogName": "Airdrop",
                "articles": [{
                    "id": 12801,
                    "code": "drop-eth",
                    "title": "ETH airdrop",
                    "releaseDate": now_ms - 5_000,
                }],
            },
        ]
        with patch.object(harvester.urllib.request, "urlopen", self._binance_urlopen(_cms(catalogs))), \
             patch.object(harvester.time, "time", return_value=FROZEN_TS):
            payload = harvester.fetch_and_analyze_news_sentiment()

        ids = [row["id"] for row in payload["latest_news"]]
        self.assertEqual(payload["enabled_sources"], ["binance"])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["source_status"]["binance"]["status"], "ok")
        self.assertEqual(payload["source_status"]["okx"]["status"], "disabled")
        self.assertIn("binance:1001", ids)
        self.assertIn("binance:1002", ids)
        self.assertTrue(all(not str(item_id).startswith("okx:") for item_id in ids))
        self.assertNotIn("binance:9301", ids)
        self.assertNotIn("binance:12801", ids)
        listed = next(row for row in payload["latest_news"] if row["id"] == "binance:1001")
        self.assertEqual(listed["title"], "BTC will be listed")
        self.assertEqual(listed["coins"], ["BTC"])
        self.assertEqual(listed["summary"], "")
        self.assertEqual(listed["source"], "binance")
        self.assertEqual(listed["url"], "https://www.binance.com/en/support/announcement/detail/btc-list-ok")
        all_row = next(row for row in payload["latest_news"] if row["id"] == "binance:1002")
        self.assertEqual(all_row["coins"], [])
        self.assertEqual(payload["coins_sentiment"], {})
        self.assertEqual(payload["source_news"]["okx"], [])
        self.assertFalse(payload["sentiment_stale"])
        self.assertEqual(payload["macro_sentiment"], harvester.MACRO_UNAVAILABLE)
        self.assertNotIn("中性", payload["macro_sentiment"])
        self.assertTrue(self.cache.exists())

    def test_dual_source_partial_failure_keeps_other_source(self):
        self.selected.return_value = ("okx", "binance")
        now_ms = int(FROZEN_TS * 1000)
        old_bn = {
            "id": "binance:old",
            "time": "2023-01-01 00:00:00",
            "title": "old binance",
            "summary": "",
            "coins": ["BTC"],
            "platforms": ["Binance"],
            "importance": "",
            "url": "https://www.binance.com/en/support/announcement/detail/old",
            "source": "binance",
            "source_name": "币安官方公告",
            "category": "Listing",
            "published_at": now_ms - 120_000,
            "stale": False,
        }
        self._seed_cache({
            "latest_news": [],
            "source_news": {"okx": [], "binance": [old_bn]},
            "coins_sentiment": {},
            "source_status": {
                "binance": {"status": "ok", "count": 1, "updated_at": "old", "error": None},
            },
        })

        okx_items = [{
            "id": str(i),
            "title": f"ETH tape {i}",
            "summary": "desk",
            "cTime": now_ms - (10 - i) * 1_000,
            "ccyList": ["ETH"],
            "platformList": ["OKX"],
            "importance": "high",
            "sourceUrl": f"https://www.okx.com/help/eth{i}",
        } for i in range(10)]

        def okx_run(cmd, **kwargs):
            self.assertIn("okx news", cmd)
            if "coin-sentiment" in cmd:
                return _okx_proc([{"details": [{
                    "ccy": "BTC",
                    "mentionCnt": 8,
                    "sentiment": {
                        "bullishRatio": 0.8,
                        "bearishRatio": 0.2,
                        "bullishCnt": 8,
                        "bearishCnt": 2,
                        "neutralCnt": 0,
                        "label": "bullish",
                    },
                }]}])
            if "important" in cmd:
                return _okx_proc({"details": []})
            if "latest" in cmd:
                return _okx_proc({"details": okx_items})
            raise AssertionError(cmd)

        with patch.object(harvester.subprocess, "run", okx_run), \
             patch.object(
                 harvester.urllib.request, "urlopen",
                 side_effect=urllib.error.URLError("timeout"),
             ), \
             patch.object(harvester.time, "time", return_value=FROZEN_TS):
            payload = harvester.fetch_and_analyze_news_sentiment()

        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["source_status"]["okx"]["status"], "ok")
        self.assertEqual(payload["source_status"]["binance"]["status"], "stale")
        self.assertEqual(payload["source_status"]["okx"]["count"], 10)
        self.assertEqual(payload["source_status"]["binance"]["count"], 1)
        self.assertEqual({row["source"] for row in payload["latest_news"]}, {"okx", "binance"})
        self.assertEqual(len(payload["latest_news"]), 10)
        bn_visible = [row for row in payload["latest_news"] if row["source"] == "binance"]
        self.assertEqual([row["id"] for row in bn_visible], ["binance:old"])
        self.assertTrue(bn_visible[0]["stale"])
        bn_cached = payload["source_news"]["binance"]
        self.assertEqual([row["id"] for row in bn_cached], ["binance:old"])
        self.assertTrue(bn_cached[0]["stale"])
        self.assertEqual(len(payload["source_news"]["okx"]), 10)
        self.assertTrue(all(not row["stale"] for row in payload["source_news"]["okx"]))
        self.assertIn("BTC", payload["coins_sentiment"])
        self.assertNotEqual(payload["coins_sentiment"]["BTC"].get("bullish_pct"), "50.0%")
        self.assertTrue(payload["stale_sections"])
        self.assertFalse(payload["sentiment_stale"])

    def test_disable_all_makes_zero_requests_and_is_not_neutral(self):
        self.selected.return_value = ()
        self._seed_cache({
            "latest_news": [{
                "id": "okx:1",
                "title": "cached",
                "source": "okx",
                "published_at": 1,
                "stale": False,
                "url": "https://www.okx.com/x",
                "time": "t",
                "summary": "",
                "coins": [],
                "platforms": [],
                "importance": "high",
                "source_name": "OKX",
                "category": "",
            }],
            "coins_sentiment": {"BTC": {"mentions": 3, "bull_cnt": 2, "bear_cnt": 1, "sentiment_factor_score": 0.4}},
            "macro_sentiment": "中性平衡",
        })
        shutil.rmtree(self.data)
        payload = harvester.fetch_and_analyze_news_sentiment()
        self.assertEqual(payload["status"], "disabled")
        self.assertEqual(payload["enabled_sources"], [])
        self.assertEqual(payload["latest_news"], [])
        self.assertEqual(payload["source_news"]["okx"], [])
        self.assertEqual(payload["source_news"]["binance"], [])
        self.assertFalse(payload["sentiment_stale"])
        self.assertEqual(payload["coins_sentiment"], {})
        self.assertEqual(payload["macro_sentiment"], harvester.MACRO_UNAVAILABLE)
        self.assertNotEqual(payload["macro_sentiment"], "中性平衡")
        self.assertEqual(payload["source_status"]["okx"]["status"], "disabled")
        self.assertEqual(payload["source_status"]["binance"]["status"], "disabled")
        self.assertTrue(self.cache.exists())

    def test_source_id_prefix_dedup(self):
        self.selected.return_value = ("okx", "binance")
        now_ms = int(FROZEN_TS * 1000)

        def okx_run(cmd, **kwargs):
            if "coin-sentiment" in cmd:
                return _okx_proc([{"details": []}])
            details = [
                {"id": "55", "title": "first okx", "summary": "", "cTime": now_ms - 10_000,
                 "ccyList": ["BTC"], "platformList": [], "sourceUrl": "https://www.okx.com/a"},
                {"id": "55", "title": "dup okx", "summary": "", "cTime": now_ms - 9_000,
                 "ccyList": ["BTC"], "platformList": [], "sourceUrl": "https://www.okx.com/b"},
            ]
            return _okx_proc({"details": details if "latest" in cmd else []})

        catalogs = [{
            "catalogId": 49,
            "catalogName": "Latest Binance News",
            "articles": [
                {"id": "55", "code": "bn-55", "title": "binance same numeric id", "releaseDate": now_ms - 8_000},
                {"id": "55", "code": "bn-55-dup", "title": "binance dup id dropped", "releaseDate": now_ms - 7_000},
            ],
        }]
        with patch.object(harvester.subprocess, "run", okx_run), \
             patch.object(harvester.urllib.request, "urlopen", self._binance_urlopen(_cms(catalogs))), \
             patch.object(harvester.time, "time", return_value=FROZEN_TS):
            payload = harvester.fetch_and_analyze_news_sentiment()

        ids = [row["id"] for row in payload["latest_news"]]
        self.assertEqual(ids.count("okx:55"), 1)
        self.assertEqual(ids.count("binance:55"), 1)
        self.assertIn("okx:55", ids)
        self.assertIn("binance:55", ids)

    def test_safe_url_rejects_unsafe_codes_and_schemes(self):
        self.selected.return_value = ("binance",)
        now_ms = int(FROZEN_TS * 1000)
        catalogs = [{
            "catalogId": 51,
            "catalogName": "API Updates",
            "articles": [
                {"id": "u1", "code": "../etc/passwd", "title": "bad path", "releaseDate": now_ms},
                {"id": "u2", "code": "https://evil.example/x", "title": "absolute", "releaseDate": now_ms},
                {"id": "u3", "code": "ok_code-1", "title": "good", "releaseDate": now_ms,
                 "url": "javascript:alert(1)"},
                {"id": "u4", "code": "has/slash", "title": "slash", "releaseDate": now_ms},
            ],
        }]
        with patch.object(harvester.urllib.request, "urlopen", self._binance_urlopen(_cms(catalogs))), \
             patch.object(harvester.time, "time", return_value=FROZEN_TS):
            payload = harvester.fetch_and_analyze_news_sentiment()

        urls = {row["id"]: row["url"] for row in payload["latest_news"]}
        self.assertEqual(list(urls), ["binance:u3"])
        self.assertEqual(urls["binance:u3"], "https://www.binance.com/en/support/announcement/detail/ok_code-1")
        self.assertTrue(urls["binance:u3"].startswith("https://www.binance.com/"))
        self.assertNotIn("evil.example", json.dumps(payload["latest_news"]))
        self.assertNotIn("javascript:", json.dumps(payload["latest_news"]))

    def test_valid_and_missing_sentiment_never_fabricates_50_50(self):
        self.selected.return_value = ("okx",)
        now_ms = int(FROZEN_TS * 1000)
        news = {"details": [{
            "id": "s1", "title": "BTC tape", "summary": "", "cTime": now_ms - 15_000,
            "ccyList": ["BTC"], "platformList": [], "sourceUrl": "https://www.okx.com/n",
        }]}

        def okx_run_valid(cmd, **kwargs):
            if "coin-sentiment" in cmd:
                return _okx_proc([{"details": [{
                    "ccy": "BTC",
                    "mentionCnt": 10,
                    "sentiment": {
                        "bullishRatio": 0.7,
                        "bearishRatio": 0.3,
                        "bullishCnt": 7,
                        "bearishCnt": 3,
                        "neutralCnt": 0,
                        "label": "bullish",
                    },
                }]}])
            return _okx_proc(news if "latest" in cmd else {"details": []})

        with patch.object(harvester.subprocess, "run", okx_run_valid), \
             patch.object(harvester.time, "time", return_value=FROZEN_TS):
            valid = harvester.fetch_and_analyze_news_sentiment()
        self.assertEqual(valid["coins_sentiment"]["BTC"]["bull_cnt"], 7)
        self.assertNotEqual(valid["coins_sentiment"]["BTC"]["bullish_pct"], "50.0%")
        self.assertNotIn("ETH", valid["coins_sentiment"])
        self.assertEqual(valid["macro_sentiment"], "偏多震荡")
        self.assertFalse(valid["sentiment_stale"])

        def okx_run_missing(cmd, **kwargs):
            if "coin-sentiment" in cmd:
                return _okx_proc([{"details": [{"ccy": "BTC", "mentionCnt": 0, "sentiment": {}}]}])
            return _okx_proc(news if "latest" in cmd else {"details": []})

        with patch.object(harvester.subprocess, "run", okx_run_missing), \
             patch.object(harvester.time, "time", return_value=FROZEN_TS):
            missing = harvester.fetch_and_analyze_news_sentiment()
        self.assertEqual(missing["coins_sentiment"]["BTC"]["bull_cnt"], 7)
        self.assertTrue(missing["stale_sections"])
        self.assertNotEqual(missing["coins_sentiment"]["BTC"].get("bullish_ratio"), "50.0%")
        self.assertTrue(missing["sentiment_stale"])

        self.cache.unlink()

        def okx_run_fail(cmd, **kwargs):
            if "coin-sentiment" in cmd:
                raise AssertionError("sentiment transport failed")
            return _okx_proc(news if "latest" in cmd else {"details": []})

        with patch.object(harvester.subprocess, "run", okx_run_fail), \
             patch.object(harvester.time, "time", return_value=FROZEN_TS):
            failed = harvester.fetch_and_analyze_news_sentiment()
        self.assertEqual(failed["coins_sentiment"], {})
        self.assertEqual(failed["macro_sentiment"], harvester.MACRO_UNAVAILABLE)
        self.assertTrue(failed["sentiment_stale"])
        self.assertNotIn("50.0%", json.dumps(failed["coins_sentiment"]))

    def test_closed_source_rows_are_not_reused(self):
        self.selected.return_value = ("binance",)
        now_ms = int(FROZEN_TS * 1000)
        self._seed_cache({
            "latest_news": [
                {
                    "id": "okx:legacy",
                    "title": "old okx should drop",
                    "source": "okx",
                    "published_at": now_ms,
                    "stale": False,
                    "url": "https://www.okx.com/legacy",
                    "time": "t",
                    "summary": "",
                    "coins": ["BTC"],
                    "platforms": [],
                    "importance": "high",
                    "source_name": "OKX 聚合资讯",
                    "category": "",
                },
                {
                    "id": "99",
                    "title": "legacy untagged okx format",
                    "summary": "ccy",
                    "coins": ["ETH"],
                    "platforms": ["OKX"],
                    "importance": "high",
                    "url": "https://www.okx.com/old",
                    "time": "t",
                },
            ],
            "coins_sentiment": {"BTC": {"mentions": 4, "bull_cnt": 3, "bear_cnt": 1, "sentiment_factor_score": 0.5}},
        })
        catalogs = [{
            "catalogId": 157,
            "catalogName": "Maintenance",
            "articles": [{
                "id": "m1",
                "code": "maint-1",
                "title": "spot wallet maintenance",
                "releaseDate": now_ms - 1_000,
            }],
        }]
        with patch.object(harvester.urllib.request, "urlopen", self._binance_urlopen(_cms(catalogs))), \
             patch.object(harvester.time, "time", return_value=FROZEN_TS):
            payload = harvester.fetch_and_analyze_news_sentiment()

        self.assertEqual([row["source"] for row in payload["latest_news"]], ["binance"])
        self.assertTrue(all(row["id"].startswith("binance:") for row in payload["latest_news"]))
        self.assertEqual(payload["coins_sentiment"], {})
        self.assertEqual(payload["source_status"]["okx"]["status"], "disabled")
        self.assertEqual(payload["source_status"]["okx"]["count"], 0)
        self.assertEqual(payload["source_news"]["okx"], [])
        self.assertFalse(payload["sentiment_stale"])

    def test_failure_is_not_reported_as_empty(self):
        self.selected.return_value = ("binance",)
        now_ms = int(FROZEN_TS * 1000)
        with patch.object(
            harvester.urllib.request, "urlopen",
            side_effect=urllib.error.URLError("429"),
        ), patch.object(harvester.time, "time", return_value=FROZEN_TS):
            failed = harvester.fetch_and_analyze_news_sentiment()
        self.assertEqual(failed["source_status"]["binance"]["status"], "error")
        self.assertNotEqual(failed["source_status"]["binance"]["status"], "empty")
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["latest_news"], [])
        self.assertTrue(failed["source_status"]["binance"]["error"])
        self.assertEqual(failed["source_news"]["binance"], [])
        self.assertFalse(failed["sentiment_stale"])

        self._seed_cache({
            "latest_news": [{
                "id": "binance:keep",
                "title": "previous",
                "source": "binance",
                "published_at": now_ms - 3_000,
                "stale": False,
                "url": "https://www.binance.com/en/support/announcement/detail/keep",
                "time": "t",
                "summary": "",
                "coins": [],
                "platforms": ["Binance"],
                "importance": "",
                "source_name": "币安官方公告",
                "category": "API",
            }],
            "source_status": {"binance": {"status": "ok", "count": 1, "updated_at": "prev", "error": None}},
        })
        with patch.object(
            harvester.urllib.request, "urlopen",
            side_effect=urllib.error.URLError("429"),
        ), patch.object(harvester.time, "time", return_value=FROZEN_TS):
            stale = harvester.fetch_and_analyze_news_sentiment()
        self.assertEqual(stale["source_status"]["binance"]["status"], "stale")
        self.assertEqual(stale["latest_news"][0]["id"], "binance:keep")
        self.assertTrue(stale["latest_news"][0]["stale"])
        self.assertNotEqual(stale["source_status"]["binance"]["status"], "empty")
        self.assertEqual(stale["source_news"]["binance"][0]["id"], "binance:keep")

        with patch.object(harvester.urllib.request, "urlopen", self._binance_urlopen(_cms([]))), \
             patch.object(harvester.time, "time", return_value=FROZEN_TS):
            empty = harvester.fetch_and_analyze_news_sentiment()
        self.assertEqual(empty["source_status"]["binance"]["status"], "empty")
        self.assertEqual(empty["latest_news"], [])
        self.assertIsNone(empty["source_status"]["binance"]["error"])
        self.assertEqual(empty["source_news"]["binance"], [])

    def test_future_and_cached_news_do_not_trigger_breaker(self):
        self.selected.return_value = ("binance",)
        now_ms = int(FROZEN_TS * 1000)

        future_catalogs = [{
            "catalogId": 49,
            "catalogName": "Latest Binance News",
            "articles": [{
                "id": "future",
                "code": "future-swan",
                "title": SWAN_TITLE,
                "releaseDate": now_ms + 60_000,
            }],
        }]
        with patch.object(harvester.urllib.request, "urlopen", self._binance_urlopen(_cms(future_catalogs))), \
             patch.object(harvester.time, "time", return_value=FROZEN_TS):
            future = harvester.fetch_and_analyze_news_sentiment()
        self.assertEqual(future["latest_news"][0]["title"], SWAN_TITLE)
        self.assertFalse(future["circuit_breaker"].get("active"))
        self.assertFalse(self.cb.exists())
        self.notify.assert_not_called()

        self._seed_cache({
            "latest_news": [{
                "id": "binance:cached-swan",
                "title": SWAN_TITLE,
                "source": "binance",
                "published_at": now_ms - 30_000,
                "stale": False,
                "url": "https://www.binance.com/en/support/announcement/detail/cached",
                "time": "t",
                "summary": "",
                "coins": [],
                "platforms": ["Binance"],
                "importance": "",
                "source_name": "币安官方公告",
                "category": "News",
            }],
        })
        with patch.object(
            harvester.urllib.request, "urlopen",
            side_effect=urllib.error.URLError("down"),
        ), patch.object(harvester.time, "time", return_value=FROZEN_TS):
            cached = harvester.fetch_and_analyze_news_sentiment()
        self.assertTrue(cached["latest_news"][0]["stale"])
        self.assertFalse(cached["circuit_breaker"].get("active"))
        self.notify.assert_not_called()
        self.assertFalse(self.cb.exists() and json.loads(self.cb.read_text(encoding="utf-8")).get("active"))

        live_catalogs = [{
            "catalogId": 49,
            "catalogName": "Latest Binance News",
            "articles": [{
                "id": "live",
                "code": "live-swan",
                "title": SWAN_TITLE,
                "releaseDate": now_ms - 30_000,
            }],
        }]
        with patch.object(harvester.urllib.request, "urlopen", self._binance_urlopen(_cms(live_catalogs))), \
             patch.object(harvester.time, "time", return_value=FROZEN_TS):
            live = harvester.fetch_and_analyze_news_sentiment()
        self.assertTrue(live["circuit_breaker"].get("active"))
        self.notify.assert_called_once()
        self.assertTrue(self.cb.exists())

    def test_binance_media_rss_folds_into_binance_source(self):
        self.selected.return_value = ("binance",)
        now_ms = int(FROZEN_TS * 1000)
        catalogs = [{
            "catalogId": 48,
            "catalogName": "New Cryptocurrency Listing",
            "articles": [{
                "id": 48,
                "code": "list-ok",
                "title": "BTC listed",
                "releaseDate": now_ms - 10_000,
            }],
        }]
        from email.utils import formatdate
        rss = (
            "<?xml version='1.0'?><rss version='2.0'><channel>"
            f"<item><title>ETH ETF inflows</title><link>https://www.coindesk.com/eth-etf</link>"
            f"<guid>cd-eth-1</guid><pubDate>{formatdate(FROZEN_TS - 20, usegmt=True)}</pubDate>"
            "<description>inflows</description></item></channel></rss>"
        )
        media = {harvester.BINANCE_MEDIA_FEEDS[0]["url"]: rss}
        with patch.object(harvester.urllib.request, "urlopen", self._binance_urlopen(_cms(catalogs), media=media)), \
             patch.object(harvester.time, "time", return_value=FROZEN_TS):
            payload = harvester.fetch_and_analyze_news_sentiment()
        ids = [row["id"] for row in payload["latest_news"]]
        self.assertIn("binance:48", ids)
        media_rows = [row for row in payload["latest_news"] if row.get("authority") == "media"]
        self.assertTrue(media_rows)
        self.assertEqual(media_rows[0]["category"], "CoinDesk")
        self.assertEqual(media_rows[0]["url"], "https://www.coindesk.com/eth-etf")
        self.assertEqual(media_rows[0]["source"], "binance")
        self.assertIn("ETH", media_rows[0]["coins"])
        self.assertEqual(payload["coins_sentiment"], {})

    def test_media_headline_does_not_arm_breaker(self):
        self.selected.return_value = ("binance",)
        now_ms = int(FROZEN_TS * 1000)
        catalogs = [{
            "catalogId": 48,
            "catalogName": "New Cryptocurrency Listing",
            "articles": [{
                "id": "ok",
                "code": "ok-list",
                "title": "BTC listed",
                "releaseDate": now_ms - 10_000,
            }],
        }]
        from email.utils import formatdate
        rss = (
            "<?xml version='1.0'?><rss version='2.0'><channel>"
            f"<item><title>{SWAN_TITLE}</title><link>https://www.coindesk.com/swan</link>"
            f"<guid>cd-swan</guid><pubDate>{formatdate(FROZEN_TS - 20, usegmt=True)}</pubDate>"
            "</item></channel></rss>"
        )
        media = {harvester.BINANCE_MEDIA_FEEDS[0]["url"]: rss}
        with patch.object(harvester.urllib.request, "urlopen", self._binance_urlopen(_cms(catalogs), media=media)), \
             patch.object(harvester.time, "time", return_value=FROZEN_TS):
            payload = harvester.fetch_and_analyze_news_sentiment()
        self.assertTrue(any(row["title"] == SWAN_TITLE for row in payload["latest_news"]))
        self.assertFalse(payload["circuit_breaker"].get("active"))
        self.notify.assert_not_called()
        self.assertFalse(self.cb.exists())



if __name__ == "__main__":
    raise SystemExit("isolation regressions are not executed in this assignment")

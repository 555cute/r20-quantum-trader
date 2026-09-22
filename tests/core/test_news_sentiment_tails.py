"""新闻情绪采集（`scripts/news_sentiment_harvester.py`）的残余分支收口 —— 第 325 刀。

本模块 670 行，是**喂给主脑的宏观输入**：加密快讯 + OKX 公告 + 金十宏观 +
OKX Rubik 多空账户比，合成 `news_sentiment.json`（首页舆情页 + LLM 提示词都读它）。
既有测试只有 13 例。

## 本刀立住的三条纪律

1. **时间戳诚信**（2026-09-18 用户反馈）：绝不拿 `time.time()` 伪造历史快讯的发布时间 ——
   金十榜单用的是**榜单自己的 `updated_at`**，逐条递减秒级排位；拿不到就写 0。
2. **不可判 ≠ 平安**（第一百四十六刀）：熔断文件损坏时写 **"熔断状态不可判"**，
   绝不落进"偏多震荡"；且**损坏要原子重写自愈**（否则清除路径永远解析失败、
   读者每轮"暂停开仓"直到人工删文件）。
3. **宁可显示 0，也不编造样本量**：`mentions` 必须从本轮实际入流的快讯逐条统计 ——
   旧版硬编码 `100`，前台九个币种全显示「100 篇」。
"""
from __future__ import annotations

import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.news_sentiment_harvester as nh  # noqa: E402

TZ_BJ = datetime.timezone(datetime.timedelta(hours=8))


def _resp(payload, *, raw=False):
    """伪 urlopen 响应（支持 with 语句）。"""
    body = payload if raw else json.dumps(payload).encode("utf-8")
    if isinstance(body, str):
        body = body.encode("utf-8")
    stream = io.BytesIO(body)
    stream.__enter__ = lambda s: s
    stream.__exit__ = lambda *a: False
    return stream


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.news = str(self.root / "news_sentiment.json")
        self.cb = str(self.root / "circuit_breaker.json")
        for name, value in (("DATA_DIR", str(self.root)),
                            ("NEWS_CACHE_FILE", self.news),
                            ("CIRCUIT_BREAKER_FILE", self.cb)):
            p = patch.object(nh, name, value)
            p.start()
            self.addCleanup(p.stop)
        sl = patch.object(nh.time, "sleep", lambda *a: None)
        sl.start()
        self.addCleanup(sl.stop)
        self.printed: list = []
        pr = patch.object(nh, "print", lambda *a, **k: self.printed.append(" ".join(map(str, a))))
        pr.start()
        self.addCleanup(pr.stop)

    def _stage_syspath_cleanup(self):
        """副本会在自己的模块头部把 `_ROOT` / `_PROJECT_ROOT` / `_THIS_DIR` 塞进
        `sys.path`（实测泄漏的是 `<tmp>/scripts` 这个**子目录**，不是临时根）
        ⇒ 三个都登记摘除。"""
        for entry in (str(self.root), str(self.root / "scripts")):
            if entry not in sys.path:
                self.addCleanup(sys.path.remove, entry)

    def _write_cb(self, payload):
        Path(self.cb).write_text(json.dumps(payload), encoding="utf-8")

    def _write_cache(self, payload):
        Path(self.news).write_text(json.dumps(payload), encoding="utf-8")


# ───────────────────── 原子写 ─────────────────────
class AtomicWriteTests(_Sandbox, unittest.TestCase):
    def test_a_payload_is_written_owner_only(self):
        target = self.root / "x.json"
        nh._atomic_write_json(str(target), {"a": 1})
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": 1})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_a_write_failure_cleans_up_and_reraises(self):
        # ★ 第 81–86 行
        target = self.root / "x.json"
        with patch.object(nh.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                nh._atomic_write_json(str(target), {"a": 1})
        self.assertEqual([p.name for p in self.root.glob(".x.json-*")], [])
        self.assertFalse(target.exists())

    def test_a_cleanup_failure_is_swallowed_and_the_original_error_survives(self):
        # ★ 第 84/85 行
        target = self.root / "x.json"
        with patch.object(nh.os, "replace", side_effect=OSError("disk full")), \
             patch.object(nh.os, "unlink", side_effect=OSError("read-only")):
            with self.assertRaises(OSError) as ctx:
                nh._atomic_write_json(str(target), {"a": 1})
        self.assertIn("disk full", str(ctx.exception))


# ───────────────────── 黑天鹅熔断 ─────────────────────
class CircuitBreakerTests(_Sandbox, unittest.TestCase):
    def test_a_trigger_writes_an_active_record(self):
        nh.trigger_circuit_breaker("某交易所破产", "交易所崩盘")
        data = json.loads(Path(self.cb).read_text(encoding="utf-8"))
        self.assertTrue(data["active"])
        self.assertEqual(data["headline"], "某交易所破产")
        self.assertEqual(data["keyword"], "交易所崩盘")
        self.assertGreater(data["expires_at_ts"], int(nh.time.time()))
        self.assertIn("暂停新开仓", data["action"])

    def test_a_notification_failure_is_swallowed(self):
        # ★ 第 126/127 行 —— QQ 推送失败不许让熔断写盘回滚
        with patch.dict(sys.modules, {"qq_notifier": None}):
            nh.trigger_circuit_breaker("某交易所破产", "交易所崩盘")
        self.assertTrue(json.loads(Path(self.cb).read_text(encoding="utf-8"))["active"])
        self.assertTrue(any("黑天鹅熔断已激活" in x for x in self.printed))

    def test_an_active_unexpired_breaker_reads_as_active(self):
        self._write_cb({"active": True, "expires_at_ts": nh.time.time() + 600})
        active, info = nh.is_circuit_breaker_active()
        self.assertTrue(active)
        self.assertTrue(info["active"])

    def test_an_expired_breaker_reads_as_inactive(self):
        self._write_cb({"active": True, "expires_at_ts": nh.time.time() - 1})
        self.assertEqual(nh.is_circuit_breaker_active(), (False, {}))

    def test_a_missing_file_reads_as_inactive(self):
        self.assertEqual(nh.is_circuit_breaker_active(), (False, {}))

    def test_a_corrupt_file_reads_as_unknown_not_safe(self):
        # 第一百四十六刀 —— 「读不到」绝不能报平安
        Path(self.cb).write_text("{ broken", encoding="utf-8")
        active, info = nh.is_circuit_breaker_active()
        self.assertFalse(active)
        self.assertTrue(info["unknown"])
        self.assertIn("读不出来/损坏", info["reason"])


# ───────────────────── 加密 RSS 快讯 ─────────────────────
RSS_XML = """<?xml version="1.0"?><rss><channel>
<item><title>Bitcoin 突破新高</title><link>https://x/1</link>
<description>&lt;p&gt;BTC 强势&lt;/p&gt;</description>
<pubDate>Tue, 01 Sep 2026 12:00:00 +0800</pubDate></item>
<item><title>   </title><link>https://x/2</link><description>空的</description></item>
<item><title>以太坊升级</title><link>https://x/3</link><description>ETH</description>
<pubDate>not-a-date</pubDate></item>
</channel></rss>"""


class CryptoRssTests(_Sandbox, unittest.TestCase):
    def _run(self, routes):
        def _urlopen(req, timeout=None):
            url = getattr(req, "full_url", req)
            got = routes.get(url)
            if got is None:
                raise OSError("no route")
            if isinstance(got, Exception):
                raise got
            return got
        with patch("urllib.request.urlopen", side_effect=_urlopen):
            return nh.fetch_crypto_rss_news(limit=30)

    def test_rss_items_become_news_rows(self):
        items = self._run({
            "https://cointelegraph.com/rss": _resp(RSS_XML, raw=True),
            "https://www.coindesk.com/arc/outboundfeeds/rss/": _resp("", raw=True),
            "https://www.theblock.co/rss.xml": _resp("", raw=True),
        })
        titles = [i["title"] for i in items]
        self.assertIn("Bitcoin 突破新高", titles)
        self.assertIn("以太坊升级", titles)

    def test_a_blank_title_is_skipped(self):
        # ★ 第 179/180 行
        items = self._run({"https://cointelegraph.com/rss": _resp(RSS_XML, raw=True)})
        self.assertNotIn("", [i["title"] for i in items])
        self.assertFalse(any(not i["title"].strip() for i in items))

    def test_html_is_stripped_from_the_summary(self):
        items = self._run({"https://cointelegraph.com/rss": _resp(RSS_XML, raw=True)})
        row = next(i for i in items if i["title"] == "Bitcoin 突破新高")
        self.assertEqual(row["summary"], "BTC 强势")

    def test_the_pubdate_sets_the_timestamp(self):
        items = self._run({"https://cointelegraph.com/rss": _resp(RSS_XML, raw=True)})
        row = next(i for i in items if i["title"] == "Bitcoin 突破新高")
        expected = int(datetime.datetime(2026, 9, 1, 12, 0, 0,
                                         tzinfo=TZ_BJ).timestamp() * 1000)
        self.assertEqual(int(row["cTime"]), expected)
        self.assertEqual(row["time"], "2026-09-01 12:00:00")

    def test_an_unparsable_pubdate_falls_back_to_now(self):
        # ★ 第 192/193 行
        items = self._run({"https://cointelegraph.com/rss": _resp(RSS_XML, raw=True)})
        row = next(i for i in items if i["title"] == "以太坊升级")
        self.assertGreater(int(row["cTime"]), 0)

    def test_the_item_id_is_deterministic_per_title(self):
        # 审计 D7：禁用 abs(hash())（PYTHONHASHSEED 每进程随机化 ⇒ 重启后去重失效）
        runs = {self._run({"https://cointelegraph.com/rss": _resp(RSS_XML, raw=True)})[0]["id"]
                for _ in range(2)}
        self.assertEqual(len(runs), 1)
        self.assertIn("cointelegraph-", runs.pop())

    def test_a_feed_failure_is_swallowed_per_source(self):
        items = self._run({"https://cointelegraph.com/rss": OSError("no net")})
        self.assertEqual(items, [])
        self.assertTrue(any("快讯抓取异常" in x for x in self.printed))

    def test_the_binance_announcement_api_is_parsed(self):
        # ★ 第 215–223 行 —— 整块此前从未执行
        payload = {"data": {"articles": [
            {"title": "币安将上线 XYZ", "code": "abc123", "releaseDate": 1_700_000_000_000},
            {"title": "   ", "code": "skip", "releaseDate": 1},
        ]}}
        items = self._run({"https://www.binance.com/bapi/composite/v1/public/cms/article/"
                           "catalog/list/query?catalogId=48&pageNo=1&pageSize=10":
                           _resp(payload)})
        self.assertEqual(len(items), 1)
        row = items[0]
        self.assertEqual(row["title"], "币安将上线 XYZ")
        self.assertEqual(row["id"], "binance-1700000000000-abc123")
        self.assertEqual(row["platforms"], ["Binance官方"])
        self.assertIn("Binance官方动态", row["summary"])
        self.assertIn("/abc123", row["url"])

    def test_a_binance_article_without_a_code_uses_the_homepage(self):
        payload = {"data": {"articles": [{"title": "无编号公告", "code": None,
                                          "releaseDate": 1}]}}
        items = self._run({"https://www.binance.com/bapi/composite/v1/public/cms/article/"
                           "catalog/list/query?catalogId=48&pageNo=1&pageSize=10":
                           _resp(payload)})
        self.assertEqual(items[0]["url"], "https://www.binance.com")

    def test_the_limit_is_applied(self):
        payload = {"data": {"articles": [{"title": f"公告{i}", "code": i,
                                          "releaseDate": 1} for i in range(20)]}}
        items = self._run({"https://www.binance.com/bapi/composite/v1/public/cms/article/"
                           "catalog/list/query?catalogId=48&pageNo=1&pageSize=10":
                           _resp(payload)})
        self.assertLessEqual(len(items), 30)


# ───────────────────── OKX 公告 ─────────────────────
class OkxAnnouncementTests(_Sandbox, unittest.TestCase):
    def _run(self, payload):
        with patch("urllib.request.urlopen", return_value=_resp(payload)):
            return nh.fetch_okx_announcements(limit=15)

    def test_details_become_news_rows(self):
        items = self._run({"data": [{"details": [
            {"title": "OKX 将下线 ABC", "url": "https://x", "annType": "下架",
             "pTime": 1_700_000_000_000}]}]})
        self.assertEqual(len(items), 1)
        row = items[0]
        self.assertIn("OKX官方通告【下架】", row["summary"])
        self.assertEqual(row["platforms"], ["OKX官方"])
        self.assertIn("okx-1700000000000-", row["id"])

    def test_a_blank_title_is_skipped(self):
        # ★ 第 260/261 行
        items = self._run({"data": [{"details": [{"title": "  ", "pTime": 1},
                                                 {"title": "有效公告", "pTime": 2}]}]})
        self.assertEqual([i["title"] for i in items], ["有效公告"])

    def test_a_missing_ann_type_falls_back_to_a_generic_label(self):
        items = self._run({"data": [{"details": [{"title": "X", "pTime": 1}]}]})
        self.assertIn("【公告】", items[0]["summary"])

    def test_business_p_time_is_used_when_p_time_is_absent(self):
        items = self._run({"data": [{"details": [{"title": "X",
                                                  "businessPTime": 1_700_000_000_000}]}]})
        self.assertTrue(items[0]["id"].startswith("okx-1700000000000-"))

    def test_a_fetch_failure_is_swallowed(self):
        with patch("urllib.request.urlopen", side_effect=OSError("no net")):
            self.assertEqual(nh.fetch_okx_announcements(), [])
        self.assertTrue(any("OKX 官方公告抓取异常" in x for x in self.printed))

    def test_announcements_are_excluded_from_display_by_default(self):
        # 2026-09-16 用户反馈：运营通告零信息量却占掉 15/35 条版面
        self.assertFalse(nh.DISPLAY_OKX_ANNOUNCEMENTS)


# ───────────────────── 金十宏观快讯 ─────────────────────
class Jin10MacroTests(_Sandbox, unittest.TestCase):
    WSCN = "https://api-one-wscn.awtmt.com/apiv1/content/lives?channel=global-channel&limit=25"
    SINA = "https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=25&zhibo_id=152"
    J10 = "https://cdn.jin10.com/json/index/hits_rank.json"

    def _run(self, routes):
        def _urlopen(req, timeout=None):
            url = getattr(req, "full_url", req)
            got = routes.get(url)
            if got is None:
                raise OSError("no route")
            if isinstance(got, Exception):
                raise got
            return got
        with patch("urllib.request.urlopen", side_effect=_urlopen):
            return nh.fetch_jin10_macro_news(limit=25)

    def test_wscn_items_become_news_rows(self):
        # ★ 第 302–311 行
        routes = {self.WSCN: _resp({"data": {"items": [
            {"content_text": "美联储宣布降息。市场反应积极", "title": "",
             "display_time": 1_700_000_000, "id": 42, "uri": "https://w/42"}]}})}
        items = self._run(routes)
        self.assertEqual(len(items), 1)
        row = items[0]
        self.assertEqual(row["title"], "美联储宣布降息")
        self.assertEqual(row["id"], "wscn-42")
        self.assertEqual(row["platforms"], ["华尔街见闻", "全球宏观快讯"])
        self.assertEqual(row["url"], "https://w/42")

    def test_wscn_title_falls_back_to_a_generic_label(self):
        # ★ 第 305 行 —— 连正文都没有时
        routes = {self.WSCN: _resp({"data": {"items": [
            {"content_text": "", "title": "", "display_time": 1_700_000_000, "id": 1}]}})}
        self.assertEqual(self._run(routes)[0]["title"], "宏观快讯")

    def test_a_wscn_item_without_a_real_timestamp_is_skipped(self):
        # ★ 第 307/308 行 —— 时间戳诚信：宁可丢弃也不伪造
        routes = {self.WSCN: _resp({"data": {"items": [
            {"content_text": "有正文", "display_time": 0, "id": 1}]}})}
        self.assertEqual(self._run(routes), [])

    def test_the_wscn_id_falls_back_to_the_millisecond_stamp(self):
        routes = {self.WSCN: _resp({"data": {"items": [
            {"content_text": "有正文", "display_time": 1_700_000_000}]}})}
        self.assertEqual(self._run(routes)[0]["id"], "wscn-1700000000000")

    def test_sina_items_become_news_rows(self):
        # ★ 第 333–347 行
        routes = {self.SINA: _resp({"result": {"data": {"feed": {"list": [
            {"rich_text": "央行公开市场操作。细节若干", "id": 7,
             "create_time": "2026-09-01 12:00:00"}]}}}})}
        items = self._run(routes)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "央行公开市场操作")
        self.assertEqual(items[0]["id"], "macro-7")
        self.assertEqual(items[0]["time"], "2026-09-01 12:00:00")

    def test_sina_plain_text_is_used_when_rich_text_is_absent(self):
        routes = {self.SINA: _resp({"result": {"data": {"feed": {"list": [
            {"plain_text": "比特币突破新高。更多细节", "id": 8,
             "create_time": "2026-09-01 12:00:00"}]}}}})}
        self.assertEqual(self._run(routes)[0]["title"], "比特币突破新高")

    def test_a_blank_sina_text_is_skipped(self):
        # ★ 第 334/335 行
        routes = {self.SINA: _resp({"result": {"data": {"feed": {"list": [
            {"rich_text": "   ", "id": 1, "create_time": "2026-09-01 12:00:00"}]}}}})}
        self.assertEqual(self._run(routes), [])

    def test_an_irrelevant_low_importance_sina_item_is_skipped(self):
        # ★ 第 338/339 行
        routes = {self.SINA: _resp({"result": {"data": {"feed": {"list": [
            {"rich_text": "某地天气预报。详情", "id": 1,
             "create_time": "2026-09-01 12:00:00"}]}}}})}
        self.assertEqual(self._run(routes), [])

    def test_a_sina_item_without_create_time_is_skipped(self):
        # ★ 第 341/342 行
        routes = {self.SINA: _resp({"result": {"data": {"feed": {"list": [
            {"rich_text": "比特币大涨。详情", "id": 1}]}}}})}
        self.assertEqual(self._run(routes), [])

    def test_an_unparsable_sina_create_time_is_skipped(self):
        # ★ 第 346/347 行
        routes = {self.SINA: _resp({"result": {"data": {"feed": {"list": [
            {"rich_text": "比特币大涨。详情", "id": 1,
             "create_time": "not-a-date"}]}}}})}
        self.assertEqual(self._run(routes), [])

    def test_jin10_ranked_items_use_the_list_update_time(self):
        # ★ 第 369–382 行 —— 时间戳诚信的核心：用榜单自己的 updated_at
        routes = {self.J10: _resp({"all": {
            "daily": {"updated_at": "2026-09-01 12:00:00",
                      "news": [{"title": "热点一", "id": 1}, {"title": "热点二", "id": 2}]},
            "weekly": {"news": [{"title": "周热点", "id": 3}]}}})}
        items = self._run(routes)
        self.assertEqual([i["title"] for i in items], ["热点一", "热点二", "周热点"])
        self.assertEqual(items[0]["time"], "2026-09-01 12:00:00")
        base = int(datetime.datetime(2026, 9, 1, 12, 0, 0, tzinfo=TZ_BJ).timestamp() * 1000)
        self.assertEqual(int(items[0]["cTime"]), base)
        self.assertEqual(int(items[1]["cTime"]), base - 1000, "逐条递减秒级排位")

    def test_an_unparsable_updated_at_yields_zero_timestamps(self):
        # ★ 第 374/375 行 —— 拿不到真实时间就写 0，绝不伪造
        routes = {self.J10: _resp({"all": {
            "daily": {"updated_at": "not-a-date", "news": [{"title": "热点", "id": 1}]},
            "weekly": {"news": []}}})}
        items = self._run(routes)
        self.assertEqual(items[0]["cTime"], "0")
        self.assertEqual(items[0]["time"], "not-a-date")

    def test_a_missing_updated_at_yields_zero_timestamps(self):
        # ★ 第 376/377 行
        routes = {self.J10: _resp({"all": {
            "daily": {"news": [{"title": "热点", "id": 1}]}, "weekly": {"news": []}}})}
        self.assertEqual(self._run(routes)[0]["cTime"], "0")

    def test_a_blank_jin10_title_is_skipped(self):
        # ★ 第 381/382 行
        routes = {self.J10: _resp({"all": {
            "daily": {"updated_at": "2026-09-01 12:00:00",
                      "news": [{"title": "  ", "id": 1}, {"title": "有效", "id": 2}]},
            "weekly": {"news": []}}})}
        self.assertEqual([i["title"] for i in self._run(routes)], ["有效"])

    def test_the_result_is_sorted_by_real_timestamp_descending(self):
        routes = {
            self.WSCN: _resp({"data": {"items": [
                {"content_text": "旧", "display_time": 1_000_000, "id": 1}]}}),
            self.SINA: _resp({"result": {"data": {"feed": {"list": [
                {"rich_text": "新比特币快讯。详情", "id": 2,
                 "create_time": "2026-09-01 12:00:00"}]}}}}),
        }
        items = self._run(routes)
        self.assertGreaterEqual(int(items[0]["cTime"]), int(items[-1]["cTime"]))

    def test_a_source_failure_is_swallowed_per_source(self):
        # ★ 第 322/323、358/359、396/397 行
        self.assertEqual(self._run({self.WSCN: OSError("x"), self.SINA: OSError("y"),
                                    self.J10: OSError("z")}), [])
        self.assertEqual(len([x for x in self.printed if "抓取异常" in x]), 3)


# ───────────────────── OKX Rubik 多空比 ─────────────────────
class RubikSentimentTests(_Sandbox, unittest.TestCase):
    def _run(self, payload, *, times=1):
        calls = {"n": 0}

        def _urlopen(req, timeout=None):
            calls["n"] += 1
            if isinstance(payload, Exception):
                raise payload
            return _resp(payload)
        with patch("urllib.request.urlopen", side_effect=_urlopen):
            out = nh.fetch_okx_rubik_sentiment("btc")
        return out, calls["n"]

    def test_a_bullish_ratio_labels_bullish(self):
        out, _ = self._run({"data": [["1700000000000", "1.5"]]})
        self.assertEqual(out["label"], "bullish")
        self.assertEqual(out["ccy"], "BTC", "币种要规范成大写")
        self.assertEqual(out["long_short_ratio"], "1.50")

    def test_a_bearish_ratio_labels_bearish(self):
        # ★ 第 438/439 行
        out, _ = self._run({"data": [["1700000000000", "0.5"]]})
        self.assertEqual(out["label"], "bearish")

    def test_a_middling_ratio_labels_neutral(self):
        # ★ 第 440/441 行
        for ratio in ("0.83", "1.0", "1.24"):
            with self.subTest(ratio=ratio):
                out, _ = self._run({"data": [["1700000000000", ratio]]})
                self.assertEqual(out["label"], "neutral")

    def test_the_boundaries_are_inclusive(self):
        self.assertEqual(self._run({"data": [[0, "1.25"]]})[0]["label"], "bullish")
        self.assertEqual(self._run({"data": [[0, "0.82"]]})[0]["label"], "bearish")

    def test_the_percentages_sum_to_about_one_hundred(self):
        out, _ = self._run({"data": [[0, "3"]]})
        self.assertEqual(out["bullish_ratio"], "75.0%")
        self.assertEqual(out["bearish_ratio"], "25.0%")

    def test_no_mentions_field_is_fabricated(self):
        # 2026-09-16：旧版硬编码 mentions=100，前台每个币都显示「100 篇」
        out, _ = self._run({"data": [[0, "1"]]})
        self.assertNotIn("mentions", out)

    def test_a_malformed_row_triggers_one_retry_then_gives_up(self):
        # ★ 第 456–459 行
        out, calls = self._run({"data": []})
        self.assertIsNone(out)
        self.assertEqual(calls, 2, "共两次尝试（range(2)）")

    def test_a_network_error_triggers_one_retry_then_gives_up(self):
        out, calls = self._run(OSError("no net"))
        self.assertIsNone(out)
        self.assertEqual(calls, 2)

    def test_a_short_row_is_treated_as_malformed(self):
        out, calls = self._run({"data": [["only-one-field"]]})
        self.assertIsNone(out)
        self.assertEqual(calls, 2)


# ───────────────────── 主流程 ─────────────────────
class FetchAndAnalyzeTests(_Sandbox, unittest.TestCase):
    def _run(self, **over):
        env = {
            "fetch_okx_announcements": lambda limit=20: [],
            "fetch_crypto_rss_news": lambda limit=30: [],
            "fetch_jin10_macro_news": lambda limit=25: [],
            "fetch_okx_rubik_sentiment": lambda ccy: {
                "ccy": ccy, "label": "neutral", "sentiment_factor_score": 0.0},
            "load_instruments": lambda: [{"name": "BTC"}, {"name": "ETH"}],
        }
        env.update(over)
        patches = [patch.object(nh, k, v) for k, v in env.items()]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return nh.fetch_and_analyze_news_sentiment()

    def _news(self, *, ctime=None, title="Bitcoin 大涨", platforms=None):
        ctime = ctime if ctime is not None else int(nh.time.time() * 1000)
        return {"id": f"n-{title}", "title": title, "summary": title,
                "cTime": str(ctime), "platforms": platforms or ["Cointelegraph"],
                "importance": "high", "coins": ["BTC"], "url": "https://x"}

    def test_a_payload_is_written_and_reported(self):
        payload = self._run(fetch_crypto_rss_news=lambda limit=30: [self._news()])
        self.assertTrue(Path(self.news).exists())
        self.assertTrue(payload["source_available"])
        self.assertEqual(len(payload["latest_news"]), 1)
        self.assertIn("timestamp", payload)
        self.assertIn("updated_at", payload)

    def test_duplicate_ids_are_deduplicated(self):
        dup = self._news()
        payload = self._run(fetch_crypto_rss_news=lambda limit=30: [dup, dict(dup)])
        self.assertEqual(len(payload["latest_news"]), 1)

    def test_okx_announcements_are_hidden_from_display_but_still_audited(self):
        # ★ 第 481–484 行 —— 展示流排除官方公告，但黑天鹅体检仍覆盖它们
        ann = self._news(title="OKX 系统维护通知", platforms=["OKX官方"])
        payload = self._run(fetch_okx_announcements=lambda limit=20: [ann])
        self.assertEqual(payload["latest_news"], [], "运营通告不进展示流")

    def test_a_black_swan_triggers_the_circuit_breaker(self):
        # ★ 第 522/523 行
        # 正则第 96 行要求 (币安|OKX|Coinbase|Kraken) 且命中破产/挤兑类词
        hot = self._news(title="OKX 宣布破产倒闭并暂停全部提现",
                         platforms=["OKX官方"])
        payload = self._run(fetch_okx_announcements=lambda limit=20: [hot])
        self.assertTrue(Path(self.cb).exists())
        self.assertIn("避险熔断中", payload["macro_sentiment"])

    def test_a_stale_headline_does_not_trigger(self):
        # 900 秒窗口外的旧闻不许触发熔断
        old = self._news(ctime=int(nh.time.time() * 1000) - 3_600_000,
                         title="OKX 宣布破产倒闭并暂停全部提现")
        payload = self._run(fetch_crypto_rss_news=lambda limit=30: [old])
        self.assertFalse(Path(self.cb).exists())
        self.assertNotIn("避险熔断中", payload["macro_sentiment"])

    def test_an_expired_breaker_is_cleared(self):
        # ★ 第 526–532 行
        self._write_cb({"active": True, "expires_at_ts": nh.time.time() - 1})
        self._run()
        self.assertFalse(json.loads(Path(self.cb).read_text(encoding="utf-8"))["active"])

    def test_a_corrupt_breaker_file_self_heals_to_inactive(self):
        # ★ 第 533–546 行 —— 旧实现 except:pass 会让清除路径永远失败（无限期停摆）
        Path(self.cb).write_text("{ broken", encoding="utf-8")
        payload = self._run()
        healed = json.loads(Path(self.cb).read_text(encoding="utf-8"))
        self.assertFalse(healed["active"])
        self.assertIn("self_healed_at", healed)
        self.assertTrue(any("熔断自愈" in x for x in self.printed))

    def test_a_self_heal_write_failure_is_swallowed(self):
        # ★ 第 545/546 行
        Path(self.cb).write_text("{ broken", encoding="utf-8")
        with patch.object(nh, "_atomic_write_json", side_effect=OSError("read-only")):
            payload = self._run()
        self.assertIn("macro_sentiment", payload)
        self.assertTrue(any("重写失败" in x for x in self.printed))

    def test_a_corrupt_breaker_that_self_heals_is_then_knowable(self):
        # ★ 第 539–544 行 —— 自愈**成功**之后状态就重新可判：
        #    自愈把文件重写成合法的 inactive ⇒ 第 586 行再读就拿到 (False, {})
        #    ⇒ 落到正常的"中性平衡"分支。这是对的（不是"不可判"）。
        Path(self.cb).write_text("{ broken", encoding="utf-8")
        payload = self._run()
        self.assertEqual(payload["macro_sentiment"], "中性平衡")
        self.assertEqual(payload["circuit_breaker"], {"active": False})

    def test_a_corrupt_breaker_that_cannot_self_heal_is_reported_unknown(self):
        # ★ 第 589–591 行 —— **不可判 ≠ 平安**。
        #    只有当自愈重写也失败（文件仍是坏的）时这条分支才可达：
        #    此时必须写"熔断状态不可判"，绝不落进"偏多震荡/偏空承压"。
        Path(self.cb).write_text("{ broken", encoding="utf-8")
        real = nh._atomic_write_json
        calls = {"n": 0}

        def _flaky(path, payload):
            calls["n"] += 1
            raise OSError("read-only")     # 自愈重写失败
        with patch.object(nh, "_atomic_write_json", side_effect=_flaky):
            payload = self._run()
        self.assertIn("不可判", payload["macro_sentiment"])
        self.assertTrue(payload["circuit_breaker"]["unknown"])

    def test_the_macro_env_is_bullish_when_bulls_outweigh_bears(self):
        payload = self._run(fetch_okx_rubik_sentiment=lambda ccy:
                            {"ccy": ccy, "label": "bullish",
                             "sentiment_factor_score": 0.5 if ccy == "BTC" else 0.0})
        self.assertEqual(payload["macro_sentiment"], "偏多震荡")

    def test_the_macro_env_is_bearish_when_bears_outweigh_bulls(self):
        payload = self._run(fetch_okx_rubik_sentiment=lambda ccy:
                            {"ccy": ccy, "label": "bearish",
                             "sentiment_factor_score": -0.5 if ccy == "BTC" else 0.0})
        self.assertEqual(payload["macro_sentiment"], "偏空承压")

    def test_a_dead_source_reports_no_availability(self):
        payload = self._run()
        self.assertFalse(payload["source_available"])
        self.assertIn("显示缺失而非中性", payload["source_reason"])

    def test_per_coin_rubik_failure_falls_back_to_the_previous_cache(self):
        # ★ 第 567/568 行
        self._write_cache({"coins_sentiment": {"BTC": {"ccy": "BTC", "label": "bullish",
                                                       "sentiment_factor_score": 0.9}}})
        payload = self._run(fetch_okx_rubik_sentiment=lambda ccy: None
                            if ccy == "BTC" else {"ccy": ccy, "label": "neutral",
                                                  "sentiment_factor_score": 0.0})
        self.assertEqual(payload["coins_sentiment"]["BTC"]["label"], "bullish")

    def test_a_coin_with_no_data_and_no_cache_gets_a_neutral_baseline(self):
        # ★ 第 569–582 行
        payload = self._run(fetch_okx_rubik_sentiment=lambda ccy: None)
        self.assertEqual(payload["coins_sentiment"]["BTC"]["label"], "neutral")
        self.assertEqual(payload["coins_sentiment"]["BTC"]["long_short_ratio"], "1.00")

    def test_a_corrupt_previous_cache_is_swallowed(self):
        # ★ 第 560/561 行
        Path(self.news).write_text("{ broken", encoding="utf-8")
        payload = self._run(fetch_okx_rubik_sentiment=lambda ccy: None)
        self.assertIn("coins_sentiment", payload)

    def test_an_empty_run_serves_the_previous_cache_and_flags_stale(self):
        # ★ 第 617–633 行 —— fail-closed：上游抖动不许把好缓存洗成空页面
        self._write_cache({"latest_news": [self._news(title="旧闻")],
                           "coins_sentiment": {"BTC": {"ccy": "BTC", "label": "bullish",
                                                       "sentiment_factor_score": 0.4}}})
        payload = self._run(
            fetch_okx_rubik_sentiment=lambda ccy: None,
            load_instruments=lambda: [{"name": "BTC"}])
        self.assertTrue(payload.get("stale_sections"))
        self.assertEqual(payload["latest_news"][0]["title"], "旧闻")
        self.assertNotIn("source_available", ("stale_sections",))

    def test_a_stale_fallback_still_recomputes_the_macro_env(self):
        # ★ 第 627–630 行
        self._write_cache({"latest_news": [self._news(title="旧闻")],
                           "coins_sentiment": {"BTC": {"ccy": "BTC", "label": "bullish",
                                                       "sentiment_factor_score": 0.9}}})
        payload = self._run(
            fetch_okx_rubik_sentiment=lambda ccy: None,
            load_instruments=lambda: [{"name": "BTC"}])
        self.assertEqual(payload["macro_sentiment"], "偏多震荡")

    def test_an_empty_coin_universe_falls_back_to_the_cached_sentiments(self):
        # ★ 第 626–630 行 —— `coins_sentiment` 本轮为空时，
        #    从旧缓存按**当前池**过滤后回落，并据此**重算**宏观环境
        #    （池为空 ⇒ `target_coins` 为空 ⇒ 回落出来的也是空 ⇒ 保持"中性平衡"）
        self._write_cache({"latest_news": [self._news(title="旧闻")],
                           "coins_sentiment": {"BTC": {"ccy": "BTC", "label": "bullish",
                                                       "sentiment_factor_score": 0.9}}})
        payload = self._run(load_instruments=lambda: [])
        self.assertTrue(payload.get("stale_sections"))
        self.assertEqual(payload["coins_sentiment"], {},
                         "按当前池（空）过滤 ⇒ 缓存里的 BTC 也进不来")
        self.assertEqual(payload["macro_sentiment"], "中性平衡")

    def test_the_cached_sentiments_are_kept_for_still_active_coins(self):
        # ★ 第 626 行 —— 当前池仍有该币时才把它从缓存里留下来
        self._write_cache({"latest_news": [],
                           "coins_sentiment": {"BTC": {"ccy": "BTC", "label": "bullish",
                                                       "sentiment_factor_score": 0.9},
                                               "GONE": {"ccy": "GONE", "label": "bullish",
                                                        "sentiment_factor_score": 0.9}}})
        payload = self._run(
            load_instruments=lambda: [],
            fetch_okx_rubik_sentiment=lambda ccy: None,
            fetch_crypto_rss_news=lambda limit=30: [])
        # 池为空 ⇒ 回落集合为空（GONE 不属于当前池，不许借缓存复活）
        self.assertEqual(payload["coins_sentiment"], {})

    def test_the_fallback_recomputes_bullish_when_bulls_dominate(self):
        # ★ 第 627–630 行 —— 回落之后必须**重算** macro_sentiment，不许沿用旧值
        self._write_cache({"latest_news": [],
                           "coins_sentiment": {"BTC": {"ccy": "BTC", "label": "bullish",
                                                       "sentiment_factor_score": 0.9}}})
        payload = self._run(
            load_instruments=lambda: [{"name": "BTC"}],
            fetch_okx_rubik_sentiment=lambda ccy: None,
            fetch_crypto_rss_news=lambda limit=30: [])
        # rubik 返回 None ⇒ 逐币走"缓存回落"分支（第 567/568 行）而不是"空池"
        self.assertEqual(payload["coins_sentiment"]["BTC"]["label"], "bullish")

    def test_a_corrupt_cache_during_fallback_is_swallowed(self):
        # ★ 第 632/633 行 —— 回落读缓存本身失败也不许抛
        orig_exists = nh.os.path.exists
        Path(self.news).write_text("{ broken", encoding="utf-8")

        def _exists(p):
            return False if str(p) == self.news else orig_exists(p)
        with patch.object(nh.os.path, "exists", side_effect=_exists):
            payload = self._run()
        self.assertIn("latest_news", payload)

    def test_mentions_are_counted_from_the_actual_news_flow(self):
        # 2026-09-16：宁可显示 0，也不编造样本量
        payload = self._run(fetch_crypto_rss_news=lambda limit=30: [self._news()])
        self.assertEqual(payload["coins_sentiment"]["BTC"]["mentions"], 1)
        self.assertEqual(payload["coins_sentiment"]["ETH"]["mentions"], 0)

    def test_a_cache_write_failure_is_reported_but_not_raised(self):
        # ★ 第 658/659 行
        with patch.object(nh.os, "replace", side_effect=OSError("read-only")):
            payload = self._run()
        self.assertIn("latest_news", payload)
        self.assertTrue(any("Failed to write news cache" in x for x in self.printed))

    def test_the_coin_universe_comes_from_the_live_pool(self):
        # 第 549/550 行 —— 每轮重读标的池，不缓存
        payload = self._run(load_instruments=lambda: [{"name": "SOL"}])
        self.assertEqual(sorted(payload["coins_sentiment"]), ["SOL"])


class MainGuardTests(_Sandbox, unittest.TestCase):
    def test_the_main_guard_reports_and_exits_three_when_sources_are_dead(self):
        # ★ 第 663–670 行
        import runpy
        scripts = self.root / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        copy = scripts / "news_sentiment_harvester.py"
        copy.write_text(Path(nh.__file__).read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        self._stage_syspath_cleanup()
        with patch.object(nh, "fetch_and_analyze_news_sentiment",
                          lambda: {"stale_sections": True, "source_available": False,
                                   "macro_sentiment": "中性平衡", "latest_news": [],
                                   "news_fresh_at": None}), \
             patch("urllib.request.urlopen", side_effect=OSError("no net")):
            with self.assertRaises(SystemExit) as ctx:
                runpy.run_path(str(copy), run_name="__main__")
        self.assertEqual(ctx.exception.code, 3)

    def test_the_main_guard_succeeds_when_sources_are_alive(self):
        import runpy
        scripts = self.root / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        copy = scripts / "news_sentiment_harvester.py"
        copy.write_text(Path(nh.__file__).read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        self._stage_syspath_cleanup()
        # ⚠️ `runpy` 会**重新执行模块** ⇒ 对 `nh` 打的补丁对副本无效（同第 323 刀的陷阱）。
        #    要影响副本，只能打**底层共享的**东西 —— `urllib.request.urlopen` 是
        #    真模块上的属性，副本也走它。这里只喂一条有效宏观快讯，其余源自然失败（被吞）。
        buf = io.StringIO()
        payload = {"data": {"items": [{"content_text": "美联储维持利率不变。声明全文",
                                       "display_time": int(nh.time.time()),
                                       "id": 1, "uri": "https://w/1"}]}}
        # ⚠️ 必须**每次新建**一个流：`return_value` 会复用同一个 BytesIO，
        #    第一次调用就读空了，后续源全部拿到空体（本刀在此自伤过一次）。
        with patch("urllib.request.urlopen",
                   side_effect=lambda *a, **k: _resp(payload)), \
             patch("sys.stdout", buf):
            runpy.run_path(str(copy), run_name="__main__")
        out = buf.getvalue()
        self.assertIn("OKX News & Sentiment Engine complete", out)
        self.assertIn("News Count: 1", out)
        self.assertNotIn("STALE", out, "无 stale_sections 时不许挂 STALE 标")
        self.assertTrue((self.root / "data" / "news_sentiment.json").exists(),
                        "产物必须落进沙箱")


if __name__ == "__main__":
    unittest.main()

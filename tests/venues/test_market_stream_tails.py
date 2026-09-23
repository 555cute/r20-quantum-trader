"""公共行情流（`scripts/market_stream.py`）的残余分支收口 —— 第 330 刀。

本模块 481 行，只读探测 OKX / Gate / Binance 三所公共行情流（**不常驻、不下单**），
归一成 tick 与"流健康账本"。既有 `tests/venues/test_market_stream.py` 覆盖了主干，
本刀补 18 行缺口。

## 本刀立住的三条纪律

1. **`parse_frame` 永不抛异常** —— 解析失败一律记成 `error` 字段。
   第 137 刀的教训是：不可接受的是"没人知道"，而不是"函数不抛"。
2. **"已连接但没数据"是最要防的形态**（Binance 路径式订阅对不存在的符号**不报错，
   就是静默**）⇒ `last_tick_ms` 与 `last_msg_ms` **分开记**；从未收到过 tick 时
   `staleness_s` 返回 **None 而不是 0**（不可判定 ≠ 安全）。
3. **订阅应答帧不是 tick** —— Gate 的 `event=subscribe` 应答必须**直接返回**，
   否则每连一次多一条假解析错误（本机实跑才发现）。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.market_stream as ms  # noqa: E402


class _FakeWs:
    """假传输：按脚本回放帧，脚本用尽后抛（模拟"本轮该所安静"）。"""

    def __init__(self, frames):
        self._frames = list(frames)
        self.sent: list = []

    def send(self, text):
        self.sent.append(text)

    def recv(self, timeout=None):
        if not self._frames:
            raise TimeoutError("no more frames")
        return self._frames.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _connect(frames):
    holder: dict = {}

    def _factory(url, **kw):
        holder["url"] = url
        holder["kw"] = kw
        return _FakeWs(frames)
    return _factory, holder


# ───────────────────── 数值与场所名 ─────────────────────
class NumTests(unittest.TestCase):
    def test_a_numeric_value_is_returned(self):
        self.assertEqual(ms._num("1.5"), 1.5)
        self.assertEqual(ms._num(2), 2.0)

    def test_a_non_numeric_value_is_none(self):
        for bad in ("abc", None, {}, []):
            with self.subTest(bad=bad):
                self.assertIsNone(ms._num(bad))

    def test_nan_and_infinity_are_none(self):
        for bad in (float("nan"), float("inf"), float("-inf"), "nan", "inf"):
            with self.subTest(bad=bad):
                self.assertIsNone(ms._num(bad))


class VenueSymbolTests(unittest.TestCase):
    def test_each_venue_gets_its_native_contract_name(self):
        for venue, expected in (("okx", "BTC-USDT-SWAP"), ("gate", "BTC_USDT"),
                                ("binance", "BTCUSDT")):
            with self.subTest(venue=venue):
                self.assertEqual(ms.venue_symbol(venue, "BTC-USDT-SWAP"), expected)

    def test_slash_and_underscore_forms_are_normalised(self):
        for raw in ("BTC/USDT", "BTC_USDT", "btc-usdt-swap", "BTC-USDT"):
            with self.subTest(raw=raw):
                self.assertEqual(ms.venue_symbol("okx", raw), "BTC-USDT-SWAP")

    def test_the_perp_suffix_is_stripped(self):
        self.assertEqual(ms.venue_symbol("binance", "BTC-USDT-PERP"), "BTCUSDT")

    def test_a_bare_base_defaults_the_quote_to_usdt(self):
        self.assertEqual(ms.venue_symbol("okx", "BTC"), "BTC-USDT-SWAP")

    def test_an_empty_symbol_defaults_to_btc(self):
        self.assertEqual(ms.venue_symbol("okx", ""), "BTC-USDT-SWAP")

    def test_an_unknown_venue_raises(self):
        # ★ 第 108 行
        with self.assertRaises(KeyError) as ctx:
            ms.venue_symbol("kraken", "BTC-USDT-SWAP")
        self.assertIn("未知场所", str(ctx.exception))

    def test_the_second_part_becomes_the_quote(self):
        self.assertEqual(ms.venue_symbol("gate", "ETH-BTC"), "ETH_BTC")


class SubscribePayloadTests(unittest.TestCase):
    def test_binance_is_path_subscribed_and_needs_no_frame(self):
        # `_PATH_SUBSCRIBE` 的场所返回 None（不发订阅帧）
        self.assertIsNone(ms.subscribe_payload("binance", "BTCUSDT"))

    def test_okx_sends_a_tickers_subscribe(self):
        payload = ms.subscribe_payload("okx", "BTC-USDT-SWAP")
        self.assertEqual(payload["op"], "subscribe")
        self.assertEqual(payload["args"][0]["instId"], "BTC-USDT-SWAP")

    def test_gate_sends_a_futures_tickers_subscribe(self):
        payload = ms.subscribe_payload("gate", "BTC_USDT")
        self.assertEqual(payload["event"], "subscribe")
        self.assertEqual(payload["channel"], "futures.tickers")
        self.assertEqual(payload["payload"], ["BTC_USDT"])

    def test_an_unknown_venue_raises(self):
        # ★ 第 132 行
        with self.assertRaises(KeyError) as ctx:
            ms.subscribe_payload("kraken", "X")
        self.assertIn("未知场所", str(ctx.exception))


class StreamUrlTests(unittest.TestCase):
    def test_binance_uses_a_path_style_subscription(self):
        url = ms.stream_url("binance", "BTC-USDT-SWAP")
        self.assertTrue(url.endswith("/btcusdtswap@trade"), url)

    def test_okx_returns_the_bare_endpoint(self):
        self.assertEqual(ms.stream_url("okx", "BTC-USDT-SWAP"),
                         ms.VENUE_ENDPOINTS["okx"])


# ───────────────────── _tick ─────────────────────
class TickTests(unittest.TestCase):
    def test_a_tick_records_both_clocks(self):
        tick = ms._tick("okx", "BTC-USDT-SWAP", 100.0, kind="ticker",
                        exchange_ms=1000, now_ms=1500)
        self.assertEqual(tick["exchange_ms"], 1000)
        self.assertEqual(tick["local_ms"], 1500)
        self.assertEqual(tick["latency_ms"], 500)

    def test_a_missing_exchange_clock_gives_no_latency(self):
        # 「只在交易所给了时间戳时才算，不给就是 None（不臆造 0）」
        tick = ms._tick("okx", "BTC-USDT-SWAP", 100.0, kind="ticker", now_ms=1500)
        self.assertIsNone(tick["exchange_ms"])
        self.assertIsNone(tick["latency_ms"])

    def test_a_clock_skew_never_yields_negative_latency(self):
        tick = ms._tick("okx", "X", 1.0, kind="t", exchange_ms=9999, now_ms=1000)
        self.assertEqual(tick["latency_ms"], 0)

    def test_a_bad_price_yields_no_tick(self):
        self.assertIsNone(ms._tick("okx", "X", "abc", kind="t"))

    def test_a_blank_symbol_yields_no_tick(self):
        self.assertIsNone(ms._tick("okx", "", 1.0, kind="t"))

    def test_an_unparsable_exchange_clock_is_treated_as_absent(self):
        tick = ms._tick("okx", "X", 1.0, kind="t", exchange_ms="junk", now_ms=5)
        self.assertIsNone(tick["exchange_ms"])


# ───────────────────── parse_frame ─────────────────────
class ParseFrameTests(unittest.TestCase):
    def _p(self, venue, raw, **kw):
        return ms.parse_frame(venue, raw, now_ms=kw.pop("now_ms", 5000), **kw)

    def test_bytes_frames_are_decoded(self):
        raw = json.dumps({"event": "subscribe"}).encode("utf-8")
        self.assertEqual(self._p("okx", raw)["control"], "okx subscribe")

    def test_a_non_object_frame_is_reported_not_raised(self):
        out = self._p("okx", "[1, 2]")
        self.assertIn("非对象帧", out["error"])

    def test_malformed_json_is_reported_not_raised(self):
        out = self._p("okx", "{ broken")
        self.assertIn("error", out["error"].lower())

    def test_an_unknown_venue_is_reported(self):
        out = self._p("kraken", {"a": 1})
        self.assertIn("未知场所", out["error"])

    # — OKX —
    def test_an_okx_error_event_is_reported(self):
        # ★ 第 173–175 行
        out = self._p("okx", {"event": "error", "code": "60012", "msg": "bad request"})
        self.assertIn("okx error code=60012", out["error"])
        self.assertIn("bad request", out["error"])
        self.assertEqual(out["ticks"], [])

    def test_a_non_dict_okx_row_is_skipped(self):
        # ★ 第 180/181 行
        out = self._p("okx", {"data": ["junk", {"instId": "BTC-USDT-SWAP", "last": "1"}]})
        self.assertEqual(len(out["ticks"]), 1)

    def test_the_okx_inst_id_falls_back_to_the_arg(self):
        out = self._p("okx", {"arg": {"instId": "BTC-USDT-SWAP"}, "data": [{"last": "1"}]})
        self.assertEqual(out["ticks"][0]["symbol"], "BTC-USDT-SWAP")

    def test_an_okx_row_without_a_last_price_is_reported(self):
        out = self._p("okx", {"data": [{"instId": "BTC-USDT-SWAP"}]})
        self.assertIn("没有可解析的 last", out["error"])

    def test_okx_bid_and_ask_are_attached(self):
        out = self._p("okx", {"data": [{"instId": "BTC-USDT-SWAP", "last": "1",
                                        "bidPx": "0.9", "askPx": "1.1"}]})
        self.assertEqual(out["ticks"][0]["bid"], 0.9)
        self.assertEqual(out["ticks"][0]["ask"], 1.1)

    # — Gate —
    def test_a_gate_subscribe_ack_is_not_a_tick(self):
        # 第一版栽在这里：应答帧被当成 tickers 帧 ⇒ 每连一次多一条假解析错误
        out = self._p("gate", {"event": "subscribe",
                               "result": {"status": "success"}})
        self.assertEqual(out["ticks"], [])
        self.assertIsNone(out["error"])
        self.assertIn("gate subscribe success", out["control"])

    def test_a_gate_subscribe_rejection_is_reported(self):
        out = self._p("gate", {"event": "subscribe", "result": {"status": "fail"},
                               "error": {"code": 2, "message": "unknown currency pair"}})
        self.assertIn("gate 订阅被拒 code=2", out["error"])
        self.assertIn("unknown currency pair", out["error"])

    def test_a_gate_error_event_is_reported(self):
        # ★ 第 204–207 行
        out = self._p("gate", {"event": "error",
                               "error": {"code": 9, "message": "throttled"}})
        self.assertIn("gate error code=9", out["error"])
        self.assertIn("throttled", out["error"])

    def test_a_gate_error_without_an_event_key_is_reported(self):
        out = self._p("gate", {"error": {"code": 1, "message": "x"}})
        self.assertIn("gate error code=1", out["error"])

    def test_a_non_dict_gate_row_is_skipped(self):
        # ★ 第 211/212 行
        out = self._p("gate", {"event": "update", "result": [
            "junk", {"contract": "BTC_USDT", "last": "1"}]})
        self.assertEqual(len(out["ticks"]), 1)

    def test_a_gate_row_without_a_last_price_is_reported(self):
        # ★ 第 216/217 行
        out = self._p("gate", {"event": "update", "result": [{"contract": "BTC_USDT"}]})
        self.assertIn("没有可解析的 last", out["error"])

    def test_the_gate_mark_price_is_attached(self):
        out = self._p("gate", {"event": "update", "result": [
            {"contract": "BTC_USDT", "last": "1", "mark_price": "1.5"}]})
        self.assertEqual(out["ticks"][0]["mark_price"], 1.5)

    def test_a_gate_result_that_is_not_a_list_is_tolerated(self):
        out = self._p("gate", {"event": "update", "result": {"junk": 1}})
        self.assertEqual(out["ticks"], [])

    # — Binance —
    def test_a_combined_stream_envelope_is_unwrapped(self):
        inner = {"e": "trade", "s": "BTCUSDT", "p": "100", "T": 1000}
        out = self._p("binance", {"stream": "btcusdt@trade", "data": inner})
        self.assertEqual(out["ticks"][0]["price"], 100.0)

    def test_a_binance_ack_is_recorded_as_control(self):
        out = self._p("binance", {"result": None, "id": 1})
        self.assertIn("binance ack id=1", out["control"])

    def test_a_binance_error_payload_is_reported(self):
        # ★ 第 227–229 行
        out = self._p("binance", {"code": -1121, "msg": "Invalid symbol."})
        self.assertIn("binance error code=-1121", out["error"])
        self.assertIn("Invalid symbol.", out["error"])

    def test_a_binance_trade_event_is_parsed(self):
        out = self._p("binance", {"e": "trade", "s": "BTCUSDT", "p": "100",
                                  "q": "2", "T": 1000})
        self.assertEqual(out["ticks"][0]["kind"], "trade")
        self.assertEqual(out["ticks"][0]["qty"], 2.0)

    def test_a_mark_price_update_is_parsed(self):
        out = self._p("binance", {"e": "markPriceUpdate", "s": "BTCUSDT",
                                  "p": "99", "E": 1})
        self.assertEqual(out["ticks"][0]["kind"], "mark")

    def test_a_24hr_ticker_event_uses_the_close_price(self):
        out = self._p("binance", {"e": "24hrTicker", "s": "BTCUSDT", "c": "101", "E": 1})
        self.assertEqual(out["ticks"][0]["kind"], "ticker")
        self.assertEqual(out["ticks"][0]["price"], 101.0)

    def test_an_unhandled_binance_event_is_control_only(self):
        out = self._p("binance", {"e": "depthUpdate", "s": "BTCUSDT"})
        self.assertEqual(out["ticks"], [])
        self.assertIn("binance depthUpdate", out["control"])

    def test_a_binance_frame_without_an_event_yields_nothing(self):
        out = self._p("binance", {"junk": 1})
        self.assertEqual(out["ticks"], [])
        self.assertIsNone(out["control"])

    def test_an_exception_inside_the_parser_is_reported_not_raised(self):
        # ★ 第 250–252 行 —— 解析器自身异常也必须留痕，绝不上抛
        with patch.object(ms, "_tick", side_effect=RuntimeError("boom")):
            out = self._p("binance", {"e": "trade", "s": "BTCUSDT", "p": "1"})
        self.assertIn("parse RuntimeError: boom", out["error"])

    def test_the_parse_error_keeps_any_control_collected_so_far(self):
        # 先由 ack 分支设上 control，再让 `_tick` 抛 ⇒ control 必须被保留进错误返回
        with patch.object(ms, "_tick", side_effect=RuntimeError("boom")):
            out = self._p("binance", {"result": None, "id": 7,
                                      "e": "trade", "s": "BTCUSDT", "p": "1"})
        self.assertIn("binance ack id=7", out["control"])
        self.assertIn("parse RuntimeError", out["error"])


# ───────────────────── TickBuffer ─────────────────────
class TickBufferTests(unittest.TestCase):
    def _buf(self, maxlen=3):
        return ms.TickBuffer(maxlen=maxlen)

    def test_the_key_is_lowercased_venue_colon_symbol(self):
        self.assertEqual(ms.TickBuffer.key("OKX", "BTC-USDT-SWAP"), "okx:BTC-USDT-SWAP")

    def test_a_tick_can_be_read_back(self):
        buf = self._buf()
        buf.put({"venue": "okx", "symbol": "BTC-USDT-SWAP", "price": 1.0})
        self.assertEqual(buf.latest("okx:BTC-USDT-SWAP")["price"], 1.0)

    def test_latest_returns_a_copy(self):
        buf = self._buf()
        buf.put({"venue": "okx", "symbol": "X", "price": 1.0})
        got = buf.latest("okx:X")
        got["price"] = 999
        self.assertEqual(buf.latest("okx:X")["price"], 1.0)

    def test_an_unknown_key_yields_none(self):
        self.assertIsNone(self._buf().latest("nope"))

    def test_the_buffer_is_bounded(self):
        buf = self._buf(maxlen=2)
        for i in range(5):
            buf.put({"venue": "okx", "symbol": "X", "price": i, "local_ms": i})
        self.assertEqual(buf.sizes()["okx:X"], 2)
        self.assertEqual(buf.latest("okx:X")["price"], 4)

    def test_keys_are_sorted(self):
        # ★ 第 288–290 行
        buf = self._buf()
        for sym in ("ETH", "BTC", "ADA"):
            buf.put({"venue": "okx", "symbol": sym, "price": 1.0})
        self.assertEqual(buf.keys(), ["okx:ADA", "okx:BTC", "okx:ETH"])

    def test_an_empty_buffer_has_no_keys(self):
        self.assertEqual(self._buf().keys(), [])

    def test_sizes_reports_per_key_counts(self):
        buf = self._buf(maxlen=10)
        buf.put({"venue": "okx", "symbol": "A", "price": 1.0})
        buf.put({"venue": "okx", "symbol": "A", "price": 1.0})
        buf.put({"venue": "gate", "symbol": "B", "price": 1.0})
        self.assertEqual(buf.sizes(), {"okx:A": 2, "gate:B": 1})

    def test_age_is_measured_from_the_local_clock(self):
        buf = self._buf()
        buf.put({"venue": "okx", "symbol": "X", "price": 1.0, "local_ms": 1000})
        self.assertEqual(buf.age_s("okx:X", now_ms=3500), 2.5)

    def test_age_never_goes_negative(self):
        buf = self._buf()
        buf.put({"venue": "okx", "symbol": "X", "price": 1.0, "local_ms": 5000})
        self.assertEqual(buf.age_s("okx:X", now_ms=1000), 0.0)

    def test_age_of_an_unknown_key_is_none(self):
        self.assertIsNone(self._buf().age_s("nope", now_ms=1))


# ───────────────────── StreamHealth ─────────────────────
class StreamHealthTests(unittest.TestCase):
    def test_a_frame_is_counted_with_its_ticks(self):
        h = ms.StreamHealth()
        h.note_frame("okx", ticks=3, now_ms=1000)
        venues = h.snapshot(now_ms=2000)["venues"]["okx"]
        self.assertEqual((venues["frames"], venues["ticks"]), (1, 3))

    def test_a_frame_without_ticks_does_not_refresh_the_tick_clock(self):
        # 「只连上、只收到订阅应答，在账本里必须表现为 tick 陈旧，而不是健康」
        h = ms.StreamHealth()
        h.note_frame("binance", ticks=0, now_ms=1000)
        self.assertIsNone(h.staleness_s("binance", now_ms=2000),
                          "从未收到 tick ⇒ None（不可判定 ≠ 安全）")
        self.assertEqual(h.snapshot(now_ms=2000)["venues"]["binance"]["last_msg_ms"],
                         1000)

    def test_a_frame_error_is_counted_separately(self):
        h = ms.StreamHealth()
        h.note_frame("okx", error="bad frame", now_ms=1)
        snap = h.snapshot(now_ms=2)["venues"]["okx"]
        self.assertEqual(snap["parse_errors"], 1)
        self.assertEqual(snap["last_error"], "bad frame")

    def test_the_error_text_is_truncated(self):
        h = ms.StreamHealth()
        h.note_frame("okx", error="x" * 500, now_ms=1)
        self.assertEqual(len(h.snapshot(now_ms=2)["venues"]["okx"]["last_error"]), 200)

    def test_a_connection_error_is_counted(self):
        h = ms.StreamHealth()
        h.note_error("gate", "OSError: no net", now_ms=1)
        snap = h.snapshot(now_ms=2)["venues"]["gate"]
        self.assertEqual(snap["errors"], 1)
        self.assertIn("no net", snap["last_error"])

    def test_a_reconnect_is_counted(self):
        # ★ 第 334–336 行
        h = ms.StreamHealth()
        h.note_frame("okx", now_ms=1)
        h.note_reconnect("okx")
        h.note_reconnect("okx")
        self.assertEqual(h.snapshot(now_ms=1)["venues"]["okx"]["reconnects"], 2)

    def test_a_venue_known_only_by_reconnects_is_invisible_in_the_snapshot(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：第 349 行的场所集合是
        #   `set(self._frames) | set(self._ticks) | set(self._errors)`
        #   —— **不含 `self._reconnects`**。
        #   ⇒ 一个只被 `note_reconnect()` 记过的场所**根本不出现在快照里**，
        #     它的重连次数被静默吞掉。
        #   这与本模块"静默的失败是最贵的"的一贯纪律相悖（reconnect 本身是健康信号）。
        h = ms.StreamHealth()
        h.note_reconnect("gate")
        self.assertNotIn("gate", h.snapshot(now_ms=1)["venues"])

    def test_a_venue_with_frames_does_report_its_reconnects(self):
        # 对照：只要该场所同时有帧/错误，reconnect 就正常露出
        h = ms.StreamHealth()
        h.note_frame("gate", now_ms=1)
        h.note_reconnect("gate")
        self.assertEqual(h.snapshot(now_ms=1)["venues"]["gate"]["reconnects"], 1)

    def test_staleness_is_none_before_the_first_tick(self):
        # 「从未收到过返回 None（不是 0）」
        self.assertIsNone(ms.StreamHealth().staleness_s("okx", now_ms=1000))

    def test_staleness_is_measured_from_the_last_tick(self):
        h = ms.StreamHealth()
        h.note_frame("okx", ticks=1, now_ms=1000)
        self.assertEqual(h.staleness_s("okx", now_ms=4000), 3.0)

    def test_staleness_never_goes_negative(self):
        h = ms.StreamHealth()
        h.note_frame("okx", ticks=1, now_ms=5000)
        self.assertEqual(h.staleness_s("okx", now_ms=1000), 0.0)

    def test_the_snapshot_carries_the_schema_version(self):
        self.assertEqual(ms.StreamHealth().snapshot(now_ms=1)["schema_version"],
                         ms.SCHEMA_VERSION)

    def test_the_snapshot_lists_all_known_venues_sorted(self):
        h = ms.StreamHealth()
        h.note_frame("okx", now_ms=1)
        h.note_error("binance", "x", now_ms=1)
        h.note_frame("gate", now_ms=1)
        self.assertEqual(list(h.snapshot(now_ms=2)["venues"]), ["binance", "gate", "okx"])

    def test_tick_age_is_rounded_to_milliseconds(self):
        h = ms.StreamHealth()
        h.note_frame("okx", ticks=1, now_ms=1000)
        self.assertEqual(h.snapshot(now_ms=2500)["venues"]["okx"]["tick_age_s"], 1.5)

    def test_tick_age_is_none_without_a_tick(self):
        h = ms.StreamHealth()
        h.note_frame("okx", ticks=0, now_ms=1000)
        self.assertIsNone(h.snapshot(now_ms=2000)["venues"]["okx"]["tick_age_s"])


# ───────────────────── 快照落盘 ─────────────────────
class SnapshotIoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_a_snapshot_round_trips(self):
        path = self.root / "s.json"
        payload = {"schema_version": ms.SCHEMA_VERSION, "a": 1}
        self.assertTrue(ms.write_snapshot(str(path), payload))
        self.assertEqual(ms.load_snapshot(str(path))["a"], 1)

    def test_the_parent_directory_is_created(self):
        path = self.root / "deep" / "s.json"
        self.assertTrue(ms.write_snapshot(str(path), {"schema_version": ms.SCHEMA_VERSION}))
        self.assertTrue(path.exists())

    def test_no_temporary_file_survives(self):
        ms.write_snapshot(str(self.root / "s.json"), {"a": 1})
        self.assertEqual([p.name for p in self.root.glob(".ms-*")], [])

    def test_a_write_failure_cleans_up_and_returns_false(self):
        # ★ 第 385–393 行 —— **绝不抛异常**，失败返回 False
        path = self.root / "s.json"
        with patch.object(ms.os, "replace", side_effect=OSError("disk full")):
            self.assertFalse(ms.write_snapshot(str(path), {"a": 1}))
        self.assertEqual([p.name for p in self.root.glob(".ms-*")], [])

    def test_a_cleanup_failure_still_returns_false(self):
        # ★ 第 388/389 行 —— 清理本身失败也只吞掉
        path = self.root / "s.json"
        with patch.object(ms.os, "replace", side_effect=OSError("disk full")), \
             patch.object(ms.os, "unlink", side_effect=OSError("read-only")):
            self.assertFalse(ms.write_snapshot(str(path), {"a": 1}))

    def test_an_unusable_path_returns_false(self):
        self.assertFalse(ms.write_snapshot(None, {"a": 1}))

    def test_a_missing_file_loads_as_empty(self):
        self.assertEqual(ms.load_snapshot(str(self.root / "nope.json")), {})

    def test_a_corrupt_file_loads_as_empty(self):
        path = self.root / "s.json"
        path.write_text("{ broken", encoding="utf-8")
        self.assertEqual(ms.load_snapshot(str(path)), {})

    def test_a_non_dict_body_loads_as_empty(self):
        path = self.root / "s.json"
        path.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(ms.load_snapshot(str(path)), {})

    def test_an_unknown_schema_version_loads_as_empty(self):
        # 版本不认识 ⇒ `{}`（调用方据此标 source_ok=0）
        path = self.root / "s.json"
        path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
        self.assertEqual(ms.load_snapshot(str(path)), {})


# ───────────────────── probe ─────────────────────
class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.now = lambda: 10_000

    def test_a_venue_with_ticks_is_healthy(self):
        factory, holder = _connect([json.dumps({"e": "trade", "s": "BTCUSDT",
                                                "p": "100", "T": 9000})])
        snap = ms.probe(venues=["binance"], seconds=0.5, connect_factory=factory,
                        now=self.now)
        self.assertEqual(snap["venues"]["binance"]["ticks"], 1)
        # `now` 固定 ⇒ 写入时刻与 tick 时刻相同 ⇒ age 是 0.0（**不是** None）
        self.assertEqual(snap["venues"]["binance"]["tick_age_s"], 0.0)
        # `venue_symbol` 已把 `-SWAP` 摘掉 ⇒ 流名是 `btcusdt@trade`
        self.assertTrue(holder["url"].endswith("/btcusdt@trade"), holder["url"])

    def test_a_venue_that_never_sends_data_is_flagged(self):
        # 「连上了但一条数据都没来」也要留痕（Binance 静默订阅的形态）
        factory, _ = _connect([])
        snap = ms.probe(venues=["binance"], seconds=0.5, connect_factory=factory,
                        now=self.now)
        entry = snap["venues"]["binance"]
        self.assertEqual(entry["frames"], 0)
        self.assertEqual(entry["errors"], 1)
        self.assertIn("零数据帧", entry["last_error"])

    def test_one_dead_venue_does_not_affect_the_others(self):
        # 每一所独立 try
        def _factory(url, **kw):
            if "binance" in url:
                raise OSError("binance down")
            return _FakeWs([json.dumps({"event": "subscribe"})])
        snap = ms.probe(venues=["okx", "binance"], seconds=0.3,
                        connect_factory=_factory, now=self.now)
        self.assertIn("binance down", snap["venues"]["binance"]["last_error"])
        self.assertEqual(snap["venues"]["okx"]["frames"], 1)

    def test_each_venue_gets_its_own_window(self):
        # 第一版把 deadline 设成全场共享 ⇒ 只有第一个所有窗口
        seen: list = []

        def _factory(url, **kw):
            seen.append(url)
            return _FakeWs([])
        ms.probe(venues=["okx", "gate", "binance"], seconds=0.5,
                 connect_factory=_factory, now=self.now)
        self.assertEqual(len(seen), 3)

    def test_the_snapshot_is_written_when_a_path_is_given(self):
        factory, _ = _connect([])
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "s.json")
            snap = ms.probe(venues=["okx"], seconds=0.3, connect_factory=factory,
                            snapshot_path=path, now=self.now)
            self.assertTrue(snap["written"])
            self.assertTrue(Path(path).exists())

    def test_the_buffer_sizes_are_reported(self):
        factory, _ = _connect([json.dumps({"event": "subscribe"}),
                               json.dumps({"data": [{"instId": "BTC-USDT-SWAP",
                                                     "last": "1"}]})])
        snap = ms.probe(venues=["okx"], seconds=0.5, connect_factory=factory,
                        now=self.now)
        self.assertEqual(snap["buffers"], {"okx:BTC-USDT-SWAP": 1})

    def test_a_subscribe_frame_is_sent_for_non_path_venues(self):
        # 非路径式场所（okx/gate）必须**主动发订阅帧**；binance 是路径式，不发
        sent: list = []

        def _factory(url, **kw):
            ws = _FakeWs([])
            orig_send = ws.send

            def _send(text):
                sent.append(text)
                return orig_send(text)
            ws.send = _send
            return ws
        ms.probe(venues=["okx", "binance"], seconds=0.3, connect_factory=_factory,
                 now=self.now)
        self.assertEqual(len(sent), 1, "只有 okx 需要发订阅帧")
        frame = json.loads(sent[0])
        self.assertEqual(frame["op"], "subscribe")
        self.assertEqual(frame["args"][0]["channel"], "tickers")

    def test_symbol_by_venue_overrides_the_default_name(self):
        urls: list = []

        def _factory(url, **kw):
            urls.append(url)
            return _FakeWs([])
        ms.probe(venues=["binance"], symbol="BTC-USDT-SWAP",
                 symbol_by_venue={"binance": "ETHUSDT"}, seconds=0.3,
                 connect_factory=_factory, now=self.now)
        self.assertIn("ethusdt@trade", urls[0])


# ───────────────────── CLI ─────────────────────
class MainTests(unittest.TestCase):
    def test_without_probe_the_help_is_printed(self):
        with patch.object(sys, "stdout", new=__import__("io").StringIO()) as buf:
            self.assertEqual(ms._main([]), 0)
        self.assertIn("--probe", buf.getvalue())

    def test_with_probe_the_venues_are_split_and_probed(self):
        # ★ 第 473 行 —— 逗号列表被 split/strip/filter
        captured: dict = {}

        def _probe(**kw):
            captured.update(kw)
            return {"venues": {}}
        with patch.object(ms, "probe", _probe), \
             patch.object(sys, "stdout", new=__import__("io").StringIO()):
            rc = ms._main(["--probe", "--venues", "okx, gate ,,binance",
                           "--seconds", "0.1", "--symbol", "ETH-USDT-SWAP"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured["venues"], ["okx", "gate", "binance"])

    def test_with_probe_a_snapshot_path_is_forwarded(self):
        captured: dict = {}

        def _probe(**kw):
            captured.update(kw)
            return {"venues": {}}
        with patch.object(ms, "probe", _probe), \
             patch.object(sys, "stdout", new=__import__("io").StringIO()):
            ms._main(["--probe", "--snapshot", "/tmp/x.json"])
        self.assertEqual(captured["snapshot_path"], "/tmp/x.json")

    def test_an_empty_snapshot_flag_becomes_none(self):
        captured: dict = {}

        def _probe(**kw):
            captured.update(kw)
            return {"venues": {}}
        with patch.object(ms, "probe", _probe), \
             patch.object(sys, "stdout", new=__import__("io").StringIO()):
            ms._main(["--probe"])
        self.assertIsNone(captured["snapshot_path"])

    def test_the_snapshot_is_printed_as_json(self):
        with patch.object(ms, "probe", lambda **kw: {"venues": {"okx": {}}}), \
             patch.object(sys, "stdout", new=__import__("io").StringIO()) as buf:
            ms._main(["--probe"])
        self.assertIn('"venues"', buf.getvalue())


if __name__ == "__main__":
    unittest.main()

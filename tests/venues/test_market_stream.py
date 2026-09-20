"""公共行情流（只读）契约测试。

## 为什么夹具用**真实帧**

本刀三个 bug 全部是"照着文档写、真端点不认"：Gate 要期货专用域、Binance JSON 订阅
**应答成功但永不推数据**、Gate 订阅应答被误当 tick 记了一次假解析错误。
故下面的夹具**逐字来自本机实跑抓到的真帧**（2026-09-20），改动它们等于改动事实。

## 守什么

1. **解析不能"看着成功"**：订阅应答/错误帧必须与 tick 分开（假 tick 会让
   下游把"订阅成功"当成一次真实报价）；
2. **沉默必须可判**："已连接但零帧"要留痕、`staleness` 在从未收到 tick 时是
   `None` 而**不是 0**（0 会被读成"刚刚有数据"）；
3. **只读**：`probe()` 的传输可注入，测试绝不出网；CLI 不带 `--probe` 不联网。
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import market_stream as ms  # noqa: E402

# ── 真实帧（本机实跑抓取，逐字保留）────────────────────────────────────────
OKX_ACK = ('{"event":"subscribe","arg":{"channel":"tickers","instId":"BTC-USDT-SWAP"},'
           '"connId":"4880aa85"}')
OKX_TICK = ('{"arg":{"channel":"tickers","instId":"BTC-USDT-SWAP"},"data":[{"instType":"SWAP",'
            '"instId":"BTC-USDT-SWAP","last":"80214.9","lastSz":"0.28","askPx":"80214.9",'
            '"askSz":"214.99","bidPx":"80214.8","bidSz":"589.89","open24h":"81290.8",'
            '"ts":"1789893848451"}]}')
GATE_ACK = ('{"time":1789893869,"time_ms":1789893869774,"conn_id":"ca6603ab1c63e6a0",'
            '"channel":"futures.tickers","event":"subscribe","payload":["BTC_USDT"],'
            '"result":{"status":"success"}}')
GATE_TICK = ('{"time":1789893869,"time_ms":1789893869795,"channel":"futures.tickers",'
             '"event":"update","result":[{"contract":"BTC_USDT","last":"80236.0",'
             '"change_percentage":"-1.3034","volume_24h":"364322875","mark_price":"80237.1",'
             '"time_ms":1789893869795}]}')
GATE_REJECTED = ('{"channel":"futures.tickers","event":"subscribe","payload":["BTC_USDT"],'
                 '"error":{"code":2,"message":"Unknown channel futures.tickers"},'
                 '"result":{"status":"fail"}}')
BINANCE_ACK = '{"result":null,"id":1}'
BINANCE_TRADE = ('{"e":"trade","E":1789893896920,"T":1789893896920,"s":"BTCUSDT",'
                 '"t":8097313955,"p":"80227.00","q":"0.010","X":"MARKET","m":true,"st":1}')
BINANCE_MARK = ('{"e":"markPriceUpdate","E":1789893896000,"s":"BTCUSDT","p":"80230.5",'
                '"T":1789893896000}')
BINANCE_24H = ('{"e":"24hrTicker","E":1789893896000,"s":"BTCUSDT","c":"80230.5",'
               '"h":"81930","l":"80100"}')
BINANCE_COMBINED = ('{"stream":"btcusdt@trade","data":' + BINANCE_TRADE + '}')


class RealFrameParseTest(unittest.TestCase):
    def test_okx_subscribe_ack_is_control_not_tick(self):
        out = ms.parse_frame("okx", OKX_ACK)
        self.assertEqual(out["ticks"], [])
        self.assertIn("subscribe", out["control"])
        self.assertIsNone(out["error"])

    def test_okx_ticker_frame_yields_one_tick(self):
        out = ms.parse_frame("okx", OKX_TICK, now_ms=1789893848451)
        self.assertEqual(len(out["ticks"]), 1)
        tick = out["ticks"][0]
        self.assertEqual((tick["venue"], tick["symbol"], tick["kind"]),
                         ("okx", "BTC-USDT-SWAP", "ticker"))
        self.assertAlmostEqual(tick["price"], 80214.9)
        self.assertAlmostEqual(tick["bid"], 80214.8)
        self.assertAlmostEqual(tick["ask"], 80214.9)
        self.assertEqual(tick["exchange_ms"], 1789893848451)
        self.assertEqual(tick["latency_ms"], 0, "同刻时间戳 ⇒ 延迟 0，不是 None")

    def test_gate_subscribe_ack_is_not_a_parse_error(self):
        """回归钉（本机实跑抓到的假错误）：应答帧若落进行解析会多一条假解析错误。"""
        out = ms.parse_frame("gate", GATE_ACK)
        self.assertIsNone(out["error"], "订阅应答不是 tick，不该报解析错误")
        self.assertEqual(out["ticks"], [])
        self.assertIn("subscribe success", out["control"])

    def test_gate_update_frame_yields_tick_with_mark_price(self):
        out = ms.parse_frame("gate", GATE_TICK, now_ms=1789893869795)
        self.assertEqual(len(out["ticks"]), 1)
        tick = out["ticks"][0]
        self.assertEqual((tick["symbol"], tick["kind"]), ("BTC_USDT", "ticker"))
        self.assertAlmostEqual(tick["price"], 80236.0)
        self.assertAlmostEqual(tick["mark_price"], 80237.1)

    def test_gate_rejected_subscribe_is_an_error(self):
        """真帧：往现货域发 futures.tickers 会"连上但订阅被拒"。"""
        out = ms.parse_frame("gate", GATE_REJECTED)
        self.assertEqual(out["ticks"], [])
        self.assertIn("订阅被拒", out["error"])
        self.assertIn("2", out["error"])

    def test_binance_ack_and_trade_frames(self):
        ack = ms.parse_frame("binance", BINANCE_ACK)
        self.assertEqual(ack["ticks"], [])
        self.assertIn("ack", ack["control"])
        trade = ms.parse_frame("binance", BINANCE_TRADE, now_ms=1789893896920)
        self.assertEqual(trade["ticks"][0]["kind"], "trade")
        self.assertAlmostEqual(trade["ticks"][0]["price"], 80227.0)
        self.assertAlmostEqual(trade["ticks"][0]["qty"], 0.010)

    def test_binance_mark_and_24h_variants(self):
        mark = ms.parse_frame("binance", BINANCE_MARK)
        self.assertEqual(mark["ticks"][0]["kind"], "mark")
        day = ms.parse_frame("binance", BINANCE_24H)
        self.assertEqual(day["ticks"][0]["kind"], "ticker")
        self.assertAlmostEqual(day["ticks"][0]["price"], 80230.5)

    def test_binance_combined_stream_is_unwrapped(self):
        out = ms.parse_frame("binance", BINANCE_COMBINED)
        self.assertEqual(len(out["ticks"]), 1)
        self.assertEqual(out["ticks"][0]["kind"], "trade")

    def test_unknown_event_is_control_not_tick(self):
        out = ms.parse_frame("binance", '{"e":"listenKeyExpired","E":1}')
        self.assertEqual(out["ticks"], [])
        self.assertIn("listenKeyExpired", out["control"])

    def test_garbage_never_raises_but_is_recorded(self):
        for bad in ("{not json", "[1,2,3]", b"\xff\xfe", "", "null"):
            with self.subTest(bad=bad):
                out = ms.parse_frame("okx", bad)
                self.assertEqual(out["ticks"], [])
                self.assertTrue(out["error"], f"坏帧必须留痕：{bad!r}")

    def test_unknown_venue_is_an_error(self):
        out = ms.parse_frame("kraken", "{}")
        self.assertIn("未知场所", out["error"])

    def test_tick_without_price_is_not_a_tick(self):
        out = ms.parse_frame("okx", '{"arg":{"instId":"BTC-USDT-SWAP"},'
                                    '"data":[{"instId":"BTC-USDT-SWAP"}]}')
        self.assertEqual(out["ticks"], [])
        self.assertTrue(out["error"], "没有 last 的帧不能算报价")


class SymbolMappingTest(unittest.TestCase):
    """实测两连击：Gate `BTC_USDT_SWAP` 被拒；Binance `btcusdtswap` 静默零数据。"""

    def test_canonical_symbol_maps_per_venue(self):
        for venue, want in (("okx", "BTC-USDT-SWAP"), ("gate", "BTC_USDT"),
                            ("binance", "BTCUSDT")):
            with self.subTest(venue=venue):
                self.assertEqual(ms.venue_symbol(venue, "BTC-USDT-SWAP"), want)

    def test_various_input_spellings_normalise(self):
        self.assertEqual(ms.venue_symbol("gate", "eth_usdt"), "ETH_USDT")
        self.assertEqual(ms.venue_symbol("binance", "ETH/USDT"), "ETHUSDT")
        self.assertEqual(ms.venue_symbol("okx", "ETH-USDT"), "ETH-USDT-SWAP")

    def test_unknown_venue_raises_loudly(self):
        with self.assertRaises(KeyError):
            ms.venue_symbol("kraken", "BTC-USDT-SWAP")

    def test_binance_uses_path_subscription(self):
        url = ms.stream_url("binance", ms.venue_symbol("binance", "BTC-USDT-SWAP"))
        self.assertTrue(url.endswith("/btcusdt@trade"), url)
        self.assertIsNone(ms.subscribe_payload("binance", "BTCUSDT"),
                          "路径式订阅不该再发 JSON 订阅帧")

    def test_json_venues_send_subscribe_frames(self):
        okx = ms.subscribe_payload("okx", "BTC-USDT-SWAP")
        self.assertEqual(okx["op"], "subscribe")
        self.assertEqual(okx["args"][0]["channel"], "tickers")
        gate = ms.subscribe_payload("gate", "BTC_USDT")
        self.assertEqual(gate["channel"], "futures.tickers")
        self.assertEqual(gate["payload"], ["BTC_USDT"])
        self.assertIn("fx-ws.gateio.ws", ms.stream_url("gate", "BTC_USDT"),
                      "Gate 期货流必须走专用域（现货域不接受 futures.tickers）")


class TickBufferTest(unittest.TestCase):
    def test_buffer_is_bounded(self):
        buf = ms.TickBuffer(maxlen=3)
        for i in range(10):
            buf.put({"venue": "okx", "symbol": "BTC-USDT-SWAP", "price": i, "local_ms": i})
        self.assertEqual(buf.sizes()["okx:BTC-USDT-SWAP"], 3, "缓冲必须有界")
        self.assertEqual(buf.latest("okx:BTC-USDT-SWAP")["price"], 9)

    def test_age_of_unknown_key_is_none_not_zero(self):
        buf = ms.TickBuffer()
        self.assertIsNone(buf.age_s("okx:NOPE", now_ms=1000),
                          "没有数据 ≠ 0 秒前有数据")

    def test_age_is_computed_from_local_clock(self):
        buf = ms.TickBuffer()
        buf.put({"venue": "okx", "symbol": "BTC-USDT-SWAP", "price": 1, "local_ms": 1_000})
        self.assertAlmostEqual(buf.age_s("okx:BTC-USDT-SWAP", now_ms=4_000), 3.0)

    def test_latest_returns_a_copy(self):
        buf = ms.TickBuffer()
        buf.put({"venue": "okx", "symbol": "BTC-USDT-SWAP", "price": 1, "local_ms": 1})
        got = buf.latest("okx:BTC-USDT-SWAP")
        got["price"] = 999
        self.assertEqual(buf.latest("okx:BTC-USDT-SWAP")["price"], 1)


class StreamHealthTest(unittest.TestCase):
    def test_counters_and_staleness(self):
        h = ms.StreamHealth()
        self.assertIsNone(h.staleness_s("okx", now_ms=5_000),
                          "从未收到 tick ⇒ None（不可判定，不是健康）")
        h.note_frame("okx", ticks=2, now_ms=1_000)
        h.note_frame("okx", ticks=0, error="bad frame", now_ms=2_000)
        h.note_error("gate", "connect reset", now_ms=2_000)
        snap = h.snapshot(now_ms=6_000)
        okx = snap["venues"]["okx"]
        self.assertEqual((okx["frames"], okx["ticks"], okx["parse_errors"]), (2, 2, 1))
        self.assertEqual(okx["last_msg_ms"], 2_000)
        self.assertEqual(okx["last_tick_ms"], 1_000)
        self.assertAlmostEqual(okx["tick_age_s"], 5.0)
        self.assertEqual(snap["venues"]["gate"]["errors"], 1)
        self.assertEqual(h.staleness_s("okx", now_ms=6_000), 5.0)
        self.assertEqual(snap["schema_version"], ms.SCHEMA_VERSION)

    def test_silence_after_ack_shows_as_stale(self):
        """Binance 实测形态：收到应答、没有 tick ⇒ 绝不能表现成"健康"。

        ⚠️ 这里必须是 `None` 而**不是** 0：0 会被下游读成"刚刚还有数据"。
        可判据是"有帧但零 tick"—— 这条组合就是"订阅被静默"的指纹，
        与"刚连上还没来得及收"（frames=0）也区分得开。
        """
        h = ms.StreamHealth()
        h.note_frame("binance", ticks=0, now_ms=1_000)     # ack
        snap = h.snapshot(now_ms=61_000)
        bnb = snap["venues"]["binance"]
        self.assertEqual(bnb["frames"], 1)
        self.assertEqual(bnb["ticks"], 0)
        self.assertIsNone(bnb["tick_age_s"], "从未收到 tick ⇒ None（0 会被误读成刚有数据）")
        self.assertEqual(h.staleness_s("binance", now_ms=61_000), None)

    def test_healthy_venue_is_distinguishable_from_silent_one(self):
        silent, healthy = ms.StreamHealth(), ms.StreamHealth()
        silent.note_frame("binance", ticks=0, now_ms=1_000)
        healthy.note_frame("binance", ticks=5, now_ms=1_000)
        s = silent.snapshot(now_ms=61_000)["venues"]["binance"]
        h = healthy.snapshot(now_ms=61_000)["venues"]["binance"]
        self.assertNotEqual((s["frames"], s["ticks"], s["tick_age_s"]),
                            (h["frames"], h["ticks"], h["tick_age_s"]),
                            "沉默所的指纹必须与健康所可区分")
        self.assertAlmostEqual(h["tick_age_s"], 60.0)


class FakeTransportTest(unittest.TestCase):
    """`probe()` 的传输全部注入：测试绝不出网。"""

    class _FakeWS:
        def __init__(self, frames, *, fail_connect=False):
            self._frames = list(frames)
            self._fail = fail_connect
            self.sent = []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def send(self, payload):
            self.sent.append(payload)

        def recv(self, timeout=None):
            if self._frames:
                return self._frames.pop(0)
            raise TimeoutError("no more frames")

    def _factory(self, by_venue, connect_log=None):
        def connect(url, **kw):
            venue = next(v for v in ms.VENUE_ENDPOINTS if ms.VENUE_ENDPOINTS[v] in url)
            if connect_log is not None:
                connect_log.append((venue, url))
            item = by_venue.get(venue)
            if isinstance(item, Exception):
                raise item
            return self._FakeWS(item or [])
        return connect

    def test_probe_collects_ticks_from_every_venue(self):
        snap = ms.probe(seconds=0.5, connect_factory=self._factory({
            "okx": [OKX_ACK, OKX_TICK],
            "gate": [GATE_ACK, GATE_TICK],
            "binance": [BINANCE_TRADE, BINANCE_TRADE],
        }))
        self.assertEqual(snap["venues"]["okx"]["ticks"], 1)
        self.assertEqual(snap["venues"]["gate"]["ticks"], 1)
        self.assertEqual(snap["venues"]["binance"]["ticks"], 2)
        self.assertEqual(snap["buffers"]["binance:BTCUSDT"], 2)

    def test_each_venue_gets_its_own_window(self):
        """回归钉：第一版全场共享一个 deadline，OKX 收满后 Gate/Binance 一轮都没跑。"""
        log = []
        ms.probe(seconds=0.5, connect_factory=self._factory({
            "okx": [OKX_TICK], "gate": [GATE_TICK], "binance": [BINANCE_TRADE]}, connect_log=log))
        self.assertEqual([v for v, _ in log], ["okx", "gate", "binance"],
                         "三所都必须真的被连上（共享 deadline 会让后面的所连都不连）")

    def test_silent_venue_is_recorded_not_treated_as_healthy(self):
        snap = ms.probe(seconds=0.5, connect_factory=self._factory({
            "okx": [OKX_TICK], "gate": [], "binance": []}))
        for venue in ("gate", "binance"):
            with self.subTest(venue=venue):
                self.assertEqual(snap["venues"][venue]["ticks"], 0)
                self.assertEqual(snap["venues"][venue]["errors"], 1)
                self.assertIn("零数据帧", snap["venues"][venue]["last_error"])
                self.assertIsNone(snap["venues"][venue]["tick_age_s"])

    def test_one_venue_failure_does_not_stop_the_others(self):
        snap = ms.probe(seconds=0.5, connect_factory=self._factory({
            "okx": RuntimeError("dns down"), "gate": [GATE_TICK], "binance": [BINANCE_TRADE]}))
        self.assertIn("dns down", snap["venues"]["okx"]["last_error"])
        self.assertEqual(snap["venues"]["okx"]["ticks"], 0)
        self.assertEqual(snap["venues"]["gate"]["ticks"], 1, "一个所挂了不该影响其他所")
        self.assertEqual(snap["venues"]["binance"]["ticks"], 1)

    def test_probe_writes_snapshot_when_asked(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "market_stream_health.json"
            ms.probe(seconds=0.5, snapshot_path=str(path),
                     connect_factory=self._factory({"okx": [OKX_TICK], "gate": [], "binance": []}))
            loaded = ms.load_snapshot(str(path))
            self.assertEqual(loaded["venues"]["okx"]["ticks"], 1)


class SnapshotFileTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "market_stream_health.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        payload = ms.StreamHealth().snapshot(now_ms=1234)
        self.assertTrue(ms.write_snapshot(str(self.path), payload))
        self.assertEqual(ms.load_snapshot(str(self.path))["written_at_ms"], 1234)

    def test_missing_broken_and_wrong_version_are_empty(self):
        self.assertEqual(ms.load_snapshot(str(self.path)), {})
        self.path.write_text("{oops", encoding="utf-8")
        self.assertEqual(ms.load_snapshot(str(self.path)), {})
        self.path.write_text(json.dumps({"schema_version": 99, "venues": {}}), encoding="utf-8")
        self.assertEqual(ms.load_snapshot(str(self.path)), {},
                         "版本不认识必须当没有数据")

    def test_write_failure_returns_false(self):
        self.assertFalse(ms.write_snapshot("/proc/nope/x.json",
                                           ms.StreamHealth().snapshot()))


class CliTest(unittest.TestCase):
    def test_without_probe_flag_prints_help_and_does_not_connect(self):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ms._main([])
        self.assertEqual(rc, 0)
        self.assertIn("--probe", buf.getvalue())

    def test_module_import_does_not_pull_a_websocket_library(self):
        """本模块被 import 时不该拉起网络栈（websockets 只在 probe 里延迟导入）。"""
        import subprocess
        code = ("import sys; sys.path.insert(0, '%s'); import scripts.market_stream; "
                "print('websockets' in sys.modules)" % ROOT)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "False", out.stderr[-300:])


if __name__ == "__main__":
    unittest.main()

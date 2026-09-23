"""行情数据服务（`scripts/market_data_service.py`）的残余分支收口 —— 第 326 刀。

本模块 595 行，是**所有行情取数的唯一入口**（ticker / 深度 / 指标 / 蜡烛 / 资金费率 /
持仓量），自带 `www → aws → 异所备源 → 本地纯 Python 数学` 四级容灾。
既有 `tests/venues/test_market_data_service.py` 只有 324 行。本刀补 30 行缺口。

## 本刀立住的两条纪律

1. **多级容灾的每一级都要留痕**（第 137 刀教训）：静默 `except` 会让"现价恒 0 /
   30 小时无信号"；取值行为可以不变，但必须 `note_failure`。
   —— 本刀逐一钉住四级各自的失败路径。
2. **备源序是"防横跳"的**（US-004）：`failed` 数升序为主键，`avg_ms` 只在**差距 > 5 倍**
   时才参与翻转；健康文件缺失/损坏/结构异常一律回退静态 `ALT_VENUES`，**绝不抛**
   （本模块被生产 trader 子进程直接加载）。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.market_data_service as mds  # noqa: E402


def _http(status=200, payload=None, *, json_exc=None):
    resp = MagicMock()
    resp.status_code = status
    if json_exc is not None:
        resp.json.side_effect = json_exc
    else:
        resp.json.return_value = payload if payload is not None else {"code": "0", "data": []}
    return resp


def _candles(n=80, start=100.0):
    """升序蜡烛（OKX 契约是「最新在前」，本模块内部会 reverse）。"""
    return [[str(i), str(start + i), str(start + i + 1), str(start + i - 1),
             str(start + i), "10", "10", "10", "1"] for i in range(n)]


class _Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.health = str(self.root / "venue_health.json")
        for name, value in (("VENUE_HEALTH_FILE", self.health),
                            ("_ALT_ORDER_CACHE", {"ts": 0.0, "order": None})):
            p = patch.object(mds, name, value)
            p.start()
            self.addCleanup(p.stop)
        # 备源默认关闭，避免任何真实网络/适配器调用；用到的用例自行打开
        ev = patch.dict("os.environ", {"R20_ALT_VENUE_FALLBACK": "0"})
        ev.start()
        self.addCleanup(ev.stop)
        rd = patch.object(mds, "REST_FALLBACK_ENABLED", False, create=True)
        rd.start()
        self.addCleanup(rd.stop)


# ───────────────────── OKX 双域直连 ─────────────────────
class PublicGetTests(unittest.TestCase):
    def _run(self, responses):
        sess = MagicMock()
        sess.get.side_effect = list(responses)
        with patch.object(mds, "get_market_session", return_value=sess):
            return mds._public_get("/api/v5/market/ticker", params={"instId": "BTC"})

    def test_a_good_response_is_returned(self):
        out = self._run([_http(payload={"code": "0", "data": [{"instId": "BTC"}]})])
        self.assertEqual(out["data"][0]["instId"], "BTC")

    def test_a_non_zero_business_code_falls_through_to_the_next_host(self):
        out = self._run([_http(payload={"code": "51001", "msg": "no such instrument"}),
                         _http(payload={"code": "0", "data": ["ok"]})])
        self.assertEqual(out["data"], ["ok"])

    def test_an_http_error_status_falls_through_to_the_next_host(self):
        # ★ 第 144/145 行
        out = self._run([_http(status=503), _http(payload={"code": "0", "data": ["ok"]})])
        self.assertEqual(out["data"], ["ok"])

    def test_all_hosts_failing_yields_none(self):
        self.assertIsNone(self._run([_http(status=500), _http(status=500)]))

    def test_exactly_two_hosts_are_tried(self):
        # 第 133 行 `for base in OKX_PUBLIC_HOSTS` —— 双域
        sess = MagicMock()
        sess.get.return_value = _http(status=500)
        with patch.object(mds, "get_market_session", return_value=sess):
            mds._public_get("/api/v5/x")
        self.assertEqual(sess.get.call_count, len(mds.OKX_PUBLIC_HOSTS))
        self.assertEqual(len(mds.OKX_PUBLIC_HOSTS), 2, "www → aws 双域")

    def test_a_transport_exception_is_swallowed_and_the_next_host_is_tried(self):
        out = self._run([OSError("no net"), _http(payload={"code": "0", "data": ["ok"]})])
        self.assertEqual(out["data"], ["ok"])

    def test_a_bad_json_body_is_swallowed(self):
        self.assertIsNone(self._run([_http(json_exc=ValueError("not json")),
                                     _http(json_exc=ValueError("not json"))]))

    def test_failures_are_instrumented_not_silent(self):
        # 第 137 刀教训：静默 except 会让"现价恒 0 / 30 小时无信号"
        with patch.object(mds, "note_failure") as nf, \
             patch.object(mds, "note_call") as nc, \
             patch.object(mds, "get_market_session", return_value=MagicMock(
                 get=MagicMock(return_value=_http(status=500)))):
            mds._public_get("/api/v5/x")
        self.assertEqual(nf.call_count, len(mds.OKX_PUBLIC_HOSTS))
        self.assertEqual(nc.call_count, len(mds.OKX_PUBLIC_HOSTS))


class PublicPostTests(unittest.TestCase):
    def _run(self, responses):
        sess = MagicMock()
        sess.post.side_effect = list(responses)
        with patch.object(mds, "get_market_session", return_value=sess):
            return mds._public_post("/api/v5/aigc/mcp/indicators", {"a": 1})

    def test_a_good_response_is_returned(self):
        out = self._run([_http(payload={"code": "0", "data": [{"x": 1}]})])
        self.assertEqual(out["data"][0]["x"], 1)

    def test_a_non_zero_business_code_falls_through(self):
        # ★ 第 172 行
        out = self._run([_http(payload={"code": "50011", "msg": "rate limited"}),
                         _http(payload={"code": "0", "data": ["ok"]})])
        self.assertEqual(out["data"], ["ok"])

    def test_an_http_error_status_falls_through(self):
        # ★ 第 173/174 行
        out = self._run([_http(status=429), _http(payload={"code": "0", "data": ["ok"]})])
        self.assertEqual(out["data"], ["ok"])

    def test_all_hosts_failing_yields_none(self):
        # ★ 第 181 行
        self.assertIsNone(self._run([_http(status=500), _http(status=500)]))

    def test_a_transport_exception_is_swallowed(self):
        out = self._run([OSError("no net"), _http(payload={"code": "0", "data": ["ok"]})])
        self.assertEqual(out["data"], ["ok"])

    def test_a_json_content_type_header_is_sent(self):
        sess = MagicMock()
        sess.post.return_value = _http(payload={"code": "0", "data": []})
        with patch.object(mds, "get_market_session", return_value=sess):
            mds._public_post("/api/v5/x", {"a": 1})
        self.assertEqual(sess.post.call_args.kwargs["headers"]["Content-Type"],
                         "application/json")


# ───────────────────── 备源序（健康感知） ─────────────────────
class AltVenueOrderTests(_Sandbox, unittest.TestCase):
    def _order(self, payload=None, *, raw=None):
        if raw is not None:
            Path(self.health).write_text(raw, encoding="utf-8")
        elif payload is not None:
            Path(self.health).write_text(json.dumps(payload), encoding="utf-8")
        return mds._alt_venue_order()

    def test_a_missing_health_file_falls_back_to_the_static_order(self):
        self.assertEqual(self._order(), mds.ALT_VENUES)

    def test_a_corrupt_health_file_falls_back_to_the_static_order(self):
        self.assertEqual(self._order(raw="{ broken"), mds.ALT_VENUES)

    def test_a_health_file_without_venues_falls_back(self):
        self.assertEqual(self._order({"other": 1}), mds.ALT_VENUES)

    def test_the_venue_with_fewer_failures_goes_first(self):
        out = self._order({"venues": {"binance": {"failed": {"a": 1, "b": 2}},
                                      "gate": {"failed": {}}}})
        self.assertEqual(out, ("gate", "binance"))

    def test_latency_only_flips_the_order_when_five_times_worse(self):
        # 「差 >5x 才翻转」防毫秒级抖动让备源序反复横跳
        same_ballpark = self._order({"venues": {
            "binance": {"failed": {}, "avg_ms": 100.0},
            "gate": {"failed": {}, "avg_ms": 400.0}}})
        self.assertEqual(same_ballpark, ("binance", "gate"), "4x 不翻转")

    def test_a_five_times_latency_gap_does_flip(self):
        flipped = self._order({"venues": {
            "binance": {"failed": {}, "avg_ms": 1000.0},
            "gate": {"failed": {}, "avg_ms": 100.0}}})
        self.assertEqual(flipped, ("gate", "binance"))

    def test_more_failures_dominate_over_latency(self):
        out = self._order({"venues": {
            "binance": {"failed": {"a": 1}, "avg_ms": 1.0},
            "gate": {"failed": {}, "avg_ms": 10_000.0}}})
        self.assertEqual(out, ("gate", "binance"), "failed 数是主排序键")

    def test_a_missing_avg_ms_is_neutral(self):
        out = self._order({"venues": {"binance": {"failed": {}},
                                      "gate": {"failed": {}}}})
        self.assertEqual(out, ("binance", "gate"), "静态原序")

    def test_a_non_numeric_avg_ms_is_neutral(self):
        out = self._order({"venues": {"binance": {"failed": {}, "avg_ms": "abc"},
                                      "gate": {"failed": {}, "avg_ms": None}}})
        self.assertEqual(out, ("binance", "gate"))

    def test_a_non_dict_venue_record_is_treated_as_empty(self):
        out = self._order({"venues": {"binance": "junk", "gate": {"failed": {}}}})
        self.assertEqual(out, ("binance", "gate"))

    def test_a_list_failed_field_is_counted(self):
        out = self._order({"venues": {"binance": {"failed": ["a", "b"]},
                                      "gate": {"failed": {}}}})
        self.assertEqual(out, ("gate", "binance"))

    def test_the_result_is_cached_for_sixty_seconds(self):
        first = self._order({"venues": {"gate": {"failed": {"a": 1}}}})
        Path(self.health).write_text(json.dumps({"venues": {
            "binance": {"failed": {"a": 1}}}}), encoding="utf-8")
        self.assertEqual(mds._alt_venue_order(), first, "60s TTL 内读内存")

    def test_the_outer_guard_returns_the_static_order_on_any_failure(self):
        # ★ 第 258/259 行 —— 热文件纪律：被生产 trader 子进程直接加载，绝不抛
        with patch.object(mds.time, "time", side_effect=RuntimeError("boom")):
            self.assertEqual(mds._alt_venue_order(), mds.ALT_VENUES)

    def test_the_return_value_is_always_a_tuple(self):
        for payload in (None, {"venues": {}}, {"venues": {"binance": {}}}):
            with self.subTest(payload=payload):
                self.assertIsInstance(self._order(payload), tuple)


class AltVenueAllowedTests(unittest.TestCase):
    def test_the_fallback_is_on_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertTrue(mds._alt_venue_allowed())

    def test_the_kill_switch_disables_it(self):
        # 离线/测试熔断开关：=0 时备源路径完全不发网络请求
        for value in ("0", "off", "OFF", "false", "False", " off "):
            with self.subTest(value=value):
                with patch.dict("os.environ", {"R20_ALT_VENUE_FALLBACK": value}):
                    self.assertFalse(mds._alt_venue_allowed())

    def test_a_truthy_value_keeps_it_on(self):
        with patch.dict("os.environ", {"R20_ALT_VENUE_FALLBACK": "1"}):
            self.assertTrue(mds._alt_venue_allowed())


class GetVenueAdapterTests(unittest.TestCase):
    def test_the_project_root_is_added_to_sys_path(self):
        # ★ 第 270–276 行 —— scripts 入口的 sys.path 引导
        root = str(Path(mds.__file__).resolve().parents[1])
        stripped = [p for p in sys.path if p != root]
        with patch.object(sys, "path", stripped), \
             patch("r20_backend.exchanges.get_adapter", return_value="ADAPTER") as ga:
            self.assertEqual(mds._get_venue_adapter("binance"), "ADAPTER")
            self.assertIn(root, sys.path)
            ga.assert_called_once_with("binance")

    def test_an_existing_root_entry_is_not_duplicated(self):
        root = str(Path(mds.__file__).resolve().parents[1])
        before = list(sys.path)
        with patch("r20_backend.exchanges.get_adapter", return_value="A"):
            mds._get_venue_adapter("gate")
        self.assertEqual(sys.path, before, "已在 path 上时不许重复插入")


# ───────────────────── 备源取数 ─────────────────────
class _FakeAdapter:
    def __init__(self, *, ticker=None, candles=None, funding=None, exc=None):
        self._ticker, self._candles, self._funding, self._exc = ticker, candles, funding, exc

    def canonical(self, inst_id):
        return inst_id.replace("-", "")

    def fetch_ticker(self, sym):
        if self._exc:
            raise self._exc
        return self._ticker

    def fetch_candles(self, sym, bar, limit):
        if self._exc:
            raise self._exc
        return self._candles

    def fetch_funding_rate(self, sym):
        if self._exc:
            raise self._exc
        return self._funding


class AltVenueTickerTests(_Sandbox, unittest.TestCase):
    def _run(self, adapter):
        with patch.dict("os.environ", {"R20_ALT_VENUE_FALLBACK": "1"}), \
             patch.object(mds, "_get_venue_adapter", return_value=adapter):
            return mds._alt_venue_ticker("BTC-USDT-SWAP")

    def test_a_good_ticker_is_normalised_to_the_okx_shape(self):
        out = self._run(_FakeAdapter(ticker={"last": 61234.5, "bid": 61230, "ask": 61240,
                                             "open_24h": 60000, "high_24h": 62000,
                                             "low_24h": 59000, "vol_24h_base": 1234.5,
                                             "ts_ms": 1700000000000}))
        self.assertEqual(out["instId"], "BTC-USDT-SWAP")
        self.assertEqual(out["last"], "61234.5")
        self.assertEqual(out["bidPx"], "61230")
        self.assertEqual(out["ts"], "1700000000000")
        self.assertIn("venue", out)

    def test_an_adapter_exception_is_treated_as_no_data(self):
        # ★ 第 286/287 行
        self.assertIsNone(self._run(_FakeAdapter(exc=RuntimeError("boom"))))

    def test_a_ticker_without_a_last_price_is_rejected(self):
        self.assertIsNone(self._run(_FakeAdapter(ticker={"bid": 1})))

    def test_a_zero_last_price_is_rejected(self):
        # `t.get("last")` 真值判据 ⇒ 0 也算"没有价格"
        self.assertIsNone(self._run(_FakeAdapter(ticker={"last": 0})))

    def test_the_kill_switch_short_circuits(self):
        with patch.dict("os.environ", {"R20_ALT_VENUE_FALLBACK": "0"}):
            self.assertIsNone(mds._alt_venue_ticker("BTC-USDT-SWAP"))

    def test_missing_optional_fields_become_empty_strings(self):
        out = self._run(_FakeAdapter(ticker={"last": 1}))
        self.assertEqual(out["bidPx"], "")
        self.assertEqual(out["askPx"], "")
        self.assertEqual(out["ts"], "")


class AltVenueCandlesTests(_Sandbox, unittest.TestCase):
    def _run(self, adapter):
        with patch.dict("os.environ", {"R20_ALT_VENUE_FALLBACK": "1"}), \
             patch.object(mds, "_get_venue_adapter", return_value=adapter):
            return mds._alt_venue_candles("BTC-USDT-SWAP", "1H", 5)

    def test_candles_are_reversed_into_the_okx_contract(self):
        # 适配器升序 → OKX 契约「最新在前」
        rows = [["1"], ["2"], ["3"], ["4"], ["5"], ["6"]]
        out = self._run(_FakeAdapter(candles=rows))
        self.assertEqual(out, [["6"], ["5"], ["4"], ["3"], ["2"]])

    def test_the_limit_is_applied_from_the_newest_end(self):
        rows = [[str(i)] for i in range(10)]
        out = self._run(_FakeAdapter(candles=rows))
        self.assertEqual(out[0], ["9"])
        self.assertEqual(len(out), 5)

    def test_an_adapter_exception_is_treated_as_no_data(self):
        # ★ 第 312/313 行
        self.assertEqual(self._run(_FakeAdapter(exc=RuntimeError("boom"))), [])

    def test_an_empty_result_falls_through(self):
        self.assertEqual(self._run(_FakeAdapter(candles=[])), [])

    def test_the_kill_switch_short_circuits(self):
        with patch.dict("os.environ", {"R20_ALT_VENUE_FALLBACK": "0"}):
            self.assertEqual(mds._alt_venue_candles("X", "1H", 5), [])


class AltVenueFundingTests(_Sandbox, unittest.TestCase):
    def _run(self, adapter):
        with patch.dict("os.environ", {"R20_ALT_VENUE_FALLBACK": "1"}), \
             patch.object(mds, "_get_venue_adapter", return_value=adapter):
            return mds._alt_funding_rate("BTC-USDT-SWAP")

    def test_the_rate_is_converted_to_a_percentage(self):
        # 对齐 OKX 路径的百分数口径：原始 0.0001 ⇒ 0.01
        self.assertEqual(self._run(_FakeAdapter(funding=0.0001)), 0.01)

    def test_an_adapter_exception_is_treated_as_no_data(self):
        # ★ 第 330/331 行
        self.assertIsNone(self._run(_FakeAdapter(exc=RuntimeError("boom"))))

    def test_a_none_rate_falls_through(self):
        self.assertIsNone(self._run(_FakeAdapter(funding=None)))

    def test_a_zero_rate_is_returned_as_zero(self):
        # `r is not None` 判据（不是真值判据）⇒ 0 是有效费率
        self.assertEqual(self._run(_FakeAdapter(funding=0.0)), 0.0)

    def test_the_kill_switch_short_circuits(self):
        # ★ 第 324/325 行
        with patch.dict("os.environ", {"R20_ALT_VENUE_FALLBACK": "0"}):
            self.assertIsNone(mds._alt_funding_rate("BTC-USDT-SWAP"))


# ───────────────────── 公开取数入口 ─────────────────────
class FetchTickersBulkTests(unittest.TestCase):
    def test_a_good_response_is_keyed_by_inst_id(self):
        with patch.object(mds, "_public_get", return_value={"data": [
                {"instId": "BTC-USDT-SWAP", "last": "1"},
                {"instId": "ETH-USDT-SWAP", "last": "2"}]}):
            out = mds.fetch_tickers_bulk()
        self.assertEqual(sorted(out), ["BTC-USDT-SWAP", "ETH-USDT-SWAP"])

    def test_a_row_without_an_inst_id_is_dropped(self):
        with patch.object(mds, "_public_get", return_value={"data": [
                {"instId": "BTC-USDT-SWAP"}, {"last": "1"}]}):
            self.assertEqual(list(mds.fetch_tickers_bulk()), ["BTC-USDT-SWAP"])

    def test_a_failure_yields_an_empty_dict(self):
        # ★ 第 354 行
        with patch.object(mds, "_public_get", return_value=None):
            self.assertEqual(mds.fetch_tickers_bulk(), {})

    def test_a_response_without_data_yields_an_empty_dict(self):
        with patch.object(mds, "_public_get", return_value={"code": "0"}):
            self.assertEqual(mds.fetch_tickers_bulk(), {})


class FetchOrderbookDepthTests(unittest.TestCase):
    def test_a_good_response_is_returned(self):
        with patch.object(mds, "_public_get", return_value={"data": [
                {"bids": [["1", "2"]], "asks": [["3", "4"]]}]}):
            self.assertEqual(mds.fetch_orderbook_depth("BTC-USDT-SWAP")["bids"], [["1", "2"]])

    def test_a_failure_yields_none(self):
        # ★ 第 368 行 —— 深度语义场所间不可比 ⇒ 无备源
        with patch.object(mds, "_public_get", return_value=None):
            self.assertIsNone(mds.fetch_orderbook_depth("BTC-USDT-SWAP"))

    def test_the_size_is_forwarded(self):
        with patch.object(mds, "_public_get", return_value={"data": [{}]}) as pg:
            mds.fetch_orderbook_depth("BTC-USDT-SWAP", sz=25)
        self.assertEqual(pg.call_args.kwargs["params"]["sz"], 25)


class LocalMathIndicatorsTests(unittest.TestCase):
    def test_no_candles_yields_an_empty_dict(self):
        # ★ 第 399/400 行
        with patch.object(mds, "fetch_candles", return_value=[]):
            self.assertEqual(mds._local_math_indicators("BTC-USDT-SWAP", ["ADX"]), {})

    def test_malformed_candles_yield_an_empty_dict(self):
        # ★ 第 407/408 行
        with patch.object(mds, "fetch_candles", return_value=[["a", "b", "c"]]):
            self.assertEqual(mds._local_math_indicators("BTC-USDT-SWAP", ["ADX"]), {})

    def test_an_index_error_in_the_candles_is_swallowed(self):
        with patch.object(mds, "fetch_candles", return_value=[["1", "2"]]):
            self.assertEqual(mds._local_math_indicators("BTC-USDT-SWAP", ["ADX"]), {})

    def test_adx_is_computed_from_enough_candles(self):
        with patch.object(mds, "fetch_candles", return_value=_candles(80)):
            out = mds._local_math_indicators("BTC-USDT-SWAP", ["ADX"])
        self.assertIn("ADX", out)
        self.assertIn("adx", out["ADX"])

    def test_too_few_candles_leaves_a_given_indicator_out(self):
        with patch.object(mds, "fetch_candles", return_value=_candles(5)):
            self.assertEqual(mds._local_math_indicators("BTC-USDT-SWAP", ["ADX"]), {})

    def test_an_unknown_indicator_is_ignored(self):
        with patch.object(mds, "fetch_candles", return_value=_candles(80)):
            self.assertEqual(mds._local_math_indicators("BTC-USDT-SWAP", ["NOPE"]), {})

    def test_a_per_indicator_failure_does_not_kill_the_others(self):
        # 第 464/465 行 —— 单个指标算错只记 debug，其余照常返回
        with patch.object(mds, "fetch_candles", return_value=_candles(80)), \
             patch.object(mds, "_indicator_key", side_effect=lambda n: "ADX"):
            out = mds._local_math_indicators("BTC-USDT-SWAP", ["ADX", "ADX"])
        self.assertIn("ADX", out)


class FetchIndicatorsBatchTests(unittest.TestCase):
    def test_the_mcp_batch_result_is_used_when_present(self):
        payload = {"data": [{"data": [{"timeframes": {"1H": {"indicators": {
            "ADX": [{"values": {"adx": "20.1"}}]}}}}]}]}
        with patch.object(mds, "_public_post", return_value=payload):
            out = mds.fetch_indicators_batch("BTC-USDT-SWAP", ["ADX"])
        self.assertEqual(out["ADX"]["adx"], "20.1")

    def test_a_malformed_mcp_body_is_swallowed(self):
        # ★ 第 501/502 行
        with patch.object(mds, "_public_post", return_value={"data": [{"junk": 1}]}), \
             patch.object(mds, "fetch_single_indicator", return_value={}), \
             patch.object(mds, "_local_math_indicators", return_value={}):
            self.assertEqual(mds.fetch_indicators_batch("BTC-USDT-SWAP", ["ADX"]), {})

    def test_an_mcp_data_entry_without_nested_data_is_swallowed(self):
        with patch.object(mds, "_public_post", return_value={"data": [{}]}), \
             patch.object(mds, "fetch_single_indicator", return_value={}), \
             patch.object(mds, "_local_math_indicators", return_value={}):
            self.assertEqual(mds.fetch_indicators_batch("BTC-USDT-SWAP", ["ADX"]), {})

    def test_an_empty_nested_data_list_is_swallowed(self):
        # ★ 第 501/502 行 —— `{"data": [{"data": []}]}` 时内层 `[0]` 抛 IndexError
        with patch.object(mds, "_public_post", return_value={"data": [{"data": []}]}), \
             patch.object(mds, "fetch_single_indicator", return_value={}), \
             patch.object(mds, "_local_math_indicators", return_value={}):
            self.assertEqual(mds.fetch_indicators_batch("BTC-USDT-SWAP", ["ADX"]), {})

    def test_a_non_dict_timeframes_value_is_swallowed(self):
        with patch.object(mds, "_public_post", return_value={
                "data": [{"data": [{"timeframes": "junk"}]}]}), \
             patch.object(mds, "fetch_single_indicator", return_value={}), \
             patch.object(mds, "_local_math_indicators", return_value={}):
            self.assertEqual(mds.fetch_indicators_batch("BTC-USDT-SWAP", ["ADX"]), {})

    def test_the_rest_fallback_is_used_per_indicator(self):
        with patch.object(mds, "_public_post", return_value=None), \
             patch.object(mds, "fetch_single_indicator",
                          return_value={"adx": "1.0"}) as fs, \
             patch.object(mds, "_local_math_indicators", return_value={}):
            out = mds.fetch_indicators_batch("BTC-USDT-SWAP", ["ADX", "KDJ"])
        self.assertEqual(fs.call_count, 2)
        self.assertEqual(out["ADX"]["adx"], "1.0")

    def test_the_local_math_is_the_last_resort(self):
        # ★ 第 513–516 行
        with patch.object(mds, "_public_post", return_value=None), \
             patch.object(mds, "fetch_single_indicator", return_value={}), \
             patch.object(mds, "_local_math_indicators",
                          return_value={"ADX": {"adx": "9.9"}}) as lm:
            out = mds.fetch_indicators_batch("BTC-USDT-SWAP", ["ADX"])
        self.assertEqual(out["ADX"]["adx"], "9.9")
        self.assertEqual(lm.call_args.args[1], ["ADX"], "只补缺失的指标")

    def test_the_local_math_result_never_overwrites_a_good_value(self):
        # `result.setdefault` ⇒ 已有值优先
        with patch.object(mds, "_public_post", return_value=None), \
             patch.object(mds, "fetch_single_indicator",
                          return_value={"adx": "from-rest"}), \
             patch.object(mds, "_local_math_indicators",
                          return_value={"ADX": {"adx": "from-math"}}):
            out = mds.fetch_indicators_batch("BTC-USDT-SWAP", ["ADX"])
        self.assertEqual(out["ADX"]["adx"], "from-rest")

    def test_no_missing_indicators_skips_the_local_math(self):
        payload = {"data": [{"data": [{"timeframes": {"1H": {"indicators": {
            "ADX": [{"values": {"adx": "1"}}]}}}}]}]}
        with patch.object(mds, "_public_post", return_value=payload), \
             patch.object(mds, "_local_math_indicators") as lm:
            mds.fetch_indicators_batch("BTC-USDT-SWAP", ["ADX"])
        lm.assert_not_called()


class FetchSingleIndicatorTests(unittest.TestCase):
    def test_a_good_mcp_response_is_returned(self):
        payload = {"data": [{"data": [{"timeframes": {"1H": {"indicators": {
            "ADX": [{"values": {"adx": "33.3"}}]}}}}]}]}
        with patch.object(mds, "_public_post", return_value=payload):
            self.assertEqual(mds.fetch_single_indicator("BTC-USDT-SWAP", "ADX"),
                             {"adx": "33.3"})

    def test_a_malformed_body_falls_through_to_local_math(self):
        # ★ 第 537–543 行
        with patch.object(mds, "_public_post", return_value={"data": [{"junk": 1}]}), \
             patch.object(mds, "_local_math_indicators",
                          return_value={"ADX": {"adx": "7"}}):
            self.assertEqual(mds.fetch_single_indicator("BTC-USDT-SWAP", "ADX"),
                             {"adx": "7"})

    def test_a_missing_data_key_falls_through(self):
        with patch.object(mds, "_public_post", return_value=None), \
             patch.object(mds, "_local_math_indicators", return_value={}):
            self.assertEqual(mds.fetch_single_indicator("BTC-USDT-SWAP", "ADX"), {})

    def test_an_empty_nested_data_list_is_swallowed(self):
        # ★ 第 542/543 行
        with patch.object(mds, "_public_post", return_value={"data": [{"data": []}]}), \
             patch.object(mds, "_local_math_indicators", return_value={}):
            self.assertEqual(mds.fetch_single_indicator("BTC-USDT-SWAP", "ADX"), {})

    def test_an_empty_items_list_falls_through(self):
        payload = {"data": [{"data": [{"timeframes": {"1H": {"indicators": {"ADX": []}}}}]}]}
        with patch.object(mds, "_public_post", return_value=payload), \
             patch.object(mds, "_local_math_indicators", return_value={}):
            self.assertEqual(mds.fetch_single_indicator("BTC-USDT-SWAP", "ADX"), {})

    def test_the_indicator_key_is_normalised_in_the_payload(self):
        with patch.object(mds, "_public_post", return_value=None) as pp, \
             patch.object(mds, "_local_math_indicators", return_value={}):
            mds.fetch_single_indicator("BTC-USDT-SWAP", "ema-20")
        self.assertIn("EMA20", pp.call_args.args[1]["indicators"])


class FetchCandlesTests(_Sandbox, unittest.TestCase):
    def test_a_good_response_is_returned(self):
        with patch.object(mds, "_public_get", return_value={"data": [["1"]]}):
            self.assertEqual(mds.fetch_candles("BTC-USDT-SWAP"), [["1"]])

    def test_a_failure_falls_through_to_the_alt_venues(self):
        with patch.object(mds, "_public_get", return_value=None), \
             patch.object(mds, "_alt_venue_candles", return_value=[["alt"]]) as av:
            self.assertEqual(mds.fetch_candles("BTC-USDT-SWAP"), [["alt"]])
        self.assertTrue(av.called)

    def test_a_non_numeric_limit_falls_back_to_forty_five(self):
        # ★ 第 562/563 行
        with patch.object(mds, "_public_get", return_value={"data": []}) as pg:
            mds.fetch_candles("BTC-USDT-SWAP", limit="abc")
        self.assertEqual(pg.call_args.kwargs["params"]["limit"], 45)

    def test_the_limit_is_clamped_to_the_okx_ceiling(self):
        with patch.object(mds, "_public_get", return_value={"data": []}) as pg:
            mds.fetch_candles("BTC-USDT-SWAP", limit=10_000)
        self.assertEqual(pg.call_args.kwargs["params"]["limit"], 300)

    def test_the_limit_is_clamped_to_at_least_one(self):
        with patch.object(mds, "_public_get", return_value={"data": []}) as pg:
            mds.fetch_candles("BTC-USDT-SWAP", limit=0)
        self.assertEqual(pg.call_args.kwargs["params"]["limit"], 1)

    def test_the_bar_is_normalised(self):
        with patch.object(mds, "_public_get", return_value={"data": []}) as pg:
            mds.fetch_candles("BTC-USDT-SWAP", bar="1h")
        self.assertEqual(pg.call_args.kwargs["params"]["bar"], "1H")


class FetchFundingRateTests(_Sandbox, unittest.TestCase):
    def test_a_good_rate_is_converted_to_a_percentage(self):
        with patch.object(mds, "_public_get", return_value={
                "data": [{"fundingRate": "0.0002"}]}):
            self.assertEqual(mds.fetch_funding_rate("BTC-USDT-SWAP"), 0.02)

    def test_an_unparsable_rate_falls_through_to_the_alt_venues(self):
        # ★ 第 585/586 行
        with patch.object(mds, "_public_get", return_value={
                "data": [{"fundingRate": "not-a-number"}]}), \
             patch.object(mds, "_alt_funding_rate", return_value=0.5) as af:
            self.assertEqual(mds.fetch_funding_rate("BTC-USDT-SWAP"), 0.5)
        self.assertTrue(af.called)

    def test_a_missing_rate_field_silently_becomes_zero(self):
        # ⚠️ 实测行为（本刀仅记录，**未改**）：第 584 行是
        #   `float(data["data"][0].get("fundingRate", 0.0)) * 100` —— 用 `.get(..., 0.0)`
        #   兜底 ⇒ **字段缺失 = 费率恰好 0%**，而不是"没有数据"。
        #   于是备源（`_alt_funding_rate`）**不会**被调用，调用方拿到一个看起来
        #   完全正常的 `0.0`。这属「缺失 ≠ 0」家族：OKX 返回了空对象本该落空/落备源，
        #   现在却报了一个确定的费率。
        with patch.object(mds, "_public_get", return_value={"data": [{}]}), \
             patch.object(mds, "_alt_funding_rate", return_value=0.5) as af:
            out = mds.fetch_funding_rate("BTC-USDT-SWAP")
        self.assertEqual(out, 0.0, "缺失字段被兜底成 0.0")
        self.assertFalse(af.called, "正因为没落空，备源拿不到机会")

    def test_an_explicit_null_rate_falls_through(self):
        # 对照：显式 `null` 会走 `float(None)` → TypeError → 落备源
        with patch.object(mds, "_public_get", return_value={
                "data": [{"fundingRate": None}]}), \
             patch.object(mds, "_alt_funding_rate", return_value=0.5):
            self.assertEqual(mds.fetch_funding_rate("BTC-USDT-SWAP"), 0.5)

    def test_a_failure_falls_through(self):
        with patch.object(mds, "_public_get", return_value=None), \
             patch.object(mds, "_alt_funding_rate", return_value=1.0):
            self.assertEqual(mds.fetch_funding_rate("BTC-USDT-SWAP"), 1.0)


class FetchOpenInterestTests(unittest.TestCase):
    def test_a_good_response_is_returned(self):
        # ★ 第 592–595 行 —— 整块此前从未执行
        with patch.object(mds, "_public_get", return_value={
                "data": [{"oi": "12345", "oiCcy": "1"}]}):
            out = mds.fetch_open_interest("BTC-USDT-SWAP")
        self.assertEqual(out["oi"], "12345")

    def test_a_failure_yields_none(self):
        with patch.object(mds, "_public_get", return_value=None):
            self.assertIsNone(mds.fetch_open_interest("BTC-USDT-SWAP"))

    def test_an_empty_data_list_yields_none(self):
        with patch.object(mds, "_public_get", return_value={"data": []}):
            self.assertIsNone(mds.fetch_open_interest("BTC-USDT-SWAP"))

    def test_no_alt_venue_fallback_exists_for_open_interest(self):
        # 语义场所间不可比 —— 与 depth 同款纪律
        with patch.object(mds, "_public_get", return_value=None), \
             patch.object(mds, "_alt_venue_ticker") as at:
            mds.fetch_open_interest("BTC-USDT-SWAP")
        at.assert_not_called()


if __name__ == "__main__":
    unittest.main()

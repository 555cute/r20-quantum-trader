"""单标的数据包装配（brain/packages.py）收口 —— 第 306 刀。

本模块是「抽取时零测试覆盖」的那一块（模块 docstring 自己写着：`grep` 双向实测均无引用）。
它每 15 分钟对每个标的跑一次，把一个标的的行情/指标/聪明钱装配成一个字典，
**其中一个字段错了会静默影响模型决策**。本刀按三类钉：

1. **默认包形状** —— 缺省值不是随手写的，`smart_money.reason` 是**诚实的不可用说明**，
   `data_quality` 默认 `invalid`（宁可说无效，也不默认有效）。
2. **七个数据源各自坏掉不影响别人** —— ticker / 15M / 1H / 4H / 资金费 / OI / 多空比 / taker
   各自独立 `try`，坏了只 `note_failure` 并把该块留在缺省，**其余成果原样保留**。
3. **两条"提升"语义** —— 1H ATR **覆盖** 15M ATR（`pkg["atr"]` 被改写），
   而 `recent_*` 保持**新→旧**（正好喂给会翻转的 `calculate_multi_timeframe`）。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.brain import packages as bp  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


TICKER = {"code": "0", "data": [{"last": "100.0", "bidPx": "99.5", "askPx": "100.5",
                                 "open24h": "95.0", "vol24h": "12345.678"}]}
FUNDING = {"code": "0", "data": [{"fundingRate": "0.00012"}]}
OPEN_INTEREST = {"code": "0", "data": [{"oiUsd": "123456789.0"}]}
LS_RATIO = {"code": "0", "data": [["1700000000000", "1.85"]]}
TAKER = {"code": "0", "data": [["1700000000000", "50000", "40000"]]}


def _candles(n, *, start=100.0, step=1.0, vol=10.0, newest_first=True):
    """OKX 形态 `[ts, open, high, low, close, vol]`，`step > 0` 表示**时间正序上涨**。

    ⚠️ 顺序是这里的语义核心：先按正序造（时间戳递增、`step>0` 即上涨），
    再整体翻成**新→旧**（默认），才与 OKX 真实回报一致。
    被 `recent_*` 原样保留、又被 `reversed()` 还原成正序 —— 所以夹具必须两端都对。
    """
    rows = []
    for i in range(n):
        close = start + i * step
        rows.append([1_700_000_000_000 + i * 60_000, close - 0.5, close + 1.0,
                     close - 1.0, close, vol])
    return list(reversed(rows)) if newest_first else rows


class _Base(unittest.TestCase):
    def setUp(self):
        self.calls: list[str] = []
        self.failures: list[tuple] = []
        self.printed: list[str] = []
        self.candle_routes: dict = {}
        self.indicator_calls: list = []
        self.indicator_result = {"adx": 27.5}
        for name, value in (
            ("note_failure", lambda src, exc: self.failures.append((src, str(exc)))),
            ("print", lambda *a, **k: self.printed.append(" ".join(str(x) for x in a))),
        ):
            p = patch.object(bp, name, value)
            p.start()
            self.addCleanup(p.stop)

    def _item(self, **over):
        item = {"instId": "BTC-USDT-SWAP", "name": "BTC", "type": "crypto",
                "precision": 2, "ccy": "BTC"}
        item.update(over)
        return item

    def _fetch_candles(self, inst_id, bar="15m", limit=45):
        route = self.candle_routes.get(bar)
        if isinstance(route, Exception):
            raise route
        return route if route is not None else []

    def _fetch_indicator(self, inst_id, name, bar="1H"):
        self.indicator_calls.append((inst_id, name, bar))
        if isinstance(self.indicator_result, Exception):
            raise self.indicator_result
        return self.indicator_result

    def _run(self, *, http=None, item=None, candles=None, full=True):
        http = http if http is not None else {}
        routes = {"/market/ticker": TICKER, "/public/funding-rate": FUNDING,
                  "/public/open-interest": OPEN_INTEREST,
                  "long-short-account-ratio": LS_RATIO, "taker-volume": TAKER}
        routes.update(http)
        self.candle_routes = candles if candles is not None else (
            {"15m": _candles(24), "1H": _candles(24, step=2.0),
             "4H": _candles(16, step=4.0)} if full else {})

        def opener(req, timeout=None):
            url = req.full_url
            self.calls.append(url)
            for key, value in routes.items():
                if key in url:
                    if isinstance(value, Exception):
                        raise value
                    return _Resp(value)
            raise AssertionError(f"未路由的 URL: {url}")

        with patch.object(bp.urllib.request, "urlopen", opener):
            return bp.fetch_single_instrument_package(
                item or self._item(),
                fetch_candles=self._fetch_candles,
                fetch_single_indicator=self._fetch_indicator)


class DefaultShapeTests(_Base, unittest.TestCase):
    """HTTP 与行情全部拿不到时，包仍必须是一个**形状完整**的字典。"""

    def test_all_sources_down_still_returns_full_shape(self):
        pkg = self._run(http={k: OSError("net down") for k in
                              ("/market/ticker", "/public/funding-rate",
                               "/public/open-interest", "long-short-account-ratio",
                               "taker-volume")},
                        candles={"15m": OSError("x"), "1H": OSError("x"), "4H": OSError("x")})
        self.assertEqual(pkg["instId"], "BTC-USDT-SWAP")
        self.assertEqual(pkg["price"], 0.0)
        self.assertEqual(pkg["rsi"], 50.0)               # 中性，不是 0
        self.assertEqual(pkg["vol_ratio"], 1.0)          # 中性
        self.assertEqual(pkg["obv_flow"], "NEUTRAL")
        self.assertEqual(pkg["lsRatio"], "N/A")
        self.assertEqual(pkg["takerNetUsd"], "N/A")
        self.assertEqual(pkg["oiUsd"], "N/A")
        self.assertEqual(pkg["recent_15m"], [])
        self.assertEqual(pkg["data_quality"], "invalid")

    def test_smart_money_default_is_an_honest_unavailable_note(self):
        pkg = self._run()
        sm = pkg["smart_money"]
        self.assertFalse(sm["available"])
        # 缺省理由必须说清"为什么没有"，而不是留空或写 0
        self.assertIn("OKX CLI 已移除", sm["reason"])
        for key in ("weighted_long_pct", "net_flow_usdt", "avg_long_entry",
                    "avg_short_entry", "top_win_rate"):
            self.assertEqual(sm[key], "--", key)

    def test_calculus_default_is_explicitly_unreliable(self):
        with patch.dict(sys.modules, {"calculus_engine": None}):
            pkg = self._run()
        self.assertFalse(pkg["calculus"]["valid"])
        self.assertEqual(pkg["calculus"]["regime"], "DATA_UNRELIABLE")
        self.assertIn("error", pkg["calculus"])

    def test_missing_required_item_fields_raise(self):
        for missing in ("instId", "name", "type", "precision"):
            item = self._item()
            item.pop(missing)
            with self.assertRaises(KeyError, msg=missing):
                self._run(item=item)


class TickerTests(_Base, unittest.TestCase):
    def test_ticker_populates_price_and_spread(self):
        pkg = self._run()
        self.assertEqual(pkg["price"], 100.0)
        self.assertEqual(pkg["bidPx"], 99.5)
        self.assertEqual(pkg["askPx"], 100.5)
        self.assertEqual(pkg["chg24h"], 5.26)            # (100-95)/95*100
        self.assertEqual(pkg["vol24h"], 12345.68)
        self.assertGreaterEqual(pkg["okx_latency_ms"], 1)

    def test_zero_open24h_yields_zero_change_not_division_error(self):
        pkg = self._run(http={"/market/ticker": {"code": "0", "data": [
            {"last": "100", "bidPx": "99", "askPx": "101", "open24h": "0", "vol24h": "1"}]}})
        self.assertEqual(pkg["chg24h"], 0.0)

    def test_missing_bid_ask_fall_back_to_last_price(self):
        pkg = self._run(http={"/market/ticker": {"code": "0", "data": [
            {"last": "77.0", "open24h": "70", "vol24h": "1"}]}})
        self.assertEqual(pkg["bidPx"], 77.0)
        self.assertEqual(pkg["askPx"], 77.0)

    def test_empty_data_leaves_defaults_and_does_not_note_failure(self):
        pkg = self._run(http={"/market/ticker": {"code": "0", "data": []}})
        self.assertEqual(pkg["price"], 0.0)
        self.assertEqual(self.failures, [])

    def test_nonzero_code_is_not_parsed(self):
        pkg = self._run(http={"/market/ticker": {"code": "51001", "data": [{"last": "1"}]}})
        self.assertEqual(pkg["price"], 0.0)

    def test_ticker_network_error_is_noted_but_others_survive(self):
        pkg = self._run(http={"/market/ticker": OSError("boom")})
        self.assertEqual([f[0] for f in self.failures], ["okx_ticker"])
        self.assertTrue(pkg["recent_15m"])               # 其余数据源照常
        self.assertEqual(pkg["adx_1h"], 27.5)


class MicrostructureTests(_Base, unittest.TestCase):
    def test_recent_candles_keep_newest_first_and_five_fields(self):
        pkg = self._run()
        rows = pkg["recent_15m"]
        self.assertEqual(len(rows), 12)                  # 只留 12 根
        self.assertEqual(len(rows[0]), 5)                # [open, high, low, close, vol]
        # ★ 保持**新→旧**（时间戳递减）—— 正好喂给会自行翻转的 calculate_multi_timeframe；
        #   同接 `recent_15m[0]` 是**最新**那根（收盘价最高，因为夹具是上涨序列）
        # ★ 字段映射：raw 是 `[ts, open, high, low, close, vol]`，
        #   装进包里变成 `[open, high, low, close, vol]` —— 索引 3 是 **close**，
        #   而 raw 的索引 3 是 **low**（本刀我自己就在这里读错过一次）
        raw_newest = _candles(24)[0]
        self.assertEqual(rows[0], [raw_newest[1], raw_newest[2], raw_newest[3],
                                   raw_newest[4], round(raw_newest[5], 1)])
        self.assertGreater(rows[0][1], rows[1][1])       # high 随新→旧递减
        self.assertGreater(rows[0][3], rows[-1][3])      # 最新那根收盘最高
        self.assertEqual(len(pkg["recent_4h"]), 8)       # 4H 只留 8 根

    def test_indicators_require_fifteen_rows(self):
        pkg = self._run(candles={"15m": _candles(14), "1H": _candles(24, step=2.0),
                                 "4H": _candles(16, step=4.0)})
        self.assertNotIn("atr_15m", pkg)
        self.assertEqual(pkg["rsi"], 50.0)               # 保持缺省
        self.assertEqual(pkg["atr"], pkg.get("atr_1h"))  # 只有 1H 提升过

    def test_atr_rsi_vwap_volratio_computed_on_trend_series(self):
        pkg = self._run()
        self.assertGreater(pkg["atr_15m"], 0)
        # 单调上涨 ⇒ 平均跌幅为 0 ⇒ 源码把 rs 封顶在 100.0 ⇒ RSI = 100 - 100/101 = 99.0
        # （**不是** 100.0 —— 这个封顶正是"永不给出满分"的保守设计）
        self.assertEqual(pkg["rsi"], 99.0)
        self.assertEqual(pkg["rsi_15m"], pkg["rsi"])
        self.assertEqual(pkg["rsi_1h"], 99.0)
        # ★ 跨源比较：VWAP 用**K线**算（均值≈111.5），而现价来自 **ticker**（100.0）
        #   ⇒ 这里必然是负的。钉住"它比的是两个不同来源"，不是同源自洽
        self.assertLess(pkg["vwap_bias"], 0)
        self.assertEqual(pkg["vol_ratio"], 1.0)          # 量恒定

    def test_vwap_bias_skipped_when_total_volume_is_zero(self):
        pkg = self._run(candles={"15m": _candles(24, vol=0.0),
                                 "1H": _candles(24, step=2.0), "4H": _candles(16, step=4.0)})
        self.assertEqual(pkg["vwap_bias"], 0.0)

    def test_obv_flow_three_states(self):
        up = self._run(candles={"15m": _candles(24, step=1.0), "1H": _candles(24),
                                "4H": _candles(16)})
        self.assertEqual(up["obv_flow"], "BULL_FLOW")
        down = self._run(candles={"15m": _candles(24, step=-1.0), "1H": _candles(24),
                                  "4H": _candles(16)})
        self.assertEqual(down["obv_flow"], "BEAR_FLOW")
        flat = self._run(candles={"15m": _candles(24, step=0.0), "1H": _candles(24),
                                  "4H": _candles(16)})
        self.assertEqual(flat["obv_flow"], "NEUTRAL")

    def test_empty_candle_list_degrades_with_a_visible_warning(self):
        pkg = self._run(candles={"15m": [], "1H": [], "4H": []})
        self.assertEqual(pkg["recent_15m"], [])
        self.assertTrue(any("15m K线获取失败" in line for line in self.printed))
        self.assertTrue(any("1H K线获取失败" in line for line in self.printed))
        self.assertTrue(any("4H K线获取失败" in line for line in self.printed))

    def test_candle_exception_is_printed_not_raised(self):
        pkg = self._run(candles={"15m": RuntimeError("bad"), "1H": RuntimeError("bad"),
                                 "4H": RuntimeError("bad")})
        self.assertTrue(any("15m K线处理异常" in line for line in self.printed))
        self.assertEqual(pkg["data_quality"], "invalid")


class AtrElevationTests(_Base, unittest.TestCase):
    """★ 1H ATR **覆盖** 15M ATR —— `pkg["atr"]` 是被改写的同一个键。"""

    def test_one_hour_atr_overwrites_the_fifteen_minute_one(self):
        pkg = self._run()
        self.assertIn("atr_15m", pkg)
        self.assertIn("atr_1h", pkg)
        self.assertNotEqual(pkg["atr_15m"], pkg["atr_1h"])
        self.assertEqual(pkg["atr"], pkg["atr_1h"])       # 1H 赢

    def test_only_15m_available_keeps_the_15m_value(self):
        pkg = self._run(candles={"15m": _candles(24), "1H": [], "4H": []})
        self.assertEqual(pkg["atr"], pkg["atr_15m"])
        self.assertNotIn("atr_1h", pkg)

    def test_neither_available_leaves_default_zero(self):
        pkg = self._run(candles={"15m": [], "1H": [], "4H": []})
        self.assertEqual(pkg["atr"], 0.0)


class StructureTests(_Base, unittest.TestCase):
    def _structure(self, h1_step):
        return self._run(candles={"15m": _candles(24), "1H": _candles(24, step=h1_step),
                                  "4H": _candles(16)})["structure_1h"]

    def test_one_hour_swing_structure_three_states(self):
        self.assertEqual(self._structure(1.0), "1H_SWING_BULL")
        self.assertEqual(self._structure(-1.0), "1H_SWING_BEAR")
        # 横盘 ⇒ 三个均线相等 ⇒ `>` 与 `<` 两个条件**都不成立** ⇒ 落到 CHOP
        self.assertEqual(self._structure(0.0), "1H_SWING_CHOP")

    def test_one_hour_swing_structure_covers_the_bounce_case(self):
        # 长跌之后一根急弹：末值 > ma7，但 ma7 < ma20 ⇒ 既非 BULL 也非 BEAR ⇒ CHOP
        rows = _candles(24, step=-1.0, newest_first=False)
        rows.append([1_700_000_000_000 + 24 * 60_000, 89.0, 91.0, 88.0, 90.0, 10.0])
        pkg = self._run(candles={"15m": _candles(24), "1H": list(reversed(rows)),
                                 "4H": _candles(16)})
        self.assertEqual(pkg["structure_1h"], "1H_SWING_CHOP")

    def test_four_hour_macro_structure_three_states(self):
        def macro(step):
            return self._run(candles={"15m": _candles(24), "1H": _candles(24),
                                      "4H": _candles(16, step=step)})["macro_4h"]
        self.assertEqual(macro(1.0), "4H_MACRO_BULL (大级别多头通道)")
        self.assertEqual(macro(-1.0), "4H_MACRO_BEAR (大级别空头承压)")
        self.assertEqual(macro(0.0), "4H_MACRO_RANGE (大级别区间震荡)")

    def test_structure_needs_eight_four_hour_rows(self):
        pkg = self._run(candles={"15m": _candles(24), "1H": _candles(24),
                                 "4H": _candles(7)})
        self.assertNotIn("macro_4h", pkg)


class CryptoOnlySourceTests(_Base, unittest.TestCase):
    """资金费 / OI / 多空比 / taker / ADX **只在 crypto 类别**才取。"""

    def test_non_crypto_skips_all_swap_specific_sources(self):
        pkg = self._run(item=self._item(type="index"))
        urls = " ".join(self.calls)
        for fragment in ("funding-rate", "open-interest", "long-short-account-ratio",
                         "taker-volume"):
            self.assertNotIn(fragment, urls)
        self.assertEqual(pkg["fundingRate"], 0.0)
        self.assertEqual(pkg["oiUsd"], "N/A")
        # ★ `adx_1h` **本来就在默认包里**（默认值 0.0）—— 所以"键不在"根本不是判据；
        #   真判据是**没有去调用**指标接口，且值停在默认
        self.assertEqual(self.indicator_calls, [])
        self.assertEqual(pkg["adx_1h"], 0.0)

    def test_crypto_queries_all_swap_specific_sources(self):
        pkg = self._run()
        urls = " ".join(self.calls)
        for fragment in ("funding-rate", "open-interest", "long-short-account-ratio",
                         "taker-volume"):
            self.assertIn(fragment, urls)
        self.assertEqual(pkg["fundingRate"], 0.012)      # 0.00012 * 100
        self.assertEqual(pkg["lsRatio"], 1.85)
        self.assertEqual(pkg["adx_1h"], 27.5)
        self.assertEqual(self.indicator_calls, [("BTC-USDT-SWAP", "ADX", "1H")])

    def test_oi_formatting_switches_at_one_hundred_million(self):
        big = self._run(http={"/public/open-interest": {"code": "0", "data": [
            {"oiUsd": "250000000.0"}]}})
        self.assertEqual(big["oiUsd"], "2.5亿 U")
        small = self._run(http={"/public/open-interest": {"code": "0", "data": [
            {"oiUsd": "45000.0"}]}})
        self.assertEqual(small["oiUsd"], "4.5万 U")

    def test_taker_net_is_formatted_in_ten_thousands(self):
        pkg = self._run()
        self.assertEqual(pkg["takerNetUsd"], "1.0万 U")  # (50000-40000)/1e4

    def test_no_ccy_skips_rubik_queries(self):
        pkg = self._run(item=self._item(ccy=""))
        urls = " ".join(self.calls)
        self.assertNotIn("long-short-account-ratio", urls)
        self.assertNotIn("taker-volume", urls)
        self.assertEqual(pkg["lsRatio"], "N/A")

    def test_each_swap_source_failure_is_isolated(self):
        pkg = self._run(http={"/public/funding-rate": OSError("f"),
                              "/public/open-interest": OSError("o"),
                              "long-short-account-ratio": OSError("l"),
                              "taker-volume": OSError("t")})
        self.assertEqual(sorted(f[0] for f in self.failures),
                         ["okx_funding_rate", "okx_ls_ratio", "okx_open_interest",
                          "okx_taker_volume"])
        self.assertEqual(pkg["adx_1h"], 27.5)            # ADX 不受影响
        self.assertTrue(pkg["recent_15m"])               # K线不受影响

    def test_adx_requires_a_non_empty_payload_with_the_key(self):
        # 默认包里 adx_1h 恒为 0.0 ⇒ 判据是"值有没有被**覆盖**"，不是键在不在
        self.indicator_result = {}
        self.assertEqual(self._run()["adx_1h"], 0.0)
        self.indicator_result = {"other": 1}
        self.assertEqual(self._run()["adx_1h"], 0.0)

    def test_adx_none_value_falls_back_to_zero(self):
        self.indicator_result = {"adx": None}
        self.assertEqual(self._run()["adx_1h"], 0.0)

    def test_adx_failure_is_noted(self):
        self.indicator_result = RuntimeError("adx down")
        self._run()
        self.assertEqual([f[0] for f in self.failures], ["okx_adx_1h"])

    def test_bad_data_shapes_are_noted_not_raised(self):
        pkg = self._run(http={"/market/ticker": {"code": "0", "data": [{"last": "abc"}]}})
        self.assertEqual([f[0] for f in self.failures], ["okx_ticker"])
        self.assertEqual(pkg["price"], 0.0)


class DataQualityTests(_Base, unittest.TestCase):
    """`data_quality` 是模型据以判断可信度的开关：六条判据缺一即为 invalid。"""

    def test_full_data_is_valid(self):
        pkg = self._run()
        self.assertEqual(pkg["data_quality"], "valid")
        self.assertGreater(pkg["price"], 0)
        self.assertGreaterEqual(len(pkg["recent_15m"]), 12)
        self.assertGreaterEqual(len(pkg["recent_1h"]), 8)
        self.assertGreaterEqual(len(pkg["recent_4h"]), 6)

    def test_missing_price_makes_it_invalid(self):
        pkg = self._run(http={"/market/ticker": OSError("x")})
        self.assertEqual(pkg["data_quality"], "invalid")

    def test_ask_below_bid_makes_it_invalid(self):
        pkg = self._run(http={"/market/ticker": {"code": "0", "data": [
            {"last": "100", "bidPx": "101", "askPx": "99", "open24h": "95",
             "vol24h": "1"}]}})
        self.assertEqual(pkg["data_quality"], "invalid")

    def test_too_few_rows_makes_it_invalid(self):
        pkg = self._run(candles={"15m": _candles(11), "1H": _candles(24),
                                 "4H": _candles(16)})
        self.assertEqual(pkg["data_quality"], "invalid")

    def test_empty_everything_is_invalid_but_still_returns(self):
        pkg = self._run(candles={"15m": [], "1H": [], "4H": []},
                        http={"/market/ticker": OSError("x")})
        self.assertEqual(pkg["data_quality"], "invalid")
        self.assertIn("instId", pkg)


class CalculusIntegrationTests(_Base, unittest.TestCase):
    def test_calculus_receives_newest_first_rows_under_tf_keys(self):
        import calculus_engine
        seen = {}

        def fake(mapping):
            seen.update(mapping)
            return {"valid": True, "regime": "BULL_STABLE"}

        with patch.object(calculus_engine, "calculate_multi_timeframe", fake):
            pkg = self._run()
        self.assertEqual(sorted(seen), ["15M", "1H", "4H"])
        # ★ 传进去的就是原样的 new→旧 列表（本函数**不翻转**，翻转是 calculus 自己的事）
        self.assertEqual(seen["15M"], pkg["recent_15m"])
        self.assertEqual(pkg["calculus"]["regime"], "BULL_STABLE")

    def test_calculus_exception_is_captured_with_the_message(self):
        import calculus_engine

        def boom(mapping):
            raise RuntimeError("math exploded")

        with patch.object(calculus_engine, "calculate_multi_timeframe", boom):
            pkg = self._run()
        self.assertFalse(pkg["calculus"]["valid"])
        self.assertEqual(pkg["calculus"]["regime"], "DATA_UNRELIABLE")
        self.assertIn("math exploded", pkg["calculus"]["error"])

    def test_import_failure_of_calculus_engine_is_also_captured(self):
        with patch.dict(sys.modules, {"calculus_engine": None}):
            pkg = self._run()
        self.assertFalse(pkg["calculus"]["valid"])
        self.assertEqual(pkg["calculus"]["quality"], 0.0)


class InjectionTests(_Base, unittest.TestCase):
    """两个行情函数是**注入**的，本模块不许自己 import 门面（模块 docstring 的硬约束）。"""

    def test_injected_fetchers_are_actually_used(self):
        seen = {"candles": [], "indicator": []}
        orig = self._fetch_candles

        def spy_candles(inst_id, bar="15m", limit=45):
            seen["candles"].append(bar)
            return orig(inst_id, bar=bar, limit=limit)

        def spy_indicator(inst_id, name, bar="1H"):
            seen["indicator"].append((inst_id, name, bar))
            return self.indicator_result

        self.candle_routes = {"15m": _candles(24), "1H": _candles(24), "4H": _candles(16)}

        def opener(req, timeout=None):
            url = req.full_url
            for key, value in {"/market/ticker": TICKER}.items():
                if key in url:
                    return _Resp(value)
            return _Resp({"code": "0", "data": []})

        with patch.object(bp.urllib.request, "urlopen", opener):
            bp.fetch_single_instrument_package(self._item(), fetch_candles=spy_candles,
                                               fetch_single_indicator=spy_indicator)
        self.assertEqual(seen["candles"], ["15m", "1H", "4H"])
        self.assertEqual(seen["indicator"], [("BTC-USDT-SWAP", "ADX", "1H")])

    def test_module_does_not_import_the_facade(self):
        source = Path(bp.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import ai_brain_trader", source)
        self.assertNotIn("from ai_brain_trader", source)


if __name__ == "__main__":
    unittest.main()

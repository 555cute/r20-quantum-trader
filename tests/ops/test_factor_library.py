"""五因子库引擎：外部数据源容灾与智能资金覆盖（第二百九十九刀，开新面 factor_library.py）。

先打印整个文件（315 行）再动笔。它是**每分钟一轮**的因子引擎，也是 `job_runs` 表
增长的主要来源。原有测试只覆盖了「正常取数 → 算分」的主路径（59.2%）。

| 语义 | 口径 |
|---|---|
| ★ **每一个外部数据源都必须"坏了不影响别人"** | 全文有 **7 处**独立的 `except: pass` / `except: print`：ticker、orderbook depth、15m K线、1H K线、官方指标批、资金费率、持仓量、Rubik 两个接口。任何一处返回垃圾/抛异常，都只能**丢掉自己那一块**，前面算好的字段必须原样保留 |
| ★ **深度失衡用 0.67 / 1.5 两条门槛** | `bid/ask ≥ 1.5` ⇒ `STRONG_BID`；`≤ 0.67` ⇒ `STRONG_ASK`；中间**不写** `depth_bias`（保留默认值，而不是写个 `"NEUTRAL"`）。比值只在 `total_ask_sz > 0` 时可算 |
| ★ **Rubik 长空比与主动成交都是"有 ccy 才问"** | 请求 URL 带 `ccy`；结果是 `data[0][1]`（长空比）与 `data[0][1] − data[0][2]`（主动买 − 主动卖）。两者都要求 `code == "0"`、`data` 非空 |
| ★ **`available` 只在真有数据源时才翻真** | `ccy in smart_money_pool` ⇒ `available=True` 且 `reason=""`；没有源就保持 `False`（**优雅缺失**，不是编造）。专测空池时字段不变 |
| ★ **净流入的两档单位** | `|net| ≥ 1e4` ⇒ `X万 U`，否则 `Y U`（四舍五入到整数）|
| ★ **大户信号的两条互斥条件** | `加权多头 ≥ 65% 且 净流入 > 0` ⇒ `BULL_ACCUMULATION`；`≤ 35% 且 净流入 < 0` ⇒ `BEAR_DISTRIBUTION`；其余**不写** `signal`（专测"有方向但金额不对"两个半个条件都不触发）|
| ★ **胜率串只在有值时拼接** | 多/空胜率各自 `> 0` 才入串，用 `" / "` 连接；两者都为 0 不写字段 |
| ★ **`_resolve_calculate_calculus` 是 memo 的** | 模块内只解析一次；**取不到就返回 `None`**（并缓存这个"没有"），此时 `compute_15m_indicators` 跳过 Pillar 6 而保留已算好的 ATR/RSI/VWAP/OBV |
| ★ **落盘是 tmp + `os.replace`** | 写失败只打印，**不许让整轮 `job` 失败**（否则每分钟一条失败历史）|
| ★ **智能资金池导入有两级兜底** | 先 `scripts.factors.smart_money`，`ImportError` 再退 `factors.smart_money`；两者都炸才把池置空 |

## 封闭性

全文**会真的发 HTTP 请求**（ticker / 资金费率 / 持仓量 / Rubik×2）并调
`market_data_service`，故 `urlopen` 与四个 data-service 函数**全程打桩**；
`R20_DATA_DIR` 指向临时目录（模块顶部就把它作为 `DATA_DIR` 的首选），
确保 `factor_library_snapshot.json` 绝不落到生产 `data/`。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts import factor_library as FL
from scripts.factors.defaults import build_default_factors

_ITEM = {"instId": "BTC-USDT-SWAP", "name": "BTC", "ccy": "BTC"}


class _Resp:
    """`urllib.request.urlopen` 的上下文管理器替身。"""

    def __init__(self, payload):
        self._payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _dispatch(mapping, req):
    """按 URL 子串分发；值是 `Exception` 实例则抛出。"""
    url = getattr(req, "full_url", None) or str(req)
    for key, payload in mapping.items():
        if key in url:
            if isinstance(payload, Exception):
                raise payload
            return _Resp(payload)
    raise OSError(f"未预料的 URL（测试必须显式登记每条外呼）: {url}")


def _candles(count=20):
    """`[ts, o, h, l, c, vol]` —— h=idx2, l=idx3, c=idx4（模块只读这三个）。"""
    return [[i, 100.0, 110.0 + i, 90.0 - i, 100.0 + i, 5.0] for i in range(count)]


class _Base(unittest.TestCase):
    def _patch(self, target, **kwargs):
        patcher = mock.patch.object(target, **kwargs) if isinstance(target, str) \
            else mock.patch.object(target, kwargs.pop("attr"))
        return patcher

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.env = mock.patch.dict(os.environ,
                                   {"R20_DATA_DIR": str(self.data)}).start()
        # ⚠️ `DATA_DIR` / `FACTOR_LIB_CACHE_FILE` 是**模块 import 期**就算好的常量，
        # 光改环境变量对已导入的模块没用 —— 必须打模块全局，否则文件会落到
        # 沙箱或生产 data/ 下。
        self.cache = self.data / "factor_library_snapshot.json"
        mock.patch.object(FL, "DATA_DIR", str(self.data)).start()
        mock.patch.object(FL, "FACTOR_LIB_CACHE_FILE", str(self.cache)).start()
        self.addCleanup(mock.patch.stopall)
        # ⚠️ `patch.object(target, attr, new)` 的 `.start()` 返回的是 **new 本身**
        # （这里就是那个普通函数），不是 MagicMock —— 所以 `self.urls.side_effect = ...`
        # 只是在给一个函数对象挂属性，**完全无效**，urlopen 仍走原来的空表。
        # 正确做法是让替身读一张**可变的**路由表。
        self.routes = {}
        mock.patch.object(FL.urllib.request, "urlopen",
                          lambda req, timeout=None: _dispatch(self.routes, req)).start()
        self.depth = mock.patch.object(
            FL, "fetch_orderbook_depth",
            return_value={"bids": [], "asks": []}).start()
        self.candles = mock.patch.object(FL, "fetch_candles",
                                         return_value=_candles()).start()
        self.indicators = mock.patch.object(
            FL, "compute_15m_indicators", return_value=([], [], [], [])).start()
        self.inds = mock.patch.object(FL, "fetch_indicators_batch",
                                      return_value={}).start()
        self.score = mock.patch.object(FL, "score_composite_alpha").start()
        self.depth_impl = self.depth

    def _route(self, mapping):
        self.routes.clear()
        self.routes.update(mapping)

    def _ticker(self, price=100.0, open24h=95.0):
        return {"code": "0", "data": [{"last": str(price), "bidPx": "99",
                                       "askPx": "101", "open24h": str(open24h)}]}

    def _compute(self, item=None, pool=None):
        return FL.compute_instrument_factors(item or dict(_ITEM), pool or {})


# =====================================================================
# 微积分引擎的 memo 解析
# =====================================================================

class CalculusResolverTests(_Base):
    def test_a_memoized_value_is_returned_without_re_resolving(self):
        original = FL._CALCULUS_ENGINE_FN
        self.addCleanup(setattr, FL, "_CALCULUS_ENGINE_FN", original)
        sentinel = object()
        FL._CALCULUS_ENGINE_FN = sentinel
        with mock.patch.dict(sys.modules, {"calculus_engine": None}):
            self.assertIs(FL._resolve_calculate_calculus(), sentinel)

    def test_an_unavailable_engine_resolves_to_none(self):
        original = FL._CALCULUS_ENGINE_FN
        self.addCleanup(setattr, FL, "_CALCULUS_ENGINE_FN", original)
        FL._CALCULUS_ENGINE_FN = FL._UNRESOLVED
        with mock.patch.dict(sys.modules, {"calculus_engine": None}):
            self.assertIsNone(FL._resolve_calculate_calculus())

    def test_the_absence_is_memoized_too(self):
        """取不到也要记住"取不到" —— 否则每个标的都白重试一次导入。"""
        original = FL._CALCULUS_ENGINE_FN
        self.addCleanup(setattr, FL, "_CALCULUS_ENGINE_FN", original)
        FL._CALCULUS_ENGINE_FN = FL._UNRESOLVED
        with mock.patch.dict(sys.modules, {"calculus_engine": None}):
            FL._resolve_calculate_calculus()
        self.assertIsNone(FL._CALCULUS_ENGINE_FN)
        self.assertIsNot(FL._CALCULUS_ENGINE_FN, FL._UNRESOLVED)


# =====================================================================
# 逐数据源容灾
# =====================================================================

class SourceIsolationTests(_Base):
    def test_a_ticker_failure_is_swallowed(self):
        self._route({"market/ticker": OSError("ticker 挂了"),
                     "funding-rate": {"code": "0", "data": [{"fundingRate": "0.0001"}]},
                     "open-interest": {"code": "0", "data": [{"oiUsd": "100000000"}]},
                     "long-short-account-ratio": {"code": "0", "data": [["t", "1.2"]]},
                     "taker-volume": {"code": "0", "data": [["t", "30000", "10000"]]}})
        factors = self._compute()
        self.assertEqual(factors["price"], 0.0, "ticker 挂了 price 保持缺省")
        self.assertEqual(factors["smart_money_derivatives"]["funding_rate_pct"], 0.01,
                         "别的数据源必须照常工作")

    def test_a_depth_failure_is_swallowed(self):
        self._route({"market/ticker": self._ticker()})
        self.depth.side_effect = RuntimeError("深度接口挂了")
        factors = self._compute()
        self.assertEqual(factors["microstructure"]["bid_ask_depth_ratio"], 1.0,
                         "缺省比值是 1.0（中性），不是 0")

    def test_an_indicator_batch_failure_is_swallowed(self):
        self._route({"market/ticker": self._ticker()})
        self.inds.side_effect = RuntimeError("指标批挂了")
        self.assertEqual(self._compute()["trend_momentum"]["adx_1h"], 0.0)

    def test_a_funding_rate_failure_is_swallowed(self):
        self._route({"market/ticker": self._ticker(),
                     "funding-rate": OSError("资金费率挂了")})
        self.assertEqual(
            self._compute()["smart_money_derivatives"]["funding_rate_pct"], 0.0)

    def test_an_open_interest_failure_is_swallowed(self):
        self._route({"market/ticker": self._ticker(),
                     "open-interest": OSError("持仓量挂了")})
        self.assertEqual(self._compute()["smart_money_derivatives"]["oi_usd"], "--")

    def test_a_long_short_ratio_failure_is_swallowed(self):
        self._route({"market/ticker": self._ticker(),
                     "long-short-account-ratio": OSError("长空比挂了")})
        self.assertEqual(
            self._compute()["smart_money_derivatives"]["long_short_ratio"], "--")

    def test_a_taker_volume_failure_is_swallowed(self):
        self._route({"market/ticker": self._ticker(),
                     "taker-volume": OSError("主动成交挂了")})
        self.assertEqual(
            self._compute()["volume_money_flow"]["taker_net_usd"], "0 U")

    def test_a_15m_failure_is_swallowed_without_losing_the_15m_basics(self):
        self._route({"market/ticker": self._ticker()})
        self.candles.return_value = []
        factors = self._compute()
        self.assertEqual(factors["price"], 100.0, "K线挂了不该清掉 ticker 的成果")

    def test_a_15m_compute_exception_is_swallowed(self):
        self._route({"market/ticker": self._ticker()})
        self.indicators.side_effect = RuntimeError("15m 炸了")
        self.assertEqual(self._compute()["price"], 100.0)

    def test_a_1h_failure_keeps_the_15m_result(self):
        self._route({"market/ticker": self._ticker()})
        self.candles.side_effect = [[_candles()], []]
        factors = self._compute()
        self.assertEqual(factors["price"], 100.0)


class OrderbookBiasTests(_Base):
    def _book(self, bid_sz, ask_sz):
        self.depth.return_value = {"bids": [["100", str(bid_sz)]],
                                   "asks": [["101", str(ask_sz)]]}

    def test_a_bid_heavy_book_is_flagged_strong_bid(self):
        self._book(10, 5)
        factors = self._compute()
        self.assertEqual(factors["microstructure"]["bid_ask_depth_ratio"], 2.0)
        self.assertEqual(factors["microstructure"]["depth_bias"], "STRONG_BID")

    def test_an_ask_heavy_book_is_flagged_strong_ask(self):
        self._book(1, 5)
        factors = self._compute()
        self.assertEqual(factors["microstructure"]["depth_bias"], "STRONG_ASK")

    def test_the_upper_threshold_is_inclusive(self):
        self._book(15, 10)     # 1.5 整
        self.assertEqual(self._compute()["microstructure"]["depth_bias"],
                         "STRONG_BID")

    def test_the_lower_threshold_is_inclusive(self):
        self._book(67, 100)    # 0.67 整
        self.assertEqual(self._compute()["microstructure"]["depth_bias"],
                         "STRONG_ASK")

    def test_a_balanced_book_gets_no_bias_at_all(self):
        """中间地带**不写** `depth_bias`（保留缺省），不是写 `"NEUTRAL"`。"""
        self._book(1, 1)
        self.assertEqual(self._compute()["microstructure"]["depth_bias"], "NEUTRAL")

    def test_an_empty_ask_side_yields_no_ratio(self):
        self.depth.return_value = {"bids": [["100", "5"]], "asks": []}
        self.assertEqual(self._compute()["microstructure"]["bid_ask_depth_ratio"], 1.0)

    def test_a_non_dict_depth_payload_is_ignored(self):
        self.depth.return_value = "垃圾"
        self.assertEqual(self._compute()["microstructure"]["bid_ask_depth_ratio"], 1.0)


class RubikTests(_Base):
    def _routes(self, **over):
        mapping = {"market/ticker": self._ticker(),
                   "long-short-account-ratio": {"code": "0", "data": [["t", "1.23"]]},
                   "taker-volume": {"code": "0", "data": [["t", "30000", "10000"]]}}
        mapping.update(over)
        self._route(mapping)

    def test_the_long_short_ratio_is_read(self):
        self._routes()
        self.assertEqual(self._compute()["smart_money_derivatives"]["long_short_ratio"],
                         "1.23")

    def test_a_zero_code_response_is_ignored(self):
        self._routes(**{"long-short-account-ratio": {"code": "50011", "data": [["t", "9"]]}})
        self.assertEqual(self._compute()["smart_money_derivatives"]["long_short_ratio"], "--")

    def test_an_empty_data_array_is_ignored(self):
        self._routes(**{"long-short-account-ratio": {"code": "0", "data": []}})
        self.assertEqual(self._compute()["smart_money_derivatives"]["long_short_ratio"], "--")

    def test_the_taker_net_flow_is_formatted(self):
        self._routes()
        self.assertEqual(self._compute()["volume_money_flow"]["taker_net_usd"],
                         "2.0万 U")

    def test_a_negative_net_flow_is_formatted(self):
        self._routes(**{"taker-volume": {"code": "0", "data": [["t", "1000", "3000"]]}})
        self.assertEqual(self._compute()["volume_money_flow"]["taker_net_usd"],
                         "-0.2万 U")

    def test_the_rubik_calls_are_skipped_without_a_currency(self):
        self._route({"market/ticker": self._ticker()})
        item = {"instId": "X", "name": "X"}
        factors = self._compute(item)
        self.assertEqual(factors["smart_money_derivatives"]["long_short_ratio"], "--")
        self.assertEqual(factors["volume_money_flow"]["taker_net_usd"], "0 U")

    def test_the_currency_is_part_of_the_request_url(self):
        """URL 里没带 `ccy` 就会打到一个必然为空的接口（静默拿不到数据）。"""
        self._routes()
        self._compute()
        source = Path(FL.__file__).read_text(encoding="utf-8")
        self.assertIn('long-short-account-ratio?ccy={ccy}', source)
        self.assertIn('taker-volume?ccy={ccy}', source)


class SmartMoneyOverlayTests(_Base):
    POOL_KEY = "BTC"

    def _pool(self, **over):
        payload = {
            "longShortRatio": {"weightedLongRatio": 0.70, "longShortRatio": 2.5},
            "notional": {"netNotionalUsdt": 50000.0,
                         "smartMoneyLongAvgEntry": 123.4567890,
                         "smartMoneyShortAvgEntry": 0},
            "winRate": {"avgLongWinRate": 0.62, "avgShortWinRate": 0.0},
        }
        for key, value in over.items():
            if isinstance(value, dict) and isinstance(payload.get(key), dict):
                payload[key] = {**payload[key], **value}
            else:
                payload[key] = value
        return {self.POOL_KEY: payload}

    def test_the_source_is_marked_available(self):
        factors = self._compute(pool=self._pool())
        self.assertIs(factors["smart_money_derivatives"]["available"], True)
        self.assertEqual(factors["smart_money_derivatives"]["reason"], "")

    def test_an_empty_pool_stays_unavailable(self):
        """★ 优雅缺失：没有源就**不编造**，字段保持缺省。"""
        factors = self._compute(pool={})
        self.assertIs(factors["smart_money_derivatives"]["available"], False)
        self.assertEqual(factors["smart_money_derivatives"]["weighted_long_pct"], "--")

    def test_a_pool_for_another_currency_does_not_apply(self):
        factors = self._compute(pool={"ETH": self._pool()["BTC"]})
        self.assertIs(factors["smart_money_derivatives"]["available"], False)

    def test_the_weighted_long_and_flow_are_rendered(self):
        row = self._compute(pool=self._pool())["smart_money_derivatives"]
        self.assertEqual(row["weighted_long_pct"], 70.0)
        self.assertEqual(row["smart_money_flow_usd"], "5.0万 U")

    def test_a_small_flow_is_rendered_in_usdt(self):
        pool = self._pool(notional={"netNotionalUsdt": 1234.0})
        row = self._compute(pool=pool)["smart_money_derivatives"]
        self.assertEqual(row["smart_money_flow_usd"], "1234.0 U",
                         "`round(x, 0)` 返回 float ⇒ 会渲染成 `1234.0 U`")

    def test_a_large_negative_flow_uses_wan(self):
        pool = self._pool(notional={"netNotionalUsdt": -50000.0})
        row = self._compute(pool=pool)["smart_money_derivatives"]
        self.assertEqual(row["smart_money_flow_usd"], "-5.0万 U")

    def test_the_long_short_ratio_overrides_the_rubik_value(self):
        self._route({"market/ticker": self._ticker(),
                     "long-short-account-ratio": {"code": "0", "data": [["t", "1.0"]]}})
        row = self._compute(pool=self._pool())["smart_money_derivatives"]
        self.assertEqual(row["long_short_ratio"], "2.5")

    def test_a_missing_overlay_ratio_leaves_the_earlier_value(self):
        """overlay 没给长空比 ⇒ Rubik 那条值必须活下来。

        ⚠️ 这里**不能**用 `self._pool(longShortRatio={...})`：`_pool` 是**合并**语义，
        会把 `longShortRatio` 键并进原来的 `{"longShortRatio": 2.5}`，覆盖照旧发生。
        要测"缺失"必须整块替换。
        """
        self._route({"market/ticker": self._ticker(),
                     "long-short-account-ratio": {"code": "0", "data": [["t", "1.0"]]}})
        pool = {"BTC": {"longShortRatio": {"weightedLongRatio": 0.7},
                        "notional": {}, "winRate": {}}}
        row = self._compute(pool=pool)["smart_money_derivatives"]
        self.assertEqual(row["long_short_ratio"], "1.0")

    def test_the_average_long_entry_is_rendered(self):
        row = self._compute(pool=self._pool())["smart_money_derivatives"]
        self.assertEqual(row["avg_long_entry"], "123.457")

    def test_the_average_short_entry_is_rendered(self):
        pool = self._pool(notional={"smartMoneyLongAvgEntry": 0,
                                    "smartMoneyShortAvgEntry": 65432.1})
        row = self._compute(pool=pool)["smart_money_derivatives"]
        self.assertEqual(row["avg_short_entry"], "65432.1")

    def test_a_zero_average_entry_is_not_rendered(self):
        row = self._compute(pool=self._pool())["smart_money_derivatives"]
        self.assertEqual(row["avg_short_entry"], "--")

    def test_the_top_win_rate_is_joined(self):
        pool = self._pool(winRate={"avgLongWinRate": 0.62, "avgShortWinRate": 0.41})
        row = self._compute(pool=pool)["smart_money_derivatives"]
        self.assertEqual(row["top_win_rate"], "多胜率62.0% / 空胜率41.0%")

    def test_only_the_long_win_rate_is_rendered(self):
        row = self._compute(pool=self._pool())["smart_money_derivatives"]
        self.assertEqual(row["top_win_rate"], "多胜率62.0%")

    def test_no_win_rate_at_all_leaves_the_field_empty(self):
        pool = self._pool(winRate={"avgLongWinRate": 0, "avgShortWinRate": 0})
        row = self._compute(pool=pool)["smart_money_derivatives"]
        self.assertEqual(row["top_win_rate"], "--")

    def test_a_bullish_overlay_raises_accumulation(self):
        row = self._compute(pool=self._pool())["smart_money_derivatives"]
        self.assertEqual(row["signal"], "BULL_ACCUMULATION")

    def test_a_bearish_overlay_raises_distribution(self):
        pool = self._pool(longShortRatio={"weightedLongRatio": 0.20},
                          notional={"netNotionalUsdt": -50000.0})
        row = self._compute(pool=pool)["smart_money_derivatives"]
        self.assertEqual(row["signal"], "BEAR_DISTRIBUTION")

    def test_a_bullish_ratio_with_an_outflow_is_not_accumulation(self):
        """★ 半个条件不算数：加权多头够高但**净流出** ⇒ 不写 signal。"""
        pool = self._pool(notional={"netNotionalUsdt": -50000.0})
        row = self._compute(pool=pool)["smart_money_derivatives"]
        self.assertEqual(row["signal"], "UNAVAILABLE")

    def test_a_bearish_ratio_with_an_inflow_is_not_distribution(self):
        pool = self._pool(longShortRatio={"weightedLongRatio": 0.20},
                          notional={"netNotionalUsdt": 50000.0})
        row = self._compute(pool=pool)["smart_money_derivatives"]
        self.assertEqual(row["signal"], "UNAVAILABLE")

    def test_a_neutral_ratio_stays_silent(self):
        pool = self._pool(longShortRatio={"weightedLongRatio": 0.50})
        self.assertEqual(self._compute(pool=pool)["smart_money_derivatives"]["signal"],
                         "UNAVAILABLE")

    def test_the_pool_is_not_mutated(self):
        pool = self._pool()
        before = json.dumps(pool, sort_keys=True)
        self._compute(pool=pool)
        self.assertEqual(json.dumps(pool, sort_keys=True), before)


class IndicatorBatchTests(_Base):
    def test_all_four_official_indicators_are_mapped(self):
        self.inds.return_value = {"ADX": {"adx": "31.5"}, "KDJ": {"j": "88.2"},
                                  "BBWIDTH": {"bbWidth": "4.5"}, "CMF": {"cmf": "0.12"}}
        factors = self._compute()
        self.assertEqual(factors["trend_momentum"]["adx_1h"], 31.5)
        self.assertEqual(factors["trend_momentum"]["kdj_j"], 88.2)
        self.assertEqual(factors["volatility_channel"]["bb_width_1h"], 4.5)
        self.assertEqual(factors["volume_money_flow"]["cmf_1h"], 0.12)

    def test_a_missing_indicator_is_simply_absent(self):
        self.inds.return_value = {"ADX": {"adx": "31.5"}}
        factors = self._compute()
        self.assertEqual(factors["volatility_channel"]["bb_width_1h"], 0.0)


class OneHourAtrTests(_Base):
    def test_the_1h_atr_is_computed_from_fourteen_true_ranges(self):
        factors = self._compute()
        self.assertGreater(factors["volatility_channel"]["atr_1h"], 0)

    def test_the_1h_atr_percentage_needs_a_price(self):
        self._route({"market/ticker": self._ticker(price=100.0)})
        factors = self._compute()
        self.assertGreater(factors["volatility_channel"]["atr_1h_pct"], 0,
                           "有价格时必须算出 ATR%")

    def test_a_price_of_zero_leaves_the_percentage_absent(self):
        self.candles.return_value = _candles()
        factors = self._compute()
        self.assertEqual(factors["volatility_channel"]["atr_1h_pct"], 0.0)

    def test_too_few_1h_candles_skip_the_atr(self):
        self.candles.return_value = _candles(10)
        self.assertEqual(self._compute()["volatility_channel"]["atr_1h"], 0.0)


# =====================================================================
# 顶层编排
# =====================================================================

class UpdateFactorLibraryTests(_Base):
    def setUp(self):
        super().setUp()
        self.pool = mock.patch("scripts.factors.smart_money.fetch_smart_money_pool",
                               return_value={}).start()
        self.instruments = mock.patch.object(
            FL, "TARGET_INSTRUMENTS",
            [{"instId": "BTC-USDT-SWAP", "name": "BTC", "ccy": "BTC"},
             {"instId": "ETH-USDT-SWAP", "name": "ETH", "ccy": "ETH"}]).start()

    def test_the_snapshot_shape(self):
        snap = FL.update_factor_library()
        self.assertEqual(set(snap), {"timestamp", "time_str", "instruments"})
        self.assertIsInstance(snap["timestamp"], int)
        self.assertIn("T", snap["time_str"].replace(" ", "T"))

    def test_one_result_per_instrument(self):
        snap = FL.update_factor_library()
        self.assertEqual([i["name"] for i in snap["instruments"]], ["BTC", "ETH"])

    def test_the_smart_money_pool_is_fetched_once(self):
        FL.update_factor_library()
        self.pool.assert_called_once()
        self.assertEqual(self.pool.call_args[0][0], self.instruments)

    def test_the_pool_reaches_the_factor_computation(self):
        self.pool.return_value = {"BTC": {"longShortRatio": {"weightedLongRatio": 0.9},
                                          "notional": {}, "winRate": {}}}
        snap = FL.update_factor_library()
        btc = next(i for i in snap["instruments"] if i["name"] == "BTC")
        self.assertIs(btc["smart_money_derivatives"]["available"], True)

    def test_a_pool_failure_falls_back_to_an_empty_pool(self):
        self.pool.side_effect = RuntimeError("双源都挂了")
        snap = FL.update_factor_library()
        self.assertIs(snap["instruments"][0]["smart_money_derivatives"]["available"], False)

    def test_the_import_error_fallback_uses_the_bare_package_name(self):
        """★ 两种 import 拼写都要能工作（双拼写铁律）：`scripts.` 前缀不可用时退 `factors.`。"""
        import factors.smart_money as alt
        with mock.patch.object(alt, "fetch_smart_money_pool",
                               return_value={"BTC": {}}) as alt_pool:
            with mock.patch.dict(sys.modules, {"scripts.factors.smart_money": None}):
                FL.update_factor_library()
        alt_pool.assert_called_once()

    def test_the_cache_file_is_written(self):
        FL.update_factor_library()
        target = self.cache
        self.assertTrue(target.exists())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["instruments"][0]["name"],
                         "BTC")

    def test_no_temp_file_is_left_behind(self):
        FL.update_factor_library()
        self.assertEqual(sorted(p.name for p in self.data.iterdir()),
                         ["factor_library_snapshot.json"],
                         "tmp + os.replace ⇒ 不许留 .tmp 残留")

    def test_a_cache_write_failure_is_swallowed(self):
        """★ 落盘失败只打印 —— 绝不让整轮 job 挂掉（否则每分钟一条失败历史）。"""
        with mock.patch("builtins.open", side_effect=OSError("磁盘满了")):
            snap = FL.update_factor_library()
        self.assertIn("instruments", snap)

    def test_the_returned_snapshot_is_the_persisted_one(self):
        snap = FL.update_factor_library()
        stored = json.loads(self.cache.read_text(encoding="utf-8"))
        self.assertEqual(stored["timestamp"], snap["timestamp"])

    def test_the_cache_path_lives_under_the_data_dir(self):
        """`R20_DATA_DIR` 是沙箱专用变量：生产不设它 ⇒ 取值与原先逐位相同。"""
        self.assertEqual(str(Path(FL.FACTOR_LIB_CACHE_FILE).parent), FL.DATA_DIR)
        self.assertEqual(Path(FL.FACTOR_LIB_CACHE_FILE).name,
                         "factor_library_snapshot.json")


class CliEntryTests(_Base):
    """★ 覆盖 `if __name__ == "__main__":` 那段 —— 用隔离命名空间 exec 整个源码。

    ⚠️ 这**不是**为了凑数：调度器真的就是 `python scripts/factor_library.py` 这样拉起它的，
    入口段（打印逐标的摘要）坏掉等于运维每天看不到因子快照。之所以能安全 exec，
    是因为本模块的外部依赖**全部可打桩**（`urlopen` + 四个 data-service 函数 +
    `instrument_pool.load_instruments`），且 `R20_DATA_DIR` 已指向临时目录 ——
    生产 `data/` 不会被动一个字节。
    """

    def setUp(self):
        super().setUp()
        self.instruments = mock.patch.object(
            FL, "load_instruments",
            return_value=[{"instId": "BTC-USDT-SWAP", "name": "BTC", "ccy": "BTC"}]).start()
        mock.patch("scripts.factors.smart_money.fetch_smart_money_pool",
                   return_value={}).start()
        self._route({"market/ticker": self._ticker()})

    def _run_as_main(self):
        import io
        from contextlib import redirect_stdout
        source = Path(FL.__file__).read_text(encoding="utf-8")
        namespace = {"__name__": "__main__", "__file__": str(FL.__file__),
                     "__package__": "scripts"}
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exec(compile(source, str(FL.__file__), "exec"), namespace)  # noqa: S102
        return buffer.getvalue()

    def test_the_cli_entry_prints_the_snapshot_summary(self):
        out = self._run_as_main()
        self.assertIn("Factor Library Engine Snapshot Complete", out)

    def test_the_cli_entry_prints_one_line_per_instrument(self):
        out = self._run_as_main()
        self.assertIn("[BTC] Alpha Score:", out)
        self.assertIn("Signal:", out)

    def test_the_cli_entry_writes_the_cache_to_the_sandbox(self):
        self._run_as_main()
        self.assertTrue(self.cache.exists())

    def test_the_live_module_is_not_affected_by_the_exec(self):
        """那次 exec 只活在隔离命名空间里：活模块的常量与函数都不该被动过。"""
        before = FL.TARGET_INSTRUMENTS
        self._run_as_main()
        self.assertIs(FL.TARGET_INSTRUMENTS, before)
        self.assertIs(build_default_factors("BTC-USDT-SWAP", "BTC")["instId"],
                      "BTC-USDT-SWAP")


if __name__ == "__main__":
    unittest.main()

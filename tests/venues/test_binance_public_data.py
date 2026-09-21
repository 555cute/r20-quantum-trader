"""Binance 公共行情取数（第二百六十一刀）。

这一层是行情入口：取不到就必须是 **`None`（没有数据）**，绝不能造出 `0.0` 冒充价格
（本仓红线「读不到 ≠ 没有」在价格上直接等于**开着 0 价格去下单**）。

| 方法 | 关键语义 |
|---|---|
| `fetch_ticker` | 24h 统计缺 `lastPrice` ⇒ `None`；`bookTicker` 被 WAF 拦（返回 HTML）⇒ **回退 depth 档一**；两路都拿不到 ⇒ `bid/ask = None`（**不是 0.0**）|
| `fetch_candles` | 非列表/空 ⇒ `None`；单行形状坏 ⇒ **跳过该行**（不让一根坏 K 线毁掉整包）；limit 受能力上限钳制 |
| `fetch_funding_rate` / `fetch_open_interest` | 无值或非数值 ⇒ `None` |
| `fetch_orderbook` | 无 `bids` ⇒ `None`；`asks` 可缺省为空列表 |
| `fetch_top_trader_ratio` | 主通道拿不到 ⇒ **换 session 通道再试一次**；取**最新一根** `data[-1]`；非数值 ⇒ `None` |
"""

import json
import time as _time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from r20_backend.exchanges.binance import BinanceAdapter


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = BinanceAdapter.__new__(BinanceAdapter)
        self.ad.base_url = "https://fapi.binance.com"
        self.ad.environment = "demo"
        self.ad.capabilities = SimpleNamespace(max_candle_limit=1500)
        self.calls = []

    def _public(self, mapping):
        """`_public_get(path, params)` 的假实现：按 path 取，缺省 None。"""
        def _get(path, params=None, **kwargs):   # `_load_spec` 会多传 timeout
            self.calls.append((path, dict(params or {})))
            return mapping.get(path)
        self.ad._public_get = _get
        self.ad.native_symbol = lambda s: f"{s}USDT"
        return self.ad


class LoadSpecTest(_Base):
    """`_load_spec`：交易所的 tick/step/minQty 是**唯一**的下单精度来源。"""

    def _spec_payload(self, **filters):
        fl = []
        if "tick" in filters:
            fl.append({"filterType": "PRICE_FILTER", "tickSize": filters["tick"]})
        if "step" in filters or "minq" in filters:
            fl.append({"filterType": "LOT_SIZE", "stepSize": filters.get("step"),
                       "minQty": filters.get("minq")})
        return {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "filters": fl}]}

    def test_filters_are_parsed_into_the_spec(self):
        self._public({"/fapi/v1/exchangeInfo": self._spec_payload(tick="0.1", step="0.002",
                                                                  minq="0.005")})
        spec = self.ad._load_spec("BTCUSDT")
        self.assertEqual(spec.tick_size, 0.1)
        self.assertEqual(spec.step_size, 0.002)
        self.assertEqual(spec.min_size, 0.005)
        self.assertEqual(spec.ct_val, 1.0, "USDT-M 线性合约 ctVal=1（张=币）")
        self.assertEqual(spec.status, "trading")

    def test_missing_filters_keep_conservative_defaults(self):
        """过滤项缺失 ⇒ 退回**保守默认**（tick=0.1/step=0.001/minQty=0.001），不抛。"""
        self._public({"/fapi/v1/exchangeInfo": {"symbols": [{"symbol": "BTCUSDT",
                                                             "filters": []}]}})
        spec = self.ad._load_spec("BTCUSDT")
        self.assertEqual((spec.tick_size, spec.step_size, spec.min_size),
                         (0.1, 0.001, 0.001))

    def test_unknown_symbol_returns_none(self):
        self._public({"/fapi/v1/exchangeInfo": {"symbols": [{"symbol": "ETHUSDT"}]}})
        self.assertIsNone(self.ad._load_spec("BTCUSDT"), "没有该标的的规格 ⇒ 不编造")


class FetchTickerTest(_Base):
    def test_missing_last_price_means_no_data(self):
        self._public({"/fapi/v1/ticker/24hr": {"symbol": "BTCUSDT"}})
        self.assertIsNone(self.ad.fetch_ticker("BTC"), "没 lastPrice 就不能有 ticker")

    def test_non_dict_stats_means_no_data(self):
        self._public({"/fapi/v1/ticker/24hr": "<html>blocked</html>"})
        self.assertIsNone(self.ad.fetch_ticker("BTC"))

    def test_happy_path_uses_book_ticker_for_bid_ask(self):
        self._public({
            "/fapi/v1/ticker/24hr": {"lastPrice": "100.5", "openPrice": "99",
                                     "highPrice": "101", "lowPrice": "98",
                                     "priceChangePercent": "1.5", "volume": "10",
                                     "quoteVolume": "1000", "closeTime": 1700000000000},
            "/fapi/v1/bookTicker": {"bidPrice": "100.4", "askPrice": "100.6"},
        })
        t = self.ad.fetch_ticker("BTC")
        self.assertEqual(t["last"], 100.5)
        self.assertEqual(t["bid"], 100.4)
        self.assertEqual(t["ask"], 100.6)
        self.assertEqual(t["open_24h"], 99.0)
        self.assertEqual(t["chg_24h_pct"], 1.5)
        self.assertEqual(t["ts_ms"], 1700000000000, "有 closeTime 就用它（不靠本机时钟）")

    def test_book_ticker_blocked_falls_back_to_depth(self):
        """★ 机房出口 IP 上 `bookTicker` 可能被 WAF 拦 ⇒ **回退 depth 档一**。"""
        self._public({
            "/fapi/v1/ticker/24hr": {"lastPrice": "100", "closeTime": 1},
            "/fapi/v1/bookTicker": None,                      # WAF 拦截 ⇒ 拿不到 dict
            "/fapi/v1/depth": {"bids": [["100.1", "5"]], "asks": [["100.3", "7"]]},
        })
        t = self.ad.fetch_ticker("BTC")
        self.assertEqual(t["bid"], 100.1)
        self.assertEqual(t["ask"], 100.3)
        self.assertIn(("/fapi/v1/depth", {"symbol": "BTCUSDT", "limit": 5}), self.calls)

    def test_no_book_anywhere_leaves_bid_ask_none_not_zero(self):
        """★ 两路都拿不到 ⇒ `bid/ask = None`。**0.0 会被下游当成真实价格**。"""
        self._public({"/fapi/v1/ticker/24hr": {"lastPrice": "100", "closeTime": 1},
                      "/fapi/v1/bookTicker": None, "/fapi/v1/depth": None})
        t = self.ad.fetch_ticker("BTC")
        self.assertIsNone(t["bid"])
        self.assertIsNone(t["ask"])

    def test_zero_open_price_becomes_none(self):
        # ⚠️ 这里**不能**给 closeTime（给了就走它，测不到回退）—— 我第一版写了 closeTime=1，
        # 断言自然失败：那是我的数据错，不是代码错。
        self._public({"/fapi/v1/ticker/24hr": {"lastPrice": "100", "openPrice": "0"}})
        t = self.ad.fetch_ticker("BTC")
        self.assertIsNone(t["open_24h"], "0 开盘价是无意义值 ⇒ 归 None，不冒充数据")
        self.assertGreater(t["ts_ms"], 1_700_000_000_000, "无 closeTime ⇒ 退回本机时钟（毫秒）")


class FetchCandlesTest(_Base):
    def test_non_list_or_empty_means_no_data(self):
        for payload in (None, {}, []):
            with self.subTest(payload=payload):
                self._public({"/fapi/v1/klines": payload})
                self.assertIsNone(self.ad.fetch_candles("BTC"))

    def test_rows_are_normalised_to_okx_shape(self):
        self._public({"/fapi/v1/klines": [
            [1700000000000, "100", "101", "99", "100.5", "12"],
            [1700000060000, "100.5", "102", "100", "101.5", "13"],
        ]})
        out = self.ad.fetch_candles("BTC")
        self.assertEqual(out, [[1700000000000, "100", "101", "99", "100.5", "12"],
                               [1700000060000, "100.5", "102", "100", "101.5", "13"]],
                         "ts 转 int、其余转 str，顺序保持原生升序（旧→新）")

    def test_one_broken_row_is_skipped_not_fatal(self):
        """单行形状坏 ⇒ 跳过该行（**不让一根坏 K 线毁掉整包**）。"""
        self._public({"/fapi/v1/klines": [
            [1, "100", "101", "99", "100.5", "12"],
            ["bad", "x"],                      # 形状坏
            [3, "100", "101", "99", "100.5", "12"],
        ]})
        out = self.ad.fetch_candles("BTC")
        self.assertEqual(len(out), 2)
        self.assertEqual([r[0] for r in out], [1, 3])

    def test_limit_is_clamped_to_capability(self):
        self.ad.capabilities = SimpleNamespace(max_candle_limit=3)
        self._public({"/fapi/v1/klines": [[1, "1", "1", "1", "1", "1"]]})
        self.ad.fetch_candles("BTC", limit=9999)
        self.assertEqual(self.calls[-1][1]["limit"], 3, "limit 必须被能力上限钳制")


class ScalarFetchTest(_Base):
    def test_funding_rate_paths(self):
        for payload, want in ((None, None), ({}, None),
                              ({"lastFundingRate": "0.0001"}, 0.0001),
                              ({"lastFundingRate": "abc"}, None)):
            with self.subTest(payload=payload):
                self._public({"/fapi/v1/premiumIndex": payload})
                self.assertEqual(self.ad.fetch_funding_rate("BTC"), want)

    def test_open_interest_paths(self):
        """⚠️ 如实钉住一处**不对称**：`openInterest` 是**字符串** `"0"` 时真值为真 ⇒ 返回 `0.0`；
        而**数值** `0` 为假 ⇒ 返回 `None`。交易所实际发字符串，故线上走的是前者；
        两种写法语义不同这点容易被误读，故显式钉住。"""
        for payload, want in ((None, None), ({"openInterest": "123.5"}, 123.5),
                              ({"openInterest": "x"}, None),
                              ({"openInterest": "0"}, 0.0),
                              ({"openInterest": 0}, None)):
            with self.subTest(payload=payload):
                self._public({"/fapi/v1/openInterest": payload})
                self.assertEqual(self.ad.fetch_open_interest("BTC"), want)

    def test_orderbook_requires_bids_and_defaults_asks(self):
        self._public({"/fapi/v1/depth": {"bids": [["1", "2"]]}})
        book = self.ad.fetch_orderbook("BTC")
        self.assertEqual(book["bids"], [["1", "2"]])
        self.assertEqual(book["asks"], [], "缺 asks ⇒ 空列表（不是 None，下游可迭代）")
        self._public({"/fapi/v1/depth": {"asks": [["1", "2"]]}})
        self.assertIsNone(self.ad.fetch_orderbook("BTC"), "没 bids 就是没盘口")

    def test_top_trader_ratio_primary_channel(self):
        self._public({"/futures/data/topLongShortPositionRatio":
                      [{"longShortRatio": "1.8"}]})
        self.assertEqual(self.ad.fetch_top_trader_ratio("BTC"), 1.8)

    def test_top_trader_ratio_falls_back_to_session_channel(self):
        """★ 主通道（`_public_get`）拿不到 ⇒ **换 session 通道再试一次**。"""
        self._public({"/futures/data/topLongShortPositionRatio": None})
        resp = SimpleNamespace(status_code=200, json=lambda: [{"longShortRatio": "2.5"}])
        session = MagicMock()
        session.get.return_value = resp
        self.ad.get_session = lambda: session
        self.assertEqual(self.ad.fetch_top_trader_ratio("BTC"), 2.5)
        self.assertEqual(session.get.call_args.kwargs["params"]["limit"], 1)

    def test_top_trader_ratio_session_failure_returns_none(self):
        self._public({"/futures/data/topLongShortPositionRatio": None})
        session = MagicMock()
        session.get.side_effect = RuntimeError("出口被封")
        self.ad.get_session = lambda: session
        self.assertIsNone(self.ad.fetch_top_trader_ratio("BTC"))

    def test_top_trader_ratio_non_numeric_last_row_returns_none(self):
        self._public({"/futures/data/topLongShortPositionRatio":
                      [{"longShortRatio": "1.0"}, {"longShortRatio": "n/a"}]})
        self.assertIsNone(self.ad.fetch_top_trader_ratio("BTC"),
                          "取**最新一根**（data[-1]）；它坏掉就是没数据，不退回旧值")


if __name__ == "__main__":
    unittest.main()

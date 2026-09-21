"""Gate 公共行情取数（第二百七十三刀）。

Gate 与 OKX/Binance 的**载荷形态不同**，取数层必须把它归一掉，否则统一形态的消费代码会炸：

| 项 | Gate 原生 | 归一后 |
|---|---|---|
| K 线 | `{t,v,l,h,o,c,sum}` **dict 数组** | `[ts,o,h,l,c,v]` 数组，且**排成升序** |
| 盘口深度 | `[{"p":价,"s":量}]` **对象数组** | `[[price, qty]]`（与 OKX/Binance 同构）|

「**归一壳必须也归一核**」—— 只换外层名字、不换内层形态，等于让消费方在最深一层 KeyError。
"""

import unittest

from r20_backend.exchanges.gate import GateAdapter


class _Base(unittest.TestCase):
    def setUp(self):
        self.ad = GateAdapter.__new__(GateAdapter)
        self.ad.base_url = "https://api.gateio.ws"
        self.ad.native_symbol = lambda s: f"{s}_USDT" if s else ""
        self.ad.capabilities = type("C", (), {"max_candle_limit": 2000})()
        self.calls = []

    def _public(self, payload):
        def _get(path, params=None, **kw):
            self.calls.append((path, dict(params or {})))
            return payload
        self.ad._public_get = _get
        return self.ad


class FetchTickerTest(_Base):
    def test_missing_contract_means_no_ticker(self):
        self._public([{"contract": "ETH_USDT", "last": "1"}])
        self.assertIsNone(self.ad.fetch_ticker("BTC"))
        self._public({"detail": "upstream"})
        self.assertIsNone(self.ad.fetch_ticker("BTC"))

    def test_open_24h_is_backed_out_of_last_and_change(self):
        self._public([{"contract": "BTC_USDT", "last": "110", "change_percentage": "10",
                       "highest_bid": "109.9", "lowest_ask": "0", "mark_price": "110"}])
        t = self.ad.fetch_ticker("BTC")
        self.assertEqual(t["last"], 110.0)
        self.assertAlmostEqual(t["open_24h"], 100.0, places=6,
                               msg="开盘价由 last/(1+涨幅) 反推")
        self.assertEqual(t["bid"], 109.9)
        self.assertIsNone(t["ask"], "缺盘口 ⇒ None（不是 0.0）")

    def test_unusable_values_become_none_and_drop_guard_is_respected(self):
        """`chg <= -100` 时反推开盘价会除零/负无穷 ⇒ **不猜**，直接 `None`。"""
        self._public([{"contract": "BTC_USDT", "last": "0", "change_percentage": "-100",
                       "high_24h": "0", "funding_rate": "0"}])
        t = self.ad.fetch_ticker("BTC")
        self.assertIsNone(t["last"])
        self.assertIsNone(t["open_24h"])
        self.assertIsNone(t["high_24h"])
        self.assertIsNone(t["funding_rate"])
        self.assertEqual(t["chg_24h_pct"], -100.0, "涨跌幅本身照实回显")


class FetchCandlesTest(_Base):
    def _rows(self):
        # Gate 原生**倒序**（新在前）+ dict 形态；顺带混一行坏数据
        return [
            {"t": 3000, "o": "3", "h": "3.1", "l": "2.9", "c": "3.05", "v": "30"},
            {"t": 2000, "o": "2", "h": "2.1", "l": "1.9", "c": "2.05", "v": "20"},
            {"broken": True},
            {"t": 1000, "o": "1", "h": "1.1", "l": "0.9", "c": "1.05", "v": "10"},
        ]

    def test_dict_rows_are_normalised_and_sorted_ascending(self):
        """★ 归一外壳**也归一核**：dict ⇒ 数组，并把倒序排成**升序**（统一契约）。"""
        self._public(self._rows())
        out = self.ad.fetch_candles("BTC")
        self.assertEqual([r[0] for r in out], [1000, 2000, 3000], "必须升序（旧→新）")
        self.assertEqual(out[0], [1000, "1", "1.1", "0.9", "1.05", "10"],
                         "顺序是 [ts, o, h, l, c, v]")
        self.assertEqual(len(out), 3, "坏行被跳过")

    def test_empty_or_all_bad_means_no_data(self):
        self._public([])
        self.assertIsNone(self.ad.fetch_candles("BTC"))
        self._public([{"broken": True}])
        self.assertIsNone(self.ad.fetch_candles("BTC"), "全坏 ⇒ None（不是空列表）")

    def test_limit_is_clamped(self):
        self.ad.capabilities = type("C", (), {"max_candle_limit": 3})()
        self._public(self._rows()[:1])
        self.ad.fetch_candles("BTC", limit=9999)
        self.assertEqual(self.calls[-1][1]["limit"], 3)


class FundingRateAndOrderbookTest(_Base):
    def test_funding_rate_paths(self):
        for payload, want in (([], None), ("html", None),
                              ([{"name": "BTC_USDT", "funding_rate_indicative": "0.0001"}], 0.0001),
                              ([{"name": "BTC_USDT", "funding_rate_indicative": None}], None),
                              ([{"name": "BTC_USDT", "funding_rate_indicative": "x"}], None)):
            with self.subTest(payload=payload):
                self._public(payload)
                self.assertEqual(self.ad.fetch_funding_rate("BTC"), want)

    def test_orderbook_levels_are_normalised_to_pairs(self):
        """★ Gate 深度是 `{"p","s"}` 对象数组 ⇒ 归一成 `[[price, qty]]`（否则消费方 KeyError: 0）。"""
        self._public({"bids": [{"p": "100.1", "s": "5"}, ["100.0", "2"], {"bad": 1}],
                      "asks": [{"p": "100.3", "s": "7"}]})
        book = self.ad.fetch_orderbook("BTC")
        self.assertEqual(book["bids"], [["100.1", "5"], ["100.0", "2"]])
        self.assertEqual(book["asks"], [["100.3", "7"]])
        self.assertEqual(self.calls[-1][1]["limit"], 20)

    def test_orderbook_requires_bids_and_clamps_depth(self):
        self._public({"asks": [{"p": "1", "s": "1"}]})
        self.assertIsNone(self.ad.fetch_orderbook("BTC"), "没 bids 就是没盘口")
        self._public({"bids": [{"p": "1", "s": "1"}]})
        self.ad.fetch_orderbook("BTC", depth=999)
        self.assertEqual(self.calls[-1][1]["limit"], 50, "深度上限 50")


if __name__ == "__main__":
    unittest.main()

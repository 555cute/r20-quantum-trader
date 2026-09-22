"""聪明钱双源容灾采集（factors/smart_money.py）收口 —— 第 310 刀。

本模块的**全部价值**在模块 docstring 的第 3 条：

> 双源均不可用时优雅返回 `None`，保留**显式缺失语义，绝不伪造虚假中性信号**。

这条不是修辞 —— 下游 `brain/packages.py` 的 `smart_money.available` 就是据此判断的，
而提示词里对"数据源缺失"与"多空均衡"给出的是**完全不同**的指令
（前者明写"本项不构成任何方向的证据，禁止臆测填充"）。所以本刀第一条就是把这个
"取不到就得说取不到"的边界钉死。

第二条钉的是**两个源的口径并不对称** —— 那不是笔误，是被保留的既有行为，
但必须写进测试，否则下一个人"顺手统一"就会静默改变产出的数字含义。
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

from scripts.factors import smart_money as sm  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


BINANCE_LS = [{"longAccount": "0.62", "longShortRatio": "1.63"}]
BINANCE_TAKER = [{"buyVol": "30000", "sellVol": "10000"}]


class _HttpMixin:
    def setUp(self):
        self.urls: list[str] = []

    def _opener(self, routes):
        def opener(req, timeout=None):
            url = req.full_url
            self.urls.append(url)
            for key, value in routes.items():
                if key in url:
                    if isinstance(value, Exception):
                        raise value
                    return _Resp(value)
            raise AssertionError(f"未路由的 URL: {url}")
        return opener

    def _run(self, routes, ccy="BTC", **kw):
        with patch.object(sm.urllib.request, "urlopen", self._opener(routes)):
            return sm.fetch_smart_money_for_symbol(ccy, price=100.0, **kw)


class SourcePreferenceTests(_HttpMixin, unittest.TestCase):
    """★ 主源优先、备源兜底、双源皆失 ⇒ None。"""

    def test_binance_is_preferred_and_okx_is_never_called(self):
        res = self._run({"topLongShortPositionRatio": BINANCE_LS,
                         "takerlongshortRatio": BINANCE_TAKER,
                         "okx.com": AssertionError("不该走到备源")})
        self.assertEqual(res["weighted_long_pct"], 62.0)
        self.assertFalse(any("okx.com" in u for u in self.urls))

    def test_okx_is_used_when_binance_returns_nothing(self):
        res = self._run({"topLongShortPositionRatio": OSError("down"),
                         "long-short-pos-ratio": {"code": "0", "data": [["t", "2.0"]]},
                         "taker-volume": {"code": "0", "data": [["t", "40000", "10000"]]}})
        self.assertEqual(res["lsRatio"], 2.0)
        self.assertAlmostEqual(res["weighted_long_pct"], 66.7)

    def test_both_sources_down_returns_none_not_a_neutral_stub(self):
        # ★★ 模块 docstring 的第 3 条：**绝不伪造虚假中性信号**
        res = self._run({"fapi.binance.com": OSError("down"), "okx.com": OSError("down")})
        self.assertIsNone(res)
        # 反证：返回的不能是一个"看起来中性"的完整字典
        self.assertNotIsInstance(res, dict)

    def test_binance_result_without_long_short_ratio_falls_through_to_okx(self):
        # 判据是 `res and res.get("longShortRatio")` —— 真值 + 键存在，两者都要
        with patch.object(sm, "_fetch_from_binance", lambda *a, **k: {"notional": {}}), \
             patch.object(sm, "_fetch_from_okx_rubik",
                          lambda *a, **k: {"longShortRatio": {"weightedLongRatio": 0.5}}):
            self.assertEqual(sm.fetch_smart_money_for_symbol("BTC")["longShortRatio"],
                             {"weightedLongRatio": 0.5})

    def test_okx_result_without_long_short_ratio_returns_none(self):
        with patch.object(sm, "_fetch_from_binance", lambda *a, **k: None), \
             patch.object(sm, "_fetch_from_okx_rubik", lambda *a, **k: {"notional": {}}):
            self.assertIsNone(sm.fetch_smart_money_for_symbol("BTC"))

    def test_blank_currency_short_circuits_without_any_request(self):
        for ccy in ("", "   ", None):
            with self.subTest(ccy=ccy):
                self.urls = []
                self.assertIsNone(self._run({}, ccy))
                self.assertEqual(self.urls, [], "空币种不许发请求")


class BinanceSourceTests(_HttpMixin, unittest.TestCase):
    def _binance(self, routes, price=0.0):
        with patch.object(sm.urllib.request, "urlopen", self._opener(routes)):
            return sm._fetch_from_binance("BTC", price=price)

    def test_symbol_is_currency_plus_usdt(self):
        self._binance({"topLongShortPositionRatio": BINANCE_LS})
        self.assertTrue(all("symbol=BTCUSDT" in u for u in self.urls), self.urls)

    def test_none_when_the_primary_request_fails(self):
        self.assertIsNone(self._binance({"topLongShortPositionRatio": OSError("x")}))

    def test_none_when_the_payload_is_not_a_non_empty_list(self):
        for payload in ({"code": "0"}, [], None, "junk", 42):
            with self.subTest(payload=payload):
                self.assertIsNone(self._binance({"topLongShortPositionRatio": payload}))

    def test_long_account_defaults_to_half_when_missing(self):
        res = self._binance({"topLongShortPositionRatio": [{}]})
        self.assertEqual(res["weighted_long_pct"], 50.0)

    def test_long_short_ratio_prefers_the_exchange_value(self):
        res = self._binance({"topLongShortPositionRatio": BINANCE_LS})
        self.assertEqual(res["longShortRatio"]["longShortRatio"], 1.63)
        self.assertEqual(res["lsRatio"], 1.63)

    def test_absent_long_short_ratio_defaults_to_one_not_to_a_derived_value(self):
        # ★ 实测发现：`float(data[0].get("longShortRatio", 1.0))` 的**默认值是 1.0**，
        #   不是 None。所以"交易所没给比值"时拿到的是 **1.0**，而不是按
        #   `w_long / (1 - w_long)` 推导出来的值。
        res = self._binance({"topLongShortPositionRatio": [{"longAccount": "0.75"}]})
        self.assertEqual(res["longShortRatio"]["longShortRatio"], 1.0)
        self.assertEqual(res["lsRatio"], 1.0)
        # 而按 0.75 推导本应是 3.0 —— 说明这条推导路径**没有被走到**
        self.assertNotAlmostEqual(res["longShortRatio"]["longShortRatio"], 3.0)

    def test_the_derived_ratio_only_fires_when_the_exchange_explicitly_sends_zero(self):
        # ⇒ 于是 `ls_ratio or round(...)` 里的推导式**只在交易所显式给 0 时**才可达。
        #   （给空串会 `float("")` 抛异常 ⇒ 被 except 吞掉 ⇒ w_long 留 None ⇒ 整体返回 None）
        res = self._binance({"topLongShortPositionRatio":
                             [{"longAccount": "0.75", "longShortRatio": "0"}]})
        self.assertAlmostEqual(res["longShortRatio"]["longShortRatio"], 3.0)

    def test_unparsable_long_short_ratio_leaves_an_internally_inconsistent_result(self):
        # ★★ 实测发现的**内部不一致**：两条赋值同一行内先后执行 ——
        #     `w_long = float(...longAccount...)` 先成立，`ls_ratio = float(...longShortRatio...)`
        #    后抛异常被 `except Exception: pass` 吞掉。于是：
        #      · `w_long` 已有效 ⇒ 整体**不是** None、函数照常返回；
        #      · `ls_ratio` 留 None ⇒ 顶层 `lsRatio` 为 **None**；
        #      · 但嵌套的 `longShortRatio.longShortRatio` 因为 `ls_ratio` 为假而**走了推导** ⇒ 3.0。
        #    同一份结果里两个"多空比"字段一个 None 一个 3.0。**保持原样**（改它属于改口径），
        #    但必须让下一个人看见 —— 消费方若读 `lsRatio` 会当成"缺数据"、读嵌套的会当成"3.0"。
        res = self._binance({"topLongShortPositionRatio":
                             [{"longAccount": "0.75", "longShortRatio": ""}]})
        self.assertIsNotNone(res)
        self.assertEqual(res["lsRatio"], None)
        self.assertAlmostEqual(res["longShortRatio"]["longShortRatio"], 3.0)
        self.assertEqual(res["weighted_long_pct"], 75.0)

    def test_weighted_long_pct_is_rounded_to_one_decimal(self):
        res = self._binance({"topLongShortPositionRatio": [{"longAccount": "0.5231"}]})
        self.assertEqual(res["weighted_long_pct"], 52.3)

    def test_taker_net_is_multiplied_by_price_when_price_is_known(self):
        # ★★ Binance 口径：`diff * price`
        res = self._binance({"topLongShortPositionRatio": BINANCE_LS,
                             "takerlongshortRatio": [{"buyVol": "3", "sellVol": "1"}]},
                            price=100.0)
        self.assertEqual(res["notional"]["netNotionalUsdt"], 200.0)

    def test_taker_net_is_the_bare_difference_when_price_is_unknown(self):
        res = self._binance({"topLongShortPositionRatio": BINANCE_LS,
                             "takerlongshortRatio": [{"buyVol": "3", "sellVol": "1"}]},
                            price=0.0)
        self.assertEqual(res["notional"]["netNotionalUsdt"], 2.0)

    def test_taker_formatting_switches_at_ten_thousand(self):
        big = self._binance({"topLongShortPositionRatio": BINANCE_LS,
                             "takerlongshortRatio": [{"buyVol": "30000", "sellVol": "10000"}]})
        self.assertEqual(big["takerNetUsd"], "2.0万 U")
        # ⚠️ 小额分支是 `round(x, 0)` —— 它返回 **float** ⇒ 渲染成 "500.0 U"。
        #    这不是我记错：实测 `f"{round(500.0, 0)} U"` == "500.0 U"。
        #    本次改动**保持原样**（改文案属于改变用户可见输出），只把现状钉住。
        small = self._binance({"topLongShortPositionRatio": BINANCE_LS,
                               "takerlongshortRatio": [{"buyVol": "600", "sellVol": "100"}]})
        self.assertEqual(small["takerNetUsd"], "500.0 U")
        self.assertNotEqual(small["takerNetUsd"], "500 U")

    def test_negative_net_uses_absolute_value_for_the_threshold(self):
        res = self._binance({"topLongShortPositionRatio": BINANCE_LS,
                             "takerlongshortRatio": [{"buyVol": "10000", "sellVol": "30000"}]})
        self.assertEqual(res["takerNetUsd"], "-2.0万 U")
        self.assertEqual(res["notional"]["netNotionalUsdt"], -20000.0)

    def test_taker_failure_leaves_the_placeholder_and_zero(self):
        # taker 是**附加**项：它坏掉不该把主数据一起丢掉
        res = self._binance({"topLongShortPositionRatio": BINANCE_LS,
                             "takerlongshortRatio": OSError("x")})
        self.assertEqual(res["takerNetUsd"], "--")
        self.assertEqual(res["notional"]["netNotionalUsdt"], 0.0)

    def test_win_rate_block_is_always_empty(self):
        # 该源不提供胜率 ⇒ 给空字典而不是编一个假值
        self.assertEqual(self._binance({"topLongShortPositionRatio": BINANCE_LS})["winRate"], {})

    def test_returned_keys_match_the_contract(self):
        res = self._binance({"topLongShortPositionRatio": BINANCE_LS})
        self.assertEqual(set(res), {"longShortRatio", "notional", "winRate",
                                    "takerNetUsd", "lsRatio", "weighted_long_pct"})


class OkxRubikSourceTests(_HttpMixin, unittest.TestCase):
    def _okx(self, routes, price=0.0):
        with patch.object(sm.urllib.request, "urlopen", self._opener(routes)):
            return sm._fetch_from_okx_rubik("BTC", price=price)

    def test_position_ratio_is_tried_first_and_derives_weighted_long(self):
        res = self._okx({"long-short-pos-ratio": {"code": "0", "data": [["t", "3.0"]]}})
        # 3.0 / (1 + 3.0) = 0.75
        self.assertEqual(res["longShortRatio"]["weightedLongRatio"], 0.75)
        self.assertEqual(res["lsRatio"], 3.0)

    def test_account_ratio_is_the_fallback_endpoint(self):
        res = self._okx({"long-short-pos-ratio": {"code": "0", "data": []},
                         "long-short-account-ratio": {"code": "0", "data": [["t", "1.0"]]}})
        self.assertEqual(res["lsRatio"], 1.0)
        self.assertTrue(any("long-short-account-ratio" in u for u in self.urls))

    def test_none_when_both_ratio_endpoints_fail(self):
        self.assertIsNone(self._okx({"long-short-pos-ratio": OSError("x"),
                                     "long-short-account-ratio": OSError("x")}))

    def test_nonzero_code_is_not_parsed(self):
        self.assertIsNone(self._okx({"long-short-pos-ratio": {"code": "51001", "data": [["t", "2"]]},
                                     "long-short-account-ratio": {"code": "51001", "data": []}}))

    def test_taker_net_is_the_bare_difference_never_multiplied_by_price(self):
        # ★★ 与 Binance 路径**不对称**：OKX 的 taker 是**合约张数差**，不乘价格。
        #    这是既有行为（不是笔误），钉住它以免被"顺手统一"
        res = self._okx({"long-short-pos-ratio": {"code": "0", "data": [["t", "3.0"]]},
                         "taker-volume": {"code": "0", "data": [["t", "30000", "10000"]]}},
                        price=100.0)
        self.assertEqual(res["notional"]["netNotionalUsdt"], 20000.0)
        self.assertEqual(res["takerNetUsd"], "2.0万 U")

    def test_taker_uses_the_second_and_third_columns(self):
        res = self._okx({"long-short-pos-ratio": {"code": "0", "data": [["t", "1.0"]]},
                         "taker-volume": {"code": "0", "data": [["t", "600", "100"]]}})
        self.assertEqual(res["notional"]["netNotionalUsdt"], 500.0)
        # 同 Binance 路径：小额分支走 `round(x, 0)` ⇒ float ⇒ 带 ".0"
        self.assertEqual(res["takerNetUsd"], "500.0 U")

    def test_taker_failure_leaves_the_placeholder(self):
        res = self._okx({"long-short-pos-ratio": {"code": "0", "data": [["t", "3.0"]]},
                         "taker-volume": OSError("x")})
        self.assertEqual(res["takerNetUsd"], "--")
        self.assertEqual(res["notional"]["netNotionalUsdt"], 0.0)

    def test_long_short_ratio_is_not_derived_when_the_exchange_gave_one(self):
        # 与 Binance 路径不同：OKX 把交易所给的比值**原样**放进 longShortRatio
        res = self._okx({"long-short-pos-ratio": {"code": "0", "data": [["t", "2.5"]]}})
        self.assertEqual(res["longShortRatio"],
                         {"weightedLongRatio": res["longShortRatio"]["weightedLongRatio"],
                          "longShortRatio": 2.5})

    def test_returned_keys_match_the_contract(self):
        res = self._okx({"long-short-pos-ratio": {"code": "0", "data": [["t", "3.0"]]}})
        self.assertEqual(set(res), {"longShortRatio", "notional", "winRate",
                                    "takerNetUsd", "lsRatio", "weighted_long_pct"})

    def test_price_argument_does_not_affect_anything_here(self):
        # 显式钉住：该函数收了 price 但**从不使用** —— 上面那条不对称的来源
        a = self._okx({"long-short-pos-ratio": {"code": "0", "data": [["t", "3.0"]]}}, price=1.0)
        b = self._okx({"long-short-pos-ratio": {"code": "0", "data": [["t", "3.0"]]}}, price=99999.0)
        self.assertEqual(a, b)


class PoolTests(unittest.TestCase):
    """池采集：并发 + **只保留真正取到的**（取不到的不许留成 None 占位）。"""

    def _pool(self, instruments, results, **kw):
        seen = []

        def fake(ccy, price=0.0, *, timeout=3.5):
            seen.append((ccy, price, timeout))
            return results.get(ccy)

        with patch.object(sm, "fetch_smart_money_for_symbol", fake):
            out = sm.fetch_smart_money_pool(instruments, **kw)
        return out, seen

    def test_empty_instrument_list_returns_an_empty_pool(self):
        out, seen = self._pool([], {})
        self.assertEqual(out, {})
        self.assertEqual(seen, [], "空池不许建线程池、不许发请求")

    def test_currency_is_taken_from_ccy_then_name_then_inst_id(self):
        instruments = [{"ccy": "BTC"}, {"name": "ETH"}, {"instId": "SOL-USDT-SWAP"}]
        out, seen = self._pool(instruments, {"BTC": {"a": 1}, "ETH": {"a": 1},
                                             "SOL": {"a": 1}})
        self.assertEqual(sorted(c for c, _, _ in seen), ["BTC", "ETH", "SOL"])
        self.assertEqual(sorted(out), ["BTC", "ETH", "SOL"])

    def test_inst_id_without_a_dash_yields_an_empty_currency(self):
        out, seen = self._pool([{"instId": "WEIRD"}], {"": {"a": 1}})
        self.assertEqual(seen[0][0], "")
        self.assertEqual(out, {}, "空币种的结果不许进池")

    def test_failed_symbols_are_omitted_not_stored_as_none(self):
        # ★★ "保留显式缺失语义"：取不到就是**没有这个键**，
        #    而不是 `{"DOGE": None}`（后者会让下游以为"查过了，是中性"）
        out, _ = self._pool([{"ccy": "BTC"}, {"ccy": "DOGE"}], {"BTC": {"a": 1}})
        self.assertEqual(out, {"BTC": {"a": 1}})
        self.assertNotIn("DOGE", out)

    def test_a_falsy_result_is_also_omitted(self):
        out, _ = self._pool([{"ccy": "BTC"}], {"BTC": {}})
        self.assertEqual(out, {})

    def test_price_is_forwarded_as_a_float(self):
        _, seen = self._pool([{"ccy": "BTC", "price": "123.5"}], {"BTC": {"a": 1}})
        self.assertEqual(seen[0][1], 123.5)

    def test_missing_price_becomes_zero(self):
        _, seen = self._pool([{"ccy": "BTC"}], {"BTC": {"a": 1}})
        self.assertEqual(seen[0][1], 0.0)

    def test_timeout_is_forwarded(self):
        _, seen = self._pool([{"ccy": "BTC"}], {"BTC": {"a": 1}}, timeout=9.5)
        self.assertEqual(seen[0][2], 9.5)

    def test_workers_are_capped_by_the_instrument_count(self):
        # `min(max_workers, len(instruments))` —— 单标的池不该开 6 个线程
        with patch.object(sm, "ThreadPoolExecutor") as pool:
            pool.return_value.__enter__.return_value.map.return_value = iter([])
            sm.fetch_smart_money_pool([{"ccy": "BTC"}], max_workers=6)
        pool.assert_called_once_with(max_workers=1)

    def test_workers_use_the_requested_value_when_instruments_are_plenty(self):
        items = [{"ccy": f"C{i}"} for i in range(10)]
        with patch.object(sm, "ThreadPoolExecutor") as pool:
            pool.return_value.__enter__.return_value.map.return_value = iter([])
            sm.fetch_smart_money_pool(items, max_workers=3)
        pool.assert_called_once_with(max_workers=3)

    def test_real_concurrency_collects_every_symbol(self):
        # 不打桩线程池：真跑一次并发，确认结果与串行等价
        instruments = [{"ccy": c, "name": c} for c in ("BTC", "ETH", "SOL", "DOGE", "XRP")]
        payload = {"longShortRatio": {"weightedLongRatio": 0.6}, "winRate": {}}
        out, _ = self._pool(instruments, {c: dict(payload) for c in
                                          ("BTC", "ETH", "SOL", "DOGE", "XRP")})
        self.assertEqual(sorted(out), ["BTC", "DOGE", "ETH", "SOL", "XRP"])


if __name__ == "__main__":
    unittest.main()

"""回测入场装配与绩效统计（backtest/metrics.py）收口 —— 第 309 刀。

本模块是「抽取时零行为测试」的那一块：全仓只有**两个结构性抽取门**引用它
（`tests/extraction/test_backtest_metrics_extraction.py` 等），都在比对 AST 形态，
**没有一条用例真正调用过这三个函数**。

而这三个函数恰恰是回测的**结论来源**：胜率、盈亏比、夏普、索提诺、卡玛、平均 R
——报告里那些数字全从这里出来。所以本刀按模块 docstring 自己列的
「四处必须原样保留的**怪**写法」逐条钉死，因为这些"怪"正是**改了就改变既有产出、
而且不会报错**的地方（换来的是一份看起来合理、实则换了口径的历史回测）。
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts.backtest import metrics  # noqa: E402
from scripts.backtest.metrics import (  # noqa: E402
    HOURS_PER_YEAR,
    aggregate_portfolio,
    build_entry_candidate,
    compute_performance_metrics,
)


def _trade(pnl_usd, r_multiple=0.0):
    return SimpleNamespace(pnl_usd=pnl_usd, r_multiple=r_multiple)


def _entry(**over):
    kw = {"sig": {"action": "BUY", "atr": 10.0}, "close": 100.0,
          "timestamp": "2026-01-01T00:00:00", "capital": 10_000.0,
          "risk_per_trade_pct": 0.01, "slippage": 0.001, "rr": 2.0}
    kw.update(over)
    return build_entry_candidate(**kw)


def _metrics(trades=(), **over):
    kw = {"trades": list(trades), "capital": 11_000.0, "initial_capital": 10_000.0,
          "returns_list": [0.01, -0.005, 0.02], "max_drawdown": 0.05}
    kw.update(over)
    return compute_performance_metrics(**kw)


def _asset(trades=5, winning=3, losing=2, **over):
    res = {"total_trades": trades, "winning_trades": winning, "losing_trades": losing,
           "profit_factor": 2.0, "sharpe_ratio": 1.0, "sortino_ratio": 1.5,
           "calmar_ratio": 0.5, "avg_r_multiple": 0.3, "max_drawdown_pct": 6.0}
    res.update(over)
    return res


class EntryCandidateTests(unittest.TestCase):
    def test_direction_is_long_unless_action_is_buy(self):
        self.assertEqual(_entry(sig={"action": "BUY"})["direction"], "LONG")
        for action in ("SELL", "sell", "", None):
            with self.subTest(action=action):
                self.assertEqual(_entry(sig={"action": action})["direction"], "SHORT")

    def test_long_entry_pays_the_slippage(self):
        # ★ 多头买贵：×(1+slip) —— 取其**不利**方向
        self.assertAlmostEqual(_entry(close=100.0, slippage=0.001)["entry_price"], 100.1)

    def test_short_entry_sells_cheap(self):
        # ★ 空头卖便宜：×(1-slip) —— 同样取其**不利**方向
        c = _entry(sig={"action": "SELL"}, close=100.0, slippage=0.001)
        self.assertAlmostEqual(c["entry_price"], 99.9)

    def test_zero_slippage_is_the_bare_close(self):
        self.assertEqual(_entry(slippage=0.0)["entry_price"], 100.0)

    def test_stop_is_two_atr_wide_on_each_side(self):
        long = _entry(sig={"action": "BUY", "atr": 10.0})
        self.assertAlmostEqual(long["entry_price"] - long["stop_loss"], 20.0)
        short = _entry(sig={"action": "SELL", "atr": 10.0})
        self.assertAlmostEqual(short["stop_loss"] - short["entry_price"], 20.0)

    def test_take_profit_is_rr_multiples_of_the_risk_distance(self):
        long = _entry(sig={"action": "BUY", "atr": 10.0}, rr=3.0)
        self.assertAlmostEqual(long["take_profit"] - long["entry_price"], 60.0)
        short = _entry(sig={"action": "SELL", "atr": 10.0}, rr=3.0)
        self.assertAlmostEqual(short["entry_price"] - short["take_profit"], 60.0)

    def test_size_comes_from_the_risk_percentage(self):
        # risk_usd = 10000 * 0.01 = 100；risk_dist = 10 * 2 = 20 ⇒ 5 张
        self.assertAlmostEqual(_entry()["size"], 5.0)

    def test_rr_none_is_treated_as_zero_not_read_from_sig(self):
        # ★★ 文档里点名的陷阱：`rr` 是**显式形参**，不是从 sig 现取。
        #    `rr=None` ⇒ 按 0.0 ⇒ 止盈落在开仓价上（不设止盈）
        c = _entry(rr=None)
        self.assertEqual(c["take_profit"], c["entry_price"])

    def test_rr_key_inside_sig_is_deliberately_ignored(self):
        # 对照：sig 里带着 rr 也不读 —— 调用方才是 rr 的单一事实源
        c = _entry(sig={"action": "BUY", "atr": 10.0, "rr": 9.0}, rr=None)
        self.assertEqual(c["take_profit"], c["entry_price"])
        c2 = _entry(sig={"action": "BUY", "atr": 10.0, "rr": 9.0}, rr=1.0)
        self.assertAlmostEqual(c2["take_profit"] - c2["entry_price"], 20.0)

    def test_missing_atr_defaults_to_one_point_two_percent_of_close(self):
        # ★ 文档点名的第二条：atr 缺省是 close*0.012（**不是** 0），
        #   否则 risk_dist 为 0、张数算不出来
        c = _entry(sig={"action": "BUY"}, close=1000.0)
        self.assertAlmostEqual(c["entry_price"] - c["stop_loss"], 1000.0 * 0.012 * 2.0)

    def test_explicit_zero_atr_yields_zero_size_not_a_crash(self):
        c = _entry(sig={"action": "BUY", "atr": 0.0})
        self.assertEqual(c["size"], 0.0)
        self.assertEqual(c["stop_loss"], c["entry_price"])

    def test_returned_keys_match_the_documented_set(self):
        self.assertEqual(set(_entry()),
                         {"direction", "entry_time", "entry_price", "stop_loss",
                          "take_profit", "size"})

    def test_timestamp_is_passed_through_verbatim(self):
        self.assertEqual(_entry(timestamp="T-42")["entry_time"], "T-42")

    def test_signal_dict_is_not_mutated(self):
        sig = {"action": "BUY", "atr": 10.0}
        snapshot = dict(sig)
        build_entry_candidate(sig=sig, close=100.0, timestamp="t", capital=1.0,
                              risk_per_trade_pct=0.1, slippage=0.0, rr=1.0)
        self.assertEqual(sig, snapshot)


class PerformanceMetricTests(unittest.TestCase):
    """★ 三处口径原样保留：零盈亏算亏、盈亏比封顶 99、std 下限 1e-6。"""

    def test_zero_pnl_counts_as_a_loss(self):
        # ★★ 文档点名：`losing` 用 `pnl_usd <= 0`（不是 `< 0`）
        m = _metrics([_trade(5.0), _trade(0.0), _trade(-1.0)])
        self.assertEqual(m["winning_trades"], 1)
        self.assertEqual(m["losing_trades"], 2)
        self.assertAlmostEqual(m["win_rate"], 100 / 3)

    def test_win_rate_on_empty_trade_list_is_zero(self):
        m = _metrics([])
        self.assertEqual(m["win_rate"], 0.0)
        self.assertEqual(m["avg_r"], 0.0)
        self.assertEqual(m["winning_trades"], 0)
        self.assertEqual(m["losing_trades"], 0)

    def test_all_winners_caps_profit_factor_at_ninety_nine(self):
        # ★★ 封顶 99.0 而**不是** inf —— 为了 JSON 可序列化
        m = _metrics([_trade(10.0), _trade(5.0)])
        self.assertEqual(m["profit_factor"], 99.0)
        self.assertNotEqual(m["profit_factor"], float("inf"))

    def test_no_trades_at_all_gives_zero_profit_factor(self):
        self.assertEqual(_metrics([])["profit_factor"], 0.0)

    def test_profit_factor_is_gross_profit_over_gross_loss(self):
        m = _metrics([_trade(30.0), _trade(-10.0)])
        self.assertAlmostEqual(m["profit_factor"], 3.0)

    def test_total_return_is_percentage_of_initial_capital(self):
        self.assertAlmostEqual(_metrics(capital=11_000.0, initial_capital=10_000.0)["total_return"],
                               10.0)

    def test_sharpe_and_sortino_are_annualized_by_8760(self):
        returns = [0.01, -0.005, 0.02]
        m = _metrics(returns_list=returns)
        mean = sum(returns) / 3
        var = sum((r - mean) ** 2 for r in returns) / 2
        self.assertAlmostEqual(m["sharpe"], mean / math.sqrt(var) * math.sqrt(8760))
        down = [r for r in returns if r < 0]
        var_d = sum(r ** 2 for r in down) / len(down)
        self.assertAlmostEqual(m["sortino"], mean / math.sqrt(var_d) * math.sqrt(8760))

    def test_year_factor_constant_is_8760(self):
        self.assertEqual(HOURS_PER_YEAR, 8760)

    def test_single_return_gives_zero_sharpe_and_sortino(self):
        for returns in ([], [0.01]):
            with self.subTest(returns=returns):
                m = _metrics(returns_list=returns)
                self.assertEqual(m["sharpe"], 0.0)
                self.assertEqual(m["sortino"], 0.0)

    def test_identical_returns_hit_the_one_e_minus_six_floor(self):
        # ★★ 文档点名：var_ret == 0 时 std 下限 1e-6 ⇒ 夏普被**放大到极大值**。
        #    "原样保留"—— 换掉它会让这份历史回测数字无法复现
        m = _metrics(returns_list=[0.01, 0.01, 0.01])
        mean = 0.01
        self.assertAlmostEqual(m["sharpe"], mean / 1e-6 * math.sqrt(8760))
        self.assertGreater(m["sharpe"], 900_000)

    def test_no_downside_returns_caps_sortino_at_ninety_nine(self):
        m = _metrics(returns_list=[0.01, 0.02, 0.03])
        self.assertEqual(m["sortino"], 99.0)
        self.assertNotEqual(m["sortino"], float("inf"))

    def test_sortino_uses_the_downside_deviation_not_the_standard_one(self):
        m = _metrics(returns_list=[0.01, -0.02])
        down = [-0.02]
        var_d = 0.02 ** 2
        mean = (0.01 - 0.02) / 2
        self.assertAlmostEqual(m["sortino"], mean / math.sqrt(var_d) * math.sqrt(8760))

    def test_calmar_is_zero_when_there_is_no_drawdown(self):
        self.assertEqual(_metrics(max_drawdown=0.0)["calmar"], 0.0)

    def test_calmar_divides_by_drawdown_in_percent(self):
        m = _metrics(capital=11_000.0, initial_capital=10_000.0, max_drawdown=0.05)
        self.assertAlmostEqual(m["calmar"], 10.0 / (0.05 * 100))

    def test_avg_r_is_the_mean_r_multiple(self):
        m = _metrics([_trade(1.0, r_multiple=2.0), _trade(1.0, r_multiple=1.0)])
        self.assertAlmostEqual(m["avg_r"], 1.5)

    def test_returned_keys_match_the_documented_set(self):
        self.assertEqual(set(_metrics([_trade(1.0)])),
                         {"win_rate", "profit_factor", "total_return", "sharpe",
                          "sortino", "calmar", "avg_r", "winning_trades",
                          "losing_trades"})

    def test_counts_are_integers_not_lists(self):
        # ★ 返回**计数**而非中间列表（避免把内部列表泄漏成接口）
        m = _metrics([_trade(1.0), _trade(-1.0)])
        self.assertIsInstance(m["winning_trades"], int)
        self.assertIsInstance(m["losing_trades"], int)

    def test_inputs_are_not_mutated(self):
        trades = [_trade(1.0)]
        returns = [0.01, -0.01]
        compute_performance_metrics(trades=trades, capital=1.0, initial_capital=1.0,
                                    returns_list=returns, max_drawdown=0.1)
        self.assertEqual(len(trades), 1)
        self.assertEqual(returns, [0.01, -0.01])


class AggregatePortfolioTests(unittest.TestCase):
    """★ 组合层是**等权算术平均**，且分母是 `len(symbols)` 不是 `len(asset_results)`。"""

    def _agg(self, asset_results, symbols, **over):
        kw = {"asset_results": asset_results, "symbols": symbols, "total_initial": 10_000.0,
              "total_final": 12_000.0, "total_gatekeeper_filtered": 7,
              "combined_trades": list(range(30))}
        kw.update(over)
        return aggregate_portfolio(**kw)

    def test_scalar_metrics_are_plain_arithmetic_means(self):
        res = {"A": _asset(profit_factor=1.0, sharpe_ratio=2.0, sortino_ratio=3.0,
                           calmar_ratio=4.0, avg_r_multiple=5.0, max_drawdown_pct=1.0),
               "B": _asset(profit_factor=3.0, sharpe_ratio=4.0, sortino_ratio=5.0,
                           calmar_ratio=6.0, avg_r_multiple=7.0, max_drawdown_pct=5.0)}
        out = self._agg(res, ["A", "B"])
        self.assertEqual(out["profit_factor"], 2.0)
        self.assertEqual(out["sharpe_ratio"], 3.0)
        self.assertEqual(out["sortino_ratio"], 4.0)
        self.assertEqual(out["calmar_ratio"], 5.0)
        self.assertEqual(out["avg_r_multiple"], 6.0)
        self.assertEqual(out["max_drawdown_pct"], 5.0)     # 取 max，不是平均

    def test_unweighted_mean_ignores_trade_counts(self):
        # ★ 文档点名：**不是**按资金或成交笔数加权；改成加权会改变产出
        res = {"BIG": _asset(trades=1000, sharpe_ratio=10.0),
               "TINY": _asset(trades=1, sharpe_ratio=0.0)}
        self.assertEqual(self._agg(res, ["BIG", "TINY"])["sharpe_ratio"], 5.0)

    def test_denominator_is_len_symbols_not_len_asset_results(self):
        # ★★ 文档点名：原实现用 `len(symbols)`。某标的失败被跳过时，
        #    **分母不许静默改变** —— 这里用一个"没出现在 asset_results 里的标的"制造分叉
        res = {"A": _asset(sharpe_ratio=6.0)}
        out = self._agg(res, ["A", "B", "C"])
        self.assertEqual(out["sharpe_ratio"], 2.0, "分母是 3（symbols）而不是 1（结果）")

    def test_symbols_is_copied_not_aliased(self):
        symbols = ["A"]
        self._agg({"A": _asset()}, symbols)
        self.assertEqual(symbols, ["A"])

    def test_combined_totals_are_summed_across_assets(self):
        res = {"A": _asset(trades=5, winning=3, losing=2),
               "B": _asset(trades=7, winning=4, losing=3)}
        out = self._agg(res, ["A", "B"])
        self.assertEqual(out["total_trades"], 12)
        self.assertEqual(out["winning_trades"], 7)
        self.assertEqual(out["losing_trades"], 5)
        self.assertAlmostEqual(out["win_rate_pct"], round(7 / 12 * 100, 1))

    def test_zero_total_trades_gives_zero_win_rate(self):
        res = {"A": _asset(trades=0, winning=0, losing=0)}
        self.assertEqual(self._agg(res, ["A"])["win_rate_pct"], 0.0)

    def test_total_return_is_computed_from_the_scalars_not_summed(self):
        out = self._agg({"A": _asset()}, ["A"], total_initial=10_000.0, total_final=12_000.0)
        self.assertEqual(out["total_return_pct"], 20.0)

    def test_gatekeeper_count_is_passed_through(self):
        self.assertEqual(self._agg({"A": _asset()}, ["A"])["gatekeeper_filtered_count"], 7)

    def test_equity_curve_comes_from_btc_regardless_of_order(self):
        res = {"ETH-USDT-SWAP": _asset(equity_curve=[1, 2]),
               "BTC-USDT-SWAP": _asset(equity_curve=[9, 9])}
        self.assertEqual(self._agg(res, ["ETH-USDT-SWAP", "BTC-USDT-SWAP"])["equity_curve"],
                         [9, 9])

    def test_absent_btc_yields_an_empty_equity_curve(self):
        self.assertEqual(self._agg({"ETH-USDT-SWAP": _asset(equity_curve=[1])},
                                   ["ETH-USDT-SWAP"])["equity_curve"], [])

    def test_recent_trades_are_the_first_fifteen(self):
        # ★ 文档点名：取合并列表的**前 15 笔**（不是后 15 笔）
        out = self._agg({"A": _asset()}, ["A"], combined_trades=list(range(30)))
        self.assertEqual(out["recent_trades"], list(range(15)))

    def test_fewer_than_fifteen_trades_are_all_kept(self):
        out = self._agg({"A": _asset()}, ["A"], combined_trades=[1, 2, 3])
        self.assertEqual(out["recent_trades"], [1, 2, 3])

    def test_symbol_label_is_the_fixed_portfolio_marker(self):
        self.assertIn("ALL_PORTFOLIO", self._agg({"A": _asset()}, ["A"])["symbol"])

    def test_rounding_is_applied_to_the_reported_scalars(self):
        res = {"A": _asset(sharpe_ratio=1.23456, profit_factor=2.34567)}
        out = self._agg(res, ["A"])
        self.assertEqual(out["sharpe_ratio"], 1.23)
        self.assertEqual(out["profit_factor"], 2.35)

    def test_empty_symbols_list_fails_loud_with_zero_division(self):
        # 当前调用点保证非空（循环内每个 sym 都会写入）。这里钉的是"非空是前提"**且**
        # 违反时是**响亮失败**（ZeroDivisionError）—— 而不是替它编一个"无数据也返回 0"
        # 的假口径。分母 `len(symbols)` 为 0 时不静默兜底，正是这条设计的一部分。
        with self.assertRaises(ZeroDivisionError):
            self._agg({}, [])

    def test_assets_without_a_matching_symbol_still_divide_by_symbols(self):
        # 反面：`asset_results` 非空但 `symbols` 比它长 ⇒ 不报错，分母照旧是 len(symbols)
        out = self._agg({"A": _asset(sharpe_ratio=9.0)}, ["A", "B", "C"])
        self.assertEqual(out["sharpe_ratio"], 3.0)


if __name__ == "__main__":
    unittest.main()

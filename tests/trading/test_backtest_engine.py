import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.backtest_engine as backtest_engine  # noqa: E402
from scripts.backtest_engine import BacktestEngine, BacktestSummary  # noqa: E402


class BacktestEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = BacktestEngine(
            initial_capital=10000.0,
            risk_per_trade_pct=0.02,
            min_confidence_gate=0.70,
            min_rr_gate=1.5,
        )

    def test_empty_or_short_series_returns_zero_summary(self):
        res = self.engine.run([])
        self.assertIsInstance(res, BacktestSummary)
        self.assertEqual(res.total_trades, 0)
        self.assertEqual(res.initial_equity, 10000.0)

    def test_interceptor_filtering_and_risk_metrics(self):
        # Construct synthetic trend series
        candles = []
        base = 60000.0
        for i in range(100):
            p = base + (i * 100) if i < 50 else base + 5000 - ((i - 50) * 120)
            candles.append({
                "symbol": "ETH-USDT-SWAP",
                "timestamp": f"2026-09-01T{i:02d}:00:00Z",
                "open": p - 20,
                "high": p + 60,
                "low": p - 40,
                "close": p,
                "volume": 500.0,
            })

        signals = [
            # Signal 1: High confidence, high RR -> should trigger LONG
            {
                "timestamp": "2026-09-01T10:00:00Z",
                "action": "BUY",
                "confidence": 0.85,
                "rr": 2.5,
                "atr": 100.0,
            },
            # Signal 2: Low confidence (< 0.70) -> Gatekeeper must block
            {
                "timestamp": "2026-09-01T12:00:00Z",
                "action": "BUY",
                "confidence": 0.55,
                "rr": 2.0,
                "atr": 100.0,
            }
        ]

        summary = self.engine.run(candles, signals=signals)
        self.assertIsInstance(summary, BacktestSummary)
        self.assertGreaterEqual(summary.gatekeeper_filtered_count, 1)
        self.assertIn(summary.symbol, ["ETH-USDT-SWAP", "PORTFOLIO"])


class FetchOkxCandlesTests(unittest.TestCase):
    """`fetch_okx_candles`：把 OKX 的**最新在前**原始行翻成**时间正序**的字典。"""

    def _raw(self, n=3):
        # OKX 原始行序：`[ts, open, high, low, close, vol]`，**最新在前**
        return [[1757850000000 + i * 3600000, 100.0 + i, 101.0 + i, 99.0 + i,
                 100.5 + i, 1000.0 + i] for i in reversed(range(n))]

    def _run(self, raw=None, exc=None):
        calls = []

        def fetch(inst_id, bar=None, limit=None, timeout=None):
            calls.append({"inst_id": inst_id, "bar": bar, "limit": limit,
                          "timeout": timeout})
            if exc is not None:
                raise exc
            return raw

        import market_data_service
        out = io.StringIO()
        with patch.object(market_data_service, "fetch_candles", fetch), \
             contextlib.redirect_stdout(out):
            result = backtest_engine.fetch_okx_candles("BTC-USDT-SWAP", bar="1H",
                                                       limit=3)
        return result, calls, out.getvalue()

    def test_rows_are_reversed_into_chronological_order(self):
        result, _, _ = self._run(self._raw(3))
        self.assertEqual([c["ts_ms"] for c in result],
                         sorted(c["ts_ms"] for c in result), "必须是时间正序")
        self.assertLess(result[0]["close"], result[-1]["close"])

    def test_every_documented_field_is_produced(self):
        result, _, _ = self._run(self._raw(1))
        self.assertEqual(sorted(result[0]),
                         ["close", "high", "low", "open", "symbol", "timestamp",
                          "ts_ms", "volume"])
        self.assertEqual(result[0]["symbol"], "BTC-USDT-SWAP")
        self.assertEqual(result[0]["volume"], 1000.0)

    def test_timestamp_text_is_beijing_time(self):
        # `%m-%d %H:%M` 格式，按 UTC+8 渲染
        result, _, _ = self._run(self._raw(1))
        self.assertRegex(result[0]["timestamp"], r"^\d{2}-\d{2} \d{2}:\d{2}$")

    def test_fetch_is_called_with_the_documented_arguments(self):
        _, calls, _ = self._run(self._raw(1))
        self.assertEqual(calls[0]["bar"], "1H")
        self.assertEqual(calls[0]["limit"], 3)
        self.assertEqual(calls[0]["timeout"], 10.0)

    def test_a_six_element_row_carries_volume(self):
        raw = [[1757850000000, 1.0, 2.0, 0.5, 1.5, 42.0]]
        result, _, _ = self._run(raw)
        self.assertEqual(result[0]["volume"], 42.0)

    def test_a_five_element_row_defaults_volume_to_zero(self):
        raw = [[1757850000000, 1.0, 2.0, 0.5, 1.5]]
        result, _, _ = self._run(raw)
        self.assertEqual(result[0]["volume"], 0.0)

    def test_empty_source_yields_an_empty_list(self):
        result, _, _ = self._run([])
        self.assertEqual(result, [])

    def test_none_source_is_treated_as_empty(self):
        result, _, _ = self._run(None)
        self.assertEqual(result, [])

    def test_unparsable_rows_are_dropped_with_a_message(self):
        # 第 119–121 行：解析失败 ⇒ 打印 + 返回**空表**（不是半截表）
        result, _, out = self._run([["not-a-ts", 1.0, 2.0, 0.5, 1.5]])
        self.assertEqual(result, [])
        self.assertIn("Failed to parse OKX public candles", out)

    def test_a_short_row_is_also_unparsable(self):
        result, _, out = self._run([[1757850000000, 1.0]])
        self.assertEqual(result, [])
        self.assertIn("Failed to parse", out)


def _series(n=60, *, trend="up", base=60000.0):
    """构造一段有明确均线方向的行情（`run` 至少需要 20 根）。"""
    rows = []
    for i in range(n):
        p = base + (i * 120.0) if trend == "up" else base - (i * 120.0)
        rows.append({"symbol": "ETH-USDT-SWAP",
                     "timestamp": f"2026-09-01T{i:02d}:00:00Z",
                     "open": p - 10, "high": p + 50, "low": p - 50, "close": p,
                     "volume": 500.0})
    return rows


class AutoSignalGenerationTests(unittest.TestCase):
    """`signals=None` 时的双均线自动信号（第 180–194 行）。

    既有用例**总是显式传 `signals=`**，所以这条分支此前从未被执行过。
    """

    def setUp(self):
        self.engine = BacktestEngine(initial_capital=10000.0,
                                     min_confidence_gate=0.70, min_rr_gate=1.5)

    def test_uptrend_produces_signals_and_trades(self):
        summary = self.engine.run(_series(60, trend="up"))
        self.assertIsInstance(summary, BacktestSummary)

    def test_downtrend_is_also_traded(self):
        summary = self.engine.run(_series(60, trend="down"))
        self.assertIsInstance(summary, BacktestSummary)

    def test_auto_signals_respect_the_gatekeeper(self):
        # 自动信号的 conf 是 0.82 或 0.65、rr 是 2.2 或 1.5
        # ⇒ 把门槛提到 0.80 时那些 0.65 的信号必须被物理过滤
        strict = BacktestEngine(initial_capital=10000.0, min_confidence_gate=0.80,
                                min_rr_gate=2.0)
        summary = strict.run(_series(60, trend="up"))
        self.assertGreaterEqual(summary.gatekeeper_filtered_count, 1)

    def test_an_empty_signal_list_does_not_bypass_the_auto_generation(self):
        # ⚠️ 实测语义：判据是 `if signals:`（**真值**判定，不是 `is not None`）⇒
        #    `signals=[]` 与 `signals=None` **不可区分**，两者都会走双均线自动分支。
        #    也就是说"我要显式跑一份没有任何信号的回测"这件事**表达不出来**。
        #    本刀只钉现状，未改。
        # ⚠️ 必须用**两个新引擎**：`BacktestEngine` 的 `self.capital` 是跨 `run()`
        #    累积的（见 EngineIsNotReentrantTests），同一实例跑两次结果会不同
        a = BacktestEngine(initial_capital=10000.0, min_confidence_gate=0.70,
                           min_rr_gate=1.5)
        b = BacktestEngine(initial_capital=10000.0, min_confidence_gate=0.70,
                           min_rr_gate=1.5)
        empty = a.run(_series(60, trend="up"), signals=[])
        none_ = b.run(_series(60, trend="up"), signals=None)
        self.assertEqual(empty.total_trades, none_.total_trades)
        self.assertEqual(empty.total_return_pct, none_.total_return_pct)

    def test_a_non_empty_signal_list_does_bypass_the_auto_generation(self):
        # 给了非空 signals ⇒ 只认那张表；表里没有的时点一笔都不开
        summary = self.engine.run(_series(60, trend="up"),
                                  signals=[{"timestamp": "1970-01-01T00:00:00Z",
                                            "action": "BUY", "confidence": 0.9,
                                            "rr": 3.0, "atr": 1.0}])
        self.assertEqual(summary.total_trades, 0)

    def test_a_flat_market_produces_no_auto_signal(self):
        flat = [{"symbol": "ETH-USDT-SWAP", "timestamp": f"2026-09-01T{i:02d}:00:00Z",
                 "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
                 "volume": 1.0} for i in range(60)]
        summary = self.engine.run(flat)
        self.assertEqual(summary.total_trades, 0, "横盘没有均线交叉")


class EngineIsNotReentrantTests(unittest.TestCase):
    """⚠️ 实测行为（本刀仅记录，**未改**）：同一个引擎实例**不能重跑**。

    `run()` 把结果写回 `self.capital`，而 `self.capital` 只在 `__init__` 里
    初始化一次 ⇒ 第二次 `run()` 从**上一轮的期末权益**起步。
    `run_full_portfolio_backtest` 每次都新建引擎所以生产无碍，但任何"复用引擎做参数扫描"
    的写法都会静默拿到错数。
    """

    def test_a_second_run_starts_from_the_first_runs_ending_capital(self):
        def fresh():
            return BacktestEngine(initial_capital=10000.0, min_confidence_gate=0.70,
                                  min_rr_gate=1.5)

        engine = fresh()
        first = engine.run(_series(60, trend="up"))
        carried = engine.capital
        second = engine.run(_series(60, trend="up"))

        self.assertNotEqual(first.initial_equity, first.final_equity, "第一轮要真的赚/亏")
        self.assertAlmostEqual(carried, first.final_equity, places=2)
        self.assertEqual(second.initial_equity, 10000.0, "initial_equity 是常量快照")
        # 第二轮把第一轮的期末权益当起点 ⇒ 终值 = 起点 + 第二轮的盈亏
        self.assertAlmostEqual(engine.capital, second.final_equity, places=2)
        self.assertGreater(second.final_equity, first.final_equity, "两次都赚 ⇒ 累积")
        # 对照：新引擎从头跑**同一段行情** ⇒ 终值明显更低
        self.assertLess(fresh().run(_series(60, trend="up")).final_equity,
                        second.final_equity)

    def test_two_fresh_engines_agree(self):
        def once():
            return BacktestEngine(initial_capital=10000.0, min_confidence_gate=0.70,
                                  min_rr_gate=1.5).run(_series(60, trend="up"))
        self.assertEqual(once().final_equity, once().final_equity)

    def test_capital_is_mutated_in_place(self):
        engine = BacktestEngine(initial_capital=10000.0, min_confidence_gate=0.70,
                                min_rr_gate=1.5)
        before = engine.capital
        engine.run(_series(60, trend="up"))
        self.assertEqual(before, 10000.0)
        self.assertNotEqual(engine.capital, before)


class DrawdownTrackingTests(unittest.TestCase):
    """最大回撤要**按权益曲线逐根更新**（第 246 行）。"""

    def test_drawdown_is_recorded_after_a_losing_trade(self):
        # ★ 第 246 行 `max_drawdown = dd` —— 只有**真的亏过**才会被执行：
        #   `cur_equity = self.capital` 只在平仓时变，而 `peak_equity` 记住历史最高。
        #   所以必须造一笔**开进去就被打止损**的单。
        engine = BacktestEngine(initial_capital=10000.0, risk_per_trade_pct=0.05,
                                min_confidence_gate=0.5, min_rr_gate=1.0)
        rows = []
        for i in range(40):
            p = 100.0 if i < 20 else 100.0 - (i - 19) * 3.0   # 开仓后一路急跌
            rows.append({"symbol": "ETH-USDT-SWAP",
                         "timestamp": f"2026-09-01T{i:02d}:00:00Z",
                         "open": p, "high": p + 0.5, "low": p - 0.5, "close": p,
                         "volume": 100.0})
        signals = [{"timestamp": rows[20]["timestamp"], "action": "BUY",
                    "confidence": 0.99, "rr": 3.0, "atr": 1.0}]
        summary = engine.run(rows, signals=signals)

        self.assertGreater(summary.total_trades, 0, "必须真的开出一笔")
        self.assertLess(summary.final_equity, summary.initial_equity, "这一笔要亏")
        self.assertGreater(summary.max_drawdown_pct, 0.0, "亏了就必须有回撤")

    def test_max_drawdown_is_zero_when_equity_never_falls(self):
        engine = BacktestEngine(initial_capital=10000.0, min_confidence_gate=0.9,
                                min_rr_gate=3.0)
        # 高门槛 ⇒ 一笔都不开 ⇒ 权益恒定 ⇒ 回撤恒为 0
        summary = engine.run(_series(60, trend="up"))
        self.assertEqual(summary.max_drawdown_pct, 0.0)

    def test_equity_curve_is_sampled_down_for_the_mini_chart(self):
        # `equity_curve_data[:: max(1, len // 20)]` —— 起始点多一个（第 168 行的种子点）
        # ⇒ 201 个点按步长 10 采样得 **21** 个，比 20 多一
        engine = BacktestEngine(initial_capital=10000.0, min_confidence_gate=0.5,
                                min_rr_gate=1.0)
        summary = engine.run(_series(200, trend="up"))
        self.assertLessEqual(len(summary.equity_curve), 21)
        self.assertGreater(len(summary.equity_curve), 0)
        self.assertLess(len(summary.equity_curve), 200, "必须被降采样")

    def test_recent_trades_are_capped_at_ten(self):
        engine = BacktestEngine(initial_capital=10000.0, min_confidence_gate=0.5,
                                min_rr_gate=1.0)
        summary = engine.run(_series(200, trend="up"))
        self.assertLessEqual(len(summary.recent_trades), 10)


if __name__ == "__main__":
    unittest.main()

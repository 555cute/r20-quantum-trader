"""组合回测入口与 CLI（`scripts/backtest_engine.py` 的 `run_full_portfolio_backtest` / `main`）
—— 第 319 刀的后半。

这两个函数此前**从未被执行过**：既有 `tests/trading/test_backtest_engine.py` 只测
`BacktestEngine.run`，且总是显式传 `signals=`，所以自动信号分支、行情抓取、
组合聚合、CLI 落盘四条路径全空。

## 本文件只做"入口编排"

真实的单标的回测逻辑属于 `BacktestEngine`（已在另一文件覆盖），这里把
`fetch_okx_candles` 与 `BacktestEngine` 都打桩，只断言**编排契约**：
遍历哪些标的、失败时怎么兜底、聚合参数怎么传、CLI 往哪写。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.backtest_engine as be  # noqa: E402


def _candles(sym, n=30):
    return [{"symbol": sym, "timestamp": f"2026-09-01T{i:02d}:00:00Z",
             "ts_ms": i * 3600000, "open": 100.0, "high": 101.0, "low": 99.0,
             "close": 100.0 + i, "volume": 10.0} for i in range(n)]


@dataclass
class _FakeSummary:
    """必须是**真 dataclass** —— `run_full_portfolio_backtest` 会对它调 `asdict()`。"""

    final_equity: float
    gatekeeper_filtered_count: int
    recent_trades: list = field(default_factory=lambda: [{"symbol": "X"}])


class PortfolioRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fetched: list = []
        self.portfolio_calls: list = []
        self.engines: list = []

    def _run(self, *, symbols=("BTC-USDT-SWAP", "ETH-USDT-SWAP"), candles=None,
             bar="1H", limit=50, capital=1000.0):
        def fake_fetch(sym, bar=None, limit=None):
            self.fetched.append({"sym": sym, "bar": bar, "limit": limit})
            return candles(sym) if callable(candles) else candles

        engines = self.engines   # ⚠️ 必须绑到闭包变量：在嵌套类里
                                 #    `self.engines` 指的是**引擎自身**，不是这个列表

        class _Engine:
            def __init__(self, initial_capital=None, **kw):
                self.initial_capital = initial_capital
                self.seen = None
                engines.append(self)

            def run(self, series, signals=None):
                self.seen = series
                return _FakeSummary(final_equity=self.initial_capital + 100.0,
                                    gatekeeper_filtered_count=3)

        def fake_aggregate(**kw):
            self.portfolio_calls.append(kw)
            return {"total_return_pct": 1.0, "win_rate_pct": 50.0, "winning_trades": 1,
                    "losing_trades": 1, "total_trades": 2, "sharpe_ratio": 0.1,
                    "sortino_ratio": 0.2, "max_drawdown_pct": 0.0, "calmar_ratio": 0.0,
                    "final_equity": 20200.0, "gatekeeper_filtered_count": 6}

        with patch.object(be, "DEFAULT_SYMBOLS", list(symbols)), \
             patch.object(be, "fetch_okx_candles", fake_fetch), \
             patch.object(be, "BacktestEngine", _Engine), \
             patch.object(be, "aggregate_portfolio", fake_aggregate):
            return be.run_full_portfolio_backtest(bar=bar, limit=limit,
                                                  capital_per_asset=capital)

    def test_payload_has_the_documented_top_level_keys(self):
        payload = self._run()
        self.assertEqual(sorted(payload),
                         ["active_symbols", "bar", "by_symbol", "limit", "portfolio",
                          "updated_at"])

    def test_every_symbol_in_the_universe_is_fetched_and_backtested(self):
        self._run(symbols=("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"))
        self.assertEqual([f["sym"] for f in self.fetched],
                         ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"])
        self.assertEqual([e.initial_capital for e in self.engines], [1000.0] * 3)

    def test_bar_and_limit_are_forwarded_to_the_fetcher(self):
        self._run(bar="4H", limit=77)
        self.assertTrue(all(f["bar"] == "4H" and f["limit"] == 77 for f in self.fetched))

    def test_one_engine_is_created_per_symbol(self):
        # 引擎**不可重入**（`self.capital` 跨 run 累积）⇒ 必须每个标的都新建一个
        self._run(symbols=("A-USDT-SWAP", "B-USDT-SWAP"))
        self.assertEqual(len(self.engines), 2)
        self.assertIsNot(self.engines[0], self.engines[1])

    def test_real_candles_are_passed_through_untouched(self):
        series = _candles("BTC-USDT-SWAP")
        self._run(symbols=("BTC-USDT-SWAP",), candles=series)
        self.assertIs(self.engines[0].seen, series)

    def test_empty_fetch_falls_back_to_a_synthetic_series(self):
        # 第 317–333 行：抓不到行情时**不跳过标的**，而是造一段可复现的合成序列
        self._run(symbols=("BTC-USDT-SWAP",), candles=[])
        series = self.engines[0].seen
        self.assertEqual(len(series), 100)
        self.assertTrue(all(c["symbol"] == "BTC-USDT-SWAP" for c in series))
        self.assertTrue(all(set(c) >= {"symbol", "timestamp", "ts_ms", "open", "high",
                                       "low", "close", "volume"} for c in series))

    def test_synthetic_base_price_is_chosen_per_symbol(self):
        # `100 if SOL / 2500 if ETH / 80000 if BTC / else 1`
        for sym, expected in (("SOL-USDT-SWAP", 100.0), ("ETH-USDT-SWAP", 2500.0),
                             ("BTC-USDT-SWAP", 80000.0), ("XYZ-USDT-SWAP", 1.0)):
            with self.subTest(sym=sym):
                self.engines.clear()
                self._run(symbols=(sym,), candles=[])
                first = self.engines[0].seen[0]
                self.assertGreater(first["close"], expected * 0.5)
                self.assertLess(first["close"], expected * 2.0)

    def test_synthetic_series_is_deterministic(self):
        a = self._run(symbols=("BTC-USDT-SWAP",), candles=[]), self.engines[-1].seen
        b = self._run(symbols=("BTC-USDT-SWAP",), candles=[]), self.engines[-1].seen
        self.assertEqual([c["close"] for c in a[1]], [c["close"] for c in b[1]])

    def test_portfolio_totals_are_accumulated_across_symbols(self):
        self._run(symbols=("A-USDT-SWAP", "B-USDT-SWAP"), capital=1000.0)
        call = self.portfolio_calls[0]
        self.assertEqual(call["total_initial"], 2000.0, "每标的资金 × 标的数")
        self.assertEqual(call["total_final"], 2200.0, "两个引擎各 +100")
        self.assertEqual(call["total_gatekeeper_filtered"], 6, "被过滤次数要累加")

    def test_combined_trades_gather_every_symbol(self):
        self._run(symbols=("A-USDT-SWAP", "B-USDT-SWAP"))
        self.assertEqual(len(self.portfolio_calls[0]["combined_trades"]), 2)

    def test_aggregate_receives_the_symbol_list_and_per_symbol_results(self):
        self._run(symbols=("A-USDT-SWAP", "B-USDT-SWAP"))
        call = self.portfolio_calls[0]
        self.assertEqual(call["symbols"], ["A-USDT-SWAP", "B-USDT-SWAP"])
        self.assertEqual(sorted(call["asset_results"]), ["A-USDT-SWAP", "B-USDT-SWAP"])

    def test_by_symbol_payload_is_a_plain_dict_per_symbol(self):
        payload = self._run(symbols=("A-USDT-SWAP",))
        self.assertEqual(sorted(payload["by_symbol"]["A-USDT-SWAP"]),
                         ["final_equity", "gatekeeper_filtered_count", "recent_trades"])

    def test_bar_and_limit_and_active_symbols_are_echoed(self):
        payload = self._run(symbols=("A-USDT-SWAP", "B-USDT-SWAP"), bar="15m", limit=9)
        self.assertEqual(payload["bar"], "15m")
        self.assertEqual(payload["limit"], 9)
        self.assertEqual(payload["active_symbols"], ["A-USDT-SWAP", "B-USDT-SWAP"])

    def test_updated_at_is_beijing_time_text(self):
        payload = self._run(symbols=("A-USDT-SWAP",))
        self.assertRegex(payload["updated_at"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \(北京时间\)$")

    def test_empty_universe_yields_an_empty_portfolio(self):
        self._run(symbols=())
        self.assertEqual(self.portfolio_calls[0]["total_initial"], 0.0)
        self.assertEqual(self.portfolio_calls[0]["total_final"], 0.0)


class MainCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.argv = ["backtest_engine.py"]
        self.calls: list = []

    def _run(self, argv=(), report=None, expect_exit=None):
        payload = report if report is not None else {
            "updated_at": "2026-09-22 12:00:00 (北京时间)", "bar": "1H", "limit": 100,
            "active_symbols": ["BTC-USDT-SWAP"],
            "portfolio": {"total_return_pct": 12.5, "final_equity": 11250.0,
                          "win_rate_pct": 60.0, "winning_trades": 6, "losing_trades": 4,
                          "total_trades": 10, "sharpe_ratio": 1.2, "sortino_ratio": 1.5,
                          "max_drawdown_pct": 3.3, "calmar_ratio": 0.8,
                          "gatekeeper_filtered_count": 7}}
        out = io.StringIO()
        with patch.object(sys, "argv", self.argv + list(argv)), \
             patch.object(be, "run_full_portfolio_backtest",
                          lambda **kw: self.calls.append(kw) or payload), \
             contextlib.redirect_stdout(out):
            be.main()
        return out.getvalue()

    def test_default_output_path_is_the_data_directory(self):
        # 默认 `data/backtest_report.json` —— 只用仓库相对路径断言，不真写
        src = Path(be.__file__).read_text(encoding="utf-8")
        self.assertIn('default="data/backtest_report.json"', src)

    def test_report_is_written_to_the_requested_path(self):
        target = Path(self.tmp.name) / "out.json"
        self._run(["--output", str(target)])
        self.assertTrue(target.exists())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["bar"], "1H")

    def test_missing_parent_directories_are_created(self):
        target = Path(self.tmp.name) / "a" / "b" / "out.json"
        self._run(["--output", str(target)])
        self.assertTrue(target.exists())

    def test_written_json_is_utf8_and_not_ascii_escaped(self):
        target = Path(self.tmp.name) / "out.json"
        self._run(["--output", str(target)])
        self.assertIn("北京时间", target.read_text(encoding="utf-8"))

    def test_cli_defaults_are_forwarded(self):
        target = Path(self.tmp.name) / "out.json"
        self._run(["--output", str(target)])
        self.assertEqual(self.calls[0],
                         {"bar": "1H", "limit": 100, "capital_per_asset": 10000.0})

    def test_cli_overrides_are_forwarded(self):
        target = Path(self.tmp.name) / "out.json"
        self._run(["--output", str(target), "--bar", "4H", "--limit", "42",
                   "--capital", "500"])
        self.assertEqual(self.calls[0],
                         {"bar": "4H", "limit": 42, "capital_per_asset": 500.0})

    def test_report_banner_and_key_numbers_are_printed(self):
        target = Path(self.tmp.name) / "out.json"
        out = self._run(["--output", str(target)])
        self.assertIn("R20 QUANTUM TRADER 6-ASSET PORTFOLIO BACKTEST ATTRIBUTION REPORT", out)
        self.assertIn("Total Return         : 12.5%", out)
        self.assertIn("$11,250.00", out)
        self.assertIn("60.0% (6胜 / 4负, 共10单)", out)
        self.assertIn("Max Drawdown         : 3.3% | Calmar: 0.8", out)
        self.assertIn("Gatekeeper Blocked   : 7 次", out)

    def test_the_mode_argument_is_echoed_in_the_progress_line(self):
        target = Path(self.tmp.name) / "out.json"
        out = self._run(["--output", str(target), "--symbol", "BTC"])
        self.assertIn("mode=BTC", out)

    def test_main_does_not_read_candle_data_itself(self):
        # 数据抓取是 `run_full_portfolio_backtest` 的事；`main` 只编排 + 落盘
        target = Path(self.tmp.name) / "out.json"
        with patch.object(be, "fetch_okx_candles",
                          side_effect=AssertionError("main 不该直接抓行情")):
            self._run(["--output", str(target)])
        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.backtest_engine import (
    BacktestEngine,
    BacktestSummary,
    MarketDataError,
    default_symbols,
    main,
    run_full_portfolio_backtest,
)


def _candles(symbol: str, count: int, base: float = 60000.0) -> list[dict]:
    series = []
    for i in range(count):
        price = base + (i * 100) if i < count // 2 else base + 5000 - ((i - count // 2) * 120)
        series.append(
            {
                "symbol": symbol,
                "timestamp": f"2026-09-01T{i:02d}:00:00Z",
                "open": price - 20,
                "high": price + 60,
                "low": price - 40,
                "close": price,
                "volume": 500.0,
            }
        )
    return series


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
        candles = _candles("ETH-USDT-SWAP", 100)
        signals = [
            {
                "timestamp": "2026-09-01T10:00:00Z",
                "action": "BUY",
                "confidence": 0.85,
                "rr": 2.5,
                "atr": 100.0,
            },
            {
                "timestamp": "2026-09-01T12:00:00Z",
                "action": "BUY",
                "confidence": 0.55,
                "rr": 2.0,
                "atr": 100.0,
            },
        ]

        summary = self.engine.run(candles, signals=signals)
        self.assertIsInstance(summary, BacktestSummary)
        self.assertGreaterEqual(summary.gatekeeper_filtered_count, 1)
        self.assertIn(summary.symbol, ["ETH-USDT-SWAP", "PORTFOLIO"])

    def test_gated_signals_leave_equity_unchanged(self):
        candles = _candles("ETH-USDT-SWAP", 40, base=2000.0)
        signals = [
            {
                "timestamp": candles[20]["timestamp"],
                "action": "BUY",
                "confidence": 0.10,
                "rr": 0.50,
                "atr": 10.0,
            }
        ]
        summary = self.engine.run(candles, signals=signals)
        self.assertEqual(summary.total_trades, 0)
        self.assertEqual(summary.final_equity, 10000.0)
        self.assertGreaterEqual(summary.gatekeeper_filtered_count, 1)


class MarketDataIntegrityTests(unittest.TestCase):
    def test_missing_candles_do_not_fabricate_portfolio_success(self):
        report = run_full_portfolio_backtest(
            symbols=["BTC-USDT-SWAP"],
            candle_loader=lambda *args, **kwargs: [],
        )
        self.assertEqual(report["status"], "incomplete")
        self.assertFalse(report["llm_or_council_replay"])
        self.assertEqual(report["strategy"], "independent_ma_reference")
        self.assertNotIn("portfolio", report)
        self.assertTrue(report["errors"])
        self.assertEqual(report["errors"][0]["code"], "missing")

    def test_invalid_ohlc_is_incomplete(self):
        illegal = [
            {
                "symbol": "BTC-USDT-SWAP",
                "timestamp": f"t{i}",
                "open": 100.0,
                "high": 90.0,
                "low": 80.0,
                "close": 95.0,
                "volume": 1.0,
            }
            for i in range(30)
        ]
        report = run_full_portfolio_backtest(
            symbols=["BTC-USDT-SWAP"],
            candle_loader=lambda *args, **kwargs: illegal,
        )
        self.assertEqual(report["status"], "incomplete")
        self.assertNotIn("portfolio", report)
        self.assertEqual(report["errors"][0]["code"], "invalid")

    def test_insufficient_candles_is_incomplete(self):
        report = run_full_portfolio_backtest(
            symbols=["BTC-USDT-SWAP"],
            candle_loader=lambda *args, **kwargs: _candles("BTC-USDT-SWAP", 5),
        )
        self.assertEqual(report["status"], "incomplete")
        self.assertNotIn("portfolio", report)
        self.assertEqual(report["errors"][0]["code"], "insufficient")

    def test_valid_injected_series_is_ma_reference_success(self):
        series = _candles("ETH-USDT-SWAP", 40, base=2000.0)
        report = run_full_portfolio_backtest(
            symbols=["ETH-USDT-SWAP"],
            candle_loader=lambda *args, **kwargs: series,
        )
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["strategy"], "independent_ma_reference")
        self.assertFalse(report["llm_or_council_replay"])
        self.assertEqual(report["active_symbols"], ["ETH-USDT-SWAP"])
        self.assertIn("portfolio", report)
        self.assertIsInstance(report["portfolio"]["total_return_pct"], (int, float))

    def test_cli_failure_does_not_overwrite_prior_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backtest_report.json"
            prior = {"status": "ok", "portfolio": {"total_return_pct": 12.5}, "marker": "prior-success"}
            path.write_text(json.dumps(prior), encoding="utf-8")
            with patch(
                "scripts.backtest_engine.fetch_okx_candles",
                side_effect=MarketDataError("offline", symbol="BTC-USDT-SWAP", code="missing"),
            ):
                with patch("sys.stdout"), patch("sys.stderr"):
                    code = main(["--symbol", "BTC-USDT-SWAP", "--output", str(path)])
            self.assertNotEqual(code, 0)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), prior)

    def test_default_symbols_reuse_current_instrument_pool(self):
        pool = [
            {"instId": "LINK-USDT-SWAP"},
            {"instId": "BTC-USDT-SWAP"},
            {"instId": "LINK-USDT-SWAP"},
        ]
        with patch("scripts.backtest_engine.load_pool_instruments", return_value=pool):
            symbols = default_symbols()
        self.assertEqual(symbols, ["LINK-USDT-SWAP", "BTC-USDT-SWAP"])
        self.assertNotIn("ASTER-USDT-SWAP", symbols)

    def test_default_loader_uses_selected_exchange_not_okx_host(self):
        from scripts.backtest_engine import fetch_okx_candles

        class Fake:
            def candles(self, inst_id, bar="1H", limit=100):
                return list(reversed([[str(1_700_000_000_000 + i * 3_600_000), "100", "101", "99", "100.5", "1"] for i in range(limit)]))

        with patch("r20_exchange.runtime.get_exchange", return_value=Fake()):
            rows = fetch_okx_candles("BTC-USDT-SWAP", bar="1H", limit=5)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["symbol"], "BTC-USDT-SWAP")
        self.assertGreater(rows[-1]["ts_ms"], rows[0]["ts_ms"])



if __name__ == "__main__":
    unittest.main()

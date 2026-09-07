"""Offline ledger/account-scope regressions: temp dirs, injectable exchange, no network."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from r20_backend.account_paths import classify_bill, history_fetch_incomplete
from scripts import sync_full_ledger as ledger


class FakeIncomplete(RuntimeError):
    incomplete = True
    code = "positions_history_incomplete"
    status = "unavailable"


class FakeExchange:
    def __init__(self, *, positions=None, history=None, orders=None, history_error=None):
        self._positions = positions or []
        self._history = history or []
        self._orders = orders or []
        self._history_error = history_error

    def positions(self):
        return list(self._positions)

    def positions_history(self, limit=100):
        if self._history_error is not None:
            raise self._history_error
        return list(self._history)

    def order_history(self, limit=100):
        return list(self._orders)


class LedgerAccountScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = SimpleNamespace(
            exchange="okx",
            mode="demo",
            fingerprint="abc123",
            identity="okx:demo:abc123",
            configured=True,
        )
        self.ledger_file = self.root / "trading_ledger.json"
        self.status_file = self.root / "ledger_sync_status.json"
        self.patches = [
            patch.object(ledger, "LEDGER_JSON_FILE", str(self.ledger_file)),
            patch.object(ledger, "POSITION_TRACKER_FILE", str(self.root / "position_trackers.json")),
            patch.object(ledger, "TARGET_INSTRUMENTS", [{"instId": "BTC-USDT-SWAP", "name": "BTC", "ctVal": 1}]),
            patch.object(ledger, "selected_environment", return_value=self.env),
            patch.object(ledger, "state_path", side_effect=lambda name, env=None: self.root / name),
            patch.object(ledger, "load_account_baseline", return_value={"reset_time": "1970-01-01 00:00:00", "initial_capital": 10000}),
            patch.object(ledger, "write_ledger_sync_status", side_effect=lambda payload, env=None: self.status_file.write_text(json.dumps(payload), encoding="utf-8") or self.status_file),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def test_incomplete_history_does_not_overwrite_complete_ledger(self):
        existing = [{"id": "pos_hist_1_BTC", "status": "closed", "pnl": 12.5, "inst": "BTC"}]
        self.ledger_file.write_text(json.dumps(existing), encoding="utf-8")
        result = ledger.build_lifecycle_ledger(FakeExchange(history_error=FakeIncomplete("truncated")))
        self.assertEqual(result, existing)
        self.assertEqual(json.loads(self.ledger_file.read_text(encoding="utf-8")), existing)
        status = json.loads(self.status_file.read_text(encoding="utf-8"))
        self.assertTrue(status["incomplete"])

    def test_okx_closed_history_keeps_net_as_pnl_plus_signed_fee(self):
        history = [{
            "instId": "BTC-USDT-SWAP",
            "direction": "long",
            "openAvgPx": "100",
            "closeAvgPx": "110",
            "pnl": "10",
            "fee": "-0.4",
            "lever": "5",
            "closeTotalPos": "0.1",
            "cTime": "1700000000000",
            "uTime": "1700003600000",
            "type": "1",
            "pnlRatio": "0.2",
        }]
        result = ledger.build_lifecycle_ledger(FakeExchange(history=history))
        closed = [row for row in result if row["status"] == "closed"]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["gross_pnl"], 10.0)
        self.assertEqual(closed[0]["fee"], -0.4)
        self.assertEqual(closed[0]["pnl"], 9.6)
        self.assertEqual(closed[0]["ctVal"], "1")
        self.assertEqual(closed[0]["quantity_unit"], "base")

    def test_binance_commission_is_not_added_into_realized_pnl_twice(self):
        history = [{
            "instId": "BTC-USDT-SWAP",
            "direction": "short",
            "openAvgPx": "110",
            "closeAvgPx": "100",
            "pnl": "8.0",
            "fee": "-0.5",
            "lever": "3",
            "closeTotalPos": "0.2",
            "cTime": "1700000000000",
            "uTime": "1700007200000",
            "type": "1",
            "pnlRatio": "0.1",
        }]
        result = ledger.build_lifecycle_ledger(FakeExchange(history=history))
        closed = result[0]
        self.assertEqual(closed["gross_pnl"], 8.0)
        self.assertEqual(closed["pnl"], 7.5)
        self.assertNotEqual(closed["pnl"], 8.0 + (-0.5) + (-0.5))

    def test_recent_window_preserves_older_closed_cycles_but_not_stale_holdings(self):
        closed = {"id": "older-cycle", "status": "closed", "pnl": 12.5, "inst": "BTC", "close_time": "2025-01-01 00:00:00"}
        stale_holding = {"id": "stale-holding", "status": "holding", "inst": "BTC"}
        self.ledger_file.write_text(json.dumps([closed, stale_holding]), encoding="utf-8")
        result = ledger.build_lifecycle_ledger(FakeExchange())
        self.assertEqual(result, [closed])
        self.assertEqual(json.loads(self.ledger_file.read_text(encoding="utf-8")), [closed])

    def test_missing_historical_leverage_does_not_fabricate_margin_or_roi(self):
        history = [{
            "instId": "BTC-USDT-SWAP", "direction": "long",
            "openAvgPx": "100", "closeAvgPx": "110", "pnl": "10", "fee": "-0.4",
            "lever": None, "pnlRatio": None, "closeTotalPos": "0.1",
            "cTime": "1700000000000", "uTime": "1700003600000", "type": "1",
        }]
        result = ledger.build_lifecycle_ledger(FakeExchange(history=history))
        self.assertIsNone(result[0]["lever"])
        self.assertIsNone(result[0]["margin"])
        self.assertIsNone(result[0]["roi_pct"])
        self.assertEqual(result[0]["net_pnl"], 9.6)

    def test_classify_bill_keeps_realized_commission_and_funding_apart(self):
        self.assertEqual(classify_bill({"subType": "5", "pnl": "2", "fee": "-0.1"}), "realized")
        self.assertEqual(classify_bill({"type": "REALIZED_PNL", "pnl": "2", "fee": "0"}), "realized")
        self.assertEqual(classify_bill({"type": "COMMISSION", "pnl": "0", "fee": "-0.2"}), "commission")
        self.assertEqual(classify_bill({"type": "FUNDING_FEE", "pnl": "-0.3", "fee": "0"}), "funding")
        self.assertEqual(classify_bill({"subType": "173", "pnl": "-0.1"}), "funding")

    def test_history_incomplete_flag_on_runtime_error(self):
        self.assertTrue(history_fetch_incomplete(FakeIncomplete("no")))
        self.assertFalse(history_fetch_incomplete(RuntimeError("timeout")))


if __name__ == "__main__":
    unittest.main()

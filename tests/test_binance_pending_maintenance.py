"""Offline Binance pending-entry maintenance and trader-cycle gating.

No network, no credentials, no real orders. Temporary DATA_DIR only.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import scripts.ai_factor_trader as aft
import scripts.binance_order_maintenance as bom
from r20_backend.file_lock import acquire, release
from r20_exchange import runtime as exchange_runtime


def _factor(inst_id: str, name: str) -> dict:
    return {
        "name": name,
        "instId": inst_id,
        "type": "crypto",
        "price": 100000.0,
        "rsi": 50.0,
        "atr": 100.0,
        "precision": 1,
        "position": None,
        "sz": Decimal("0.01"),
        "risk_per_trade_usd": 15.0,
        "lotSz": Decimal("0.001"),
        "minSz": Decimal("0.001"),
        "tickSz": Decimal("0.1"),
        "minNotional": Decimal("5"),
        "bidPx": 100000.0,
        "askPx": 100000.0,
        "market_data_valid": True,
    }


class _Exchange:
    def __init__(self):
        self.pending = False
        self.reconcile_result = {"pending": 0, "blocked": False, "errors": []}
        self.has_pending_calls = 0
        self.reconcile_calls = 0
        self.place_payload = {"ordId": "ord-1", "protection_status": "awaiting_fill"}
        self.place_calls = 0
        self.leverage_payload = {"lever": "5"}

    def has_pending_entries(self):
        self.has_pending_calls += 1
        return self.pending

    def reconcile_pending_entries(self):
        self.reconcile_calls += 1
        return dict(self.reconcile_result)

    def place_protected_limit_order(self, *args, **kwargs):
        self.place_calls += 1
        return dict(self.place_payload)

    def set_leverage(self, inst_id, leverage, pos_side="long"):
        return dict(self.leverage_payload)


class PendingMaintenanceLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(exchange_runtime.unfreeze_environment)

    def _isolate(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(exchange_runtime, "DATA_DIR", self.root))
        stack.enter_context(patch("scripts.okx_runtime._load_dotenv", return_value={
            "R20_EXCHANGE": "binance",
            "R20_BINANCE_ENV": "demo",
            "BINANCE_DEMO_API_KEY": "fixture-key",
            "BINANCE_DEMO_SECRET_KEY": "fixture-secret",
        }))
        return stack

    def test_same_lock_excludes_maintenance_without_network(self):
        self._isolate()
        lock_path = self.root / ".ai_factor_trader.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
        self.addCleanup(handle.close)
        acquire(handle, blocking=False)
        self.addCleanup(lambda: release(handle))
        exchange = _Exchange()
        exchange.pending = True
        with patch.object(bom, "get_exchange", create=True), patch(
            "r20_exchange.runtime.get_exchange", return_value=exchange,
        ):
            result = bom.run_pending_entry_maintenance()
        self.assertEqual(result["status"], "busy")
        self.assertEqual(result["reason"], "trader cycle lock held")
        self.assertFalse(result["networked"])
        self.assertEqual(exchange.reconcile_calls, 0)
        self.assertEqual(exchange.has_pending_calls, 0)

    def test_unconfigured_skips_without_get_exchange(self):
        stack = self._isolate()
        stack.enter_context(patch("scripts.okx_runtime._load_dotenv", return_value={
            "R20_EXCHANGE": "binance",
            "R20_BINANCE_ENV": "demo",
        }))
        with patch("r20_exchange.runtime.get_exchange") as get_ex:
            result = bom.run_pending_entry_maintenance()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "unconfigured")
        self.assertFalse(result["networked"])
        get_ex.assert_not_called()

    def test_okx_skips_without_pending_calls(self):
        stack = self._isolate()
        stack.enter_context(patch("scripts.okx_runtime._load_dotenv", return_value={
            "R20_EXCHANGE": "okx",
            "R20_OKX_ENV": "demo",
            "OKX_DEMO_API_KEY": "k",
            "OKX_DEMO_SECRET_KEY": "s",
            "OKX_DEMO_PASSPHRASE": "p",
        }))
        with patch("r20_exchange.runtime.get_exchange") as get_ex:
            result = bom.run_pending_entry_maintenance()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "not_binance")
        self.assertFalse(result["networked"])
        get_ex.assert_not_called()

    def test_no_pending_state_does_not_reconcile(self):
        self._isolate()
        exchange = _Exchange()
        exchange.pending = False
        with patch("r20_exchange.runtime.get_exchange", return_value=exchange):
            result = bom.run_pending_entry_maintenance()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_pending")
        self.assertFalse(result["networked"])
        self.assertEqual(exchange.has_pending_calls, 1)
        self.assertEqual(exchange.reconcile_calls, 0)

    def test_pending_reconcile_does_not_place_entry(self):
        self._isolate()
        exchange = _Exchange()
        exchange.pending = True
        exchange.reconcile_result = {"pending": 1, "blocked": True, "errors": []}
        with patch("r20_exchange.runtime.get_exchange", return_value=exchange):
            result = bom.run_pending_entry_maintenance()
        self.assertEqual(result["status"], "reconciled")
        self.assertTrue(result["blocked"])
        self.assertEqual(result["pending"], 1)
        self.assertTrue(result["networked"])
        self.assertEqual(exchange.reconcile_calls, 1)
        self.assertEqual(exchange.place_calls, 0)


class PendingRiskGateTests(unittest.TestCase):
    def test_okx_is_not_blocked(self):
        env = MagicMock(exchange="okx")
        with patch.object(aft, "selected_environment", return_value=env), patch.object(
            aft, "_call_exchange",
        ) as call:
            blocked, log = aft.binance_pending_risk_gate()
        self.assertFalse(blocked)
        self.assertEqual(log, "")
        call.assert_not_called()

    def test_awaiting_fill_is_pending_not_filled_or_protected(self):
        env = MagicMock(exchange="binance")
        def call(method, *args, **kwargs):
            if method == "has_pending_entries":
                return True, True, ""
            if method == "reconcile_pending_entries":
                return True, {"pending": 1, "blocked": True, "errors": []}, ""
            self.fail(method)
        with patch.object(aft, "selected_environment", return_value=env), patch.object(
            aft, "_call_exchange", side_effect=call,
        ):
            blocked, log = aft.binance_pending_risk_gate()
        self.assertTrue(blocked)
        self.assertTrue(log.startswith("[Trader] Pending:"))
        self.assertIn("awaiting fill", log)
        self.assertNotIn("filled", log.lower())
        self.assertNotIn("已保护", log)
        self.assertNotIn("[Trader] Abort:", log)

    def test_reconcile_errors_abort_and_do_not_open_replacement(self):
        env = MagicMock(exchange="binance")
        def call(method, *args, **kwargs):
            if method == "has_pending_entries":
                return True, True, ""
            if method == "reconcile_pending_entries":
                return True, {"pending": 1, "blocked": True, "errors": ["order status unknown"]}, ""
            self.fail(method)
        with patch.object(aft, "selected_environment", return_value=env), patch.object(
            aft, "_call_exchange", side_effect=call,
        ):
            blocked, log = aft.binance_pending_risk_gate()
        self.assertTrue(blocked)
        self.assertTrue(log.startswith("[Trader] Abort:"))
        self.assertIn("unknown", log)
    def test_cleared_pending_allows_later_cycle(self):
        env = MagicMock(exchange="binance")
        def call(method, *args, **kwargs):
            if method == "has_pending_entries":
                return True, False, ""
            self.fail(method)
        with patch.object(aft, "selected_environment", return_value=env), patch.object(
            aft, "_call_exchange", side_effect=call,
        ):
            blocked, log = aft.binance_pending_risk_gate()
        self.assertFalse(blocked)
        self.assertEqual(log, "")

    def test_binance_uncertain_requires_maintenance_okx_does_not(self):
        with patch.object(aft, "selected_environment", return_value=MagicMock(exchange="binance")):
            self.assertTrue(aft._binance_submit_requires_maintenance("awaiting_fill"))
            self.assertTrue(aft._binance_submit_requires_maintenance("uncertain"))
            self.assertTrue(aft._binance_submit_requires_maintenance("protection_pending"))
            self.assertFalse(aft._binance_submit_requires_maintenance("accepted"))
        with patch.object(aft, "selected_environment", return_value=MagicMock(exchange="okx")):
            self.assertTrue(aft._binance_submit_requires_maintenance("awaiting_fill"))
            self.assertTrue(aft._binance_submit_requires_maintenance("protection_pending"))
            self.assertFalse(aft._binance_submit_requires_maintenance("uncertain"))
            self.assertFalse(aft._binance_submit_requires_maintenance("accepted"))



class AwaitingFillSubmitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = Path(self.temp.name) / "signal_journal.json"
        self.exchange = _Exchange()
        env = MagicMock(exchange="binance")
        for target, value in (
            ("SIGNAL_JOURNAL_FILE", str(self.journal)),
            ("get_exchange", lambda: self.exchange),
            ("selected_environment", lambda: env),
            ("notify_trade_open", None),
        ):
            patcher = patch.object(aft, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)


    def test_new_awaiting_fill_is_submitted_not_filled_or_protected(self):
        result = aft.submit_entry_with_confirmed_leverage(
            "BTC-USDT-SWAP", "buy", "long",
            {"name": "BTC", "price": 100.0, "sz": Decimal("10"), "risk_per_trade_usd": 15.0,
             "lotSz": Decimal("0.001"), "minSz": Decimal("0.001"),
             "tickSz": Decimal("0.1"), "minNotional": Decimal("5"),
             "bidPx": 100.0, "askPx": 100.0,
             "calculus": {"velocity": 0.3, "probability_theory": {"continuation_prob_pct": 70}}},
            {"entry_price": 100.0, "take_profit_price": 130.0, "stop_loss_price": 90.0},
            "test", "AI", 100.0, 100.0, 5, False, 0.0,
            {}, [], set(), 30.0, 1.0, 1,
        )
        self.assertEqual(result, "awaiting_fill")
        journal = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual(journal[0]["status"], "submitted")
        self.assertEqual(journal[0]["snapshot"]["protection_status"], "awaiting_fill")
        self.assertNotEqual(journal[0]["status"], "filled")
        self.assertNotEqual(journal[0]["snapshot"]["protection_status"], "protected")

    def test_filled_sl_only_is_protection_pending_not_new_unfilled(self):
        self.exchange.place_payload = {"ordId": "ord-filled", "protection_status": "sl_only"}
        actions: list[str] = []
        result = aft.submit_entry_with_confirmed_leverage(
            "BTC-USDT-SWAP", "buy", "long",
            {"name": "BTC", "price": 100.0, "sz": Decimal("10"), "risk_per_trade_usd": 15.0,
             "lotSz": Decimal("0.001"), "minSz": Decimal("0.001"),
             "tickSz": Decimal("0.1"), "minNotional": Decimal("5"),
             "bidPx": 100.0, "askPx": 100.0,
             "calculus": {"velocity": 0.3, "probability_theory": {"continuation_prob_pct": 70}}},
            {"entry_price": 100.0, "take_profit_price": 130.0, "stop_loss_price": 90.0},
            "test", "AI", 100.0, 100.0, 5, False, 0.0,
            {}, actions, set(), 30.0, 1.0, 1,
        )
        self.assertEqual(result, "protection_pending")
        journal = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual(journal[0]["snapshot"]["protection_status"], "sl_only")
        self.assertNotEqual(journal[0]["snapshot"]["protection_status"], "protected")
        self.assertNotEqual(journal[0]["snapshot"]["protection_status"], "awaiting_fill")
        blob = " ".join(actions)
        self.assertIn("sl_only", blob)
        self.assertIn("FILLED", blob)
        self.assertNotIn("待成交", blob)
        self.assertNotIn("NEW entry awaiting fill", blob)

    def test_unknown_binance_protection_status_is_not_protected(self):
        self.exchange.place_payload = {"ordId": "ord-x", "protection_status": "mystery"}
        result = aft.submit_entry_with_confirmed_leverage(
            "BTC-USDT-SWAP", "buy", "long",
            {"name": "BTC", "price": 100.0, "sz": Decimal("10"), "risk_per_trade_usd": 15.0,
             "lotSz": Decimal("0.001"), "minSz": Decimal("0.001"),
             "tickSz": Decimal("0.1"), "minNotional": Decimal("5"),
             "bidPx": 100.0, "askPx": 100.0,
             "calculus": {"velocity": 0.3, "probability_theory": {"continuation_prob_pct": 70}}},
            {"entry_price": 100.0, "take_profit_price": 130.0, "stop_loss_price": 90.0},
            "test", "AI", 100.0, 100.0, 5, False, 0.0,
            {}, [], set(), 30.0, 1.0, 1,
        )
        self.assertEqual(result, "protection_pending")
        journal = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual(journal[0]["snapshot"]["protection_status"], "mystery")
        self.assertNotEqual(journal[0]["snapshot"]["protection_status"], "protected")



class PendingCycleBlockingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(exchange_runtime.unfreeze_environment)

    def _cycle_env(self, stack: ExitStack) -> None:
        for key in ("LEDGER_JSON_FILE", "POSITION_TRACKER_FILE", "STOP_COOLDOWN_FILE",
                    "SIGNAL_JOURNAL_FILE", "TRADER_SLOT_FILE", "TRADING_STATE_FILE",
                    "AI_POSITION_MANAGEMENT_FILE", "CIRCUIT_BREAKER_FILE", "LOG_FILE"):
            stack.enter_context(patch.object(aft, key, str(self.root / key)))
        stack.enter_context(patch.object(aft, "DATA_DIR", str(self.root)))
        stack.enter_context(patch.object(aft, "TRADER_LOCK_FILE", str(self.root / ".ai_factor_trader.lock")))
        stack.enter_context(patch.object(exchange_runtime, "DATA_DIR", self.root))
        stack.enter_context(patch("scripts.okx_runtime._load_dotenv", return_value={
            "R20_EXCHANGE": "binance", "R20_BINANCE_ENV": "demo",
            "BINANCE_DEMO_API_KEY": "fixture-key", "BINANCE_DEMO_SECRET_KEY": "fixture-secret",
        }))
        stack.enter_context(patch.object(aft, "query_positions", return_value=(True, [], "")))
        stack.enter_context(patch.object(aft, "clean_orphan_protections", return_value=(True, "ok")))
        stack.enter_context(patch.object(aft, "is_circuit_breaker_active", return_value=(False, "")))
        stack.enter_context(patch.object(aft, "load_trackers", return_value={}))
        stack.enter_context(patch.object(aft, "save_trackers"))
        stack.enter_context(patch.object(aft, "prune_trackers", return_value=0))
        stack.enter_context(patch.object(aft, "evaluate_asset_signal", return_value=(0, "WAIT", [], "", "")))
        stack.enter_context(patch.object(aft, "MAX_CONCURRENT_POSITIONS", 6))
        stack.enter_context(patch.object(aft, "MAX_SAME_DIRECTION_POSITIONS", 3))
        stack.enter_context(patch.object(aft, "TARGET_INSTRUMENTS", [
            {"instId": "BTC-USDT-SWAP", "name": "BTC"},
            {"instId": "ETH-USDT-SWAP", "name": "ETH"},
        ]))
        def fetch(item, all_positions, usdt_available):
            return _factor(item["instId"], item["name"])
        stack.enter_context(patch.object(aft, "fetch_single_instrument_data", side_effect=fetch))

        def call(method, *args, **kwargs):
            if method == "open_orders":
                return True, [], ""
            if method == "balance":
                return True, [{"details": [{"ccy": "USDT", "availBal": "100"}]}], ""
            if method == "protection_orders":
                return True, [], ""
            return False, None, method
        stack.enter_context(patch.object(aft, "_call_exchange", side_effect=call))

    def test_pending_gate_blocks_news_and_model_before_calls(self):
        brain = MagicMock()
        stale = MagicMock(return_value=(True, "ok"))
        submit = MagicMock(return_value="accepted")
        orphan = MagicMock(return_value=(True, "ok"))
        trail = MagicMock()
        with ExitStack() as stack:
            self._cycle_env(stack)
            stack.enter_context(patch.object(
                aft, "binance_pending_risk_gate",
                return_value=(True, "[Trader] Pending: Binance has 1 pending entry awaiting fill or protection; new risk and paid analysis skipped"),
            ))
            stack.enter_context(patch.object(aft, "execute_batch_ai_brain_cycle", brain))
            stack.enter_context(patch.object(aft, "clean_stale_open_orders", stale))
            stack.enter_context(patch.object(aft, "submit_entry_with_confirmed_leverage", submit))
            stack.enter_context(patch.object(aft, "clean_orphan_protections", orphan))
            stack.enter_context(patch.object(aft, "manage_position_tp_and_trailing", trail))
            news = stack.enter_context(patch("scripts.ai_factor_trader.subprocess.run"))
            aft.execute_portfolio()
        brain.assert_not_called()
        stale.assert_not_called()
        submit.assert_not_called()
        news.assert_not_called()
        orphan.assert_not_called()
        trail.assert_not_called()


    def test_cleared_pending_allows_model_on_later_cycle(self):
        brain = MagicMock(return_value={})
        gate = MagicMock(side_effect=[
            (True, "[Trader] Pending: Binance has 1 pending entry awaiting fill or protection; new risk and paid analysis skipped"),
            (False, ""),
        ])
        with ExitStack() as stack:
            self._cycle_env(stack)
            stack.enter_context(patch.object(aft, "binance_pending_risk_gate", gate))
            stack.enter_context(patch.object(aft, "execute_batch_ai_brain_cycle", brain))
            stack.enter_context(patch.object(aft, "clean_stale_open_orders", return_value=(True, "ok")))
            stack.enter_context(patch("scripts.ai_factor_trader.subprocess.run"))
            aft.execute_portfolio()
            self.assertEqual(brain.call_count, 0)
            Path(aft.TRADER_SLOT_FILE).unlink(missing_ok=True)
            aft.execute_portfolio()
        self.assertEqual(brain.call_count, 1)

    def test_awaiting_fill_stops_remaining_entries(self):
        submit = MagicMock(return_value="awaiting_fill")
        brain_payload = {
            "BTC-USDT-SWAP": {"decision": {
                "action": "BUY_LONG", "confidence": 90, "summary_reason": "x",
                "entry_price": 100000, "take_profit_price": 110000, "stop_loss_price": 90000,
                "margin_usdt": 50, "leverage": 3,
            }},
            "ETH-USDT-SWAP": {"decision": {
                "action": "BUY_LONG", "confidence": 90, "summary_reason": "y",
                "entry_price": 3000, "take_profit_price": 3300, "stop_loss_price": 2700,
                "margin_usdt": 50, "leverage": 3,
            }},
        }
        with ExitStack() as stack:
            self._cycle_env(stack)
            stack.enter_context(patch.object(aft, "binance_pending_risk_gate", return_value=(False, "")))
            stack.enter_context(patch.object(aft, "execute_batch_ai_brain_cycle", return_value=brain_payload))
            stack.enter_context(patch.object(aft, "clean_stale_open_orders", return_value=(True, "ok")))
            stack.enter_context(patch.object(aft, "submit_entry_with_confirmed_leverage", submit))
            stack.enter_context(patch.object(aft, "execute_ai_position_management"))
            stack.enter_context(patch.object(aft, "MIN_ENTRY_CONFIDENCE", 50.0))
            stack.enter_context(patch("scripts.ai_factor_trader.subprocess.run"))
            aft.execute_portfolio()
        self.assertEqual(submit.call_count, 1)

    def test_binance_uncertain_stops_remaining_entries(self):
        submit = MagicMock(return_value="uncertain")
        brain_payload = {
            "BTC-USDT-SWAP": {"decision": {
                "action": "BUY_LONG", "confidence": 90, "summary_reason": "x",
                "entry_price": 100000, "take_profit_price": 110000, "stop_loss_price": 90000,
                "margin_usdt": 50, "leverage": 3,
            }},
            "ETH-USDT-SWAP": {"decision": {
                "action": "BUY_LONG", "confidence": 90, "summary_reason": "y",
                "entry_price": 3000, "take_profit_price": 3300, "stop_loss_price": 2700,
                "margin_usdt": 50, "leverage": 3,
            }},
        }
        with ExitStack() as stack:
            self._cycle_env(stack)
            stack.enter_context(patch.object(aft, "binance_pending_risk_gate", return_value=(False, "")))
            stack.enter_context(patch.object(aft, "execute_batch_ai_brain_cycle", return_value=brain_payload))
            stack.enter_context(patch.object(aft, "clean_stale_open_orders", return_value=(True, "ok")))
            stack.enter_context(patch.object(aft, "submit_entry_with_confirmed_leverage", submit))
            stack.enter_context(patch.object(aft, "execute_ai_position_management"))
            stack.enter_context(patch.object(aft, "MIN_ENTRY_CONFIDENCE", 50.0))
            news = stack.enter_context(patch("scripts.ai_factor_trader.subprocess.run"))
            aft.execute_portfolio()
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(news.call_count, 1)

    def test_protection_pending_stops_remaining_entries(self):
        submit = MagicMock(return_value="protection_pending")
        brain_payload = {
            "BTC-USDT-SWAP": {"decision": {
                "action": "BUY_LONG", "confidence": 90, "summary_reason": "x",
                "entry_price": 100000, "take_profit_price": 110000, "stop_loss_price": 90000,
                "margin_usdt": 50, "leverage": 3,
            }},
            "ETH-USDT-SWAP": {"decision": {
                "action": "BUY_LONG", "confidence": 90, "summary_reason": "y",
                "entry_price": 3000, "take_profit_price": 3300, "stop_loss_price": 2700,
                "margin_usdt": 50, "leverage": 3,
            }},
        }
        with ExitStack() as stack:
            self._cycle_env(stack)
            stack.enter_context(patch.object(aft, "binance_pending_risk_gate", return_value=(False, "")))
            stack.enter_context(patch.object(aft, "execute_batch_ai_brain_cycle", return_value=brain_payload))
            stack.enter_context(patch.object(aft, "clean_stale_open_orders", return_value=(True, "ok")))
            stack.enter_context(patch.object(aft, "submit_entry_with_confirmed_leverage", submit))
            stack.enter_context(patch.object(aft, "execute_ai_position_management"))
            stack.enter_context(patch.object(aft, "MIN_ENTRY_CONFIDENCE", 50.0))
            news = stack.enter_context(patch("scripts.ai_factor_trader.subprocess.run"))
            aft.execute_portfolio()
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(news.call_count, 1)




if __name__ == "__main__":
    unittest.main()

"""Offline trader boundaries: BASE qty, leverage confirm, protection legs, account state.
No network, no credentials, injectable fake exchange only.
"""
from __future__ import annotations
import ast
from contextlib import ExitStack

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import scripts.ai_factor_trader as aft
from fastapi import HTTPException
from r20_exchange import runtime as exchange_runtime

_APP_TREE = ast.parse((ROOT / "r20_backend" / "app.py").read_text(encoding="utf-8"))


class TraderConfigLockTests(unittest.TestCase):
    def test_configuration_and_all_account_cycles_share_one_lock(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            for key in ("LEDGER_JSON_FILE", "POSITION_TRACKER_FILE", "STOP_COOLDOWN_FILE",
                        "SIGNAL_JOURNAL_FILE", "TRADER_SLOT_FILE", "TRADING_STATE_FILE",
                        "AI_POSITION_MANAGEMENT_FILE"):
                stack.enter_context(patch.object(aft, key, str(root / key)))
            stack.enter_context(patch.object(aft, "DATA_DIR", str(root)))
            stack.enter_context(patch.object(aft, "TRADER_LOCK_FILE", str(root / ".ai_factor_trader.lock")))
            stack.enter_context(patch.object(exchange_runtime, "DATA_DIR", root))
            stack.enter_context(patch("scripts.okx_runtime._load_dotenv", return_value={
                "R20_EXCHANGE": "binance", "R20_BINANCE_ENV": "demo",
                "BINANCE_DEMO_API_KEY": "fixture-key", "BINANCE_DEMO_SECRET_KEY": "fixture-secret",
            }))
            namespace = {"DATA_DIR": root, "HTTPException": HTTPException}
            nodes = [node for node in _APP_TREE.body if isinstance(node, ast.FunctionDef) and
                     node.name in {"_acquire_trader_cycle_lock", "_release_trader_cycle_lock"}]
            exec(compile(ast.Module(body=nodes, type_ignores=[]), "app-cycle-lock", "exec"), namespace)
            acquire_config = namespace["_acquire_trader_cycle_lock"]
            release_config = namespace["_release_trader_cycle_lock"]
            ran = []

            @aft.single_trader_cycle
            def cycle():
                ran.append(exchange_runtime.selected_environment().exchange)
                with self.assertRaises(HTTPException) as blocked:
                    acquire_config()
                self.assertEqual(blocked.exception.status_code, 409)

            handle = acquire_config()
            try:
                cycle()
                self.assertEqual(ran, [])
            finally:
                release_config(handle)
            cycle()
            self.assertEqual(ran, ["binance"])
            handle = acquire_config()
            release_config(handle)


class FakeExchange:
    def __init__(self):
        self.calls = []
        self.instruments_data = [{
            "instId": "BTC-USDT-SWAP",
            "lotSz": "0.001",
            "minSz": "0.001",
            "tickSz": "0.1",
            "minNotional": "5",
            "ctVal": "1",
        }]
        self.protection_seq = None
        self.protection = []
        self.open_orders_data = []
        self.open_orders_error = None
        self.cancel_error = None
        self.place_error = None
        self.place_payload = {"ordId": "ord-1", "protection_mechanism": "paired_conditional", "protection_status": "protected"}
        self.leverage_payload = {"lever": "10"}
        self.ticker_data = {"instId": "BTC-USDT-SWAP", "last": "100000", "bidPx": "99999", "askPx": "100001"}

    def instruments(self, inst_id=None):
        self.calls.append(("instruments", inst_id))
        return list(self.instruments_data)

    def ticker(self, inst_id):
        return dict(self.ticker_data)

    def set_leverage(self, inst_id, leverage, pos_side="long"):
        self.calls.append(("set_leverage", inst_id, leverage, pos_side))
        if self.leverage_payload is None:
            return {}
        return dict(self.leverage_payload)

    def place_protected_limit_order(self, inst_id, side, pos_side, size, price, tp_px, sl_px):
        self.calls.append(("place_protected_limit_order", inst_id, side, pos_side, size, price, tp_px, sl_px))
        if self.place_error:
            raise self.place_error
        return dict(self.place_payload)

    def protection_orders(self, inst_id):
        self.calls.append(("protection_orders", inst_id))
        if self.protection_seq is not None:
            return self.protection_seq.pop(0)
        return list(self.protection)

    def place_protection(self, inst_id, pos_side, size, tp_px, sl_px):
        self.calls.append(("place_protection", inst_id, pos_side, size, tp_px, sl_px))
        return {"algoId": "prot-1"}

    def cancel_protection(self, inst_id, algo_id):
        self.calls.append(("cancel_protection", inst_id, algo_id))
        return {"algoId": algo_id}

    def open_orders(self, inst_id=None):
        self.calls.append(("open_orders", inst_id))
        if self.open_orders_error:
            raise RuntimeError(self.open_orders_error)
        return list(self.open_orders_data)

    def cancel_order(self, inst_id, order_id):
        self.calls.append(("cancel_order", inst_id, order_id))
        if self.cancel_error:
            raise RuntimeError(self.cancel_error)
        return {"ordId": order_id}


class PlanBaseQuantityTests(unittest.TestCase):
    def test_small_btc_margin_is_not_hundreds_of_btc(self):
        qty = aft.plan_base_quantity(
            price="100000",
            available_margin="100",
            leverage="1",
            lot_sz="0.001",
            min_sz="0.001",
            min_notional="5",
            planned_margin="100",
        )
        self.assertEqual(qty, Decimal("0.001"))
        self.assertLess(qty, Decimal("1"))
        self.assertEqual(qty * Decimal("100000"), Decimal("100"))

    def test_does_not_round_up_to_minimum_one_btc(self):
        qty = aft.plan_base_quantity(
            price="100000",
            available_margin="50",
            leverage="1",
            lot_sz="0.001",
            min_sz="0.001",
            min_notional="5",
            planned_margin="50",
        )
        self.assertEqual(qty, Decimal("0"))

    def test_floors_to_step_and_caps_by_available_margin(self):
        qty = aft.plan_base_quantity(
            price="100",
            available_margin="10",
            leverage="2",
            lot_sz="0.01",
            min_sz="0.01",
            min_notional="1",
            planned_margin="1000",
        )
        self.assertEqual(qty, Decimal("0.20"))

    def test_legacy_ctval_is_not_multiplied_into_base_qty(self):
        qty = aft.plan_base_quantity(
            price="100000",
            available_margin="100",
            leverage="10",
            lot_sz="0.001",
            min_sz="0.001",
            min_notional="5",
            planned_margin="100",
        )
        self.assertEqual(qty, Decimal("0.010"))
        self.assertNotEqual(qty, Decimal("1"))


class LeverageConfirmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = Path(self.temp.name) / "signal_journal.json"
        self.exchange = FakeExchange()
        self.exchange.leverage_payload = {"lever": "5"}
        for target, value in (
            ("SIGNAL_JOURNAL_FILE", str(self.journal)),
            ("get_exchange", lambda: self.exchange),
            ("notify_trade_open", None),
        ):
            patcher = patch.object(aft, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def submit(self, *, side="long", price=100.0, entry=100.0, tp=130.0, sl=90.0,
               available=100.0, planned=100.0, leverage=5, step="0.001", risk=15.0):
        return aft.submit_entry_with_confirmed_leverage(
            "BTC-USDT-SWAP", "buy" if side == "long" else "sell", side,
            {"name": "BTC", "price": price, "sz": Decimal("10"), "risk_per_trade_usd": risk,
             "lotSz": Decimal(step), "minSz": Decimal(step),
             "tickSz": Decimal("0.1"), "minNotional": Decimal("5"),
             "bidPx": price, "askPx": price,
             "calculus": {"velocity": 0.3, "probability_theory": {"continuation_prob_pct": 70}}},
            {"entry_price": entry, "take_profit_price": tp, "stop_loss_price": sl},
            "test", "AI", available, planned, leverage, False, 0.0,
            {}, [], set(), 30.0, 1.0, 1,
        )

    def test_old_btc_metadata_cannot_determine_altcoin_order_step_or_quantity(self):
        from scripts.instrument_pool import normalize_pool_item
        from r20_exchange.binance import BinanceExchange
        from tests.test_binance_exchange import BTC_INFO, FakeEnv, FakeSession, _perp_row

        transport = FakeSession()
        transport.on("GET", "/fapi/v1/exchangeInfo", {
            "symbols": [*BTC_INFO["symbols"], _perp_row(
                "DUSKUSDT", "DUSK", "0.0001", "0.0001", step="1", min_qty="1",
            )],
        })
        adapter = BinanceExchange(FakeEnv(), session=transport)
        row = normalize_pool_item({
            "instId": "DUSK-USDT-SWAP", "name": "BTC", "ccy": "BTC",
            "base_qty": 0.0001, "ctVal": 1, "tickSz": "0.10",
            "lotSz": "0.0001", "minSz": "0.0001", "minNotional": "50",
        })
        candles = [
            [str(index * 900000), "0.1300", "0.1319", "0.1299", "0.1309", "1000", "130.9", "130.9", "1"]
            for index in range(45, 0, -1)
        ]
        self.exchange.ticker_data = {
            "instId": "DUSK-USDT-SWAP", "last": "0.1309", "bidPx": "0.1309", "askPx": "0.1310",
        }
        with patch.object(self.exchange, "instruments", adapter.instruments), \
             patch.object(aft, "fetch_candles_direct", return_value=candles), \
             patch.object(aft, "load_adaptive_config", return_value={}), \
             patch.object(aft, "effective_risk_per_trade", return_value=2.0):
            factors = aft.fetch_single_instrument_data(row, [], 100.0)
            self.assertTrue(factors["market_data_valid"])
            result = aft.submit_entry_with_confirmed_leverage(
                "DUSK-USDT-SWAP", "buy", "long", factors,
                {"entry_price": 0.1309, "take_profit_price": 0.19, "stop_loss_price": 0.11},
                "test", "AI", 100.0, 10.0, 5, False, 0.0,
                {}, [], set(), 0.06, 0.02, row["precision"],
            )
        self.assertEqual(result, "accepted")
        order = next(call for call in self.exchange.calls if call[0] == "place_protected_limit_order")
        self.assertEqual(order[1], "DUSK-USDT-SWAP")
        self.assertEqual(order[4], Decimal("95"))
        self.assertEqual(order[5:8], (0.1309, 0.19, 0.11))
        self.assertLessEqual(order[4] * Decimal("0.0209"), Decimal("2"))

    def test_unconfirmed_leverage_blocks_place(self):
        self.exchange.leverage_payload = {}
        self.assertEqual(self.submit(), "rejected")
        self.assertFalse(any(c[0] == "place_protected_limit_order" for c in self.exchange.calls))
        self.assertFalse(self.journal.exists())

    def test_actual_limit_price_and_configured_margin_cap_bound_both_sides(self):
        for side, tp, sl in (("long", 202.0, 199.0), ("short", 198.0, 201.0)):
            with self.subTest(side=side):
                self.exchange.calls.clear()
                self.assertEqual(self.submit(side=side, entry=200.0, tp=tp, sl=sl, leverage=50), "accepted")
                order = next(c for c in self.exchange.calls if c[0] == "place_protected_limit_order")
                self.assertEqual(order[4], Decimal("0.500"))
                self.assertEqual(order[5], 200.0)
                self.assertEqual(next(c[2] for c in self.exchange.calls if c[0] == "set_leverage"), 5)
        journal = json.loads(self.journal.read_text(encoding="utf-8"))
        self.assertEqual([r["posSide"] for r in journal], ["long", "short"])
        self.assertTrue(all(r["order_id"] == "ord-1" and r["status"] == "submitted" for r in journal))
        self.assertEqual(journal[0]["snapshot"]["continuation_prob_pct"], 70)

    def test_actual_stop_distance_limits_loss_not_default_atr_distance(self):
        self.assertEqual(self.submit(entry=100.0, tp=160.0, sl=80.0), "accepted")
        order = next(c for c in self.exchange.calls if c[0] == "place_protected_limit_order")
        self.assertEqual(order[4], Decimal("0.100"))
        self.assertEqual(order[4] * Decimal("20"), Decimal("2"))

    def test_small_account_uses_fractional_base_and_unaffordable_order_is_not_inflated(self):
        args = dict(price=80000.0, entry=80000.0, tp=83000.0, sl=79000.0, step="0.0001")
        self.assertEqual(self.submit(available=20.0, **args), "accepted")
        order = next(c for c in self.exchange.calls if c[0] == "place_protected_limit_order")
        self.assertEqual(order[4], Decimal("0.0002"))
        before = self.journal.read_bytes()
        self.exchange.calls.clear()
        self.assertEqual(self.submit(available=1.0, **args), "rejected")
        self.assertFalse(any(c[0] == "place_protected_limit_order" for c in self.exchange.calls))
        self.assertEqual(self.journal.read_bytes(), before)

    def test_uncertain_submission_is_not_retried_or_recorded_as_entry_evidence(self):
        self.exchange.place_error = RuntimeError("upstream timeout")
        self.assertEqual(self.submit(), "uncertain")
        self.assertEqual(sum(c[0] == "place_protected_limit_order" for c in self.exchange.calls), 1)
        self.assertFalse(self.journal.exists())


class ProtectionCoverageTests(unittest.TestCase):
    def test_okx_oco_dual_leg_covers(self):
        orders = [{
            "state": "live", "posSide": "long", "ordType": "oco",
            "sz": "0.01", "tpTriggerPx": "106000", "slTriggerPx": "97000",
        }]
        coverage, close_all = aft._live_protection_coverage(orders, "long")
        self.assertEqual(coverage, Decimal("0.01"))
        self.assertFalse(close_all)
        ok, detail = self._ensure(orders)
        self.assertTrue(ok)
        self.assertIn("verified", detail)

    def test_binance_paired_conditional_close_all_covers_without_reduce_only(self):
        orders = [{
            "state": "live", "posSide": "long", "ordType": "paired_conditional",
            "closeAll": True, "tpTriggerPx": "106000", "slTriggerPx": "97000", "sz": "0",
        }]
        coverage, close_all = aft._live_protection_coverage(orders, "long")
        self.assertTrue(close_all)
        ok, _detail = self._ensure(orders)
        self.assertTrue(ok)

    def test_missing_leg_is_not_invented_coverage(self):
        orders = [{
            "state": "live", "posSide": "long", "ordType": "paired_conditional",
            "sz": "0.01", "tpTriggerPx": "106000",
        }]
        coverage, close_all = aft._live_protection_coverage(orders, "long")
        self.assertEqual(coverage, Decimal("0"))
        self.assertFalse(close_all)

    def _ensure(self, orders):
        exchange = FakeExchange()
        exchange.protection = orders
        with patch.object(aft, "get_exchange", return_value=exchange):
            return aft.ensure_cloud_position_protection("BTC-USDT-SWAP", "long", Decimal("0.01"), 106000, 97000)


class PendingAndUncertainTests(unittest.TestCase):
    def test_clean_stale_open_orders_fail_closed(self):
        exchange = FakeExchange()
        exchange.open_orders_error = "timeout"
        with patch.object(aft, "get_exchange", return_value=exchange):
            ok, detail = aft.clean_stale_open_orders()
        self.assertFalse(ok)
        self.assertIn("timeout", detail)

    def test_stale_cancel_failure_is_fail_closed(self):
        exchange = FakeExchange()
        exchange.open_orders_data = [{"instId": "SOL-USDT-SWAP", "ordId": "11", "state": "live", "cTime": "1"}]
        exchange.cancel_error = "rejected"
        with patch.object(aft, "get_exchange", return_value=exchange), patch.object(aft.time, "time", return_value=1000):
            ok, detail = aft.clean_stale_open_orders()
        self.assertFalse(ok)
        self.assertIn("rejected", detail)
        self.assertTrue(any(c[0] == "cancel_order" and c[1] == "SOL-USDT-SWAP" and c[2] == "11" for c in exchange.calls))

    def test_orphan_protection_cancelled_when_no_position(self):
        exchange = FakeExchange()
        exchange.protection = [{
            "state": "live", "posSide": "long", "ordType": "oco", "algoId": "g1",
            "tpTriggerPx": "1", "slTriggerPx": "2", "sz": "1",
        }]
        with patch.object(aft, "get_exchange", return_value=exchange), patch.object(aft, "TARGET_INSTRUMENTS", [{"instId": "BTC-USDT-SWAP"}]):
            ok, _detail = aft.clean_orphan_protections({})
        self.assertTrue(ok)
        self.assertTrue(any(c[0] == "cancel_protection" and c[2] == "g1" for c in exchange.calls))


class AccountStateIsolationTests(unittest.TestCase):
    def test_refresh_uses_state_path_not_legacy_data_root(self):
        with tempfile.TemporaryDirectory() as td:
            account_dir = Path(td) / "exchanges" / "binance" / "demo" / "abc"
            saved_paths = {
                key: getattr(aft, key) for key in (
                    "LEDGER_JSON_FILE", "POSITION_TRACKER_FILE", "STOP_COOLDOWN_FILE",
                    "SIGNAL_JOURNAL_FILE", "TRADER_LOCK_FILE", "TRADER_SLOT_FILE",
                    "TRADING_STATE_FILE", "AI_POSITION_MANAGEMENT_FILE",
                )
            }
            patcher = patch.multiple(aft, **saved_paths)
            patcher.start()
            self.addCleanup(patcher.stop)
            legacy = Path(td) / "legacy"
            legacy.mkdir()
            (legacy / "position_trackers.json").write_text(json.dumps({"LEGACY": 1}), encoding="utf-8")

            def fake_state_path(name, env=None):
                return account_dir / name

            with patch.object(aft, "state_path", side_effect=fake_state_path):
                aft.refresh_account_state_paths(MagicMock())
                self.assertEqual(Path(aft.POSITION_TRACKER_FILE), account_dir / "position_trackers.json")
                self.assertNotEqual(Path(aft.POSITION_TRACKER_FILE), legacy / "position_trackers.json")
                aft.save_trackers({"LIVE": 1})
                self.assertTrue((account_dir / "position_trackers.json").exists())
                self.assertEqual(json.loads((legacy / "position_trackers.json").read_text(encoding="utf-8")), {"LEGACY": 1})
                loaded = aft.load_trackers()
                self.assertEqual(loaded, {"LIVE": 1})
                self.assertNotIn("LEGACY", loaded)


class ScaleInMarginCapTests(unittest.TestCase):
    def test_effective_single_asset_margin_tightens_on_small_equity(self):
        self.assertEqual(aft.effective_single_asset_margin(100.0), 30.0)
        self.assertEqual(aft.effective_single_asset_margin(100000.0), aft.MAX_SINGLE_ASSET_MARGIN)

    def test_planned_entry_margin_prefers_ai_budget_else_notional_over_leverage(self):
        self.assertEqual(aft.planned_entry_margin(40.0, 1000.0, 10.0), 40.0)
        self.assertEqual(aft.planned_entry_margin(0.0, 1000.0, 10.0), 100.0)
        self.assertEqual(aft.planned_entry_margin(0.0, 1000.0, 0.0), 1000.0)

    def test_within_asset_margin_cap_blocks_over_equity_ratio(self):
        self.assertTrue(aft.within_asset_margin_cap(5.0, 20.0, 100.0))
        self.assertFalse(aft.within_asset_margin_cap(15.0, 20.0, 50.0))

    def test_remaining_asset_margin_shrinks_by_open_margin(self):
        self.assertEqual(aft.remaining_asset_margin(100.0, 10.0), 20.0)
        self.assertEqual(aft.remaining_asset_margin(100.0, 40.0), 0.0)



if __name__ == "__main__":
    unittest.main()

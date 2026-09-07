"""Offline trader boundaries: BASE qty, leverage confirm, protection legs, account state.
No network, no credentials, injectable fake exchange only.
"""
from __future__ import annotations

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
        self.place_payload = {"ordId": "ord-1", "protection_mechanism": "paired_conditional"}
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
    def test_unconfirmed_leverage_blocks_place(self):
        exchange = FakeExchange()
        exchange.leverage_payload = {}
        with patch.object(aft, "get_exchange", return_value=exchange), patch.object(aft, "selected_environment") as env:
            env.return_value = MagicMock(simulated=False)
            result = aft.submit_entry_with_confirmed_leverage(
                "BTC-USDT-SWAP", "buy", "long",
                {"name": "BTC", "price": 100000.0, "sz": Decimal("0.001"), "risk_per_trade_usd": 15,
                 "lotSz": Decimal("0.001"), "minSz": Decimal("0.001"), "tickSz": Decimal("0.1"), "minNotional": Decimal("5"),
                 "bidPx": 99999.0},
                {"entry_price": 100000.0, "take_profit_price": 103000.0, "stop_loss_price": 98500.0},
                "test", "🧠 AI", 100.0, 100.0, 10, False, 0.0, {}, [], set(), 3000.0, 1500.0, 1,
            )
        self.assertEqual(result, "rejected")
        self.assertFalse(any(c[0] == "place_protected_limit_order" for c in exchange.calls))
        self.assertTrue(any(c[0] == "set_leverage" for c in exchange.calls))

    def test_confirmed_leverage_then_place_uses_same_budget(self):
        exchange = FakeExchange()
        exchange.leverage_payload = {"lever": "10"}
        actions = []
        available = Decimal("100")
        planned_margin = Decimal("100")
        confirmed = Decimal("10")
        price = Decimal("100000")
        with patch.object(aft, "get_exchange", return_value=exchange), patch.object(aft, "selected_environment") as env:
            env.return_value = MagicMock(simulated=False)
            result = aft.submit_entry_with_confirmed_leverage(
                "BTC-USDT-SWAP", "buy", "long",
                {"name": "BTC", "price": float(price), "sz": Decimal("0.02"), "risk_per_trade_usd": 15,
                 "lotSz": Decimal("0.001"), "minSz": Decimal("0.001"), "tickSz": Decimal("0.1"), "minNotional": Decimal("5"),
                 "bidPx": 100000.0},
                {"entry_price": 100000.0, "take_profit_price": 106000.0, "stop_loss_price": 97000.0},
                "test", "🧠 AI", float(available), float(planned_margin), 10, False, 0.0, {}, actions, set(), 6000.0, 3000.0, 1,
            )
        self.assertEqual(result, "accepted")
        self.assertTrue(any(c[0] == "set_leverage" and c[2] == 10 for c in exchange.calls))
        place = [c for c in exchange.calls if c[0] == "place_protected_limit_order"]
        self.assertEqual(len(place), 1)
        qty = aft._as_decimal(place[0][4])
        notional = qty * price
        max_notional = min(planned_margin, available) * confirmed
        self.assertGreater(qty, Decimal("0"))
        self.assertLess(qty, Decimal("1"))
        self.assertLessEqual(notional, max_notional)
        self.assertGreater(notional, available)
        self.assertEqual(qty, aft.floor_to_step(qty, Decimal("0.001")))


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


if __name__ == "__main__":
    unittest.main()

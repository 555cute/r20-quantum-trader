"""Offline adapter-mock regressions for sync_cloud_algo_stop.

Covers OKX native OCO and Binance paired_conditional (long/short/net):
success with post-amend query confirm, empty/failed responses, DEMO not
auto-succeeding, and retryable tracker state after a failed sync.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from decimal import Decimal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import scripts.ai_factor_trader as aft


def _oco(pos_side="long", sl="2400.0", algo_id="algo_101", tp="2600.0"):
    return {
        "state": "live",
        "posSide": pos_side,
        "ordType": "oco",
        "algoId": algo_id,
        "tpTriggerPx": tp,
        "slTriggerPx": sl,
        "sz": "2",
    }


def _paired(pos_side="short", sl="2600.0", algo_id="grp1", tp="2400.0"):
    return {
        "state": "live",
        "posSide": pos_side,
        "ordType": "paired_conditional",
        "algoId": algo_id,
        "tpTriggerPx": tp,
        "slTriggerPx": sl,
        "closeAll": True,
    }


def _eth_factor(price=2500.0):
    return {
        "instId": "ETH-USDT-SWAP",
        "name": "ETH",
        "price": price,
        "atr": 20.0,
        "precision": 2,
        "ctVal": 0.1,
        "type": "crypto",
        "market_data_valid": True,
    }


class CloudStopExchange:
    def __init__(
        self,
        orders,
        *,
        amend_result=None,
        amend_error=None,
        confirm_orders=None,
        fail_amends=0,
        empty_amend=False,
    ):
        self.calls = []
        self.orders = [dict(row) for row in orders]
        self.amend_result = amend_result
        self.amend_error = amend_error
        self.confirm_orders = None if confirm_orders is None else [dict(row) for row in confirm_orders]
        self.fail_amends = fail_amends
        self.empty_amend = empty_amend

    def protection_orders(self, inst_id):
        self.calls.append(("protection_orders", inst_id))
        if self.confirm_orders is not None and any(c[0] == "amend_stop" for c in self.calls):
            return [dict(row) for row in self.confirm_orders]
        return [dict(row) for row in self.orders]

    def amend_stop(self, inst_id, algo_id, new_sl):
        self.calls.append(("amend_stop", inst_id, algo_id, new_sl))
        if self.fail_amends > 0:
            self.fail_amends -= 1
            raise RuntimeError("amend rejected")
        if self.amend_error is not None:
            raise self.amend_error
        if self.empty_amend:
            return {}
        if self.amend_result is None and self.confirm_orders is None:
            for row in self.orders:
                if str(row.get("algoId")) == str(algo_id):
                    row["slTriggerPx"] = str(new_sl)
            return {"algoId": algo_id, "slTriggerPx": str(new_sl)}
        if self.amend_result is None:
            return None
        return dict(self.amend_result) if isinstance(self.amend_result, dict) else self.amend_result


def _amend_count(exchange):
    return sum(1 for c in exchange.calls if c[0] == "amend_stop")


class CloudAlgoStopAdapterSyncTests(unittest.TestCase):
    def _sync(self, exchange, pos_side, new_sl, inst_id="ETH-USDT-SWAP"):
        with patch.object(aft, "get_exchange", return_value=exchange):
            return aft.sync_cloud_algo_stop(inst_id, pos_side, new_sl)


    def test_okx_oco_long_amend_is_confirmed_by_requery(self):
        exchange = CloudStopExchange([_oco(sl="2400.0")])
        self.assertTrue(self._sync(exchange, "long", 2505.0))
        self.assertEqual(_amend_count(exchange), 1)
        self.assertEqual(exchange.calls[0][0], "protection_orders")
        self.assertEqual(exchange.calls[-1][0], "protection_orders")
        self.assertEqual(exchange.orders[0]["slTriggerPx"], "2505.0")

    def test_okx_oco_idempotent_skips_amend(self):
        exchange = CloudStopExchange([_oco(sl="2505.0")])
        self.assertTrue(self._sync(exchange, "long", 2505.0))
        self.assertEqual(_amend_count(exchange), 0)
        self.assertEqual([c[0] for c in exchange.calls], ["protection_orders"])

    def test_binance_paired_conditional_short_amend_confirms(self):
        exchange = CloudStopExchange([_paired(pos_side="short", sl="2600.0")])
        self.assertTrue(self._sync(exchange, "short", 2495.0))
        self.assertEqual(_amend_count(exchange), 1)
        self.assertEqual(exchange.calls[1][1:], ("ETH-USDT-SWAP", "grp1", 2495.0))
        self.assertEqual(exchange.orders[0]["slTriggerPx"], "2495.0")

    def test_binance_net_protection_matches_long_request(self):
        exchange = CloudStopExchange([_paired(pos_side="net", sl="2400.0", tp="2600.0")])
        self.assertTrue(self._sync(exchange, "long", 2505.0))
        self.assertEqual(_amend_count(exchange), 1)

    def test_all_position_protections_must_reach_the_target(self):
        exchange = CloudStopExchange([_oco(sl="2400", algo_id="first"), _oco(sl="2450", algo_id="second")])
        self.assertTrue(self._sync(exchange, "long", 2505))
        self.assertEqual({Decimal(order["slTriggerPx"]) for order in exchange.orders}, {Decimal("2505")})

    def test_existing_tighter_cloud_stop_is_never_loosened(self):
        exchange = CloudStopExchange([_oco(sl="2510")])
        self.assertTrue(self._sync(exchange, "long", 2505))
        self.assertEqual(exchange.orders[0]["slTriggerPx"], "2510")
        self.assertEqual(_amend_count(exchange), 0)

    def test_low_price_stop_changes_are_not_hidden_by_fixed_tolerance(self):
        exchange = CloudStopExchange([_oco(sl="0.0000100", tp="0.0000200")])
        self.assertTrue(self._sync(exchange, "long", 0.0000105, "PEPE-USDT-SWAP"))
        self.assertEqual(Decimal(exchange.orders[0]["slTriggerPx"]), Decimal("0.0000105"))

    def test_one_confirmed_order_cannot_hide_another_unconfirmed_order(self):
        first, second = _oco(sl="2505", algo_id="first"), _oco(sl="2400", algo_id="second")
        exchange = CloudStopExchange([first, second], amend_result={"algoId": "second"}, confirm_orders=[first, second])
        self.assertFalse(self._sync(exchange, "long", 2505))

    def test_empty_protection_list_is_not_success(self):
        exchange = CloudStopExchange([])
        self.assertFalse(self._sync(exchange, "long", 2505.0))
        self.assertEqual(_amend_count(exchange), 0)

    def test_okx_empty_amend_response_is_not_success(self):
        exchange = CloudStopExchange([_oco(sl="2400.0")], empty_amend=True)
        self.assertFalse(self._sync(exchange, "long", 2505.0))
        self.assertEqual(exchange.orders[0]["slTriggerPx"], "2400.0")

    def test_binance_unconfirmed_requery_is_not_success(self):
        live = _paired(pos_side="long", sl="2400.0", tp="2600.0")
        exchange = CloudStopExchange(
            [live],
            amend_result={"algoId": "grp1"},
            confirm_orders=[dict(live)],
        )
        self.assertFalse(self._sync(exchange, "long", 2505.0))
        self.assertEqual(_amend_count(exchange), 1)

    def test_demo_environment_still_requires_live_confirmation(self):
        env = MagicMock()
        env.mode = "demo"
        env.simulated = True
        env.exchange = "binance"
        exchange = CloudStopExchange([])
        with patch.object(aft, "get_exchange", return_value=exchange), patch.object(
            aft, "selected_environment", return_value=env
        ):
            self.assertFalse(aft.sync_cloud_algo_stop("ETH-USDT-SWAP", "long", 2505.0))
        self.assertEqual(_amend_count(exchange), 0)

    def test_okx_failed_sync_keeps_protection_and_retries(self):
        exchange = CloudStopExchange([_oco(sl="2472.0")], fail_amends=1)
        f = _eth_factor(2535.0)
        curr_pos = {"pos": "2.0", "side": "long", "avgPx": "2500.0", "upl": 7.0}
        trackers = {
            "ETH-USDT-SWAP_long": {
                "instId": "ETH-USDT-SWAP",
                "name": "ETH",
                "side": "long",
                "entryPx": 2500.0,
                "entryTs": 1,
                "highWaterMark": 2500.0,
                "lowWaterMark": 2500.0,
                "trailingStopPx": 2472.0,
                "takeProfitPx": 2556.0,
                "stage_desc": "持有监控中",
                "currentSz": 2.0,
            }
        }
        with patch.object(aft, "get_exchange", return_value=exchange), patch.object(
            aft, "ensure_cloud_position_protection", return_value=(True, "verified")
        ):
            closed, _reason = aft.manage_position_tp_and_trailing(
                f, curr_pos, trackers, "2026-09-07 10:15:00", []
            )
            self.assertFalse(closed)
            t = trackers["ETH-USDT-SWAP_long"]
            self.assertGreaterEqual(t["trailingStopPx"], 2505.0)
            self.assertFalse(t.get("cloudStopSynced"))
            self.assertEqual(t.get("pendingCloudStopPx"), t["trailingStopPx"])
            self.assertEqual(exchange.orders[0]["slTriggerPx"], "2472.0")
            self.assertEqual(_amend_count(exchange), 1)

            closed, _reason = aft.manage_position_tp_and_trailing(
                f, curr_pos, trackers, "2026-09-07 10:30:00", []
            )
        self.assertFalse(closed)
        self.assertTrue(t.get("cloudStopSynced"))
        self.assertNotIn("pendingCloudStopPx", t)
        self.assertEqual(_amend_count(exchange), 2)
        self.assertEqual(float(exchange.orders[0]["slTriggerPx"]), t["trailingStopPx"])

    def test_binance_failed_sync_keeps_protection_and_retries_short(self):
        exchange = CloudStopExchange(
            [_paired(pos_side="short", sl="2528.0", tp="2444.0")],
            fail_amends=1,
        )
        f = _eth_factor(2465.0)
        curr_pos = {"pos": "2.0", "side": "short", "avgPx": "2500.0", "upl": 7.0}
        trackers = {
            "ETH-USDT-SWAP_short": {
                "instId": "ETH-USDT-SWAP",
                "name": "ETH",
                "side": "short",
                "entryPx": 2500.0,
                "entryTs": 1,
                "highWaterMark": 2500.0,
                "lowWaterMark": 2500.0,
                "trailingStopPx": 2528.0,
                "takeProfitPx": 2444.0,
                "stage_desc": "持有监控中",
                "currentSz": 2.0,
            }
        }
        with patch.object(aft, "get_exchange", return_value=exchange), patch.object(
            aft, "ensure_cloud_position_protection", return_value=(True, "verified")
        ):
            closed, _reason = aft.manage_position_tp_and_trailing(
                f, curr_pos, trackers, "2026-09-07 10:15:00", []
            )
            self.assertFalse(closed)
            t = trackers["ETH-USDT-SWAP_short"]
            self.assertLessEqual(t["trailingStopPx"], 2495.0)
            self.assertFalse(t.get("cloudStopSynced"))
            self.assertEqual(t.get("pendingCloudStopPx"), t["trailingStopPx"])
            self.assertEqual(exchange.orders[0]["slTriggerPx"], "2528.0")
            self.assertEqual(_amend_count(exchange), 1)

            closed, _reason = aft.manage_position_tp_and_trailing(
                f, curr_pos, trackers, "2026-09-07 10:30:00", []
            )
        self.assertFalse(closed)
        self.assertTrue(t.get("cloudStopSynced"))
        self.assertNotIn("pendingCloudStopPx", t)
        self.assertEqual(_amend_count(exchange), 2)
        self.assertEqual(float(exchange.orders[0]["slTriggerPx"]), t["trailingStopPx"])


if __name__ == "__main__":
    unittest.main()

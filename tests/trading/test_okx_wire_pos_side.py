#!/usr/bin/env python3
"""Wire-payload tests: logical side -> OKX posSide (A–J). No network."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts.okx_pos_mode import (
    algo_order_matches_logical_side,
    get_position_mode,
    logical_side_from_position,
    normalize_trader_position,
    resolve_wire_pos_side,
    wire_pos_side,
)
from scripts.trader.order_submit import submit_protected_limit_order
from scripts.trader import cloud_protection, cycle_stages, position_mgmt, scale_out, venue_query


class TestResolveWire(unittest.TestCase):
    def test_net_open_long_omits(self):
        self.assertIsNone(resolve_wire_pos_side("long", "net_mode", endpoint="order"))

    def test_net_open_short_omits(self):
        self.assertIsNone(resolve_wire_pos_side("short", "net_mode", endpoint="order"))

    def test_hedge_open_long(self):
        self.assertEqual(resolve_wire_pos_side("long", "long_short_mode", endpoint="order"), "long")

    def test_hedge_open_short(self):
        self.assertEqual(resolve_wire_pos_side("short", "long_short_mode", endpoint="order"), "short")

    def test_net_set_leverage_omits(self):
        self.assertIsNone(resolve_wire_pos_side("long", "net_mode", endpoint="set_leverage"))

    def test_hedge_set_leverage(self):
        self.assertEqual(resolve_wire_pos_side("short", "long_short_mode", endpoint="set_leverage"), "short")

    def test_net_algo_uses_net(self):
        self.assertEqual(resolve_wire_pos_side("long", "net_mode", endpoint="algo"), "net")
        self.assertEqual(resolve_wire_pos_side("short", "net_mode", endpoint="place_algo_oco"), "net")

    def test_hedge_algo(self):
        self.assertEqual(resolve_wire_pos_side("long", "long_short_mode", endpoint="algo"), "long")

    def test_net_close_uses_net(self):
        self.assertEqual(resolve_wire_pos_side("long", "net_mode", endpoint="close"), "net")

    def test_hedge_close(self):
        self.assertEqual(resolve_wire_pos_side("long", "long_short_mode", endpoint="close"), "long")
        self.assertEqual(resolve_wire_pos_side("short", "long_short_mode", endpoint="close"), "short")

    def test_fail_closed_unknown_mode(self):
        with self.assertRaises(ValueError):
            resolve_wire_pos_side("long", "whatever", endpoint="order")
        with self.assertRaises(ValueError):
            resolve_wire_pos_side("long", None, endpoint="order")

    def test_never_long_short_in_net(self):
        for ep in ("order", "set_leverage", "algo", "close", "place_algo_oco"):
            w = resolve_wire_pos_side("long", "net_mode", endpoint=ep)
            self.assertNotIn(w, ("long", "short"))
            w = resolve_wire_pos_side("short", "net_mode", endpoint=ep)
            self.assertNotIn(w, ("long", "short"))


class TestOkxRestPayload(unittest.TestCase):
    def test_place_order_omits_none(self):
        from scripts import okx_rest

        captured = {}

        def fake_request(method, path, params, env=None):
            captured["params"] = params
            return [{"ordId": "1"}]

        with patch.object(okx_rest, "request", fake_request):
            okx_rest.place_order("BTC-USDT-SWAP", "buy", "1", pos_side=None, attach_tp=1, attach_sl=2)
        self.assertNotIn("posSide", captured["params"])

    def test_place_order_hedge(self):
        from scripts import okx_rest

        captured = {}

        def fake_request(method, path, params, env=None):
            captured["params"] = params
            return [{"ordId": "1"}]

        with patch.object(okx_rest, "request", fake_request):
            okx_rest.place_order("BTC-USDT-SWAP", "buy", "1", pos_side="long")
        self.assertEqual(captured["params"].get("posSide"), "long")

    def test_set_leverage_omits_none(self):
        from scripts import okx_rest

        captured = {}

        def fake_request(method, path, params, env=None):
            captured["params"] = params
            return []

        with patch.object(okx_rest, "request", fake_request):
            okx_rest.set_leverage("BTC-USDT-SWAP", 3, pos_side=None)
        self.assertNotIn("posSide", captured["params"])

    def test_place_algo_oco_omits_none(self):
        from scripts import okx_rest

        captured = {}

        def fake_request(method, path, params, env=None):
            captured["params"] = params
            return []

        with patch.object(okx_rest, "request", fake_request):
            okx_rest.place_algo_oco(
                "BTC-USDT-SWAP", "sell", "1", pos_side=None,
                tp_trigger_px=1, sl_trigger_px=2,
            )
        self.assertNotIn("posSide", captured["params"])

    def test_close_position_net(self):
        from scripts import okx_rest

        captured = {}

        def fake_request(method, path, params, env=None):
            captured["params"] = params
            return []

        with patch.object(okx_rest, "request", fake_request):
            okx_rest.close_position("BTC-USDT-SWAP", "net")
        self.assertEqual(captured["params"].get("posSide"), "net")


class TestOrderSubmitNet(unittest.TestCase):
    def _run(self, logical, mock_wire, expect_wire):
        mock_rest = MagicMock()
        mock_rest.place_order.return_value = [{"ordId": "ord_ok"}]
        env = SimpleNamespace(simulated=True, mode="demo")

        def route(*a, **k):
            return {"ok": True, "reservation": object(), "venue": "okx"}

        with patch("scripts.okx_pos_mode.wire_pos_side", mock_wire):
            ok, order_id = submit_protected_limit_order(
                "BTC-USDT-SWAP",
                "buy" if logical == "long" else "sell",
                logical,
                0.01,
                100.0,
                120.0 if logical == "long" else 80.0,
                90.0 if logical == "long" else 110.0,
                venue_ctx={"margin_usdt": 10, "leverage": 3, "notional_usdt": 1},
                confirm_signal_reservation=lambda *a, **k: None,
                record_open_intent=lambda *a, **k: None,
                release_signal_reservation=lambda *a, **k: None,
                route_and_reserve_signal=route,
                MAX_LEVERAGE=20,
                MIN_LEVERAGE=1,
                canonical_base=lambda x: "BTC",
                current_environment=lambda: env,
                fetch_ticker=lambda inst: {"last": 100.0},
                okx_rest=mock_rest,
                venue_registry=SimpleNamespace(native_symbol_pure=lambda b, v: "BTC-USDT-SWAP"),
            )
        self.assertTrue(ok, order_id)
        kwargs = mock_rest.place_order.call_args.kwargs
        self.assertEqual(kwargs.get("pos_side"), expect_wire)
        self.assertNotIn(kwargs.get("pos_side"), ("long", "short") if expect_wire is None else ("never",))
        # set_leverage must use same wire
        if mock_rest.set_leverage.called:
            self.assertEqual(mock_rest.set_leverage.call_args.kwargs.get("pos_side"), expect_wire)

    def test_net_long_wire_omitted(self):
        self._run("long", lambda *a, **k: None, None)

    def test_net_short_wire_omitted(self):
        self._run("short", lambda *a, **k: None, None)

    def test_hedge_long(self):
        self._run("long", lambda *a, **k: "long", "long")

    def test_hedge_short(self):
        self._run("short", lambda *a, **k: "short", "short")

    def test_fail_closed_when_posmode_unknown(self):
        mock_rest = MagicMock()
        env = SimpleNamespace(simulated=True, mode="demo")

        def route(*a, **k):
            return {"ok": True, "reservation": object(), "venue": "okx"}

        with patch("scripts.okx_pos_mode.wire_pos_side", side_effect=ValueError("no posMode")):
            ok, reason = submit_protected_limit_order(
                "BTC-USDT-SWAP", "buy", "long", 0.01, 100.0, 120.0, 90.0,
                venue_ctx={},
                confirm_signal_reservation=lambda *a, **k: None,
                record_open_intent=lambda *a, **k: None,
                release_signal_reservation=lambda *a, **k: None,
                route_and_reserve_signal=route,
                MAX_LEVERAGE=20, MIN_LEVERAGE=1,
                canonical_base=lambda x: "BTC",
                current_environment=lambda: env,
                fetch_ticker=lambda inst: {"last": 100.0},
                okx_rest=mock_rest,
                venue_registry=SimpleNamespace(native_symbol_pure=lambda b, v: "BTC-USDT-SWAP"),
            )
        self.assertFalse(ok)
        self.assertIn("posMode", reason)
        mock_rest.place_order.assert_not_called()


class TestCloudOcoNet(unittest.TestCase):
    def _coverage(self, orders, pos_side, **kw):
        fo = kw.get('_float_or_zero') or (lambda x: float(x or 0))
        cov = 0.0
        close_side = "sell" if pos_side == "long" else "buy"
        for order in orders:
            if str(order.get("state", "live")).lower() not in {"live", "effective"}:
                continue
            if str(order.get("posSide", "net")).lower() not in {pos_side, "net"}:
                continue
            if str(order.get("side", close_side)).lower() != close_side:
                continue
            if not order.get("tpTriggerPx") or not order.get("slTriggerPx"):
                continue
            if str(order.get("reduceOnly", "true")).lower() not in {"true", "1", "yes"}:
                continue
            cov += fo(order.get("sz") or order.get("actualSz"))
        return cov

    def _run(self, wire, logical="long"):
        mock_rest = MagicMock()
        state = {"placed": False}

        def pending(*a, **k):
            if not state["placed"]:
                return []
            return [{
                "state": "live",
                "posSide": wire or "net",
                "side": "sell" if logical == "long" else "buy",
                "tpTriggerPx": "1",
                "slTriggerPx": "2",
                "reduceOnly": "true",
                "sz": "1",
            }]

        def place(*a, **k):
            state["placed"] = True
            return []

        mock_rest.pending_algo_orders.side_effect = pending
        mock_rest.place_algo_oco.side_effect = place
        close_side = "sell" if logical == "long" else "buy"
        with patch("scripts.okx_pos_mode.wire_pos_side", lambda *a, **k: wire):
            ok, msg = cloud_protection.ensure_cloud_position_protection(
                "BTC-USDT-SWAP", logical, 1.0, 110.0, 90.0,
                okx_rest=mock_rest,
                _live_oco_coverage=self._coverage,
            )
        self.assertTrue(ok, msg)
        self.assertTrue(mock_rest.place_algo_oco.called)
        args = mock_rest.place_algo_oco.call_args
        self.assertEqual(args.args[1], close_side)
        self.assertEqual(args.kwargs.get("pos_side"), wire)
        return args.kwargs.get("pos_side")

    def test_net_oco_uses_net_not_long(self):
        w = self._run("net", "long")
        self.assertNotIn(w, ("long", "short"))

    def test_hedge_oco_long(self):
        self.assertEqual(self._run("long", "long"), "long")


class TestScaleOutNet(unittest.TestCase):
    def _run(self, logical):
        is_long = logical == "long"
        f = {
            "instId": "BTC-USDT-SWAP", "name": "BTC",
            "price": 120.0 if is_long else 80.0,
            "atr": 5.0, "precision": 1, "ctVal": 1.0,
            "minSz": 1.0, "market_data_valid": True,
        }
        position = {
            "side": logical, "posSide": logical,
            "avgPx": 100.0, "pos": 10.0, "venue": "okx",
        }
        trackers = {
            f"BTC-USDT-SWAP_{logical}": {
                "initialSz": 10.0, "currentSz": 10.0,
                "takeProfitPx": 140.0 if is_long else 60.0,
                "trailingStopPx": 90.0 if is_long else 110.0,
                "scale_out_phase": 0, "scale_count": 0,
            }
        }
        mock_rest = MagicMock()
        mock_rest.place_order.return_value = [{"ordId": "scale-1"}]
        mock_rest.pending_algo_orders.return_value = []
        ensure_oco = MagicMock()

        with patch("scripts.okx_pos_mode.wire_pos_side", lambda *a, **k: None):
            ok, reason = scale_out.execute_scale_out_if_eligible(
                f, position, trackers, "2026-09-23 12:00:00", [],
                okx_rest=mock_rest,
                ensure_cloud_position_protection=ensure_oco,
            )

        self.assertTrue(ok, reason)
        call = mock_rest.place_order.call_args
        self.assertEqual(call.args[1], "sell" if is_long else "buy")
        self.assertGreater(float(call.args[2]), 0.0)
        self.assertIsNone(call.kwargs.get("pos_side"))
        self.assertTrue(call.kwargs.get("reduce_only"))
        self.assertEqual(call.kwargs.get("ord_type"), "market")

    def test_net_long_reduce_omits_posside(self):
        self._run("long")

    def test_net_short_reduce_omits_posside(self):
        self._run("short")



class TestTraderPositionNormalization(unittest.TestCase):
    def test_raw_position_mapping(self):
        cases = (
            ({"posSide": "net", "pos": "2.5"}, "long", 2.5, "net", 2.5),
            ({"posSide": "net", "pos": "-3.5"}, "short", 3.5, "net", -3.5),
            ({"posSide": "long", "pos": "4"}, "long", 4.0, "long", 4.0),
            ({"posSide": "short", "pos": "5"}, "short", 5.0, "short", 5.0),
        )
        for raw, logical, size, wire, signed in cases:
            with self.subTest(raw=raw):
                row = normalize_trader_position(raw)
                self.assertEqual(row["posSide"], logical)
                self.assertEqual(row["side"], logical)
                self.assertEqual(row["pos"], size)
                self.assertEqual(row["wirePosSide"], wire)
                self.assertEqual(row["signedPos"], signed)

    def test_logical_side_net_zero_and_invalid_fail_closed(self):
        self.assertEqual(logical_side_from_position("net", 0), "net")
        with self.assertRaises(ValueError):
            logical_side_from_position("mystery", 1)

    def test_trader_facade_normalizes_net_long_and_short(self):
        import scripts.ai_factor_trader as aft

        for signed, logical in (("7.0", "long"), ("-8.0", "short")):
            fake = SimpleNamespace(positions=lambda **kw: [
                {"instId": "BTC-USDT-SWAP", "posSide": "net", "pos": signed}
            ])
            with self.subTest(signed=signed), patch.object(aft, "okx_rest", fake):
                ok, rows, error = aft.query_positions()
                self.assertTrue(ok, error)
                self.assertEqual(rows[0]["posSide"], logical)
                self.assertEqual(rows[0]["pos"], abs(float(signed)))


class TestTraderCycleNetCounts(unittest.TestCase):
    def _run(self, signed):
        import scripts.ai_factor_trader as aft

        fake = SimpleNamespace(
            positions=lambda **kw: [{
                "instId": "BTC-USDT-SWAP", "posSide": "net", "pos": str(signed),
                "avgPx": "100", "markPx": "101",
            }],
            pending_orders=lambda *a, **k: [],
            balances=lambda *a, **k: [{"details": [{"ccy": "USDT", "availBal": "1000"}]}],
        )
        with patch.object(aft, "okx_rest", fake):
            return cycle_stages.fetch_positions_and_reconcile(
                entries_blocked=False,
                _BROKEN_VENUES=set(),
                collect_pending_inst_ids=lambda **k: (set(), 0, 0),
                current_environment=lambda: SimpleNamespace(mode="demo", simulated=True),
                fetch_other_venue_positions=lambda env: (True, {}, ""),
                load_instruments=lambda: [],
                okx_rest=fake,
                query_positions=aft.query_positions,
                reconcile_reservation_ledger=lambda *a, **k: None,
                venue_execution_ready=lambda *a, **k: False,
                venue_registry=SimpleNamespace(),
            )

    def test_net_long_is_counted(self):
        got = self._run(2.0)
        self.assertEqual(got[1], 1)
        self.assertEqual(got[4], 1)
        self.assertEqual(got[10], 0)
        self.assertEqual(got[2][0]["posSide"], "long")
        self.assertEqual(got[2][0]["pos"], 2.0)

    def test_net_short_is_counted(self):
        got = self._run(-2.0)
        self.assertEqual(got[1], 1)
        self.assertEqual(got[4], 0)
        self.assertEqual(got[10], 1)
        self.assertEqual(got[2][0]["posSide"], "short")
        self.assertEqual(got[2][0]["pos"], 2.0)


class TestNetAlgoMatching(unittest.TestCase):
    def test_net_algo_side_disambiguates_direction(self):
        self.assertTrue(algo_order_matches_logical_side(
            {"posSide": "net", "side": "sell"}, "long"))
        self.assertFalse(algo_order_matches_logical_side(
            {"posSide": "net", "side": "buy"}, "long"))
        self.assertTrue(algo_order_matches_logical_side(
            {"posSide": "net", "side": "buy"}, "short"))
        self.assertFalse(algo_order_matches_logical_side(
            {"posSide": "net", "side": "sell"}, "short"))

    def test_cloud_ratchet_finds_net_algo(self):
        for logical, close_side in (("long", "sell"), ("short", "buy")):
            rest = MagicMock()
            rest.pending_algo_orders.return_value = [{
                "state": "live", "posSide": "net", "side": close_side,
                "slTriggerPx": "90", "algoId": f"a-{logical}",
            }]
            with self.subTest(logical=logical):
                ok = cloud_protection.sync_cloud_algo_stop(
                    "BTC-USDT-SWAP", logical, 95.0, okx_rest=rest)
                self.assertTrue(ok)
                rest.amend_algo_sl.assert_called_once_with(
                    f"a-{logical}", 95.0, inst_id="BTC-USDT-SWAP",
                    new_sl_ord_px="-1")

    def test_ai_update_sl_finds_net_algo(self):
        for logical, close_side, mark, new_sl in (
            ("long", "sell", 120.0, 116.0),
            ("short", "buy", 80.0, 84.0),
        ):
            with self.subTest(logical=logical), tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, "position_mgmt.json")
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump({
                        "timestamp": int(time.time()),
                        "instructions": [{
                            "instId": "BTC-USDT-SWAP", "action": "UPDATE_SL",
                            "confidence": 90, "suggested_sl_price": new_sl,
                            "reason": "tighten",
                        }],
                    }, handle)
                rest = MagicMock()
                rest.pending_algo_orders.return_value = [{
                    "state": "live", "posSide": "net", "side": close_side,
                    "slTriggerPx": "90", "algoId": f"pm-{logical}",
                }]
                actions = []
                trackers = {
                    f"BTC-USDT-SWAP_{logical}": {"trailingStopPx": 90.0}
                }
                position_mgmt.execute_ai_position_management(
                    {"BTC-USDT-SWAP": {
                        "instId": "BTC-USDT-SWAP", "posSide": logical,
                        "side": logical, "pos": 1.0, "avgPx": 100.0,
                        "markPx": mark, "atr_1h": 2.0, "venue": "okx",
                    }},
                    trackers,
                    "2026-09-23 12:00:00",
                    actions,
                    ai_position_management_file=path,
                    ai_tightens_stop=lambda *a, **k: True,
                    close_position_confirmed=lambda *a, **k: (True, "ok"),
                    okx_rest=rest,
                    venue_registry=None,
                    current_environment=None,
                    amend_venue_stop_loss=None,
                )
                rest.amend_algo_sl.assert_called_once_with(
                    f"pm-{logical}", new_sl, inst_id="BTC-USDT-SWAP",
                    new_sl_ord_px="-1")
                self.assertTrue(any("云端止损收紧至" in item for item in actions))


class TestCloseNet(unittest.TestCase):
    def test_net_close_net_posside(self):
        mock_rest = MagicMock()
        mock_rest.pending_orders.return_value = []
        mock_rest.close_position.return_value = []

        def query_positions():
            return True, [{"instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "0"}], ""

        with patch("scripts.okx_pos_mode.wire_pos_side", lambda *a, **k: "net"):
            ok, msg = venue_query.close_position_confirmed(
                "BTC-USDT-SWAP", "long", 1.0, venue="okx",
                okx_rest=mock_rest,
                current_environment=lambda: SimpleNamespace(mode="demo"),
                query_positions=query_positions,
                fetch_other_venue_positions=lambda *a, **k: (False, {}, ""),
            )
        self.assertTrue(ok, msg)
        args = mock_rest.close_position.call_args.args
        self.assertEqual(args[0], "BTC-USDT-SWAP")
        self.assertEqual(args[1], "net")

    def test_hedge_close_long(self):
        mock_rest = MagicMock()
        mock_rest.pending_orders.return_value = []
        mock_rest.close_position.return_value = []

        def query_positions():
            return True, [{"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "0"}], ""

        with patch("scripts.okx_pos_mode.wire_pos_side", lambda *a, **k: "long"):
            ok, msg = venue_query.close_position_confirmed(
                "BTC-USDT-SWAP", "long", 1.0, venue="okx",
                okx_rest=mock_rest,
                current_environment=lambda: SimpleNamespace(mode="demo"),
                query_positions=query_positions,
                fetch_other_venue_positions=lambda *a, **k: (False, {}, ""),
            )
        self.assertTrue(ok, msg)
        self.assertEqual(mock_rest.close_position.call_args.args[1], "long")


if __name__ == "__main__":
    unittest.main()

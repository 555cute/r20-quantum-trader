"""Offline pending-entry lifecycle regressions. Credentials-free injected transport."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from r20_exchange.binance import BinanceExchange
from r20_exchange.binance_pending import pending_state_path
from tests.test_binance_exchange import (
    ENTRY_ID,
    FakeEnv,
    FakeResponse,
    FakeSession,
    GROUP,
    INST,
    LONG_POS,
    SL_ID,
    TP_ID,
    _calls,
    install_defaults,
)

CL_ID = f"R20G{GROUP}CL"


class BinancePendingEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = FakeSession()
        self.env = FakeEnv()
        self._tmp = tempfile.TemporaryDirectory()
        self.data_patch = patch("r20_exchange.runtime.DATA_DIR", Path(self._tmp.name))
        self.data_patch.start()
        self.hex_patch = patch("r20_exchange.binance.secrets.token_hex", return_value=GROUP)
        self.hex_patch.start()

    def tearDown(self) -> None:
        self.hex_patch.stop()
        self.data_patch.stop()
        self._tmp.cleanup()

    def _exchange(self) -> BinanceExchange:
        return BinanceExchange(self.env, session=self.session)

    def test_has_pending_entries_does_not_touch_transport(self):
        ex = self._exchange()
        self.assertFalse(ex.has_pending_entries())
        self.assertEqual(self.session.calls, [])

    def test_corrupt_store_fails_closed_without_new_entry(self):
        install_defaults(self.session, hedge=True)
        path = pending_state_path(self.env)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not-json", encoding="utf-8")
        ex = self._exchange()
        self.assertTrue(ex.has_pending_entries())
        result = ex.reconcile_pending_entries()
        self.assertTrue(result["blocked"])
        self.assertTrue(result["errors"])
        with self.assertRaises(RuntimeError):
            ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(_calls(self.session, "POST", "/fapi/v1/order"), [])

    def test_restart_reuses_client_id_and_refuses_second_entry(self):
        install_defaults(self.session, hedge=True)
        self.session.on("POST", "/fapi/v1/order", {
            "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
            "positionSide": "LONG", "origQty": "0.001", "executedQty": "0", "price": "50000",
            "avgPrice": "0", "status": "NEW", "reduceOnly": False, "time": 1, "updateTime": 1,
        })
        first = self._exchange()
        result = first.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(result["protection_status"], "awaiting_fill")
        self.assertEqual(result["clOrdId"], ENTRY_ID)
        restarted = self._exchange()
        self.assertTrue(restarted.has_pending_entries())
        self.session.on("GET", "/fapi/v1/order", {
            "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
            "positionSide": "LONG", "origQty": "0.001", "executedQty": "0", "price": "50000",
            "avgPrice": "0", "status": "NEW", "reduceOnly": False, "time": 1, "updateTime": 1,
        })
        rec = restarted.reconcile_pending_entries()
        self.assertEqual(rec["pending"], 1)
        self.assertTrue(rec["blocked"])
        self.assertEqual(len(_calls(self.session, "POST", "/fapi/v1/order")), 1)
        with self.assertRaises(RuntimeError):
            restarted.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(len(_calls(self.session, "POST", "/fapi/v1/order")), 1)
        self.assertEqual(_calls(self.session, "POST", "/fapi/v1/algoOrder"), [])

    def test_unknown_missing_order_is_not_resent(self):
        install_defaults(self.session, hedge=True)
        posts = {"n": 0}

        def post_order(_call):
            posts["n"] += 1
            raise TimeoutError("timed out")

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("GET", "/fapi/v1/order", lambda _c: FakeResponse({"code": -2013, "msg": "Order does not exist."}))
        ex = self._exchange()
        with self.assertRaises(RuntimeError):
            ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(posts["n"], 1)
        rec = ex.reconcile_pending_entries()
        self.assertTrue(rec["blocked"])
        self.assertTrue(any("refusing to resend" in item for item in rec["errors"]))
        with self.assertRaises(RuntimeError):
            ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(posts["n"], 1)

    def test_partial_then_terminal_extra_fills_then_protect(self):
        state = install_defaults(self.session, hedge=True)
        state["filled"] = "0.0005"
        order_state = {"status": "PARTIALLY_FILLED", "filled": "0.0005"}

        def post_order(call):
            if call["query"].get("type") == "MARKET":
                raise AssertionError("flatten is not allowed before terminal protection")
            return {
                "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
                "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.0005", "price": "50000",
                "avgPrice": "50000", "status": "PARTIALLY_FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        def get_order(_call):
            return {
                "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
                "positionSide": "LONG", "origQty": "0.001", "executedQty": order_state["filled"],
                "price": "50000", "avgPrice": "50000", "status": order_state["status"],
                "reduceOnly": False, "time": 1, "updateTime": 2,
            }

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("GET", "/fapi/v1/order", get_order)
        self.session.on("POST", "/fapi/v1/algoOrder", lambda call: {
            "algoId": 3, "clientAlgoId": call["query"].get("clientAlgoId"), "algoStatus": "NEW",
            "closePosition": True, "orderType": call["query"].get("type"),
            "triggerPrice": call["query"].get("triggerPrice"),
        })
        ex = self._exchange()
        result = ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(result["protection_status"], "awaiting_fill")
        self.assertEqual(_calls(self.session, "POST", "/fapi/v1/algoOrder"), [])
        order_state["status"] = "CANCELED"
        order_state["filled"] = "0.0008"
        pos = dict(LONG_POS)
        pos["positionAmt"] = "0.0008"
        state["positions"] = [pos]
        rec = ex.reconcile_pending_entries()
        self.assertEqual(rec["pending"], 0)
        self.assertFalse(rec["blocked"])
        algos = _calls(self.session, "POST", "/fapi/v1/algoOrder")
        self.assertEqual(len(algos), 2)
        self.assertEqual(algos[0]["query"]["clientAlgoId"], SL_ID)
        self.assertEqual(algos[1]["query"]["clientAlgoId"], TP_ID)

    def test_existing_same_direction_baseline_is_required_before_protect(self):
        state = install_defaults(self.session, hedge=True, positions=[dict(LONG_POS)])

        def post_order(_call):
            held = dict(LONG_POS)
            held["positionAmt"] = "0.002"
            state["positions"] = [held]
            return {
                "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
                "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.001", "price": "50000",
                "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("POST", "/fapi/v1/algoOrder", lambda call: {
            "algoId": 4, "clientAlgoId": call["query"].get("clientAlgoId"), "algoStatus": "NEW",
            "closePosition": True, "orderType": call["query"].get("type"),
            "triggerPrice": call["query"].get("triggerPrice"),
        })
        result = self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(result["protection_status"], "protected")
        self.assertEqual(len(_calls(self.session, "POST", "/fapi/v1/algoOrder")), 2)

    def test_unknown_sl_does_not_recreate_or_flatten(self):
        state = install_defaults(self.session, hedge=True)
        posts = {"sl": 0}

        def post_order(_call):
            state["positions"] = [dict(LONG_POS)]
            state["filled"] = "0.001"
            return {
                "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
                "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.001", "price": "50000",
                "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        def post_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            if cid.endswith("SL"):
                posts["sl"] += 1
                return FakeResponse({"code": -1007, "msg": "Timeout waiting for response from backend server."})
            return {"algoId": 9, "clientAlgoId": cid, "algoStatus": "NEW", "closePosition": True}

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("POST", "/fapi/v1/algoOrder", post_algo)
        self.session.on("GET", "/fapi/v1/algoOrder", lambda _c: FakeResponse({"code": -2013, "msg": "Order does not exist."}))
        ex = self._exchange()
        with self.assertRaises(RuntimeError):
            ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(posts["sl"], 1)
        rec = ex.reconcile_pending_entries()
        self.assertTrue(rec["blocked"])
        self.assertEqual(posts["sl"], 1)
        self.assertEqual([call for call in _calls(self.session, "POST", "/fapi/v1/order") if call["query"].get("type") == "MARKET"], [])
        self.assertTrue(ex.has_pending_entries())
        payload = json.loads(pending_state_path(self.env).read_text(encoding="utf-8"))
        self.assertEqual(payload["entries"][0]["client_ids"]["SL"], SL_ID)

    def test_pending_protection_is_not_swept_or_replaced(self):
        state = install_defaults(self.session, hedge=True)
        sl_algo = {
            "algoId": 1, "clientAlgoId": SL_ID, "algoStatus": "NEW", "closePosition": True,
            "symbol": "BTCUSDT", "positionSide": "LONG", "side": "SELL", "triggerPrice": "49000",
            "orderType": "STOP_MARKET", "workingType": "MARK_PRICE",
        }

        def post_order(_call):
            state["positions"] = [dict(LONG_POS)]
            state["algos"] = [dict(sl_algo)]
            return {
                "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
                "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.001", "price": "50000",
                "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        def post_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            if cid.endswith("TP"):
                return FakeResponse({"code": -2021, "msg": "Order would immediately trigger."})
            row = dict(sl_algo)
            state["algos"] = [row]
            return row

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("POST", "/fapi/v1/algoOrder", post_algo)
        ex = self._exchange()
        result = ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(result["protection_status"], "sl_only")
        deletes_before = len(_calls(self.session, "DELETE", "/fapi/v1/algoOrder"))
        algo_posts_before = len(_calls(self.session, "POST", "/fapi/v1/algoOrder"))
        with self.assertRaises(RuntimeError):
            ex.cancel_protection(INST, GROUP)
        with self.assertRaises(RuntimeError):
            ex.place_protection(INST, "long", "0.001", "52000", "49000")
        with self.assertRaises(RuntimeError):
            ex.amend_stop(INST, GROUP, "49100")
        ex._sweep_orphans(INST, "long")
        self.assertEqual(len(_calls(self.session, "DELETE", "/fapi/v1/algoOrder")), deletes_before)
        self.assertEqual(len(_calls(self.session, "POST", "/fapi/v1/algoOrder")), algo_posts_before)

    def _filled_limit(self, state, amt="0.001"):
        pos = dict(LONG_POS)
        pos["positionAmt"] = amt
        state["positions"] = [pos]
        state["filled"] = amt
        return {
            "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
            "positionSide": "LONG", "origQty": amt, "executedQty": amt, "price": "50000",
            "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
        }

    def test_sl_triggered_flat_releases_pending_gate(self):
        state = install_defaults(self.session, hedge=True)

        def post_order(call):
            if call["query"].get("type") == "MARKET":
                raise AssertionError("flatten is not required after SL already closed the fill")
            return self._filled_limit(state)

        def get_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            if cid.endswith("TP"):
                return FakeResponse({"code": -2013, "msg": "Order does not exist."})
            row = {
                "algoId": 7, "clientAlgoId": cid, "algoStatus": "NEW", "closePosition": True,
                "symbol": "BTCUSDT", "positionSide": "LONG", "side": "SELL", "workingType": "MARK_PRICE",
                "orderType": "STOP_MARKET", "triggerPrice": "49000",
            }
            if state.get("sl_triggered"):
                row["algoStatus"] = "TRIGGERED"
            return row

        def post_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            if cid.endswith("TP"):
                return FakeResponse({"code": -2021, "msg": "Order would immediately trigger."})
            return {
                "algoId": 1, "clientAlgoId": cid, "algoStatus": "NEW",
                "closePosition": True, "orderType": "STOP_MARKET", "triggerPrice": "49000",
            }

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("POST", "/fapi/v1/algoOrder", post_algo)
        self.session.on("GET", "/fapi/v1/algoOrder", get_algo)
        ex = self._exchange()
        result = ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(result["protection_status"], "sl_only")
        self.assertTrue(ex.has_pending_entries())
        state["positions"] = []
        state["sl_triggered"] = True
        rec = ex.reconcile_pending_entries()
        self.assertEqual(rec["pending"], 0)
        self.assertFalse(rec["blocked"])
        self.assertFalse(ex.has_pending_entries())

    def test_incomplete_or_wrong_algo_identity_is_not_protection(self):
        state = install_defaults(self.session, hedge=True)
        mode = {"kind": "missing"}

        def post_order(_call):
            return self._filled_limit(state)

        def get_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            row = {
                "algoId": 7, "clientAlgoId": cid, "algoStatus": "NEW", "closePosition": True,
                "symbol": "BTCUSDT", "positionSide": "LONG", "side": "SELL", "workingType": "MARK_PRICE",
                "orderType": "STOP_MARKET", "triggerPrice": "49000",
            }
            if mode["kind"] == "missing":
                row.pop("workingType")
            elif mode["kind"] == "symbol":
                row["symbol"] = "ETHUSDT"
            else:
                row["side"] = "BUY"
            return row

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("POST", "/fapi/v1/algoOrder", {"algoId": 1, "algoStatus": "NEW", "closePosition": True})
        self.session.on("GET", "/fapi/v1/algoOrder", get_algo)
        ex = self._exchange()
        for kind in ("missing", "symbol", "side"):
            mode["kind"] = kind
            state["positions"] = []
            with self.assertRaises(RuntimeError):
                BinanceExchange(FakeEnv(api_key=kind, secret_key=kind), session=self.session).place_protected_limit_order(
                    INST, "buy", "long", "0.001", "50000", "52000", "49000"
                )
            self.assertFalse(any(call["query"].get("clientAlgoId", "").endswith("TP") for call in _calls(self.session, "POST", "/fapi/v1/algoOrder")))

    def test_cancelled_sl_on_refresh_does_not_place_tp(self):
        state = install_defaults(self.session, hedge=True)

        def get_order(call):
            is_close = call["query"].get("origClientOrderId") == CL_ID
            return {
                "orderId": 22 if is_close else 11, "clientOrderId": CL_ID if is_close else ENTRY_ID,
                "symbol": "BTCUSDT", "side": "SELL" if is_close else "BUY", "positionSide": "LONG",
                "type": "MARKET" if is_close else "LIMIT", "origQty": "0.001", "executedQty": "0.001",
                "price": "0" if is_close else "50000", "avgPrice": "50000", "status": "FILLED",
            }

        def get_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            return {
                "algoId": 7, "clientAlgoId": cid, "algoStatus": "CANCELED", "closePosition": True,
                "symbol": "BTCUSDT", "positionSide": "LONG", "side": "SELL", "workingType": "MARK_PRICE",
                "orderType": "STOP_MARKET", "triggerPrice": "49000",
            }

        def post_close(call):
            if call["query"].get("type") == "MARKET":
                state["positions"] = []
                return {
                    "orderId": 22, "clientOrderId": call["query"].get("newClientOrderId"), "symbol": "BTCUSDT",
                    "side": "SELL", "positionSide": "LONG", "origQty": call["query"].get("quantity"),
                    "executedQty": call["query"].get("quantity"), "price": "0", "avgPrice": "50000",
                    "status": "FILLED", "reduceOnly": False, "time": 3, "updateTime": 3,
                }
            return self._filled_limit(state)

        self.session.on("POST", "/fapi/v1/order", post_close)
        self.session.on("POST", "/fapi/v1/algoOrder", {"algoId": 1, "algoStatus": "NEW", "closePosition": True})
        self.session.on("GET", "/fapi/v1/algoOrder", get_algo)
        self.session.on("GET", "/fapi/v1/order", get_order)
        ex = self._exchange()
        with self.assertRaises(RuntimeError):
            ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertFalse(ex.has_pending_entries())
        self.assertEqual(state["positions"], [])
        self.assertFalse(any(call["query"].get("clientAlgoId", "").endswith("TP") for call in _calls(self.session, "POST", "/fapi/v1/algoOrder")))

    def test_compensate_close_uses_fill_qty_and_does_not_resend(self):
        for baseline in ("0", "0.001"):
            with self.subTest(baseline=baseline):
                initial = [dict(LONG_POS)] if baseline != "0" else []
                state = install_defaults(self.session, hedge=True, positions=initial)
                posts = {"close": 0}
                env = FakeEnv(api_key=f"compensation-{baseline}")

                def post_order(call):
                    query = call["query"]
                    if query.get("type") == "MARKET":
                        posts["close"] += 1
                        if posts["close"] == 1:
                            raise TimeoutError("close unknown")
                        raise AssertionError("compensation close was resent")
                    held = dict(LONG_POS)
                    held["positionAmt"] = "0.002" if initial else "0.001"
                    state["positions"] = [held]
                    return {
                        "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT",
                        "side": "BUY", "positionSide": "LONG", "origQty": "0.001",
                        "executedQty": "0.001", "price": "50000", "avgPrice": "50000",
                        "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
                    }

                def get_order(call):
                    is_close = call["query"].get("origClientOrderId") == CL_ID
                    if is_close and not state["closed"]:
                        return FakeResponse({"code": -2013, "msg": "Order does not exist."})
                    return {
                        "orderId": 22 if is_close else 11,
                        "clientOrderId": CL_ID if is_close else ENTRY_ID,
                        "symbol": state.get("close_symbol", "BTCUSDT") if is_close else "BTCUSDT",
                        "side": "SELL" if is_close else "BUY",
                        "positionSide": "LONG", "type": "MARKET" if is_close else "LIMIT",
                        "origQty": "0.001", "executedQty": "0.001",
                        "price": "0" if is_close else "50000", "avgPrice": "50000",
                        "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
                    }

                def post_algo(call):
                    if call["query"]["clientAlgoId"].endswith("SL"):
                        return FakeResponse({"code": -2021, "msg": "Order would immediately trigger."})
                    raise AssertionError("TP must not be posted after SL reject")

                self.session.on("POST", "/fapi/v1/order", post_order)
                self.session.on("POST", "/fapi/v1/algoOrder", post_algo)
                self.session.on("GET", "/fapi/v1/algoOrder", lambda _c: FakeResponse({"code": -2013, "msg": "Order does not exist."}))
                self.session.on("GET", "/fapi/v1/order", get_order)
                ex = BinanceExchange(env, session=self.session)
                with self.assertRaises(RuntimeError):
                    ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
                closes = [
                    call for call in _calls(self.session, "POST", "/fapi/v1/order")
                    if call["query"].get("type") == "MARKET"
                ]
                self.assertEqual(closes[-1]["query"]["newClientOrderId"], CL_ID)
                self.assertEqual(closes[-1]["query"]["quantity"], "0.001")
                self.assertTrue(ex.reconcile_pending_entries()["blocked"])
                self.assertEqual(posts["close"], 1)

                state["closed"] = True
                state["positions"] = initial
                restarted = BinanceExchange(env, session=self.session)
                state["close_symbol"] = "ETHUSDT"
                self.assertTrue(restarted.reconcile_pending_entries()["blocked"])
                state["close_symbol"] = "BTCUSDT"
                if initial:
                    state["positions"] = []
                    self.assertTrue(restarted.reconcile_pending_entries()["blocked"])
                    state["positions"] = initial
                recovered = restarted.reconcile_pending_entries()
                self.assertFalse(recovered["blocked"], recovered)
                self.assertEqual(recovered["pending"], 0)
                self.assertFalse(restarted.has_pending_entries())
                self.assertEqual(state["positions"], initial)
                self.assertEqual(posts["close"], 1)

    def test_compensate_keeps_baseline_quantity(self):
        state = install_defaults(self.session, hedge=True, positions=[dict(LONG_POS)])

        def post_order(call):
            query = call["query"]
            if query.get("type") == "MARKET":
                self.assertEqual(query.get("quantity"), "0.001")
                self.assertEqual(query.get("newClientOrderId"), CL_ID)
                state["positions"] = [dict(LONG_POS)]
                return {
                    "orderId": 22, "clientOrderId": CL_ID, "symbol": "BTCUSDT",
                    "side": "SELL", "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.001",
                    "price": "0", "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 3, "updateTime": 3,
                }
            held = dict(LONG_POS)
            held["positionAmt"] = "0.002"
            state["positions"] = [held]
            return {
                "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
                "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.001", "price": "50000",
                "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("POST", "/fapi/v1/algoOrder", lambda call: FakeResponse({"code": -2021, "msg": "Order would immediately trigger."}) if str(call["query"].get("clientAlgoId") or "").endswith("SL") else (_ for _ in ()).throw(AssertionError("TP posted")))
        self.session.on("GET", "/fapi/v1/algoOrder", lambda _c: FakeResponse({"code": -2013, "msg": "Order does not exist."}))
        self.session.on("GET", "/fapi/v1/order", lambda call: {
            "orderId": 22, "clientOrderId": CL_ID, "symbol": "BTCUSDT", "side": "SELL",
            "positionSide": "LONG", "type": "MARKET", "origQty": "0.001", "executedQty": "0.001",
            "price": "0", "avgPrice": "50000", "status": "FILLED",
        })
        ex = self._exchange()
        with self.assertRaises(RuntimeError):
            ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertFalse(ex.has_pending_entries())
        self.assertEqual(state["positions"][0]["positionAmt"], "0.001")

    def test_live_residual_algo_blocks_retirement_after_cancel_ack(self):
        state = install_defaults(self.session, hedge=True)

        def get_algo(call):
            cid = call["query"]["clientAlgoId"]
            is_sl = cid.endswith("SL")
            if not is_sl and not state.get("tp_visible"):
                return FakeResponse({"code": -2013, "msg": "Order does not exist."})
            status = "NEW"
            if is_sl and state.get("sl_triggered"):
                status = "TRIGGERED"
            elif not is_sl and state.get("tp_canceled"):
                status = "CANCELED"
            return {
                "algoId": 7 if is_sl else 8, "clientAlgoId": cid, "algoStatus": status,
                "closePosition": True, "symbol": "BTCUSDT", "positionSide": "LONG",
                "side": "SELL", "workingType": "MARK_PRICE",
                "orderType": "STOP_MARKET" if is_sl else "TAKE_PROFIT_MARKET",
                "triggerPrice": "49000" if is_sl else "52000",
            }

        def post_algo(call):
            if call["query"]["clientAlgoId"].endswith("TP"):
                return FakeResponse({"code": -1007, "msg": "Timeout waiting for response."})
            return {"algoId": 7, "clientAlgoId": SL_ID, "algoStatus": "NEW"}

        self.session.on("POST", "/fapi/v1/order", lambda _c: self._filled_limit(state))
        self.session.on("POST", "/fapi/v1/algoOrder", post_algo)
        self.session.on("GET", "/fapi/v1/algoOrder", get_algo)
        ex = self._exchange()
        result = ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(result["protection_status"], "sl_only")
        state.update(positions=[], sl_triggered=True, tp_visible=True)
        unresolved = ex.reconcile_pending_entries()
        self.assertTrue(unresolved["blocked"])
        self.assertTrue(ex.has_pending_entries())
        state["tp_canceled"] = True
        self.assertFalse(ex.reconcile_pending_entries()["blocked"])
        self.assertFalse(ex.has_pending_entries())
        self.assertEqual(len(_calls(self.session, "POST", "/fapi/v1/algoOrder")), 2)


if __name__ == "__main__":
    unittest.main()

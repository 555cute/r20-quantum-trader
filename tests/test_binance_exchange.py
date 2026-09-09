"""Offline Binance USDⓈ-M adapter tests. No network, no credentials, injectable transport."""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from r20_exchange.binance import DEMO_HOST, LIVE_HOST, BinanceExchange, RECV_WINDOW_MS

INST = "BTC-USDT-SWAP"
GROUP = "aabbccddeeff"
ENTRY_ID = f"R20G{GROUP}EN"
SL_ID = f"R20G{GROUP}SL"
TP_ID = f"R20G{GROUP}TP"


class FakeEnv:
    def __init__(self, mode: str = "demo", api_key: str = "testkey", secret_key: str = "testsecret") -> None:
        self.exchange = "binance"
        self.mode = mode
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = ""
        self.base_url = DEMO_HOST if mode == "demo" else LIVE_HOST
        self.source = "test"

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key)

    @property
    def fingerprint(self) -> str:
        material = "\0".join((self.exchange, self.mode, self.api_key, self.secret_key, self.passphrase))
        return hashlib.sha256(material.encode()).hexdigest()[:24]

class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self.status_code = status_code
        self._payload = payload
        if isinstance(payload, str):
            self.text = payload
        else:
            self.text = json.dumps(payload)
        self.headers: dict[str, str] = {}

    def json(self):
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.handlers: dict[tuple[str, str], object] = {}
        self.closed = False

    def on(self, method: str, path: str, handler) -> None:
        self.handlers[(method.upper(), path)] = handler

    def request(self, method, url, headers=None, timeout=None, allow_redirects=True, **kwargs):
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        call = {
            "method": str(method).upper(),
            "url": url,
            "path": parsed.path,
            "host": f"{parsed.scheme}://{parsed.netloc}",
            "query": query,
            "headers": dict(headers or {}),
            "allow_redirects": allow_redirects,
            "timeout": timeout,
        }
        self.calls.append(call)
        handler = self.handlers.get((call["method"], parsed.path))
        if handler is None:
            return FakeResponse({"code": -1101, "msg": f"unhandled {call['method']} {parsed.path}"})
        result = handler(call) if callable(handler) else handler
        if isinstance(result, Exception):
            raise result
        if isinstance(result, FakeResponse):
            return result
        return FakeResponse(result)

    def close(self) -> None:
        self.closed = True


BTC_INFO = {
    "symbols": [{
        "symbol": "BTCUSDT",
        "contractType": "PERPETUAL",
        "quoteAsset": "USDT",
        "baseAsset": "BTC",
        "status": "TRADING",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10", "minPrice": "0.10", "maxPrice": "1000000"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
            {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
        ],
    }]
}


def _perp_row(symbol: str, base: str, tick: str, min_price: str, step: str = "0.001", min_qty: str = "0.001") -> dict:
    return {
        "symbol": symbol,
        "contractType": "PERPETUAL",
        "quoteAsset": "USDT",
        "baseAsset": base,
        "status": "TRADING",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": tick, "minPrice": min_price, "maxPrice": "1000000"},
            {"filterType": "LOT_SIZE", "stepSize": step, "minQty": min_qty, "maxQty": "1000000"},
            {"filterType": "MIN_NOTIONAL", "notional": "5.0"},
        ],
    }


MIXED_INFO = {
    "symbols": [
        _perp_row("BTCUSDT", "BTC", "0.10", "100"),
        _perp_row("LINKUSDT", "LINK", "0.001", "0.001"),
        _perp_row("UNIUSDT", "UNI", "0.001", "0.001"),
        _perp_row("XRPUSDT", "XRP", "0.0001", "0.0001", step="0.1", min_qty="0.1"),
    ]
}
LONG_POS = {
    "symbol": "BTCUSDT",
    "positionAmt": "0.001",
    "positionSide": "LONG",
    "entryPrice": "50000",
    "markPrice": "50000",
    "unRealizedProfit": "0",
    "leverage": "10",
    "marginType": "cross",
    "notional": "50",
    "liquidationPrice": "0",
    "updateTime": 1,
}
ACCOUNT = {
    "totalMarginBalance": "1000",
    "assets": [{
        "asset": "USDT",
        "marginBalance": "1000",
        "availableBalance": "900",
        "walletBalance": "980",
        "unrealizedProfit": "20",
    }],
}


def _query(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))


def _calls(session: FakeSession, method: str, path: str) -> list[dict]:
    return [call for call in session.calls if call["method"] == method and call["path"] == path]


def install_defaults(session: FakeSession, *, hedge: bool = True, positions=None, algos=None, filled: str = "0") -> dict:
    state = {"positions": list(positions or []), "algos": list(algos or []), "closed": False, "filled": filled}

    def exchange_info(_call):
        return BTC_INFO

    def dual(_call):
        return {"dualSidePosition": hedge}

    def risk(_call):
        return list(state["positions"])

    def open_algos(_call):
        return list(state["algos"])

    def get_order(call):
        return {
            "orderId": 11,
            "clientOrderId": call["query"].get("origClientOrderId") or ENTRY_ID,
            "symbol": "BTCUSDT",
            "side": "BUY",
            "positionSide": "LONG" if hedge else "BOTH",
            "origQty": "0.001",
            "executedQty": state["filled"],
            "price": "50000",
            "avgPrice": "50000" if state["filled"] != "0" else "0",
            "status": "FILLED" if state["filled"] != "0" else "CANCELED",
            "reduceOnly": False,
            "time": 1,
            "updateTime": 2,
        }

    def get_algo(call):
        cid = call["query"].get("clientAlgoId") or ""
        is_sl = cid.endswith("SL") or cid.endswith("SX")
        return {
            "algoId": 7,
            "clientAlgoId": cid,
            "algoStatus": "NEW",
            "closePosition": True,
            "symbol": "BTCUSDT",
            "positionSide": "LONG" if hedge else "BOTH",
            "side": "SELL",
            "workingType": "MARK_PRICE",
            "orderType": "STOP_MARKET" if is_sl else "TAKE_PROFIT_MARKET",
            "triggerPrice": "49000" if is_sl else "52000",
        }

    session.on("GET", "/fapi/v1/exchangeInfo", exchange_info)
    session.on("GET", "/fapi/v1/positionSide/dual", dual)
    session.on("GET", "/fapi/v2/positionRisk", risk)
    session.on("GET", "/fapi/v2/account", ACCOUNT)
    session.on("GET", "/fapi/v1/openAlgoOrders", open_algos)
    session.on("GET", "/fapi/v1/order", get_order)
    session.on("GET", "/fapi/v1/algoOrder", get_algo)
    session.on("GET", "/fapi/v1/ticker/24hr", {"symbol": "BTCUSDT", "lastPrice": "50000", "openPrice": "49000", "highPrice": "51000", "lowPrice": "48000", "volume": "1", "quoteVolume": "50000", "closeTime": 3, "bidPrice": "49999", "askPrice": "50001"})
    session.on("GET", "/fapi/v1/ticker/bookTicker", {"symbol": "BTCUSDT", "bidPrice": "49999", "askPrice": "50001"})
    session.on("DELETE", "/fapi/v1/order", {"orderId": 11, "status": "CANCELED", "symbol": "BTCUSDT", "origQty": "0.001", "executedQty": state["filled"], "clientOrderId": ENTRY_ID, "side": "BUY", "positionSide": "LONG" if hedge else "BOTH"})
    session.on("DELETE", "/fapi/v1/algoOrder", {"algoId": 7, "status": "CANCELED"})
    return state


class BinanceExchangeTests(unittest.TestCase):
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

    def _exchange(self, env: FakeEnv | None = None) -> BinanceExchange:
        return BinanceExchange(env or self.env, session=self.session)

    def test_constructor_does_not_touch_transport(self):
        BinanceExchange(self.env, session=self.session)
        self.assertEqual(self.session.calls, [])

    def test_signature_and_environment_isolation(self):
        install_defaults(self.session, hedge=True)
        frozen = 1591702613.943
        with patch("r20_exchange.binance.time.time", return_value=frozen):
            self._exchange().balance()
        call = _calls(self.session, "GET", "/fapi/v2/account")[0]
        self.assertTrue(call["url"].startswith(DEMO_HOST))
        self.assertFalse(call["allow_redirects"])
        self.assertEqual(call["headers"].get("X-MBX-APIKEY"), "testkey")
        query = call["query"]
        self.assertEqual(query.get("recvWindow"), str(RECV_WINDOW_MS))
        self.assertEqual(query.get("timestamp"), str(int(frozen * 1000)))
        unsigned = "&".join(f"{key}={value}" for key, value in _query(call["url"]).items() if key != "signature")
        expected = hmac.new(b"testsecret", unsigned.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(query.get("signature"), expected)
        live_session = FakeSession()
        install_defaults(live_session, hedge=True)
        with patch("r20_exchange.binance.time.time", return_value=frozen):
            BinanceExchange(FakeEnv(mode="live"), session=live_session).balance()
        live_call = _calls(live_session, "GET", "/fapi/v2/account")[0]
        self.assertTrue(live_call["url"].startswith(LIVE_HOST))
        self.assertNotIn("demo-fapi", live_call["url"])
        self.assertEqual(live_call["headers"].get("X-MBX-APIKEY"), "testkey")

    def test_signed_timestamp_follows_binance_server_time(self):
        install_defaults(self.session, hedge=True)
        frozen = 1_700_000_000.0
        self.session.on("GET", "/fapi/v1/time", {"serverTime": int(frozen * 1000) - 2500})
        with patch("r20_exchange.binance.time.time", return_value=frozen):
            self._exchange().balance()
        time_calls = _calls(self.session, "GET", "/fapi/v1/time")
        self.assertEqual(len(time_calls), 1)
        self.assertNotIn("signature", time_calls[0]["query"])
        account = _calls(self.session, "GET", "/fapi/v2/account")[0]
        self.assertEqual(account["query"].get("timestamp"), str(int(frozen * 1000) - 2500))

    def test_time_offset_uses_request_midpoint_not_pre_request_clock(self):
        install_defaults(self.session, hedge=True)
        clock = {"t": 1_700_000_000.0}

        def now():
            return clock["t"]

        def time_ep(_call):
            clock["t"] += 2.0
            return {"serverTime": int((1_700_000_000.0 + 1.0) * 1000)}

        self.session.on("GET", "/fapi/v1/time", time_ep)
        with patch("r20_exchange.binance.time.time", side_effect=now):
            exchange = self._exchange()
            exchange._sync_time_offset(force=True)
            self.assertEqual(exchange._time_offset_ms, 0)
            exchange.balance()
        account = _calls(self.session, "GET", "/fapi/v2/account")[0]
        self.assertEqual(account["query"].get("timestamp"), str(1_700_000_002_000))


    def test_timestamp_ahead_error_resyncs_once(self):
        install_defaults(self.session, hedge=True)
        frozen = 1_700_000_000.0
        state = {"n": 0}

        def account(_call):
            state["n"] += 1
            if state["n"] == 1:
                return {"code": -1021, "msg": "Timestamp for this request was 1000ms ahead of the server's time."}
            return ACCOUNT

        self.session.on("GET", "/fapi/v2/account", account)
        self.session.on("GET", "/fapi/v1/time", {"serverTime": int(frozen * 1000) - 1500})
        with patch("r20_exchange.binance.time.time", return_value=frozen):
            rows = self._exchange().balance()
        self.assertEqual(state["n"], 2)
        self.assertTrue(isinstance(rows, list) and rows)

        stamps = [call["query"].get("timestamp") for call in _calls(self.session, "GET", "/fapi/v2/account")]
        self.assertEqual(stamps[-1], str(int(frozen * 1000) - 1500))


    def test_redirects_and_secrets_are_not_leaked(self):
        self.session.on("GET", "/fapi/v2/account", lambda _c: FakeResponse({}, 302))
        with self.assertRaisesRegex(RuntimeError, "redirect"):
            self._exchange().balance()
        self.assertFalse(self.session.calls[0]["allow_redirects"])

        def boom(_call):
            raise TimeoutError("upstream signature=deadbeef X-MBX-APIKEY: testkey secret=testsecret")

        leaking = FakeSession()
        leaking.on("GET", "/fapi/v2/account", boom)
        with self.assertRaises(RuntimeError) as raised:
            BinanceExchange(self.env, session=leaking).balance()
        message = str(raised.exception)
        self.assertNotIn("deadbeef", message)
        self.assertNotIn("testkey", message)
        self.assertNotIn("testsecret", message)

    def test_small_btc_quantity_is_not_rounded_up(self):
        install_defaults(self.session, hedge=True)
        self.session.on("POST", "/fapi/v1/order", {
            "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
            "positionSide": "LONG", "origQty": "0.001", "executedQty": "0", "price": "50000",
            "avgPrice": "0", "status": "NEW", "reduceOnly": False, "time": 1, "updateTime": 1,
        })
        self.session.on("POST", "/fapi/v1/algoOrder", {
            "algoId": 1, "clientAlgoId": SL_ID, "algoStatus": "NEW", "closePosition": True,
        })
        order = self._exchange().place_protected_limit_order(INST, "buy", "long", "0.0019", "50000.19", "52000.19", "49000.19")
        entry = _calls(self.session, "POST", "/fapi/v1/order")[0]["query"]
        self.assertEqual(Decimal(entry["quantity"]), Decimal("0.001"))
        self.assertLessEqual(Decimal(entry["quantity"]) * Decimal(entry["price"]), Decimal("95.000361"))
        self.assertEqual(order["protection_mechanism"], "paired_conditional")
        self.assertEqual(order["protection_status"], "awaiting_fill")
        self.assertEqual(order["ordId"], "11")
        self.assertEqual(order["clOrdId"], ENTRY_ID)
        self.assertEqual(_calls(self.session, "POST", "/fapi/v1/algoOrder"), [])

    def test_filters_reject_illegal_inputs_before_submit(self):
        install_defaults(self.session, hedge=True)
        posted = {"order": 0}

        def forbid(call):
            posted["order"] += 1
            return {"orderId": 1}

        self.session.on("POST", "/fapi/v1/order", forbid)
        ex = self._exchange()
        with self.assertRaises(ValueError):
            ex.place_protected_limit_order(INST, "buy", "long", "0.0001", "50000", "52000", "49000")
        with self.assertRaises(ValueError):
            ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "50100")
        with self.assertRaises(ValueError):
            ex.place_protected_limit_order("BTC-USDT", "buy", "long", "0.001", "50000", "52000", "49000")
        with self.assertRaises(ValueError):
            ex.place_protected_limit_order(INST, "buy", "long", float("nan"), "50000", "52000", "49000")
        with self.assertRaises(ValueError):
            ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "51000", "49000")
        self.assertEqual(posted["order"], 0)

    def test_geometry_uses_shared_risk_floor(self):
        install_defaults(self.session, hedge=True)
        posted = {"order": 0}

        def count_post(_call):
            posted["order"] += 1
            return {
                "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
                "positionSide": "LONG", "origQty": "0.001", "executedQty": "0", "price": "50000",
                "avgPrice": "0", "status": "NEW", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        self.session.on("POST", "/fapi/v1/order", count_post)
        ex = self._exchange()
        with patch("scripts.order_risk.MIN_RISK_REWARD_RATIO", 1.9):
            with self.assertRaises(ValueError):
                ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "51800", "49000")
            self.assertEqual(posted["order"], 0)
            result = ex.place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "51900", "49000")
        self.assertEqual(posted["order"], 1)
        self.assertEqual(result["protection_status"], "awaiting_fill")

    def test_new_entry_does_not_post_close_position_algos(self):
        install_defaults(self.session, hedge=True)
        self.session.on("POST", "/fapi/v1/order", {
            "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
            "positionSide": "LONG", "origQty": "0.001", "executedQty": "0", "price": "50000",
            "avgPrice": "0", "status": "NEW", "reduceOnly": False, "time": 1, "updateTime": 1,
        })
        self.session.on("POST", "/fapi/v1/algoOrder", {
            "algoId": 1, "clientAlgoId": SL_ID, "algoStatus": "NEW", "closePosition": True,
        })
        result = self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        entry = _calls(self.session, "POST", "/fapi/v1/order")[0]["query"]
        self.assertEqual(entry.get("positionSide"), "LONG")
        self.assertEqual(entry.get("timeInForce"), "GTC")
        self.assertNotIn("reduceOnly", entry)
        self.assertEqual(result["protection_status"], "awaiting_fill")
        self.assertEqual(_calls(self.session, "POST", "/fapi/v1/algoOrder"), [])

        oneway = FakeSession()
        install_defaults(oneway, hedge=False)
        oneway.on("POST", "/fapi/v1/order", {
            "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
            "positionSide": "BOTH", "origQty": "0.001", "executedQty": "0", "price": "50000",
            "avgPrice": "0", "status": "NEW", "reduceOnly": False, "time": 1, "updateTime": 1,
        })
        oneway_env = FakeEnv(api_key="onewaykey", secret_key="onewaysecret")
        result = BinanceExchange(oneway_env, session=oneway).place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        one_entry = _calls(oneway, "POST", "/fapi/v1/order")[0]["query"]
        self.assertEqual(one_entry.get("positionSide"), "BOTH")
        self.assertNotIn("reduceOnly", one_entry)
        self.assertEqual(result["protection_status"], "awaiting_fill")
        self.assertEqual(_calls(oneway, "POST", "/fapi/v1/algoOrder"), [])

    def test_second_leg_failure_keeps_confirmed_stop(self):
        state = install_defaults(self.session, hedge=True)

        def post_order(call):
            query = call["query"]
            if query.get("closePosition"):
                return FakeResponse({"code": -4136, "msg": "Invalid closePosition"})
            if query.get("type") == "MARKET":
                state["positions"] = []
                return {
                    "orderId": 22, "clientOrderId": query.get("newClientOrderId"), "symbol": "BTCUSDT",
                    "side": "SELL", "positionSide": "LONG", "origQty": query.get("quantity") or "0",
                    "executedQty": query.get("quantity") or "0", "price": "0", "avgPrice": "50000",
                    "status": "FILLED", "reduceOnly": False, "time": 3, "updateTime": 3,
                }
            state["positions"] = [dict(LONG_POS)]
            return {
                "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
                "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.001", "price": "50000",
                "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        def post_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            if cid.endswith("TP"):
                return FakeResponse({"code": -2021, "msg": "Order would immediately trigger."})
            return {"algoId": 1, "clientAlgoId": cid, "algoStatus": "NEW", "closePosition": True, "orderType": "STOP_MARKET", "triggerPrice": "49000", "closePosition": True}

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("POST", "/fapi/v1/algoOrder", post_algo)
        result = self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(result["protection_status"], "sl_only")
        self.assertEqual([call for call in _calls(self.session, "POST", "/fapi/v1/order") if call["query"].get("type") == "MARKET"], [])
        self.assertEqual(_calls(self.session, "DELETE", "/fapi/v1/algoOrder"), [])
        self.assertTrue(self._exchange().has_pending_entries())

    def test_unknown_canceled_entry_does_not_attach_protection(self):
        install_defaults(self.session, hedge=True)
        posts = {"count": 0}

        def post_order(_call):
            posts["count"] += 1
            raise TimeoutError("timed out signature=ffff")

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("POST", "/fapi/v1/algoOrder", {"algoId": 1, "algoStatus": "NEW", "closePosition": True})
        with self.assertRaises(RuntimeError):
            self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(posts["count"], 1)
        self.assertEqual(_calls(self.session, "POST", "/fapi/v1/algoOrder"), [])

    def test_unknown_live_entry_reconciles_without_repost(self):
        install_defaults(self.session, hedge=True)
        posts = {"count": 0}

        def post_order(_call):
            posts["count"] += 1
            raise TimeoutError("timed out signature=ffff")

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("GET", "/fapi/v1/order", {
            "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
            "positionSide": "LONG", "origQty": "0.001", "executedQty": "0", "price": "50000",
            "avgPrice": "0", "status": "NEW", "reduceOnly": False, "time": 1, "updateTime": 1,
        })
        self.session.on("POST", "/fapi/v1/algoOrder", {"algoId": 1, "algoStatus": "NEW", "closePosition": True})
        result = self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(posts["count"], 1)
        self.assertEqual(len(_calls(self.session, "POST", "/fapi/v1/order")), 1)
        self.assertEqual(result["ordId"], "11")
        self.assertEqual(result["clOrdId"], ENTRY_ID)
        self.assertEqual(result["protection_status"], "awaiting_fill")
        self.assertEqual(_calls(self.session, "POST", "/fapi/v1/algoOrder"), [])
        self.assertTrue(self._exchange().has_pending_entries())
        with self.assertRaises(RuntimeError):
            self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(posts["count"], 1)

    def test_one_leg_orphan_does_not_cover_next_position(self):
        orphan = [{
            "algoId": 9,
            "clientAlgoId": SL_ID,
            "algoStatus": "NEW",
            "closePosition": True,
            "symbol": "BTCUSDT",
            "positionSide": "LONG",
            "side": "SELL",
            "triggerPrice": "49000",
            "orderType": "STOP_MARKET",
        }]
        state = install_defaults(self.session, hedge=True, algos=orphan)
        self.session.on("POST", "/fapi/v1/algoOrder", {"algoId": 2, "algoStatus": "NEW", "closePosition": True})
        self.session.on("GET", "/fapi/v1/ticker/24hr", {"symbol": "BTCUSDT", "lastPrice": "50000", "openPrice": "1", "highPrice": "1", "lowPrice": "1", "volume": "1", "quoteVolume": "1", "closeTime": 1})
        ex = self._exchange()
        self.assertEqual(ex.protection_orders(INST), [])
        placed = ex.place_protection(INST, "long", "0.001", "52000", "49000")
        deletes = _calls(self.session, "DELETE", "/fapi/v1/algoOrder")
        self.assertTrue(any(call["query"].get("clientAlgoId") == SL_ID for call in deletes))
        self.assertEqual(placed["protection_mechanism"], "paired_conditional")
        self.assertEqual(placed["ordType"], "paired_conditional")
        self.assertTrue(placed["closeAll"])
        self.assertEqual(placed["algoId"], GROUP)
        state["algos"] = []

    def test_market_failure_does_not_fake_empty_account(self):
        install_defaults(self.session, hedge=True)
        self.session.on("GET", "/fapi/v1/ticker/24hr", lambda _c: FakeResponse("broken", 500))
        ex = self._exchange()
        with self.assertRaises(RuntimeError):
            ex.ticker(INST)
        with self.assertRaises(RuntimeError):
            ex.tickers()
        snapshot = ex.balance()
        self.assertEqual(snapshot[0]["details"][0]["ccy"], "USDT")
        self.assertEqual(snapshot[0]["totalEq"], "1000")
        self.session.on("GET", "/fapi/v2/account", lambda _c: FakeResponse({"code": -1001, "msg": "failed"}))
        with self.assertRaises(RuntimeError):
            ex.balance()

    def test_bills_do_not_double_count_commission(self):
        install_defaults(self.session, hedge=True)
        self.session.on("GET", "/fapi/v1/income", [
            {"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "10", "asset": "USDT", "time": 10, "tranId": 1, "tradeId": "9"},
            {"symbol": "BTCUSDT", "incomeType": "COMMISSION", "income": "-0.04", "asset": "USDT", "time": 11, "tranId": 2, "tradeId": "9"},
            {"symbol": "BTCUSDT", "incomeType": "FUNDING_FEE", "income": "-0.01", "asset": "USDT", "time": 12, "tranId": 3, "tradeId": ""},
        ])
        rows = self._exchange().bills()
        realized = next(row for row in rows if row["type"] == "realized_pnl")
        commission = next(row for row in rows if row["type"] == "commission")
        funding = next(row for row in rows if row["type"] == "funding_fee")
        self.assertEqual(realized["pnl"], "10")
        self.assertEqual(realized["fee"], "0")
        self.assertEqual(realized["billId"], "REALIZED_PNL:1")
        self.assertEqual(realized["ordId"], "")
        self.assertEqual(realized["tradeId"], "9")
        self.assertEqual(commission["pnl"], "0")
        self.assertEqual(commission["fee"], "-0.04")
        self.assertEqual(commission["billId"], "COMMISSION:2")
        self.assertEqual(commission["ordId"], "")
        self.assertEqual(commission["tradeId"], "9")
        self.assertEqual(funding["pnl"], "-0.01")
        self.assertEqual(funding["fee"], "0")
        net = sum(float(row["pnl"]) + float(row["fee"]) for row in rows)
        self.assertAlmostEqual(net, 10 - 0.04 - 0.01)

    def test_close_position_market_uses_base_qty_not_closePosition(self):
        state = install_defaults(self.session, hedge=True, positions=[dict(LONG_POS)])

        def post_order(call):
            query = call["query"]
            if query.get("closePosition"):
                return FakeResponse({"code": -4136, "msg": "Invalid closePosition"})
            if query.get("type") == "MARKET":
                state["positions"] = []
            return {
                "orderId": 22, "clientOrderId": query.get("newClientOrderId"), "symbol": "BTCUSDT",
                "side": query.get("side"), "positionSide": query.get("positionSide"),
                "origQty": query.get("quantity") or "0", "executedQty": query.get("quantity") or "0",
                "price": "0", "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        self.session.on("POST", "/fapi/v1/order", post_order)
        self._exchange().close_position(INST, "long")
        close = _calls(self.session, "POST", "/fapi/v1/order")[0]["query"]
        self.assertEqual(close.get("type"), "MARKET")
        self.assertEqual(close.get("quantity"), "0.001")
        self.assertEqual(close.get("side"), "SELL")
        self.assertEqual(close.get("positionSide"), "LONG")
        self.assertNotIn("closePosition", close)
        self.assertNotIn("reduceOnly", close)

    def test_close_position_oneway_sends_reduceOnly_without_closePosition(self):
        pos = dict(LONG_POS)
        pos["positionSide"] = "BOTH"
        state = install_defaults(self.session, hedge=False, positions=[pos])

        def post_order(call):
            query = call["query"]
            if query.get("closePosition"):
                return FakeResponse({"code": -4136, "msg": "Invalid closePosition"})
            if query.get("type") == "MARKET":
                state["positions"] = []
            return {
                "orderId": 22, "clientOrderId": query.get("newClientOrderId"), "symbol": "BTCUSDT",
                "side": query.get("side"), "positionSide": query.get("positionSide"),
                "origQty": query.get("quantity") or "0", "executedQty": query.get("quantity") or "0",
                "price": "0", "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        self.session.on("POST", "/fapi/v1/order", post_order)
        self._exchange().close_position(INST, "long")
        close = _calls(self.session, "POST", "/fapi/v1/order")[0]["query"]
        self.assertEqual(close.get("type"), "MARKET")
        self.assertEqual(close.get("quantity"), "0.001")
        self.assertEqual(close.get("positionSide"), "BOTH")
        self.assertEqual(close.get("reduceOnly"), "true")
        self.assertNotIn("closePosition", close)

    def test_partial_fill_cancels_remainder_without_protection(self):
        state = install_defaults(self.session, hedge=True)
        state["filled"] = "0.0005"

        def post_order(call):
            query = call["query"]
            if query.get("type") == "MARKET":
                return {
                    "orderId": 22, "clientOrderId": query.get("newClientOrderId"), "symbol": "BTCUSDT",
                    "side": "SELL", "positionSide": "LONG", "origQty": query.get("quantity") or "0",
                    "executedQty": query.get("quantity") or "0", "price": "0", "avgPrice": "50000",
                    "status": "FILLED", "reduceOnly": False, "time": 3, "updateTime": 3,
                }
            return {
                "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
                "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.0005", "price": "50000",
                "avgPrice": "50000", "status": "PARTIALLY_FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("POST", "/fapi/v1/algoOrder", {"algoId": 1, "algoStatus": "NEW", "closePosition": True})
        result = self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(result["protection_status"], "awaiting_fill")
        self.assertEqual(_calls(self.session, "POST", "/fapi/v1/algoOrder"), [])
        self.assertEqual([call for call in _calls(self.session, "POST", "/fapi/v1/order") if call["query"].get("type") == "MARKET"], [])
        self.assertTrue(_calls(self.session, "DELETE", "/fapi/v1/order"))

    def test_unknown_tp_keeps_confirmed_stop(self):
        state = install_defaults(self.session, hedge=True)

        def post_order(call):
            if call["query"].get("type") == "MARKET":
                state["positions"] = []
                return {
                    "orderId": 22, "clientOrderId": call["query"].get("newClientOrderId"), "symbol": "BTCUSDT",
                    "side": "SELL", "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.001",
                    "price": "0", "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 3, "updateTime": 3,
                }
            state["positions"] = [dict(LONG_POS)]
            return {
                "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
                "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.001", "price": "50000",
                "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        def post_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            if cid.endswith("TP"):
                return FakeResponse({"code": -1007, "msg": "Timeout waiting for response from backend server."})
            return {
                "algoId": 1, "clientAlgoId": cid, "algoStatus": "NEW", "closePosition": True,
                "orderType": "STOP_MARKET", "triggerPrice": "49000",
            }

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("POST", "/fapi/v1/algoOrder", post_algo)
        self.session.on("GET", "/fapi/v1/algoOrder", lambda call: FakeResponse({"code": -2013, "msg": "Order does not exist."}) if call["query"].get("clientAlgoId", "").endswith("TP") else {
            "algoId": 7, "clientAlgoId": call["query"]["clientAlgoId"], "algoStatus": "NEW", "closePosition": True,
            "symbol": "BTCUSDT", "positionSide": "LONG", "side": "SELL", "workingType": "MARK_PRICE",
            "triggerPrice": "49000", "orderType": "STOP_MARKET",
        })
        result = self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(result["protection_status"], "sl_only")
        self.assertEqual(_calls(self.session, "DELETE", "/fapi/v1/algoOrder"), [])
        self.assertEqual([call for call in _calls(self.session, "POST", "/fapi/v1/order") if call["query"].get("type") == "MARKET"], [])

    def test_sl_timeout_is_reconciled_before_compensate(self):
        state = install_defaults(self.session, hedge=True)

        def post_order(call):
            if call["query"].get("type") == "LIMIT":
                state["positions"] = [dict(LONG_POS)]
                return {
                    "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
                    "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.001", "price": "50000",
                    "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
                }
            raise AssertionError("unexpected order type")

        def post_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            if cid.endswith("SL"):
                return FakeResponse({"code": -1007, "msg": "Timeout waiting for response from backend server. Send status unknown; execution status unknown."})
            return {
                "algoId": 2, "clientAlgoId": cid, "algoStatus": "NEW", "closePosition": True,
                "orderType": "TAKE_PROFIT_MARKET", "triggerPrice": "52000",
            }

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("POST", "/fapi/v1/algoOrder", post_algo)
        self.session.on("GET", "/fapi/v1/algoOrder", lambda call: {
            "algoId": 7 if call["query"]["clientAlgoId"] == SL_ID else 8,
            "clientAlgoId": call["query"]["clientAlgoId"], "algoStatus": "NEW", "closePosition": True,
            "symbol": "BTCUSDT", "positionSide": "LONG", "side": "SELL", "workingType": "MARK_PRICE",
            "orderType": "STOP_MARKET" if call["query"]["clientAlgoId"] == SL_ID else "TAKE_PROFIT_MARKET",
            "triggerPrice": "49000" if call["query"]["clientAlgoId"] == SL_ID else "52000",
        })
        result = self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(result["protection_status"], "protected")
        stop_posts = [call for call in _calls(self.session, "POST", "/fapi/v1/algoOrder") if call["query"].get("clientAlgoId") == SL_ID]
        self.assertEqual(len(stop_posts), 1)
        self.assertEqual([call for call in _calls(self.session, "POST", "/fapi/v1/order") if call["query"].get("type") == "MARKET"], [])

    def test_amend_stop_alternates_client_ids(self):
        state = install_defaults(self.session, hedge=True, positions=[dict(LONG_POS)], algos=[
            {"algoId": 1, "clientAlgoId": TP_ID, "algoStatus": "NEW", "closePosition": True, "symbol": "BTCUSDT", "positionSide": "LONG", "triggerPrice": "52000", "orderType": "TAKE_PROFIT_MARKET"},
            {"algoId": 2, "clientAlgoId": SL_ID, "algoStatus": "NEW", "closePosition": True, "symbol": "BTCUSDT", "positionSide": "LONG", "triggerPrice": "49000", "orderType": "STOP_MARKET"},
        ])
        self.session.on("POST", "/fapi/v1/algoOrder", {"algoId": 3, "algoStatus": "NEW", "closePosition": True, "clientAlgoId": f"R20G{GROUP}SX"})
        self.session.on("GET", "/fapi/v1/ticker/24hr", {"symbol": "BTCUSDT", "lastPrice": "50000", "openPrice": "1", "highPrice": "1", "lowPrice": "1", "volume": "1", "quoteVolume": "1", "closeTime": 1})

        def delete_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            state["algos"] = [row for row in state["algos"] if row.get("clientAlgoId") != cid]
            return {"status": "CANCELED"}

        def post_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            row = {"algoId": 9, "clientAlgoId": cid, "algoStatus": "NEW", "closePosition": True, "symbol": "BTCUSDT", "positionSide": "LONG", "triggerPrice": call["query"].get("triggerPrice"), "orderType": "STOP_MARKET"}
            state["algos"].append(row)
            return row

        self.session.on("DELETE", "/fapi/v1/algoOrder", delete_algo)
        self.session.on("POST", "/fapi/v1/algoOrder", post_algo)
        ex = self._exchange()
        first = ex.amend_stop(INST, GROUP, "49100")
        self.assertEqual(_calls(self.session, "POST", "/fapi/v1/algoOrder")[0]["query"].get("clientAlgoId"), f"R20G{GROUP}SX")
        second = ex.amend_stop(INST, GROUP, "49200")
        self.assertEqual(_calls(self.session, "POST", "/fapi/v1/algoOrder")[1]["query"].get("clientAlgoId"), f"R20G{GROUP}SL")
        self.assertEqual(first["algoId"], GROUP)
        self.assertEqual(second["slTriggerPx"], "49200")

    def test_usdt_rebate_is_not_forced_negative(self):
        install_defaults(self.session, hedge=True)
        self.session.on("GET", "/fapi/v1/income", [{"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "8", "asset": "USDT", "time": 2, "tranId": 1}])
        self.session.on("GET", "/fapi/v1/userTrades", [
            {"symbol": "BTCUSDT", "id": 1, "orderId": 8, "side": "BUY", "positionSide": "LONG", "price": "50000", "qty": "0.001", "realizedPnl": "0", "commission": "-0.01", "commissionAsset": "USDT", "time": 1},
            {"symbol": "BTCUSDT", "id": 2, "orderId": 9, "side": "SELL", "positionSide": "LONG", "price": "58000", "qty": "0.001", "realizedPnl": "8", "commission": "0.02", "commissionAsset": "USDT", "time": 2},
        ])
        rows = self._exchange().positions_history()
        self.assertEqual(rows[0]["fee"], "-0.01")

    def test_positions_history_inventory_drift_is_incomplete(self):
        install_defaults(self.session, hedge=True)
        hits = {"n": 0}

        def risk(_call):
            hits["n"] += 1
            if hits["n"] <= 1:
                return []
            return [dict(LONG_POS)]

        self.session.on("GET", "/fapi/v2/positionRisk", risk)
        self.session.on("GET", "/fapi/v1/income", [{"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "8", "asset": "USDT", "time": 2, "tranId": 1}])
        self.session.on("GET", "/fapi/v1/userTrades", [
            {"symbol": "BTCUSDT", "id": 1, "orderId": 8, "side": "BUY", "positionSide": "LONG", "price": "50000", "qty": "0.001", "realizedPnl": "0", "commission": "0", "commissionAsset": "USDT", "time": 1},
            {"symbol": "BTCUSDT", "id": 2, "orderId": 9, "side": "SELL", "positionSide": "LONG", "price": "58000", "qty": "0.001", "realizedPnl": "8", "commission": "0", "commissionAsset": "USDT", "time": 2},
        ])
        with self.assertRaises(RuntimeError) as raised:
            self._exchange().positions_history()
        self.assertTrue(getattr(raised.exception, "incomplete", False))

    def test_funding_rate_converts_last_funding_to_percent(self):
        self.session.on("GET", "/fapi/v1/premiumIndex", {"symbol": "BTCUSDT", "lastFundingRate": "0.0001", "markPrice": "65000"})
        self.assertEqual(self._exchange().funding_rate(INST), 0.01)

    def test_open_interest_usd_is_base_times_mark(self):
        self.session.on("GET", "/fapi/v1/openInterest", {"symbol": "BTCUSDT", "openInterest": "2", "time": 1})
        self.session.on("GET", "/fapi/v1/premiumIndex", {"symbol": "BTCUSDT", "markPrice": "65000", "lastFundingRate": "0.0001"})
        oi = self._exchange().open_interest(INST)
        self.assertEqual(oi["oi"], "2")
        self.assertEqual(oi["oiUsd"], "130000")

    def test_long_short_ratio_reads_global_account_ratio(self):
        self.session.on("GET", "/futures/data/globalLongShortAccountRatio", [{"symbol": "BTCUSDT", "longShortRatio": "1.84"}])
        self.assertEqual(self._exchange().long_short_ratio(INST), 1.84)

    def test_taker_volume_converts_base_contracts_to_usdt(self):
        self.session.on("GET", "/futures/data/takerlongshortRatio", [{"buyVol": "2", "sellVol": "1", "buySellRatio": "2"}])
        self.session.on("GET", "/fapi/v1/premiumIndex", {"symbol": "BTCUSDT", "markPrice": "65000"})
        vol = self._exchange().taker_volume(INST)
        self.assertEqual(vol["buyVol"], "130000")
        self.assertEqual(vol["sellVol"], "65000")

    def test_ls_and_taker_unavailable_on_failure(self):
        self.assertIsNone(self._exchange().long_short_ratio(INST))
        self.assertIsNone(self._exchange().taker_volume(INST))

    def test_taker_unavailable_without_mark(self):
        self.session.on("GET", "/futures/data/takerlongshortRatio", [{"buyVol": "2", "sellVol": "1"}])
        self.session.on("GET", "/fapi/v1/premiumIndex", {"symbol": "BTCUSDT", "markPrice": "0"})
        self.assertIsNone(self._exchange().taker_volume(INST))

    def test_funding_rate_unavailable_without_last_rate(self):
        self.session.on("GET", "/fapi/v1/premiumIndex", {"symbol": "BTCUSDT", "markPrice": "65000"})
        with self.assertRaises(RuntimeError):
            self._exchange().funding_rate(INST)

    def test_positions_history_entry_order_ids_dedupe_opens_not_closes(self):
        install_defaults(self.session, hedge=True)
        self.session.on("GET", "/fapi/v1/income", [{"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "8", "asset": "USDT", "time": 4, "tranId": 1}])
        self.session.on("GET", "/fapi/v1/userTrades", [
            {"symbol": "BTCUSDT", "id": 1, "orderId": 8, "side": "BUY", "positionSide": "LONG", "price": "50000", "qty": "0.001", "realizedPnl": "0", "commission": "0.01", "commissionAsset": "USDT", "time": 1},
            {"symbol": "BTCUSDT", "id": 2, "orderId": 8, "side": "BUY", "positionSide": "LONG", "price": "50000", "qty": "0.001", "realizedPnl": "0", "commission": "0.01", "commissionAsset": "USDT", "time": 2},
            {"symbol": "BTCUSDT", "id": 3, "orderId": 10, "side": "BUY", "positionSide": "LONG", "price": "51000", "qty": "0.001", "realizedPnl": "0", "commission": "0.01", "commissionAsset": "USDT", "time": 3},
            {"symbol": "BTCUSDT", "id": 4, "orderId": 11, "side": "SELL", "positionSide": "LONG", "price": "58000", "qty": "0.003", "realizedPnl": "8", "commission": "0.01", "commissionAsset": "USDT", "time": 4},
        ])
        rows = self._exchange().positions_history()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entryOrderIds"], ["8", "10"])
        self.assertNotIn("11", rows[0]["entryOrderIds"])

    def test_candles_cache_does_not_leak_across_demo_and_live(self):
        from r20_exchange.candle_cache import clear
        clear()
        now = 1_700_000_000_000

        def kline(close: str):
            return [now, "1", "2", "0.5", close, "10", now + 1000, "15"]

        demo_session = FakeSession()
        live_session = FakeSession()
        demo_session.on("GET", "/fapi/v1/klines", [kline("101")])
        live_session.on("GET", "/fapi/v1/klines", [kline("202")])
        demo = BinanceExchange(FakeEnv("demo"), session=demo_session)
        live = BinanceExchange(FakeEnv("live"), session=live_session)
        demo_rows = demo.candles(INST, "15m", 24)
        live_rows = live.candles(INST, "15m", 24)
        self.assertEqual(demo_rows[0][4], "101")
        self.assertEqual(live_rows[0][4], "202")
        self.assertEqual(len(_calls(demo_session, "GET", "/fapi/v1/klines")), 1)
        demo.candles(INST, "15m", 24)
        self.assertEqual(len(_calls(demo_session, "GET", "/fapi/v1/klines")), 1)

    def test_public_klines_retries_once_on_429(self):
        from r20_exchange.candle_cache import clear
        clear()
        now = 1_700_000_000_000
        state = {"n": 0}

        def handler(call):
            state["n"] += 1
            if state["n"] == 1:
                resp = FakeResponse({"code": -1003, "msg": "too many"}, status_code=429)
                resp.headers["Retry-After"] = "0.2"
                return resp
            return [[now, "1", "2", "0.5", "9", "10", now + 1000, "15"]]

        self.session.on("GET", "/fapi/v1/klines", handler)
        with patch("r20_exchange.binance.time.sleep", return_value=None) as slept:
            rows = self._exchange().candles(INST, "15m", 24)
        self.assertEqual(rows[0][4], "9")
        self.assertEqual(state["n"], 2)
        slept.assert_called_once()


    def test_symbol_filters_use_exact_match_not_first_row(self):
        install_defaults(self.session, hedge=True)
        self.session.on("GET", "/fapi/v1/exchangeInfo", MIXED_INFO)
        posted = []

        def post_order(call):
            posted.append(call["query"])
            return {
                "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": call["query"]["symbol"], "side": "BUY",
                "positionSide": "LONG", "origQty": call["query"]["quantity"], "executedQty": "0",
                "price": call["query"]["price"], "avgPrice": "0", "status": "NEW", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        self.session.on("POST", "/fapi/v1/order", post_order)
        ex = self._exchange()
        link = ex.place_protected_limit_order("LINK-USDT-SWAP", "buy", "long", "1", "10.5", "12.5", "9.5")
        self.assertEqual(link["protection_status"], "awaiting_fill")
        self.assertEqual(posted[-1]["symbol"], "LINKUSDT")
        self.assertEqual(posted[-1]["price"], "10.5")
        uni_env = FakeEnv(api_key="unikey", secret_key="unisecret")
        uni = BinanceExchange(uni_env, session=self.session).place_protected_limit_order("UNI-USDT-SWAP", "buy", "long", "1", "10.5", "12.5", "9.5")
        self.assertEqual(uni["protection_status"], "awaiting_fill")
        xrp_env = FakeEnv(api_key="xrpkey", secret_key="xrpsecret")
        xrp = BinanceExchange(xrp_env, session=self.session).place_protected_limit_order("XRP-USDT-SWAP", "buy", "long", "10", "2.05", "2.09", "2.03")
        self.assertEqual(xrp["protection_status"], "awaiting_fill")
        self.assertEqual(posted[-1]["price"], "2.05")
        self.assertNotEqual(posted[-1]["price"], posted[-1].get("unused"))
        self.assertEqual(_calls(self.session, "POST", "/fapi/v1/algoOrder"), [])
        with self.assertRaises(RuntimeError):
            ex._filters.clear()
            ex._symbol_filters("DOGE-USDT-SWAP")

    def test_duplicate_and_malformed_symbol_rows_are_rejected(self):
        install_defaults(self.session, hedge=True)
        dup = {"symbols": [MIXED_INFO["symbols"][0], dict(MIXED_INFO["symbols"][0])]}
        self.session.on("GET", "/fapi/v1/exchangeInfo", dup)
        with self.assertRaises(RuntimeError):
            self._exchange()._symbol_filters(INST)
        bad = {"symbols": [{**MIXED_INFO["symbols"][0], "symbol": "BTCUSDT", "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0", "minPrice": "0.1"}]}]}
        self.session.on("GET", "/fapi/v1/exchangeInfo", bad)
        ex = self._exchange()
        ex._filters.clear()
        with self.assertRaises(RuntimeError):
            ex._symbol_filters(INST)

    def test_filled_entry_places_hedge_protection_without_quantity(self):
        state = install_defaults(self.session, hedge=True)

        def post_order(call):
            state["positions"] = [dict(LONG_POS)]
            return {
                "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
                "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.001", "price": "50000",
                "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("POST", "/fapi/v1/algoOrder", lambda call: {
            "algoId": 1, "clientAlgoId": call["query"].get("clientAlgoId"), "algoStatus": "NEW",
            "closePosition": True, "orderType": call["query"].get("type"),
            "triggerPrice": call["query"].get("triggerPrice"),
        })
        result = self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(result["protection_status"], "protected")
        algos = _calls(self.session, "POST", "/fapi/v1/algoOrder")
        self.assertEqual(len(algos), 2)
        for algo in algos:
            query = algo["query"]
            self.assertEqual(query.get("algoType"), "CONDITIONAL")
            self.assertEqual(query.get("closePosition"), "true")
            self.assertEqual(query.get("positionSide"), "LONG")
            self.assertEqual(query.get("side"), "SELL")
            self.assertNotIn("quantity", query)
            self.assertNotIn("reduceOnly", query)
        self.assertTrue(algos[0]["query"]["clientAlgoId"].endswith("SL"))
        self.assertTrue(algos[1]["query"]["clientAlgoId"].endswith("TP"))
        self.assertFalse(self._exchange().has_pending_entries())


if __name__ == "__main__":
    unittest.main()

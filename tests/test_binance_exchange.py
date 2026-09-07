"""Offline Binance USDⓈ-M adapter tests. No network, no credentials, injectable transport."""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
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
        return {
            "algoId": 7,
            "clientAlgoId": cid,
            "algoStatus": "NEW",
            "closePosition": True,
            "symbol": "BTCUSDT",
            "positionSide": "LONG" if hedge else "BOTH",
            "triggerPrice": "49000" if cid.endswith("SL") or cid.endswith("SX") else "52000",
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
        self.hex_patch = patch("r20_exchange.binance.secrets.token_hex", return_value=GROUP)
        self.hex_patch.start()

    def tearDown(self) -> None:
        self.hex_patch.stop()

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
        self.assertEqual(order["ordId"], "11")

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

    def test_hedge_and_oneway_protection_parameters(self):
        install_defaults(self.session, hedge=True)
        self.session.on("POST", "/fapi/v1/order", {
            "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
            "positionSide": "LONG", "origQty": "0.001", "executedQty": "0", "price": "50000",
            "avgPrice": "0", "status": "NEW", "reduceOnly": False, "time": 1, "updateTime": 1,
        })
        self.session.on("POST", "/fapi/v1/algoOrder", {
            "algoId": 1, "clientAlgoId": SL_ID, "algoStatus": "NEW", "closePosition": True,
        })
        self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        entry = _calls(self.session, "POST", "/fapi/v1/order")[0]["query"]
        self.assertEqual(entry.get("positionSide"), "LONG")
        self.assertNotIn("reduceOnly", entry)
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

        oneway = FakeSession()
        install_defaults(oneway, hedge=False)
        oneway.on("POST", "/fapi/v1/order", {
            "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
            "positionSide": "BOTH", "origQty": "0.001", "executedQty": "0", "price": "50000",
            "avgPrice": "0", "status": "NEW", "reduceOnly": False, "time": 1, "updateTime": 1,
        })
        oneway.on("POST", "/fapi/v1/algoOrder", {"algoId": 1, "algoStatus": "NEW", "closePosition": True})
        BinanceExchange(self.env, session=oneway).place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        one_entry = _calls(oneway, "POST", "/fapi/v1/order")[0]["query"]
        self.assertEqual(one_entry.get("positionSide"), "BOTH")
        self.assertNotIn("reduceOnly", one_entry)
        for algo in _calls(oneway, "POST", "/fapi/v1/algoOrder"):
            self.assertEqual(algo["query"].get("positionSide"), "BOTH")
            self.assertEqual(algo["query"].get("closePosition"), "true")
            self.assertNotIn("quantity", algo["query"])
            self.assertNotIn("reduceOnly", algo["query"])

    def test_second_leg_failure_keeps_stop_until_flat(self):
        state = install_defaults(self.session, hedge=True, filled="0.001")
        state["positions"] = [dict(LONG_POS)]

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
            return {
                "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
                "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.001", "price": "50000",
                "avgPrice": "50000", "status": "FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
            }

        def post_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            if cid.endswith("TP"):
                return FakeResponse({"code": -2021, "msg": "Order would immediately trigger."})
            return {"algoId": 1, "clientAlgoId": cid, "algoStatus": "NEW", "closePosition": True}

        self.session.on("POST", "/fapi/v1/order", post_order)
        self.session.on("POST", "/fapi/v1/algoOrder", post_algo)
        self.session.on("DELETE", "/fapi/v1/algoOrder", {"algoId": 7, "status": "CANCELED"})
        with self.assertRaises(RuntimeError):
            self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        close_posts = [call for call in _calls(self.session, "POST", "/fapi/v1/order") if call["query"].get("type") == "MARKET"]
        self.assertEqual(len(close_posts), 1)
        close = close_posts[0]["query"]
        self.assertEqual(close.get("quantity"), "0.001")
        self.assertEqual(close.get("side"), "SELL")
        self.assertEqual(close.get("positionSide"), "LONG")
        self.assertNotIn("closePosition", close)
        self.assertNotIn("reduceOnly", close)
        self.assertTrue(_calls(self.session, "DELETE", "/fapi/v1/order"))
        delete_indexes = [index for index, call in enumerate(self.session.calls) if call["method"] == "DELETE" and call["path"] == "/fapi/v1/algoOrder"]
        close_index = next(index for index, call in enumerate(self.session.calls) if call["method"] == "POST" and call["path"] == "/fapi/v1/order" and call["query"].get("type") == "MARKET")
        self.assertTrue(delete_indexes)
        self.assertGreater(min(delete_indexes), close_index)

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
        self.assertEqual(result["protection_mechanism"], "paired_conditional")
        self.assertEqual(len(_calls(self.session, "POST", "/fapi/v1/algoOrder")), 2)

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

    def _fail_second_leg(self) -> None:
        def post_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            if cid.endswith("TP"):
                return FakeResponse({"code": -2021, "msg": "Order would immediately trigger."})
            return {"algoId": 1, "clientAlgoId": cid, "algoStatus": "NEW", "closePosition": True}

        self.session.on("POST", "/fapi/v1/order", {
            "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
            "positionSide": "LONG", "origQty": "0.001", "executedQty": "0", "price": "50000",
            "avgPrice": "0", "status": "NEW", "reduceOnly": False, "time": 1, "updateTime": 1,
        })
        self.session.on("POST", "/fapi/v1/algoOrder", post_algo)

    def test_unknown_cancel_does_not_drop_protection(self):
        install_defaults(self.session, hedge=True)
        self._fail_second_leg()
        self.session.on("DELETE", "/fapi/v1/order", lambda _c: TimeoutError("cancel unknown"))
        self.session.on("GET", "/fapi/v1/order", {
            "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
            "positionSide": "LONG", "origQty": "0.001", "executedQty": "0", "price": "50000",
            "avgPrice": "0", "status": "NEW", "reduceOnly": False, "time": 1, "updateTime": 1,
        })
        with self.assertRaises(RuntimeError):
            self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(_calls(self.session, "DELETE", "/fapi/v1/algoOrder"), [])
        self.assertEqual([call for call in _calls(self.session, "POST", "/fapi/v1/order") if call["query"].get("type") == "MARKET"], [])

    def test_non_terminal_entry_does_not_drop_protection(self):
        install_defaults(self.session, hedge=True, positions=[dict(LONG_POS)])
        self._fail_second_leg()
        self.session.on("GET", "/fapi/v1/order", {
            "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
            "positionSide": "LONG", "origQty": "0.001", "executedQty": "0.001", "price": "50000",
            "avgPrice": "50000", "status": "PARTIALLY_FILLED", "reduceOnly": False, "time": 1, "updateTime": 1,
        })
        with self.assertRaises(RuntimeError):
            self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(_calls(self.session, "DELETE", "/fapi/v1/algoOrder"), [])
        self.assertEqual([call for call in _calls(self.session, "POST", "/fapi/v1/order") if call["query"].get("type") == "MARKET"], [])

    def test_positions_history_refuses_unprovable_entry(self):
        install_defaults(self.session, hedge=True)
        self.session.on("GET", "/fapi/v1/income", [{"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "-1", "asset": "USDT", "time": 1, "tranId": 1}])
        self.session.on("GET", "/fapi/v1/userTrades", [{
            "symbol": "BTCUSDT", "id": 1, "orderId": 8, "side": "SELL", "positionSide": "LONG",
            "price": "50000", "qty": "0.001", "realizedPnl": "-1", "commission": "-0.02",
            "commissionAsset": "USDT", "time": 1,
        }])
        with self.assertRaises(RuntimeError) as raised:
            self._exchange().positions_history()
        self.assertTrue(getattr(raised.exception, "incomplete", False))
        self.assertEqual(getattr(raised.exception, "status", ""), "unavailable")

    def test_positions_history_keeps_fee_out_of_pnl(self):
        install_defaults(self.session, hedge=True)
        self.session.on("GET", "/fapi/v1/income", [{"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "8", "asset": "USDT", "time": 2, "tranId": 1}])
        self.session.on("GET", "/fapi/v1/userTrades", [
            {"symbol": "BTCUSDT", "id": 1, "orderId": 8, "side": "BUY", "positionSide": "LONG", "price": "50000", "qty": "0.001", "realizedPnl": "0", "commission": "0.02", "commissionAsset": "USDT", "time": 1},
            {"symbol": "BTCUSDT", "id": 2, "orderId": 9, "side": "SELL", "positionSide": "LONG", "price": "58000", "qty": "0.001", "realizedPnl": "8", "commission": "0.02", "commissionAsset": "USDT", "time": 2},
        ])
        rows = self._exchange().positions_history()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pnl"], "8")
        self.assertEqual(rows[0]["fee"], "-0.04")
        self.assertEqual(rows[0]["direction"], "long")
        self.assertEqual(rows[0]["instId"], INST)

    def test_positions_history_rejects_mid_window_add_reduce(self):
        install_defaults(self.session, hedge=True, positions=[{**LONG_POS, "positionAmt": "1"}])
        self.session.on("GET", "/fapi/v1/income", [{"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "50", "asset": "USDT", "time": 11, "tranId": 1}])
        self.session.on("GET", "/fapi/v1/userTrades", [
            {"symbol": "BTCUSDT", "id": 10, "orderId": 10, "side": "BUY", "positionSide": "LONG", "price": "60000", "qty": "0.001", "realizedPnl": "0", "commission": "0.01", "commissionAsset": "USDT", "time": 10},
            {"symbol": "BTCUSDT", "id": 11, "orderId": 11, "side": "SELL", "positionSide": "LONG", "price": "60000", "qty": "0.001", "realizedPnl": "50", "commission": "0.01", "commissionAsset": "USDT", "time": 11},
        ])
        with self.assertRaises(RuntimeError) as raised:
            self._exchange().positions_history()
        self.assertTrue(getattr(raised.exception, "incomplete", False))

    def test_positions_history_splits_oneway_zero_cross(self):
        install_defaults(self.session, hedge=False)
        self.session.on("GET", "/fapi/v1/income", [{"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "15", "asset": "USDT", "time": 3, "tranId": 1}])
        self.session.on("GET", "/fapi/v1/userTrades", [
            {"symbol": "BTCUSDT", "id": 1, "orderId": 1, "side": "BUY", "positionSide": "BOTH", "price": "100", "qty": "1", "realizedPnl": "0", "commission": "0.01", "commissionAsset": "USDT", "time": 1},
            {"symbol": "BTCUSDT", "id": 2, "orderId": 2, "side": "SELL", "positionSide": "BOTH", "price": "110", "qty": "2", "realizedPnl": "10", "commission": "0.02", "commissionAsset": "USDT", "time": 2},
            {"symbol": "BTCUSDT", "id": 3, "orderId": 3, "side": "BUY", "positionSide": "BOTH", "price": "105", "qty": "1", "realizedPnl": "5", "commission": "0.01", "commissionAsset": "USDT", "time": 3},
        ])
        rows = self._exchange().positions_history()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["direction"], "long")
        self.assertEqual(rows[1]["closeTotalPos"], "1")
        self.assertEqual(rows[0]["direction"], "short")
        self.assertEqual(rows[0]["closeTotalPos"], "1")
        self.assertEqual(rows[0]["openAvgPx"], "110")
        self.assertEqual(rows[0]["closeAvgPx"], "105")
        self.assertEqual(rows[0]["pnl"], "5")
        self.assertIsNone(rows[0]["lever"])
        self.assertEqual(sum(Decimal(row["fee"]) for row in rows), Decimal("-0.04"))

    def test_positions_history_bnb_fee_is_incomplete(self):
        install_defaults(self.session, hedge=True)
        self.session.on("GET", "/fapi/v1/income", [{"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "8", "asset": "USDT", "time": 2, "tranId": 1}])
        self.session.on("GET", "/fapi/v1/userTrades", [
            {"symbol": "BTCUSDT", "id": 1, "orderId": 8, "side": "BUY", "positionSide": "LONG", "price": "50000", "qty": "0.001", "realizedPnl": "0", "commission": "0.001", "commissionAsset": "BNB", "time": 1},
            {"symbol": "BTCUSDT", "id": 2, "orderId": 9, "side": "SELL", "positionSide": "LONG", "price": "58000", "qty": "0.001", "realizedPnl": "8", "commission": "0.001", "commissionAsset": "BNB", "time": 2},
        ])
        with self.assertRaises(RuntimeError) as raised:
            self._exchange().positions_history()
        self.assertTrue(getattr(raised.exception, "incomplete", False))

    def test_positions_history_full_window_page_is_incomplete(self):
        install_defaults(self.session, hedge=True)
        self.session.on("GET", "/fapi/v1/income", [{"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "1", "asset": "USDT", "time": 1, "tranId": 1}])
        page = [{"symbol": "BTCUSDT", "id": i, "orderId": i, "side": "BUY", "positionSide": "LONG", "price": "1", "qty": "0.001", "realizedPnl": "0", "commission": "0", "time": i} for i in range(1000)]
        self.session.on("GET", "/fapi/v1/userTrades", page)
        with self.assertRaises(RuntimeError) as raised:
            self._exchange().positions_history()
        self.assertTrue(getattr(raised.exception, "window_limited", False))

    def test_sl_timeout_is_reconciled_before_compensate(self):
        install_defaults(self.session, hedge=True)
        self.session.on("POST", "/fapi/v1/order", {
            "orderId": 11, "clientOrderId": ENTRY_ID, "symbol": "BTCUSDT", "side": "BUY",
            "positionSide": "LONG", "origQty": "0.001", "executedQty": "0", "price": "50000",
            "avgPrice": "0", "status": "NEW", "reduceOnly": False, "time": 1, "updateTime": 1,
        })

        def post_algo(call):
            cid = call["query"].get("clientAlgoId") or ""
            if cid.endswith("SL"):
                return FakeResponse({"code": -1007, "msg": "Timeout waiting for response from backend server. Send status unknown; execution status unknown."})
            return {"algoId": 2, "clientAlgoId": cid, "algoStatus": "NEW", "closePosition": True}

        self.session.on("POST", "/fapi/v1/algoOrder", post_algo)
        self.session.on("GET", "/fapi/v1/algoOrder", lambda call: {
            "algoId": 7 if call["query"]["clientAlgoId"] == SL_ID else 8,
            "clientAlgoId": call["query"]["clientAlgoId"], "algoStatus": "NEW", "closePosition": True,
            "symbol": "BTCUSDT", "positionSide": "LONG",
            "triggerPrice": "49000" if call["query"]["clientAlgoId"] == SL_ID else "52000",
        })
        result = self._exchange().place_protected_limit_order(INST, "buy", "long", "0.001", "50000", "52000", "49000")
        self.assertEqual(result["protection_mechanism"], "paired_conditional")
        stop_posts = [call for call in _calls(self.session, "POST", "/fapi/v1/algoOrder") if call["query"].get("clientAlgoId") == SL_ID]
        self.assertEqual(len(stop_posts), 1)

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

if __name__ == "__main__":
    unittest.main()

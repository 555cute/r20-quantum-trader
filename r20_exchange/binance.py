"""Binance USDⓈ-M USDT perpetual adapter. Internal quantities are base-asset Decimals."""
from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import time
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import requests
from scripts.order_risk import validate_quote_geometry_and_rr

if TYPE_CHECKING:
    from r20_exchange.runtime import ExchangeEnvironment

LIVE_HOST = "https://fapi.binance.com"
DEMO_HOST = "https://demo-fapi.binance.com"
RECV_WINDOW_MS = 5000
TRADE_WINDOW_MS = 7 * 24 * 60 * 60 * 1000
TRADE_LOOKBACK_MS = 90 * 24 * 60 * 60 * 1000
UNKNOWN_MUTATE_CODES = {-1000, -1006, -1007}
CLIENT_ID_RE = re.compile(r"^[.A-Z:/a-z0-9_-]{1,36}$")
INST_RE = re.compile(r"^([A-Z0-9]+)-USDT-SWAP$")
GROUP_RE = re.compile(r"^R20G([0-9a-f]{12})([A-Z]{2})$")
BAR_INTERVALS = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1H": "1h",
    "1h": "1h",
    "2H": "2h",
    "2h": "2h",
    "4H": "4h",
    "4h": "4h",
    "6H": "6h",
    "6h": "6h",
    "8H": "8h",
    "8h": "8h",
    "12H": "12h",
    "12h": "12h",
    "1D": "1d",
    "1d": "1d",
    "1W": "1w",
    "1w": "1w",
    "1M": "1M",
}
ORDER_STATES = {
    "NEW": "live",
    "PARTIALLY_FILLED": "partially_filled",
    "FILLED": "filled",
    "CANCELED": "canceled",
    "EXPIRED": "canceled",
    "EXPIRED_IN_MATCH": "canceled",
    "REJECTED": "canceled",
}
TERMINAL_ORDER_STATES = {"FILLED", "CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED"}
UNKNOWN_BODY_HINTS = (
    "unknown error, please check your request",
    "request occur unknown error",
)
SENSITIVE_RE = re.compile(r"(signature|X-MBX-APIKEY|api[_-]?key|secret)\s*[=:]\s*[^&\s]+", re.I)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Exchange numeric values must be finite")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid exchange numeric value") from exc
    if not result.is_finite():
        raise ValueError("Exchange numeric values must be finite")
    return result


def _text(value: Decimal) -> str:
    return format(value, "f")


def _sanitize(message: Any, *tokens: str) -> str:
    text = SENSITIVE_RE.sub(r"\1=[redacted]", str(message))
    for token in tokens:
        if token:
            text = text.replace(token, "[redacted]")
    return text


def _host_for(mode: str) -> str:
    if mode == "live":
        return LIVE_HOST
    if mode == "demo":
        return DEMO_HOST
    raise ValueError("Unsupported Binance environment")


def _to_symbol(inst_id: str) -> str:
    match = INST_RE.fullmatch(str(inst_id or "").strip().upper())
    if not match:
        raise ValueError("Instrument is not a USDT perpetual contract")
    return f"{match.group(1)}USDT"


def _from_symbol(symbol: str) -> str:
    raw = str(symbol or "").upper()
    if not raw.endswith("USDT") or len(raw) <= 4:
        raise ValueError("Instrument is not a USDT perpetual contract")
    return f"{raw[:-4]}-USDT-SWAP"


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise ValueError("Filter step must be positive")
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step

class BinanceAPIError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code



def _bool_text(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


class UncertainSubmission(RuntimeError):
    def __init__(self, message: str, client_id: str = "") -> None:
        super().__init__(message)
        self.client_id = client_id


class BinanceExchange:
    def __init__(self, env: Any, session: Any = None) -> None:
        self.env = env
        self.session = session if session is not None else requests.Session()
        self._filters: dict[str, dict[str, Any]] = {}
        self._hedge: bool | None = None

    def close(self) -> None:
        closer = getattr(self.session, "close", None)
        if callable(closer):
            closer()

    def _base_url(self) -> str:
        return _host_for(str(getattr(self.env, "mode", "")).lower())

    def _api_key(self) -> str:
        return str(getattr(self.env, "api_key", "") or "")

    def _secret(self) -> str:
        return str(getattr(self.env, "secret_key", "") or "")

    def _configured(self) -> bool:
        configured = getattr(self.env, "configured", None)
        if configured is not None:
            return bool(configured)
        return bool(self._api_key() and self._secret())

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        signed: bool = False,
        mutate: bool = False,
        client_id: str = "",
    ) -> Any:
        method = method.upper()
        payload: dict[str, str] = {}
        for key, value in (params or {}).items():
            if value is None or value == "":
                continue
            if isinstance(value, bool):
                payload[key] = "true" if value else "false"
            else:
                payload[key] = str(value)
        if signed:
            if not self._configured():
                raise ValueError("Binance credentials are not configured")
            payload["timestamp"] = str(int(time.time() * 1000))
            payload["recvWindow"] = str(RECV_WINDOW_MS)
            unsigned = urlencode(payload)
            signature = hmac.new(self._secret().encode(), unsigned.encode(), hashlib.sha256).hexdigest()
            query = f"{unsigned}&signature={signature}"
            headers = {"X-MBX-APIKEY": self._api_key()}
        else:
            query = urlencode(payload)
            headers = {}
        url = self._base_url() + path + (f"?{query}" if query else "")
        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                timeout=15,
                allow_redirects=False,
            )
        except Exception as exc:
            message = _sanitize(exc, self._api_key(), self._secret())
            if mutate:
                raise UncertainSubmission(message, client_id) from exc
            raise RuntimeError(message) from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if 300 <= status < 400:
            raise RuntimeError("Binance redirected; refusing to follow credentials")
        body = getattr(response, "text", "") or ""
        if status == 503 and any(hint in body.lower() for hint in UNKNOWN_BODY_HINTS):
            if mutate:
                raise UncertainSubmission("Binance mutation result is unknown", client_id)
            raise RuntimeError("Binance request result is unknown")
        try:
            payload_json = response.json()
        except Exception as exc:
            message = _sanitize(f"Binance returned a non-JSON response (HTTP {status})", self._api_key(), self._secret())
            if mutate:
                raise UncertainSubmission(message, client_id) from exc
            raise RuntimeError(message) from exc
        if isinstance(payload_json, dict) and isinstance(payload_json.get("code"), int) and payload_json["code"] < 0:
            message = _sanitize(payload_json.get("msg") or "Binance request failed", self._api_key(), self._secret())
            code = int(payload_json["code"])
            if mutate and (code in UNKNOWN_MUTATE_CODES or status >= 500):
                raise UncertainSubmission(message, client_id)
            raise BinanceAPIError(code, message)
        if status >= 500 and mutate:
            raise UncertainSubmission(_sanitize(f"Binance request failed (HTTP {status})", self._api_key(), self._secret()), client_id)
        if status >= 400:
            raise RuntimeError(_sanitize(f"Binance request failed (HTTP {status})", self._api_key(), self._secret()))
        return payload_json

    def _public(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params, signed=False)

    def _private(self, method: str, path: str, params: dict[str, Any] | None = None, *, mutate: bool = False, client_id: str = "") -> Any:
        return self._request(method, path, params, signed=True, mutate=mutate, client_id=client_id)

    def _interval(self, bar: str) -> str:
        interval = BAR_INTERVALS.get(str(bar))
        if not interval:
            raise ValueError("Unsupported candle interval")
        return interval

    def _symbol_filters(self, inst_id: str) -> dict[str, Any]:
        if inst_id in self._filters:
            return self._filters[inst_id]
        symbol = _to_symbol(inst_id)
        info = self._public("/fapi/v1/exchangeInfo", {"symbol": symbol})
        rows = info.get("symbols") if isinstance(info, dict) else None
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("Binance exchangeInfo is unavailable")
        row = rows[0]
        if row.get("contractType") != "PERPETUAL" or row.get("quoteAsset") != "USDT":
            raise ValueError("Instrument is not a USDT perpetual contract")
        filters = {str(item.get("filterType")): item for item in row.get("filters") or [] if isinstance(item, dict)}
        lot = filters.get("LOT_SIZE") or {}
        price = filters.get("PRICE_FILTER") or {}
        notional = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
        parsed = {
            "instId": inst_id,
            "symbol": symbol,
            "baseCcy": str(row.get("baseAsset") or inst_id.split("-")[0]),
            "settleCcy": "USDT",
            "state": "live" if row.get("status") == "TRADING" else str(row.get("status") or ""),
            "tickSz": str(price.get("tickSize") or "0"),
            "lotSz": str(lot.get("stepSize") or "0"),
            "minSz": str(lot.get("minQty") or "0"),
            "minNotional": str(notional.get("notional") or notional.get("minNotional") or "0"),
            "minPrice": str(price.get("minPrice") or "0"),
            "maxPrice": str(price.get("maxPrice") or "0"),
        }
        self._filters[inst_id] = parsed
        return parsed

    def _align_price(self, inst_id: str, price: Any) -> str:
        meta = self._symbol_filters(inst_id)
        tick = _decimal(meta["tickSz"])
        value = _floor_step(_decimal(price), tick)
        minimum = _decimal(meta["minPrice"])
        if value <= 0 or (minimum > 0 and value < minimum):
            raise ValueError("Price is below the exchange tick")
        return _text(value)

    def _align_qty(self, inst_id: str, size: Any, price: Any | None = None) -> str:
        meta = self._symbol_filters(inst_id)
        step = _decimal(meta["lotSz"])
        minimum = _decimal(meta["minSz"])
        quantity = _floor_step(_decimal(size), step)
        if quantity <= 0 or quantity < minimum:
            raise ValueError("Order quantity is below the exchange minimum")
        if price is not None:
            notional = quantity * _decimal(price)
            floor = _decimal(meta["minNotional"])
            if floor > 0 and notional < floor:
                raise ValueError("Order notional is below the exchange minimum")
        return _text(quantity)

    def _hedge_mode(self) -> bool:
        if self._hedge is None:
            row = self._private("GET", "/fapi/v1/positionSide/dual")
            if not isinstance(row, dict) or "dualSidePosition" not in row:
                raise RuntimeError("Binance position mode is unavailable")
            self._hedge = _bool_text(row.get("dualSidePosition"))
        return self._hedge

    def _position_side(self, pos_side: str) -> str:
        side = str(pos_side or "").lower()
        hedge = self._hedge_mode()
        if hedge:
            if side not in {"long", "short"}:
                raise ValueError("Hedge mode requires long or short position side")
            return "LONG" if side == "long" else "SHORT"
        if side not in {"long", "short", "net"}:
            raise ValueError("Invalid position direction")
        return "BOTH"

    def _close_side(self, pos_side: str) -> str:
        side = str(pos_side or "").lower()
        if side == "long":
            return "SELL"
        if side == "short":
            return "BUY"
        raise ValueError("Invalid position direction")

    def _entry_side(self, side: str, pos_side: str) -> None:
        pair = (str(side).lower(), str(pos_side).lower())
        if pair not in {("buy", "long"), ("sell", "short")}:
            raise ValueError("Entry side does not match position direction")
    def _reject_mixed_exposure(self, inst_id: str, pos_side: str) -> None:
        if self._hedge_mode():
            return
        wanted = str(pos_side).lower()
        for row in self.positions(inst_id):
            qty = _decimal(row.get("pos") or "0")
            if qty == 0:
                continue
            existing = str(row.get("posSide") or "net").lower()
            if existing == "net":
                existing = "long" if qty > 0 else "short"
            if existing != wanted:
                raise ValueError("Conflicting opposite exposure in one-way mode")

    def _group_ids(self, group: str) -> dict[str, str]:
        ids = {
            "EN": f"R20G{group}EN",
            "SL": f"R20G{group}SL",
            "TP": f"R20G{group}TP",
            "SX": f"R20G{group}SX",
        }
        for value in ids.values():
            if not CLIENT_ID_RE.fullmatch(value):
                raise ValueError("Invalid client order id")
        return ids

    def _new_group(self) -> str:
        return secrets.token_hex(6)

    def _parse_group(self, client_id: str) -> tuple[str, str] | None:
        match = GROUP_RE.fullmatch(str(client_id or ""))
        if not match:
            return None
        return match.group(1), match.group(2)

    def _query_order(self, symbol: str, *, order_id: str = "", client_id: str = "") -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": symbol}
        if order_id:
            params["orderId"] = order_id
        if client_id:
            params["origClientOrderId"] = client_id
        row = self._private("GET", "/fapi/v1/order", params)
        if not isinstance(row, dict):
            raise RuntimeError("Binance order query failed")
        return row

    def _entry_live_or_filled(self, order: dict[str, Any]) -> bool:
        status = str(order.get("status") or "").upper()
        filled = _decimal(order.get("executedQty") or "0")
        if status in {"NEW", "PARTIALLY_FILLED", "FILLED"}:
            return True
        return status in TERMINAL_ORDER_STATES and filled > 0

    def _place_order(self, params: dict[str, Any], client_id: str) -> dict[str, Any]:
        if str(params.get("type") or "").upper() == "MARKET" and "closePosition" in params:
            raise ValueError("MARKET orders cannot include closePosition")
        try:
            row = self._private("POST", "/fapi/v1/order", params, mutate=True, client_id=client_id)
        except UncertainSubmission:
            try:
                found = self._query_order(str(params["symbol"]), client_id=client_id)
            except Exception as exc:
                raise RuntimeError("Binance order submission is unknown and could not be reconciled") from exc
            if str(params.get("type") or "").upper() != "MARKET" and not self._entry_live_or_filled(found):
                raise RuntimeError("Entry order was not accepted")
            return found
        if not isinstance(row, dict) or not row.get("orderId"):
            raise RuntimeError("Binance did not return a verifiable order ID")
        if str(params.get("type") or "").upper() != "MARKET" and not self._entry_live_or_filled(row):
            raise RuntimeError("Entry order was not accepted")
        return row

    def _query_algo(self, client_id: str) -> dict[str, Any]:
        if not CLIENT_ID_RE.fullmatch(client_id):
            raise ValueError("Invalid protective client order ID")
        row = self._private("GET", "/fapi/v1/algoOrder", {"clientAlgoId": client_id})
        if not isinstance(row, dict) or not row.get("algoId"):
            raise RuntimeError("Binance protective order query is unavailable")
        if row.get("clientAlgoId") not in (None, "", client_id):
            raise RuntimeError("Binance protective order identity does not match")
        return row

    def _place_algo(self, params: dict[str, Any], client_id: str) -> dict[str, Any]:
        try:
            row = self._private("POST", "/fapi/v1/algoOrder", params, mutate=True, client_id=client_id)
        except UncertainSubmission:
            try:
                found = self._query_algo(client_id)
            except Exception as exc:
                raise RuntimeError("Binance algo submission is unknown and could not be reconciled") from exc
            return found
        if not isinstance(row, dict):
            raise RuntimeError("Binance did not acknowledge algo order")
        status = str(row.get("algoStatus") or row.get("status") or "")
        if status and status not in {"NEW", "RUNNING", "TRIGGERING"}:
            raise RuntimeError("Binance algo order was not accepted")
        return row

    def _cancel_order(self, symbol: str, *, order_id: str = "", client_id: str = "") -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": symbol}
        if order_id:
            params["orderId"] = order_id
        if client_id:
            params["origClientOrderId"] = client_id
        return self._private("DELETE", "/fapi/v1/order", params, mutate=True, client_id=client_id)

    def _cancel_algo(self, client_algo_id: str) -> None:
        try:
            self._private("DELETE", "/fapi/v1/algoOrder", {"clientAlgoId": client_algo_id}, mutate=True, client_id=client_algo_id)
        except UncertainSubmission:
            try:
                row = self._query_algo(client_algo_id)
            except BinanceAPIError as exc:
                if exc.code in {-2011, -2013}:
                    return
                raise
            status = str(row.get("algoStatus") or row.get("status") or "").upper()
            if status not in {"CANCELED", "CANCELLED", "EXPIRED", "REJECTED", "FINISHED", "TRIGGERED"}:
                raise RuntimeError("Binance algo cancellation is unknown")
        except BinanceAPIError as exc:
            if exc.code not in {-2011, -2013}:
                raise

    def _open_algos(self, inst_id: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"algoType": "CONDITIONAL"}
        if inst_id:
            params["symbol"] = _to_symbol(inst_id)
        rows = self._private("GET", "/fapi/v1/openAlgoOrders", params)
        if rows is None:
            raise RuntimeError("Binance open algo orders are unavailable")
        if not isinstance(rows, list):
            raise RuntimeError("Binance open algo orders are unavailable")
        return [row for row in rows if isinstance(row, dict)]

    def _live_position_qty(self, inst_id: str, pos_side: str) -> Decimal:
        wanted = str(pos_side).lower()
        total = Decimal("0")
        for row in self.positions(inst_id):
            side = str(row.get("posSide") or "").lower()
            qty = _decimal(row.get("pos") or "0")
            if side == "net":
                if wanted == "long" and qty > 0:
                    total += qty
                elif wanted == "short" and qty < 0:
                    total += abs(qty)
                elif wanted == "net":
                    total += abs(qty)
            elif side == wanted:
                total += abs(qty)
        return total

    def _protection_params(self, inst_id: str, pos_side: str, trigger: str, kind: str, client_id: str) -> dict[str, Any]:
        hedge = self._hedge_mode()
        params: dict[str, Any] = {
            "algoType": "CONDITIONAL",
            "symbol": _to_symbol(inst_id),
            "side": self._close_side(pos_side),
            "type": "STOP_MARKET" if kind == "SL" else "TAKE_PROFIT_MARKET",
            "positionSide": self._position_side(pos_side),
            "triggerPrice": trigger,
            "workingType": "MARK_PRICE",
            "closePosition": "true",
            "clientAlgoId": client_id,
        }
        if hedge:
            # Hedge mode must not send reduceOnly; closePosition forbids quantity.
            params.pop("reduceOnly", None)
            params.pop("quantity", None)
        else:
            params.pop("quantity", None)
            params.pop("reduceOnly", None)
        return params

    def _confirm_algo(self, client_id: str) -> dict[str, Any]:
        row = self._query_algo(client_id)
        status = str(row.get("algoStatus") or row.get("status") or "")
        if status not in {"NEW", "RUNNING", "TRIGGERING"}:
            raise RuntimeError("Protective algo leg was not confirmed live")
        return row

    def _cancel_group(self, group: str) -> None:
        ids = self._group_ids(group)
        for kind in ("SL", "TP", "SX"):
            self._cancel_algo(ids[kind])

    def _sweep_orphans(self, inst_id: str, pos_side: str | None = None) -> None:
        live = {str(row.get("posSide") or "").lower(): _decimal(row.get("pos") or "0") for row in self.positions(inst_id)}
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for row in self._open_algos(inst_id):
            parsed = self._parse_group(str(row.get("clientAlgoId") or ""))
            if not parsed:
                continue
            group, kind = parsed
            grouped.setdefault(group, {})[kind] = row
        for group, legs in grouped.items():
            sample = next(iter(legs.values()))
            native_side = str(sample.get("positionSide") or "BOTH").upper()
            logical = {"LONG": "long", "SHORT": "short", "BOTH": "net"}.get(native_side, "net")
            if pos_side and logical not in {pos_side, "net"}:
                continue
            qty = live.get(logical, Decimal("0"))
            if logical == "net":
                qty = sum((abs(v) for v in live.values()), Decimal("0"))
            complete = ("TP" in legs) and ("SL" in legs or "SX" in legs)
            if qty == 0 or not complete:
                self._cancel_group(group)

    def _normalize_order(self, row: dict[str, Any]) -> dict[str, Any]:
        native_side = str(row.get("positionSide") or "BOTH").upper()
        pos_side = {"LONG": "long", "SHORT": "short"}.get(native_side, "net")
        return {
            "instId": _from_symbol(str(row.get("symbol"))),
            "ordId": str(row.get("orderId") or ""),
            "clOrdId": str(row.get("clientOrderId") or ""),
            "posSide": pos_side,
            "side": str(row.get("side") or "").lower(),
            "state": ORDER_STATES.get(str(row.get("status") or ""), str(row.get("status") or "").lower()),
            "sz": str(row.get("origQty") or "0"),
            "accFillSz": str(row.get("executedQty") or "0"),
            "px": str(row.get("price") or "0"),
            "avgPx": str(row.get("avgPrice") or "0"),
            "reduceOnly": "true" if _bool_text(row.get("reduceOnly")) else "false",
            "cTime": str(row.get("time") or row.get("updateTime") or ""),
            "uTime": str(row.get("updateTime") or row.get("time") or ""),
            "quantity_unit": "base",
        }

    def _ticker_row(self, stats: dict[str, Any], book: dict[str, Any] | None = None) -> dict[str, Any]:
        symbol = str(stats.get("symbol") or (book or {}).get("symbol") or "")
        book = book or {}
        return {
            "instId": _from_symbol(symbol),
            "last": str(stats.get("lastPrice") or "0"),
            "bidPx": str(book.get("bidPrice") or stats.get("bidPrice") or "0"),
            "askPx": str(book.get("askPrice") or stats.get("askPrice") or "0"),
            "open24h": str(stats.get("openPrice") or "0"),
            "high24h": str(stats.get("highPrice") or "0"),
            "low24h": str(stats.get("lowPrice") or "0"),
            "vol24h": str(stats.get("volume") or "0"),
            "volCcy24h": str(stats.get("quoteVolume") or "0"),
            "ts": str(stats.get("closeTime") or stats.get("time") or ""),
        }

    def instruments(self, inst_id: str | None = None) -> list[dict[str, Any]]:
        if inst_id:
            meta = self._symbol_filters(inst_id)
            if meta["state"] != "live":
                return []
            return [{
                "instId": meta["instId"],
                "baseCcy": meta["baseCcy"],
                "settleCcy": "USDT",
                "state": "live",
                "ctVal": "1",
                "nativeCtVal": "1",
                "lotSz": meta["lotSz"],
                "minSz": meta["minSz"],
                "tickSz": meta["tickSz"],
                "minNotional": meta["minNotional"],
                "quantity_unit": "base",
            }]
        info = self._public("/fapi/v1/exchangeInfo")
        rows = info.get("symbols") if isinstance(info, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("Binance exchangeInfo is unavailable")
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("contractType") != "PERPETUAL" or row.get("quoteAsset") != "USDT" or row.get("status") != "TRADING":
                continue
            try:
                mapped = _from_symbol(str(row["symbol"]))
            except ValueError:
                continue
            filters = {str(item.get("filterType")): item for item in row.get("filters") or [] if isinstance(item, dict)}
            lot = filters.get("LOT_SIZE") or {}
            price = filters.get("PRICE_FILTER") or {}
            notional = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
            parsed = {
                "instId": mapped,
                "symbol": str(row["symbol"]),
                "baseCcy": str(row.get("baseAsset") or mapped.split("-")[0]),
                "settleCcy": "USDT",
                "state": "live",
                "tickSz": str(price.get("tickSize") or "0"),
                "lotSz": str(lot.get("stepSize") or "0"),
                "minSz": str(lot.get("minQty") or "0"),
                "minNotional": str(notional.get("notional") or notional.get("minNotional") or "0"),
                "minPrice": str(price.get("minPrice") or "0"),
                "maxPrice": str(price.get("maxPrice") or "0"),
            }
            self._filters[mapped] = parsed
            result.append({
                "instId": mapped,
                "baseCcy": parsed["baseCcy"],
                "settleCcy": "USDT",
                "state": "live",
                "ctVal": "1",
                "nativeCtVal": "1",
                "lotSz": parsed["lotSz"],
                "minSz": parsed["minSz"],
                "tickSz": parsed["tickSz"],
                "minNotional": parsed["minNotional"],
                "quantity_unit": "base",
            })
        return result

    def ticker(self, inst_id: str) -> dict[str, Any]:
        symbol = _to_symbol(inst_id)
        stats = self._public("/fapi/v1/ticker/24hr", {"symbol": symbol})
        book = self._public("/fapi/v1/ticker/bookTicker", {"symbol": symbol})
        if not isinstance(stats, dict) or not isinstance(book, dict):
            raise RuntimeError("Binance ticker unavailable")
        return self._ticker_row(stats, book)

    def tickers(self) -> dict[str, dict[str, Any]]:
        stats = self._public("/fapi/v1/ticker/24hr")
        books = self._public("/fapi/v1/ticker/bookTicker")
        if not isinstance(stats, list) or not isinstance(books, list):
            raise RuntimeError("Binance tickers unavailable")
        book_map = {str(row.get("symbol")): row for row in books if isinstance(row, dict)}
        live = {item["instId"] for item in self.instruments()}
        result: dict[str, dict[str, Any]] = {}
        for row in stats:
            if not isinstance(row, dict):
                continue
            try:
                inst_id = _from_symbol(str(row.get("symbol")))
            except ValueError:
                continue
            if inst_id not in live:
                continue
            result[inst_id] = self._ticker_row(row, book_map.get(str(row.get("symbol"))))
        return result

    def candles(self, inst_id: str, bar: str = "15m", limit: int = 100) -> list[list[str]]:
        symbol = _to_symbol(inst_id)
        rows = self._public(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": self._interval(bar), "limit": min(int(limit), 1500)},
        )
        if not isinstance(rows, list):
            raise RuntimeError("Binance candles unavailable")
        now = int(time.time() * 1000)
        result: list[list[str]] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 8:
                continue
            close_time = int(row[6])
            confirm = "0" if close_time > now else "1"
            quote = str(row[7])
            result.append([str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]), str(row[5]), quote, quote, confirm])
        result.reverse()
        return result

    def orderbook(self, inst_id: str, sz: int = 5) -> dict[str, Any]:
        symbol = _to_symbol(inst_id)
        row = self._public("/fapi/v1/depth", {"symbol": symbol, "limit": int(sz)})
        if not isinstance(row, dict) or "bids" not in row or "asks" not in row:
            raise RuntimeError("Binance orderbook unavailable")
        return {"bids": row.get("bids") or [], "asks": row.get("asks") or []}

    def funding_rate(self, inst_id: str) -> float:
        row = self._public("/fapi/v1/premiumIndex", {"symbol": _to_symbol(inst_id)})
        if not isinstance(row, dict) or row.get("lastFundingRate") in (None, ""):
            raise RuntimeError("Binance funding rate unavailable")
        return float(_decimal(row["lastFundingRate"]) * 100)

    def open_interest(self, inst_id: str) -> dict[str, Any]:
        symbol = _to_symbol(inst_id)
        oi = self._public("/fapi/v1/openInterest", {"symbol": symbol})
        mark = self._public("/fapi/v1/premiumIndex", {"symbol": symbol})
        if not isinstance(oi, dict) or oi.get("openInterest") in (None, ""):
            raise RuntimeError("Binance open interest unavailable")
        base = _decimal(oi["openInterest"])
        price = _decimal((mark or {}).get("markPrice") or "0") if isinstance(mark, dict) else Decimal("0")
        return {"oi": _text(base), "oiCcy": _text(base), "oiUsd": _text(base * price), "ts": str(oi.get("time") or "")}

    def long_short_ratio(self, inst_id: str) -> float | None:
        try:
            rows = self._public(
                "/futures/data/globalLongShortAccountRatio",
                {"symbol": _to_symbol(inst_id), "period": "5m", "limit": 1},
            )
        except RuntimeError:
            return None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return None
        ratio = rows[0].get("longShortRatio")
        if ratio in (None, ""):
            return None
        return float(_decimal(ratio))

    def taker_volume(self, inst_id: str) -> dict[str, Any] | None:
        try:
            rows = self._public(
                "/futures/data/takerlongshortRatio",
                {"symbol": _to_symbol(inst_id), "period": "5m", "limit": 1},
            )
        except RuntimeError:
            return None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return None
        row = rows[0]
        buy = row.get("buyVol")
        sell = row.get("sellVol")
        if buy in (None, "") or sell in (None, ""):
            return None
        return {"buyVol": str(buy), "sellVol": str(sell)}

    def balance(self) -> list[dict[str, Any]]:
        row = self._private("GET", "/fapi/v2/account")
        if not isinstance(row, dict):
            raise RuntimeError("Binance account snapshot is unavailable")
        details = []
        for asset in row.get("assets") or []:
            if not isinstance(asset, dict) or asset.get("asset") != "USDT":
                continue
            details.append({
                "ccy": "USDT",
                "eq": str(asset.get("marginBalance") or "0"),
                "availBal": str(asset.get("availableBalance") or "0"),
                "cashBal": str(asset.get("walletBalance") or "0"),
                "upl": str(asset.get("unrealizedProfit") or "0"),
            })
        if not details:
            raise RuntimeError("Binance USDT balance is unavailable")
        return [{"totalEq": str(row.get("totalMarginBalance") or details[0]["eq"]), "details": details}]

    def positions(self, inst_id: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": _to_symbol(inst_id)} if inst_id else None
        rows = self._private("GET", "/fapi/v2/positionRisk", params)
        if not isinstance(rows, list):
            raise RuntimeError("Binance positions are unavailable")
        hedge = self._hedge_mode()
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            amt = _decimal(row.get("positionAmt") or "0")
            if amt == 0:
                continue
            try:
                mapped = _from_symbol(str(row.get("symbol")))
            except ValueError:
                continue
            native = str(row.get("positionSide") or "BOTH").upper()
            if hedge:
                pos_side = "long" if native == "LONG" else "short" if native == "SHORT" else ""
                if not pos_side:
                    continue
                pos = abs(amt)
            else:
                pos_side = "net"
                pos = amt
            mgn = "isolated" if str(row.get("marginType") or "").lower() == "isolated" else "cross"
            result.append({
                "instId": mapped,
                "posSide": pos_side,
                "pos": _text(pos),
                "avgPx": str(row.get("entryPrice") or "0"),
                "markPx": str(row.get("markPrice") or "0"),
                "upl": str(row.get("unRealizedProfit") or "0"),
                "lever": str(row.get("leverage") or "0"),
                "mgnMode": mgn,
                "margin": str(row.get("isolatedMargin") or row.get("positionInitialMargin") or "0"),
                "notionalUsd": str(row.get("notional") or "0"),
                "liqPx": str(row.get("liquidationPrice") or "0"),
                "posId": str(row.get("symbol") or "") + native,
                "cTime": str(row.get("updateTime") or ""),
                "uTime": str(row.get("updateTime") or ""),
                "quantity_unit": "base",
            })
        return result

    def open_orders(self, inst_id: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": _to_symbol(inst_id)} if inst_id else None
        rows = self._private("GET", "/fapi/v1/openOrders", params)
        if not isinstance(rows, list):
            raise RuntimeError("Binance open orders are unavailable")
        return [self._normalize_order(row) for row in rows if isinstance(row, dict)]

    def order_history(self, inst_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        symbols: list[str]
        if inst_id:
            symbols = [_to_symbol(inst_id)]
        else:
            symbols = sorted({_to_symbol(row["instId"]) for row in self.positions()})
            for bill in self._income():
                symbol = str(bill.get("symbol") or "")
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
        result: list[dict[str, Any]] = []
        for symbol in symbols:
            rows = self._private("GET", "/fapi/v1/allOrders", {"symbol": symbol, "limit": min(int(limit), 1000)})
            if not isinstance(rows, list):
                raise RuntimeError("Binance order history is unavailable")
            result.extend(self._normalize_order(row) for row in rows if isinstance(row, dict))
        result.sort(key=lambda row: int(row.get("uTime") or 0), reverse=True)
        return result[: int(limit)]

    def fills(self, inst_id: str, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self._private(
            "GET",
            "/fapi/v1/userTrades",
            {"symbol": _to_symbol(inst_id), "limit": min(int(limit), 1000)},
        )
        if not isinstance(rows, list):
            raise RuntimeError("Binance user trades are unavailable")
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            native = str(row.get("positionSide") or "BOTH").upper()
            fee = self._usdt_fee(row)
            result.append({
                "instId": inst_id,
                "tradeId": str(row.get("id") or ""),
                "ordId": str(row.get("orderId") or ""),
                "side": str(row.get("side") or "").lower(),
                "posSide": {"LONG": "long", "SHORT": "short"}.get(native, "net"),
                "px": str(row.get("price") or "0"),
                "sz": str(row.get("qty") or "0"),
                "fee": _text(fee) if fee is not None else str(row.get("commission") or "0"),
                "feeCcy": str(row.get("commissionAsset") or "USDT"),
                "pnl": str(row.get("realizedPnl") or "0"),
                "ts": str(row.get("time") or ""),
                "quantity_unit": "base",
            })
        return result

    def _income(self, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is not None:
            # Dashboard callers request a bounded recent view, not a full audit scan.
            rows = self._private("GET", "/fapi/v1/income", {"limit": min(max(int(limit), 1), 1000)})
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise RuntimeError("Binance income history is unavailable")
            return sorted(rows, key=lambda row: int(row.get("time") or 0), reverse=True)[:int(limit)]
        now = int(time.time() * 1000)
        oldest_allowed = now - TRADE_LOOKBACK_MS
        end = now
        found: list[dict[str, Any]] = []
        seen: set[tuple[Any, Any]] = set()
        while end > oldest_allowed:
            start = max(oldest_allowed, end - TRADE_WINDOW_MS)
            page = 1
            while True:
                chunk = self._private(
                    "GET",
                    "/fapi/v1/income",
                    {"startTime": start, "endTime": end, "page": page, "limit": 1000},
                )
                if not isinstance(chunk, list):
                    raise RuntimeError("Binance income history is unavailable")
                for row in chunk:
                    if not isinstance(row, dict):
                        continue
                    key = (row.get("incomeType"), row.get("tranId"))
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(row)
                if len(chunk) < 1000:
                    break
                page += 1
                if page > 100:
                    self._raise_incomplete()
            end = start - 1
        found.sort(key=lambda item: int(item.get("time") or 0), reverse=True)
        return found

    def _raise_incomplete(self) -> None:
        error = RuntimeError("Binance positions history is incomplete and unavailable")
        error.incomplete = True  # type: ignore[attr-defined]
        error.status = "unavailable"  # type: ignore[attr-defined]
        error.code = "positions_history_incomplete"  # type: ignore[attr-defined]
        error.window_limited = True  # type: ignore[attr-defined]
        raise error

    def _signed_fill_delta(self, side: str, pos_side: str, qty: Decimal) -> Decimal:
        trade_side = side.upper()
        native = pos_side.upper()
        if native == "LONG":
            return qty if trade_side == "BUY" else -qty
        if native == "SHORT":
            return qty if trade_side == "SELL" else -qty
        return qty if trade_side == "BUY" else -qty

    def _usdt_fee(self, row: dict[str, Any]) -> Decimal | None:
        raw = _decimal(row.get("commission") or "0")
        if raw == 0:
            return Decimal("0")
        asset = str(row.get("commissionAsset") or "USDT").upper()
        if asset != "USDT":
            return None
        return -raw

    def _fetch_user_trades(self, symbol: str) -> list[dict[str, Any]]:
        now = int(time.time() * 1000)
        pending = [(now - TRADE_LOOKBACK_MS, now)]
        found: dict[Any, dict[str, Any]] = {}
        while pending:
            start, end = pending.pop()
            if end - start > TRADE_WINDOW_MS:
                boundary = end - TRADE_WINDOW_MS
                pending.append((start, boundary - 1))
                start = boundary
            chunk = self._private(
                "GET", "/fapi/v1/userTrades",
                {"symbol": symbol, "startTime": start, "endTime": end, "limit": 1000},
            )
            if not isinstance(chunk, list) or any(not isinstance(row, dict) for row in chunk):
                raise RuntimeError("Binance user trades are unavailable")
            if len(chunk) >= 1000:
                if start >= end:
                    self._raise_incomplete()
                middle = (start + end) // 2
                pending.extend(((middle + 1, end), (start, middle)))
                continue
            for row in chunk:
                if row.get("id") is None:
                    self._raise_incomplete()
                found[row["id"]] = row
        return sorted(found.values(), key=lambda item: (int(item.get("time") or 0), int(item["id"])))

    def _live_signed_map(self) -> dict[tuple[str, str], Decimal]:
        mapping: dict[tuple[str, str], Decimal] = {}
        for row in self.positions():
            inst = str(row.get("instId") or "")
            side = str(row.get("posSide") or "net").lower()
            qty = _decimal(row.get("pos") or "0")
            if side == "long":
                key = (inst, "LONG")
                mapping[key] = mapping.get(key, Decimal("0")) + abs(qty)
            elif side == "short":
                key = (inst, "SHORT")
                mapping[key] = mapping.get(key, Decimal("0")) + abs(qty)
            else:
                key = (inst, "BOTH")
                mapping[key] = mapping.get(key, Decimal("0")) + qty
        return mapping

    def positions_history(self, limit: int = 100) -> list[dict[str, Any]]:
        live_before = self._live_signed_map()
        income = self._income()
        symbols = sorted({str(row.get("symbol") or "") for row in income if str(row.get("symbol") or "").endswith("USDT")})
        for inst, _native in live_before:
            symbol = _to_symbol(inst)
            if symbol not in symbols:
                symbols.append(symbol)
        proven: list[dict[str, Any]] = []
        fetched: dict[str, list[dict[str, Any]]] = {}
        for symbol in symbols:
            fetched[symbol] = self._fetch_user_trades(symbol)
        live_after = self._live_signed_map()
        if live_before != live_after:
            self._raise_incomplete()
        for symbol, fills in fetched.items():
            try:
                inst_id = _from_symbol(symbol)
            except ValueError:
                continue
            buckets: dict[str, list[dict[str, Any]]] = {}
            for row in fills:
                if not isinstance(row, dict):
                    continue
                native = str(row.get("positionSide") or "BOTH").upper()
                buckets.setdefault(native, []).append(row)
            for native, rows in buckets.items():
                net = sum((self._signed_fill_delta(str(row.get("side") or ""), native, _decimal(row.get("qty") or "0")) for row in rows), Decimal("0"))
                start = live_after.get((inst_id, native), Decimal("0")) - net
                if start != 0:
                    self._raise_incomplete()
                closed, missing = self._cycles_from_fills(inst_id, native, rows)
                if missing:
                    self._raise_incomplete()
                proven.extend(closed)
        proven.sort(key=lambda row: int(row.get("uTime") or 0), reverse=True)
        return proven[: int(limit)]

    def _split_fill(self, row: dict[str, Any], close_qty: Decimal, open_qty: Decimal) -> tuple[dict[str, Any], dict[str, Any]]:
        total = close_qty + open_qty
        close_part = dict(row)
        open_part = dict(row)
        close_part["qty"] = _text(close_qty)
        open_part["qty"] = _text(open_qty)
        raw_fee = _decimal(row.get("commission") or "0")
        if total > 0:
            close_part["commission"] = _text(raw_fee * close_qty / total)
            open_part["commission"] = _text(raw_fee * open_qty / total)
        open_part["realizedPnl"] = "0"
        return close_part, open_part

    def _cycles_from_fills(self, inst_id: str, native: str, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
        proven: list[dict[str, Any]] = []
        running = Decimal("0")
        cycle: list[dict[str, Any]] = []
        for row in sorted(rows, key=lambda item: (int(item.get("time") or 0), int(item.get("id") or 0))):
            qty = _decimal(row.get("qty") or "0")
            delta = self._signed_fill_delta(str(row.get("side") or ""), native, qty)
            if running == 0:
                cycle = [row]
                running = delta
                continue
            next_qty = running + delta
            if next_qty * running < 0:
                close_qty = abs(running)
                open_qty = abs(next_qty)
                close_part, open_part = self._split_fill(row, close_qty, open_qty)
                cycle.append(close_part)
                closed = self._closed_cycle(inst_id, native, cycle)
                if closed is None:
                    return [], True
                proven.append(closed)
                cycle = [open_part]
                running = next_qty
                continue
            cycle.append(row)
            running = next_qty
            if running == 0:
                closed = self._closed_cycle(inst_id, native, cycle)
                if closed is None:
                    return [], True
                proven.append(closed)
                cycle = []
        return proven, False

    def _closed_cycle(self, inst_id: str, native: str, cycle: list[dict[str, Any]]) -> dict[str, Any] | None:
        opens: list[dict[str, Any]] = []
        closes: list[dict[str, Any]] = []
        fee = Decimal("0")
        direction = "short" if native == "SHORT" or (native == "BOTH" and str(cycle[0].get("side")).upper() == "SELL") else "long"
        opening_sign = Decimal("-1") if native == "BOTH" and direction == "short" else Decimal("1")
        for item in cycle:
            signed_fee = self._usdt_fee(item)
            if signed_fee is None:
                return None
            fee += signed_fee
            delta = self._signed_fill_delta(str(item.get("side") or ""), native, _decimal(item.get("qty") or "0")) * opening_sign
            if delta > 0:
                opens.append(item)
            elif delta < 0:
                closes.append(item)
        open_qty = sum((_decimal(item.get("qty") or "0") for item in opens), Decimal("0"))
        close_qty = sum((_decimal(item.get("qty") or "0") for item in closes), Decimal("0"))
        if open_qty <= 0 or close_qty <= 0:
            return None
        open_notional = sum((_decimal(item.get("price") or "0") * _decimal(item.get("qty") or "0") for item in opens), Decimal("0"))
        close_notional = sum((_decimal(item.get("price") or "0") * _decimal(item.get("qty") or "0") for item in closes), Decimal("0"))
        pnl = sum((_decimal(item.get("realizedPnl") or "0") for item in cycle), Decimal("0"))
        return {
            "instId": inst_id,
            "direction": direction,
            "openAvgPx": _text(open_notional / open_qty),
            "closeAvgPx": _text(close_notional / close_qty),
            "pnl": _text(pnl),
            "fee": _text(fee),
            "openFee": _text(sum((self._usdt_fee(item) or Decimal("0") for item in opens), Decimal("0"))),
            "closeFee": _text(sum((self._usdt_fee(item) or Decimal("0") for item in closes), Decimal("0"))),
            "lever": None,
            "closeTotalPos": _text(close_qty),
            "openMaxPos": _text(open_qty),
            "cTime": str(cycle[0].get("time") or ""),
            "uTime": str(cycle[-1].get("time") or ""),
            "type": "1",
            "pnlRatio": None,
            "posId": str(cycle[0].get("orderId") or ""),
            "quantity_unit": "base",
        }

    def bills(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = []
        for item in self._income(limit):
            income_type = str(item.get("incomeType") or "")
            amount = str(item.get("income") or "0")
            inst = ""
            symbol = str(item.get("symbol") or "")
            if symbol:
                try:
                    inst = _from_symbol(symbol)
                except ValueError:
                    inst = ""
            if income_type == "REALIZED_PNL":
                bill_type, sub_type, pnl, fee = "realized_pnl", "pnl", amount, "0"
            elif income_type == "COMMISSION":
                bill_type, sub_type, pnl, fee = "commission", "fee", "0", amount
            elif income_type == "FUNDING_FEE":
                bill_type, sub_type, pnl, fee = "funding_fee", "funding", amount, "0"
            else:
                bill_type, sub_type, pnl, fee = income_type.lower() or "other", income_type.lower() or "other", amount, "0"
            rows.append({
                "billId": f"{income_type}:{item.get('tranId')}" if item.get("tranId") not in (None, "") else str(item.get("tradeId") or ""),
                "ts": str(item.get("time") or ""),
                "instId": inst,
                "type": bill_type,
                "subType": sub_type,
                "pnl": pnl,
                "fee": fee,
                "balChg": amount,
                "ccy": str(item.get("asset") or "USDT"),
                "ordId": "",
                "tradeId": str(item.get("tradeId") or ""),
            })
        return rows[: int(limit)]

    def cancel_order(self, inst_id: str, order_id: str) -> dict[str, Any]:
        row = self._cancel_order(_to_symbol(inst_id), order_id=str(order_id))
        if not isinstance(row, dict):
            raise RuntimeError("Binance cancellation was not acknowledged")
        return self._normalize_order(row)

    def close_position(self, inst_id: str, pos_side: str, *, cancel_protection: bool = True) -> dict[str, Any]:
        hedge = self._hedge_mode()
        logical = str(pos_side or "").lower()
        qty = self._live_position_qty(inst_id, logical)
        if qty <= 0:
            raise RuntimeError("No live position to close")
        quantity = self._align_qty(inst_id, qty)
        if logical == "net":
            if hedge:
                raise ValueError("Hedge mode requires long or short position side")
            held = self.positions(inst_id)
            signed = _decimal(held[0].get("pos") or "0") if held else Decimal("0")
            side = "SELL" if signed > 0 else "BUY"
            position_side = "BOTH"
            reduce_only = True
        elif hedge:
            side = self._close_side(logical)
            position_side = "LONG" if logical == "long" else "SHORT"
            reduce_only = False
        else:
            side = self._close_side(logical)
            position_side = "BOTH"
            reduce_only = True
        client_id = f"R20C{secrets.token_hex(8)}"[:36]
        params: dict[str, Any] = {
            "symbol": _to_symbol(inst_id),
            "side": side,
            "type": "MARKET",
            "quantity": quantity,
            "positionSide": position_side,
            "newClientOrderId": client_id,
            "newOrderRespType": "RESULT",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        if "closePosition" in params:
            raise ValueError("MARKET orders cannot include closePosition")
        row = self._place_order(params, client_id)
        if cancel_protection and self._live_position_qty(inst_id, logical) == 0:
            self._sweep_orphans(inst_id, None if logical == "net" else logical)
        return self._normalize_order(row)

    def set_leverage(self, inst_id: str, leverage: Any, pos_side: str = "long") -> dict[str, Any]:
        _ = pos_side
        value = _decimal(leverage)
        if value <= 0 or value != value.to_integral_value():
            raise ValueError("Leverage must be a positive integer")
        row = self._private("POST", "/fapi/v1/leverage", {"symbol": _to_symbol(inst_id), "leverage": str(int(value))}, mutate=True)
        if not isinstance(row, dict):
            raise RuntimeError("Binance leverage change was not acknowledged")
        return row

    def _validate_geometry(self, pos_side: str, price: Decimal, tp: Decimal, sl: Decimal) -> None:
        action = "BUY_LONG" if pos_side == "long" else "SELL_SHORT"
        valid, reason, _rr = validate_quote_geometry_and_rr(action, _text(price), _text(tp), _text(sl))
        if not valid:
            raise ValueError(reason)
        if pos_side == "long":
            risk, reward = price - sl, tp - price
        else:
            risk, reward = sl - price, price - tp
        if risk <= 0 or (reward / risk) < Decimal("2"):
            raise ValueError("核心风控拦截：盈亏比不足 2.0")

    def _is_terminal(self, order: dict[str, Any]) -> bool:
        return str(order.get("status") or "").upper() in TERMINAL_ORDER_STATES

    def _require_entry_state(self, symbol: str, client_id: str) -> dict[str, Any]:
        try:
            order = self._query_order(symbol, client_id=client_id)
        except Exception as exc:
            raise RuntimeError("Entry order state is unknown; protection left in place") from exc
        if not isinstance(order, dict) or not order.get("status"):
            raise RuntimeError("Entry order state is unknown; protection left in place")
        return order

    def _compensate(self, inst_id: str, pos_side: str, entry_client: str, keep_sl: str) -> None:
        symbol = _to_symbol(inst_id)
        try:
            self._cancel_order(symbol, client_id=entry_client)
        except UncertainSubmission as exc:
            order = self._require_entry_state(symbol, entry_client)
            if not self._is_terminal(order):
                raise RuntimeError("Entry order state is unknown; protection left in place") from exc
        except RuntimeError:
            order = self._require_entry_state(symbol, entry_client)
        else:
            order = self._require_entry_state(symbol, entry_client)
        if not self._is_terminal(order):
            raise RuntimeError("Entry order is not terminal; protection left in place")
        filled = _decimal(order.get("executedQty") or "0")
        exposure = self._live_position_qty(inst_id, pos_side)
        if filled > 0 and exposure > 0:
            self.close_position(inst_id, pos_side, cancel_protection=False)
            exposure = self._live_position_qty(inst_id, pos_side)
        if exposure != 0:
            raise RuntimeError("Position is not confirmed flat; protection left in place")
        if not keep_sl:
            return
        parsed = self._parse_group(keep_sl)
        if parsed:
            self._cancel_group(parsed[0])
        else:
            self._cancel_algo(keep_sl)

    def place_protected_limit_order(
        self,
        inst_id: str,
        side: str,
        pos_side: str,
        size: Any,
        price: Any,
        tp_px: Any,
        sl_px: Any,
    ) -> dict[str, Any]:
        self._entry_side(side, pos_side)
        self._reject_mixed_exposure(inst_id, pos_side)
        px = self._align_price(inst_id, price)
        tp = self._align_price(inst_id, tp_px)
        sl = self._align_price(inst_id, sl_px)
        qty = self._align_qty(inst_id, size, px)
        self._validate_geometry(str(pos_side).lower(), _decimal(px), _decimal(tp), _decimal(sl))
        group = self._new_group()
        ids = self._group_ids(group)
        symbol = _to_symbol(inst_id)
        entry = {
            "symbol": symbol,
            "side": str(side).upper(),
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": qty,
            "price": px,
            "positionSide": self._position_side(pos_side),
            "newClientOrderId": ids["EN"],
            "newOrderRespType": "RESULT",
        }
        entry.pop("reduceOnly", None)
        placed = self._place_order(entry, ids["EN"])
        sl_row = None
        try:
            sl_row = self._place_algo(self._protection_params(inst_id, pos_side, sl, "SL", ids["SL"]), ids["SL"])
            self._confirm_algo(ids["SL"])
            self._place_algo(self._protection_params(inst_id, pos_side, tp, "TP", ids["TP"]), ids["TP"])
            self._confirm_algo(ids["TP"])
        except Exception as exc:
            keep = ids["SL"] if sl_row is not None else ""
            self._compensate(inst_id, pos_side, ids["EN"], keep)
            raise RuntimeError(_sanitize(exc)) from exc
        return {
            **self._normalize_order(placed),
            "ordId": str(placed.get("orderId") or ""),
            "protection_mechanism": "paired_conditional",
            "algoId": group,
            "tpTriggerPx": tp,
            "slTriggerPx": sl,
            "quantity_unit": "base",
        }

    def _logical_pos_side(self, row: dict[str, Any]) -> str:
        native = str(row.get("positionSide") or "BOTH").upper()
        return {"LONG": "long", "SHORT": "short"}.get(native, "net")

    def protection_orders(self, inst_id: str) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for row in self._open_algos(inst_id):
            parsed = self._parse_group(str(row.get("clientAlgoId") or ""))
            if not parsed:
                continue
            group, kind = parsed
            grouped.setdefault(group, {})[kind] = row
        result = []
        for group, legs in grouped.items():
            tp = legs.get("TP")
            sl = legs.get("SL") or legs.get("SX")
            if not tp or not sl:
                continue
            sample = tp
            close_all = _bool_text(tp.get("closePosition")) and _bool_text(sl.get("closePosition"))
            pos_side = self._logical_pos_side(sample)
            size = self._live_position_qty(inst_id, pos_side if pos_side != "net" else "net")
            result.append({
                "algoId": group,
                "instId": inst_id,
                "tpTriggerPx": str(tp.get("triggerPrice") or tp.get("stopPrice") or ""),
                "slTriggerPx": str(sl.get("triggerPrice") or sl.get("stopPrice") or ""),
                "posSide": pos_side,
                "sz": _text(size) if size else "",
                "state": "live",
                "ordType": "paired_conditional",
                "closeAll": close_all,
                "protection_mechanism": "paired_conditional",
                "quantity_unit": "base",
            })
        return result

    def place_protection(self, inst_id: str, pos_side: str, size: Any, tp_px: Any, sl_px: Any) -> dict[str, Any]:
        if str(pos_side).lower() not in {"long", "short"}:
            raise ValueError("Invalid position direction")
        px_ref = self.ticker(inst_id)["last"]
        tp = self._align_price(inst_id, tp_px)
        sl = self._align_price(inst_id, sl_px)
        qty = self._align_qty(inst_id, size, px_ref)
        self._validate_geometry(str(pos_side).lower(), _decimal(px_ref), _decimal(tp), _decimal(sl))
        self._sweep_orphans(inst_id, str(pos_side).lower())
        group = self._new_group()
        ids = self._group_ids(group)
        sl_row = None
        try:
            sl_row = self._place_algo(self._protection_params(inst_id, pos_side, sl, "SL", ids["SL"]), ids["SL"])
            self._confirm_algo(ids["SL"])
            self._place_algo(self._protection_params(inst_id, pos_side, tp, "TP", ids["TP"]), ids["TP"])
            self._confirm_algo(ids["TP"])
        except Exception as exc:
            if sl_row is not None and self._live_position_qty(inst_id, pos_side) > 0:
                pass
            else:
                self._cancel_group(group)
            raise RuntimeError(_sanitize(exc)) from exc
        return {
            "algoId": group,
            "ordId": group,
            "tpTriggerPx": tp,
            "slTriggerPx": sl,
            "sz": qty,
            "protection_mechanism": "paired_conditional",
            "ordType": "paired_conditional",
            "closeAll": True,
            "quantity_unit": "base",
        }

    def amend_stop(self, inst_id: str, algo_id: str, new_sl: Any) -> dict[str, Any]:
        sl = self._align_price(inst_id, new_sl)
        group = str(algo_id)
        ids = self._group_ids(group)
        live = {item["algoId"]: item for item in self.protection_orders(inst_id)}
        current = live.get(group)
        if not current or not current.get("tpTriggerPx"):
            raise RuntimeError("Existing paired protection was not found")
        pos_side = current["posSide"]
        live_kinds = set()
        for row in self._open_algos(inst_id):
            parsed = self._parse_group(str(row.get("clientAlgoId") or ""))
            if parsed and parsed[0] == group:
                live_kinds.add(parsed[1])
        new_kind, old_kind = ("SL", "SX") if "SX" in live_kinds else ("SX", "SL")
        replacement = None
        try:
            replacement = self._place_algo(self._protection_params(inst_id, pos_side, sl, "SL", ids[new_kind]), ids[new_kind])
            self._confirm_algo(ids[new_kind])
        except Exception as exc:
            if replacement is not None:
                self._cancel_algo(ids[new_kind])
            raise RuntimeError(_sanitize(exc)) from exc
        self._cancel_algo(ids[old_kind])
        return {
            "algoId": group,
            "slTriggerPx": sl,
            "tpTriggerPx": current.get("tpTriggerPx"),
            "protection_mechanism": "paired_conditional",
        }

    def cancel_protection(self, inst_id: str, algo_id: str) -> dict[str, Any]:
        _ = inst_id
        self._cancel_group(str(algo_id))
        return {"algoId": str(algo_id), "state": "canceled"}


__all__ = ["BinanceExchange", "LIVE_HOST", "DEMO_HOST"]

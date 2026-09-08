"""OKX V5 adapter. All public quantities are base-asset units, never contracts."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any

import requests

from r20_backend.okx_trade_service import _request
from scripts.okx_runtime import OKXEnvironment
from scripts.order_risk import validate_quote_geometry_and_rr


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid exchange numeric value") from exc
    if not result.is_finite():
        raise ValueError("Exchange numeric values must be finite")
    return result


def _text(value: Decimal) -> str:
    return format(value, "f")


class OKXExchange:
    def __init__(self, env: Any, session: Any = None):
        self.env = env
        self.session = session if session is not None else requests.Session()
        self._native_env = OKXEnvironment(env.mode, env.api_key, env.secret_key, env.passphrase, env.base_url, env.source)
        self._metadata: dict[str, dict[str, Any]] = {}

    def close(self) -> None:
        self.session.close()

    def _public(self, path: str, params: dict[str, Any] | None = None) -> list[Any]:
        response = self.session.get(self.env.base_url + path, params=params or {}, timeout=8, allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError(f"OKX public request failed (HTTP {response.status_code})")
        payload = response.json()
        if not isinstance(payload, dict) or str(payload.get("code")) != "0" or not isinstance(payload.get("data"), list):
            raise RuntimeError("OKX public response is not successful market data")
        return payload["data"]

    def _private(self, method: str, path: str, params: Any = None) -> list[dict[str, Any]]:
        return _request(method, path, params, self._native_env)

    def _instrument(self, inst_id: str) -> dict[str, Any]:
        if inst_id not in self._metadata:
            rows = self._public("/api/v5/public/instruments", {"instType": "SWAP", "instId": inst_id})
            for row in rows:
                if row.get("settleCcy") == "USDT" and row.get("state") == "live":
                    self._metadata[str(row["instId"])] = row
        if inst_id not in self._metadata:
            raise ValueError("Instrument is not an active USDT perpetual contract")
        return self._metadata[inst_id]

    def _contract_value(self, inst_id: str) -> Decimal:
        value = _decimal(self._instrument(inst_id).get("ctVal"))
        if value <= 0:
            raise ValueError("Invalid contract value")
        return value

    def instruments(self, inst_id: str | None = None) -> list[dict[str, Any]]:
        if inst_id:
            rows = [self._instrument(inst_id)]
        else:
            rows = self._public("/api/v5/public/instruments", {"instType": "SWAP"})
        result = []
        for row in rows:
            if row.get("settleCcy") != "USDT" or row.get("state") != "live":
                continue
            self._metadata[str(row["instId"])] = row
            ct = _decimal(row["ctVal"])
            result.append({**row, "ctVal": "1", "nativeCtVal": _text(ct), "quantity_unit": "base",
                           "baseCcy": row.get("baseCcy") or str(row["instId"]).split("-")[0],
                           "lotSz": _text(_decimal(row["lotSz"]) * ct),
                           "minSz": _text(_decimal(row["minSz"]) * ct), "minNotional": "0"})
        return result

    def ticker(self, inst_id: str) -> dict[str, Any]:
        rows = self._public("/api/v5/market/ticker", {"instId": inst_id})
        if not rows:
            raise RuntimeError("OKX ticker unavailable")
        return rows[0]

    def tickers(self) -> dict[str, dict[str, Any]]:
        return {row["instId"]: row for row in self._public("/api/v5/market/tickers", {"instType": "SWAP"}) if str(row.get("instId", "")).endswith("-USDT-SWAP")}

    def candles(self, inst_id: str, bar: str = "15m", limit: int = 100) -> list[list[str]]:
        from r20_exchange.candle_cache import get_or_fetch

        def load(pull: int) -> list[list[str]]:
            rows = self._public("/api/v5/market/candles", {"instId": inst_id, "bar": bar, "limit": min(int(pull), 300)})
            return [[str(v) for v in row[:5]] + [str(row[6]), str(row[7]), str(row[7]), str(row[8])] for row in rows if len(row) >= 9]

        return get_or_fetch(str(getattr(self.env, "base_url", "") or "okx"), inst_id, bar, limit, load, min_pull=80, max_pull=300)

    def orderbook(self, inst_id: str, sz: int = 5) -> dict[str, Any]:
        rows = self._public("/api/v5/market/books", {"instId": inst_id, "sz": sz})
        if not rows:
            raise RuntimeError("OKX orderbook unavailable")
        ct = self._contract_value(inst_id)
        return {**rows[0], **{side: [[level[0], _text(_decimal(level[1]) * ct), *level[2:]] for level in rows[0][side]] for side in ("bids", "asks")}}

    def funding_rate(self, inst_id: str) -> float:
        rows = self._public("/api/v5/public/funding-rate", {"instId": inst_id})
        if not rows:
            raise RuntimeError("OKX funding rate unavailable")
        return float(_decimal(rows[0]["fundingRate"]) * 100)

    def open_interest(self, inst_id: str) -> dict[str, Any]:
        rows = self._public("/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst_id})
        if not rows:
            raise RuntimeError("OKX open interest unavailable")
        row = rows[0]
        base = _decimal(row["oiCcy"]) if row.get("oiCcy") not in (None, "") else _decimal(row["oi"]) * self._contract_value(inst_id)
        return {**row, "oi": _text(base), "oiCcy": _text(base)}

    def long_short_ratio(self, inst_id: str) -> float | None:
        rows = self._public("/api/v5/rubik/stat/contracts/long-short-account-ratio", {"ccy": inst_id.split("-")[0], "period": "5m"})
        return float(rows[0][1]) if rows else None

    def taker_volume(self, inst_id: str) -> dict[str, Any] | None:
        rows = self._public("/api/v5/rubik/stat/taker-volume", {"ccy": inst_id.split("-")[0], "instType": "CONTRACTS", "period": "5m"})
        return {"sellVol": rows[0][1], "buyVol": rows[0][2]} if rows else None

    def _base_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for row in rows:
            item = dict(row)
            inst_id = str(item.get("instId") or "")
            if inst_id:
                ct = self._contract_value(inst_id)
                for key in ("pos", "availPos", "sz", "accFillSz", "fillSz", "openMaxPos", "closeTotalPos"):
                    if item.get(key) not in (None, ""):
                        item[key] = _text(_decimal(item[key]) * ct)
            item["quantity_unit"] = "base"
            result.append(item)
        return result

    def balance(self) -> list[dict[str, Any]]:
        return self._private("GET", "/api/v5/account/balance", {"ccy": "USDT"})

    def positions(self, inst_id: str | None = None) -> list[dict[str, Any]]:
        return self._base_rows(self._private("GET", "/api/v5/account/positions", {"instType": "SWAP", "instId": inst_id}))

    def open_orders(self, inst_id: str | None = None) -> list[dict[str, Any]]:
        return self._base_rows(self._private("GET", "/api/v5/trade/orders-pending", {"instType": "SWAP", "instId": inst_id}))

    def order_history(self, inst_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self._base_rows(self._private("GET", "/api/v5/trade/orders-history", {"instType": "SWAP", "instId": inst_id, "limit": min(limit, 100)}))

    def positions_history(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._base_rows(self._private("GET", "/api/v5/account/positions-history", {"instType": "SWAP", "limit": min(limit, 100)}))

    def bills(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._private("GET", "/api/v5/account/bills", {"instType": "SWAP", "limit": min(limit, 100)})

    def cancel_order(self, inst_id: str, order_id: str) -> dict[str, Any]:
        rows = self._private("POST", "/api/v5/trade/cancel-order", {"instId": inst_id, "ordId": order_id})
        if not rows:
            raise RuntimeError("OKX cancellation was not acknowledged")
        return rows[0]

    def close_position(self, inst_id: str, pos_side: str) -> dict[str, Any]:
        rows = self._private("POST", "/api/v5/trade/close-position", {"instId": inst_id, "mgnMode": "cross", "posSide": pos_side, "autoCxl": True})
        if not rows:
            raise RuntimeError("OKX close was not acknowledged")
        return rows[0]

    def set_leverage(self, inst_id: str, leverage: Any, pos_side: str = "long") -> dict[str, Any]:
        rows = self._private("POST", "/api/v5/account/set-leverage", {"instId": inst_id, "lever": str(leverage), "mgnMode": "cross", "posSide": pos_side})
        if not rows:
            raise RuntimeError("OKX leverage change was not acknowledged")
        return rows[0]

    def _quantity(self, inst_id: str, size: Any) -> str:
        raw = self._instrument(inst_id)
        ct, step, minimum = self._contract_value(inst_id), _decimal(raw["lotSz"]), _decimal(raw["minSz"])
        quantity = _decimal(size)
        if quantity <= 0 or step <= 0:
            raise ValueError("Order quantity and lot step must be positive")
        contracts = (quantity / ct / step).to_integral_value(rounding=ROUND_DOWN) * step
        if contracts < minimum:
            raise ValueError("Order quantity is below the exchange minimum")
        return _text(contracts)

    def _price(self, inst_id: str, price: Any) -> str:
        tick = _decimal(self._instrument(inst_id)["tickSz"])
        value = _decimal(price)
        if tick <= 0 or value <= 0:
            raise ValueError("Price and tick must be positive")
        value = (value / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        if value <= 0:
            raise ValueError("Price is below the exchange tick")
        return _text(value)

    def place_protected_limit_order(self, inst_id: str, side: str, pos_side: str, size: Any, price: Any, tp_px: Any, sl_px: Any) -> dict[str, Any]:
        if (side, pos_side) not in {("buy", "long"), ("sell", "short")}:
            raise ValueError("Entry side does not match position direction")
        quantity = self._quantity(inst_id, size)
        px, tp, sl = (self._price(inst_id, value) for value in (price, tp_px, sl_px))
        valid, reason, _ = validate_quote_geometry_and_rr("BUY_LONG" if pos_side == "long" else "SELL_SHORT", float(px), float(tp), float(sl))
        if not valid:
            raise ValueError(reason)
        rows = self._private("POST", "/api/v5/trade/order", {"instId": inst_id, "tdMode": "cross", "side": side, "posSide": pos_side, "ordType": "limit", "px": px, "sz": quantity, "attachAlgoOrds": [{"tpTriggerPx": tp, "tpOrdPx": "-1", "slTriggerPx": sl, "slOrdPx": "-1"}]})
        if not rows or not rows[0].get("ordId"):
            raise RuntimeError("OKX did not return a verifiable order ID")
        return {**rows[0], "protection_mechanism": "native_attached_tp_sl", "quantity_unit": "base"}

    def protection_orders(self, inst_id: str) -> list[dict[str, Any]]:
        rows = self._private("GET", "/api/v5/trade/orders-algo-pending", {"instType": "SWAP", "instId": inst_id, "ordType": "oco"})
        return [{**row, "protection_mechanism": "native_oco"} for row in self._base_rows(rows)]

    def place_protection(self, inst_id: str, pos_side: str, size: Any, tp_px: Any, sl_px: Any) -> dict[str, Any]:
        if pos_side not in {"long", "short"}:
            raise ValueError("Invalid position direction")
        rows = self._private("POST", "/api/v5/trade/order-algo", {"instId": inst_id, "tdMode": "cross", "side": "sell" if pos_side == "long" else "buy", "posSide": pos_side, "ordType": "oco", "sz": self._quantity(inst_id, size), "tpTriggerPx": self._price(inst_id, tp_px), "tpOrdPx": "-1", "slTriggerPx": self._price(inst_id, sl_px), "slOrdPx": "-1", "reduceOnly": True, "cxlOnClosePos": True})
        if not rows or not rows[0].get("algoId"):
            raise RuntimeError("OKX did not acknowledge protection")
        return rows[0]

    def amend_stop(self, inst_id: str, algo_id: str, new_sl: Any) -> dict[str, Any]:
        rows = self._private("POST", "/api/v5/trade/amend-algos", {"instId": inst_id, "algoId": algo_id, "newSlTriggerPx": self._price(inst_id, new_sl), "newSlOrdPx": "-1"})
        if not rows:
            raise RuntimeError("OKX did not acknowledge stop amendment")
        return rows[0]

    def cancel_protection(self, inst_id: str, algo_id: str) -> dict[str, Any]:
        rows = self._private("POST", "/api/v5/trade/cancel-algos", [{"instId": inst_id, "algoId": algo_id}])
        if not rows:
            raise RuntimeError("OKX did not acknowledge protection cancellation")
        return rows[0]

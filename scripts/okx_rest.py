"""OKX V5 signed REST client with explicit request contracts.

Official reference: https://www.okx.com/docs-v5/en/
Algo cancel/amend, ordinary order attached legs and account bills are verified
against the official request tables (see tests.test_okx_v5_official_contract).
This client does not claim to replace other legacy signing implementations.

Design contract (mission/okx-cli-removal, US-001):
- Credentials/environment are resolved exclusively through
  ``scripts.okx_runtime.current_environment()`` (frozen cycle env first, then the
  LIVE/DEMO selection). This module never parses .env itself, never shells out to
  the removed ``okx`` command line and never touches child processes.
- Fail-closed: any private call with an unconfigured credential group raises
  ``OKXNotConfigured`` *before* a network request is made.
- Signature口径 identical to ``r20_backend.okx_trade_service._request``:
  prehash = timestamp + method + request_path + body, HMAC-SHA256 keyed with the
  secret then base64; demo mode adds ``x-simulated-trading: 1``.
- Returns the payload ``data`` list (``[]`` when empty). Envelope ``code`` or row
  ``sCode`` != 0 raises ``RuntimeError`` carrying the OKX code and msg.
- Public market data (ticker/candles) stays in scripts/market_data_service.py.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from urllib.request import Request, urlopen

from scripts.okx_runtime import OKXEnvironment, current_environment

__all__ = [
    "OKXNotConfigured",
    "request",
    "place_order", "cancel_order", "amend_order", "close_position",
    "pending_orders", "orders_history", "fills",
    "position", "positions", "balances", "bills", "positions_history",
    "place_algo_oco", "cancel_algo_orders", "amend_algo_sl", "pending_algo_orders",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 20


class OKXNotConfigured(RuntimeError):
    """No static V5 API Key for the selected LIVE/DEMO environment (fail-closed)."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _fmt(value: Any) -> Any:
    """Preserve JSON booleans; render numeric String fields without rounding.

    Decimal.normalize() uses the ambient precision and can round long prices.
    Fixed-point formatting followed by fractional-zero removal is lossless.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (float, Decimal)):
        number = Decimal(repr(value)) if isinstance(value, float) else value
        if not number.is_finite():
            raise ValueError("OKX numeric parameters must be finite")
        text = format(number, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip().lower() in {
        "nan", "snan", "inf", "infinity", "+nan", "-nan", "+inf", "-inf",
        "+infinity", "-infinity",
    }:
        raise ValueError("OKX numeric parameters must be finite")
    return value


_BOOLEAN_FIELDS = {"reduceOnly", "cxlOnClosePos", "autoCxl", "cxlOnFail",
                   "banAmend", "prohibitSlippage"}
_PRICE_FIELDS = {"px", "newPx", "tpTriggerPx", "slTriggerPx", "tpOrdPx", "slOrdPx",
                 "newTpTriggerPx", "newSlTriggerPx", "newTpOrdPx", "newSlOrdPx",
                 "sz", "newSz", "closeFraction"}


def _validate_params(value: Any) -> None:
    """Validate field types/ranges before signing, including custom attached legs.

    V5 documents price/size as String, flags as Boolean. Zero for *new* TP/SL
    prices deletes the leg; -1 is valid only for a TP/SL execution price.
    This is not a substitute for instrument tick/lot-size or account risk checks.
    """
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_params(item)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if item is None or item == "":
                continue
            if key in _BOOLEAN_FIELDS and not isinstance(item, bool):
                raise ValueError(f"{key} must be a JSON Boolean")
            if key.endswith("TriggerPxType") and item not in {"last", "index", "mark"}:
                raise ValueError(f"{key} must be last, index or mark")
            if key in _PRICE_FIELDS:
                if isinstance(item, bool):
                    raise ValueError(f"{key} must be a finite decimal")
                try:
                    number = Decimal(str(item))
                except Exception as exc:
                    raise ValueError(f"{key} must be a finite decimal") from exc
                if not number.is_finite():
                    raise ValueError(f"{key} must be finite")
                deletion = key.startswith("new") and key not in {"newPx", "newSz"}
                market = key.endswith("OrdPx") and number == -1
                if not market and (number < 0 or (number == 0 and not deletion)):
                    raise ValueError(f"{key} is outside its V5 price/size range")
            _validate_params(item)
        if value.get("cxlOnClosePos") is True and value.get("reduceOnly") is not True:
            raise ValueError("cxlOnClosePos requires reduceOnly=true")


def _required(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value


def _validate_attachments(legs: Any, *, amend: bool = False) -> None:
    if legs is None:
        return
    if not isinstance(legs, (list, tuple)):
        raise ValueError("attachAlgoOrds must be an array of objects")
    for leg in legs:
        if not isinstance(leg, Mapping) or not leg:
            raise ValueError("attached leg must be a nonempty object")
        if "tdMode" in leg:
            raise ValueError("tdMode belongs to the parent order, not attachAlgoOrds")
        prefix = "new" if amend else ""
        for side in ("Tp", "Sl"):
            stem = prefix + side if amend else side.lower()
            trigger, price = stem + "TriggerPx", stem + "OrdPx"
            if not amend and trigger in leg and price not in leg:
                raise ValueError(f"{trigger} requires {price}")
        _validate_params(leg)


def _clean(params: Mapping[str, Any] | Sequence[Any] | None) -> Any:
    """Recursively drop empty scalars and format nested values (attachAlgoOrds legs)."""
    if params is None:
        return None
    if isinstance(params, Mapping):
        cleaned = {}
        for key, value in params.items():
            if isinstance(value, (Mapping, list, tuple)):
                nested = _clean(value)
                if nested not in (None, {}, []):
                    cleaned[key] = nested
            elif value not in (None, ""):
                cleaned[key] = _fmt(value)
        return cleaned
    if isinstance(params, (list, tuple)):
        return [row for row in (_clean(item) for item in params) if row not in (None, {}, [])]
    return params


def request(
    method: str,
    path: str,
    params: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    *,
    env: OKXEnvironment | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """One signed V5 private request. GET params go into the query string and the
    prehash; POST/list bodies are compact JSON and the prehash. Never falls back
    to the removed CLI."""
    selected = env or current_environment()
    if not selected.configured:
        raise OKXNotConfigured(
            f"OKX {selected.mode.upper()} API Key 未配置：V5 直签是唯一私有通道（fail-closed，无 CLI 回退）"
        )
    method = method.upper()
    _validate_params(params)
    payload = _clean(params) or {}
    if method == "GET":
        query = urllib.parse.urlencode({k: str(v).lower() if isinstance(v, bool) else v
                                         for k, v in payload.items()}) if payload else ""
        request_path = path + (f"?{query}" if query else "")
        body_text = ""
    else:
        request_path = path
        body_text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    timestamp = _timestamp()
    prehash = timestamp + method + request_path + body_text
    signature = base64.b64encode(
        hmac.new(selected.secret_key.encode(), prehash.encode(), hashlib.sha256).digest()
    ).decode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "OK-ACCESS-KEY": selected.api_key,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": selected.passphrase,
    }
    if selected.simulated:
        headers["x-simulated-trading"] = "1"
    http_request = Request(
        selected.base_url + request_path,
        data=body_text.encode("utf-8") if body_text else None,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(http_request, timeout=timeout) as response:
            payload_json = json.loads(response.read().decode("utf-8") or "{}")
    except Exception as exc:
        raise RuntimeError(f"OKX V5 网络请求失败：{type(exc).__name__}: {exc}") from exc
    if not isinstance(payload_json, dict):
        raise RuntimeError("OKX V5 invalid response envelope")
    if str(payload_json.get("code", "0")) != "0":
        raise RuntimeError(f"OKX {payload_json.get('code')}: {payload_json.get('msg') or '请求失败'}")
    data = payload_json.get("data") or []
    if not isinstance(data, list):
        data = [data]
    rows = [row for row in data if isinstance(row, dict)]
    failures = [row for row in rows if str(row.get("sCode", "0")) != "0"]
    if failures:
        raise RuntimeError(
            f"OKX {failures[0].get('sCode')}: {failures[0].get('sMsg') or '业务请求失败'}"
        )
    return rows


# ---------------------------------------------------------------------------
# Trade — regular orders (maps: okx swap place / cancel / amend / close)
# ---------------------------------------------------------------------------

def place_order(
    inst_id: str,
    side: str,
    size: Any,
    *,
    pos_side: str | None = None,
    td_mode: str = "cross",
    ord_type: str = "limit",
    px: Any = None,
    cl_ord_id: str | None = None,
    reduce_only: bool | None = None,
    target_adj: Any = None,
    attach_tp: Any = None,
    attach_sl: Any = None,
    attach_tp_ord_px: Any = "-1",
    attach_sl_ord_px: Any = "-1",
    attach_algo_ords: Sequence[Mapping[str, Any]] | None = None,
    extra: Mapping[str, Any] | None = None,
    env: OKXEnvironment | None = None,
) -> list[dict[str, Any]]:
    """POST /api/v5/trade/order. ``attach_tp``/``attach_sl`` build the V5
    ``attachAlgoOrds`` array (market execution via px=-1 by default, matching the
    old ``--tpTriggerPx X --tpOrdPx=-1 --slTriggerPx Y --slOrdPx=-1`` CLI flags).
    Callers needing full control pass ``attach_algo_ords``/``extra`` verbatim.
    Result rows carry ``ordId``/``clOrdId``; row ``sCode`` non-zero raises."""
    params: dict[str, Any] = {
        "instId": inst_id,
        "tdMode": td_mode,
        "side": side,
        "ordType": ord_type,
        "sz": size,
    }
    if pos_side:
        params["posSide"] = pos_side
    if px is not None:
        params["px"] = px
    if cl_ord_id:
        params["clOrdId"] = cl_ord_id
    if reduce_only is not None:
        params["reduceOnly"] = reduce_only
    if target_adj is not None:
        params["targetAdj"] = target_adj
    legs = list(attach_algo_ords or [])
    if attach_tp is not None or attach_sl is not None:
        leg: dict[str, Any] = {}
        if attach_tp is not None:
            leg["tpTriggerPx"] = attach_tp
            leg["tpOrdPx"] = attach_tp_ord_px
        if attach_sl is not None:
            leg["slTriggerPx"] = attach_sl
            leg["slOrdPx"] = attach_sl_ord_px
        legs.append(leg)
    if legs:
        params["attachAlgoOrds"] = legs
    if extra:
        params.update(extra)
    _required(inst_id, "instId")
    _validate_attachments(params.get("attachAlgoOrds"))
    return request("POST", "/api/v5/trade/order", params, env=env)


def cancel_order(inst_id: str, ord_id: str, *, cl_ord_id: str | None = None, env: OKXEnvironment | None = None) -> list[dict[str, Any]]:
    return request("POST", "/api/v5/trade/cancel-order", {"instId": inst_id, "ordId": ord_id, "clOrdId": cl_ord_id}, env=env)


def amend_order(
    inst_id: str, ord_id: str, *, new_px: Any = None, new_sz: Any = None,
    req_id: str | None = None, req_tx_id: str | None = None,
    attach_algo_ords: Sequence[Mapping[str, Any]] | None = None,
    cxl_on_fail: bool | None = None, env: OKXEnvironment | None = None,
) -> list[dict[str, Any]]:
    """V5 amend-order: reqId and attachAlgoOrds (not standalone algo IDs).

    req_tx_id remains a Python compatibility alias only; it is never a wire key.
    Acceptance sCode=0 is not final confirmation: consumers must query status.
    """
    _required(inst_id, "instId")
    _required(ord_id, "ordId")
    if req_id is not None and req_tx_id is not None and req_id != req_tx_id:
        raise ValueError("conflicting reqId aliases")
    _validate_attachments(attach_algo_ords, amend=True)
    return request("POST", "/api/v5/trade/amend-order", {
        "instId": inst_id, "ordId": ord_id, "newPx": new_px, "newSz": new_sz,
        "reqId": req_id if req_id is not None else req_tx_id,
        "attachAlgoOrds": attach_algo_ords, "cxlOnFail": cxl_on_fail,
    }, env=env)


def close_position(
    inst_id: str,
    pos_side: str = "net",
    *,
    td_mode: str = "cross",
    auto_cxl: bool = True,
    cl_ord_id: str | None = None,
    env: OKXEnvironment | None = None,
) -> list[dict[str, Any]]:
    """Market close of the whole position (old ``okx swap close --autoCxl``)."""
    return request("POST", "/api/v5/trade/close-position", {
        "instId": inst_id, "mgnMode": td_mode, "posSide": pos_side, "autoCxl": auto_cxl, "clOrdId": cl_ord_id,
    }, env=env)


# ---------------------------------------------------------------------------
# Trade — order queries (maps: okx swap orders [--history])
# ---------------------------------------------------------------------------

def pending_orders(inst_id: str | None = None, *, inst_type: str = "SWAP", ord_type: str | None = None, env: OKXEnvironment | None = None) -> list[dict[str, Any]]:
    return request("GET", "/api/v5/trade/orders-pending", {"instType": inst_type, "instId": inst_id, "ordType": ord_type}, env=env)


def orders_history(*, inst_type: str = "SWAP", inst_id: str | None = None, state: str | None = None,
                   begin: Any = None, end: Any = None, limit: int = 100, env: OKXEnvironment | None = None) -> list[dict[str, Any]]:
    """Last-month filled/partially_filled orders. V5 defaults instType to SPOT,
    hence the explicit SWAP default; for >1 month ranges use orders-history-archive."""
    return request("GET", "/api/v5/trade/orders-history", {
        "instType": inst_type, "instId": inst_id, "state": state, "begin": begin, "end": end, "limit": limit,
    }, env=env)


def fills(*, inst_type: str = "SWAP", inst_id: str | None = None, begin: Any = None, end: Any = None,
          limit: int = 100, env: OKXEnvironment | None = None) -> list[dict[str, Any]]:
    """Recent trade fills (V5 keeps ~3 days here; older history via fills-history)."""
    return request("GET", "/api/v5/trade/fills", {
        "instType": inst_type, "instId": inst_id, "begin": begin, "end": end, "limit": limit,
    }, env=env)


# ---------------------------------------------------------------------------
# Account (maps: okx account positions / balance / bills / positions-history)
# ---------------------------------------------------------------------------

def positions(*, inst_type: str = "SWAP", inst_id: str | None = None, env: OKXEnvironment | None = None) -> list[dict[str, Any]]:
    return request("GET", "/api/v5/account/positions", {"instType": inst_type, "instId": inst_id}, env=env)


def position(inst_id: str, *, inst_type: str = "SWAP", env: OKXEnvironment | None = None) -> dict[str, Any] | None:
    """Single instrument position row (or None). Convenience over ``positions``."""
    rows = [row for row in positions(inst_type=inst_type, inst_id=inst_id, env=env) if str(row.get("pos") or "0") != "0"]
    return rows[0] if rows else None


def balances(ccy: str | None = None, *, env: OKXEnvironment | None = None) -> list[dict[str, Any]]:
    return request("GET", "/api/v5/account/balance", {"ccy": ccy}, env=env)


def bills(*, inst_type: str | None = None, inst_id: str | None = None, mgn_mode: str | None = None,
          type: str | None = None, ccy: str | None = None, begin: Any = None, end: Any = None,
          limit: int = 100, before: str | None = None, after: str | None = None, env: OKXEnvironment | None = None) -> list[dict[str, Any]]:
    """Last-7-days bills, one page. after=older billId; before=newer billId.

    These are billId cursors, NOT timestamps; begin/end remain time filters.
    Consumers must paginate/deduplicate and use bills-archive for older ranges.
    """
    if not 1 <= limit <= 100:
        raise ValueError("bills limit must be 1..100")
    return request("GET", "/api/v5/account/bills", {
        "instType": inst_type, "instId": inst_id, "mgnMode": mgn_mode, "type": type,
        "ccy": ccy, "begin": begin, "end": end, "limit": limit,
        "before": before, "after": after,
    }, env=env)


def positions_history(*, inst_type: str = "SWAP", inst_id: str | None = None, limit: int = 100, env: OKXEnvironment | None = None) -> list[dict[str, Any]]:
    return request("GET", "/api/v5/account/positions-history", {
        "instType": inst_type, "instId": inst_id, "limit": limit,
    }, env=env)


# ---------------------------------------------------------------------------
# Trade — algo / cloud OCO protection (maps: okx swap algo place|orders|amend|cancel)
# ---------------------------------------------------------------------------

def place_algo_oco(
    inst_id: str,
    side: str,
    size: Any,
    *,
    pos_side: str,
    td_mode: str = "cross",
    tp_trigger_px: Any,
    sl_trigger_px: Any,
    tp_ord_px: Any = "-1",
    sl_ord_px: Any = "-1",
    reduce_only: bool = True,
    cxl_on_close_pos: bool = True,
    extra: Mapping[str, Any] | None = None,
    env: OKXEnvironment | None = None,
) -> list[dict[str, Any]]:
    """POST /api/v5/trade/order-algo with ordType=oco — the cloud TP/SL pair the
    engine ratchets (old ``okx swap algo place --ordType oco --reduceOnly --cxlOnClosePos``)."""
    params: dict[str, Any] = {
        "instId": inst_id, "tdMode": td_mode, "side": side, "posSide": pos_side,
        "ordType": "oco", "sz": size,
        "tpTriggerPx": tp_trigger_px, "tpOrdPx": tp_ord_px,
        "slTriggerPx": sl_trigger_px, "slOrdPx": sl_ord_px,
        "reduceOnly": reduce_only, "cxlOnClosePos": cxl_on_close_pos,
    }
    if extra:
        params.update(extra)
    _required(params.get("instId"), "instId")
    if params.get("ordType") != "oco":
        raise ValueError("place_algo_oco requires ordType=oco")
    return request("POST", "/api/v5/trade/order-algo", params, env=env)


def cancel_algo_orders(
    algo_ids: Sequence[str | Mapping[str, Any]] | str, *, inst_id: str | None = None,
    env: OKXEnvironment | None = None,
) -> list[dict[str, Any]]:
    """V5 cancel-algos: ARRAY of 1..10 {instId, algoId|algoClOrdId} objects.

    Bare IDs require inst_id. Never guess a product from instType or silently
    split batches (partial success needs explicit caller reconciliation).
    """
    ids = [algo_ids] if isinstance(algo_ids, str) else list(algo_ids)
    if not 1 <= len(ids) <= 10:
        raise ValueError("cancel-algos requires 1..10 orders per request")
    body = []
    for item in ids:
        if isinstance(item, Mapping):
            row = {k: item[k] for k in ("instId", "algoId", "algoClOrdId") if k in item}
            if inst_id and row.get("instId", inst_id) != inst_id:
                raise ValueError("conflicting instId")
            row.setdefault("instId", inst_id)
        else:
            row = {"instId": inst_id, "algoId": item}
        _required(row.get("instId"), "instId")
        _required(row.get("algoId") or row.get("algoClOrdId"), "algoId/algoClOrdId")
        body.append(row)
    return request("POST", "/api/v5/trade/cancel-algos", body, env=env)


def amend_algo_sl(
    algo_id: str, new_sl_trigger_px: Any, *, inst_id: str | None = None,
    new_sl_ord_px: Any = "-1", new_tp_trigger_px: Any = None,
    new_tp_ord_px: Any = None, req_id: str | None = None,
    new_sl_trigger_px_type: str | None = None,
    new_tp_trigger_px_type: str | None = None,
    cxl_on_fail: bool | None = None, env: OKXEnvironment | None = None,
) -> list[dict[str, Any]]:
    """V5 amend-algos takes ONE object, with instId and algoId (not an array).

    Zero trigger/execution price deletes that TP/SL leg. -1 is market execution.
    """
    _required(inst_id, "instId")
    _required(algo_id, "algoId")
    if new_tp_trigger_px is not None and new_tp_ord_px is None:
        raise ValueError("newTpTriggerPx requires newTpOrdPx")
    return request("POST", "/api/v5/trade/amend-algos", {
        "instId": inst_id, "algoId": algo_id, "newSlTriggerPx": new_sl_trigger_px,
        "newSlOrdPx": new_sl_ord_px, "newTpTriggerPx": new_tp_trigger_px,
        "newTpOrdPx": new_tp_ord_px, "reqId": req_id, "cxlOnFail": cxl_on_fail,
        "newSlTriggerPxType": new_sl_trigger_px_type,
        "newTpTriggerPxType": new_tp_trigger_px_type,
    }, env=env)


def pending_algo_orders(inst_id: str | None = None, *, inst_type: str = "SWAP", ord_type: str = "oco",
                        limit: int = 100, before: str | None = None, after: str | None = None, env: OKXEnvironment | None = None) -> list[dict[str, Any]]:
    """GET /api/v5/trade/orders-algo-pending. ``instId`` is sent to the API and
    additionally filtered locally — older deployments ignored the query filter."""
    if not 1 <= limit <= 100:
        raise ValueError("algo pending limit must be 1..100")
    if "," in ord_type and set(ord_type.split(",")) != {"conditional", "oco"}:
        raise ValueError("only conditional,oco may be combined")
    rows = request("GET", "/api/v5/trade/orders-algo-pending", {
        "instType": inst_type, "instId": inst_id, "ordType": ord_type, "limit": limit,
        "before": before, "after": after,
    }, env=env)
    if inst_id:
        rows = [row for row in rows if str(row.get("instId") or "") == inst_id]
    return rows

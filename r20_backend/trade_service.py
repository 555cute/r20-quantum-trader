"""Account snapshots and one-use, account-bound confirmed close intents."""
from __future__ import annotations

import secrets
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from r20_exchange.runtime import ExchangeEnvironment, get_exchange, selected_environment

_INTENTS: dict[str, dict[str, Any]] = {}
_INTENT_LOCK = threading.Lock()
INTENT_TTL_SECONDS = 90


def _quantity(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation) as exc:
        raise ValueError("Invalid position quantity") from exc
    if not result.is_finite():
        raise ValueError("Position quantity must be finite")
    return result


def _create_intent(env: ExchangeEnvironment, position: dict[str, Any]) -> tuple[str, str]:
    signed = _quantity(position.get("pos", "0"))
    size = abs(signed)
    if size == 0:
        raise ValueError("Cannot close an empty position")
    side = str(position.get("posSide") or "net").lower()
    confirmation = f"CLOSE {env.exchange.upper()} {env.mode.upper()} {position['instId']} {side.upper()} {size:f}"
    token = secrets.token_urlsafe(32)
    record = {
        "environment_id": env.identity, "instId": str(position["instId"]),
        "posSide": side, "posId": str(position.get("posId") or ""),
        "expected_size": str(size), "signed_size": str(signed), "confirmation": confirmation,
        "expires_at": time.monotonic() + INTENT_TTL_SECONDS,
    }
    with _INTENT_LOCK:
        now = time.monotonic()
        for key in [key for key, value in _INTENTS.items() if value["expires_at"] <= now]:
            del _INTENTS[key]
        _INTENTS[token] = record
    return token, confirmation


def account_snapshot() -> dict[str, Any]:
    env = selected_environment()
    exchange = get_exchange(env)
    positions = [row for row in exchange.positions() if _quantity(row.get("pos", "0")) != 0]
    orders = exchange.open_orders()
    public_positions = []
    for row in positions:
        token, confirmation = _create_intent(env, row)
        public_positions.append({**row, "close_token": token, "close_confirmation": confirmation, "close_token_expires_in": INTENT_TTL_SECONDS})
    return {
        "exchange": env.exchange, "environment": env.mode, "environment_id": env.identity,
        "credential_source": "api-key" if env.configured else "cli-oauth",
        "positions": public_positions, "orders": orders, "quantity_unit": "base",
        "captured_at_ms": int(time.time() * 1000),
    }


def _consume_intent(token: str) -> dict[str, Any]:
    with _INTENT_LOCK:
        intent = _INTENTS.pop(str(token), None)
    if intent is None or intent["expires_at"] <= time.monotonic():
        raise ValueError("Close confirmation expired or was already used; refresh the position")
    return intent


def _position_match(positions: list[dict[str, Any]], intent: dict[str, Any]) -> dict[str, Any] | None:
    for row in positions:
        if row.get("instId") != intent["instId"] or str(row.get("posSide") or "net").lower() != intent["posSide"]:
            continue
        if intent["posId"] and str(row.get("posId") or "") != intent["posId"]:
            continue
        return row
    return None


def fast_close_confirmed(close_token: str, confirmation: str) -> dict[str, Any]:
    intent = _consume_intent(close_token)
    env = selected_environment()
    if env.identity != intent["environment_id"]:
        raise ValueError("交易所、环境或凭证已变化，请刷新当前持仓")
    if confirmation.strip().upper() != intent["confirmation"]:
        raise ValueError("确认短语与当前平仓意图不匹配")
    exchange = get_exchange(env)
    inst_id, pos_side = intent["instId"], intent["posSide"]
    target = _position_match(exchange.positions(inst_id), intent)
    if target is None:
        raise ValueError("目标仓位已不存在，请刷新")
    signed = _quantity(target.get("pos", "0"))
    if signed != _quantity(intent["signed_size"]):
        raise ValueError("仓位方向或数量已变化，请刷新当前持仓")
    canceled = []
    direction = pos_side if pos_side in {"long", "short"} else ("long" if signed > 0 else "short")
    for order in exchange.open_orders(inst_id):
        if str(order.get("posSide") or "net").lower() in {pos_side, direction, "net"}:
            order_id = str(order.get("ordId") or "")
            if order_id:
                exchange.cancel_order(inst_id, order_id)
                canceled.append(order_id)
    # Keep protective orders in place until the exchange confirms zero exposure.
    close_result = exchange.close_position(inst_id, pos_side)
    for attempt in range(10):
        current = _position_match(exchange.positions(inst_id), intent)
        remaining = abs(_quantity(current.get("pos", "0"))) if current is not None else Decimal(0)
        if remaining == 0:
            break
        if attempt < 9:
            time.sleep(0.7)
    else:
        raise RuntimeError("平仓请求已受理但仓位未确认归零；请刷新，禁止重复点击")
    cleanup_errors = []
    try:
        for protection in exchange.protection_orders(inst_id):
            if str(protection.get("posSide") or "net").lower() in {pos_side, direction, "net"}:
                exchange.cancel_protection(inst_id, str(protection["algoId"]))
    except Exception as exc:
        cleanup_errors.append(type(exc).__name__)
    return {
        "status": "confirmed_closed", "exchange": env.exchange, "environment": env.mode,
        "instId": inst_id, "posSide": pos_side, "closed_size": str(abs(signed)),
        "quantity_unit": "base", "canceled_entry_orders": canceled, "close_result": close_result,
        "protection_cleanup_errors": cleanup_errors,
    }

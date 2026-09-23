"""Logical position side -> OKX wire posSide (single source of truth).

Strategy keeps logical long/short. Exchange boundary converts using account posMode:

- net_mode: place_order / set_leverage omit posSide; close/algos use "net"
- long_short_mode: wire long/short

Fail closed: unknown posMode never guesses and never calls set-position-mode.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

VALID_LOGICAL = ("long", "short", "net")
VALID_POS_MODES = ("net_mode", "long_short_mode")

# endpoint semantics:
#   order | place_order | set_leverage  -> omit in net (True OKX wire)
#   close | close_position | algo | order_algo | place_algo -> "net" in net_mode
_OMIT_ENDPOINTS = frozenset({"order", "place_order", "set_leverage", "open", "entry"})
_NET_ENDPOINTS = frozenset({
    "close", "close_position", "close-position",
    "algo", "order_algo", "place_algo", "place_algo_oco", "oco", "protect", "protection",
})


def normalize_logical_side(side: Any) -> str:
    value = str(side or "").strip().lower()
    if value not in VALID_LOGICAL:
        raise ValueError(f"logical pos_side 仅允许 long/short/net，收到 {side!r}")
    return value


def normalize_pos_mode(pos_mode: Any) -> str:
    value = str(pos_mode or "").strip().lower()
    # accept OKX raw values only — do not guess
    if value not in VALID_POS_MODES:
        raise ValueError(
            f"无法确认 OKX posMode（期望 net_mode/long_short_mode），拒绝猜测: {pos_mode!r}"
        )
    return value


def logical_side_from_position(pos_side: Any, signed_pos: Any) -> str:
    """Convert an OKX position row into the trader logical side.

    OKX net-mode rows carry ``posSide=net`` and encode direction in the sign of
    ``pos``. Hedge-mode rows carry long/short explicitly. Zero rows are kept as
    ``net`` so downstream active-position filters can ignore them naturally.
    """
    wire = str(pos_side or "").strip().lower()
    try:
        position = float(signed_pos or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"OKX position pos 非法: {signed_pos!r}") from exc

    if wire in ("long", "short"):
        return wire
    if wire == "net":
        if position > 0:
            return "long"
        if position < 0:
            return "short"
        return "net"
    raise ValueError(f"OKX position posSide 非法: {pos_side!r}")


def normalize_trader_position(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one raw OKX position row for trader-internal consumption.

    Trader logic expects an explicit long/short side and a positive position
    size. Preserve the exchange representation for diagnostics while exposing a
    stable logical shape to risk, factor, protection and lifecycle code.
    """
    if not isinstance(row, Mapping):
        raise TypeError(f"OKX position row 必须是对象，收到 {type(row).__name__}")
    normalized = dict(row)
    wire_side = str(row.get("posSide") or "").strip().lower()
    try:
        signed_pos = float(row.get("pos") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"OKX position pos 非法: {row.get('pos')!r}") from exc
    logical = logical_side_from_position(wire_side, signed_pos)
    normalized["wirePosSide"] = wire_side
    normalized["signedPos"] = signed_pos
    normalized["posSide"] = logical
    normalized["side"] = logical
    normalized["pos"] = abs(signed_pos)
    return normalized


def algo_order_matches_logical_side(order: Mapping[str, Any], logical_side: Any) -> bool:
    """Match a pending OKX protective algo order to a trader logical side.

    Hedge-mode algos identify the side directly. Net-mode algos use
    ``posSide=net`` so the close order side disambiguates the protected
    position: sell protects a long, buy protects a short. Missing/unknown side
    information fails closed.
    """
    if not isinstance(order, Mapping):
        return False
    logical = normalize_logical_side(logical_side)
    if logical not in ("long", "short"):
        return False
    wire = str(order.get("posSide") or "").strip().lower()
    if wire == logical:
        return True
    if wire != "net":
        return False
    expected_close_side = "sell" if logical == "long" else "buy"
    return str(order.get("side") or "").strip().lower() == expected_close_side


def resolve_wire_pos_side(
    logical_side: Any,
    pos_mode: Any,
    *,
    endpoint: str = "order",
) -> Optional[str]:
    """Return wire posSide or None (field must be omitted from JSON body)."""
    logical = normalize_logical_side(logical_side)
    mode = normalize_pos_mode(pos_mode)
    ep = str(endpoint or "order").strip().lower()

    if mode == "long_short_mode":
        if logical == "net":
            raise ValueError("long_short_mode 不接受逻辑侧 net")
        return logical

    # net_mode
    if ep in _OMIT_ENDPOINTS:
        return None
    if ep in _NET_ENDPOINTS:
        return "net"
    # unknown endpoint: fail closed to omit (never long/short in net_mode)
    return None


def get_position_mode(env: Any = None) -> str:
    """Fetch and validate account posMode. Fail closed on any doubt."""
    from scripts import okx_rest

    rows = okx_rest.account_config(env=env)
    if not rows:
        raise RuntimeError("OKX account/config 为空，无法确认 posMode（fail-closed）")
    row = rows[0] if isinstance(rows, list) else rows
    if not isinstance(row, dict):
        raise RuntimeError("OKX account/config 形态异常（fail-closed）")
    return normalize_pos_mode(row.get("posMode"))


def wire_pos_side(logical_side: Any, *, endpoint: str = "order", env: Any = None) -> Optional[str]:
    """Resolve wire posSide using live account posMode (fail closed)."""
    return resolve_wire_pos_side(logical_side, get_position_mode(env=env), endpoint=endpoint)

"""Logical position side -> OKX wire posSide (single source of truth).

Strategy keeps logical long/short. Exchange boundary converts using account posMode:

- net_mode: place_order / set_leverage omit posSide; close/algos use "net"
- long_short_mode: wire long/short

Fail closed: unknown posMode never guesses and never calls set-position-mode.
"""
from __future__ import annotations

from typing import Any, Optional

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

"""Account-scoped durable pending-entry state. Local file I/O only; no network."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from r20_exchange.runtime import state_path

PENDING_STATE_NAME = "pending_entries.json"
STORE_VERSION = 1
PENDING_LOCK = threading.RLock()

REQUIRED_ENTRY_FIELDS = (
    "group",
    "inst_id",
    "side",
    "pos_side",
    "qty",
    "price",
    "tp",
    "sl",
    "baseline_qty",
    "client_ids",
    "order_id",
    "created_ms",
    "posted",
    "entry_unknown",
    "entry_status",
    "entry_filled",
    "cancel_requested",
    "sl_leg",
    "tp_leg",
    "close_leg",
    "protection_status",
)
REQUIRED_CLIENT_KEYS = ("EN", "SL", "TP", "CL")
REQUIRED_LEG_FIELDS = ("state", "posted", "unknown", "algo_id")
LEG_STATES = {"idle", "submitting", "unknown", "confirmed", "rejected", "finished"}
PROTECTION_STATUSES = {
    "awaiting_fill",
    "protected",
    "sl_only",
    "flat",
    "blocked",
}


class PendingStoreCorrupt(RuntimeError):
    """Malformed durable pending-entry state; callers must fail closed."""


def pending_state_path(env: Any) -> Path:
    return state_path(PENDING_STATE_NAME, env)


def empty_leg() -> dict[str, Any]:
    return {"state": "idle", "posted": False, "unknown": False, "algo_id": ""}


def new_record(
    *,
    group: str,
    inst_id: str,
    side: str,
    pos_side: str,
    qty: str,
    price: str,
    tp: str,
    sl: str,
    baseline_qty: str,
    client_ids: dict[str, str],
    created_ms: int,
) -> dict[str, Any]:
    return {
        "group": str(group),
        "inst_id": str(inst_id),
        "side": str(side),
        "pos_side": str(pos_side),
        "qty": str(qty),
        "price": str(price),
        "tp": str(tp),
        "sl": str(sl),
        "baseline_qty": str(baseline_qty),
        "client_ids": {key: str(client_ids[key]) for key in REQUIRED_CLIENT_KEYS},
        "order_id": "",
        "created_ms": int(created_ms),
        "posted": False,
        "entry_unknown": False,
        "entry_status": "submitting",
        "entry_filled": "0",
        "cancel_requested": False,
        "sl_leg": empty_leg(),
        "tp_leg": empty_leg(),
        "close_leg": empty_leg(),
        "protection_status": "awaiting_fill",
    }


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PendingStoreCorrupt(f"pending entry field {field} is invalid")
    return value


def _validate_leg(leg: Any, name: str) -> dict[str, Any]:
    if not isinstance(leg, dict):
        raise PendingStoreCorrupt(f"pending {name} is malformed")
    for field in REQUIRED_LEG_FIELDS:
        if field not in leg:
            raise PendingStoreCorrupt(f"pending {name} is missing {field}")
    state = str(leg.get("state") or "")
    if state not in LEG_STATES:
        raise PendingStoreCorrupt(f"pending {name} state is invalid")
    if not isinstance(leg.get("posted"), bool) or not isinstance(leg.get("unknown"), bool):
        raise PendingStoreCorrupt(f"pending {name} flags are invalid")
    algo_id = leg.get("algo_id")
    if not isinstance(algo_id, str):
        raise PendingStoreCorrupt(f"pending {name} algo_id is invalid")
    return {
        "state": state,
        "posted": bool(leg["posted"]),
        "unknown": bool(leg["unknown"]),
        "algo_id": algo_id,
    }


def validate_record(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise PendingStoreCorrupt("pending entry is not an object")
    for field in REQUIRED_ENTRY_FIELDS:
        if field not in row:
            raise PendingStoreCorrupt(f"pending entry is missing {field}")
    client_ids = row.get("client_ids")
    if not isinstance(client_ids, dict):
        raise PendingStoreCorrupt("pending client_ids are malformed")
    for key in REQUIRED_CLIENT_KEYS:
        value = client_ids.get(key)
        if not isinstance(value, str) or not value:
            raise PendingStoreCorrupt(f"pending client id {key} is invalid")
    status = str(row.get("protection_status") or "")
    if status not in PROTECTION_STATUSES:
        raise PendingStoreCorrupt("pending protection_status is invalid")
    created_ms = row.get("created_ms")
    if not isinstance(created_ms, int) or created_ms < 0:
        raise PendingStoreCorrupt("pending created_ms is invalid")
    for flag in ("posted", "entry_unknown", "cancel_requested"):
        if not isinstance(row.get(flag), bool):
            raise PendingStoreCorrupt(f"pending flag {flag} is invalid")
    entry_status = row.get("entry_status")
    if not isinstance(entry_status, str) or not entry_status:
        raise PendingStoreCorrupt("pending entry_status is invalid")
    order_id = row.get("order_id")
    if not isinstance(order_id, str):
        raise PendingStoreCorrupt("pending order_id is invalid")
    return {
        "group": _require_text(row["group"], "group"),
        "inst_id": _require_text(row["inst_id"], "inst_id"),
        "side": _require_text(row["side"], "side"),
        "pos_side": _require_text(row["pos_side"], "pos_side"),
        "qty": _require_text(row["qty"], "qty"),
        "price": _require_text(row["price"], "price"),
        "tp": _require_text(row["tp"], "tp"),
        "sl": _require_text(row["sl"], "sl"),
        "baseline_qty": str(row["baseline_qty"]),
        "client_ids": {key: str(client_ids[key]) for key in REQUIRED_CLIENT_KEYS},
        "order_id": order_id,
        "created_ms": created_ms,
        "posted": bool(row["posted"]),
        "entry_unknown": bool(row["entry_unknown"]),
        "entry_status": str(entry_status),
        "entry_filled": str(row["entry_filled"]),
        "cancel_requested": bool(row["cancel_requested"]),
        "sl_leg": _validate_leg(row["sl_leg"], "sl_leg"),
        "tp_leg": _validate_leg(row["tp_leg"], "tp_leg"),
        "close_leg": _validate_leg(row["close_leg"], "close_leg"),
        "protection_status": status,
    }


def load_pending_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PendingStoreCorrupt("pending entry state is unreadable") from exc
    if not isinstance(raw, dict) or raw.get("version") != STORE_VERSION:
        raise PendingStoreCorrupt("pending entry state version is invalid")
    rows = raw.get("entries")
    if not isinstance(rows, list):
        raise PendingStoreCorrupt("pending entry list is malformed")
    return [validate_record(row) for row in rows]


def save_pending_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    payload = {
        "version": STORE_VERSION,
        "entries": [validate_record(row) for row in entries],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass



__all__ = [
    "PENDING_LOCK",
    "PENDING_STATE_NAME",
    "PendingStoreCorrupt",
    "empty_leg",
    "load_pending_entries",
    "new_record",
    "pending_state_path",
    "save_pending_entries",
    "validate_record",
]

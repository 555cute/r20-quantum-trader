"""Ledger/bill helpers. Paths come from r20_exchange.runtime.state_path."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


def atomic_write_json(path: Path, payload: Any, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".account-state-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
        os.chmod(path, mode)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def history_fetch_incomplete(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    if bool(getattr(exc, "incomplete", False)) or bool(getattr(exc, "window_limited", False)):
        return True
    code = str(getattr(exc, "code", "") or "")
    if code == "positions_history_incomplete":
        return True
    status = str(getattr(exc, "status", "") or "").lower()
    text = str(exc)
    if "positions_history_incomplete" in text:
        return True
    return status == "unavailable" and "history" in text.lower()


def read_ledger_sync_status(env: Any | None = None) -> dict[str, Any]:
    from r20_exchange.runtime import state_path

    path = state_path("ledger_sync_status.json", env)
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_ledger_sync_status(payload: Mapping[str, Any], env: Any | None = None) -> Path:
    from r20_exchange.runtime import state_path

    path = state_path("ledger_sync_status.json", env)
    atomic_write_json(path, dict(payload))
    return path


def ledger_is_incomplete(env: Any | None = None) -> bool:
    status = read_ledger_sync_status(env)
    return bool(status.get("incomplete") or status.get("status") == "unavailable")


def classify_bill(bill: Mapping[str, Any]) -> str:
    """Separate realized PnL, commission, and funding. Never merge commission into pnl."""
    b_type = str(bill.get("type") or "")
    sub_type = str(bill.get("subType") or "")
    type_u = b_type.upper()
    if (
        b_type in {"8", "funding"}
        or type_u in {"FUNDING_FEE", "FUNDING"}
        or sub_type in {"173", "174"}
    ):
        return "funding"
    if type_u in {"COMMISSION", "FEE"}:
        return "commission"
    if sub_type in {"5", "6"} or type_u in {"REALIZED_PNL", "TRADE"}:
        return "realized"
    if sub_type in {"1", "2", "3", "4"}:
        return "commission"
    pnl = float(bill.get("pnl") or 0)
    fee = float(bill.get("fee") or 0)
    if pnl == 0 and fee != 0:
        return "commission"
    return "other"

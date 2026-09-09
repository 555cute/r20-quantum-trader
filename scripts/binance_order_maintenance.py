#!/usr/bin/env python3
"""Gateway-owned Binance pending-entry maintenance.

Reuses the trader/config file lock and freeze_environment. Does not import
ai_factor_trader (avoids loading strategy globals in the worker process).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from r20_backend.file_lock import acquire, release

LOCK_NAME = ".ai_factor_trader.lock"


def trader_cycle_lock_path() -> Path:
    from r20_exchange.runtime import DATA_DIR
    return Path(DATA_DIR) / LOCK_NAME


def run_pending_entry_maintenance() -> dict[str, Any]:
    """Account-scoped reconcile. No network if unconfigured or no local pending records."""
    result: dict[str, Any] = {
        "status": "skipped",
        "reason": "",
        "pending": 0,
        "blocked": False,
        "errors": [],
        "networked": False,
    }
    cycle_environment = None
    lock_handle = None
    try:
        lock_path = trader_cycle_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = open(lock_path, "a+", encoding="utf-8")
        try:
            acquire(lock_handle, blocking=False)
        except BlockingIOError:
            lock_handle.close()
            lock_handle = None
            result["status"] = "busy"
            result["reason"] = "trader cycle lock held"
            return result

        from r20_exchange.runtime import freeze_environment, get_exchange

        cycle_environment = freeze_environment()
        if cycle_environment.exchange != "binance":
            result["reason"] = "not_binance"
            return result
        if not cycle_environment.configured:
            result["reason"] = "unconfigured"
            return result

        exchange = get_exchange(cycle_environment)
        if not bool(exchange.has_pending_entries()):
            result["reason"] = "no_pending"
            return result

        result["networked"] = True
        reconciled = exchange.reconcile_pending_entries()
        if not isinstance(reconciled, dict):
            result["status"] = "error"
            result["blocked"] = True
            result["errors"] = ["reconcile_pending_entries returned a non-dict result"]
            result["reason"] = result["errors"][0]
            return result

        errors = [str(item).strip() for item in (reconciled.get("errors") or []) if str(item).strip()]
        try:
            pending = int(reconciled.get("pending"))
        except (TypeError, ValueError):
            pending = -1
            errors.append("pending count is not an integer")
        blocked = bool(reconciled.get("blocked"))
        if pending != 0:
            blocked = True
        result.update({
            "status": "error" if errors else "reconciled",
            "pending": pending,
            "blocked": blocked,
            "errors": errors,
            "reason": "; ".join(errors) if errors else ("blocked" if blocked else "ok"),
        })
        return result
    except Exception as exc:
        result["status"] = "error"
        result["blocked"] = True
        message = f"{type(exc).__name__}: {exc}"
        result["errors"] = [message]
        result["reason"] = message
        return result
    finally:
        if cycle_environment is not None:
            try:
                from r20_exchange.runtime import unfreeze_environment
                unfreeze_environment()
            except Exception:
                pass
        if lock_handle is not None:
            try:
                release(lock_handle)
            except OSError:
                pass
            try:
                lock_handle.close()
            except OSError:
                pass


if __name__ == "__main__":
    payload = run_pending_entry_maintenance()
    errors = payload.get("errors") or []
    if payload.get("status") == "error" or errors:
        print(f"[OrderMaintenance] Error: {payload.get('reason') or '; '.join(errors)}")
        raise SystemExit(1)
    if payload.get("status") == "busy":
        print(f"[OrderMaintenance] Skip: {payload.get('reason')}")
        raise SystemExit(0)
    print(f"[OrderMaintenance] {payload.get('status')}: {payload.get('reason')}")
    raise SystemExit(0)

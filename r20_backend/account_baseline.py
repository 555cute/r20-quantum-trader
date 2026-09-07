"""Atomic account performance baseline storage shared by admin and dashboard."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from r20_exchange.runtime import state_path
from .config import ROOT

_UNPATCHED_BASELINE = ROOT / "data" / "account_initial_state.json"
BASELINE_FILE = _UNPATCHED_BASELINE
BJ_TZ = timezone(timedelta(hours=8))
DEFAULT_CAPITAL = 10_000.0
MIN_CAPITAL = 1.0
MAX_CAPITAL = 1_000_000_000.0


def resolve_baseline_file() -> Path:
    """Tests may patch BASELINE_FILE; production never reads the unscoped legacy file."""
    if Path(BASELINE_FILE) != _UNPATCHED_BASELINE:
        return Path(BASELINE_FILE)
    return state_path("account_initial_state.json")




def _number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def load_account_baseline() -> dict[str, Any]:
    path = resolve_baseline_file()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    env_default = _number(os.getenv("INITIAL_CAPITAL"), DEFAULT_CAPITAL)
    capital = _number(data.get("initial_capital"), env_default)
    return {
        **data,
        "initial_capital": round(capital, 2),
        "reset_time": str(data.get("reset_time") or "1970-01-01 00:00:00"),
    }


def update_initial_capital(initial_capital: float) -> dict[str, Any]:
    capital = round(float(initial_capital), 2)
    if not MIN_CAPITAL <= capital <= MAX_CAPITAL:
        raise ValueError(f"初始本金必须在 {MIN_CAPITAL:.2f} 到 {MAX_CAPITAL:.2f} USDT 之间")
    previous = load_account_baseline()
    updated = {
        **previous,
        "initial_capital": capital,
        "capital_updated_at": datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = resolve_baseline_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".account-baseline-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(updated, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return {
        "previous_initial_capital": previous["initial_capital"],
        **updated,
    }

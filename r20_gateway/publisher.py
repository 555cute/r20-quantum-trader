"""Public publisher API for strategy and scheduler processes."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Any

from r20_backend.notifications import enabled_channels
from r20_gateway.events import GatewayEvent
from r20_gateway.store import GatewayStore

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("R20_GATEWAY_DB") or (ROOT / "data" / "r20_gateway.db"))


def publish(
    event_type: str,
    title: str,
    message: str,
    payload: dict[str, Any] | None = None,
    priority: int = 50,
    channels: list[str] | None = None,
) -> str:
    # Test suite isolation: if running under unittest/pytest and no explicit test DB is set,
    # never leak mock/fixture events into the production gateway queue.
    if not os.environ.get("R20_GATEWAY_DB") and ("unittest" in sys.modules or "pytest" in sys.modules):
        if not os.environ.get("R20_ALLOW_TEST_PUBLISH"):
            return "test-noop"

    db_path = Path(os.environ.get("R20_GATEWAY_DB") or DB_PATH)
    event = GatewayEvent(event_type=event_type, title=title, message=message, payload=payload or {}, priority=priority)
    targets = enabled_channels() if channels is None else channels
    return GatewayStore(db_path).publish(event, targets)

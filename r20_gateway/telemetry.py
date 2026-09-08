"""Best-effort model-call telemetry; never stores prompt or response content."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib
import time
from typing import Any

from r20_gateway.publisher import DB_PATH
from r20_gateway.store import GatewayStore

BJ_TZ = timezone(timedelta(hours=8))


class ModelCallTelemetry:
    def __init__(self, caller: str, model: str, reasoning_effort: str, system_prompt: str, user_prompt: str):
        self.caller = caller
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.input_chars = len(system_prompt) + len(user_prompt)
        fingerprint_source = f"{system_prompt}\0{user_prompt}".encode("utf-8")
        self.prompt_fingerprint = hashlib.sha256(fingerprint_source).hexdigest()[:16]
        self.started_at = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
        self.started = time.monotonic()

    def finish(self, status: str, response: dict[str, Any] | None = None, output_chars: int = 0, error: Exception | None = None) -> None:
        usage = (response or {}).get("usage", {}) if isinstance(response, dict) else {}
        if not isinstance(usage, dict):
            usage = {}
        if error is not None:
            err_usage = getattr(error, "usage", None)
            if isinstance(err_usage, dict) and err_usage and not usage:
                usage = err_usage
            err_chars = getattr(error, "output_chars", None)
            if output_chars == 0 and err_chars is not None:
                output_chars = int(err_chars)
        error_type = type(error).__name__ if error else ""
        stage = getattr(error, "stage", "") if error is not None else ""
        if stage and error_type:
            error_type = f"{error_type}:{stage}"
        record = {
            "caller": self.caller,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "status": status,
            "started_at": self.started_at,
            "duration_ms": max(0, round((time.monotonic() - self.started) * 1000)),
            "input_chars": self.input_chars,
            "output_chars": output_chars,
            "prompt_fingerprint": self.prompt_fingerprint,
            "prompt_transport": "python-direct",
            "input_tokens": usage.get("prompt_tokens") or usage.get("input_tokens"),
            "output_tokens": usage.get("completion_tokens") or usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "error_type": error_type,
        }
        try:
            GatewayStore(DB_PATH).record_model_call(record)
        except Exception:
            pass

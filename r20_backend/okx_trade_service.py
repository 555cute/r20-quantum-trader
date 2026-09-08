"""Native OKX V5 transport and official CLI OAuth fallback.

Account-bound user actions live in trade_service; this module handles only OKX I/O.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from scripts.okx_runtime import OKXEnvironment, selected_environment


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Refusing to redirect an authenticated exchange request")


_OPENER = urllib.request.build_opener(_NoRedirect())


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _run_cli(command: list[str], timeout: int = 20, env: OKXEnvironment | None = None) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env.cli_env() if env is not None else None)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"OKX CLI execution failed: {type(exc).__name__}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"OKX CLI failed with exit status {result.returncode}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OKX CLI returned invalid JSON") from exc
    if isinstance(payload, dict):
        if str(payload.get("code", payload.get("sCode", "0"))) != "0":
            raise RuntimeError(f"OKX CLI rejected request ({payload.get('code', payload.get('sCode'))})")
        payload = payload.get("data", [payload])
    rows = payload if isinstance(payload, list) else [payload]
    if any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("OKX CLI returned an invalid row schema")
    for row in rows:
        if str(row.get("sCode", row.get("code", "0"))) != "0":
            raise RuntimeError(f"OKX CLI rejected request ({row.get('sCode', row.get('code'))})")
    return rows


def _cli_request(path: str, params: Any, env: OKXEnvironment, timeout: int) -> list[dict[str, Any]]:
    routes = {
        "/api/v5/account/positions": ["account", "positions"],
        "/api/v5/account/balance": ["account", "balance"],
        "/api/v5/account/bills": ["account", "bills"],
        "/api/v5/account/positions-history": ["account", "positions-history"],
        "/api/v5/account/set-leverage": ["swap", "leverage"],
        "/api/v5/trade/orders-pending": ["swap", "orders"],
        "/api/v5/trade/orders-history": ["swap", "orders", "--history"],
        "/api/v5/trade/order": ["swap", "place"],
        "/api/v5/trade/cancel-order": ["swap", "cancel"],
        "/api/v5/trade/close-position": ["swap", "close"],
        "/api/v5/trade/orders-algo-pending": ["swap", "algo", "orders"],
        "/api/v5/trade/order-algo": ["swap", "algo", "place"],
        "/api/v5/trade/amend-algos": ["swap", "algo", "amend"],
        "/api/v5/trade/cancel-algos": ["swap", "algo", "cancel"],
    }
    if path not in routes:
        raise RuntimeError("This OKX endpoint requires static API credentials")
    if isinstance(params, list):
        result = []
        for row in params:
            result.extend(_cli_request(path, row, env, timeout))
        return result
    params = {key: value for key, value in (params or {}).items() if value not in (None, "")}
    command = ["okx", f"--{env.mode}", *routes[path]]
    positional = path in {"/api/v5/trade/cancel-order", "/api/v5/trade/cancel-algos"}
    if positional:
        command.append(str(params.pop("instId")))
    if path == "/api/v5/account/balance" and "ccy" in params:
        command.append(str(params.pop("ccy")))
    # Existing CLI supports flat TP/SL flags, while V5 expects attachAlgoOrds.
    attachments = params.pop("attachAlgoOrds", [])
    if attachments:
        if len(attachments) != 1:
            raise ValueError("CLI transport only supports one attached TP/SL pair")
        params.update(attachments[0])
    for key, value in params.items():
        if key == "instType" or (key == "ordType" and path == "/api/v5/trade/orders-algo-pending"):
            continue
        if isinstance(value, bool):
            if value:
                command.append(f"--{key}")
        elif str(value).startswith("-"):
            command.append(f"--{key}={value}")
        else:
            command.extend([f"--{key}", str(value)])
    command.append("--json")
    return _run_cli(command, timeout=timeout, env=env)


def _request(method: str, path: str, params: dict[str, Any] | list[dict[str, Any]] | None = None, env: OKXEnvironment | None = None, timeout: int = 20) -> list[dict[str, Any]]:
    selected = env if env is not None else selected_environment()
    if selected.base_url != "https://www.okx.com" or not path.startswith("/api/v5/"):
        raise ValueError("Unsupported authenticated OKX endpoint")
    if not selected.configured:
        return _cli_request(path, params, selected, timeout)
    method = method.upper()
    values = params if isinstance(params, list) else {key: value for key, value in (params or {}).items() if value not in (None, "")}
    query = urllib.parse.urlencode(values) if method == "GET" and isinstance(values, dict) else ""
    request_path = path + (f"?{query}" if query else "")
    body_text = json.dumps(values, separators=(",", ":"), ensure_ascii=False) if method != "GET" else ""
    timestamp = _timestamp()
    prehash = timestamp + method + request_path + body_text
    signature = base64.b64encode(hmac.new(selected.secret_key.encode(), prehash.encode(), hashlib.sha256).digest()).decode()
    headers = {
        "Content-Type": "application/json", "Accept": "application/json",
        "OK-ACCESS-KEY": selected.api_key, "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp, "OK-ACCESS-PASSPHRASE": selected.passphrase,
    }
    if selected.simulated:
        headers["x-simulated-trading"] = "1"
    request = urllib.request.Request(selected.base_url + request_path, data=body_text.encode() if body_text else None, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"OKX request could not be confirmed: {type(exc).__name__}") from exc
    if not isinstance(payload, dict) or str(payload.get("code")) != "0" or not isinstance(payload.get("data"), list):
        raise RuntimeError("OKX response did not confirm a successful operation")
    rows = payload["data"]
    if any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("OKX response contained invalid data rows")
    for row in rows:
        if str(row.get("sCode", "0")) != "0":
            raise RuntimeError(f"OKX rejected request ({row['sCode']})")
    return rows

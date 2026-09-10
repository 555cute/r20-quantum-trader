"""Small native OKX REST client; public endpoints work without credentials."""
from __future__ import annotations
import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from scripts import okx_rest
from scripts.okx_runtime import OKXEnvironment, current_environment


class OKXClient:
    def __init__(self) -> None:
        self.base_url = "https://www.okx.com"

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        # Only these public reads may use the unsigned transport. Every other
        # path goes through the single fail-closed private transport.
        if method.upper() != "GET" or path not in {
            "/api/v5/market/ticker", "/api/v5/market/candles", "/api/v5/public/instruments"
        }:
            return okx_rest.request(method, path, params, env=current_environment())
        params = params or {}
        method = method.upper()
        query = urlencode(params) if method == "GET" else ""
        request_path = path + (f"?{query}" if query else "")
        url = f"{self.base_url}{request_path}"
        body = json.dumps(params, separators=(",", ":")).encode("utf-8") if method != "GET" else None
        headers = {"User-Agent": "R20-Standalone/6.6.2"}
        if body:
            headers["Content-Type"] = "application/json"
        req = Request(url, data=body, headers=headers, method=method)
        with urlopen(req, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("code") not in (None, "0", 0):
            raise RuntimeError(payload.get("msg", "OKX request failed"))
        return payload.get("data", payload)

    def ticker(self, inst_id: str) -> Any:
        return self._request("GET", "/api/v5/market/ticker", {"instId": inst_id})

    def candles(self, inst_id: str, bar: str = "1H", limit: int = 100) -> Any:
        return self._request("GET", "/api/v5/market/candles", {"instId": inst_id, "bar": bar, "limit": limit})

    def instruments(self, inst_type: str = "SWAP", inst_id: str | None = None) -> Any:
        params = {"instType": inst_type}
        if inst_id:
            params["instId"] = inst_id
        return self._request("GET", "/api/v5/public/instruments", params)

    def balance(self, *, env: OKXEnvironment | None = None) -> Any:
        return okx_rest.balances(env=env or current_environment())

    def positions(self, *, env: OKXEnvironment | None = None) -> Any:
        return okx_rest.positions(env=env or current_environment())

    def close_position(self, inst_id: str, pos_side: str, *, env: OKXEnvironment | None = None) -> Any:
        if pos_side not in {"long", "short"}:
            raise ValueError("pos_side must be long or short")
        return okx_rest.close_position(inst_id, pos_side, env=env or current_environment())

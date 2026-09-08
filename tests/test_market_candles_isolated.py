"""Exercise the real chart route without importing application startup or credentials."""
import ast
import json
import tempfile
import time
import types
import unittest
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient


class ChartCandleAuthenticityTests(unittest.TestCase):
    def test_missing_market_data_never_becomes_generated_candles(self):
        source = Path(__file__).resolve().parents[1] / "r20_backend" / "app.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        route = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "market_candles")
        app = FastAPI()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            (root / "data" / "factor_library_snapshot.json").write_text(json.dumps({"instruments": {"BTC": {"price": 100000}}}), encoding="utf-8")
            namespace: dict[str, Any] = {
                "app": app, "Any": Any, "Response": Response, "HTTPException": HTTPException,
                "ROOT": root, "time": time, "json": json, "_CANDLES_CACHE": {},
                "selected_environment": Mock(return_value=types.SimpleNamespace(exchange="okx", mode="demo")),
                "get_exchange": Mock(side_effect=OSError("offline market failure")),
            }
            exec(compile(ast.Module(body=[route], type_ignores=[]), str(source), "exec"), namespace)
            with TestClient(app) as client:
                response = client.get("/api/v1/market/BTC-USDT-SWAP/candles")
            self.assertEqual(response.status_code, 502)
            self.assertNotIn("candles", response.json())

    def test_chart_cache_does_not_reuse_across_venues(self):
        source = Path(__file__).resolve().parents[1] / "r20_backend" / "app.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        route = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "market_candles")
        app = FastAPI()
        current = {
            "env": types.SimpleNamespace(exchange="binance", mode="demo", base_url="https://demo-fapi.binance.com"),
        }

        class Adapter:
            def candles(self, inst_id, bar="1H", limit=150):
                close = "101" if current["env"].mode == "demo" else "202"
                return [["1", "1", "2", "0", close, "1"]]

        namespace: dict[str, Any] = {
            "app": app, "Any": Any, "Response": Response, "HTTPException": HTTPException,
            "ROOT": Path("."), "time": time, "json": json, "_CANDLES_CACHE": {},
            "selected_environment": lambda: current["env"],
            "get_exchange": lambda env=None: Adapter(),
        }
        exec(compile(ast.Module(body=[route], type_ignores=[]), str(source), "exec"), namespace)
        with TestClient(app) as client:
            first = client.get("/api/v1/market/BTC-USDT-SWAP/candles?bar=1H&limit=10")
            current["env"] = types.SimpleNamespace(
                exchange="binance", mode="live", base_url="https://fapi.binance.com",
            )
            second = client.get("/api/v1/market/BTC-USDT-SWAP/candles?bar=1H&limit=10")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["candles"][0]["close"], 101.0)
        self.assertEqual(second.json()["candles"][0]["close"], 202.0)



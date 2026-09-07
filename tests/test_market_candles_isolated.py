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
                "urllib": types.SimpleNamespace(request=types.SimpleNamespace(Request=urllib.request.Request, urlopen=Mock(side_effect=OSError("offline market failure")))),
            }
            exec(compile(ast.Module(body=[route], type_ignores=[]), str(source), "exec"), namespace)
            with TestClient(app) as client:
                response = client.get("/api/v1/market/BTC-USDT-SWAP/candles")
            self.assertEqual(response.status_code, 502)
            self.assertNotIn("candles", response.json())

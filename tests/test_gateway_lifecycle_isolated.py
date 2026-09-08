"""Exercise real lifespan logic without importing credential stores or services."""
import ast
import os
import sys
import types
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import Mock, patch


class GatewayLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_owner_never_starts_or_stops_gateway(self):
        source = Path(__file__).resolve().parents[1] / "r20_backend" / "app.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan")
        start, stop = Mock(), Mock()
        dashboard = types.SimpleNamespace()
        dashboard.start_dashboard_background_worker = Mock()
        dashboard.stop_dashboard_background_worker = Mock()
        namespace: dict[str, Any] = {
            "asynccontextmanager": asynccontextmanager, "FastAPI": object, "os": os,
            "refresh_settings": Mock(), "admin_auth": Mock(),
            "settings": types.SimpleNamespace(admin_token="", setup_token=""),
            "resolve_bootstrap_tokens": lambda setup, admin, has_users=False, generate=None: (setup or "", admin or "", {}),
            "update_env": Mock(),
            "start_gateway_supervisor": start, "stop_gateway_supervisor": stop,
        }

        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        with patch.dict(os.environ, {"R20_GATEWAY_WORKER_ENABLED": "0"}), patch.dict(sys.modules, {"dashboard.app": dashboard}):
            async with namespace["lifespan"](object()):
                start.assert_not_called()
            stop.assert_not_called()

    async def test_owned_gateway_is_stopped_when_application_fails(self):
        source = Path(__file__).resolve().parents[1] / "r20_backend" / "app.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan")
        start, stop = Mock(), Mock()
        dashboard = types.SimpleNamespace()
        dashboard.start_dashboard_background_worker = Mock()
        dashboard.stop_dashboard_background_worker = Mock()
        namespace: dict[str, Any] = {
            "asynccontextmanager": asynccontextmanager, "FastAPI": object, "os": os,
            "refresh_settings": Mock(), "admin_auth": Mock(),
            "settings": types.SimpleNamespace(admin_token="", setup_token=""),
            "resolve_bootstrap_tokens": lambda setup, admin, has_users=False, generate=None: (setup or "", admin or "", {}),
            "update_env": Mock(),
            "start_gateway_supervisor": start, "stop_gateway_supervisor": stop,
        }

        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        with patch.dict(os.environ, {"R20_GATEWAY_WORKER_ENABLED": "1"}), patch.dict(sys.modules, {"dashboard.app": dashboard}):
            with self.assertRaisesRegex(RuntimeError, "application failure"):
                async with namespace["lifespan"](object()):
                    start.assert_called_once()
                    raise RuntimeError("application failure")
            stop.assert_called_once()

    def test_process_manager_override_survives_dotenv_refresh(self):
        source = Path(__file__).resolve().parents[1] / "r20_backend" / "config.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        nodes = [
            node for node in tree.body
            if (isinstance(node, ast.FunctionDef) and node.name == "load_dotenv")
            or (isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_STARTUP_WORKER_OVERRIDES"
                for target in node.targets
            ))
        ]
        namespace: dict[str, Any] = {"os": os, "Path": Path}
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"R20_GATEWAY_WORKER_ENABLED": "0"}):
            env_file = Path(directory) / ".env"
            env_file.write_text("R20_GATEWAY_WORKER_ENABLED=1\nLLM_MODEL=updated-model\n", encoding="utf-8")
            exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
            namespace["load_dotenv"](env_file)
            namespace["load_dotenv"](env_file)
            self.assertEqual(os.environ["R20_GATEWAY_WORKER_ENABLED"], "0")
            self.assertEqual(os.environ["LLM_MODEL"], "updated-model")

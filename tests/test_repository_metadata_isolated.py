"""Version information remains available outside a Git checkout."""
import ast
import os
from pathlib import Path
import subprocess
import types
import unittest
from typing import Any
from unittest.mock import Mock

from r20_backend.version import APP_NAME, __version__


class RepositoryMetadataTests(unittest.TestCase):
    def test_missing_git_and_source_archives_do_not_break_version_page(self):
        source = Path(__file__).resolve().parents[1] / "r20_backend" / "app.py"
        nodes = [node for node in ast.parse(source.read_text(encoding="utf-8")).body if isinstance(node, ast.FunctionDef) and node.name in {"git", "update_status", "admin_about"}]
        for outcome in (FileNotFoundError("git"), subprocess.CompletedProcess(["git"], 128, "", "not a git repository")):
            with self.subTest(outcome=type(outcome).__name__):
                run = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
                scope: dict[str, Any] = {
                    "Any": Any, "ROOT": source.parent, "os": os,
                    "app": types.SimpleNamespace(get=lambda *args, **kwargs: lambda fn: fn),
                    "Header": lambda *args, **kwargs: None,
                    "subprocess": types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired),
                    "require_admin_header": lambda *args: None,
                    "GatewayStore": lambda *args: None, "GATEWAY_DB_PATH": None,
                    "gateway_status": lambda *args: {"running": False},
                    "APP_NAME": APP_NAME, "__version__": __version__, "GATEWAY_VERSION": __version__,
                }
                exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
                response = scope["admin_about"]()
                self.assertEqual(response["product"]["version"], __version__)
                self.assertFalse(response["repository"]["available"])
                self.assertIn("error", response["repository"])
                self.assertIn("error", response["update"])

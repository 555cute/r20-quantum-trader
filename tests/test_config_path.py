"""Offline persisted-env path contracts. Temp dirs only; no credentials."""
from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from r20_backend import config_path
from r20_backend.config_path import env_file_path


class EnvFilePathTests(unittest.TestCase):
    def test_default_path_is_root_dotenv(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(config_path, "_ENV_FILE_OVERRIDE", None):
                self.assertEqual(env_file_path(root), root / ".env")
                missing = root / "absent"
                self.assertEqual(env_file_path(missing), missing / ".env")
                self.assertFalse(missing.exists())

    def test_absolute_override_is_used_as_is(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            absolute = root / "persist" / "config.env"
            with patch.object(config_path, "_ENV_FILE_OVERRIDE", str(absolute)):
                self.assertEqual(env_file_path(root / "other"), absolute)

    def test_relative_override_is_joined_to_root(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(config_path, "_ENV_FILE_OVERRIDE", "data/config/.env"):
                self.assertEqual(env_file_path(root), root / "data" / "config" / ".env")

    def test_empty_process_override_is_unset(self):
        with patch.dict(os.environ, {"R20_ENV_FILE": ""}, clear=False):
            self.assertIsNone(config_path._startup_override())
        with patch.dict(os.environ, {"R20_ENV_FILE": "   "}, clear=False):
            self.assertIsNone(config_path._startup_override())

    def test_loaded_env_cannot_redirect_captured_path(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            hijack = root / "hijack" / ".env"
            with patch.object(config_path, "_ENV_FILE_OVERRIDE", None):
                with patch.dict(os.environ, {"R20_ENV_FILE": str(hijack)}, clear=False):
                    self.assertEqual(env_file_path(root), root / ".env")
            captured = str(root / "data" / "config" / ".env")
            with patch.object(config_path, "_ENV_FILE_OVERRIDE", captured):
                with patch.dict(os.environ, {"R20_ENV_FILE": str(hijack)}, clear=False):
                    self.assertEqual(env_file_path(root), Path(captured))


class PersistedEnvFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from r20_backend import settings_store
        from r20_backend import notifications
        from r20_backend import config as backend_config
        import scripts.okx_runtime as okx_runtime
        from r20_exchange import runtime

        cls.settings_store = settings_store
        cls.notifications = notifications
        cls.backend_config = backend_config
        cls.okx_runtime = okx_runtime
        cls.runtime = runtime

    def setUp(self):
        self.runtime.unfreeze_environment()

    def tearDown(self):
        self.runtime.unfreeze_environment()

    def _isolate(self, env_path: Path, root: Path):
        stack = ExitStack()
        stack.enter_context(patch.object(config_path, "_ENV_FILE_OVERRIDE", str(env_path)))
        stack.enter_context(patch.object(self.settings_store, "ENV_FILE", env_path))
        stack.enter_context(patch.object(self.settings_store, "refresh_settings", lambda: None))
        stack.enter_context(patch.object(self.notifications, "ROOT", root))
        stack.enter_context(patch.object(self.okx_runtime, "ROOT", root))
        stack.enter_context(patch("r20_gateway.secrets.load_secrets", return_value={}))
        return stack

    def test_r20_env_file_is_not_api_managed(self):
        self.assertNotIn("R20_ENV_FILE", self.settings_store.MANAGED_KEYS)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / "data" / "config" / ".env"
            with self._isolate(env_path, root):
                self.settings_store.update_env({"R20_ENV_FILE": "/hijack/.env", "R20_EXCHANGE": "binance"})
            text = env_path.read_text(encoding="utf-8")
            self.assertIn("R20_EXCHANGE=binance", text)
            self.assertNotIn("R20_ENV_FILE", text)

    def test_load_dotenv_does_not_apply_path_redirect(self):
        with TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("R20_ENV_FILE=/hijack/.env\nLLM_MODEL=from-file\n", encoding="utf-8")
            with patch.dict(os.environ, {"R20_ENV_FILE": "/captured/.env", "LLM_MODEL": "old"}, clear=False):
                self.backend_config._ORIGINAL_LOAD_DOTENV(env_path)

                self.assertEqual(os.environ["R20_ENV_FILE"], "/captured/.env")
                self.assertEqual(os.environ["LLM_MODEL"], "from-file")

    def test_atomic_write_stays_in_env_parent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / "data" / "config" / ".env"
            captured: dict[str, str] = {}
            real_mkstemp = self.settings_store.tempfile.mkstemp

            def wrapped(*args, **kwargs):
                captured["dir"] = kwargs.get("dir")
                captured["prefix"] = kwargs.get("prefix")
                return real_mkstemp(*args, **kwargs)

            with self._isolate(env_path, root):
                with patch.object(self.settings_store.tempfile, "mkstemp", wrapped):
                    self.settings_store.update_env({"R20_EXCHANGE": "binance"})
            self.assertEqual(Path(captured["dir"]), env_path.parent)
            self.assertEqual(captured["prefix"], ".r20-env-")
            self.assertNotEqual(str(Path(captured["dir"])), tempfile.gettempdir())
            self.assertTrue(env_path.exists())
            self.assertFalse(any(env_path.parent.glob(".r20-env-*")))

    def test_remove_env_creates_missing_parent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / "data" / "config" / ".env"
            self.assertFalse(env_path.parent.exists())
            with self._isolate(env_path, root):
                self.settings_store.remove_env(["LLM_MODEL"])
            self.assertTrue(env_path.parent.exists())
            self.assertTrue(env_path.exists())

    def test_write_is_visible_to_exchange_runtime_and_notifications(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / "data" / "config" / ".env"
            with self._isolate(env_path, root):
                self.settings_store.update_env({
                    "R20_EXCHANGE": "binance",
                    "R20_BINANCE_ENV": "demo",
                    "R20_NOTIFY_QQ_ENABLED": "1",
                })
                self.assertEqual(env_file_path(root), env_path)
                with patch.dict(os.environ, {
                    "R20_EXCHANGE": "okx",
                    "R20_BINANCE_ENV": "live",
                    "R20_NOTIFY_QQ_ENABLED": "0",
                    "BINANCE_DEMO_API_KEY": "",
                    "BINANCE_DEMO_SECRET_KEY": "",
                    "BINANCE_LIVE_API_KEY": "",
                    "BINANCE_LIVE_SECRET_KEY": "",
                    "OKX_API_KEY": "",
                    "OKX_SECRET_KEY": "",
                    "OKX_PASSPHRASE": "",
                }, clear=False):
                    self.assertEqual(self.okx_runtime._load_dotenv()["R20_EXCHANGE"], "binance")
                    selected = self.runtime.selected_environment()
                    self.assertEqual(selected.exchange, "binance")
                    self.assertEqual(selected.mode, "demo")
                    self.assertFalse(selected.configured)
                    self.assertEqual(self.notifications._env()["R20_NOTIFY_QQ_ENABLED"], "1")
                    self.assertEqual(self.notifications._env()["R20_EXCHANGE"], "binance")



if __name__ == "__main__":
    unittest.main()

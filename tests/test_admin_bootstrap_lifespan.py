"""First-boot token generation through the real FastAPI lifespan."""
from __future__ import annotations
import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import r20_backend.app as app_module
from r20_backend.admin_auth import AdminAuthStore


class AdminBootstrapLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_boot_logs_persists_admin_token_and_clears_setup(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_file = root / "config" / ".env"
            env_file.parent.mkdir(parents=True)
            store = AdminAuthStore(root / "r20_admin.db")
            settings_obj = SimpleNamespace(setup_token="", admin_token="")
            original_setup = os.environ.get("R20_SETUP_TOKEN")
            original_admin = os.environ.get("R20_ADMIN_TOKEN")
            first_log = io.StringIO()
            env_patch = {
                "R20_TESTING": "0",
                "R20_GATEWAY_WORKER_ENABLED": "0",
                "R20_DASHBOARD_WORKER_ENABLED": "0",
                "R20_SETUP_TOKEN": "",
                "R20_ADMIN_TOKEN": "",
            }
            try:
                with patch.object(app_module, "admin_auth", store), \
                        patch.object(app_module, "settings", settings_obj), \
                        patch.object(app_module, "refresh_settings"), \
                        patch.object(app_module, "start_gateway_supervisor"), \
                        patch.object(app_module, "stop_gateway_supervisor"), \
                        patch("r20_backend.settings_store.ENV_FILE", env_file), \
                        patch.dict(os.environ, env_patch, clear=False), \
                        redirect_stdout(first_log):
                    async with app_module.lifespan(app_module.app):

                        self.assertTrue(store.has_users())
                        self.assertEqual(settings_obj.setup_token, "")
                        self.assertTrue(settings_obj.admin_token)
                        login = store.login("admin", self._token_from_log(first_log.getvalue(), "R20_SETUP_TOKEN"))
                        self.assertEqual(login["user"]["username"], "admin")
                        with self.assertRaises(PermissionError):
                            store.login("admin", settings_obj.admin_token)

                text = first_log.getvalue()
                self.assertIn("[R20] generated R20_SETUP_TOKEN (initial admin password):", text)
                self.assertIn("[R20] generated R20_ADMIN_TOKEN (X-R20-Admin-Token):", text)
                persisted = env_file.read_text(encoding="utf-8")
                self.assertIn(f"R20_ADMIN_TOKEN={settings_obj.admin_token}", persisted)
                self.assertNotIn("R20_SETUP_TOKEN=", persisted)

                restart_log = io.StringIO()
                settings_obj.setup_token = ""
                with patch.object(app_module, "admin_auth", store), \
                        patch.object(app_module, "settings", settings_obj), \
                        patch.object(app_module, "refresh_settings"), \
                        patch.object(app_module, "start_gateway_supervisor"), \
                        patch.object(app_module, "stop_gateway_supervisor"), \
                        patch("r20_backend.settings_store.ENV_FILE", env_file), \
                        patch.dict(os.environ, {
                            "R20_TESTING": "0",
                            "R20_GATEWAY_WORKER_ENABLED": "0",
                            "R20_DASHBOARD_WORKER_ENABLED": "0",
                            "R20_ADMIN_TOKEN": settings_obj.admin_token,
                            "R20_SETUP_TOKEN": "",
                        }, clear=False), \
                        redirect_stdout(restart_log):
                    async with app_module.lifespan(app_module.app):

                        self.assertTrue(store.has_users())
                self.assertNotIn("[R20] generated", restart_log.getvalue())
            finally:
                if original_setup is None:
                    os.environ.pop("R20_SETUP_TOKEN", None)
                else:
                    os.environ["R20_SETUP_TOKEN"] = original_setup
                if original_admin is None:
                    os.environ.pop("R20_ADMIN_TOKEN", None)
                else:
                    os.environ["R20_ADMIN_TOKEN"] = original_admin

    @staticmethod
    def _token_from_log(text: str, key: str) -> str:
        prefix = f"[R20] generated {key}"
        for line in text.splitlines():
            if prefix in line and ": " in line:
                return line.split(": ", 1)[1].strip()
        raise AssertionError(f"{key} was not logged")


if __name__ == "__main__":
    unittest.main()

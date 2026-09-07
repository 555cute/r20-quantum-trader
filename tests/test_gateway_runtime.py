"""Model telemetry privacy and encrypted secret-store tests."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
import os
from unittest.mock import patch

import r20_gateway.secrets as secrets
import r20_gateway.telemetry as telemetry
from r20_gateway.store import GatewayStore


class GatewayRuntimePrivacyTests(unittest.TestCase):
    def test_telemetry_never_persists_prompt_content(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "gateway.db"
            system_prompt = "PRIVATE_SYSTEM_PROMPT_123"
            user_prompt = "PRIVATE_USER_PROMPT_456"
            with patch.object(telemetry, "DB_PATH", db):
                call = telemetry.ModelCallTelemetry("trading_brain", "model", "high", system_prompt, user_prompt)
                call.finish("success", {"usage": {"total_tokens": 12}}, output_chars=42)
            raw = db.read_bytes()
            self.assertNotIn(system_prompt.encode(), raw)
            self.assertNotIn(user_prompt.encode(), raw)
            record = GatewayStore(db).model_calls()[0]
            self.assertEqual(record["prompt_transport"], "python-direct")
            self.assertEqual(record["input_chars"], len(system_prompt) + len(user_prompt))

    def test_telemetry_failure_is_non_fatal(self):
        call = telemetry.ModelCallTelemetry("trading_brain", "model", "high", "s", "u")
        with patch("r20_gateway.telemetry.GatewayStore", side_effect=OSError("disk")):
            call.finish("failed", error=RuntimeError("model"))

    def test_secret_store_encrypts_and_uses_0600(self):
        with tempfile.TemporaryDirectory() as td:
            original_key, original_store = secrets.KEY_FILE, secrets.STORE_FILE
            secrets.KEY_FILE = Path(td) / ".key"
            secrets.STORE_FILE = Path(td) / "secrets.enc"
            try:
                secrets.save_secrets({"LLM_API_KEY": "PRIVATE-KEY-123", "NOT_ALLOWED": "ignored"})
                self.assertNotIn(b"PRIVATE-KEY-123", secrets.STORE_FILE.read_bytes())
                self.assertEqual(secrets.load_secrets(), {"LLM_API_KEY": "PRIVATE-KEY-123"})
                if os.name == "posix":
                    self.assertEqual(secrets.KEY_FILE.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(secrets.STORE_FILE.stat().st_mode & 0o777, 0o600)
            finally:
                secrets.KEY_FILE, secrets.STORE_FILE = original_key, original_store


if __name__ == "__main__":
    unittest.main()

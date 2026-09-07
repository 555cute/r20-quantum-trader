from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import r20_backend.account_baseline as baseline


class AccountBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "account_initial_state.json"
        self.patch = patch.object(baseline, "BASELINE_FILE", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_update_preserves_reset_time_and_statistics(self):
        original = {
            "initial_capital": 4061.04,
            "reset_time": "2026-08-31 06:57:38",
            "total_trades": 12,
            "win_trades": 7,
        }
        self.path.write_text(json.dumps(original), encoding="utf-8")
        result = baseline.update_initial_capital(5000.125)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["initial_capital"], 5000.12)
        self.assertEqual(saved["reset_time"], original["reset_time"])
        self.assertEqual(saved["total_trades"], 12)
        self.assertEqual(result["previous_initial_capital"], 4061.04)
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_invalid_capital_is_rejected_without_writing(self):
        with self.assertRaises(ValueError):
            baseline.update_initial_capital(0)
        self.assertFalse(self.path.exists())

    def test_load_uses_environment_default_when_file_missing(self):
        with patch.dict("os.environ", {"INITIAL_CAPITAL": "8765.43"}):
            value = baseline.load_account_baseline()
        self.assertEqual(value["initial_capital"], 8765.43)
        self.assertEqual(value["reset_time"], "1970-01-01 00:00:00")

    def test_unpatched_resolve_does_not_read_legacy_file(self):
        scoped = Path(self.temp.name) / "exchanges" / "okx" / "demo" / "abc" / "account_initial_state.json"
        with patch.object(baseline, "BASELINE_FILE", baseline._UNPATCHED_BASELINE), \
             patch.object(baseline, "state_path", return_value=scoped):
            loaded = baseline.load_account_baseline()
        self.assertEqual(loaded["reset_time"], "1970-01-01 00:00:00")
        self.assertFalse(scoped.exists())




if __name__ == "__main__":
    unittest.main()

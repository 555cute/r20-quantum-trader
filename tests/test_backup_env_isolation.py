"""Packable data archives must not include persisted env files."""
from __future__ import annotations

import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.backup_runtime as runtime


class BackupEnvIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "root"
        (self.root / "data").mkdir(parents=True)
        self.old_root = runtime.ROOT
        self.old_backups = runtime.BACKUPS
        runtime.ROOT = self.root
        runtime.BACKUPS = self.root / "backups"

    def tearDown(self):
        runtime.ROOT = self.old_root
        runtime.BACKUPS = self.old_backups
        self.temp.cleanup()

    def _archive_data(self) -> tuple[Path, list[str]]:
        job = {"id": "iso", "scope": ["data"], "exclude": []}
        return runtime.create_archive(job, "20260907_000000")

    def test_default_nested_env_is_omitted_from_data_archive(self):
        nested = self.root / "data" / "config" / ".env"
        nested.parent.mkdir(parents=True)
        nested.write_text("BINANCE_LIVE_API_KEY=should-not-leak\n", encoding="utf-8")
        (self.root / "data" / "keep.json").write_text("ok", encoding="utf-8")
        with patch.object(runtime, "env_file_path", return_value=self.root / ".env"):
            archive, included = self._archive_data()
        self.assertIn("data", included)
        with tarfile.open(archive) as handle:
            names = handle.getnames()
        self.assertIn("data/keep.json", names)
        self.assertNotIn("data/config/.env", names)
        self.assertFalse(any(Path(name).name == ".env" for name in names))

    def test_r20_env_file_under_root_is_omitted(self):
        custom = self.root / "data" / "alts" / "creds.env"
        custom.parent.mkdir(parents=True)
        custom.write_text("OKX_LIVE_SECRET_KEY=should-not-leak\n", encoding="utf-8")
        (self.root / "data" / "keep.json").write_text("ok", encoding="utf-8")
        with patch.object(runtime, "env_file_path", return_value=custom):
            archive, _ = self._archive_data()
        with tarfile.open(archive) as handle:
            names = handle.getnames()
        self.assertIn("data/keep.json", names)
        self.assertNotIn("data/alts/creds.env", names)

    def test_env_file_outside_root_is_not_accessed(self):
        outside = Path(self.temp.name) / "elsewhere" / ".env"
        (self.root / "data" / "keep.json").write_text("ok", encoding="utf-8")
        with patch.object(runtime, "env_file_path", return_value=outside):
            patterns = runtime.secret_env_exclude_patterns(self.root)
            archive, _ = self._archive_data()
        self.assertEqual(patterns, (runtime.NESTED_ENV_FILE,))
        self.assertFalse(outside.exists())
        with tarfile.open(archive) as handle:
            names = handle.getnames()
        self.assertIn("data/keep.json", names)

    def test_env_override_with_parent_segments_is_still_excluded(self):
        aliases = self.root / "data" / "aliases"
        aliases.mkdir()
        private = self.root / "data" / "private.env"
        private.write_text("LLM_API_KEY=must-not-be-archived\n", encoding="utf-8")
        (self.root / "data" / "keep.json").write_text("ok", encoding="utf-8")
        with patch.object(runtime, "env_file_path", return_value=aliases / ".." / "private.env"):
            archive, _ = self._archive_data()
        with tarfile.open(archive) as handle:
            names = handle.getnames()
        self.assertIn("data/keep.json", names)
        self.assertNotIn("data/private.env", names)


if __name__ == "__main__":
    unittest.main()

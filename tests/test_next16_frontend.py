"""Tests for Next.js 16 Static Export and i18n Dictionary Integrity."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path


class Next16FrontendTests(unittest.TestCase):
    def setUp(self):
        self.frontend_next = Path("frontend_next")
        self.out_dir = self.frontend_next / "out"

    def test_next16_static_export_artifacts(self):
        self.assertTrue(self.out_dir.is_dir(), "Next.js out directory should exist")
        index_html = self.out_dir / "index.html"
        self.assertTrue(index_html.is_file(), "out/index.html must exist")
        html_content = index_html.read_text(encoding="utf-8")
        self.assertIn("R20 QUANTUM", html_content)
        self.assertIn("MASTER TERMINAL", html_content)

    def test_i18n_dictionary_completeness(self):
        i18n_file = self.frontend_next / "src" / "i18n" / "index.ts"
        self.assertTrue(i18n_file.is_file(), "i18n index.ts must exist")
        content = i18n_file.read_text(encoding="utf-8")
        # Check that both zh and en dictionaries are defined
        self.assertIn("zh: {", content)
        self.assertIn("en: {", content)
        self.assertIn("masterEquity:", content)
        self.assertIn("chartTitle:", content)
        self.assertIn("cioVerdict:", content)


if __name__ == "__main__":
    unittest.main()

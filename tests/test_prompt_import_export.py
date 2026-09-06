"""Prompt profile export/import format contract tests (US-006).

Covers the self-describing v4 export envelope, backward-compatible import of
the three shapes users actually hold on disk, and actionable unknown-variable
errors surfaced through the admin API.
"""
from __future__ import annotations
import copy
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import r20_backend.app as app_module
import scripts.prompt_library as library
from r20_backend.admin_auth import AdminAuthStore

SLOT_KEYS = ("{{decision_timestamp}}", "{{market_matrix}}", "{{closed_trades_json}}")


class PromptImportExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = library.LIBRARY_FILE
        library.LIBRARY_FILE = Path(self.temp.name) / "prompt_library.json"

    def tearDown(self):
        library.LIBRARY_FILE = self.original
        self.temp.cleanup()

    # ------------------------------------------------------------------ export
    def test_export_is_self_describing_v4(self):
        created = library.create_profile("自描述导出", "说明", source_id="stable")
        exported = library.export_profile(created["id"])
        self.assertEqual(exported["format"], "r20-prompt-profile")
        self.assertEqual(exported["version"], 4)
        self.assertEqual(exported["profile_id"], created["id"])
        self.assertIn("exported_at", exported)
        # variables metadata lets the receiving side understand the slots
        self.assertEqual(
            [item["key"] for item in exported["variables"]],
            [item["key"] for item in library.TEMPLATE_VARIABLES_METADATA],
        )
        self.assertEqual(exported["allowed_variables"], sorted(library.ALLOWED_VARIABLES))
        profile = exported["profile"]
        for key in library.TEMPLATE_KEYS:
            self.assertIn(key, profile)
        self.assertIn("pipelines", profile)
        self.assertIn("editor_mode", profile)
        self.assertIn("simple_policy", profile)

    # ------------------------------------------------------------- round trip
    def test_export_import_roundtrip_preserves_base_modules_and_slots(self):
        created = library.create_profile("往返方案", "往返说明", source_id="stable")
        exported = library.export_profile(created["id"])
        imported = library.import_profile(exported)

        self.assertTrue(imported["name"].endswith("（导入）"))
        self.assertNotEqual(imported["id"], created["id"])
        self.assertEqual(imported["editor_mode"], "modules")

        for key in library.TEMPLATE_KEYS:
            before, after = created["pipelines"][key], imported["pipelines"][key]
            self.assertEqual(len(before), len(after), f"{key} module count drifted")
            self.assertEqual(
                [m["title"] for m in before], [m["title"] for m in after], f"{key} order drifted"
            )
            self.assertEqual(
                [m["source"] for m in before], [m["source"] for m in after], f"{key} source drifted"
            )
            self.assertEqual(
                sum(1 for m in before if m["source"] == "base"),
                sum(1 for m in after if m["source"] == "base"),
                f"{key} base module count drifted",
            )
            self.assertEqual(
                [m["content"] for m in before], [m["content"] for m in after], f"{key} content drifted"
            )

        blob = " ".join(m["content"] for key in library.TEMPLATE_KEYS for m in imported["pipelines"][key])
        for slot in SLOT_KEYS:
            self.assertIn(slot, blob, f"variable slot {slot} lost during round trip")
        # flat fields stay in sync with compiled pipelines
        for key in library.TEMPLATE_KEYS:
            self.assertEqual(imported[key], library.compile_modules(imported["pipelines"][key]))

    def test_import_does_not_overwrite_existing_profile(self):
        created = library.create_profile("唯一名称方案", "", source_id="stable")
        active_before = library.load_library()["active_profile_id"]
        exported = library.export_profile(created["id"])
        library.import_profile(exported)
        library.import_profile(exported)
        names = [p["name"] for p in library.all_profiles()]
        self.assertEqual(names.count("唯一名称方案"), 1)
        self.assertEqual(len([n for n in names if n.startswith("唯一名称方案（导入）")]), 2)
        self.assertEqual(len(set(p["id"] for p in library.all_profiles() if p["name"].startswith("唯一名称方案（导入）"))), 2)
        self.assertEqual(library.load_library()["active_profile_id"], active_before)

    # ------------------------------------------------------- backward compat
    def test_import_accepts_bare_profile_object(self):
        source = {
            "name": "裸方案对象",
            "editor_mode": "advanced",
            "trading_system": "【裸方案规则】\n只做顺势回踩。{{market_matrix}}",
            "trading_user": "",
            "evolution_system": "",
            "evolution_user": "",
        }
        imported = library.import_profile(source)
        self.assertEqual(imported["name"], "裸方案对象（导入）")
        self.assertEqual(imported["editor_mode"], "modules")
        self.assertEqual(len(imported["pipelines"]["trading_system"]), 1)
        self.assertIn("{{market_matrix}}", imported["pipelines"]["trading_system"][0]["content"])

    def test_import_accepts_whole_library_export_and_uses_active_profile(self):
        active = library.create_profile("库内启用方案", "", source_id="stable")
        library.create_profile("库内备用方案", "", source_id="stable")
        library.activate_profile(active["id"])
        dump = library.load_library()
        self.assertIn("profiles", dump)

        imported = library.import_profile(dump)
        self.assertEqual(imported["name"], "库内启用方案（导入）")
        self.assertNotEqual(imported["id"], active["id"])
        self.assertEqual(
            [m["title"] for m in imported["pipelines"]["trading_user"]],
            [m["title"] for m in active["pipelines"]["trading_user"]],
        )

    def test_library_export_without_active_id_falls_back_to_first_profile(self):
        first = library.create_profile("首个方案", "", source_id="stable")
        dump = library.load_library()
        dump.pop("active_profile_id", None)
        dump.pop("active_style", None)
        imported = library.import_profile(dump)
        self.assertEqual(imported["name"], f"{first['name']}（导入）")

    def test_import_accepts_legacy_v1_and_v2_wrappers(self):
        for legacy_version in (1, 2, 3):
            payload = {
                "format": "r20-prompt-profile",
                "version": legacy_version,
                "profile": {
                    "name": f"历史版本{legacy_version}",
                    "editor_mode": "advanced",
                    "trading_system": f"【历史{legacy_version}】只做4H顺势。{{{{news_intelligence}}}}",
                    "trading_user": "",
                    "evolution_system": "",
                    "evolution_user": "",
                },
            }
            imported = library.import_profile(copy.deepcopy(payload))
            self.assertEqual(imported["name"], f"历史版本{legacy_version}（导入）")
            self.assertIn("{{news_intelligence}}", imported["pipelines"]["trading_system"][0]["content"])

    def test_import_rejects_unknown_shape_with_actionable_message(self):
        for bad in ({}, {"foo": "bar"}, "not-a-dict", {"format": "r20-prompt-profile"}):
            with self.subTest(payload=bad):
                with self.assertRaises(ValueError) as ctx:
                    library.import_profile(bad)
                message = str(ctx.exception)
                self.assertIn("无法识别的提示词文件", message)
                for hint in ("r20-prompt-profile", "active_profile_id", "pipelines"):
                    self.assertIn(hint, message)

    # ------------------------------------------------------- unknown variable
    def test_unknown_variable_import_lists_names(self):
        payload = {
            "format": "r20-prompt-profile",
            "version": 4,
            "profile": {
                "name": "含未知变量方案",
                "editor_mode": "advanced",
                "trading_system": "【规则】参考 {{not_a_real_var}} 与 {{also_missing_var}}",
                "trading_user": "",
                "evolution_system": "",
                "evolution_user": "",
            },
        }
        with self.assertRaises(ValueError) as ctx:
            library.import_profile(payload)
        message = str(ctx.exception)
        self.assertIn("未知变量", message)
        self.assertIn("not_a_real_var", message)
        self.assertIn("also_missing_var", message)
        self.assertEqual(len(library.load_library()["profiles"]), 0)

    def test_allowed_variable_set_matches_exported_metadata(self):
        exported = library.export_profile("stable")
        metadata_keys = {item["key"] for item in exported["variables"]}
        self.assertTrue(metadata_keys.issubset(set(exported["allowed_variables"])))
        self.assertEqual(exported["allowed_variables"], sorted(library.ALLOWED_VARIABLES))


class PromptImportExportApiTests(unittest.TestCase):
    """Endpoint-level contract: 200 for valid shapes, 400 with listed variables."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_auth = app_module.admin_auth
        self.original_library = library.LIBRARY_FILE
        root = Path(self.temp.name)
        app_module.admin_auth = AdminAuthStore(root / "admin.db")
        app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        library.LIBRARY_FILE = root / "prompt_library.json"
        self.client = TestClient(app_module.app)
        login = self.client.post(
            "/api/v1/admin/auth/login", json={"username": "admin", "password": "InitialAdmin123456"}
        )
        self.headers = {"X-R20-Session": login.json()["session_token"]}

    def tearDown(self):
        app_module.admin_auth = self.original_auth
        library.LIBRARY_FILE = self.original_library
        self.temp.cleanup()

    def test_export_endpoint_returns_self_describing_payload(self):
        response = self.client.get("/api/v1/admin/prompt-profiles/stable/export", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["version"], 4)
        self.assertEqual(data["profile_id"], "stable")
        self.assertIn("variables", data)
        self.assertIn("allowed_variables", data)

    def test_import_endpoint_accepts_all_three_shapes(self):
        exported = self.client.get("/api/v1/admin/prompt-profiles/stable/export", headers=self.headers).json()
        bare = exported["profile"]
        library_dump = {"version": 2, "active_profile_id": "stable", "profiles": {"stable": bare}}

        for label, payload in (("wrapped", exported), ("bare", bare), ("library", library_dump)):
            with self.subTest(shape=label):
                response = self.client.post(
                    "/api/v1/admin/prompt-profiles/import",
                    headers=self.headers,
                    json={"payload": payload},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["profile"]["id"].startswith("custom-"))

    def test_import_endpoint_returns_400_listing_unknown_variables(self):
        payload = {
            "format": "r20-prompt-profile",
            "version": 4,
            "profile": {
                "name": "坏变量",
                "editor_mode": "advanced",
                "trading_system": "参考 {{not_a_real_var}}",
                "trading_user": "",
                "evolution_system": "",
                "evolution_user": "",
            },
        }
        response = self.client.post(
            "/api/v1/admin/prompt-profiles/import", headers=self.headers, json={"payload": payload}
        )
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertIn("not_a_real_var", detail)
        self.assertIn("未知变量", detail)

    def test_import_endpoint_returns_400_for_unrecognised_file(self):
        response = self.client.post(
            "/api/v1/admin/prompt-profiles/import", headers=self.headers, json={"payload": {"junk": 1}}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("无法识别的提示词文件", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()

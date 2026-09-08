"""Administrator account, password and session security tests."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
import sqlite3

from r20_backend.admin_auth import AdminAuthStore, PLACEHOLDER_SETUP_TOKEN, resolve_bootstrap_tokens



class AdminAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AdminAuthStore(Path(self.temp.name) / "admin.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_connection_is_closed_after_success_or_failure(self):
        with self.store.connect() as connection:
            connection.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        with self.assertRaisesRegex(RuntimeError, "transaction interrupted"):
            with self.store.connect() as failed_connection:
                raise RuntimeError("transaction interrupted")
        with self.assertRaises(sqlite3.ProgrammingError):
            failed_connection.execute("SELECT 1")

    def test_legacy_initialization_and_login_session(self):
        self.assertTrue(self.store.initialize_from_legacy("LegacyToken123456"))
        self.assertFalse(self.store.initialize_from_legacy("OtherPassword123"))
        result = self.store.login("admin", "LegacyToken123456")
        user = self.store.validate_session(result["session_token"])
        self.assertEqual(user["role"], "superadmin")
        self.store.logout(result["session_token"])
        self.assertIsNone(self.store.validate_session(result["session_token"]))

    def test_password_is_not_stored_in_plaintext(self):
        self.store.create_user("alice", "StrongPassword123", "admin")
        self.assertNotIn(b"StrongPassword123", self.store.path.read_bytes())

    def test_disable_revokes_sessions_and_preserves_superadmin(self):
        self.store.create_user("rootadmin", "StrongPassword123", "superadmin")
        root = self.store.login("rootadmin", "StrongPassword123")
        child = self.store.create_user("operator", "OperatorPassword123", "admin")
        child_login = self.store.login("operator", "OperatorPassword123")
        self.store.set_enabled(child["id"], False, root["user"]["id"])
        self.assertIsNone(self.store.validate_session(child_login["session_token"]))
        with self.assertRaises(ValueError):
            self.store.set_enabled(root["user"]["id"], False, root["user"]["id"])

    def test_password_change_revokes_old_session(self):
        user = self.store.create_user("operator", "OperatorPassword123", "admin")
        login = self.store.login("operator", "OperatorPassword123")
        self.store.change_password(user["id"], "NewOperatorPassword456")
        self.assertIsNone(self.store.validate_session(login["session_token"]))
        self.assertIsNotNone(self.store.login("operator", "NewOperatorPassword456"))

    def test_failed_login_reports_remaining_attempts_and_unlocks(self):
        user = self.store.create_user("operator", "OperatorPassword123", "admin")
        for remaining in (4, 3, 2, 1):
            with self.assertRaisesRegex(PermissionError, f"还可尝试 {remaining} 次"):
                self.store.login("operator", "wrong-password")
        with self.assertRaisesRegex(PermissionError, "已锁定 15 分钟"):
            self.store.login("operator", "wrong-password")
        with self.assertRaisesRegex(PermissionError, "临时锁定"):
            self.store.login("operator", "OperatorPassword123")
        self.store.unlock_user(user["id"])
        self.assertEqual(self.store.login("operator", "OperatorPassword123")["user"]["username"], "operator")

    def test_resolve_both_missing_mints_distinct_setup_and_admin(self):
        seq = iter(["SetupTokenA1xxxx12", "AdminTokenB2xxxx12"])
        setup, admin, generated = resolve_bootstrap_tokens("", "", has_users=False, generate=lambda: next(seq))
        self.assertEqual(setup, "SetupTokenA1xxxx12")
        self.assertEqual(admin, "AdminTokenB2xxxx12")
        self.assertNotEqual(setup, admin)
        self.assertEqual(generated, {"R20_SETUP_TOKEN": setup, "R20_ADMIN_TOKEN": admin})

    def test_resolve_keeps_preset_setup_and_mints_admin(self):
        setup, admin, generated = resolve_bootstrap_tokens(
            "PresetSetupToken1", "", has_users=False, generate=lambda: "MintedAdminToken1"
        )
        self.assertEqual(setup, "PresetSetupToken1")
        self.assertEqual(admin, "MintedAdminToken1")
        self.assertEqual(generated, {"R20_ADMIN_TOKEN": admin})

    def test_resolve_keeps_preset_admin_and_mints_setup(self):
        setup, admin, generated = resolve_bootstrap_tokens(
            "", "PresetAdminToken1", has_users=False, generate=lambda: "MintedSetupToken1"
        )
        self.assertEqual(setup, "MintedSetupToken1")
        self.assertEqual(admin, "PresetAdminToken1")
        self.assertEqual(generated, {"R20_SETUP_TOKEN": setup})

    def test_placeholder_setup_token_is_treated_as_missing(self):
        setup, admin, generated = resolve_bootstrap_tokens(
            PLACEHOLDER_SETUP_TOKEN, "PresetAdminToken1", has_users=False, generate=lambda: "MintedSetupToken1"
        )
        self.assertEqual(setup, "MintedSetupToken1")
        self.assertEqual(admin, "PresetAdminToken1")
        self.assertNotIn(PLACEHOLDER_SETUP_TOKEN, generated.values())

    def test_existing_users_mint_admin_token_only(self):
        setup, admin, generated = resolve_bootstrap_tokens("", "", has_users=True, generate=lambda: "MintedAdminToken1")
        self.assertEqual(setup, "")
        self.assertEqual(admin, "MintedAdminToken1")
        self.assertEqual(generated, {"R20_ADMIN_TOKEN": admin})

    def test_existing_users_with_tokens_mint_nothing(self):
        setup, admin, generated = resolve_bootstrap_tokens(
            "ExistingSetup1", "ExistingAdmin1", has_users=True, generate=lambda: "SHOULD_NOT"
        )
        self.assertEqual((setup, admin, generated), ("ExistingSetup1", "ExistingAdmin1", {}))

    def test_generated_admin_token_is_not_the_login_password(self):
        setup, admin, generated = resolve_bootstrap_tokens(
            "", "", has_users=False, generate=iter(["SetupPassword123", "AdminApiToken123"]).__next__
        )
        self.assertEqual(generated["R20_SETUP_TOKEN"], "SetupPassword123")
        self.assertEqual(generated["R20_ADMIN_TOKEN"], "AdminApiToken123")
        self.assertTrue(self.store.initialize_from_legacy(setup))
        self.assertEqual(self.store.login("admin", "SetupPassword123")["user"]["username"], "admin")
        with self.assertRaises(PermissionError):
            self.store.login("admin", "AdminApiToken123")





    def test_invalid_password_policy(self):
        with self.assertRaises(ValueError):
            self.store.create_user("bob", "short", "admin")


if __name__ == "__main__":
    unittest.main()

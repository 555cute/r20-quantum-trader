"""Administrator API RBAC tests using an isolated auth database."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
import r20_backend.app as app_module
from r20_backend.admin_auth import AdminAuthStore


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = app_module.admin_auth
        app_module.admin_auth = AdminAuthStore(Path(self.temp.name) / "admin.db")
        app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        self.client = TestClient(app_module.app)

    def tearDown(self):
        app_module.admin_auth = self.original
        self.temp.cleanup()

    def login(self, username: str, password: str) -> dict[str, str]:
        response = self.client.post("/api/v1/admin/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return {"X-R20-Session": response.json()["session_token"]}

    def test_login_session_and_logout(self):
        headers = self.login("admin", "InitialAdmin123456")
        self.assertEqual(self.client.get("/api/v1/admin/auth/me", headers=headers).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/admin/auth/logout", headers=headers).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/admin/auth/me", headers=headers).status_code, 401)

    def test_superadmin_only_user_management(self):
        root = self.login("admin", "InitialAdmin123456")
        created = self.client.post("/api/v1/admin/users", headers=root, json={"username": "operator", "password": "OperatorPassword123", "role": "admin"})
        self.assertEqual(created.status_code, 200, created.text)
        operator = self.login("operator", "OperatorPassword123")
        self.assertEqual(self.client.get("/api/v1/admin/users", headers=operator).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/admin/about", headers=operator).status_code, 200)

    def test_health_and_about_report_651_release(self):
        health=self.client.get("/api/v1/health")
        self.assertEqual(health.status_code,200,health.text)
        self.assertEqual(health.json()["version"],"7.4.2")
        headers=self.login("admin","InitialAdmin123456")
        about=self.client.get("/api/v1/admin/about",headers=headers)
        self.assertEqual(about.status_code,200,about.text)
        self.assertEqual(about.json()["product"]["version"],"7.4.2")
        versions={item["name"]:item["version"] for item in about.json()["components"]}
        self.assertEqual(versions["FastAPI Control Plane"],"7.4.2")

    def test_legacy_header_disabled_after_initialization(self):
        response = self.client.get("/api/v1/admin/overview", headers={"X-R20-Admin-Token": "InitialAdmin123456"})
        self.assertEqual(response.status_code, 401)

    def test_vue_console_endpoints_require_session_and_return_data(self):
        headers = self.login("admin", "InitialAdmin123456")
        anonymous = {
            "/api/v1/admin/runtime": "get",
            "/api/v1/admin/logs?source=trader": "get",
            "/api/v1/admin/prompt-library": "get",
            "/api/v1/admin/agents": "get",
            "/api/v1/admin/plugins": "get",
            "/api/v1/admin/audit": "get",
            "/api/v1/admin/gateway": "get",
        }
        for path in anonymous:
            self.assertEqual(self.client.get(path).status_code, 401, path)
        runtime = self.client.get("/api/v1/admin/runtime", headers=headers)
        self.assertEqual(runtime.status_code, 200)
        self.assertIn("decisions", runtime.json())
        logs = self.client.get("/api/v1/admin/logs?source=backend&lines=30", headers=headers)
        self.assertEqual(logs.status_code, 200)
        self.assertEqual(logs.json()["file"], "r20_backend.log")
        self.assertEqual(self.client.get("/api/v1/admin/logs?source=../../etc/passwd", headers=headers).status_code, 400)
        library = self.client.get("/api/v1/admin/prompt-library", headers=headers)
        self.assertEqual(library.status_code, 200)
        self.assertEqual(set(library.json()["pipelines"]), {"trading_system", "trading_user", "evolution_system", "evolution_user"})
        plugins = self.client.get("/api/v1/admin/plugins", headers=headers)
        self.assertEqual(plugins.status_code, 200)
        self.assertEqual(plugins.json()["installation_policy"], "builtin-only")
        agents = self.client.get("/api/v1/admin/agents", headers=headers)
        self.assertEqual(agents.status_code, 200)
        self.assertIn("secret_store", agents.json())
        gateway = self.client.get("/api/v1/admin/gateway", headers=headers)
        self.assertEqual(gateway.status_code, 200)
        self.assertIn("scheduler", gateway.json())

    def test_initial_capital_update_requires_superadmin_and_confirmation(self):
        self.assertEqual(self.client.put("/api/v1/admin/account-baseline",json={"initial_capital":5000,"confirmation":"UPDATE CAPITAL"}).status_code,401)
        root=self.login("admin","InitialAdmin123456")
        from unittest.mock import patch
        current={"initial_capital":4061.04,"reset_time":"2026-08-31 06:57:38"}
        with patch.object(app_module,"load_account_baseline",return_value=current), patch.object(app_module,"update_initial_capital",return_value={"previous_initial_capital":4061.04,"initial_capital":5000.0,"reset_time":"2026-08-31 06:57:38","capital_updated_at":"2026-09-02 20:00:00"}) as update:
            wrong=self.client.put("/api/v1/admin/account-baseline",headers=root,json={"initial_capital":5000,"confirmation":"WRONG CONFIRM"})
            self.assertEqual(wrong.status_code,400)
            response=self.client.put("/api/v1/admin/account-baseline",headers=root,json={"initial_capital":5000,"confirmation":"UPDATE CAPITAL"})
        self.assertEqual(response.status_code,200,response.text)
        update.assert_called_once_with(5000.0)
        self.assertEqual(response.json()["reset_time"],"2026-08-31 06:57:38")
        self.assertIn("累计盈亏",response.json()["effect"])

    def test_config_exposes_initial_capital_without_secret(self):
        root=self.login("admin","InitialAdmin123456")
        from unittest.mock import patch
        with patch.object(app_module,"load_account_baseline",return_value={"initial_capital":4061.04,"reset_time":"2026-08-31 06:57:38"}):
            response=self.client.get("/api/v1/admin/config",headers=root)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["editable"]["initial_capital"],4061.04)
        self.assertEqual(response.json()["editable"]["initial_capital_reset_time"],"2026-08-31 06:57:38")

    def test_okx_oauth_device_flow_endpoints_are_session_protected(self):
        self.assertEqual(self.client.post("/api/v1/admin/okx/oauth/start",json={"site":"global"}).status_code,401)
        root=self.login("admin","InitialAdmin123456")
        from unittest.mock import patch
        pending={"status":"pending","site":"global","verification_uri":"https://www.okx.com/device","user_code":"ABCD-EFGH","expires_in":600}
        with patch.object(app_module,"start_oauth_device_login",return_value=pending):
            response=self.client.post("/api/v1/admin/okx/oauth/start",headers=root,json={"site":"global"})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["user_code"],"ABCD-EFGH")
        safe={"status":"logged_in","site":"global","scopes":["demo:read","demo:trade"],"account_label":""}
        with patch.object(app_module,"oauth_status",return_value=safe):
            status=self.client.get("/api/v1/admin/okx/oauth/status",headers=root)
        self.assertEqual(status.status_code,200,status.text)
        self.assertNotIn("token",status.text.lower())

    def test_okx_cli_check_and_install_require_valid_session_and_confirmation(self):
        self.assertEqual(self.client.get("/api/v1/admin/okx/cli-check").status_code, 401)
        self.assertEqual(self.client.post("/api/v1/admin/okx/install-cli", json={"confirmation":"INSTALL OKX CLI"}).status_code, 401)
        root = self.login("admin", "InitialAdmin123456")
        from unittest.mock import patch
        with patch.object(app_module, "check_node_npm", return_value={"ready":True,"node_installed":True,"node_path":"/usr/bin/node","node_version":"20","npm_installed":True,"npm_path":"/usr/bin/npm","npm_version":"10"}):
            checked=self.client.get("/api/v1/admin/okx/cli-check",headers=root)
        self.assertEqual(checked.status_code,200,checked.text)
        self.assertTrue(checked.json()["ready"])
        bad=self.client.post("/api/v1/admin/okx/install-cli",headers=root,json={"confirmation":"YES"})
        self.assertEqual(bad.status_code,422)
        wrong=self.client.post("/api/v1/admin/okx/install-cli",headers=root,json={"confirmation":"INSTALL SOMETHING"})
        self.assertEqual(wrong.status_code,400)
        installed={"ok":True,"detail":"OKX CLI 安装成功","path":"/usr/local/bin/okx","version":"1.4.5"}
        with patch.object(app_module,"install_okx_cli",return_value=installed):
            response=self.client.post("/api/v1/admin/okx/install-cli",headers=root,json={"confirmation":"INSTALL OKX CLI"})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["version"],"1.4.5")

    def test_okx_runtime_diagnostic_requires_session_and_never_returns_secrets(self):
        self.assertEqual(self.client.get("/api/v1/admin/okx/runtime").status_code, 401)
        headers = self.login("admin", "InitialAdmin123456")
        fake = {
            "selected_mode": "demo", "ready": True, "credential_source": "cli-oauth",
            "cli": {"installed": True, "path": "/usr/local/bin/okx", "version": "1.4.5", "supported": True},
            "oauth": {"status": "logged_in", "site": "global", "scopes": ["market:read", "demo:read", "demo:trade"], "ready_for_selected_mode": True},
            "api_key_profiles": [], "static_credentials_configured": False,
            "read_probe": {"ok": True, "detail": "OKX 私有只读探针通过"},
            "issues": [], "steps": [], "install_command": "npm install -g @okx_ai/okx-trade-cli@^1.4.4",
        }
        from unittest.mock import patch
        with patch.object(app_module, "diagnose_okx_runtime", return_value=fake):
            response = self.client.get("/api/v1/admin/okx/runtime", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        text = response.text.lower()
        self.assertNotIn("secret_key", text)
        self.assertNotIn("passphrase", text)
        self.assertEqual(response.json()["credential_source"], "cli-oauth")

    def test_interceptor_endpoints_and_sandbox_execution(self):
        self.assertEqual(self.client.get("/api/v1/admin/interceptors").status_code, 401)
        headers = self.login("admin", "InitialAdmin123456")
        res = self.client.get("/api/v1/admin/interceptors", headers=headers)
        self.assertEqual(res.status_code, 200)
        plugins = res.json()["plugins"]
        self.assertGreaterEqual(len(plugins), 4)
        names = [p["filename"] for p in plugins]
        self.assertIn("01_macro_trend_filter.py", names)
        self.assertIn("02_confidence_gatekeeper.py", names)

        # Test single detail
        detail = self.client.get("/api/v1/admin/interceptors/01_macro_trend_filter.py", headers=headers)
        self.assertEqual(detail.status_code, 200)
        self.assertIn("check_risk", detail.json()["code"])

        # Test sandbox test execution
        test_res = self.client.post("/api/v1/admin/interceptors/test", headers=headers, json={})
        self.assertEqual(test_res.status_code, 200)
        self.assertEqual(test_res.json()["status"], "success")
        self.assertGreaterEqual(len(test_res.json()["results"]), 4)

    def test_policy_admin_endpoints_rbac_and_exception_handling(self):
        root = self.login("admin", "InitialAdmin123456")
        self.client.post("/api/v1/admin/users", headers=root, json={"username": "operator_policy", "password": "OperatorPassword123", "role": "admin"})
        operator = self.login("operator_policy", "OperatorPassword123")

        # 1. Anonymous requests return 401
        self.assertEqual(self.client.get("/api/v1/admin/policy/current-snapshot").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/admin/policy/archives").status_code, 401)
        self.assertEqual(self.client.post("/api/v1/admin/policy/archive", json={"name": "test"}).status_code, 401)
        self.assertEqual(self.client.post("/api/v1/admin/policy/restore", json={"policy_hash": "abcdef12"}).status_code, 401)
        self.assertEqual(self.client.delete("/api/v1/admin/policy/archive/abcdef12").status_code, 401)

        # 2. Operator (admin role) can read snapshots and archives
        snap_resp = self.client.get("/api/v1/admin/policy/current-snapshot", headers=operator)
        self.assertEqual(snap_resp.status_code, 200)
        self.assertTrue(snap_resp.json()["ok"])
        self.assertIn("policy_hash", snap_resp.json())

        arch_resp = self.client.get("/api/v1/admin/policy/archives", headers=operator)
        self.assertEqual(arch_resp.status_code, 200)
        self.assertTrue(arch_resp.json()["ok"])

        # 3. Operator (admin role) is forbidden from archiving, restoring, deleting (403)
        self.assertEqual(self.client.post("/api/v1/admin/policy/archive", headers=operator, json={"name": "forbidden"}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/admin/policy/restore", headers=operator, json={"policy_hash": "abcdef12"}).status_code, 403)
        self.assertEqual(self.client.delete("/api/v1/admin/policy/archive/abcdef12", headers=operator).status_code, 403)

        # 4. Superadmin input validation and exception handling
        # 4a. Malformed archive payload (empty name or whitespace only) -> 422
        bad_name = self.client.post("/api/v1/admin/policy/archive", headers=root, json={"name": "   "})
        self.assertEqual(bad_name.status_code, 422)

        # 4b. Missing / invalid hash in restore -> 404 for missing hash, 422/400 for malformed
        bad_hash_restore = self.client.post("/api/v1/admin/policy/restore", headers=root, json={"policy_hash": "non_existent_hash_12345"})
        self.assertEqual(bad_hash_restore.status_code, 404)
        malformed_restore = self.client.post("/api/v1/admin/policy/restore", headers=root, json={"policy_hash": "../../etc/passwd"})
        self.assertEqual(malformed_restore.status_code, 422)

        # 4c. Missing / invalid hash in delete -> 404 for missing, 400 for malformed chars
        bad_del = self.client.delete("/api/v1/admin/policy/archive/non_existent_hash", headers=root)
        self.assertEqual(bad_del.status_code, 404)
        invalid_del = self.client.delete("/api/v1/admin/policy/archive/bad*hash!chars", headers=root)
        self.assertEqual(invalid_del.status_code, 400)

        # 4d. Successful archiving and deletion lifecycle by superadmin
        created = self.client.post("/api/v1/admin/policy/archive", headers=root, json={"name": "test_audit_archive", "description": "audit test"})
        self.assertEqual(created.status_code, 200, created.text)
        created_hash = created.json()["entry"]["policy_hash"]
        self.assertTrue(created_hash)

        # Check archive exists in list
        list_after = self.client.get("/api/v1/admin/policy/archives", headers=operator)
        self.assertEqual(list_after.status_code, 200)
        self.assertTrue(any(a["policy_hash"] == created_hash for a in list_after.json()["archives"]))

        # Delete archive
        deleted = self.client.delete(f"/api/v1/admin/policy/archive/{created_hash}", headers=root)
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()["ok"])

    def test_prompt_admin_endpoints_rbac_and_exception_handling(self):
        root = self.login("admin", "InitialAdmin123456")
        self.client.post("/api/v1/admin/users", headers=root, json={"username": "operator_prompt", "password": "OperatorPassword123", "role": "admin"})
        operator = self.login("operator_prompt", "OperatorPassword123")

        # 1. Anonymous access returns 401
        self.assertEqual(self.client.get("/api/v1/admin/prompt-library").status_code, 401)
        self.assertEqual(self.client.put("/api/v1/admin/prompt-library", json={"active_style": "stable"}).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/admin/prompt-profiles").status_code, 401)
        self.assertEqual(self.client.post("/api/v1/admin/prompt-profiles", json={"name": "test"}).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/admin/prompts").status_code, 401)
        self.assertEqual(self.client.put("/api/v1/admin/prompts", json={"content": "test"}).status_code, 401)

        # 2. Operator role checks: can read/validate, but CANNOT mutate prompt library or prompts override
        self.assertEqual(self.client.get("/api/v1/admin/prompt-library", headers=operator).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/admin/prompt-profiles", headers=operator).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/admin/prompts", headers=operator).status_code, 200)

        # Operator forbidden on PUT prompt-library and PUT prompts (403)
        self.assertEqual(self.client.put("/api/v1/admin/prompt-library", headers=operator, json={"active_style": "stable"}).status_code, 403)
        self.assertEqual(self.client.put("/api/v1/admin/prompts", headers=operator, json={"content": "test"}).status_code, 403)

        # 3. Superadmin can PUT prompt-library and prompts
        put_lib = self.client.put("/api/v1/admin/prompt-library", headers=root, json={"active_style": "stable", "trading_system": "", "trading_user": "", "evolution_system": "", "evolution_user": ""})
        self.assertEqual(put_lib.status_code, 200)

        put_prompts = self.client.put("/api/v1/admin/prompts", headers=root, json={"content": ""})
        self.assertEqual(put_prompts.status_code, 200)

        # 4. Exception handling on non-existent or malformed prompt profile operations (no unhandled 500)
        self.assertEqual(self.client.get("/api/v1/admin/prompt-profiles/non_existent_profile/export", headers=operator).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/admin/prompt-profiles/non_existent_profile/activate", headers=root, json={}).status_code, 404)
        self.assertEqual(self.client.delete("/api/v1/admin/prompt-profiles/non_existent_profile", headers=root).status_code, 404)
        self.assertEqual(self.client.put("/api/v1/admin/prompt-profiles/non_existent_profile", headers=root, json={"name": "new_name"}).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/admin/prompt-profiles/non_existent_profile/rollback", headers=root, json={"revision_id": "rev-123"}).status_code, 404)

        # Malformed profile_id chars -> 400
        self.assertEqual(self.client.get("/api/v1/admin/prompt-profiles/bad*profile!id/export", headers=operator).status_code, 400)
        self.assertEqual(self.client.delete("/api/v1/admin/prompt-profiles/bad*profile!id", headers=root).status_code, 400)

        # Malformed import payload -> 400
        bad_import = self.client.post("/api/v1/admin/prompt-profiles/import", headers=root, json={"payload": {"invalid": "data"}})
        self.assertEqual(bad_import.status_code, 400)

        # Whitespace-only profile name creation -> 422
        bad_create = self.client.post("/api/v1/admin/prompt-profiles", headers=root, json={"name": "   ", "source_id": "stable"})
        self.assertEqual(bad_create.status_code, 422)


if __name__ == "__main__":
    unittest.main()

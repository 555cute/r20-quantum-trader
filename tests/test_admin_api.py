"""Administrator API RBAC tests using an isolated auth database."""
from __future__ import annotations
import os
import tempfile

import unittest
from pathlib import Path

from fastapi.testclient import TestClient
import r20_backend.app as app_module
from r20_backend.admin_auth import AdminAuthStore
from r20_backend.version import __version__


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

    def test_health_and_about_report_dynamic_version(self):
        health=self.client.get("/api/v1/health")
        self.assertEqual(health.status_code,200,health.text)
        self.assertEqual(health.json()["version"], __version__)
        headers=self.login("admin","InitialAdmin123456")
        about=self.client.get("/api/v1/admin/about",headers=headers)
        self.assertEqual(about.status_code,200,about.text)
        self.assertEqual(about.json()["product"]["version"], __version__)
        versions={item["name"]:item["version"] for item in about.json()["components"]}
        self.assertEqual(versions["FastAPI Control Plane"], __version__)

    def test_admin_token_header_after_users_exist(self):
        original = app_module.settings.admin_token
        original_env = os.environ.get("R20_ADMIN_TOKEN")
        os.environ["R20_ADMIN_TOKEN"] = "ServiceAdminToken123"
        app_module.settings.admin_token = "ServiceAdminToken123"
        try:
            allowed = self.client.get("/api/v1/admin/overview", headers={"X-R20-Admin-Token": "ServiceAdminToken123"})
            self.assertEqual(allowed.status_code, 200, allowed.text)
            password = self.client.get("/api/v1/admin/overview", headers={"X-R20-Admin-Token": "InitialAdmin123456"})
            self.assertEqual(password.status_code, 403)
            me = self.client.get("/api/v1/admin/auth/me", headers={"X-R20-Admin-Token": "ServiceAdminToken123"})
            self.assertEqual(me.status_code, 401)
            users = self.client.get("/api/v1/admin/users", headers={"X-R20-Admin-Token": "ServiceAdminToken123"})
            self.assertEqual(users.status_code, 401)
        finally:
            app_module.settings.admin_token = original
            if original_env is None:
                os.environ.pop("R20_ADMIN_TOKEN", None)
            else:
                os.environ["R20_ADMIN_TOKEN"] = original_env


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

        # Test logout endpoint
        self.assertEqual(self.client.post("/api/v1/admin/okx/oauth/logout").status_code, 401)
        with patch.object(app_module, "oauth_logout", return_value={"status": "logged_out", "message": "OKX OAuth 账号已成功解绑"}):
            logout_resp = self.client.post("/api/v1/admin/okx/oauth/logout", headers=root)
        self.assertEqual(logout_resp.status_code, 200)
        self.assertEqual(logout_resp.json()["status"], "logged_out")

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

    def test_admin_update_endpoints_and_status(self):
        root = self.login("admin", "InitialAdmin123456")
        from unittest.mock import patch
        fake_status = {"branch": "main", "local": "abc1234", "remote": "abc1234", "behind": 0, "ahead": 0, "dirty": False}
        with patch.object(app_module, "update_status", return_value=fake_status):
            # 1. GET /api/v1/admin/update-status
            res1 = self.client.get("/api/v1/admin/update-status", headers=root)
            self.assertEqual(res1.status_code, 200)
            self.assertEqual(res1.json()["local"], "abc1234")

            # 2. POST /api/v1/admin/update/check
            res2 = self.client.post("/api/v1/admin/update/check", headers=root)
            self.assertEqual(res2.status_code, 200)
            self.assertEqual(res2.json()["remote"], "abc1234")

            # 3. POST /api/v1/admin/update without correct confirmation -> 400
            res_bad = self.client.post("/api/v1/admin/update", headers=root, json={"confirmation": "WRONG"})
            self.assertEqual(res_bad.status_code, 400)

            # 4. POST /api/v1/admin/update with correct confirmation
            with patch.object(app_module, "git", return_value="Already up to date."):
                res_ok = self.client.post("/api/v1/admin/update", headers=root, json={"confirmation": "UPDATE R20"})
                self.assertEqual(res_ok.status_code, 200)
                self.assertIn("git_output", res_ok.json())

    def test_backup_archive_download_header_and_query_token(self):
        # 1. Without auth -> 401
        self.assertEqual(self.client.get("/api/v1/admin/backups/download/nonexistent.tar.gz").status_code, 401)

        root = self.login("admin", "InitialAdmin123456")
        token = root["X-R20-Session"]

        # Create a dummy backup file in backups/local/
        backups_dir = app_module.ROOT / "backups" / "local"
        backups_dir.mkdir(parents=True, exist_ok=True)
        test_file = backups_dir / "test_download_archive.tar.gz"
        test_file.write_bytes(b"dummy-tar-gz-content")

        try:
            # 2. Download via Header -> 200
            res_hdr = self.client.get(f"/api/v1/admin/backups/download/{test_file.name}", headers=root)
            self.assertEqual(res_hdr.status_code, 200)
            self.assertEqual(res_hdr.content, b"dummy-tar-gz-content")
            self.assertIn('attachment; filename="test_download_archive.tar.gz"', res_hdr.headers.get("content-disposition", ""))
            self.assertEqual(res_hdr.headers.get("content-type"), "application/gzip")

            # 3. Download via Query parameter ?token=... (for native browser download link) -> 200
            res_token = self.client.get(f"/api/v1/admin/backups/download/{test_file.name}?token={token}")
            self.assertEqual(res_token.status_code, 200)
            self.assertEqual(res_token.content, b"dummy-tar-gz-content")

            # 4. Download via relative path "local/test_download_archive.tar.gz"
            res_rel = self.client.get(f"/api/v1/admin/backups/download/local/{test_file.name}?token={token}")
            self.assertEqual(res_rel.status_code, 200)
            self.assertEqual(res_rel.content, b"dummy-tar-gz-content")

            # 5. Non-existent file -> 404
            res_404 = self.client.get(f"/api/v1/admin/backups/download/does_not_exist_file.tar.gz?token={token}")
            self.assertEqual(res_404.status_code, 404)
        finally:
            if test_file.exists():
                test_file.unlink()

    def test_market_candles_endpoint(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        bad = self.client.get("/api/v1/market/BTC-USDT/candles")
        self.assertEqual(bad.status_code, 400)
        env = SimpleNamespace(exchange="binance", mode="demo", configured=False, simulated=True)
        rows = [["1", "1", "1", "1", "1", "1"]]
        adapter = Mock()
        adapter.candles.return_value = rows
        with patch.object(app_module, "selected_environment", return_value=env), patch.object(app_module, "get_exchange", return_value=adapter):
            app_module._CANDLES_CACHE.clear()
            resp = self.client.get("/api/v1/market/BTC-USDT-SWAP/candles?bar=1H&limit=10")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["instId"], "BTC-USDT-SWAP")
        self.assertEqual(data["bar"], "1H")
        self.assertIn("candles", data)
        self.assertGreater(len(data["candles"]), 0)
        c0 = data["candles"][0]
        for k in ("ts", "open", "high", "low", "close", "vol"):
            self.assertIn(k, c0)

    def test_exchange_runtime_is_local_unconfigured_and_never_leaks_secrets(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        self.assertEqual(self.client.get("/api/v1/admin/exchange/runtime").status_code, 401)
        headers = self.login("admin", "InitialAdmin123456")
        env = SimpleNamespace(exchange="binance", mode="demo", configured=False, simulated=True)
        with patch.object(app_module, "selected_environment", return_value=env), patch.object(app_module, "diagnose_okx_runtime", side_effect=AssertionError("must not diagnose")):
            response = self.client.get("/api/v1/admin/exchange/runtime", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["exchange"], "binance")
        self.assertEqual(payload["environment"], "demo")
        self.assertFalse(payload["configured"])
        self.assertEqual(payload["status"], "unconfigured")
        self.assertTrue(payload["notes"])
        text = response.text.lower()
        self.assertNotIn("secret", text)
        self.assertNotIn("api_key", text)
        self.assertNotIn("passphrase", text)

    def test_config_exposes_exchange_flags_without_plaintext_keys(self):
        headers = self.login("admin", "InitialAdmin123456")
        response = self.client.get("/api/v1/admin/config", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        editable = response.json()["editable"]
        self.assertIn("exchange", editable)
        self.assertIn("binance_environment", editable)
        self.assertIn("binance_demo_configured", editable)
        self.assertIn("binance_live_configured", editable)
        self.assertNotIn("binance_demo_api_key", editable)
        self.assertNotIn("binance_demo_secret_key", editable)
        self.assertNotIn("okx_api_key", editable)

    def test_instruments_list_exposes_binance_venue_symbol(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        headers = self.login("admin", "InitialAdmin123456")
        env = SimpleNamespace(exchange="binance", mode="demo", configured=False, simulated=True)
        pool = [{"instId": "BTC-USDT-SWAP", "name": "BTC"}]
        with patch.object(app_module, "selected_environment", return_value=env), \
             patch.object(app_module, "load_instruments", return_value=pool), \
             patch.object(app_module, "read_json", return_value={}):
            response = self.client.get("/api/v1/admin/instruments", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["exchange"], "binance")
        self.assertEqual(body["instruments"][0]["venue_symbol"], "BTCUSDT")

    def test_add_instrument_requires_canonical_swap_id(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        headers = self.login("admin", "InitialAdmin123456")
        env = SimpleNamespace(exchange="binance", mode="demo", configured=True, simulated=True)
        adapter = SimpleNamespace(instruments=lambda inst_id: [{
            "instId": "SOL-USDT-SWAP", "baseCcy": "SOL", "settleCcy": "USDT", "state": "live",
            "ctVal": "1", "nativeCtVal": "1", "lotSz": "0.01", "minSz": "0.01", "tickSz": "0.01",
            "minNotional": "5", "quantity_unit": "base",
        }] if inst_id == "SOL-USDT-SWAP" else [])
        saved = []

        with patch.object(app_module, "selected_environment", return_value=env), \
             patch.object(app_module, "get_exchange", return_value=adapter), \
             patch.object(app_module, "load_instruments", return_value=[{"instId": "BTC-USDT-SWAP", "name": "BTC"}]), \
             patch.object(app_module, "save_instruments", side_effect=lambda rows: saved.append(list(rows))), \
             patch.object(app_module, "audit_record"):
            ticker = self.client.post("/api/v1/admin/instruments", headers=headers, json={"inst_id": "SOLUSDT"})
            self.assertEqual(ticker.status_code, 422, ticker.text)
            invalid = self.client.post("/api/v1/admin/instruments", headers=headers, json={"inst_id": "BTC-USD-SWAP"})
            self.assertEqual(invalid.status_code, 422, invalid.text)
            response = self.client.post("/api/v1/admin/instruments", headers=headers, json={"inst_id": "SOL-USDT-SWAP"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["added"]["instId"], "SOL-USDT-SWAP")
        self.assertEqual(body["added"]["venue_symbol"], "SOLUSDT")
        persisted = saved[-1][-1]
        self.assertEqual(persisted["instId"], "SOL-USDT-SWAP")
        self.assertNotIn("venue_symbol", persisted)




    def test_put_binance_demo_requires_switch_phrase_and_skips_blank_secrets(self):
        from unittest.mock import patch
        headers = self.login("admin", "InitialAdmin123456")
        captured = {}
        def fake_save(values):
            captured.update(values)
        with patch.object(app_module, "save_secrets", side_effect=fake_save), patch.object(app_module, "update_env"), patch.object(app_module, "_acquire_trader_cycle_lock", return_value=None), patch.object(app_module, "_release_trader_cycle_lock"):
            missing = self.client.put("/api/v1/admin/config", headers=headers, json={"exchange": "binance", "binance_environment": "demo", "binance_demo_api_key": "demo-key", "binance_demo_secret_key": ""})
            self.assertEqual(missing.status_code, 400, missing.text)
            captured.clear()
            generic = self.client.put("/api/v1/admin/config", headers=headers, json={"exchange": "binance", "binance_environment": "demo", "binance_demo_api_key": "demo-key", "confirmation": "SWITCH EXCHANGE MODE"})
            self.assertEqual(generic.status_code, 400, generic.text)
            wrong = self.client.put("/api/v1/admin/config", headers=headers, json={"exchange": "binance", "binance_environment": "demo", "binance_demo_api_key": "demo-key", "confirmation": "SWITCH OKX LIVE"})
            self.assertEqual(wrong.status_code, 400, wrong.text)
            captured.clear()
            ok = self.client.put("/api/v1/admin/config", headers=headers, json={"exchange": "binance", "binance_environment": "demo", "binance_demo_api_key": "demo-key", "binance_demo_secret_key": "", "confirmation": "SWITCH BINANCE DEMO"})
        self.assertEqual(ok.status_code, 200, ok.text)
        body = ok.json()
        self.assertTrue(body.get("updated"))
        self.assertIn("binance_demo_configured", body)
        self.assertNotIn("demo-key", ok.text)
        self.assertNotIn("binance_demo_api_key", body)
        self.assertEqual(captured.get("BINANCE_DEMO_API_KEY"), "demo-key")
        self.assertNotIn("BINANCE_DEMO_SECRET_KEY", captured)

    def test_put_exchange_config_forbidden_for_operator(self):
        root = self.login("admin", "InitialAdmin123456")
        self.client.post("/api/v1/admin/users", headers=root, json={"username": "op_ex", "password": "OperatorPassword123", "role": "admin"})
        operator = self.login("op_ex", "OperatorPassword123")
        response = self.client.put("/api/v1/admin/config", headers=operator, json={"exchange": "binance", "binance_environment": "demo", "confirmation": "SWITCH BINANCE DEMO"})
        self.assertEqual(response.status_code, 403)

    def test_unconfigured_snapshot_does_not_call_trade_service(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        headers = self.login("admin", "InitialAdmin123456")
        env = SimpleNamespace(exchange="binance", mode="demo", configured=False, simulated=True)
        with patch.object(app_module, "selected_environment", return_value=env):
            response = self.client.get("/api/v1/admin/account-snapshot", headers=headers)
        self.assertEqual(response.status_code, 503)

    def test_trader_job_requires_superadmin_and_exact_run_phrase(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        root = self.login("admin", "InitialAdmin123456")
        self.client.post("/api/v1/admin/users", headers=root, json={"username": "op_job", "password": "OperatorPassword123", "role": "admin"})
        operator = self.login("op_job", "OperatorPassword123")
        env = SimpleNamespace(exchange="binance", mode="demo", configured=False, simulated=True)
        with patch.object(app_module, "selected_environment", return_value=env), patch("subprocess.run") as run:
            forbidden = self.client.post("/api/v1/admin/gateway/jobs/trader/run", headers=operator, json={"confirmation": "RUN BINANCE DEMO TRADER"})
            self.assertEqual(forbidden.status_code, 403)
            wrong = self.client.post("/api/v1/admin/gateway/jobs/trader/run", headers=root, json={"confirmation": "RUN JOB"})
            self.assertEqual(wrong.status_code, 400)
            mismatched = self.client.post("/api/v1/admin/gateway/jobs/trader/run", headers=root, json={"confirmation": "RUN OKX LIVE TRADER"})
            self.assertEqual(mismatched.status_code, 400)
            run.assert_not_called()

    def test_manual_trader_skip_is_recorded_as_failure(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from r20_gateway.store import GatewayStore
        root = self.login("admin", "InitialAdmin123456")
        env = SimpleNamespace(exchange="binance", mode="demo", configured=True, simulated=True)
        db = Path(self.temp.name) / "gw.db"
        completed = SimpleNamespace(
            returncode=0,
            stdout="[Trader] Skip: another portfolio cycle is still running\n",
            stderr="",
        )
        with patch.object(app_module, "selected_environment", return_value=env), \
             patch.object(app_module, "GATEWAY_DB_PATH", db), \
             patch("subprocess.run", return_value=completed):
            response = self.client.post(
                "/api/v1/admin/gateway/jobs/trader/run",
                headers=root,
                json={"confirmation": "RUN BINANCE DEMO TRADER"},
            )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("Skip", response.json()["detail"])
        runs = GatewayStore(db).job_runs(5)
        self.assertEqual(runs[0]["job_name"], "trader")
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("Skip", runs[0]["detail"])

    def test_manual_trader_abort_is_recorded_as_failure(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from r20_gateway.store import GatewayStore
        root = self.login("admin", "InitialAdmin123456")
        env = SimpleNamespace(exchange="binance", mode="demo", configured=True, simulated=True)
        db = Path(self.temp.name) / "gw-abort.db"
        completed = SimpleNamespace(
            returncode=0,
            stdout="[Trader] Abort: unable to verify/cancel stale open orders: uncertain:Timestamp for this request was 1000ms ahead of the server's time.\n",
            stderr="",
        )
        with patch.object(app_module, "selected_environment", return_value=env), \
             patch.object(app_module, "GATEWAY_DB_PATH", db), \
             patch("subprocess.run", return_value=completed):
            response = self.client.post(
                "/api/v1/admin/gateway/jobs/trader/run",
                headers=root,
                json={"confirmation": "RUN BINANCE DEMO TRADER"},
            )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("Abort", response.json()["detail"])
        runs = GatewayStore(db).job_runs(5)
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("Abort", runs[0]["detail"])





    def test_account_refresh_requires_session_and_uses_dashboard_bind(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        self.assertEqual(self.client.post("/api/v1/admin/account/refresh").status_code, 401)
        headers = self.login("admin", "InitialAdmin123456")
        env = SimpleNamespace(exchange="binance", mode="demo", configured=False, simulated=True)
        with patch("dashboard.app.refresh_account_snapshot", return_value={}) as refresh, patch.object(app_module, "selected_environment", return_value=env):
            response = self.client.post("/api/v1/admin/account/refresh", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["exchange"], "binance")
        self.assertFalse(payload["configured"])
        self.assertNotIn("secret", response.text.lower())
        refresh.assert_called_once()





if __name__ == "__main__":
    unittest.main()

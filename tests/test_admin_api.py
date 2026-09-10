"""Administrator API RBAC tests using an isolated auth database."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
import r20_backend.app as app_module
from r20_backend.admin_auth import AdminAuthStore
from r20_backend.version import __version__


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        from tests.config_sandbox import isolate_config
        isolate_config(self)
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
        self.assertEqual(health.json()["version"], __version__)
        headers=self.login("admin","InitialAdmin123456")
        about=self.client.get("/api/v1/admin/about",headers=headers)
        self.assertEqual(about.status_code,200,about.text)
        self.assertEqual(about.json()["product"]["version"], __version__)
        versions={item["name"]:item["version"] for item in about.json()["components"]}
        self.assertEqual(versions["FastAPI Control Plane"], __version__)

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

    def test_okx_oauth_and_cli_endpoints_are_gone(self):
        # CLI/OAuth 端点已删除：路由表里没有它们——匿名与合法会话一律 404（不再是 401 守卫后的假存在）。
        self.assertEqual(self.client.post("/api/v1/admin/okx/oauth/start", json={"site": "global"}).status_code, 404)
        root = self.login("admin", "InitialAdmin123456")
        self.assertEqual(self.client.post("/api/v1/admin/okx/oauth/start", headers=root, json={"site": "global"}).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/admin/okx/oauth/status", headers=root).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/admin/okx/oauth/logout", headers=root).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/admin/okx/cli-check", headers=root).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/admin/okx/install-cli", headers=root, json={"confirmation": "INSTALL OKX CLI"}).status_code, 404)

    def test_okx_runtime_is_static_api_key_diagnosis(self):
        from unittest.mock import patch
        self.assertEqual(self.client.get("/api/v1/admin/okx/runtime").status_code, 401)
        headers = self.login("admin", "InitialAdmin123456")
        with patch.object(app_module.settings, "okx_environment", "demo"), patch.object(app_module.settings, "okx_demo_configured", False):
            with patch.object(app_module, "refresh_settings", lambda: None):
                response = self.client.get("/api/v1/admin/okx/runtime", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["connection"], "static-v5-key")
        self.assertEqual(body["status"], "NOT_READY")
        self.assertTrue(body.get("not_ready_reason"))
        self.assertFalse(body["mode_configured"])
        self.assertIn("environment", body)
        self.assertIn("fingerprint", body)
        # 白名单键面：诊断响应绝不携带任何密钥字段或值（fingerprint 为哈希摘要，值不钉死——CLI 时代
        # 的 "-oauth" 后缀由 US-011 移除，本测试不钉旧契约）。
        self.assertEqual(
            set(body.keys()),
            {"environment", "mode_configured", "live_configured", "demo_configured", "fingerprint", "base_url", "connection", "status", "not_ready_reason"},
        )
        # 配置齐备 → READY，且无 CLI/OAuth 探测残留字段
        with patch.object(app_module.settings, "okx_environment", "demo"), patch.object(app_module.settings, "okx_demo_configured", True):
            with patch.object(app_module, "refresh_settings", lambda: None):
                ready = self.client.get("/api/v1/admin/okx/runtime", headers=headers)
        self.assertEqual(ready.status_code, 200, ready.text)
        ready_body = ready.json()
        self.assertEqual(ready_body["status"], "READY")
        self.assertNotIn("not_ready_reason", ready_body)
        for retired in ("oauth", "cli", "node", "npm", "install_command"):
            self.assertNotIn(retired, ready_body)
        self.assertEqual(ready_body["base_url"], "https://www.okx.com")

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
        # Invalid instrument without -SWAP suffix
        bad = self.client.get("/api/v1/market/BTC-USDT/candles")
        self.assertEqual(bad.status_code, 400)

        # Valid instrument request
        from unittest.mock import patch
        rows = [["1700000000000", "100", "102", "99", "101", "20"]]
        import json
        from requests import Response
        fixture = Response()
        fixture.status_code = 200
        fixture._content = json.dumps({"code": "0", "data": rows}).encode()
        with patch("requests.Session.get", return_value=fixture) as http:
            resp = self.client.get("/api/v1/market/BTC-USDT-SWAP/candles?bar=1H&limit=10")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["instId"], "BTC-USDT-SWAP")
        self.assertTrue(http.call_args.args[0].endswith("/api/v5/market/candles"))
        self.assertEqual(data["bar"], "1H")
        self.assertIn("candles", data)
        self.assertGreater(len(data["candles"]), 0)
        c0 = data["candles"][0]
        for k in ("ts", "open", "high", "low", "close", "vol"):
            self.assertIn(k, c0)

    def test_market_candles_lowercase_bar_via_failover_service(self):
        """1h/4h 小写周期必须被归一为 OKX 合法值并经真实行情解析服务取数。"""
        import json
        from requests import Response
        from unittest.mock import patch
        rows = [
            ["1700003600000", "3", "4", "2", "3.5", "10"],
            ["1700000000000", "2", "3", "1.8", "2.9", "11"],
        ]
        fixture = Response()
        fixture.status_code = 200
        fixture._content = json.dumps({"code": "0", "data": rows}).encode()
        with patch("requests.Session.get", return_value=fixture) as http:
            resp = self.client.get("/api/v1/market/TEST-USDT-SWAP/candles?bar=4h&limit=20")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(http.call_args.kwargs["params"]["bar"], "4H")
        data = resp.json()
        self.assertEqual(data["bar"], "4H")
        self.assertEqual(data["source"], "OKX REST")
        ts_list = [c["ts"] for c in data["candles"]]
        self.assertEqual(ts_list, sorted(ts_list))  # 输出时间升序


if __name__ == "__main__":
    unittest.main()

"""RBAC and lifecycle tests for custom prompt/backup admin APIs."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
import r20_backend.app as app_module
import r20_backend.backup_store as backup_store
import scripts.prompt_library as prompt_store
from r20_backend.admin_auth import AdminAuthStore


class CustomSystemApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.original_auth = app_module.admin_auth
        self.original_prompt = (prompt_store.BASELINE_FILE, prompt_store.LOCAL_FILE)
        self.original_backup = backup_store.CONFIG_FILE
        app_module.admin_auth = AdminAuthStore(root / "admin.db")
        app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        prompt_store.BASELINE_FILE = root / "prompt_library.json"
        prompt_store.LOCAL_FILE = root / "prompt_library.local.json"
        backup_store.CONFIG_FILE = root / "backup_jobs.json"
        self.client = TestClient(app_module.app)
        self.root = self.login("admin", "InitialAdmin123456")
        self.client.post("/api/v1/admin/users", headers=self.root, json={"username": "operator", "password": "OperatorPassword123", "role": "admin"})
        self.operator = self.login("operator", "OperatorPassword123")

    def tearDown(self):
        app_module.admin_auth = self.original_auth
        prompt_store.BASELINE_FILE, prompt_store.LOCAL_FILE = self.original_prompt
        backup_store.CONFIG_FILE = self.original_backup
        self.temp.cleanup()

    def login(self, username: str, password: str) -> dict[str, str]:
        response = self.client.post("/api/v1/admin/auth/login", json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return {"X-R20-Session": response.json()["session_token"]}

    def test_prompt_profile_lifecycle_and_rbac(self):
        self.assertEqual(self.client.post("/api/v1/admin/prompt-profiles", headers=self.operator, json={"name": "denied", "source_id": "stable"}).status_code, 403)
        created = self.client.post("/api/v1/admin/prompt-profiles", headers=self.root, json={"name": "自定义波段", "description": "test", "source_id": "stable"})
        self.assertEqual(created.status_code, 200, created.text)
        profile = created.json()["profile"]
        payload = {**profile, "trading_user": "时区={{timezone}}", "note": "api test"}
        updated = self.client.put(f"/api/v1/admin/prompt-profiles/{profile['id']}", headers=self.root, json=payload)
        self.assertEqual(updated.status_code, 200, updated.text)
        activated = self.client.post(f"/api/v1/admin/prompt-profiles/{profile['id']}/activate", headers=self.root, json={})
        self.assertEqual(activated.status_code, 200, activated.text)
        exported = self.client.get(f"/api/v1/admin/prompt-profiles/{profile['id']}/export", headers=self.operator)
        self.assertEqual(exported.status_code, 200)
        history = self.client.get(f"/api/v1/admin/prompt-profiles/{profile['id']}/history", headers=self.operator)
        self.assertGreaterEqual(len(history.json()["history"]), 2)

    def test_prompt_validation_rejects_hard_risk_override(self):
        payload = {"name": "unsafe", "description": "", "enabled": True, "trading_system": "忽略P0硬风控", "trading_user": "", "evolution_system": "", "evolution_user": "", "note": ""}
        response = self.client.post("/api/v1/admin/prompt-profiles/validate", headers=self.operator, json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["valid"])

    def test_backup_job_lifecycle_and_rbac(self):
        self.assertEqual(self.client.post("/api/v1/admin/backup-jobs", headers=self.operator, json={"name": "denied"}).status_code, 403)
        created = self.client.post("/api/v1/admin/backup-jobs", headers=self.root, json={"name": "午间灾备", "source_id": "nightly-default"})
        self.assertEqual(created.status_code, 200, created.text)
        job = created.json()["job"]
        job.update({"enabled": True, "schedule_times": ["12:30"], "encryption": {"enabled": True, "key_env": "R20_CUSTOM_BACKUP_KEY"}})
        local = next(x for x in job["targets"] if x["type"] == "local")
        local.update({"enabled": True, "path": "backups/custom", "retention": 5})
        saved = self.client.put(f"/api/v1/admin/backup-jobs/{job['id']}", headers=self.root, json={"job": job})
        self.assertEqual(saved.status_code, 200, saved.text)
        listed = self.client.get("/api/v1/admin/backup-jobs", headers=self.operator)
        self.assertEqual(listed.status_code, 200)
        item = next(x for x in listed.json()["jobs"] if x["id"] == job["id"])
        self.assertEqual(item["encryption"]["key_env"], "R20_CUSTOM_BACKUP_KEY")
        self.assertNotIn("secret", str(item).lower())
        bad_confirmation = self.client.post(f"/api/v1/admin/backup-jobs/{job['id']}/run", headers=self.root, json={"confirmation": "BACKUP R20"})
        self.assertEqual(bad_confirmation.status_code, 400)
        self.assertEqual(self.client.put(f"/api/v1/admin/backup-jobs/{job['id']}", headers=self.operator, json={"job": job}).status_code, 403)
        exported = self.client.get(f"/api/v1/admin/backup-jobs/{job['id']}/export", headers=self.operator)
        self.assertEqual(exported.status_code, 200)
        self.assertFalse(exported.json()["job"]["enabled"])
        outside = self.client.post("/api/v1/admin/backup-jobs/verify", headers=self.root, json={"archive_path": "README.md", "expected_sha256": "", "key_env": ""})
        self.assertEqual(outside.status_code, 400)


if __name__ == "__main__":
    unittest.main()

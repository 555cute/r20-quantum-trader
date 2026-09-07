"""Comprehensive health check and boundary tests for R20 backup system."""
from __future__ import annotations
import io
import json
import os
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import r20_backend.app as app_module
from r20_backend.admin_auth import AdminAuthStore
import r20_backend.backup_store as store
import scripts.backup_runtime as runtime
from scripts.nightly_backup_and_clean import main as nightly_main


class BackupMethodTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_admin_auth = app_module.admin_auth
        self.original_config_file = store.CONFIG_FILE
        self.original_root = runtime.ROOT
        self.original_app_root = app_module.ROOT
        self.original_backups = runtime.BACKUPS
        self.original_local = runtime.LOCAL_DIR
        self.original_sqlite = runtime.SQLITE_DIR
        self.original_manifests = runtime.MANIFEST_DIR

        # Setup isolated environment
        app_module.admin_auth = AdminAuthStore(self.root / "admin.db")
        app_module.admin_auth.initialize_from_legacy("InitialAdmin123456")
        self.client = TestClient(app_module.app)

        app_module.ROOT = self.root
        store.CONFIG_FILE = self.root / "data" / "backup_methods.json"
        runtime.ROOT = self.root
        runtime.BACKUPS = self.root / "backups"
        runtime.LOCAL_DIR = self.root / "backups" / "local"
        runtime.SQLITE_DIR = self.root / "backups" / "sqlite"
        runtime.MANIFEST_DIR = self.root / "backups" / "manifests"

        # Create dummy data files
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        (self.root / "scripts").mkdir(parents=True, exist_ok=True)
        (self.root / "data" / "test.json").write_text('{"key": "val"}', encoding="utf-8")
        (self.root / "scripts" / "test.py").write_text('print("ok")', encoding="utf-8")

    def tearDown(self):
        app_module.admin_auth = self.original_admin_auth
        app_module.ROOT = self.original_app_root
        store.CONFIG_FILE = self.original_config_file
        runtime.ROOT = self.original_root
        runtime.BACKUPS = self.original_backups
        runtime.LOCAL_DIR = self.original_local
        runtime.SQLITE_DIR = self.original_sqlite
        runtime.MANIFEST_DIR = self.original_manifests
        self.temp_dir.cleanup()

    def login(self) -> dict[str, str]:
        resp = self.client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "InitialAdmin123456"})
        self.assertEqual(resp.status_code, 200, resp.text)
        return {"X-R20-Session": resp.json()["session_token"]}

    def test_open_source_defaults_keep_local_enabled_and_cloud_opt_in(self):
        methods = store.load_backup_methods()
        self.assertFalse(methods["baidu"]["enabled"])
        self.assertTrue(methods["local"]["enabled"])
        self.assertFalse(methods["sqlite"]["enabled"])

    def test_sqlite_hot_backup_is_consistent(self):
        data = self.root / "data"
        source = data / "sample.db"
        connection = sqlite3.connect(source)
        connection.execute("CREATE TABLE x(value TEXT)")
        connection.execute("INSERT INTO x VALUES ('ok')")
        connection.commit()
        connection.close()

        created = runtime.sqlite_hot_backups("20260901_020000", 2)
        self.assertTrue(len(created) >= 1)
        check = sqlite3.connect(created[0])
        self.assertEqual(check.execute("SELECT value FROM x").fetchone()[0], "ok")
        check.close()

    def test_scoped_account_databases_keep_distinct_hot_backup_paths(self):
        expected = {"exchanges/okx/demo/account-a": "okx", "exchanges/binance/demo/account-b": "binance"}
        connections = []
        try:
            for scope, marker in expected.items():
                source = self.root / "data" / scope / "r20_quant.db"
                source.parent.mkdir(parents=True)
                connection = sqlite3.connect(source)
                connections.append(connection)
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE marker(value TEXT)")
                connection.execute("INSERT INTO marker VALUES (?)", (marker,))
                connection.commit()
            created = runtime.sqlite_hot_backups("scoped", 2)
            recovered = {}
            for path in created:
                check = sqlite3.connect(path)
                try:
                    recovered[path.parent.relative_to(runtime.SQLITE_DIR).as_posix()] = check.execute("SELECT value FROM marker").fetchone()[0]
                finally:
                    check.close()
            self.assertEqual(recovered, expected)
        finally:
            for connection in connections:
                connection.close()

    def test_simple_backup_test_local_connectivity(self):
        headers = self.login()
        resp = self.client.post(
            "/api/v1/admin/backups/simple/test",
            json={"destination": "local", "enabled": True, "schedule_time": "02:00", "retention": 3},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["status"], "ready")
        self.assertIn("本地目录可写", data["detail"])

    def test_simple_backup_test_cloud_blank_credentials_returns_400(self):
        headers = self.login()
        # S3 missing credentials
        resp_s3 = self.client.post(
            "/api/v1/admin/backups/simple/test",
            json={
                "destination": "s3",
                "enabled": True,
                "schedule_time": "02:00",
                "endpoint": "https://s3.amazonaws.com",
                "bucket": "my-bucket",
                "credentials": {"access_key_id": "", "secret_access_key": "   "},
            },
            headers=headers,
        )
        self.assertEqual(resp_s3.status_code, 400, resp_s3.text)
        self.assertIn("连接信息不完整", resp_s3.json()["detail"])
        self.assertIn("access_key_id", resp_s3.json()["detail"])

        # OSS missing credentials
        resp_oss = self.client.post(
            "/api/v1/admin/backups/simple/test",
            json={
                "destination": "oss",
                "enabled": True,
                "schedule_time": "02:00",
                "endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
                "bucket": "my-bucket",
                "credentials": {},
            },
            headers=headers,
        )
        self.assertEqual(resp_oss.status_code, 400, resp_oss.text)
        self.assertIn("连接信息不完整", resp_oss.json()["detail"])

        # Baidu OAuth missing credentials
        resp_baidu = self.client.post(
            "/api/v1/admin/backups/simple/test",
            json={
                "destination": "baidu_oauth",
                "enabled": True,
                "schedule_time": "02:00",
                "credentials": {"app_key": "k", "app_secret": ""},
            },
            headers=headers,
        )
        self.assertEqual(resp_baidu.status_code, 400, resp_baidu.text)
        self.assertIn("连接信息不完整", resp_baidu.json()["detail"])

    def test_simple_backup_test_missing_endpoint_or_bucket(self):
        headers = self.login()
        # S3 missing endpoint
        resp = self.client.post(
            "/api/v1/admin/backups/simple/test",
            json={
                "destination": "s3",
                "enabled": True,
                "schedule_time": "02:00",
                "endpoint": "",
                "bucket": "my-bucket",
                "credentials": {"access_key_id": "key", "secret_access_key": "sec"},
            },
            headers=headers,
        )
        self.assertEqual(resp.status_code, 400, resp.text)

        # S3 missing bucket
        with patch("r20_backend.net_security.validate_outbound_url") as outbound_probe:
            resp_bucket = self.client.post(
                "/api/v1/admin/backups/simple/test",
                json={
                    "destination": "s3",
                    "enabled": True,
                    "schedule_time": "02:00",
                    "endpoint": "https://s3.amazonaws.com",
                    "bucket": "",
                    "credentials": {"access_key_id": "key", "secret_access_key": "sec"},
                },
                headers=headers,
            )
        self.assertEqual(resp_bucket.status_code, 400, resp_bucket.text)
        outbound_probe.assert_not_called()

    def test_safe_archive_verification_boundaries(self):
        # 1. Non-existent archive
        with self.assertRaises(RuntimeError) as ctx:
            runtime.verify_archive(self.root / "nonexistent.tar.gz")
        self.assertIn("归档文件不存在", str(ctx.exception))

        # 2. Corrupted / non-gzip archive
        corrupt_file = self.root / "corrupt.tar.gz"
        corrupt_file.write_bytes(b"garbage not a tar.gz archive")
        with self.assertRaises(RuntimeError) as ctx:
            runtime.verify_archive(corrupt_file)
        self.assertIn("损坏", str(ctx.exception))

        # 3. Create a safe valid archive
        valid_tar = self.root / "valid.tar.gz"
        with tarfile.open(valid_tar, "w:gz") as tar:
            info = tarfile.TarInfo("data/hello.txt")
            content = b"hello backup world"
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

        # Verify valid archive
        res = runtime.verify_archive(valid_tar)
        self.assertTrue(res["valid"])
        self.assertEqual(res["members"], 1)

        # Checksum check
        checksum = runtime.calculate_sha256(valid_tar)
        res_with_sha = runtime.verify_archive(valid_tar, expected_sha256=checksum)
        self.assertTrue(res_with_sha["valid"])

        with self.assertRaises(RuntimeError):
            runtime.verify_archive(valid_tar, expected_sha256="0000000000000000000000000000000000000000000000000000000000000000")

        # 4. Path traversal archive (Zip Slip / Tar Slip)
        traversal_tar = self.root / "traversal.tar.gz"
        with tarfile.open(traversal_tar, "w:gz") as tar:
            info = tarfile.TarInfo("../../etc/passwd")
            content = b"root:x:0:0:..."
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

        with self.assertRaises(RuntimeError) as ctx:
            runtime.verify_archive(traversal_tar)
        self.assertIn("不安全路径", str(ctx.exception))

        # 5. Absolute path archive
        abs_tar = self.root / "absolute.tar.gz"
        with tarfile.open(abs_tar, "w:gz") as tar:
            info = tarfile.TarInfo("/tmp/pwn")
            content = b"pwn"
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

        with self.assertRaises(RuntimeError) as ctx:
            runtime.verify_archive(abs_tar)
        self.assertIn("不安全路径", str(ctx.exception))

    def test_restore_boundary_cases_and_traversal_guards(self):
        headers = self.login()
        backups_local = self.root / "backups" / "local"
        backups_local.mkdir(parents=True, exist_ok=True)

        # 1. Invalid confirmation phrase
        resp = self.client.post(
            "/api/v1/admin/backups/restore",
            json={"archive_name": "test.tar.gz", "confirmation": "WRONG PHRASE"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("RESTORE R20", resp.json()["detail"])

        # 2. Non-existent file
        resp = self.client.post(
            "/api/v1/admin/backups/restore",
            json={"archive_name": "ghost.tar.gz", "confirmation": "RESTORE R20"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 404)

        # 3. Path traversal in archive_name
        resp = self.client.post(
            "/api/v1/admin/backups/restore",
            json={"archive_name": "../../etc/passwd", "confirmation": "RESTORE R20"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("非法归档文件名", resp.json()["detail"])

        # 4. Corrupted archive file
        corrupt_tar = backups_local / "corrupted_archive.tar.gz"
        corrupt_tar.write_bytes(b"not valid gz tar")
        resp = self.client.post(
            "/api/v1/admin/backups/restore",
            json={"archive_name": "corrupted_archive.tar.gz", "confirmation": "RESTORE R20"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("损坏", resp.json()["detail"])

        # 5. Archive containing directory traversal (Tar Slip guard)
        malicious_tar = backups_local / "slip.tar.gz"
        with tarfile.open(malicious_tar, "w:gz") as tar:
            info = tarfile.TarInfo("../../../escaped.txt")
            content = b"escape!"
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

        resp = self.client.post(
            "/api/v1/admin/backups/restore",
            json={"archive_name": "slip.tar.gz", "confirmation": "RESTORE R20"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不安全", resp.json()["detail"])

        # 6. Valid safe archive restore
        safe_tar = backups_local / "safe_restore.tar.gz"
        with tarfile.open(safe_tar, "w:gz") as tar:
            info = tarfile.TarInfo("data/restored_sample.txt")
            content = b"restored content success"
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

        resp = self.client.post(
            "/api/v1/admin/backups/restore",
            json={"archive_name": "safe_restore.tar.gz", "confirmation": "RESTORE R20"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertTrue(data["restored"])
        self.assertEqual(data["restored_count"], 1)
        self.assertTrue((self.root / "data" / "restored_sample.txt").exists())
        self.assertEqual((self.root / "data" / "restored_sample.txt").read_text(encoding="utf-8"), "restored content success")

        # 7. Encrypted archive restore
        enc_plain = backups_local / "encrypted_archive.tar.gz"
        with tarfile.open(enc_plain, "w:gz") as tar:
            info = tarfile.TarInfo("data/enc_restored.txt")
            content = b"encrypted restored content"
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

        key = "SuperSecretEncryptionKey123!"
        with patch.dict(os.environ, {"R20_TEST_ENC_KEY": key}):
            enc_file = runtime.encrypt_archive(enc_plain, "R20_TEST_ENC_KEY")
            self.assertTrue(enc_file.name.endswith(".aes256"))

            # Without key_env: returns 400
            resp_no_key = self.client.post(
                "/api/v1/admin/backups/restore",
                json={"archive_name": enc_file.name, "confirmation": "RESTORE R20", "key_env": "NON_EXISTENT_KEY_VAR"},
                headers=headers,
            )
            self.assertEqual(resp_no_key.status_code, 400)

            # With correct key_env: restores successfully
            resp_enc = self.client.post(
                "/api/v1/admin/backups/restore",
                json={"archive_name": enc_file.name, "confirmation": "RESTORE R20", "key_env": "R20_TEST_ENC_KEY"},
                headers=headers,
            )
            self.assertEqual(resp_enc.status_code, 200, resp_enc.text)
            self.assertTrue((self.root / "data" / "enc_restored.txt").exists())
            self.assertEqual((self.root / "data" / "enc_restored.txt").read_text(encoding="utf-8"), "encrypted restored content")

    def test_backup_job_execution_manifests_and_sqlite(self):
        # Create a sample database to verify sqlite hot backup
        db_path = self.root / "data" / "r20_quant.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE orders (id INT, symbol TEXT)")
        conn.execute("INSERT INTO orders VALUES (1, 'BTC-USDT')")
        conn.commit()
        conn.close()

        job = {
            "id": "test-job-01",
            "name": "测试灾备任务",
            "enabled": True,
            "scope": ["data", "scripts"],
            "compression_level": 5,
            "sqlite": {"enabled": True, "retention": 3},
            "targets": [
                {"id": "loc-1", "type": "local", "enabled": True, "path": "backups/local", "retention": 3}
            ],
            "cleanup_local_on_success": True,
        }

        result = runtime.run_backup_job(job)
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["sha256"])
        self.assertEqual(len(result["targets"]), 1)
        self.assertTrue(result["targets"][0]["success"])
        self.assertEqual(len(result["sqlite"]), 1)

        # Check manifest file
        manifest_path = self.root / result["manifest"]
        self.assertTrue(manifest_path.exists())
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest_data["job_id"], "test-job-01")
        self.assertEqual(manifest_data["status"], "success")
        self.assertEqual(manifest_data["sha256"], result["sha256"])
        self.assertTrue(len(manifest_data["targets"]) >= 1)

        # Check retained local archive
        local_dir = self.root / "backups" / "local"
        archives = list(local_dir.glob("r20_backup_*"))
        self.assertTrue(len(archives) >= 1)
        verify = runtime.verify_archive(archives[0], expected_sha256=result["sha256"])
        self.assertTrue(verify["valid"])

        # Check sqlite hot backup snapshot
        sqlite_dir = self.root / "backups" / "sqlite" / "test-job-01"
        snapshots = list(sqlite_dir.glob("*.db"))
        self.assertTrue(len(snapshots) >= 1)
        sconn = sqlite3.connect(snapshots[0])
        self.assertEqual(sconn.execute("SELECT symbol FROM orders").fetchone()[0], "BTC-USDT")
        sconn.close()

    def test_local_backup_reports_paths_when_root_is_windows_short_name(self):
        short_root = self.root
        if os.name == "nt":
            import ctypes
            buf = ctypes.create_unicode_buffer(32768)
            length = ctypes.windll.kernel32.GetShortPathNameW(str(self.root), buf, 32768)
            self.assertGreater(length, 0)
            short_root = Path(buf.value)
        runtime.ROOT = short_root
        runtime.BACKUPS = short_root / "backups"
        runtime.LOCAL_DIR = short_root / "backups" / "local"
        runtime.SQLITE_DIR = short_root / "backups" / "sqlite"
        runtime.MANIFEST_DIR = short_root / "backups" / "manifests"
        job = {
            "id": "short-root-job",
            "name": "short-root",
            "enabled": True,
            "scope": ["data"],
            "sqlite": {"enabled": True, "retention": 1},
            "targets": [
                {"id": "loc-short", "type": "local", "enabled": True, "path": "backups/local", "retention": 1}
            ],
        }
        result = runtime.run_backup_job(job)
        self.assertEqual(result["status"], "success", result)
        self.assertTrue(result["targets"][0]["success"], result["targets"])
        resolved_root = Path(os.path.realpath(self.root))
        self.assertTrue((resolved_root / result["manifest"]).is_file())
        self.assertTrue((resolved_root / result["targets"][0]["destination"]).exists())
        self.assertTrue(all((resolved_root / item).exists() for item in result["sqlite"]))


    def test_nightly_backup_cli_clean_and_missing_directories(self):
        # 1. Non-existent job-id returns exit code 2 and structured json
        with patch("sys.argv", ["nightly_backup_and_clean.py", "--job-id", "non-existent-id"]):
            code = nightly_main()
            self.assertEqual(code, 2)

        # 2. When staging has 0-byte or old file, clean_stale_staging removes it
        staging = self.root / "backups" / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        stale_empty = staging / "r20_backup_stale.tar.gz"
        stale_empty.write_bytes(b"")
        cleaned = runtime.clean_stale_staging(max_age_seconds=0)
        self.assertTrue(cleaned >= 1)
        self.assertFalse(stale_empty.exists())

    def test_backup_store_job_validation_boundaries(self):
        # 1. Path escape outside backups
        invalid_job = {
            "id": "bad-path",
            "name": "逃逸任务",
            "scope": ["data"],
            "targets": [
                {"type": "local", "path": "/etc/shadow", "retention": 3, "enabled": True}
            ]
        }
        val = store.validate_backup_job(invalid_job)
        self.assertFalse(val["valid"])
        self.assertTrue(any("backups" in e for e in val["errors"]))

        # 2. Invalid retention < 1
        invalid_retention_job = {
            "id": "bad-ret",
            "name": "保留无效",
            "scope": ["data"],
            "targets": [
                {"type": "local", "path": "backups/local", "retention": 0, "enabled": True}
            ]
        }
        val_ret = store.validate_backup_job(invalid_retention_job)
        self.assertFalse(val_ret["valid"])
        self.assertTrue(any("至少保留 1 份" in e for e in val_ret["errors"]))

        # 3. Duplicate job IDs in save_backup_config
        dup_payload = {
            "jobs": [
                {"id": "job-1", "name": "任务1", "scope": ["data"], "targets": [{"type": "local", "enabled": True, "retention": 3}]},
                {"id": "job-1", "name": "任务2", "scope": ["data"], "targets": [{"type": "local", "enabled": True, "retention": 3}]},
            ]
        }
        with self.assertRaises(ValueError) as ctx:
            store.save_backup_config(dup_payload)
        self.assertIn("重复", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

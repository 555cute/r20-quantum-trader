"""网关备份域路由：**归档抽取不许逃逸、短语不许省、失败不许报成功**（第二百六十二刀，开新面 routers/gateway/backups.py）。

先打印整个文件（501 行）再动笔。20 个处理器，按三条主线钉住：

| 语义 | 口径 |
|---|---|
| ★ **归档抽取路径安全** | `restore` 逐成员校验：绝对路径 / `..` 逃逸 / 越界解析 / 符号链接指向项目外 / 特殊设备节点 —— 任一命中 ⇒ **400 且不解压**；正常成员才落到 `_get_root()` 下 |
| ★ **短语不许省** | `BACKUP {job_id}` / `BACKUP R20` / `RESTORE R20` 逐字校验 ⇒ 400（**先验短语后动手**）；工作区/归档路径含 `..` ⇒ 400 |
| ★ **调用 ≠ 成功** | 备份脚本退出码非 0 ⇒ **502** + 审计 `failed`；`verify_archive` 抛错 ⇒ **409**；删除任务 `ValueError` ⇒ **409**（与新建/更新的 400 区分）|
| 目标映射 | `simple` 端点：显式 enabled 目标优先，否则退回 local；`baidu` 仅在 `auth_mode==oauth` 时算新版，否则 `legacy_bypy` + 提示迁移 |
| 就绪判定 | `configured` 现算 `backup_credential_status`（**不读**只在另一端点注入的 `credential_status`）；`test` 端点缺字段**逐个点名** |
| 密钥分流 | `SimpleBackupUpdateRequest.credentials` ⇒ `save_backup_credentials`；上传文件名必须 `.tar.gz`/`.tgz` 且净化后无 `..` |

⚠️ 如实登记两处**结构上不可达**的防御分支（只钉可达路径，不硬凑绿）：
1. `test_simple_backup` 第 **134** 行 `wanted_type not in req_map` —— schema `destination`
   正则只允许 `local|s3|oss|webdav|baidu_oauth`，`local` 又已被上面的早返回截走，
   剩下三个全在 `req_map` 里；
2. `upload_backup_archive` 第 **417** 行 `dest_path` 越界检查 —— `dest_path` 恒等于
   `target_dir / Path(filename).name`（基名不含分隔符），即使 `backups/local` 本身是软链，
   `dest_path.resolve()` 与 `target_dir.resolve()` 会一起落到同一处。

（`test_simple_backup` 第 116 行的"本地目录必须位于 backups/ 下"**是可达的真护栏**：
把 `backups/local` 做成指向外部目录的软链即可命中，本刀已用用例钉住。）
"""

import asyncio
import json
import os
import shutil
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from fastapi.responses import FileResponse

from r20_backend.routers.gateway import backups as BT
from r20_backend.schemas import (
    BackupJobCreateRequest,
    BackupJobImportRequest,
    BackupJobRunRequest,
    BackupJobUpdateRequest,
    BackupMethodsUpdate,
    BackupRequest,
    BackupRestoreRequest,
    BackupVerifyRequest,
    SimpleBackupUpdateRequest,
)


def _fake_backup_runtime(**attrs):
    """`scripts/backup_runtime.py` 顶层 `from backup_upload import ...` 只在 scripts/ 上
    sys.path 时才能导入 ⇒ 测试里注入同名假模块（路由是函数内 `from scripts.backup_runtime import ...`）。"""
    fake = types.ModuleType("scripts.backup_runtime")
    for key, value in attrs.items():
        setattr(fake, key, value)
    return mock.patch.dict(sys.modules, {"scripts.backup_runtime": fake})


def _job(**over):
    job = {
        "id": "nightly-default",
        "enabled": True,
        "schedule_times": ["02:00"],
        "targets": [
            {"id": "local-1", "type": "local", "enabled": True, "retention": 3,
             "credential_ref": "backup:local-1"},
        ],
    }
    job.update(over)
    return job


class _BackupBase(unittest.TestCase):
    def _start(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher

    def setUp(self):
        self.audits = []
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-backup-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp
        (self.root / "backups").mkdir(parents=True, exist_ok=True)
        self._start(mock.patch.object(BT, "_get_root", return_value=self.root))
        self._start(mock.patch.object(BT, "refresh_settings"))
        self.admin = mock.Mock()
        self.super = mock.Mock(return_value={"id": 1, "username": "root",
                                             "role": "superadmin"})
        self._start(mock.patch.object(BT, "require_admin_header", self.admin))
        self._start(mock.patch.object(BT, "require_superadmin", self.super))
        self._start(mock.patch.object(BT, "audit_record",
                                      lambda *a, **k: self.audits.append((a, k))))
        self._start(mock.patch.object(BT, "SCRIPTS_DIR", self.root / "scripts"))
        (self.root / "scripts").mkdir(exist_ok=True)
        self.log_file = self.root / "backup.log"
        self._start(mock.patch.object(BT, "BACKUP_LOG_FILE", self.log_file))
        self._start(mock.patch.object(BT, "backup_credential_status",
                                      return_value={"configured": True, "fields": ["k"]}))
        self._start(mock.patch.object(BT, "validate_backup_job",
                                      return_value={"ok": True}))

    def _rec(self, i=-1):
        return self.audits[i]


class SimpleBackupConfigTests(_BackupBase):
    def test_missing_primary_job_is_404(self):
        self._start(mock.patch.object(BT, "list_backup_jobs", return_value=[]))
        with self.assertRaises(HTTPException) as ctx:
            BT.simple_backup_config(x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_enabled_target_wins_and_local_maps_to_local(self):
        self._start(mock.patch.object(BT, "list_backup_jobs", return_value=[_job()]))
        out = BT.simple_backup_config(x_r20_admin_token="tok")
        self.assertEqual(out["job_id"], "nightly-default")
        self.assertEqual(out["destination"], "local")
        self.assertEqual(out["schedule_time"], "02:00")
        self.assertEqual(out["retention"], 3)
        self.assertTrue(out["configured"], "configured 必须现算，不能读别处注入的键")
        self.assertTrue(out["advanced_preserved"])
        self.assertFalse(out["legacy_bypy"])

    def test_legacy_baidu_is_flagged_and_downgraded_to_local(self):
        job = _job(targets=[{"id": "b", "type": "baidu", "enabled": True,
                             "auth_mode": "bypy", "retention": 5}])
        self._start(mock.patch.object(BT, "list_backup_jobs", return_value=[job]))
        out = BT.simple_backup_config(x_r20_admin_token="tok")
        self.assertTrue(out["legacy_bypy"])
        self.assertEqual(out["destination"], "local")
        self.assertIn("迁移", out["migration_note"])

    def test_oauth_baidu_maps_to_its_own_destination(self):
        job = _job(targets=[{"id": "b", "type": "baidu", "enabled": True,
                             "auth_mode": "oauth", "retention": 1}])
        self._start(mock.patch.object(BT, "list_backup_jobs", return_value=[job]))
        out = BT.simple_backup_config(x_r20_admin_token="tok")
        self.assertEqual(out["destination"], "baidu_oauth")
        self.assertFalse(out["legacy_bypy"])

    def test_s3_falls_back_to_the_local_target_when_none_enabled(self):
        job = _job(targets=[
            {"id": "s3", "type": "s3", "enabled": False, "bucket": "b"},
            {"id": "l", "type": "local", "enabled": False, "retention": 2}])
        self._start(mock.patch.object(BT, "list_backup_jobs", return_value=[job]))
        out = BT.simple_backup_config(x_r20_admin_token="tok")
        self.assertEqual(out["destination"], "local", "没有 enabled 目标 ⇒ 退回 local 目标")

    def test_latest_matching_manifest_is_returned_and_corrupt_ones_skipped(self):
        manifests = self.root / "backups" / "manifests"
        manifests.mkdir(parents=True, exist_ok=True)
        now = 1_700_000_000
        good = manifests / "good.json"
        good.write_text(json.dumps({"job_id": "nightly-default", "bytes": 10}),
                        encoding="utf-8")
        other = manifests / "other.json"
        other.write_text(json.dumps({"job_id": "someone-else"}), encoding="utf-8")
        bad = manifests / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        # 让坏文件最新 ⇒ 先被迭代到（JSONDecodeError ⇒ pass），再命中 good
        os.utime(good, (now - 20, now - 20))
        os.utime(other, (now - 10, now - 10))
        os.utime(bad, (now, now))
        self._start(mock.patch.object(BT, "list_backup_jobs", return_value=[_job()]))
        out = BT.simple_backup_config(x_r20_admin_token="tok")
        self.assertEqual(out["latest"]["bytes"], 10)

    def test_public_fallback_job_is_first_when_default_absent(self):
        self._start(mock.patch.object(BT, "list_backup_jobs",
                                      return_value=[_job(id="custom")]))
        out = BT.simple_backup_config(x_r20_admin_token="tok")
        self.assertEqual(out["job_id"], "custom")


class UpdateSimpleBackupTests(_BackupBase):
    def setUp(self):
        super().setUp()
        self.job = _job()
        self.jobs = mock.Mock(return_value=[self.job])
        self._start(mock.patch.object(BT, "list_backup_jobs", self.jobs))
        self.update = mock.Mock(side_effect=lambda jid, job: job)
        self._start(mock.patch.object(BT, "update_backup_job", self.update))
        self.creds = mock.Mock()
        self._start(mock.patch.object(BT, "save_backup_credentials", self.creds))

    def test_missing_job_is_404(self):
        self.jobs.return_value = []
        with self.assertRaises(HTTPException) as ctx:
            BT.update_simple_backup(SimpleBackupUpdateRequest(destination="local"),
                                    x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_creating_a_new_s3_target_disables_the_others(self):
        payload = SimpleBackupUpdateRequest(destination="s3", endpoint="https://s3",
                                            bucket="bkt", retention=4, enabled=True,
                                            schedule_time="03:30",
                                            credentials={"access_key_id": "k"})
        out = BT.update_simple_backup(payload, x_r20_session="s")
        saved = self.update.call_args[0][1]
        types = {t["type"]: t for t in saved["targets"]}
        self.assertTrue(types["s3"]["enabled"])
        self.assertFalse(types["local"]["enabled"], "同时只允许一个启用目标")
        self.assertEqual(types["s3"]["endpoint"], "https://s3")
        self.assertEqual(types["s3"]["bucket"], "bkt")
        self.assertEqual(types["s3"]["retention"], 0, "非 local 目标 retention 归 0")
        self.assertEqual(saved["schedule_times"], ["03:30"])
        self.creds.assert_called_once()
        self.assertTrue(out["saved"])
        self.assertEqual(self._rec()[0][0], "backup.simple.update")

    def test_baidu_oauth_forces_the_oauth_auth_mode(self):
        payload = SimpleBackupUpdateRequest(destination="baidu_oauth", enabled=False)
        BT.update_simple_backup(payload, x_r20_session="s")
        saved = self.update.call_args[0][1]
        baidu = [t for t in saved["targets"] if t["type"] == "baidu"][0]
        self.assertEqual(baidu["auth_mode"], "oauth")

    def test_local_retention_is_kept_and_value_error_is_400(self):
        payload = SimpleBackupUpdateRequest(destination="local", retention=9)
        BT.update_simple_backup(payload, x_r20_session="s")
        saved = self.update.call_args[0][1]
        local = [t for t in saved["targets"] if t["type"] == "local"][0]
        self.assertEqual(local["retention"], 9)

        self.update.side_effect = ValueError("计划时间冲突")
        with self.assertRaises(HTTPException) as ctx:
            BT.update_simple_backup(payload, x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)


class TestSimpleBackupTests(_BackupBase):
    def test_local_directory_is_probed_and_cleaned(self):
        out = BT.test_simple_backup(SimpleBackupUpdateRequest(destination="local"),
                                    x_r20_session="s")
        self.assertEqual(out["status"], "ready")
        self.assertFalse(out["sent"])
        self.assertFalse((self.root / "backups" / "local" / ".test_write.tmp").exists(),
                         "探测文件必须清理")

    def test_local_directory_unwritable_is_400(self):
        # 用文件占住 local 路径 ⇒ mkdir 抛 OSError
        (self.root / "backups" / "local").write_text("x", encoding="utf-8")
        with self.assertRaises(HTTPException) as ctx:
            BT.test_simple_backup(SimpleBackupUpdateRequest(destination="local"),
                                  x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("不可写", ctx.exception.detail)

    def test_local_directory_symlinked_outside_backups_is_400(self):
        outside = Path(tempfile.mkdtemp(prefix="r20-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        os.symlink(outside, self.root / "backups" / "local")
        with self.assertRaises(HTTPException) as ctx:
            BT.test_simple_backup(SimpleBackupUpdateRequest(destination="local"),
                                  x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("必须位于 backups/ 目录下", ctx.exception.detail)
        self.assertFalse((outside / ".test_write.tmp").exists())

    def test_missing_credentials_are_named_one_by_one(self):
        self._start(mock.patch.object(BT, "list_backup_jobs", return_value=[_job()]))
        payload = SimpleBackupUpdateRequest(destination="s3", endpoint="https://s3",
                                            bucket="b", credentials={})
        with self.assertRaises(HTTPException) as ctx:
            BT.test_simple_backup(payload, x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("access_key_id", ctx.exception.detail)
        self.assertIn("secret_access_key", ctx.exception.detail)

    def test_saved_credentials_are_merged_before_the_missing_check(self):
        import r20_backend.backup_secrets as BS
        self._start(mock.patch.object(BT, "list_backup_jobs", return_value=[_job(
            targets=[{"id": "s3", "type": "s3", "enabled": True,
                      "credential_ref": "backup:s3", "endpoint": "https://s3",
                      "bucket": "b"}])]))
        self._start(mock.patch.object(BS, "load_credentials", return_value={
            "access_key_id": "k", "secret_access_key": "s"}))
        self._start(mock.patch("r20_backend.net_security.validate_outbound_url",
                               side_effect=lambda u: u))
        out = BT.test_simple_backup(SimpleBackupUpdateRequest(destination="s3",
                                                              endpoint="https://s3",
                                                              bucket="b"),
                                    x_r20_session="s")
        self.assertEqual(out["status"], "ready")
        self.assertFalse(out["sent"])

    def test_endpoint_and_bucket_are_required_after_credentials(self):
        import r20_backend.backup_secrets as BS
        self._start(mock.patch.object(BT, "list_backup_jobs", return_value=[_job()]))
        self._start(mock.patch.object(BS, "load_credentials", return_value={}))
        self._start(mock.patch("r20_backend.net_security.validate_outbound_url",
                               side_effect=lambda u: u))
        creds = {"access_key_id": "k", "secret_access_key": "s"}
        with self.assertRaises(HTTPException) as ctx:
            BT.test_simple_backup(SimpleBackupUpdateRequest(destination="s3",
                                                            credentials=creds),
                                  x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Endpoint", ctx.exception.detail)

        with self.assertRaises(HTTPException) as ctx:
            BT.test_simple_backup(SimpleBackupUpdateRequest(destination="s3",
                                                            endpoint="https://s3",
                                                            credentials=creds),
                                  x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Bucket", ctx.exception.detail)

    def test_outbound_url_rejection_is_400(self):
        import r20_backend.backup_secrets as BS
        self._start(mock.patch.object(BT, "list_backup_jobs", return_value=[_job()]))
        self._start(mock.patch.object(BS, "load_credentials", return_value={}))
        self._start(mock.patch("r20_backend.net_security.validate_outbound_url",
                               side_effect=ValueError("内网地址被拒绝")))
        with self.assertRaises(HTTPException) as ctx:
            BT.test_simple_backup(SimpleBackupUpdateRequest(
                destination="s3", endpoint="http://127.0.0.1", bucket="b",
                credentials={"access_key_id": "k", "secret_access_key": "s"}),
                x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("内网", ctx.exception.detail)

    def test_webdav_needs_no_credentials_only_an_endpoint(self):
        self._start(mock.patch.object(BT, "list_backup_jobs", return_value=[_job()]))
        self._start(mock.patch("r20_backend.net_security.validate_outbound_url",
                               side_effect=lambda u: u))
        out = BT.test_simple_backup(SimpleBackupUpdateRequest(
            destination="webdav", endpoint="https://dav"), x_r20_session="s")
        self.assertEqual(out["destination"], "webdav")

    def test_unreadable_saved_credentials_are_tolerated(self):
        import r20_backend.backup_secrets as BS
        self._start(mock.patch.object(BT, "list_backup_jobs", return_value=[_job(
            targets=[{"id": "s3", "type": "s3", "enabled": True,
                      "credential_ref": "backup:s3", "endpoint": "https://s3",
                      "bucket": "b"}])]))
        self._start(mock.patch.object(BS, "load_credentials",
                                      side_effect=RuntimeError("密文库损坏")))
        self._start(mock.patch("r20_backend.net_security.validate_outbound_url",
                               side_effect=lambda u: u))
        out = BT.test_simple_backup(SimpleBackupUpdateRequest(
            destination="s3", endpoint="https://s3", bucket="b",
            credentials={"access_key_id": "k", "secret_access_key": "s"}),
            x_r20_session="s")
        self.assertEqual(out["status"], "ready", "读不到旧凭证不阻断，改用本次提交的")


class BackupTargetAndCredentialTests(_BackupBase):
    def test_target_types_are_advertised(self):
        out = BT.backup_target_types(x_r20_admin_token="tok")
        types = {t["type"] for t in out["target_types"]}
        self.assertIn("local", types)
        self.assertIn("baidu", types)
        self.assertEqual(len(out["target_types"]), 7)
        self.admin.assert_called_once_with("tok")

    def test_credentials_update_audits_only_field_names(self):
        self._start(mock.patch.object(BT, "save_backup_credentials",
                                      return_value={"fields": ["access_key_id"]}))
        out = BT.update_backup_credentials(
            mock.Mock(credential_ref="backup:s3",
                      credentials={"access_key_id": "secret-value"}),
            x_r20_session="s")
        self.assertTrue(out["saved"])
        self.assertEqual(out["status"]["fields"], ["access_key_id"])
        self.assertEqual(self._rec()[0][2]["fields"], ["access_key_id"])
        self.assertNotIn("secret-value", str(self._rec()[0][2]))

    def test_credentials_value_error_is_400(self):
        self._start(mock.patch.object(BT, "save_backup_credentials",
                                      side_effect=ValueError("未知引用")))
        with self.assertRaises(HTTPException) as ctx:
            BT.update_backup_credentials(
                mock.Mock(credential_ref="x", credentials={}), x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)


class BackupJobsTests(_BackupBase):
    def setUp(self):
        super().setUp()
        self.jobs = mock.Mock(return_value=[_job()])
        self._start(mock.patch.object(BT, "list_backup_jobs", self.jobs))

    def test_jobs_listing_injects_credential_status_and_manifests(self):
        manifests = self.root / "backups" / "manifests"
        manifests.mkdir(parents=True, exist_ok=True)
        (manifests / "m.json").write_text(json.dumps({"job_id": "nightly-default"}),
                                          encoding="utf-8")
        (manifests / "bad.json").write_text("nope", encoding="utf-8")
        out = BT.backup_jobs_api(x_r20_admin_token="tok")
        self.assertEqual(out["limits"]["maximum_jobs"], 12)
        self.assertEqual(out["recent_manifests"][0]["manifest_file"], "m.json")
        self.assertEqual(out["jobs"][0]["targets"][0]["credential_status"],
                         {"configured": True, "fields": ["k"]})

    def test_create_job_value_error_is_400(self):
        self._start(mock.patch.object(BT, "create_backup_job",
                                      side_effect=ValueError("超上限")))
        with self.assertRaises(HTTPException) as ctx:
            BT.create_backup_job_api(BackupJobCreateRequest(name="n"), x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_create_job_success_audits(self):
        self._start(mock.patch.object(BT, "create_backup_job",
                                      return_value={"id": "new"}))
        out = BT.create_backup_job_api(BackupJobCreateRequest(name="n"), x_r20_session="s")
        self.assertEqual(out, {"job": {"id": "new"}})
        self.assertEqual(self._rec()[0][1], "success")

    def test_update_job_returns_validation_and_maps_error_to_400(self):
        self._start(mock.patch.object(BT, "update_backup_job",
                                      return_value={"id": "j", "enabled": True}))
        out = BT.update_backup_job_api("j", BackupJobUpdateRequest(job={}), x_r20_session="s")
        self.assertEqual(out["validation"], {"ok": True})

        self._start(mock.patch.object(BT, "update_backup_job",
                                      side_effect=ValueError("坏 job")))
        with self.assertRaises(HTTPException) as ctx:
            BT.update_backup_job_api("j", BackupJobUpdateRequest(job={}), x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_delete_job_conflict_is_409_not_400(self):
        self._start(mock.patch.object(BT, "delete_backup_job",
                                      side_effect=ValueError("主任务不可删")))
        with self.assertRaises(HTTPException) as ctx:
            BT.delete_backup_job_api("nightly-default", x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 409)

        self._start(mock.patch.object(BT, "delete_backup_job"))
        self.assertEqual(BT.delete_backup_job_api("x", x_r20_session="s"),
                         {"deleted": True})

    def test_validate_route_is_read_only(self):
        out = BT.validate_backup_job_api(BackupJobUpdateRequest(job={"a": 1}),
                                         x_r20_admin_token="tok")
        self.assertEqual(out, {"ok": True})
        self.assertEqual(self.audits, [], "校验不写审计")

    def test_export_missing_job_is_404(self):
        self._start(mock.patch.object(BT, "export_backup_job",
                                      side_effect=ValueError("没有")))
        with self.assertRaises(HTTPException) as ctx:
            BT.export_backup_job_api("x", x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_import_job_maps_error_to_400(self):
        self._start(mock.patch.object(BT, "import_backup_job",
                                      side_effect=ValueError("结构不对")))
        with self.assertRaises(HTTPException) as ctx:
            BT.import_backup_job_api(BackupJobImportRequest(payload={}),
                                     x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_import_job_success_audits_the_new_id(self):
        self._start(mock.patch.object(BT, "import_backup_job",
                                      return_value={"id": "imported"}))
        out = BT.import_backup_job_api(BackupJobImportRequest(payload={"a": 1}),
                                       x_r20_session="s")
        self.assertEqual(out, {"job": {"id": "imported"}})
        self.assertEqual(self._rec()[0][0], "backup.job.import")
        self.assertEqual(self._rec()[0][2]["job_id"], "imported")


class RunJobTests(_BackupBase):
    def setUp(self):
        super().setUp()
        self.get = mock.Mock(return_value=_job())
        self._start(mock.patch.object(BT, "get_backup_job", self.get))
        (self.root / "scripts" / "nightly_backup_and_clean.py").write_text("x",
                                                                          encoding="utf-8")

    def test_phrase_is_mandatory(self):
        with self.assertRaises(HTTPException) as ctx:
            BT.run_backup_job_api("nightly-default",
                                  BackupJobRunRequest(confirmation="BACKUP other"),
                                  x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_unknown_job_is_404(self):
        self.get.side_effect = ValueError("没有这个任务")
        with self.assertRaises(HTTPException) as ctx:
            BT.run_backup_job_api("nightly-default",
                                  BackupJobRunRequest(confirmation="backup nightly-default"),
                                  x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_failed_script_is_502_and_writes_the_log(self):
        self._start(mock.patch.object(BT.subprocess, "run", return_value=mock.Mock(
            returncode=1, stdout="out", stderr="boom")))
        with self.assertRaises(HTTPException) as ctx:
            BT.run_backup_job_api("nightly-default",
                                  BackupJobRunRequest(confirmation="BACKUP nightly-default"),
                                  x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("boom", ctx.exception.detail)
        self.assertEqual(self._rec()[0][1], "failed")
        self.assertIn("boom", self.log_file.read_text(encoding="utf-8"))

    def test_success_returns_the_tail_and_audits(self):
        runner = mock.Mock(return_value=mock.Mock(returncode=0, stdout="done", stderr=""))
        self._start(mock.patch.object(BT.subprocess, "run", runner))
        out = BT.run_backup_job_api("nightly-default",
                                    BackupJobRunRequest(confirmation="backup nightly-default"),
                                    x_r20_session="s")
        self.assertTrue(out["completed"])
        self.assertEqual(out["output"], "done")
        self.assertEqual(runner.call_args[0][0][-2:], ["--job-id", "nightly-default"])
        self.assertEqual(runner.call_args.kwargs["timeout"], 1800)
        self.assertEqual(self._rec()[0][1], "success")


class VerifyArchiveTests(_BackupBase):
    def test_path_outside_backups_is_400(self):
        (self.root / "secret.tar.gz").write_bytes(b"x")
        with self.assertRaises(HTTPException) as ctx:
            BT.verify_backup_archive_api(
                BackupVerifyRequest(archive_path="secret.tar.gz"), x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("backups/", ctx.exception.detail)

    def test_verification_failure_is_409(self):
        (self.root / "backups" / "a.tar.gz").write_bytes(b"x")
        with _fake_backup_runtime(verify_archive=mock.Mock(
                side_effect=RuntimeError("校验和不符"))):
            with self.assertRaises(HTTPException) as ctx:
                BT.verify_backup_archive_api(
                    BackupVerifyRequest(archive_path="backups/a.tar.gz"),
                    x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 409)

    def test_success_audits_member_count(self):
        (self.root / "backups" / "a.tar.gz").write_bytes(b"x")
        with _fake_backup_runtime(verify_archive=mock.Mock(
                return_value={"members": 4, "sha256": "abc"})):
            out = BT.verify_backup_archive_api(
                BackupVerifyRequest(archive_path="backups/a.tar.gz"), x_r20_session="s")
        self.assertEqual(out["members"], 4)
        self.assertEqual(self._rec()[0][2]["members"], 4)


class BackupMethodsTests(_BackupBase):
    def test_at_least_one_method_must_stay_enabled(self):
        payload = BackupMethodsUpdate(baidu_enabled=False, local_enabled=False,
                                      local_retention=3, sqlite_enabled=False,
                                      sqlite_retention=3)
        with self.assertRaises(HTTPException) as ctx:
            BT.update_backup_methods(payload, x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_saving_methods_audits_the_enabled_flags(self):
        saved = []
        self._start(mock.patch.object(BT, "save_backup_methods", lambda m: saved.append(m)))
        self._start(mock.patch.object(BT, "load_backup_methods",
                                      return_value={"local": {"enabled": True}}))
        out = BT.update_backup_methods(
            BackupMethodsUpdate(baidu_enabled=False, local_enabled=True,
                                local_retention=5, sqlite_enabled=True,
                                sqlite_retention=7),
            x_r20_admin_token="tok")
        self.assertEqual(saved[-1]["sqlite"]["retention"], 7)
        self.assertEqual(saved[-1]["baidu"]["retention"], 0)
        self.assertTrue(out["saved"])
        self.assertEqual(self._rec()[0][2]["local"], True)

    def test_status_lists_archives_snapshots_and_log_fallback(self):
        (self.root / "backups" / "top.tar.gz").write_bytes(b"a")
        (self.root / "backups" / "local").mkdir(exist_ok=True)
        (self.root / "backups" / "local" / "inner.tar.gz").write_bytes(b"bb")
        (self.root / "backups" / "sqlite").mkdir(exist_ok=True)
        (self.root / "backups" / "sqlite" / "snap.db").write_bytes(b"c")
        self._start(mock.patch.object(BT, "load_backup_methods", return_value={}))
        self._start(mock.patch.object(BT, "list_backup_jobs", return_value=[]))
        out = BT.backup_status(x_r20_admin_token="tok")
        names = {a["name"] for a in out["local_archives"]}
        self.assertEqual(names, {"top.tar.gz", "local/inner.tar.gz"})
        self.assertEqual(out["sqlite_snapshots"][0]["name"], "snap.db")
        self.assertEqual(out["last_log"], "尚无后台手动灾备日志")

        self.log_file.write_text("previous run", encoding="utf-8")
        out = BT.backup_status(x_r20_admin_token="tok")
        self.assertEqual(out["last_log"], "previous run")


class RunBackupTests(_BackupBase):
    def test_confirmation_is_mandatory(self):
        with self.assertRaises(HTTPException) as ctx:
            BT.run_backup(BackupRequest(confirmation="BACKUP"), x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_failure_is_502_and_success_reports_output(self):
        self._start(mock.patch.object(BT.subprocess, "run", return_value=mock.Mock(
            returncode=9, stdout="", stderr="disk full")))
        (self.root / "scripts" / "nightly_backup_and_clean.py").write_text("x",
                                                                          encoding="utf-8")
        with self.assertRaises(HTTPException) as ctx:
            BT.run_backup(BackupRequest(confirmation="backup r20"), x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(self._rec()[0][1], "failed")

        self._start(mock.patch.object(BT.subprocess, "run", return_value=mock.Mock(
            returncode=0, stdout="all good", stderr="")))
        out = BT.run_backup(BackupRequest(confirmation="BACKUP R20"), x_r20_admin_token="tok")
        self.assertTrue(out["completed"])
        self.assertEqual(out["output"], "all good")
        self.assertEqual(self._rec()[0][1], "success")


class DownloadBackupTests(_BackupBase):
    def setUp(self):
        super().setUp()
        self.local = self.root / "backups" / "local"
        self.local.mkdir(exist_ok=True)
        self.arch = self.local / "b.tar.gz"
        self.arch.write_bytes(b"archive")

    def test_dotdot_is_400_before_any_lookup(self):
        with self.assertRaises(HTTPException) as ctx:
            BT.download_backup_archive("../etc/passwd", x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("..", ctx.exception.detail)

    def test_missing_file_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            BT.download_backup_archive("nope.tar.gz", x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_session_can_come_from_the_query_token(self):
        out = BT.download_backup_archive("b.tar.gz", token="sess", x_r20_admin_token=None)
        self.assertIsInstance(out, FileResponse)
        self.admin.assert_called_once_with(None, "sess")

    def test_success_sets_attachment_headers_and_audits(self):
        out = BT.download_backup_archive("b.tar.gz", x_r20_admin_token="tok")
        self.assertEqual(out.media_type, "application/gzip")
        self.assertIn("attachment", out.headers["content-disposition"])
        self.assertEqual(self._rec()[0][0], "backup.download")

    def test_nested_name_resolves_inside_backups(self):
        (self.root / "backups" / "nested").mkdir(exist_ok=True)
        (self.root / "backups" / "nested" / "n.tar.gz").write_bytes(b"n")
        out = BT.download_backup_archive("nested/n.tar.gz", x_r20_admin_token="tok")
        self.assertIsInstance(out, FileResponse)

    def test_symlink_escaping_backups_is_400(self):
        outside = Path(tempfile.mkdtemp(prefix="r20-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        target = outside / "real.tar.gz"
        target.write_bytes(b"x")
        os.symlink(target, self.root / "backups" / "esc.tar.gz")
        with self.assertRaises(HTTPException) as ctx:
            BT.download_backup_archive("esc.tar.gz", x_r20_admin_token="tok")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("非法文件路径", ctx.exception.detail)


class _Upload:
    def __init__(self, filename, content):
        self.filename = filename
        self._content = content

    async def read(self):
        return self._content


class UploadBackupTests(_BackupBase):
    def test_extension_is_checked(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(BT.upload_backup_archive(file=_Upload("a.zip", b"x"),
                                                 x_r20_session="s"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn(".tar.gz", ctx.exception.detail)

    def test_dotdot_in_the_clean_name_is_400(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(BT.upload_backup_archive(file=_Upload("..hidden.tar.gz", b"x"),
                                                 x_r20_session="s"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("非法文件名", ctx.exception.detail)

    def test_directory_traversal_is_neutralised_by_the_basename(self):
        out = asyncio.run(BT.upload_backup_archive(file=_Upload("../a.tar.gz", b"x"),
                                                   x_r20_session="s"))
        self.assertEqual(out["filename"], "a.tar.gz")
        self.assertTrue((self.root / "backups" / "local" / "a.tar.gz").exists())
        self.assertFalse((self.root / "a.tar.gz").exists())

    def test_missing_filename_is_400(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(BT.upload_backup_archive(file=_Upload(None, b"x"),
                                                 x_r20_session="s"))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_success_writes_into_backups_local(self):
        out = asyncio.run(BT.upload_backup_archive(file=_Upload("new.tar.gz", b"data"),
                                                   x_r20_session="s"))
        self.assertTrue(out["uploaded"])
        self.assertEqual(out["bytes"], 4)
        self.assertTrue((self.root / "backups" / "local" / "new.tar.gz").exists())
        self.assertEqual(self._rec()[0][0], "backup.upload")


class RestoreBackupTests(_BackupBase):
    def setUp(self):
        super().setUp()
        self.backups = self.root / "backups"

    def _make_tar(self, name, members):
        path = self.backups / name
        with tarfile.open(path, "w:gz") as tar:
            for member in members:
                tar.addfile(member)
        return path

    def _regular(self, name, data=b"hello"):
        import io
        info = tarfile.TarInfo(name)
        info.size = len(data)
        return info, io.BytesIO(data)

    def test_confirmation_and_dotdot_are_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            BT.restore_backup_archive(
                BackupRestoreRequest(archive_name="a.tar.gz", confirmation="RESTORE"),
                x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)

        with self.assertRaises(HTTPException) as ctx:
            BT.restore_backup_archive(
                BackupRestoreRequest(archive_name="../a.tar.gz",
                                     confirmation="RESTORE R20"),
                x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("..", ctx.exception.detail)

    def test_missing_archive_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            BT.restore_backup_archive(
                BackupRestoreRequest(archive_name="nope.tar.gz",
                                     confirmation="RESTORE R20"),
                x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_restore_extracts_relative_members_and_audits(self):
        import io
        info = tarfile.TarInfo("data/ok.txt")
        payload = b"payload"
        info.size = len(payload)
        path = self.backups / "good.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            tar.addfile(info, io.BytesIO(payload))
        out = BT.restore_backup_archive(
            BackupRestoreRequest(archive_name="good.tar.gz", confirmation="RESTORE R20"),
            x_r20_session="s")
        self.assertTrue(out["restored"])
        self.assertEqual(out["restored_count"], 1)
        self.assertTrue((self.root / "data" / "ok.txt").exists())
        self.assertEqual(self._rec()[0][0], "backup.restore")

    def test_absolute_member_path_is_rejected(self):
        import io
        info = tarfile.TarInfo("/etc/evil")
        payload = b"x"
        info.size = len(payload)
        path = self.backups / "abs.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            tar.addfile(info, io.BytesIO(payload))
        with self.assertRaises(HTTPException) as ctx:
            BT.restore_backup_archive(
                BackupRestoreRequest(archive_name="abs.tar.gz",
                                     confirmation="RESTORE R20"),
                x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("绝对路径", ctx.exception.detail)

    def test_escaping_member_path_is_rejected(self):
        import io
        info = tarfile.TarInfo("../evil.txt")
        payload = b"x"
        info.size = len(payload)
        path = self.backups / "esc.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            tar.addfile(info, io.BytesIO(payload))
        with self.assertRaises(HTTPException) as ctx:
            BT.restore_backup_archive(
                BackupRestoreRequest(archive_name="esc.tar.gz",
                                     confirmation="RESTORE R20"),
                x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("逃逸", ctx.exception.detail)
        self.assertFalse((self.root.parent / "evil.txt").exists())

    def test_escaping_symlink_is_rejected(self):
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../etc/passwd"
        path = self.backups / "link.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            tar.addfile(info)
        with self.assertRaises(HTTPException) as ctx:
            BT.restore_backup_archive(
                BackupRestoreRequest(archive_name="link.tar.gz",
                                     confirmation="RESTORE R20"),
                x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("符号链接", ctx.exception.detail)

    def test_special_device_member_is_rejected(self):
        info = tarfile.TarInfo("dev/null")
        info.type = tarfile.CHRTYPE
        path = self.backups / "dev.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            tar.addfile(info)
        with self.assertRaises(HTTPException) as ctx:
            BT.restore_backup_archive(
                BackupRestoreRequest(archive_name="dev.tar.gz",
                                     confirmation="RESTORE R20"),
                x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("设备节点", ctx.exception.detail)

    def test_corrupt_archive_is_400(self):
        (self.backups / "bad.tar.gz").write_bytes(b"not a tar")
        with self.assertRaises(HTTPException) as ctx:
            BT.restore_backup_archive(
                BackupRestoreRequest(archive_name="bad.tar.gz",
                                     confirmation="RESTORE R20"),
                x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("损坏", ctx.exception.detail)

    def test_nested_archive_resolves_inside_backups_and_restores(self):
        import io
        nested = self.backups / "nested"
        nested.mkdir(exist_ok=True)
        info = tarfile.TarInfo("data/nested.txt")
        payload = b"nested"
        info.size = len(payload)
        with tarfile.open(nested / "n.tar.gz", "w:gz") as tar:
            tar.addfile(info, io.BytesIO(payload))
        out = BT.restore_backup_archive(
            BackupRestoreRequest(archive_name="nested/n.tar.gz", confirmation="RESTORE R20"),
            x_r20_session="s")
        self.assertEqual(out["restored_count"], 1)
        self.assertTrue((self.root / "data" / "nested.txt").exists())

    def test_symlink_escaping_backups_is_400(self):
        outside = Path(tempfile.mkdtemp(prefix="r20-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        target = outside / "real.tar.gz"
        target.write_bytes(b"x")
        os.symlink(target, self.backups / "esc.tar.gz")
        with self.assertRaises(HTTPException) as ctx:
            BT.restore_backup_archive(
                BackupRestoreRequest(archive_name="esc.tar.gz", confirmation="RESTORE R20"),
                x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("路径不合法", ctx.exception.detail)

    def test_member_resolving_outside_via_a_symlinked_dir_is_400(self):
        import io
        outside = Path(tempfile.mkdtemp(prefix="r20-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        os.symlink(outside, self.root / "linkdir")
        info = tarfile.TarInfo("linkdir/x.txt")
        payload = b"x"
        info.size = len(payload)
        path = self.backups / "resolve.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            tar.addfile(info, io.BytesIO(payload))
        with self.assertRaises(HTTPException) as ctx:
            BT.restore_backup_archive(
                BackupRestoreRequest(archive_name="resolve.tar.gz",
                                     confirmation="RESTORE R20"),
                x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("越界", ctx.exception.detail)
        self.assertFalse((outside / "x.txt").exists())

    def test_symlink_target_resolving_outside_is_400(self):
        outside = Path(tempfile.mkdtemp(prefix="r20-outside-"))
        self.addCleanup(shutil.rmtree, outside, True)
        # linkname 既不是绝对路径也不含 ".."，但经 root 下的软链解析后落到项目外
        os.symlink(outside, self.root / "outdir")
        info = tarfile.TarInfo("s")
        info.type = tarfile.SYMTYPE
        info.linkname = "outdir/target"
        path = self.backups / "linktarget.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            tar.addfile(info)
        with self.assertRaises(HTTPException) as ctx:
            BT.restore_backup_archive(
                BackupRestoreRequest(archive_name="linktarget.tar.gz",
                                     confirmation="RESTORE R20"),
                x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("符号链接指向项目外部", ctx.exception.detail)

    def test_encrypted_archive_requires_a_key_env(self):
        import io
        info = tarfile.TarInfo("data/x.txt")
        payload = b"x"
        info.size = len(payload)
        path = self.backups / "enc.tar.gz.aes256"
        with tarfile.open(path, "w:gz") as tar:
            tar.addfile(info, io.BytesIO(payload))
        with self.assertRaises(HTTPException) as ctx:
            BT.restore_backup_archive(
                BackupRestoreRequest(archive_name="enc.tar.gz.aes256",
                                     confirmation="RESTORE R20"),
                x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("加密密钥", ctx.exception.detail)

    def test_decrypt_failure_is_400_and_temp_is_cleaned(self):
        (self.backups / "enc.tar.gz.aes256").write_bytes(b"cipher")
        os.environ["R20_TEST_KEY"] = "k"
        self.addCleanup(lambda: os.environ.pop("R20_TEST_KEY", None))
        with _fake_backup_runtime(decrypt_archive=mock.Mock(
                side_effect=RuntimeError("密钥不对"))):
            with self.assertRaises(HTTPException) as ctx:
                BT.restore_backup_archive(
                    BackupRestoreRequest(archive_name="enc.tar.gz.aes256",
                                         confirmation="RESTORE R20",
                                         key_env="R20_TEST_KEY"),
                    x_r20_session="s")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("解密归档失败", ctx.exception.detail)
        leftovers = [p for p in self.backups.iterdir() if p.name.startswith("r20-restore-")]
        self.assertEqual(leftovers, [], "失败后临时明文必须清理")


if __name__ == "__main__":
    unittest.main()

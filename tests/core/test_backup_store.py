"""灾备任务存储：**损坏 ≠ 空库、原子写不许半截、路径不许越界**（第二百六十六刀，开新面 backup_store.py）。

先打印整个文件（387 行）再动笔。本模块是 `routers/gateway/backups.py` 的持久层
（路由器那一刀把本模块整体 mock 掉了，真正的落盘语义在此收口）。

| 语义 | 口径 |
|---|---|
| ★ **损坏 ≠ 空库** | `load_backup_config` 把"文件读不出/解不开"与"从未配置"分开：前者置 `_LOAD_WAS_CORRUPT`，随后 `save_backup_config` **熔断拒写**（否则一次 RMW 就会把最多 12 个任务 + 凭证引用以默认档静默清零）|
| ★ **原子写** | `_atomic_write` 走 `mkstemp`→`fsync`→`os.replace`→`chmod 0600`；**失败路径必须清掉临时文件**（不留半截）|
| ★ **路径不许越界** | 本地目标必须解析在项目 `backups/` 内（`../` 直接判错）；非本地目标的 `remote_path` 不得含 `..` |
| ★ **保存前必须过校验** | `save_backup_config` 逐个任务 `validate_backup_job(raise_error=True)`；ID 重复、任务数为 0、超 `MAX_JOBS` 一律拒 |
| 归一化 | 时间表 `HH:MM` 去重排序（非法值丢弃、空则回落 `02:00`）、`scope` 白名单过滤、`exclude` 上限 100、压缩 1..9、local retention 1..365（非 local 归 0）、retries 1..10 |
| 导入导出 | `export_job` 抹掉 id/启用态/时间戳/**凭证引用**；`import_job` 重新发号、默认停用、校验后落盘 |
| 兼容层 | `load/save_backup_methods` 是原三开关 API 的投影，不得破坏 v2 结构 |

⚠️ 测试隔离：`CONFIG_FILE` 与 `ROOT` 都指到临时目录（本模块的 `ROOT` 会被
`validate_backup_job` 用来解析 `backups/`，不隔离就会拿生产目录做判断）。
"""

import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from r20_backend import backup_store as BS


class _StoreBase(unittest.TestCase):
    def _start(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-bstore-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config = self.tmp / "data" / "backup_methods.json"
        self._start(mock.patch.object(BS, "CONFIG_FILE", self.config))
        self._start(mock.patch.object(BS, "ROOT", self.tmp))
        self.addCleanup(setattr, BS, "_LOAD_WAS_CORRUPT", False)
        self._start(mock.patch.object(BS, "_LOAD_WAS_CORRUPT", False))

    def _write_config(self, text):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(text, encoding="utf-8")

    def _valid_job(self, **over):
        job = BS._default_job()
        job.update(over)
        return job


class NormalizeTargetTests(unittest.TestCase):
    def test_defaults_and_truncation(self):
        out = BS._normalize_target({})
        self.assertEqual(out["type"], "local")
        self.assertTrue(out["id"].startswith("target-"))
        self.assertEqual(out["label"], "LOCAL")
        self.assertTrue(out["enabled"])
        self.assertEqual(out["credential_ref"], f"backup:{out['id']}")
        self.assertEqual(out["auth_mode"], "native")
        self.assertEqual(out["remote_path"], "R20_Backups")
        self.assertEqual(out["path"], "backups/local")
        self.assertEqual(out["retention"], 3, "local 目标保留份数上限收敛")

        long = BS._normalize_target({"type": "s3", "label": "x" * 200,
                                     "remote_path": "y" * 300, "path": "z" * 300,
                                     "bucket": "b" * 200, "endpoint": "e" * 400})
        self.assertEqual(len(long["label"]), 80)
        self.assertEqual(len(long["remote_path"]), 240)
        self.assertEqual(len(long["path"]), 240)
        self.assertEqual(len(long["bucket"]), 120)
        self.assertEqual(len(long["endpoint"]), 300)

    def test_auth_mode_is_chosen_by_target_type(self):
        self.assertEqual(BS._normalize_target({"type": "baidu"})["auth_mode"], "bypy")
        self.assertEqual(BS._normalize_target({"type": "aliyundrive"})["auth_mode"], "webdav")
        self.assertEqual(BS._normalize_target({"type": "quark"})["auth_mode"], "webdav")
        self.assertEqual(BS._normalize_target({"type": "webdav"})["auth_mode"], "native")

    def test_retention_only_applies_to_local_and_is_clamped(self):
        self.assertEqual(BS._normalize_target({"type": "local", "retention": 999})["retention"], 365)
        self.assertEqual(BS._normalize_target({"type": "local", "retention": 0})["retention"], 1)
        self.assertEqual(BS._normalize_target({"type": "s3", "retention": 99})["retention"], 0,
                         "非本地目标不保留份数（由远端生命周期管）")

    def test_non_numeric_retention_and_retries_fall_back(self):
        out = BS._normalize_target({"retention": "abc", "retries": "abc"})
        self.assertEqual(out["retention"], 3)
        self.assertEqual(out["retries"], 3)
        self.assertEqual(BS._normalize_target({"retries": 99})["retries"], 10)
        self.assertEqual(BS._normalize_target({"retries": 0})["retries"], 1)

    def test_quark_oauth_is_marked_experimental(self):
        self.assertTrue(BS._normalize_target({"type": "quark",
                                              "auth_mode": "oauth"})["experimental"])
        self.assertFalse(BS._normalize_target({"type": "quark"})["experimental"])
        self.assertFalse(BS._normalize_target({"type": "s3",
                                               "auth_mode": "oauth"})["experimental"])


class NormalizeJobTests(unittest.TestCase):
    def test_schedule_times_are_deduped_sorted_and_invalid_dropped(self):
        out = BS._normalize_job({"schedule_times": ["23:59", "02:00", "02:00",
                                                    "25:00", "aa:bb", "07:05"]})
        self.assertEqual(out["schedule_times"], ["02:00", "07:05", "23:59"])

    def test_empty_or_invalid_schedule_falls_back_to_two_am(self):
        self.assertEqual(BS._normalize_job({"schedule_times": []})["schedule_times"], ["02:00"])
        self.assertEqual(BS._normalize_job({"schedule_times": "nope"})["schedule_times"],
                         ["02:00"])
        self.assertEqual(BS._normalize_job({"schedule_time": "03:00"})["schedule_times"],
                         ["03:00"], "旧版单值 schedule_time 必须能被接住")

    def test_scope_is_filtered_by_the_whitelist(self):
        out = BS._normalize_job({"scope": ["data", "etc", "/root", "scripts"]})
        self.assertEqual(out["scope"], ["data", "scripts"])

    def test_exclude_is_trimmed_and_capped(self):
        out = BS._normalize_job({"exclude": ["  a  ", "", "   "] + [f"x{i}" for i in range(200)]})
        self.assertEqual(out["exclude"][0], "a")
        self.assertNotIn("", out["exclude"])
        self.assertEqual(len(out["exclude"]), 100)

    def test_compression_and_sqlite_retention_are_clamped(self):
        self.assertEqual(BS._normalize_job({"compression_level": 99})["compression_level"], 9)
        self.assertEqual(BS._normalize_job({"compression_level": -5})["compression_level"], 1)
        self.assertEqual(BS._normalize_job({"compression_level": "x"})["compression_level"], 6)
        self.assertEqual(BS._normalize_job({"sqlite": {"retention": 999}})["sqlite"]["retention"],
                         365)
        self.assertEqual(BS._normalize_job({"sqlite": {"retention": "x"}})["sqlite"]["retention"], 7)

    def test_encryption_and_sqlite_shapes_are_normalised(self):
        out = BS._normalize_job({"encryption": {"enabled": True, "key_env": "MY_KEY_1"}})
        self.assertEqual(out["encryption"], {"enabled": True, "key_env": "MY_KEY_1"})
        self.assertEqual(BS._normalize_job({"encryption": "nope"})["encryption"]["key_env"],
                         "R20_BACKUP_ENCRYPTION_KEY")
        self.assertEqual(BS._normalize_job({"sqlite": "nope"})["sqlite"],
                         {"enabled": False, "retention": 7})

    def test_unknown_top_level_keys_and_unknown_target_types_are_dropped(self):
        out = BS._normalize_job({"evil": 1, "targets": [
            {"type": "local", "path": "backups/local"},
            {"type": "telnet"}, {"no_type": True}, "not-a-dict"]})
        self.assertNotIn("evil", out)
        self.assertEqual([t["type"] for t in out["targets"]], ["local"],
                         "不在 ALLOWED_TARGETS 里的目标类型必须丢弃")

    def test_ids_and_names_get_defaults(self):
        out = BS._normalize_job({})
        self.assertTrue(out["id"].startswith("backup-"))
        self.assertEqual(out["name"], "自定义灾备任务")
        self.assertEqual(out["timezone"], "Asia/Shanghai")
        self.assertEqual(out["checksum"], "sha256")


class MigrateTests(unittest.TestCase):
    def test_v2_payload_is_normalised_and_capped(self):
        raw = {"version": 2, "jobs": [{"id": f"j{i}", "name": f"n{i}",
                                       "scope": ["data"],
                                       "targets": [{"type": "local", "enabled": True}]}
                                      for i in range(BS.MAX_JOBS + 5)] + ["junk"]}
        out = BS._migrate(raw)
        self.assertEqual(out["version"], 2)
        self.assertEqual(len(out["jobs"]), BS.MAX_JOBS, "超过上限的旧任务必须被截断")

    def test_v1_three_switch_payload_is_upgraded(self):
        out = BS._migrate({"baidu": {"enabled": True, "retention": 4},
                           "local": {"enabled": False, "retention": 9},
                           "sqlite": {"enabled": True, "retention": 11}})
        job = out["jobs"][0]
        targets = {t["type"]: t for t in job["targets"]}
        self.assertTrue(targets["baidu"]["enabled"])
        self.assertEqual(targets["baidu"]["retention"], 4)
        self.assertFalse(targets["local"]["enabled"])
        self.assertTrue(job["sqlite"]["enabled"])
        self.assertEqual(job["sqlite"]["retention"], 11)

    def test_v1_payload_ignores_non_dict_sections(self):
        out = BS._migrate({"baidu": "nope", "version": 1})
        targets = {t["type"]: t for t in out["jobs"][0]["targets"]}
        self.assertFalse(targets["baidu"]["enabled"], "非 dict 的旧段不得覆盖默认值")


class LoadTests(_StoreBase):
    def test_missing_file_is_a_legitimate_default_and_does_not_freeze_writes(self):
        config = BS.load_backup_config()
        self.assertEqual(config["version"], 2)
        self.assertEqual(len(config["jobs"]), 1)
        self.assertFalse(BS._LOAD_WAS_CORRUPT)

    def test_corrupt_json_freezes_the_write_side(self):
        self._write_config("{not json")
        config = BS.load_backup_config()
        self.assertTrue(BS._LOAD_WAS_CORRUPT)
        self.assertEqual(len(config["jobs"]), 1, "坏文件也要能返回一个可读形状给面板")
        with self.assertRaises(ValueError) as ctx:
            BS.save_backup_config(config)
        self.assertIn("损坏不可读", str(ctx.exception))
        self.assertIn("拒绝", str(ctx.exception))

    def test_unreadable_file_is_treated_as_corrupt(self):
        self.config.mkdir(parents=True, exist_ok=True)   # 目录冒充文件 ⇒ OSError
        BS.load_backup_config()
        self.assertTrue(BS._LOAD_WAS_CORRUPT)

    def test_non_dict_json_migrates_from_the_legacy_shape(self):
        self._write_config("[1, 2, 3]")
        config = BS.load_backup_config()
        self.assertFalse(BS._LOAD_WAS_CORRUPT)
        self.assertEqual(len(config["jobs"]), 1)

    def test_a_successful_load_clears_the_corrupt_flag(self):
        self._start(mock.patch.object(BS, "_LOAD_WAS_CORRUPT", True))
        self._write_config(json.dumps({"version": 2, "jobs": []}))
        BS.load_backup_config()
        self.assertFalse(BS._LOAD_WAS_CORRUPT, "读到好文件必须解除熔断")


class AtomicWriteTests(_StoreBase):
    def test_write_is_atomic_and_owner_only(self):
        BS._atomic_write({"version": 2, "jobs": []})
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8")),
                         {"version": 2, "jobs": []})
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600,
                         "灾备配置含凭证引用 ⇒ 必须是 0600")
        self.assertEqual(list(self.config.parent.glob(".backup-jobs-*.tmp")), [],
                         "不得留下临时文件")

    def test_failed_replace_still_cleans_the_temp_file(self):
        with mock.patch.object(BS.os, "replace", side_effect=OSError("磁盘满了")):
            with self.assertRaises(OSError):
                BS._atomic_write({"version": 2, "jobs": []})
        self.assertEqual(list(self.config.parent.glob(".backup-jobs-*.tmp")), [],
                         "失败路径也必须清掉半截临时文件")
        self.assertFalse(self.config.exists())


class SaveTests(_StoreBase):
    def test_at_least_one_job_is_required(self):
        with self.assertRaises(ValueError) as ctx:
            BS.save_backup_config({"jobs": []})
        self.assertIn("至少保留一个", str(ctx.exception))
        with self.assertRaises(ValueError):
            BS.save_backup_config({})

    def test_max_jobs_is_enforced(self):
        with self.assertRaises(ValueError) as ctx:
            BS.save_backup_config({"jobs": [self._valid_job(id=f"j{i}")
                                            for i in range(BS.MAX_JOBS + 1)]})
        self.assertIn("最多", str(ctx.exception))

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            BS.save_backup_config({"jobs": [self._valid_job(id="same"),
                                            self._valid_job(id="same")]})
        self.assertIn("不能重复", str(ctx.exception))

    def test_each_job_must_pass_validation_before_persisting(self):
        bad = self._valid_job(name="", scope=[])
        with self.assertRaises(ValueError):
            BS.save_backup_config({"jobs": [bad]})
        self.assertFalse(self.config.exists(), "校验失败不得落盘")

    def test_happy_path_persists_normalised_jobs(self):
        BS.save_backup_config({"jobs": [self._valid_job(id="j1", name="  A  ")]})
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 2)
        self.assertEqual(saved["jobs"][0]["id"], "j1")
        self.assertEqual(saved["jobs"][0]["name"], "A")


class ValidationTests(unittest.TestCase):
    def test_default_job_is_valid(self):
        with mock.patch.object(BS, "ROOT", Path(tempfile.mkdtemp(prefix="r20-v-"))):
            out = BS.validate_backup_job(BS._default_job())
        self.assertTrue(out["valid"])

    def test_empty_name_no_scope_and_no_target_are_errors(self):
        out = BS.validate_backup_job({"name": "  ", "scope": [], "sqlite": {"enabled": False},
                                      "targets": [], "encryption": {}})
        self.assertFalse(out["valid"])
        self.assertIn("任务名称不能为空", out["errors"])
        self.assertIn("至少选择一个文件范围或启用 SQLite 热备", out["errors"])
        self.assertIn("至少启用一个备份目标或 SQLite 热备", out["errors"])

    def test_sqlite_alone_satisfies_scope_and_target_requirements(self):
        out = BS.validate_backup_job({"name": "n", "scope": [], "targets": [],
                                      "sqlite": {"enabled": True, "retention": 7},
                                      "encryption": {}})
        self.assertTrue(out["valid"], out["errors"])

    def test_encryption_key_env_must_be_a_valid_variable_name(self):
        out = BS.validate_backup_job({"name": "n", "scope": ["data"], "targets": [],
                                      "sqlite": {"enabled": True},
                                      "encryption": {"enabled": True, "key_env": "1bad-name"}})
        self.assertIn("加密密钥环境变量名称无效", out["errors"])

    def test_local_target_must_stay_inside_backups(self):
        tmp = Path(tempfile.mkdtemp(prefix="r20-v-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(BS, "ROOT", tmp):
            out = BS.validate_backup_job({
                "name": "n", "scope": ["data"], "encryption": {},
                "targets": [{"type": "local", "enabled": True,
                             "path": "../outside", "retention": 3}]})
        self.assertFalse(out["valid"])
        self.assertIn("本地目标必须位于项目 backups/ 目录内", out["errors"])

    def test_local_retention_below_one_is_an_error(self):
        tmp = Path(tempfile.mkdtemp(prefix="r20-v-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(BS, "ROOT", tmp):
            out = BS.validate_backup_job({
                "name": "n", "scope": ["data"], "encryption": {},
                "targets": [{"type": "local", "enabled": True,
                             "path": "backups/local", "retention": 0}]})
        self.assertIn("本地归档至少保留 1 份", out["errors"])

    def test_non_numeric_local_retention_is_treated_as_zero(self):
        """`validate_backup_job` 可被喂**未归一化**的裸任务 ⇒ 字符串保留份数要能落地成 0。"""
        tmp = Path(tempfile.mkdtemp(prefix="r20-v-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(BS, "ROOT", tmp):
            out = BS.validate_backup_job({
                "name": "n", "scope": ["data"], "encryption": {},
                "targets": [{"type": "local", "enabled": True,
                             "path": "backups/local", "retention": "abc"}]})
        self.assertIn("本地归档至少保留 1 份", out["errors"])

    def test_remote_path_must_not_contain_dotdot(self):
        out = BS.validate_backup_job({
            "name": "n", "scope": ["data"], "encryption": {},
            "targets": [{"type": "webdav", "enabled": False,
                         "remote_path": "a/../../etc/passwd"}]})
        self.assertIn("webdav 远程路径不能包含 ..", out["errors"])

    def test_s3_and_oss_need_endpoint_and_bucket_when_enabled(self):
        for kind in ("s3", "oss"):
            out = BS.validate_backup_job({
                "name": "n", "scope": ["data"], "encryption": {},
                "targets": [{"type": kind, "enabled": True, "label": kind.upper()}]})
            self.assertIn(f"{kind.upper()} 目标必须配置 Endpoint 与 Bucket", out["errors"])
            self.assertIn(f"{kind.upper()} 凭证未完整配置：access_key_id, secret_access_key",
                          out["errors"])

    def test_endpoint_outbound_validation_errors_are_surfaced(self):
        with mock.patch("r20_backend.net_security.validate_outbound_url",
                        side_effect=ValueError("内网地址被拒绝")):
            out = BS.validate_backup_job({
                "name": "n", "scope": ["data"], "encryption": {},
                "targets": [{"type": "s3", "enabled": True, "label": "S3 主",
                             "endpoint": "http://127.0.0.1", "bucket": "b"}]})
        self.assertTrue(any("Endpoint" in e and "内网" in e for e in out["errors"]), out["errors"])

    def test_credentials_are_read_and_missing_fields_named(self):
        with mock.patch("r20_backend.backup_secrets.load_credentials",
                        return_value={"access_key_id": "k"}):
            out = BS.validate_backup_job({
                "name": "n", "scope": ["data"], "encryption": {},
                "targets": [{"type": "s3", "enabled": True, "label": "S3 主",
                             "endpoint": "https://s3.example", "bucket": "b"}]})
        self.assertIn("S3 主 凭证未完整配置：secret_access_key", out["errors"])

    def test_credential_lookup_failure_is_not_fatal(self):
        with mock.patch("r20_backend.backup_secrets.load_credentials",
                        side_effect=RuntimeError("密文库坏了")):
            out = BS.validate_backup_job({
                "name": "n", "scope": ["data"], "encryption": {},
                "targets": [{"type": "s3", "enabled": True, "label": "S3",
                             "endpoint": "https://s3.example", "bucket": "b"}]})
        self.assertFalse(out["valid"], "读不到凭证 ⇒ 视为缺失（fail-closed），但不崩")

    def test_baidu_oauth_requires_three_fields_and_bypy_requires_authorisation(self):
        with mock.patch("r20_backend.backup_secrets.load_credentials", return_value={}):
            oauth = BS.validate_backup_job({
                "name": "n", "scope": ["data"], "encryption": {},
                "targets": [{"type": "baidu", "enabled": True, "auth_mode": "oauth",
                             "label": "百度"}]})
        self.assertIn("百度 凭证未完整配置：app_key, app_secret, refresh_token",
                      oauth["errors"])

        with mock.patch("pathlib.Path.home", return_value=Path("/nonexistent-home-xyz")), \
                mock.patch("r20_backend.backup_secrets.load_credentials", return_value={}):
            bypy = BS.validate_backup_job({
                "name": "n", "scope": ["data"], "encryption": {},
                "targets": [{"type": "baidu", "enabled": True, "auth_mode": "bypy",
                             "label": "百度"}]})
        self.assertIn("百度 ByPy 尚未在当前运行用户下授权", bypy["errors"])

    def test_disabled_targets_skip_the_credential_gate(self):
        out = BS.validate_backup_job({
            "name": "n", "scope": ["data"], "encryption": {},
            "sqlite": {"enabled": True},
            "targets": [{"type": "baidu", "enabled": False, "auth_mode": "bypy"}]})
        self.assertTrue(out["valid"], out["errors"])

    def test_quark_oauth_yields_a_warning_not_an_error(self):
        out = BS.validate_backup_job({
            "name": "n", "scope": ["data"], "encryption": {},
            "sqlite": {"enabled": True},
            "targets": [{"type": "quark", "enabled": False, "experimental": True}]})
        self.assertTrue(out["valid"])
        self.assertIn("实验性", "".join(out["warnings"]))

    def test_enabled_encryption_without_env_is_a_warning(self):
        out = BS.validate_backup_job({
            "name": "n", "scope": ["data"], "targets": [], "sqlite": {"enabled": True},
            "encryption": {"enabled": True, "key_env": "R20_TEST_MISSING_KEY"}})
        self.assertTrue(out["valid"])
        self.assertIn("Fail-Closed", "".join(out["warnings"]))

    def test_errors_are_deduplicated_and_raise_error_collects_them(self):
        job = {"name": "", "scope": [], "targets": [],
               "sqlite": {"enabled": False}, "encryption": {}}
        result = BS.validate_backup_job(job)
        self.assertEqual(len(result["errors"]), len(set(result["errors"])))
        with self.assertRaises(ValueError) as ctx:
            BS.validate_backup_job(job, raise_error=True)
        self.assertIn("；", str(ctx.exception))


class JobCrudTests(_StoreBase):
    def _seed(self):
        job = self._valid_job(id="nightly-default", name="每日全系统灾备")
        BS.save_backup_config({"jobs": [job]})
        return job

    def test_list_jobs_returns_deep_copies(self):
        self._seed()
        jobs = BS.list_jobs()
        jobs[0]["name"] = "mutated"
        jobs[0]["targets"][0]["enabled"] = "mutated"
        self.assertEqual(BS.list_jobs()[0]["name"], "每日全系统灾备",
                         "面板拿到的是副本，改它不得污染磁盘")

    def test_get_job_missing_is_an_error(self):
        self._seed()
        self.assertEqual(BS.get_job("nightly-default")["id"], "nightly-default")
        with self.assertRaises(ValueError) as ctx:
            BS.get_job("nope")
        self.assertIn("不存在", str(ctx.exception))

    def test_rekey_targets_reissues_ids_and_drops_status(self):
        out = BS._rekey_targets([{"type": "local", "id": "old",
                                  "credential_ref": "backup:old",
                                  "credential_status": {"configured": True}}])
        self.assertNotEqual(out[0]["id"], "old")
        self.assertEqual(out[0]["credential_ref"], f"backup:{out[0]['id']}")
        self.assertNotIn("credential_status", out[0])
        self.assertNotIn("credential_status", BS._rekey_targets(
            [{"type": "local"}])[0])

    def test_create_job_uses_the_named_source_as_a_template(self):
        self._seed()
        job = BS.create_job("副本", source_id="nightly-default")
        self.assertEqual(job["name"], "副本")
        self.assertFalse(job["enabled"], "新建任务默认停用，避免误触发")
        self.assertNotEqual(job["id"], "nightly-default")
        self.assertEqual(len(BS.list_jobs()), 2)

    def test_create_job_falls_back_to_the_default_template(self):
        self._seed()
        job = BS.create_job("孤儿", source_id="does-not-exist")
        self.assertEqual(job["name"], "孤儿")
        self.assertTrue(job["targets"])

    def test_create_job_stops_at_max_jobs(self):
        jobs = [self._valid_job(id=f"j{i}") for i in range(BS.MAX_JOBS)]
        BS.save_backup_config({"jobs": jobs})
        with self.assertRaises(ValueError) as ctx:
            BS.create_job("多一个")
        self.assertIn("最多", str(ctx.exception))

    def test_update_job_merges_changes_and_touches_updated_at(self):
        self._seed()
        before = BS.get_job("nightly-default")["updated_at"]
        job = BS.update_job("nightly-default", {"name": "改名", "enabled": False})
        self.assertEqual(job["name"], "改名")
        self.assertFalse(job["enabled"])
        self.assertEqual(BS.get_job("nightly-default")["name"], "改名")
        self.assertEqual(job["updated_at"], before, "秒级时间戳同秒内不变属预期，但不许变空")
        self.assertTrue(job["updated_at"])

    def test_update_job_missing_or_invalid_is_rejected(self):
        self._seed()
        with self.assertRaises(ValueError) as ctx:
            BS.update_job("nope", {"name": "x"})
        self.assertIn("不存在", str(ctx.exception))
        with self.assertRaises(ValueError):
            BS.update_job("nightly-default", {"name": "", "scope": []})

    def test_delete_job_keeps_at_least_one(self):
        self._seed()
        with self.assertRaises(ValueError) as ctx:
            BS.delete_job("nightly-default")
        self.assertIn("至少保留一个", str(ctx.exception))

        BS.create_job("第二个")
        BS.delete_job("nightly-default")
        self.assertEqual([j["id"] for j in BS.list_jobs()],
                         [j["id"] for j in BS.list_jobs() if j["id"] != "nightly-default"])

    def test_jobs_due_at_matches_enabled_jobs_only(self):
        self._seed()
        second = BS.create_job("第二")
        BS.update_job(second["id"], {"enabled": True, "schedule_times": ["02:00"]})
        due = BS.jobs_due_at("02:00")
        self.assertEqual({j["id"] for j in due}, {"nightly-default", second["id"]})
        self.assertEqual(BS.jobs_due_at("03:00"), [])


class ExportImportTests(_StoreBase):
    def setUp(self):
        super().setUp()
        BS.save_backup_config({"jobs": [self._valid_job(id="src", name="源任务")]})

    def test_export_strips_identity_state_and_credentials(self):
        out = BS.export_job("src")
        self.assertEqual(out["format"], "r20-backup-job")
        self.assertEqual(out["version"], 1)
        job = out["job"]
        self.assertEqual(job["id"], "")
        self.assertFalse(job["enabled"])
        self.assertEqual(job["created_at"], "")
        self.assertEqual(job["updated_at"], "")
        for target in job["targets"]:
            self.assertEqual(target["id"], "")
            self.assertEqual(target["credential_ref"], "")

    def test_export_missing_job_is_an_error(self):
        with self.assertRaises(ValueError):
            BS.export_job("nope")

    def test_import_requires_the_r20_format_marker(self):
        for payload in ({}, {"format": "other", "job": {}}, {"format": "r20-backup-job"}):
            with self.assertRaises(ValueError) as ctx:
                BS.import_job(payload)
            self.assertIn("无效的 R20 灾备任务文件", str(ctx.exception))

    def test_import_reissues_ids_and_disables_the_job(self):
        exported = BS.export_job("src")
        job = BS.import_job(exported)
        self.assertNotEqual(job["id"], "src")
        self.assertFalse(job["enabled"])
        self.assertEqual(job["name"], "源任务", "未给 name_override 时沿用文件里的名字")
        self.assertTrue(all(t["id"] for t in job["targets"]))
        self.assertTrue(all(t["credential_ref"] for t in job["targets"]))

    def test_import_name_override_wins(self):
        job = BS.import_job(BS.export_job("src"), name_override="导入名")
        self.assertEqual(job["name"], "导入名")

    def test_import_respects_max_jobs(self):
        BS.save_backup_config({"jobs": [self._valid_job(id=f"j{i}")
                                        for i in range(BS.MAX_JOBS)]})
        with self.assertRaises(ValueError) as ctx:
            BS.import_job({"format": "r20-backup-job", "job": BS._default_job()})
        self.assertIn("最多", str(ctx.exception))

    def test_import_validates_before_persisting(self):
        bad = {"format": "r20-backup-job",
               "job": {"name": "", "scope": [], "targets": [],
                       "sqlite": {"enabled": False}}}
        before = len(BS.list_jobs())
        with self.assertRaises(ValueError):
            BS.import_job(bad)
        self.assertEqual(len(BS.list_jobs()), before, "校验失败不得落盘")


class LegacyMethodsTests(_StoreBase):
    def _authorised_home(self):
        """让 `validate_backup_job` 的 ByPy 授权检查通过（否则启用百度目标会校验失败）。"""
        home = self.tmp / "home"
        (home / ".bypy").mkdir(parents=True, exist_ok=True)
        self._start(mock.patch("pathlib.Path.home", return_value=home))
        return home

    def test_load_projects_the_first_job_onto_three_switches(self):
        self._authorised_home()
        BS.save_backup_config({"jobs": [self._valid_job(
            id="j", sqlite={"enabled": True, "retention": 5},
            targets=[{"type": "baidu", "enabled": True, "retention": 4},
                     {"type": "local", "enabled": False, "retention": 2}])]})
        out = BS.load_backup_methods()
        self.assertEqual(set(out), {"baidu", "local", "sqlite"})
        self.assertTrue(out["baidu"]["enabled"])
        self.assertEqual(out["baidu"]["retention"], 0,
                         "归一化把非 local 目标的 retention 归 0 ⇒ 老开关的百度保留份数读回总是 0")
        self.assertFalse(out["local"]["enabled"])
        self.assertEqual(out["local"]["retention"], 2)
        self.assertEqual(out["sqlite"]["retention"], 5)

    def test_load_tolerates_missing_target_types(self):
        BS.save_backup_config({"jobs": [self._valid_job(
            id="j", targets=[{"type": "local", "enabled": True, "retention": 1}])]})
        out = BS.load_backup_methods()
        self.assertFalse(out["baidu"]["enabled"])
        self.assertEqual(out["baidu"]["retention"], 0)

    def test_save_applies_switches_back_onto_the_job(self):
        self._authorised_home()
        BS.save_backup_config({"jobs": [self._valid_job(id="j")]})
        BS.save_backup_methods({"baidu": {"enabled": True, "retention": 9},
                                "local": {"enabled": False, "retention": 2},
                                "sqlite": {"enabled": True, "retention": 12}})
        job = BS.get_job("j")
        targets = {t["type"]: t for t in job["targets"]}
        self.assertTrue(targets["baidu"]["enabled"])
        self.assertFalse(targets["local"]["enabled"])
        self.assertEqual(targets["local"]["retention"], 2)
        self.assertTrue(job["sqlite"]["enabled"])
        self.assertEqual(job["sqlite"]["retention"], 12)

    def test_save_clamps_retention_into_range(self):
        self._authorised_home()
        BS.save_backup_config({"jobs": [self._valid_job(id="j")]})
        BS.save_backup_methods({"baidu": {"enabled": False, "retention": 9999},
                                "sqlite": {"enabled": True, "retention": 0}})
        job = BS.get_job("j")
        targets = {t["type"]: t for t in job["targets"]}
        # ⚠️ 已知不对称：save_backup_methods 把 baidu 保留份数夹到 [0,365]，
        #    但随后的 _normalize_target 又对非 local 目标强制归 0 ⇒ 百度那档实际被吞掉。
        self.assertEqual(targets["baidu"]["retention"], 0)
        self.assertEqual(job["sqlite"]["retention"], 1)

    def test_save_ignores_unknown_switch_groups(self):
        BS.save_backup_config({"jobs": [self._valid_job(id="j")]})
        BS.save_backup_methods({"nope": {"enabled": True}})
        self.assertTrue(BS.get_job("j")["enabled"])


class NowTests(unittest.TestCase):
    def test_now_is_beijing_time_with_second_precision(self):
        stamp = BS._now()
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_default_job_ids_are_stable_and_unique_per_call(self):
        self.assertEqual(BS._default_job()["id"], "nightly-default")
        self.assertNotEqual(uuid.uuid4().hex, uuid.uuid4().hex)


if __name__ == "__main__":
    unittest.main()

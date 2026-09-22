"""灾备运行时（`scripts/backup_runtime.py`）的内部路径收口 —— 第 320 刀。

本模块 483 行、14 个公开函数，此前**没有专属测试文件**（引用者全是审计门与抽取门）。
本刀补齐 52 行缺口：**每一条 fail-closed 闸门、每一处静默 `pass`、每个薄壳转发**。

## 沙箱纪律

`ROOT` / `BACKUPS` / `LOCAL_DIR` / `SQLITE_DIR` / `MANIFEST_DIR` **全部**在
`setUp` 里指向临时目录 —— 本模块真会 `shutil.copy2`、建 SQLite、写 manifest、
`os.chmod(0o600)`，绝不能让它在真实 `backups/` 上跑。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scripts.backup_runtime as br  # noqa: E402


class _Sandbox(unittest.TestCase):
    """把整个模块的路径常量钉进临时目录。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "backups").mkdir(parents=True, exist_ok=True)
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        for name, value in (("ROOT", self.root),
                            ("BACKUPS", self.root / "backups"),
                            ("LOCAL_DIR", self.root / "backups" / "local"),
                            ("SQLITE_DIR", self.root / "backups" / "sqlite"),
                            ("MANIFEST_DIR", self.root / "backups" / "manifests")):
            p = patch.object(br, name, value)
            p.start()
            self.addCleanup(p.stop)

    def _file(self, rel, content=b"x", mtime=None):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path


class CleanStaleStagingTests(_Sandbox):
    def test_absent_staging_directory_yields_zero(self):
        self.assertEqual(br.clean_stale_staging(), 0)

    def test_old_staging_files_are_removed(self):
        staging = self.root / "backups" / "staging"
        staging.mkdir(parents=True)
        old = staging / "r20_backup_a_20260101.tar.gz"
        old.write_bytes(b"old")
        os.utime(old, (time.time() - 7200, time.time() - 7200))
        self.assertEqual(br.clean_stale_staging(max_age_seconds=3600), 1)
        self.assertFalse(old.exists())

    def test_fresh_in_flight_archives_are_never_touched(self):
        # 审计③：旧条件「size==0 或 过期」会误删并发任务**正在写**的在途归档
        staging = self.root / "backups" / "staging"
        staging.mkdir(parents=True)
        live = staging / "r20_backup_a_20260101.tar.gz"
        live.write_bytes(b"")          # 刚 mkstemp、还没写内容
        self.assertEqual(br.clean_stale_staging(max_age_seconds=3600), 0)
        self.assertTrue(live.exists(), "在途空文件不许被当垃圾删掉")

    def test_unrelated_names_are_ignored(self):
        staging = self.root / "backups" / "staging"
        staging.mkdir(parents=True)
        keep = staging / "something_else.tar.gz"
        keep.write_bytes(b"x")
        os.utime(keep, (time.time() - 9999, time.time() - 9999))
        self.assertEqual(br.clean_stale_staging(max_age_seconds=1), 0)
        self.assertTrue(keep.exists())

    def test_stat_failure_is_swallowed(self):
        # ★ 第 80 行 `pass` —— 单个条目查不动不许把整轮清理打挂
        staging = self.root / "backups" / "staging"
        staging.mkdir(parents=True)
        (staging / "r20_backup_a_x.tar.gz").write_bytes(b"x")

        class _Bad:
            def is_file(self):
                raise OSError("stat 挂了")
        with patch.object(Path, "glob", lambda self, pat: [_Bad()]):
            self.assertEqual(br.clean_stale_staging(), 0)


class PruneTests(_Sandbox):
    def test_newest_entries_are_kept(self):
        now = time.time()
        paths = [self._file(f"backups/local/f{i}.tar", mtime=now - i * 100)
                 for i in range(3)]
        br.prune(paths, 2)
        self.assertFalse(paths[2].exists(), "最旧的被裁")
        self.assertTrue(paths[0].exists())
        self.assertTrue(paths[1].exists())

    def test_retention_zero_removes_everything(self):
        paths = [self._file(f"backups/local/g{i}.tar", mtime=time.time() - i)
                 for i in range(2)]
        br.prune(paths, 0)
        self.assertTrue(all(not p.exists() for p in paths))

    def test_negative_retention_is_clamped_by_max(self):
        p = self._file("backups/local/h.tar", mtime=time.time())
        br.prune([p], -5)
        self.assertFalse(p.exists())

    def test_missing_paths_do_not_block_pruning_of_real_ones(self):
        # 混合"存在"与"不存在"：不存在的要静默跳过，存在的仍按保留数正常裁剪
        keep = self._file("backups/local/keep.tar", mtime=time.time())
        drop = self._file("backups/local/drop.tar", mtime=0)
        gone = self.root / "backups" / "nope.tar"
        br.prune([gone, keep, drop], 1)
        self.assertTrue(keep.exists())
        self.assertFalse(drop.exists(), "不存在的那个不许影响真实条目的排序裁剪")
        self.assertFalse(gone.exists())

    def test_stat_failure_skips_only_that_entry(self):
        # ★ 第 91 行 `pass` —— 一个查不动的条目被跳过，**其余照常裁剪**
        class _Bad:
            def exists(self):
                return True
            def is_file(self):
                raise OSError("boom")

        drop = self._file("backups/local/x.tar", mtime=0)
        br.prune([_Bad(), drop], 0)
        self.assertFalse(drop.exists(), "坏条目不许让整轮裁剪放弃")

    def test_unlink_failure_is_swallowed(self):
        # ★ 第 97 行 `pass`
        p = self._file("backups/local/i.tar", mtime=0)
        with patch.object(Path, "unlink", side_effect=OSError("read-only")):
            br.prune([p], 0)
        self.assertTrue(p.exists(), "删不掉就留着，不许抛")


class RetainLocalArchiveTests(_Sandbox):
    def test_a_missing_source_is_refused(self):
        # ★ 第 102 行
        with self.assertRaises(RuntimeError) as ctx:
            br.retain_local_archive(self.root / "backups" / "nope.tar", 3)
        self.assertIn("不存在或无效", str(ctx.exception))

    def test_a_directory_source_is_refused(self):
        d = self.root / "backups" / "adir"
        d.mkdir(parents=True)
        with self.assertRaises(RuntimeError):
            br.retain_local_archive(d, 3)

    def test_a_destination_outside_backups_is_refused(self):
        # ★ 第 106 行 —— 灾备归档不许写到 backups/ 之外
        src = self._file("backups/staging/r20_backup_j_20260101.tar.gz")
        with self.assertRaises(RuntimeError) as ctx:
            br.retain_local_archive(src, 3, self.root / "elsewhere")
        self.assertIn("必须位于 backups/ 目录下", str(ctx.exception))

    def test_archive_is_copied_into_the_destination(self):
        src = self._file("backups/staging/r20_backup_j_20260101.tar.gz", b"payload")
        dest = br.retain_local_archive(src, 3, self.root / "backups" / "local")
        self.assertEqual(dest.read_bytes(), b"payload")
        self.assertTrue(src.exists(), "复制不是移动")

    def test_prune_is_scoped_to_the_same_job_prefix(self):
        # 审计③：任务 B（retention=1）跑一次不许裁掉任务 A 的最新归档
        now = time.time()
        self._file("backups/local/r20_backup_a_20260101_0000.tar.gz", b"a1", mtime=now - 300)
        self._file("backups/local/r20_backup_a_20260102_0000.tar.gz", b"a2", mtime=now - 200)
        self._file("backups/local/r20_backup_B_20260101_0000.tar.gz", b"b1", mtime=now - 100)
        src = self._file("backups/staging/r20_backup_B_20260103_0000.tar.gz", b"b2")
        br.retain_local_archive(src, 1, self.root / "backups" / "local")
        left = sorted(p.name for p in (self.root / "backups" / "local").glob("*.tar.gz"))
        self.assertIn("r20_backup_a_20260102_0000.tar.gz", left, "任务 A 的最新归档要留着")
        self.assertIn("r20_backup_B_20260103_0000.tar.gz", left, "任务 B 自己刚落盘的也要留")
        self.assertNotIn("r20_backup_B_20260101_0000.tar.gz", left, "任务 B 的旧的才该裁")


class SqliteHotBackupTests(_Sandbox):
    def _make_db(self, name, table="t"):
        path = self.root / "data" / name
        conn = sqlite3.connect(str(path))
        conn.execute(f"CREATE TABLE {table} (a int)")
        conn.execute(f"INSERT INTO {table} VALUES (1)")
        conn.commit()
        conn.close()
        return path

    def test_a_destination_outside_backups_is_refused(self):
        # ★ 第 123 行
        with self.assertRaises(RuntimeError) as ctx:
            br.sqlite_hot_backups("20260101_0000", 3, self.root / "elsewhere")
        self.assertIn("必须位于 backups/ 目录下", str(ctx.exception))

    def test_a_missing_data_directory_yields_nothing(self):
        # ★ 第 128 行
        (self.root / "data").rmdir()
        self.assertEqual(br.sqlite_hot_backups("20260101_0000", 3), [])

    def test_admin_db_is_skipped(self):
        # ★ 第 132 行 —— admin 库含凭证，绝不进灾备包
        self._make_db("r20_admin.db")
        self._make_db("r20_quant.db")
        created = br.sqlite_hot_backups("20260101_0000", 3, self.root / "backups" / "sqlite")
        self.assertEqual([p.name for p in created], ["r20_quant_20260101_0000.db"])

    def test_wal_and_shm_sidecars_are_skipped(self):
        # ★ 第 134 行
        (self.root / "data" / "r20_quant.db-wal").write_bytes(b"w")
        (self.root / "data" / "r20_quant.db-shm").write_bytes(b"s")
        self._make_db("r20_quant.db")
        created = br.sqlite_hot_backups("20260101_0000", 3, self.root / "backups" / "sqlite")
        self.assertEqual(len(created), 1)

    def test_backup_is_a_usable_database(self):
        self._make_db("r20_quant.db")
        created = br.sqlite_hot_backups("20260101_0000", 3, self.root / "backups" / "sqlite")
        conn = sqlite3.connect(str(created[0]))
        try:
            self.assertEqual(conn.execute("SELECT a FROM t").fetchall(), [(1,)])
        finally:
            conn.close()

    def test_copied_database_is_owner_only(self):
        self._make_db("r20_quant.db")
        created = br.sqlite_hot_backups("20260101_0000", 3, self.root / "backups" / "sqlite")
        self.assertEqual(created[0].stat().st_mode & 0o777, 0o600)

    def test_readonly_uri_connect_failure_falls_back_to_a_plain_connect(self):
        # ★ 第 140 行 —— 只读 URI 打不开时回落普通连接（活动库也能热备）
        self._make_db("r20_quant.db")
        real_connect = sqlite3.connect
        calls = []

        def connect(target, *a, **kw):
            calls.append(target)
            if "mode=ro" in str(target):
                raise sqlite3.OperationalError("URI 不支持")
            return real_connect(target, *a, **kw)

        with patch.object(br.sqlite3, "connect", connect):
            created = br.sqlite_hot_backups("20260101_0000", 3,
                                            self.root / "backups" / "sqlite")
        self.assertEqual(len(created), 1)
        self.assertTrue(any("mode=ro" in str(c) for c in calls))
        self.assertTrue(any("mode=ro" not in str(c) for c in calls))

    def test_backup_failure_removes_the_partial_file_and_raises(self):
        # ★ 第 153 行 —— 半截备份必须删掉，且**大声失败**（不许静默漏一个库）
        self._make_db("r20_quant.db")

        class _BadConn:
            def backup(self, other):
                raise sqlite3.OperationalError("disk full")

            def close(self):
                pass

        real_connect = sqlite3.connect
        state = {"first": True}

        def connect(target, *a, **kw):
            if state["first"]:
                state["first"] = False
                return _BadConn()
            return real_connect(target, *a, **kw)

        sqlite_dir = self.root / "backups" / "sqlite"
        with patch.object(br.sqlite3, "connect", connect):
            with self.assertRaises(RuntimeError) as ctx:
                br.sqlite_hot_backups("20260101_0000", 3, sqlite_dir)
        self.assertIn("热备份失败", str(ctx.exception))
        self.assertEqual(list(sqlite_dir.glob("*.db")), [], "半截文件要清掉")

    def test_prune_keeps_only_the_retention_window(self):
        self._make_db("r20_quant.db")
        sqlite_dir = self.root / "backups" / "sqlite"
        for stamp in ("20260101_0000", "20260102_0000", "20260103_0000"):
            br.sqlite_hot_backups(stamp, 2, sqlite_dir)
            time.sleep(0.01)
        self.assertEqual(len(list(sqlite_dir.glob("*.db"))), 2)


class DeadLineTests(_Sandbox):
    """⚠️ **机器证明的不可覆盖行**：`sqlite_hot_backups` 第 134 行的 `continue`。

    第 130 行是 `for source in data_dir.glob("*.db")` —— **只产出以 `.db` 结尾的名字**；
    而第 133 行的判据是 `name.endswith("-wal") or name.endswith("-shm")` ——
    **要求以 `-wal`/`-shm` 结尾**。同一个字符串不可能同时以 `.db` 和 `-wal` 结尾，
    所以那个条件**恒为假**，`continue` **物理不可达**。

    > 与 `trader/venue_protection.py:521` 同一性质：这是"防御性代码写在了错误的位置"。
    > 真正的 `-wal`/`-shm` 侧车文件（`r20_quant.db-wal`）**根本不会进入这个循环**，
    > 所以它们也不会被热备份（那是另一件事，且现有行为是对的 —— 侧车由 SQLite
    > 的 `backup()` 在目标库里重建，不该被当独立库复制）。
    """

    def setUp(self):
        super().setUp()
        (self.root / "data" / "r20_quant.db-wal").write_bytes(b"w")
        (self.root / "data" / "r20_quant.db-shm").write_bytes(b"s")

    def test_no_filename_can_satisfy_both_conditions(self):
        candidates = ["r20_quant.db", "r20_quant.db-wal", "r20_quant.db-shm",
                      "x-wal", "x-shm", "x.db", "-wal", "a-wal.db"]
        for name in candidates:
            with self.subTest(name=name):
                # 能进循环 ⇔ 以 .db 结尾；能进 continue 分支 ⇔ 以 -wal/-shm 结尾
                self.assertFalse(name.endswith(".db")
                                 and (name.endswith("-wal") or name.endswith("-shm")),
                                 f"{name} 竟然同时满足两个条件 —— 结论需要修正")

    def test_the_glob_pattern_only_ever_yields_dot_db_names(self):
        import ast
        tree = ast.parse(Path(br.__file__).read_text(encoding="utf-8"))
        globs = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "glob"]
        # 只取**字面量**模式（另有 f-string 形式的 glob，那是动态拼接的）
        patterns = [n.args[0].value for n in globs
                    if n.args and isinstance(n.args[0], ast.Constant)]
        self.assertIn("*.db", patterns, "循环来源必须是 *.db 的 glob")

    def test_the_wal_and_shm_sidecars_never_enter_the_loop(self):
        # 行为证据：侧车文件存在，但循环体只处理 `.db`
        self.assertTrue((self.root / "data" / "r20_quant.db-wal").exists())
        self.assertEqual([p.name for p in (self.root / "data").glob("*.db")], [])

    def test_sidecars_are_not_copied_as_standalone_databases(self):
        br.sqlite_hot_backups("20260101_0000", 3, self.root / "backups" / "sqlite")
        names = [p.name for p in (self.root / "backups" / "sqlite").glob("*")]
        self.assertFalse(any("wal" in n or "shm" in n for n in names), names)

    def test_the_admin_db_skip_next_to_it_is_reachable(self):
        # 对照：紧邻的第 132 行（跳过 admin 库）**是**可达的 —— 证明区别在 glob 模式
        sqlite3.connect(str(self.root / "data" / "r20_admin.db")).close()
        self.assertEqual(br.sqlite_hot_backups("20260101_0000", 3,
                                               self.root / "backups" / "sqlite"), [])


class CreateArchiveTests(_Sandbox):
    def setUp(self):
        super().setUp()
        (self.root / "data").mkdir(exist_ok=True)
        (self.root / "data" / "kept.json").write_text("{}", encoding="utf-8")

    def _job(self, **over):
        job = {"id": "nightly", "scope": ["data"], "exclude": []}
        job.update(over)
        return job

    def test_archive_contains_the_scope_root(self):
        path, included = br.create_archive(self._job(), "20260101_0000")
        self.assertEqual(included, ["data"])
        self.assertTrue(path.name.startswith("r20_backup_nightly_"))
        with tarfile.open(path) as tf:
            self.assertTrue(any(n.startswith("data/") for n in tf.getnames()))

    def test_archive_is_owner_only(self):
        path, _ = br.create_archive(self._job(), "20260101_0000")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_mandatory_excludes_are_always_applied(self):
        (self.root / "data" / "r20_admin.db").write_bytes(b"creds")
        (self.root / "data" / "llm_models.json").write_text("{}", encoding="utf-8")
        path, _ = br.create_archive(self._job(), "20260101_0000")
        with tarfile.open(path) as tf:
            names = tf.getnames()
        self.assertFalse(any("r20_admin.db" in n for n in names), "admin 库禁入")
        self.assertFalse(any("llm_models.json" in n for n in names), "审计A3：LLM 明文键禁入")

    def test_job_level_excludes_are_applied(self):
        path, _ = br.create_archive(self._job(exclude=["data/kept.json"]), "20260101_0000")
        with tarfile.open(path) as tf:
            self.assertFalse(any("kept.json" in n for n in tf.getnames()))

    def test_a_bad_compression_level_falls_back_to_six(self):
        # ★ 第 189 行
        for bad in ("not-a-number", None, {}):
            with self.subTest(bad=bad):
                path, _ = br.create_archive(self._job(compression_level=bad),
                                            "20260101_0000")
                self.assertTrue(path.exists())

    def test_compression_level_is_clamped_to_one_through_nine(self):
        for given in (-5, 0, 99):
            with self.subTest(given=given):
                path, _ = br.create_archive(self._job(compression_level=given),
                                            "20260101_0000")
                self.assertTrue(path.exists())

    def test_an_unknown_scope_contributes_nothing(self):
        # ★ 第 203 行 —— 一个文件都没选上时必须**大声失败**，不许产出一个空包
        with self.assertRaises(RuntimeError) as ctx:
            br.create_archive(self._job(scope=["no_such_scope"]), "20260101_0000")
        self.assertIn("没有可归档文件", str(ctx.exception))
        staging = self.root / "backups" / "staging"
        self.assertEqual(list(staging.glob("*.tar.gz")), [], "空包要删掉")

    def test_a_non_existent_scope_path_is_skipped(self):
        with self.assertRaises(RuntimeError):
            br.create_archive(self._job(scope=["recovery_guide"]), "20260101_0000")

    def test_add_failure_is_swallowed_and_counted_as_not_included(self):
        # ★ 第 200 行 `pass`
        def boom(self, *a, **kw):
            raise FileNotFoundError("race")
        with patch.object(tarfile.TarFile, "add", boom):
            with self.assertRaises(RuntimeError):
                br.create_archive(self._job(), "20260101_0000")

    def test_job_id_is_sanitised_into_the_filename(self):
        path, _ = br.create_archive(self._job(id="../../etc/passwd"),
                                    "20260101_0000")
        self.assertNotIn("..", path.name)
        self.assertNotIn("/", path.name)


class DeriveKeyTests(unittest.TestCase):
    def test_a_short_secret_is_refused(self):
        # ★ 第 210 行
        with self.assertRaises(RuntimeError) as ctx:
            br._derive_key("short", b"0" * 16)
        self.assertIn("至少 16 个字符", str(ctx.exception))

    def test_a_sixteen_character_secret_is_accepted(self):
        self.assertEqual(len(br._derive_key("x" * 16, b"0" * 16)), 32)

    def test_the_same_salt_and_secret_give_the_same_key(self):
        self.assertEqual(br._derive_key("y" * 20, b"1" * 16),
                         br._derive_key("y" * 20, b"1" * 16))

    def test_a_different_salt_gives_a_different_key(self):
        self.assertNotEqual(br._derive_key("y" * 20, b"1" * 16),
                            br._derive_key("y" * 20, b"2" * 16))


class EncryptionRoundTripTests(_Sandbox):
    KEY_ENV = "R20_TEST_BACKUP_KEY"

    def setUp(self):
        super().setUp()
        self.secret = "a-very-long-test-secret"
        env = patch.dict(os.environ, {self.KEY_ENV: self.secret})
        env.start()
        self.addCleanup(env.stop)
        self.src = self._file("backups/staging/plain.tar.gz", b"PAYLOAD" * 100)

    def test_round_trip_recovers_the_original_bytes(self):
        enc = br.encrypt_archive(self.src, self.KEY_ENV)
        self.assertTrue(enc.name.endswith(".aes256"))
        self.assertFalse(self.src.exists(), "加密后明文要删掉")
        out = br.decrypt_archive(enc, self.KEY_ENV, self.root / "backups" / "plain.tar.gz")
        self.assertEqual(out.read_bytes(), b"PAYLOAD" * 100)

    def test_the_encrypted_file_starts_with_the_magic_header(self):
        enc = br.encrypt_archive(self.src, self.KEY_ENV)
        self.assertTrue(enc.read_bytes().startswith(br.MAGIC))

    def test_both_artifacts_are_owner_only(self):
        enc = br.encrypt_archive(self.src, self.KEY_ENV)
        self.assertEqual(enc.stat().st_mode & 0o777, 0o600)
        out = br.decrypt_archive(enc, self.KEY_ENV, self.root / "backups" / "out.tar.gz")
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)

    def test_encrypting_without_the_env_var_is_refused(self):
        with patch.dict(os.environ, {self.KEY_ENV: ""}):
            with self.assertRaises(RuntimeError) as ctx:
                br.encrypt_archive(self.src, self.KEY_ENV)
        self.assertIn("未配置", str(ctx.exception))
        self.assertTrue(self.src.exists(), "拒绝时不许动明文")

    def test_decrypting_without_the_env_var_is_refused(self):
        # ★ 第 245 行
        enc = br.encrypt_archive(self.src, self.KEY_ENV)
        with patch.dict(os.environ, {self.KEY_ENV: ""}):
            with self.assertRaises(RuntimeError) as ctx:
                br.decrypt_archive(enc, self.KEY_ENV, self.root / "backups" / "out.tar.gz")
        self.assertIn("解密需要环境变量", str(ctx.exception))

    def test_a_wrong_magic_is_refused(self):
        # ★ 第 248 行
        bad = self._file("backups/staging/not-r20.aes256", b"NOTMAGIC" + b"0" * 64)
        with self.assertRaises(RuntimeError) as ctx:
            br.decrypt_archive(bad, self.KEY_ENV, self.root / "backups" / "out.tar.gz")
        self.assertIn("不是受支持的 R20 AES-256-GCM 归档", str(ctx.exception))

    def test_a_truncated_header_is_refused(self):
        # ★ 第 251 行
        bad = self._file("backups/staging/short.aes256", br.MAGIC + b"0" * 5)
        with self.assertRaises(RuntimeError) as ctx:
            br.decrypt_archive(bad, self.KEY_ENV, self.root / "backups" / "out.tar.gz")
        self.assertIn("头损坏", str(ctx.exception))

    def test_a_tampered_body_fails_the_gcm_tag(self):
        enc = br.encrypt_archive(self.src, self.KEY_ENV)
        raw = bytearray(enc.read_bytes())
        raw[-1] ^= 0xFF
        enc.write_bytes(bytes(raw))
        with self.assertRaises(Exception):
            br.decrypt_archive(enc, self.KEY_ENV, self.root / "backups" / "out.tar.gz")


class VerifyArchiveTests(_Sandbox):
    def setUp(self):
        super().setUp()
        self.good = self._file("backups/good.tar", b"payload")
        self.tarball = self.root / "backups" / "ok.tar.gz"
        inner = self.root / "data" / "a.txt"
        inner.parent.mkdir(parents=True, exist_ok=True)
        inner.write_text("hi", encoding="utf-8")
        with tarfile.open(self.tarball, "w:gz") as tf:
            tf.add(inner, arcname="data/a.txt")

    def test_a_missing_archive_is_refused(self):
        with self.assertRaises(RuntimeError) as ctx:
            br.verify_archive(self.root / "backups" / "nope.tar.gz")
        self.assertIn("不存在", str(ctx.exception))

    def test_a_good_tarball_reports_its_members_and_roots(self):
        report = br.verify_archive(self.tarball)
        self.assertTrue(report["valid"])
        self.assertFalse(report["encrypted"])
        self.assertEqual(report["roots"], ["data"])
        self.assertGreaterEqual(report["members"], 1)

    def test_a_sha256_mismatch_is_refused(self):
        with self.assertRaises(RuntimeError) as ctx:
            br.verify_archive(self.tarball, "0" * 64)
        self.assertIn("SHA256 校验失败", str(ctx.exception))

    def test_a_matching_sha256_passes(self):
        digest = br.calculate_sha256(self.tarball)
        self.assertTrue(br.verify_archive(self.tarball, digest)["valid"])

    def test_a_non_gzip_file_is_reported_as_corrupt(self):
        with self.assertRaises(RuntimeError) as ctx:
            br.verify_archive(self.good)
        self.assertIn("损坏或不是有效 gzip", str(ctx.exception))

    def _tar_with(self, name, *, linkname=None, kind=tarfile.REGTYPE):
        path = self.root / "backups" / "evil.tar.gz"
        with tarfile.open(path, "w:gz") as tf:
            info = tarfile.TarInfo(name)
            info.type = kind
            if linkname is not None:
                info.linkname = linkname
            tf.addfile(info, None)
        return path

    def test_an_absolute_path_member_is_refused(self):
        with self.assertRaises(RuntimeError) as ctx:
            br.verify_archive(self._tar_with("/etc/passwd"))
        self.assertIn("绝对路径", str(ctx.exception))

    def test_a_dotdot_member_is_refused(self):
        with self.assertRaises(RuntimeError) as ctx:
            br.verify_archive(self._tar_with("data/../../etc/passwd"))
        self.assertIn("路径逃逸", str(ctx.exception))

    def test_a_symlink_escaping_the_root_is_refused(self):
        # ★ 第 297 行
        with self.assertRaises(RuntimeError) as ctx:
            br.verify_archive(self._tar_with("data/link", linkname="/etc/passwd",
                                             kind=tarfile.SYMTYPE))
        self.assertIn("不安全符号链接", str(ctx.exception))

    def test_a_symlink_with_dotdot_is_refused(self):
        with self.assertRaises(RuntimeError) as ctx:
            br.verify_archive(self._tar_with("data/link", linkname="../../etc",
                                             kind=tarfile.SYMTYPE))
        self.assertIn("不安全符号链接", str(ctx.exception))

    def test_a_symlink_inside_the_root_is_allowed(self):
        # ★ 第 299 行的 `elif` 只在**越界**时记违规；指向库内的软链是合法的
        path = self.root / "backups" / "inroot.tar.gz"
        with tarfile.open(path, "w:gz") as tf:
            base = tarfile.TarInfo("data/real.txt")
            base.size = 0
            tf.addfile(base, None)
            link = tarfile.TarInfo("data/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "real.txt"
            tf.addfile(link, None)
        self.assertTrue(br.verify_archive(path)["valid"])

    def test_a_device_node_is_refused(self):
        with self.assertRaises(RuntimeError) as ctx:
            br.verify_archive(self._tar_with("data/dev", kind=tarfile.CHRTYPE))
        self.assertIn("特殊设备节点", str(ctx.exception))

    def test_an_encrypted_archive_is_decrypted_for_inspection_only(self):
        secret_env = "R20_TEST_VERIFY_KEY"
        with patch.dict(os.environ, {secret_env: "z" * 20}):
            enc = br.encrypt_archive(self.tarball, secret_env)
            report = br.verify_archive(enc, "", secret_env)
        self.assertTrue(report["valid"])
        self.assertTrue(report["encrypted"])
        self.assertEqual(list((self.root / "backups").glob("r20-verify-*.tar.gz")), [],
                         "临时解密文件必须清掉")


class ThinShellTests(_Sandbox):
    """六个薄壳必须**真的转发**到 `backup_upload`（门面接缝全靠它们）。"""

    def test_upload_baidu_forwards(self):
        sentinel = {"ok": 1}
        with patch.object(br, "_up_upload_baidu", lambda s, t: sentinel) as _:
            self.assertIs(br.upload_baidu(Path("x"), {"a": 1}), sentinel)

    def test_credentials_forwards(self):
        with patch.object(br, "_up_credentials", lambda t: {"k": "v"}):
            self.assertEqual(br._credentials({}), {"k": "v"})

    def test_urlencoded_json_forwards_every_argument(self):
        seen = {}

        def fake(url, data=None, timeout=60):
            seen.update({"url": url, "data": data, "timeout": timeout})
            return {"r": 1}
        with patch.object(br, "_up_urlencoded_json", fake):
            self.assertEqual(br._urlencoded_json("http://x", {"a": 1}, 7), {"r": 1})
        self.assertEqual(seen, {"url": "http://x", "data": {"a": 1}, "timeout": 7})

    def test_multipart_upload_forwards_positional_and_keyword(self):
        seen = {}

        def fake(*a, **kw):
            seen.update({"a": a, "kw": kw})
            return "done"
        with patch.object(br, "_up_multipart_upload", fake):
            self.assertEqual(br._multipart_upload(1, 2, x=3), "done")
        self.assertEqual(seen, {"a": (1, 2), "kw": {"x": 3}})

    def test_upload_oss_injects_credentials(self):
        seen = {}

        def fake(source, target, _credentials=None):
            seen["creds"] = _credentials
            return {"ok": True}
        with patch.object(br, "_up_upload_oss", fake):
            br.upload_oss(Path("x"), {})
        self.assertIs(seen["creds"], br._credentials)

    def test_upload_webdav_injects_credentials(self):
        seen = {}

        def fake(source, target, _credentials=None):
            seen["creds"] = _credentials
            return {"ok": True}
        with patch.object(br, "_up_upload_webdav", fake):
            br.upload_webdav(Path("x"), {})
        self.assertIs(seen["creds"], br._credentials)

    def test_upload_s3_injects_checksum_and_credentials(self):
        seen = {}

        def fake(source, target, calculate_sha256=None, _credentials=None):
            seen.update({"sha": calculate_sha256, "creds": _credentials})
            return {"ok": True}
        with patch.object(br, "_up_upload_s3", fake):
            br.upload_s3(Path("x"), {})
        self.assertIs(seen["sha"], br.calculate_sha256)
        self.assertIs(seen["creds"], br._credentials)

    def test_upload_baidu_oauth_injects_all_three_helpers(self):
        seen = {}

        def fake(source, target, _credentials=None, _urlencoded_json=None,
                 _multipart_upload=None):
            seen.update({"c": _credentials, "u": _urlencoded_json, "m": _multipart_upload})
            return {"ok": True}
        with patch.object(br, "_up_upload_baidu_oauth", fake):
            br.upload_baidu_oauth(Path("x"), {})
        self.assertIs(seen["c"], br._credentials)
        self.assertIs(seen["u"], br._urlencoded_json)
        self.assertIs(seen["m"], br._multipart_upload)

    def test_calculate_sha256_matches_a_manual_digest(self):
        import hashlib
        p = self._file("backups/hashme.bin", b"abc")
        self.assertEqual(br.calculate_sha256(p), hashlib.sha256(b"abc").hexdigest())


class DeliverTargetTests(_Sandbox):
    """"重试循环"这几条要把出站校验打桩 —— 否则 `validate_outbound_url` 会去
    **真的做 DNS 解析**（离网环境必挂），而这里测的不是校验本身。"""

    def setUp(self):
        super().setUp()
        self.src = self._file("backups/staging/r20_backup_j_20260101.tar.gz", b"payload")
        import r20_backend.net_security as net
        self._net = net
        self._real_validate = net.validate_outbound_url
        p = patch.object(net, "validate_outbound_url", lambda url, **kw: url)
        p.start()
        self.addCleanup(p.stop)

    def test_a_plain_http_endpoint_is_refused_before_any_upload(self):
        # `validate_outbound_url` 在**重试循环之前**执行 ⇒ 地址不合法就一次都不发。
        # 这条要放**真实**校验器（其余用例把它打桩了，避免真做 DNS 解析）。
        with patch.object(self._net, "validate_outbound_url", self._real_validate):
            with self.assertRaises(ValueError):
                br.deliver_target(self.src, {"type": "s3", "endpoint": "http://plain"})

    def test_an_unsupported_target_type_is_refused(self):
        # ★ 第 399/400 行
        with patch.object(br, "upload_s3", None), patch.object(br, "upload_oss", None), \
             patch.object(br, "upload_webdav", None):
            with self.assertRaises(RuntimeError) as ctx:
                br.deliver_target(self.src, {"type": "quantum-teleport"})
        self.assertIn("不支持的灾备目标", str(ctx.exception))

    def test_local_target_outside_backups_is_refused(self):
        # ★ 第 392 行
        with self.assertRaises(RuntimeError) as ctx:
            br.deliver_target(self.src, {"type": "local", "path": "../escape"})
        self.assertIn("本地目标路径非法", str(ctx.exception))

    def test_local_target_copies_and_reports_the_relative_destination(self):
        result = br.deliver_target(self.src, {"type": "local", "path": "backups/local"})
        self.assertTrue(result["success"])
        self.assertTrue(result["destination"].startswith("backups/local/"))

    def test_baidu_defaults_to_the_bypy_auth_mode(self):
        # ★ 第 388 行
        seen = {}
        with patch.object(br, "upload_baidu",
                          lambda s, rp, r: seen.update({"rp": rp, "r": r}) or {"ok": 1}):
            br.deliver_target(self.src, {"type": "baidu", "remote_path": "R20"})
        self.assertEqual(seen, {"rp": "R20", "r": 3})

    def test_baidu_oauth_mode_routes_to_the_oauth_uploader(self):
        # ★ 第 386/387 行
        called = []
        with patch.object(br, "upload_baidu_oauth",
                          lambda s, t: called.append(t) or {"ok": 1}):
            result = br.deliver_target(self.src, {"type": "baidu", "auth_mode": "oauth"})
        self.assertEqual(len(called), 1)
        self.assertTrue(result["ok"])

    def test_cloud_targets_validate_their_endpoint(self):
        # ★ 第 384 行 —— 出站地址必须过 net_security 校验
        import r20_backend.net_security as net
        seen = {}

        def validate(url, allow_private=False):
            seen.update({"url": url, "allow": allow_private})
            return "https://safe.example.com"
        with patch.object(net, "validate_outbound_url", validate), \
             patch.object(br, "upload_s3", lambda s, t: {"ok": True}):
            br.deliver_target(self.src, {"type": "s3", "endpoint": "http://evil",
                                         "allow_private_endpoint": True})
        self.assertEqual(seen, {"url": "http://evil", "allow": True})

    def test_a_retryable_failure_is_retried_with_backoff(self):
        # ★ 第 401–412 行
        attempts = []

        def flaky(source, target):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("503")
            return {"success": True}
        with patch.object(br, "upload_s3", flaky), \
             patch.object(br.time, "sleep", lambda s: None):
            result = br.deliver_target(self.src, {"type": "s3", "retries": 3,
                                                  "endpoint": "https://s3.example.com"})
        self.assertTrue(result["success"])
        self.assertEqual(result["attempts"], 3)

    def test_exhausting_the_retries_reports_the_last_error(self):
        def always(source, target):
            raise RuntimeError("still down")
        with patch.object(br, "upload_s3", always), \
             patch.object(br.time, "sleep", lambda s: None):
            result = br.deliver_target(self.src, {"type": "s3", "retries": 2,
                                                  "endpoint": "https://s3.example.com"})
        self.assertFalse(result["success"])
        self.assertEqual(result["attempts"], 2)
        self.assertIn("RuntimeError: still down", result["error"])

    def test_a_single_attempt_makes_no_sleep_call(self):
        slept = []
        with patch.object(br, "upload_s3", lambda s, t: {"success": True}), \
             patch.object(br.time, "sleep", lambda s: slept.append(s)):
            br.deliver_target(self.src, {"type": "s3", "retries": 1,
                                         "endpoint": "https://s3.example.com"})
        self.assertEqual(slept, [])


class RunBackupJobTests(_Sandbox):
    def setUp(self):
        super().setUp()
        (self.root / "data" / "payload.json").write_text("{}", encoding="utf-8")
        (self.root / "scripts").mkdir(exist_ok=True)
        self.jobs: list = []

    def _job(self, **over):
        job = {"id": "nightly", "name": "夜间灾备", "scope": ["data"],
               "targets": [{"id": "t1", "type": "local", "path": "backups/local",
                            "enabled": True}]}
        job.update(over)
        return job

    def test_manifest_is_always_written_even_on_failure(self):
        result = br.run_backup_job(self._job(scope=["no_such_scope"]))
        self.assertEqual(result["status"], "failed")
        manifest = self.root / result["manifest"]
        self.assertTrue(manifest.exists())
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["status"],
                         "failed")

    def test_stale_staging_cleanup_failure_does_not_abort_the_job(self):
        # ★ 第 419 行 `pass`
        with patch.object(br, "clean_stale_staging", side_effect=OSError("boom")):
            result = br.run_backup_job(self._job())
        self.assertEqual(result["status"], "success")

    def test_a_successful_local_backup(self):
        result = br.run_backup_job(self._job())
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["included"], ["data"])
        self.assertIn("archive", result)
        self.assertGreater(result["bytes"], 0)
        self.assertEqual(len(result["sha256"]), 64)
        self.assertEqual(result["archive_roots"], ["data"])
        self.assertFalse(result["encrypted"])

    def test_pre_backup_sync_runs_the_ledger_sync_script(self):
        # ★ 第 437/438 行 —— 脚本**存在**时才跑
        script = self.root / "scripts" / "sync_full_ledger.py"
        script.write_text("print('sync')", encoding="utf-8")
        ran = []
        with patch.object(br.subprocess, "run",
                          lambda cmd, **kw: ran.append(cmd) or SimpleNamespace(returncode=0)):
            result = br.run_backup_job(self._job(pre_backup_sync=True))
        self.assertEqual(len(ran), 1)
        self.assertIn("sync_full_ledger.py", ran[0][-1])
        self.assertEqual(result["status"], "success")

    def test_pre_backup_sync_is_skipped_when_the_script_is_absent(self):
        # ★ 第 438 行 `if script.exists()` 为假
        ran = []
        with patch.object(br.subprocess, "run",
                          lambda cmd, **kw: ran.append(cmd)):
            br.run_backup_job(self._job(pre_backup_sync=True))
        self.assertEqual(ran, [])

    def test_pre_backup_sync_is_not_run_without_the_flag(self):
        script = self.root / "scripts" / "sync_full_ledger.py"
        script.write_text("print('sync')", encoding="utf-8")
        ran = []
        with patch.object(br.subprocess, "run", lambda cmd, **kw: ran.append(cmd)):
            br.run_backup_job(self._job())
        self.assertEqual(ran, [])

    def test_encryption_flag_is_recorded_when_enabled(self):
        # ★ 第 443/444 行
        env = patch.dict(os.environ, {"R20_JOB_KEY": "k" * 24})
        env.start()
        self.addCleanup(env.stop)
        result = br.run_backup_job(self._job(
            encryption={"enabled": True, "key_env": "R20_JOB_KEY"}))
        self.assertTrue(result["encrypted"])
        self.assertTrue(result["archive"].endswith(".aes256"))
        self.assertEqual(result["status"], "success")

    def test_a_target_exception_becomes_a_failed_target_entry(self):
        # ★ 第 457 行
        with patch.object(br, "deliver_target", side_effect=RuntimeError("远端 500")):
            result = br.run_backup_job(self._job())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["targets"]), 1)
        self.assertFalse(result["targets"][0]["success"])
        self.assertIn("RuntimeError: 远端 500", result["targets"][0]["error"])
        self.assertTrue(any("远端 500" in e for e in result["errors"]))

    def test_partial_status_when_sqlite_succeeds_but_a_target_fails(self):
        (self.root / "data" / "r20_quant.db").write_bytes(b"")
        with patch.object(br, "deliver_target",
                          lambda s, t: {"success": False, "attempts": 1, "error": "x"}):
            result = br.run_backup_job(self._job(sqlite={"enabled": True, "retention": 3}))
        self.assertEqual(result["status"], "partial")

    def test_failed_status_when_nothing_succeeds(self):
        with patch.object(br, "deliver_target",
                          lambda s, t: {"success": False, "attempts": 1, "error": "x"}):
            result = br.run_backup_job(self._job())
        self.assertEqual(result["status"], "failed")

    def test_disabled_targets_are_not_delivered_to(self):
        result = br.run_backup_job(self._job(
            targets=[{"id": "t1", "type": "local", "enabled": False}]))
        self.assertEqual(result["targets"], [])
        self.assertEqual(result["status"], "failed", "没有任何成功面")

    def test_local_archive_is_cleaned_only_on_full_success(self):
        result = br.run_backup_job(self._job(cleanup_local_on_success=True))
        self.assertTrue(result["temporary_cleaned"])

    def test_local_archive_is_kept_when_cleanup_is_off(self):
        result = br.run_backup_job(self._job(cleanup_local_on_success=False))
        self.assertFalse(result["temporary_cleaned"])

    def test_sqlite_backups_are_listed_relative_to_the_repo(self):
        (self.root / "data" / "r20_quant.db").write_bytes(b"")
        result = br.run_backup_job(self._job(sqlite={"enabled": True, "retention": 3}))
        self.assertEqual(len(result["sqlite"]), 1)
        self.assertTrue(result["sqlite"][0].startswith("backups/sqlite/"))

    def test_manifest_records_the_job_identity_and_timestamps(self):
        result = br.run_backup_job(self._job())
        self.assertEqual(result["job_id"], "nightly")
        self.assertEqual(result["job_name"], "夜间灾备")
        self.assertIn("started_at", result)
        self.assertIn("finished_at", result)
        manifest = self.root / result["manifest"]
        self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["job_id"],
                         "nightly")

    def test_job_id_is_sanitised_in_the_manifest_name(self):
        result = br.run_backup_job(self._job(id="../../evil"))
        self.assertNotIn("..", result["manifest"])
        self.assertNotIn("/", Path(result["manifest"]).name)

    def test_an_unexpected_exception_marks_the_job_failed(self):
        # ★ 第 475 行
        with patch.object(br, "create_archive", side_effect=ValueError("boom")):
            result = br.run_backup_job(self._job())
        self.assertEqual(result["status"], "failed")
        self.assertIn("ValueError: boom", result["errors"])


if __name__ == "__main__":
    unittest.main()

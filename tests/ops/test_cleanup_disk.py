"""磁盘与日志清理（cleanup_disk.py）收口 —— 第 300 刀。

本模块跑在运维路径上，且**会真的删东西**，所以它的每一条守卫都必须钉住：

1. **日志轮转用 copytruncate，不是 move**（审计⑤）—— 旧实现把活文件 move 走再新建空壳，
   持有原 inode 的写入方（uvicorn 直写 `logs/*.log`）会继续写 `.1` 归档，
   "当前日志"再也不涨 ⇒ **等于把活日志弄丢**。断言轮转后 **inode 不变**、
   原文件被截断为 0、`.1` 是原内容的副本。
2. **`/tmp` 清扫只碰本仓前缀**（审计D）—— 旧命令 `find /tmp -type f -mtime +2 -delete`
   扫**整个 /tmp**，把别的程序的临时件（SSH agent socket、测试沙箱、harness 文件）
   当垃圾删，属严重越权。断言实际下发的命令**限定 `-maxdepth 1 -name 'r20-*'`**。
3. **深度清理只在磁盘紧张时触发**（阈值 5GB）—— 不该每次巡检都去动 npm 缓存。
"""
from __future__ import annotations

import ast
import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

scripts_dir = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import scripts.cleanup_disk as cd


class DiskStatusTests(unittest.TestCase):
    def _status(self, total, used, free):
        with patch.object(cd.shutil, "disk_usage", return_value=(total, used, free)):
            return cd.get_disk_status()

    def test_reports_rounded_gigabytes_and_percent(self):
        gib = 1024 ** 3
        status = self._status(100 * gib, 40 * gib, 60 * gib)
        self.assertEqual(status["total_gb"], 100.0)
        self.assertEqual(status["used_gb"], 40.0)
        self.assertEqual(status["free_gb"], 60.0)
        self.assertEqual(status["percent_used"], 40.0)
        self.assertFalse(status["is_low"])

    def test_low_flag_below_three_gigabytes(self):
        gib = 1024 ** 3
        self.assertTrue(self._status(100 * gib, 98 * gib, 2 * gib)["is_low"])
        # 边界：恰好 3.0 GB **不算**低（比较是严格小于）
        self.assertFalse(self._status(100 * gib, 97 * gib, 3 * gib)["is_low"])

    def test_percent_and_gb_are_rounded_to_documented_precision(self):
        status = self._status(3, 1, 2)
        self.assertEqual(status["total_gb"], round(3 / (1024 ** 3), 2))
        self.assertEqual(status["percent_used"], round(1 / 3 * 100, 1))


class CleanLogsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.logs = Path(self.tmp.name) / "logs"
        self.logs.mkdir()
        for name, value in (("LOGS_DIR", str(self.logs)),):
            patcher = patch.object(cd, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_missing_logs_dir_returns_empty(self):
        with patch.object(cd, "LOGS_DIR", str(self.logs / "does-not-exist")):
            self.assertEqual(cd.clean_logs(), [])

    def test_small_log_is_left_alone(self):
        path = self.logs / "small.log"
        path.write_text("tiny", encoding="utf-8")
        self.assertEqual(cd.clean_logs(), [])
        self.assertEqual(path.read_text(encoding="utf-8"), "tiny")
        self.assertFalse((self.logs / "small.log.1").exists())

    def test_rotation_copies_then_truncates_in_place(self):
        path = self.logs / "uvicorn.log"
        path.write_text("OLD-LINES\n" * 10, encoding="utf-8")
        inode_before = path.stat().st_ino
        # 把阈值压到极小以走轮转分支（不真写 10MB）
        with patch.object(cd, "MAX_LOG_SIZE_MB", 0.000001):
            actions = cd.clean_logs()
        self.assertEqual(len(actions), 1)
        self.assertIn("Rotated uvicorn.log", actions[0])
        # ★ copytruncate 的核心保证：inode 不变 ⇒ 已持有 fd 的写入方无感续写
        self.assertEqual(path.stat().st_ino, inode_before)
        self.assertEqual(path.read_text(encoding="utf-8"), "")
        self.assertEqual((self.logs / "uvicorn.log.1").read_text(encoding="utf-8"),
                         "OLD-LINES\n" * 10)

    def test_existing_archives_are_shifted_before_copy(self):
        path = self.logs / "a.log"
        path.write_text("CURRENT", encoding="utf-8")
        (self.logs / "a.log.1").write_text("ONE", encoding="utf-8")
        (self.logs / "a.log.2").write_text("TWO", encoding="utf-8")
        with patch.object(cd, "MAX_LOG_SIZE_MB", 0.000001):
            cd.clean_logs()
        self.assertEqual((self.logs / "a.log.1").read_text(encoding="utf-8"), "CURRENT")
        self.assertEqual((self.logs / "a.log.2").read_text(encoding="utf-8"), "ONE")
        self.assertEqual((self.logs / "a.log.3").read_text(encoding="utf-8"), "TWO")

    def test_excess_backups_beyond_count_are_deleted(self):
        path = self.logs / "b.log"
        path.write_text("x", encoding="utf-8")
        for i in (1, 2, 3, 4, 9):
            (self.logs / f"b.log.{i}").write_text(str(i), encoding="utf-8")
        actions = cd.clean_logs()
        self.assertFalse((self.logs / "b.log.4").exists())
        self.assertFalse((self.logs / "b.log.9").exists())
        self.assertTrue((self.logs / "b.log.3").exists())
        self.assertTrue(any("Deleted excess backup b.log.4" in a for a in actions))
        self.assertTrue(any("Deleted excess backup b.log.9" in a for a in actions))

    def test_non_numeric_suffix_is_ignored(self):
        path = self.logs / "c.log"
        path.write_text("x", encoding="utf-8")
        keep = self.logs / "c.log.bak"
        keep.write_text("keep me", encoding="utf-8")
        actions = cd.clean_logs()
        self.assertTrue(keep.exists())
        self.assertEqual(actions, [])

    def test_rotation_failure_is_reported_not_raised(self):
        path = self.logs / "d.log"
        path.write_text("y" * 100, encoding="utf-8")   # 必须真的越过阈值
        with patch.object(cd, "MAX_LOG_SIZE_MB", 0.000001), \
             patch.object(cd.shutil, "copy2", side_effect=OSError("no space")):
            actions = cd.clean_logs()
        self.assertEqual(len(actions), 1)
        self.assertIn("Rotation failed for d.log", actions[0])
        self.assertIn("no space", actions[0])
        # 失败时**不许**截断活日志（先 copy 后 truncate，copy 失败就该原样留着）
        self.assertEqual(path.read_text(encoding="utf-8"), "y" * 100)


class CleanSystemCachesTests(unittest.TestCase):
    def test_issues_both_scoped_commands(self):
        issued = []

        def fake_run(cmd, **kwargs):
            issued.append((cmd, kwargs))
            return None

        with patch.object(cd.subprocess, "run", fake_run):
            actions = cd.clean_system_caches()
        self.assertEqual(len(issued), 2)
        self.assertEqual(issued[0][0], "npm cache clean --force")
        # ★ 越权收口：只清 /tmp 顶层、且只清本仓前缀
        tmp_cmd = issued[1][0]
        self.assertIn("-maxdepth 1", tmp_cmd)
        self.assertIn("-name 'r20-*'", tmp_cmd)
        self.assertNotIn("-delete", tmp_cmd.replace("-exec rm -rf {} +", ""))
        self.assertEqual(actions, ["npm cache cleaned", "stale /tmp/r20-* entries cleared"])

    def test_subprocess_failure_for_each_step_is_swallowed(self):
        with patch.object(cd.subprocess, "run", side_effect=OSError("no npm")):
            self.assertEqual(cd.clean_system_caches(), [])

    def test_one_step_failing_does_not_skip_the_other(self):
        calls = []

        def flaky(cmd, **kwargs):
            calls.append(cmd)
            if cmd.startswith("npm"):
                raise OSError("npm missing")

        with patch.object(cd.subprocess, "run", flaky):
            actions = cd.clean_system_caches()
        self.assertEqual(len(calls), 2)
        self.assertEqual(actions, ["stale /tmp/r20-* entries cleared"])


class RunCleanupAndCheckTests(unittest.TestCase):
    def _report(self, free_gb, *, rotations=None, cache=None):
        with patch.object(cd, "get_disk_status",
                          return_value={"total_gb": 100.0, "used_gb": 100.0 - free_gb,
                                        "free_gb": free_gb, "percent_used": 50.0,
                                        "is_low": free_gb < 3.0}), \
             patch.object(cd, "clean_logs", return_value=rotations or []), \
             patch.object(cd, "clean_system_caches",
                          return_value=cache if cache is not None else ["purged"]):
            return cd.run_cleanup_and_check()

    def test_low_space_triggers_deep_purge(self):
        report = self._report(4.0)
        self.assertEqual(report["cache_cleared"], ["purged"])

    def test_ample_space_skips_deep_purge(self):
        report = self._report(20.0)
        self.assertEqual(report["cache_cleared"], [])

    def test_boundary_exactly_5gb_skips_purge(self):
        # 比较是严格小于 ⇒ 恰好 5.0 不触发
        self.assertEqual(self._report(5.0)["cache_cleared"], [])

    def test_report_shape_and_beijing_timestamp(self):
        report = self._report(20.0, rotations=["Rotated a.log (11.0MB)"])
        self.assertEqual(sorted(report), ["cache_cleared", "disk", "log_rotations", "timestamp"])
        self.assertEqual(report["log_rotations"], ["Rotated a.log (11.0MB)"])
        # 时间戳是北京时间（+08:00），不是 UTC
        self.assertIn("+08:00", report["timestamp"])
        self.assertEqual(len(report["timestamp"].split(" ")), 2)


class MainGuardTests(unittest.TestCase):
    """`__main__` 三态输出：有轮转 / 无轮转 / 磁盘告警。"""

    def _run_guard(self, report):
        src = Path(cd.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        node = next(n for n in tree.body if isinstance(n, ast.If) and n.lineno == 116)
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(compile(module, cd.__file__, "exec"),  # noqa: S102
                 {"__name__": "__main__", "run_cleanup_and_check": lambda: report,
                  "print": print})
        return buf.getvalue()

    def _report(self, *, rotations, is_low, free_gb=20.0):
        return {"timestamp": "2026-09-22 21:30:00+08:00",
                "disk": {"total_gb": 100.0, "free_gb": free_gb,
                         "percent_used": 80.0, "is_low": is_low},
                "log_rotations": rotations, "cache_cleared": []}

    def test_prints_disk_line_and_rotation_list(self):
        out = self._run_guard(self._report(rotations=["Rotated a.log (11.0MB)"], is_low=False))
        self.assertIn("=== 存储与日志清理检查 [2026-09-22 21:30:00+08:00] ===", out)
        self.assertIn("剩余 20.0 GB / 总计 100.0 GB (使用率: 80.0%)", out)
        self.assertIn("日志轮转: Rotated a.log (11.0MB)", out)

    def test_prints_healthy_message_when_no_rotation(self):
        out = self._run_guard(self._report(rotations=[], is_low=False))
        self.assertIn("日志状态: 正常 (文件大小均在 10MB 限制内)", out)
        self.assertNotIn("日志轮转:", out)

    def test_prints_low_disk_warning(self):
        out = self._run_guard(self._report(rotations=[], is_low=True, free_gb=2.0))
        self.assertIn("⚠️ 警告: 可用磁盘空间低于 3GB！", out)

    def test_no_warning_when_disk_is_fine(self):
        out = self._run_guard(self._report(rotations=[], is_low=False))
        self.assertNotIn("警告", out)


if __name__ == "__main__":
    unittest.main()

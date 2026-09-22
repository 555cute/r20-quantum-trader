"""独立调度进程：**到点判定、脚本隔离调用、单实例锁**（第二百七十三刀，开新面 scheduler.py）。

先打印整个文件（117 行）再动笔。它自己不做交易，只按点把既有脚本当**隔离子进程**拉起
（保留各脚本自己的文件锁与 fail-closed 行为）；故本刀钉三件事：
「到点判定不许误触发」、「失败必须进日志」、「只能有一个调度进程」。

| 语义 | 口径 |
|---|---|
| ★ **到点判定** | `due_daily` 只在 `HH:MM` **精确相等**时考虑；时间串非法（空、无冒号、非数字）⇒ False（不抛）；**同一天同一时刻已跑过 ⇒ False**（防同分钟重复触发）|
| ★ **失败必须可见** | `run_script` 退出码非 0 ⇒ `logger.error` 带 rc 与 stderr 尾巴；成功 ⇒ `logger.info` 带 stdout 尾巴 |
| ★ **单实例** | `main` 用 `fcntl.flock(LOCK_EX + LOCK_NB)` 抢占；抢不到 ⇒ `SystemExit("already running")` |
| ★ **调度表** | `JOBS` 六个任务；trader/factor_library/news 是**周期**（15 分钟 / 60 秒 / 10 分钟），三个日报类是**到点**（间隔 None）|
| ★ **北京时间** | `BeijingFormatter.converter` 用**记录自身的创建时刻**换算 +08:00（不是格式化时的墙钟）|
| 日志装配 | `configure_logging` 只给自己的 logger 挂 handler、**重复调用不重复挂**、不改全局 logging、`propagate=False` |
"""

import logging
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from r20_backend import scheduler as SC

_BJ = timezone(timedelta(hours=8))


class _LoopDone(Exception):
    """用来把 `main()` 的无限循环在第 N 次 sleep 处打断。"""


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-sched-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.logs = self.tmp / "logs"
        self.data = self.tmp / "data"
        self._start(mock.patch.object(SC, "LOGS", self.logs))
        self._start(mock.patch.object(SC, "DATA", self.data))
        self._start(mock.patch.object(SC, "ROOT", self.tmp))
        self._start(mock.patch.object(SC, "SCRIPTS", self.tmp / "scripts"))


class JobsTableTests(unittest.TestCase):
    def test_jobs_table_covers_the_six_documented_tasks(self):
        self.assertEqual(set(SC.JOBS), {"trader", "factor_library", "news",
                                        "daily_briefing", "self_improvement",
                                        "nightly_backup"})

    def test_periodic_and_daily_jobs_are_distinguished_by_their_interval(self):
        self.assertEqual(SC.JOBS["trader"][1], 15 * 60)
        self.assertEqual(SC.JOBS["factor_library"][1], 60)
        self.assertEqual(SC.JOBS["news"][1], 10 * 60)
        for daily in ("daily_briefing", "self_improvement", "nightly_backup"):
            with self.subTest(job=daily):
                self.assertIsNone(SC.JOBS[daily][1], "到点型任务没有周期间隔")

    def test_every_job_points_at_a_python_script(self):
        for name, (script, _) in SC.JOBS.items():
            with self.subTest(job=name):
                self.assertTrue(script.endswith(".py"))


class DueDailyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 22, 8, 0, 0, tzinfo=_BJ)

    def test_exact_minute_matches(self):
        self.assertTrue(SC.due_daily(self.now, "08:00", None))

    def test_wrong_minute_or_hour_does_not_match(self):
        self.assertFalse(SC.due_daily(self.now, "08:01", None))
        self.assertFalse(SC.due_daily(self.now, "20:00", None))

    def test_malformed_schedule_times_are_false_not_raised(self):
        for bad in ("", "nope", "08", "08:xx", "a:b", "::", "08:00:00"):
            with self.subTest(value=bad):
                self.assertFalse(SC.due_daily(self.now, bad, None))

    def test_none_schedule_time_raises_attributeerror(self):
        """⚠️ 已知边界缺口：`except (TypeError, ValueError)` **不覆盖 AttributeError**，
        传 None 会直接冒泡（不是回落 False）。按实际行为钉住，未擅自改语义。"""
        with self.assertRaises(AttributeError):
            SC.due_daily(self.now, None, None)

    def test_same_day_same_minute_already_run_is_false(self):
        last = datetime(2026, 9, 22, 8, 0, 30, tzinfo=_BJ)
        self.assertFalse(SC.due_daily(self.now, "08:00", last),
                         "同一分钟的重复触发必须被压掉")

    def test_previous_day_run_is_due_again(self):
        last = datetime(2026, 9, 21, 8, 0, 1, tzinfo=_BJ)
        self.assertTrue(SC.due_daily(self.now, "08:00", last))

    def test_same_day_different_minute_run_is_treated_as_due(self):
        last = datetime(2026, 9, 22, 7, 59, 0, tzinfo=_BJ)
        self.assertTrue(SC.due_daily(self.now, "08:00", last),
                        "按 (hour,minute) 比较 ⇒ 时刻不同的当日记录不算已跑")

    def test_single_digit_hour_is_accepted(self):
        early = datetime(2026, 9, 22, 8, 0, 0, tzinfo=_BJ)
        self.assertTrue(SC.due_daily(early, "8:0", None))


class BeijingFormatterTests(unittest.TestCase):
    def test_converter_uses_the_records_own_creation_epoch(self):
        # 1970-01-01 00:00:00 UTC = 北京时间 08:00
        self.assertEqual(SC.BeijingFormatter.converter(0).tm_hour, 8)
        self.assertEqual(SC.BeijingFormatter.converter(0).tm_mday, 1)

    def test_formatted_record_carries_the_beijing_offset(self):
        formatter = SC.BeijingFormatter("%(asctime)s %(message)s")
        record = logging.LogRecord("r20", logging.INFO, __file__, 1, "hello",
                                   None, None)
        record.created = 0
        formatted = formatter.format(record)
        self.assertIn("1970-01-01 08:00:00", formatted)
        self.assertIn("hello", formatted)


class ConfigureLoggingTests(_Base):
    def setUp(self):
        super().setUp()
        self.fresh = logging.Logger("r20-scheduler-test")
        self.addCleanup(lambda: [h.close() for h in list(self.fresh.handlers)])
        self._start(mock.patch.object(SC, "logger", self.fresh))

    def test_creates_the_log_directory_and_attaches_one_handler(self):
        SC.configure_logging()
        self.assertTrue(self.logs.is_dir())
        self.assertEqual(len(self.fresh.handlers), 1)
        self.assertIsInstance(self.fresh.handlers[0].formatter, SC.BeijingFormatter)

    def test_is_idempotent(self):
        SC.configure_logging()
        SC.configure_logging()
        self.assertEqual(len(self.fresh.handlers), 1, "重复调用不得重复挂 handler")

    def test_does_not_touch_global_logging(self):
        root_before = list(logging.getLogger().handlers)
        SC.configure_logging()
        self.assertEqual(logging.getLogger().handlers, root_before)
        self.assertFalse(self.fresh.propagate, "必须关掉上抛，避免污染全局日志")
        self.assertEqual(self.fresh.level, logging.INFO)

    def test_log_file_is_written_where_expected(self):
        SC.configure_logging()
        SC.logger.info("probe-line")
        for handler in SC.logger.handlers:
            handler.flush()
        text = (self.logs / "r20_scheduler.log").read_text(encoding="utf-8")
        self.assertIn("probe-line", text)
        self.assertIn("+08:00", text)


class RunScriptTests(_Base):
    def setUp(self):
        super().setUp()
        self.fresh = logging.Logger("r20-scheduler-run")
        self._start(mock.patch.object(SC, "logger", self.fresh))
        self.runner = self._start(mock.patch.object(SC.subprocess, "run"))

    def test_success_logs_the_stdout_tail(self):
        self.runner.return_value = mock.Mock(returncode=0, stdout="ok-output", stderr="")
        with self.assertLogs(self.fresh, level="INFO") as captured:
            SC.run_script("factor_library")
        self.assertTrue(any("ok-output" in line for line in captured.output))
        self.assertTrue(any("job=factor_library" in line for line in captured.output))

    def test_failure_logs_the_returncode_and_stderr_tail(self):
        self.runner.return_value = mock.Mock(returncode=3, stdout="", stderr="boom")
        with self.assertLogs(self.fresh, level="ERROR") as captured:
            SC.run_script("trader")
        self.assertTrue(any("rc=3" in line for line in captured.output))
        self.assertTrue(any("boom" in line for line in captured.output))

    def test_invocation_is_isolated_and_bounded(self):
        self.runner.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        SC.run_script("news")
        argv = self.runner.call_args[0][0]
        self.assertTrue(argv[1].endswith("news_sentiment_harvester.py"))
        self.assertEqual(self.runner.call_args[1]["cwd"], self.tmp)
        self.assertTrue(self.runner.call_args[1]["capture_output"])
        self.assertTrue(self.runner.call_args[1]["text"])
        self.assertEqual(self.runner.call_args[1]["timeout"], 600,
                         "子进程必须有超时，不能把调度器挂死")

    def test_unknown_job_name_is_a_key_error(self):
        with self.assertRaises(KeyError):
            SC.run_script("not-a-job")

    def test_long_output_is_truncated_in_the_log_line(self):
        self.runner.return_value = mock.Mock(returncode=1, stdout="",
                                             stderr="E" * 5000)
        with self.assertLogs(self.fresh, level="ERROR") as captured:
            SC.run_script("trader")
        self.assertTrue(any(len(line) < 1200 for line in captured.output),
                        "stderr 只取尾部 1000 字符")


class MainLoopTests(_Base):
    def setUp(self):
        super().setUp()
        self.fresh = logging.Logger("r20-scheduler-main")
        self._start(mock.patch.object(SC, "logger", self.fresh))
        self._start(mock.patch.object(SC, "configure_logging"))
        self._start(mock.patch.object(SC, "fcntl"))
        self.runs = []
        self._start(mock.patch.object(SC, "run_script",
                                      side_effect=lambda name: self.runs.append(name)))
        self.schedule = {
            "briefing_times": ["08:00"],
            "self_improvement_time": "08:00",
            "backup_time": "08:00",
        }
        self._start(mock.patch.object(SC, "load_schedule", return_value=self.schedule))
        self.fixed = datetime(2026, 9, 22, 8, 0, 0, tzinfo=_BJ)
        fake_datetime = mock.Mock()
        fake_datetime.now.return_value = self.fixed
        self._start(mock.patch.object(SC, "datetime", fake_datetime))
        self.sleeps = []

        def _sleep(seconds):
            self.sleeps.append(seconds)
            raise _LoopDone()

        self._start(mock.patch.object(SC.time, "sleep", side_effect=_sleep))

    def _run_one_iteration(self):
        with self.assertRaises(_LoopDone):
            SC.main()

    def test_first_iteration_runs_every_job_including_the_daily_three(self):
        self._run_one_iteration()
        self.assertEqual(set(self.runs),
                         {"trader", "factor_library", "news", "daily_briefing",
                          "self_improvement", "nightly_backup"})

    def test_loop_sleeps_five_seconds_between_passes(self):
        self._run_one_iteration()
        self.assertEqual(self.sleeps, [5])

    def test_lock_contention_exits_with_a_clear_message(self):
        self._start(mock.patch.object(SC.fcntl, "flock",
                                      side_effect=BlockingIOError("busy")))
        with self.assertRaises(SystemExit) as ctx:
            SC.main()
        self.assertIn("already running", str(ctx.exception))
        self.assertEqual(self.runs, [], "抢不到锁时必须一个任务都不跑")

    def test_lock_is_taken_exclusively_and_non_blocking(self):
        self._run_one_iteration()
        args = SC.fcntl.flock.call_args[0]
        self.assertEqual(args[1], SC.fcntl.LOCK_EX | SC.fcntl.LOCK_NB)

    def test_data_directory_is_created_for_the_lock_file(self):
        self._run_one_iteration()
        self.assertTrue(self.data.is_dir())

    def test_missing_schedule_keys_fall_back_to_the_documented_defaults(self):
        self._start(mock.patch.object(SC, "load_schedule", return_value={}))
        self._run_one_iteration()
        # 固定时刻是 08:00：默认 briefing_times 含 "08:00" ⇒ 日报触发；
        # self_improvement_time/backup_time 默认 20:00/02:00 ⇒ 不触发
        self.assertIn("daily_briefing", self.runs)
        self.assertNotIn("self_improvement", self.runs)
        self.assertNotIn("nightly_backup", self.runs)

    def test_second_iteration_skips_jobs_that_are_not_due_yet(self):
        """第二圈的所有 last_* 都是刚刚记录的时刻 ⇒ 到点型任务不应重复触发。"""
        self._start(mock.patch.object(SC, "run_script",
                                      side_effect=lambda name: self.runs.append(name)))
        calls = {"n": 0}

        def _sleep(seconds):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise _LoopDone()

        self._start(mock.patch.object(SC.time, "sleep", side_effect=_sleep))
        with self.assertRaises(_LoopDone):
            SC.main()
        self.assertEqual(self.runs.count("daily_briefing"), 1,
                         "同一分钟的第二圈不得重复触发日报")


if __name__ == "__main__":
    unittest.main()

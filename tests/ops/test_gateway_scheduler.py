"""Gateway Scheduler timing and migration tests."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from r20_gateway.scheduler import GatewayScheduler, JOBS
from r20_gateway.store import GatewayStore

BJ = timezone(timedelta(hours=8))


class GatewaySchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GatewayStore(Path(self.temp.name) / "gateway.db")
        self.scheduler = GatewayScheduler(self.store, max_workers=1)
        self.now = datetime(2026, 9, 1, 18, 0, tzinfo=BJ)

    def tearDown(self):
        self.scheduler.shutdown()
        self.temp.cleanup()

    def test_migration_baseline_prevents_immediate_launch(self):
        self.scheduler.initialize_migration_baseline(self.now)
        with patch("r20_gateway.scheduler.load_schedule", return_value={}):
            self.assertEqual(self.scheduler.tick(self.now), [])

    def test_interval_job_becomes_due_on_aligned_trader_boundary(self):
        trader = next(spec for spec in JOBS if spec.name == "trader")
        boundary = self.now.replace(minute=15, second=0)
        self.store.set_state("job.last.trader", boundary.replace(minute=0).isoformat())
        self.assertTrue(self.scheduler.due(trader, boundary, {}))
        self.assertFalse(self.scheduler.due(trader, boundary.replace(second=11), {}))

    def test_daily_job_runs_once_per_time_slot(self):
        briefing = next(spec for spec in JOBS if spec.name == "daily_briefing")
        schedule = {"briefing_times": ["08:00", "20:00"]}
        at_eight = self.now.replace(hour=8)
        self.assertTrue(self.scheduler.due(briefing, at_eight, schedule))
        self.store.set_state("job.last.daily_briefing", at_eight.isoformat())
        self.assertFalse(self.scheduler.due(briefing, at_eight, schedule))
        self.assertTrue(self.scheduler.due(briefing, at_eight.replace(hour=20), schedule))

    def test_runtime_state_survives_store_reopen(self):
        self.store.set_state("job.last.news", self.now.isoformat())
        reopened = GatewayStore(self.store.path)
        self.assertEqual(reopened.get_state("job.last.news"), self.now.isoformat())


    def test_news_staggered_schedule_avoids_trader_collision(self):
        news = next(spec for spec in JOBS if spec.name == "news")
        self.assertEqual(news.interval_seconds, 600)
        self.assertEqual(news.offset_seconds, 180)

        # 1. At 18:00:00 (when trader runs), news should NOT be due
        at_zero = self.now.replace(minute=0, second=0)
        self.store.set_state("job.last.news", self.now.replace(minute=0).isoformat())
        self.assertFalse(self.scheduler.due(news, at_zero, {}))

        # 2. At 18:03:00 (offset by +3 minutes), news transitions into slot and becomes due
        at_three = self.now.replace(minute=3, second=0)
        self.assertTrue(self.scheduler.due(news, at_three, {}))
        # After 30s in slot, it is no longer due
        self.assertFalse(self.scheduler.due(news, at_three.replace(second=35), {}))

        # 3. Simulate news finished at 18:03, at 18:13:00 it becomes due again
        self.store.set_state("job.last.news", at_three.isoformat())
        at_thirteen = self.now.replace(minute=13, second=0)
        self.assertTrue(self.scheduler.due(news, at_thirteen, {}))

        # 4. At 18:15:00 (trader's next run), news is NOT due
        at_fifteen = self.now.replace(minute=15, second=0)
        self.assertFalse(self.scheduler.due(news, at_fifteen, {}))


if __name__ == "__main__":
    unittest.main()


# =====================================================================
# 第二百九十七刀补：作业清单 / 快照 / 子进程执行 / tick / status
# =====================================================================

from datetime import timezone as _tz  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

from r20_gateway import scheduler as SCH  # noqa: E402
from r20_gateway.scheduler import (  # noqa: E402
    JobSpec, backup_job_specs, current_jobs, scheduler_snapshot,
)


class BackupJobSpecsTests(unittest.TestCase):
    """★ 备份作业是**动态**清单：开关与时刻都来自后台配置，不是硬编码。"""

    def _jobs(self, jobs):
        with patch.object(SCH, "list_backup_jobs", return_value=jobs):
            return backup_job_specs()

    def test_a_disabled_job_is_skipped(self):
        specs = self._jobs([{"id": "a", "enabled": False},
                            {"id": "b", "enabled": True}])
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].script, "nightly_backup_and_clean.py")

    def test_the_nightly_name_is_positional_over_the_unfiltered_list(self):
        """⚠️ `enumerate` 跑的是**未过滤**的原始清单（`enabled` 判断在循环体内）。

        所以"0 号位"是**配置里的第一条**，不是"第一条启用的"。于是
        「第 0 条停用、第 1 条启用」时，那条启用项拿到的是 `backup:<id>` 而**不是**
        `nightly_backup` —— 这个水位线语义值得钉住（改名会影响作业历史归属）。
        """
        specs = self._jobs([{"id": "a", "enabled": False},
                            {"id": "b", "enabled": True}])
        self.assertEqual([s.name for s in specs], ["backup:b"])

    def test_the_first_enabled_job_is_the_nightly_default(self):
        specs = self._jobs([{"id": "b", "enabled": True}])
        self.assertEqual(specs[0].name, "nightly_backup")

    def test_the_nightly_default_id_also_maps_to_the_fixed_name(self):
        specs = self._jobs([{"id": "x", "enabled": True},
                            {"id": "nightly-default", "enabled": True}])
        self.assertEqual([s.name for s in specs],
                         ["nightly_backup", "nightly_backup"])

    def test_later_jobs_get_an_id_qualified_name(self):
        specs = self._jobs([{"id": "a", "enabled": True},
                            {"id": "offsite", "enabled": True}])
        self.assertEqual(specs[1].name, "backup:offsite")

    def test_the_schedule_key_and_script_are_fixed(self):
        spec = self._jobs([{"id": "offsite", "enabled": True}])[0]
        self.assertEqual(spec.schedule_key, "backup_job:offsite")
        self.assertEqual(spec.script, "nightly_backup_and_clean.py")
        self.assertEqual(spec.timeout_seconds, 1800)

    def test_the_configured_times_are_carried_over(self):
        spec = self._jobs([{"id": "a", "enabled": True,
                            "schedule_times": ["03:30", "15:30"]}])[0]
        self.assertEqual(spec.default_times, ("03:30", "15:30"))

    def test_the_default_time_is_two_am(self):
        spec = self._jobs([{"id": "a", "enabled": True}])[0]
        self.assertEqual(spec.default_times, ("02:00",))

    def test_a_job_without_an_id_is_tolerated(self):
        specs = self._jobs([{"enabled": False}])
        self.assertEqual(specs, ())

    def test_no_jobs_yields_no_specs(self):
        self.assertEqual(self._jobs([]), ())

    def test_current_jobs_is_the_static_list_plus_the_dynamic_one(self):
        with patch.object(SCH, "backup_job_specs", return_value=("B",)):
            self.assertEqual(current_jobs(), (*JOBS, "B"))


class SchedulerSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GatewayStore(Path(self.temp.name) / "gateway.db")
        self.schedule = patch.object(SCH, "load_schedule", return_value={}).start()
        self.addCleanup(patch.stopall)

    def _job(self, name):
        return next(j for j in scheduler_snapshot(self.store)["jobs"] if j["name"] == name)

    def test_the_payload_shape(self):
        snap = scheduler_snapshot(self.store)
        self.assertEqual(set(snap), {"jobs", "recent_runs"})
        self.assertEqual(len(snap["jobs"]), len(current_jobs()))

    def test_recent_runs_are_included(self):
        snap = scheduler_snapshot(self.store)
        self.assertIsInstance(snap["recent_runs"], list)

    def test_an_unparsable_last_run_time_is_tolerated(self):
        self.store.set_state("job.last.news", "不是时间")
        with patch.object(SCH, "parse_beijing", side_effect=ValueError("坏")):
            self.assertEqual(self._job("news")["last_scheduled_at"], "")

    def test_a_known_last_run_time_is_rendered(self):
        self.store.set_state("job.last.news", "2026-09-20 10:00:00+08:00")
        self.assertIn("2026-09-20", self._job("news")["last_scheduled_at"])

    def test_the_timezone_is_declared(self):
        self.assertEqual(self._job("news")["timezone"], "Asia/Shanghai")

    def test_an_interval_job_describes_its_period(self):
        self.assertEqual(self._job("factor_library")["schedule"], "每 1 分钟")

    def test_a_staggered_job_describes_its_offset(self):
        self.assertIn("错峰 +3m", self._job("news")["schedule"])

    def test_scheduled_time_jobs_join_their_times(self):
        self.assertEqual(self._job("daily_briefing")["schedule"], "08:00、20:00")

    def test_a_configured_list_overrides_the_default_times(self):
        self.schedule.return_value = {"briefing_times": ["09:00"]}
        self.assertEqual(self._job("daily_briefing")["schedule"], "09:00")

    def test_a_configured_bare_string_is_accepted(self):
        self.schedule.return_value = {"briefing_times": "09:00"}
        self.assertEqual(self._job("daily_briefing")["schedule"], "09:00")

    def test_an_interval_job_is_never_overdue_without_a_last_run(self):
        self.assertIs(self._job("factor_library")["overdue"], False)

    def test_a_long_silence_makes_an_interval_job_overdue(self):
        self.store.set_state("job.last.factor_library", "2020-01-01 00:00:00+08:00")
        self.assertIs(self._job("factor_library")["overdue"], True)

    def test_a_timed_job_is_never_overdue(self):
        self.store.set_state("job.last.daily_briefing", "2020-01-01 00:00:00+08:00")
        self.assertIs(self._job("daily_briefing")["overdue"], False)

    def test_the_offset_is_exposed(self):
        self.assertEqual(self._job("news")["offset_seconds"], 180)


class LastAtTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GatewayStore(Path(self.temp.name) / "gateway.db")
        self.scheduler = GatewayScheduler(self.store, max_workers=1)
        self.addCleanup(self.scheduler.shutdown)

    def test_a_missing_state_yields_none(self):
        self.assertIsNone(self.scheduler._last_at("nothing"))

    def test_an_unparsable_state_yields_none(self):
        self.store.set_state("job.last.x", "坏")
        with patch.object(SCH, "parse_beijing", side_effect=ValueError("坏")):
            self.assertIsNone(self.scheduler._last_at("x"))

    def test_a_valid_state_is_parsed(self):
        self.store.set_state("job.last.news", "2026-09-20 10:00:00+08:00")
        self.assertEqual(self.scheduler._last_at("news").hour, 10)


class ScheduledTimesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GatewayStore(Path(self.temp.name) / "gateway.db")
        self.scheduler = GatewayScheduler(self.store, max_workers=1)
        self.addCleanup(self.scheduler.shutdown)

    def test_a_backup_job_ignores_the_schedule_table(self):
        spec = JobSpec("b", "s.py", None, 1, "backup_job:offsite", ("03:00",))
        self.assertEqual(self.scheduler._scheduled_times(spec, {"backup_job:offsite": ["09:00"]}),
                         ("03:00",))

    def test_a_list_value_becomes_a_tuple(self):
        spec = JobSpec("b", "s.py", None, 1, "briefing_times", ("08:00",))
        self.assertEqual(self.scheduler._scheduled_times(spec, {"briefing_times": ["09:00", "21:00"]}),
                         ("09:00", "21:00"))

    def test_a_bare_string_becomes_a_single_element_tuple(self):
        spec = JobSpec("b", "s.py", None, 1, "briefing_times", ("08:00",))
        self.assertEqual(self.scheduler._scheduled_times(spec, {"briefing_times": "09:00"}),
                         ("09:00",))

    def test_a_missing_key_falls_back_to_the_default_times(self):
        spec = JobSpec("b", "s.py", None, 1, "briefing_times", ("08:00",))
        self.assertEqual(self.scheduler._scheduled_times(spec, {}), ("08:00",))

    def test_the_legacy_singular_key_is_still_honoured(self):
        """兼容后台旧配置键：`self_improvement_time`（单数）。"""
        spec = JobSpec("b", "s.py", None, 1, "self_improvement_times", ("02:00",))
        self.assertEqual(self.scheduler._scheduled_times(spec, {"self_improvement_time": ["03:00"]}),
                         ("03:00",))

    def test_the_legacy_key_only_applies_to_the_self_improvement_job(self):
        spec = JobSpec("b", "s.py", None, 1, "briefing_times", ("08:00",))
        self.assertEqual(self.scheduler._scheduled_times(spec, {"self_improvement_time": ["03:00"]}),
                         ("08:00",))

    def test_a_non_string_entry_is_stringified(self):
        spec = JobSpec("b", "s.py", None, 1, "briefing_times", ("08:00",))
        self.assertEqual(self.scheduler._scheduled_times(spec, {"briefing_times": [800]}),
                         ("800",))


class ExecuteTests(unittest.TestCase):
    """★ `_execute` 会**真的起子进程**跑作业脚本 —— `subprocess.run` 必须全程打桩。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GatewayStore(Path(self.temp.name) / "gateway.db")
        self.scheduler = GatewayScheduler(self.store, max_workers=1)
        self.addCleanup(self.scheduler.shutdown)
        self.run = patch.object(SCH.subprocess, "run").start()
        self.addCleanup(patch.stopall)
        self.run.return_value.returncode = 0
        self.run.return_value.stdout = "一切正常"
        self.run.return_value.stderr = ""
        self.spec = JobSpec("unit-job", "unit_job.py", 60, 42)

    def _only_run(self):
        runs = self.store.job_runs(10)
        self.assertEqual(len(runs), 1)
        return runs[0]

    def test_a_successful_run_is_recorded(self):
        self.scheduler._execute(self.spec)
        row = self._only_run()
        self.assertEqual(row["job_name"], "unit-job")
        self.assertEqual(row["status"], "success")

    def test_the_command_uses_the_venv_interpreter_and_the_script_path(self):
        self.scheduler._execute(self.spec)
        command = self.run.call_args[0][0]
        self.assertEqual(command, [SCH.sys.executable, str(SCH.SCRIPTS / "unit_job.py")])

    def test_the_subprocess_contract(self):
        self.scheduler._execute(self.spec)
        kwargs = self.run.call_args[1]
        self.assertEqual(kwargs["cwd"], SCH.ROOT)
        self.assertIs(kwargs["text"], True)
        self.assertIs(kwargs["capture_output"], True)
        self.assertEqual(kwargs["timeout"], 42, "必须带上 spec 的超时，否则作业能挂死 worker")

    def test_the_stdout_tail_becomes_the_detail(self):
        self.scheduler._execute(self.spec)
        self.assertEqual(self._only_run()["detail"], "一切正常")

    def test_the_stderr_is_preferred_when_the_exit_code_is_nonzero(self):
        self.run.return_value.returncode = 3
        self.run.return_value.stdout = "别看我"
        self.run.return_value.stderr = "崩了"
        self.scheduler._execute(self.spec)
        row = self._only_run()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["detail"], "崩了")

    def test_the_detail_is_capped_at_two_thousand_chars(self):
        self.run.return_value.stdout = "x" * 5000
        self.scheduler._execute(self.spec)
        self.assertEqual(len(self._only_run()["detail"]), 2000)

    def test_a_timeout_is_recorded_as_exit_code_124(self):
        self.run.side_effect = SCH.subprocess.TimeoutExpired("cmd", 42)
        self.scheduler._execute(self.spec)
        row = self._only_run()
        self.assertEqual(row["status"], "failed")
        self.assertIn("timeout after 42s", row["detail"])

    def test_an_unexpected_exception_is_recorded_not_raised(self):
        self.run.side_effect = RuntimeError("没内存了")
        self.scheduler._execute(self.spec)
        row = self._only_run()
        self.assertEqual(row["status"], "failed")
        self.assertIn("RuntimeError: 没内存了", row["detail"])

    def test_a_backup_job_passes_its_job_id(self):
        spec = JobSpec("backup:offsite", "nightly_backup_and_clean.py", None, 1800,
                       "backup_job:offsite")
        self.scheduler._execute(spec)
        command = self.run.call_args[0][0]
        self.assertEqual(command[2:], ["--job-id", "offsite"])

    def test_a_non_backup_job_gets_no_extra_arguments(self):
        self.scheduler._execute(self.spec)
        self.assertEqual(len(self.run.call_args[0][0]), 2)

    def test_a_begin_job_failure_does_not_reach_the_subprocess(self):
        with patch.object(self.store, "begin_job", side_effect=RuntimeError("库锁住了")):
            with self.assertRaises(RuntimeError):
                self.scheduler._execute(self.spec)
        self.run.assert_not_called()


class TickTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GatewayStore(Path(self.temp.name) / "gateway.db")
        self.scheduler = GatewayScheduler(self.store, max_workers=1)
        self.addCleanup(self.scheduler.shutdown)
        self.scheduler.executor = MagicMock()
        self.now = datetime(2026, 9, 1, 18, 0, tzinfo=BJ)
        self.schedule = patch.object(SCH, "load_schedule", return_value={}).start()
        self.due = patch.object(self.scheduler, "due", return_value=True).start()
        self.addCleanup(patch.stopall)

    def _future(self, done=False):
        future = MagicMock()
        future.done.return_value = done
        self.scheduler.executor.submit.return_value = future
        return future

    def test_a_due_job_is_launched_and_its_state_stamped(self):
        self._future()
        launched = self.scheduler.tick(self.now)
        self.assertEqual(launched, [s.name for s in current_jobs()])
        self.assertEqual(self.store.get_state("job.last.trader"),
                         self.now.isoformat())

    def test_an_already_running_job_is_not_relaunched(self):
        self.scheduler.running = {"trader": self._future(done=False)}
        launched = self.scheduler.tick(self.now)
        self.assertNotIn("trader", launched)
        self.assertIn("news", launched)

    def test_a_finished_job_is_dropped_from_the_running_map(self):
        """完成的 future 必须被摘掉，否则该作业**永远不会**再被排程。"""
        self._future()
        self.scheduler.running = {"trader": self._future(done=True)}
        launched = self.scheduler.tick(self.now)
        self.assertIn("trader", launched)

    def test_a_job_that_is_not_due_is_skipped(self):
        self._future()
        self.due.return_value = False
        self.assertEqual(self.scheduler.tick(self.now), [])
        self.scheduler.executor.submit.assert_not_called()

    def test_the_execute_callable_is_submitted(self):
        """⚠️ 不能用 `assertIs`：每次访问 `self.scheduler._execute` 都会新建一个
        bound method 对象，`is` 永远为假（同一个 self 也不行）。比 `__func__`/`__self__`。"""
        self._future()
        self.scheduler.tick(self.now)
        submitted = self.scheduler.executor.submit.call_args_list[0][0][0]
        self.assertIs(submitted.__func__, GatewayScheduler._execute)
        self.assertIs(submitted.__self__, self.scheduler)

    def test_no_state_is_written_when_nothing_is_due(self):
        self._future()
        self.due.return_value = False
        self.scheduler.tick(self.now)
        self.assertEqual(self.store.get_state("job.last.trader"), "",
                         "未设置时 store 返回空串（不是 None）—— 断言写错会假红")


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GatewayStore(Path(self.temp.name) / "gateway.db")
        self.scheduler = GatewayScheduler(self.store, max_workers=1)
        self.addCleanup(self.scheduler.shutdown)
        self.schedule = patch.object(SCH, "load_schedule", return_value={}).start()
        self.addCleanup(patch.stopall)

    def _job(self, payload, name):
        return next(j for j in payload["jobs"] if j["name"] == name)

    def test_the_payload_shape(self):
        payload = self.scheduler.status()
        self.assertEqual(set(payload), {"jobs", "recent_runs"})
        self.assertEqual(len(payload["jobs"]), len(current_jobs()))

    def test_nothing_is_reported_as_running_by_default(self):
        self.assertIs(self._job(self.scheduler.status(), "news")["running"], False)

    def test_an_unfinished_future_is_reported_as_running(self):
        future = MagicMock()
        future.done.return_value = False
        self.scheduler.running = {"news": future}
        self.assertIs(self._job(self.scheduler.status(), "news")["running"], True)

    def test_a_finished_future_is_not_reported_as_running(self):
        future = MagicMock()
        future.done.return_value = True
        self.scheduler.running = {"news": future}
        self.assertIs(self._job(self.scheduler.status(), "news")["running"], False)

    def test_an_interval_job_describes_its_period(self):
        self.assertEqual(self._job(self.scheduler.status(), "factor_library")["schedule"],
                         "每 1 分钟")

    def test_a_staggered_job_describes_its_offset(self):
        self.assertIn("错峰 +3m", self._job(self.scheduler.status(), "news")["schedule"])

    def test_timed_jobs_join_their_times(self):
        self.assertEqual(self._job(self.scheduler.status(), "daily_briefing")["schedule"],
                         "08:00、20:00")

    def test_a_configured_time_overrides_the_default(self):
        self.schedule.return_value = {"briefing_times": ["09:00"]}
        self.assertEqual(self._job(self.scheduler.status(), "daily_briefing")["schedule"],
                         "09:00")

    def test_overdue_is_reported_for_a_long_silence(self):
        self.store.set_state("job.last.factor_library", "2020-01-01 00:00:00+08:00")
        self.assertIs(self._job(self.scheduler.status(), "factor_library")["overdue"], True)

    def test_the_last_run_time_is_rendered(self):
        self.store.set_state("job.last.news", "2026-09-20 10:00:00+08:00")
        self.assertIn("2026-09-20", self._job(self.scheduler.status(), "news")["last_scheduled_at"])

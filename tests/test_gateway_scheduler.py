"""Gateway Scheduler timing and migration tests."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


from r20_gateway.scheduler import GatewayScheduler, JOBS, MAINTENANCE_JOB_NAME
from r20_gateway.store import GatewayStore

BJ = timezone(timedelta(hours=8))


class GatewaySchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GatewayStore(Path(self.temp.name) / "gateway.db")
        self.scheduler = GatewayScheduler(self.store, max_workers=1)
        self.now = datetime(2026, 9, 1, 18, 0, tzinfo=BJ)
        self._maint = patch.object(GatewayScheduler, "maintain_pending_entries", return_value={"status": "skipped"})
        self._maint.start()
        self.addCleanup(self._maint.stop)


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



class GatewayPendingMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = GatewayStore(Path(self.temp.name) / "gateway.db")
        self.scheduler = GatewayScheduler(self.store, max_workers=1)
        self.now = datetime(2026, 9, 1, 18, 0, tzinfo=BJ)
        self.scheduler.initialize_migration_baseline(self.now)

    def tearDown(self):
        fut = self.scheduler._maintenance_future
        if fut is not None:
            try:
                fut.result(timeout=2)
            except Exception:
                pass
        self.scheduler.shutdown()
        self.temp.cleanup()


    def _wait_maint(self, timeout=2.0):
        fut = self.scheduler._maintenance_future
        self.assertIsNotNone(fut)
        return fut.result(timeout=timeout)

    def test_maintenance_is_not_a_queued_job_and_skips_success_records(self):
        self.assertFalse(any(spec.name == MAINTENANCE_JOB_NAME for spec in JOBS))
        skipped = {"status": "skipped", "reason": "no_pending", "pending": 0, "blocked": False, "errors": []}
        with patch("r20_gateway.scheduler.load_schedule", return_value={}), patch(
            "scripts.binance_order_maintenance.run_pending_entry_maintenance",
            return_value=skipped,
        ) as maint:
            occupied = type("Fut", (), {"done": lambda self: False})()
            self.scheduler.running["trader"] = occupied
            launched = self.scheduler.tick(self.now)
            self.assertEqual(launched, [])
            self._wait_maint()
            maint.assert_called_once()
            later = self.now + timedelta(seconds=1)
            self.scheduler.tick(later)
            self.assertEqual(maint.call_count, 1)
            due = self.now + timedelta(seconds=5)
            self.scheduler.tick(due)
            self._wait_maint()
            self.assertEqual(maint.call_count, 2)
        self.assertEqual(self.store.job_runs(30), [])

    def test_maintenance_errors_are_recorded_without_job_pool_submit(self):
        failed = {
            "status": "error",
            "reason": "reconcile timeout",
            "pending": 1,
            "blocked": True,
            "errors": ["reconcile timeout"],
        }
        with patch("r20_gateway.scheduler.load_schedule", return_value={}), patch(
            "scripts.binance_order_maintenance.run_pending_entry_maintenance",
            return_value=failed,
        ):
            self.assertEqual(self.scheduler.tick(self.now), [])
            self._wait_maint()
            self.assertEqual(self.scheduler.tick(self.now + timedelta(seconds=5)), [])
            self._wait_maint()
        runs = self.store.job_runs(10)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["job_name"], MAINTENANCE_JOB_NAME)
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("reconcile timeout", runs[0]["detail"])

    def test_tick_returns_before_blocked_maintenance_network(self):
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def blocked():
            started.set()
            release.wait(timeout=5)
            return {"status": "skipped", "reason": "no_pending", "pending": 0, "blocked": False, "errors": []}

        with patch("r20_gateway.scheduler.load_schedule", return_value={}), patch(
            "scripts.binance_order_maintenance.run_pending_entry_maintenance",
            side_effect=blocked,
        ) as maint:
            t0 = time.monotonic()
            launched = self.scheduler.tick(self.now)
            self.assertLess(time.monotonic() - t0, 0.5)
            self.assertEqual(launched, [])
            self.assertTrue(started.wait(timeout=2))
            self.scheduler.tick(self.now + timedelta(seconds=5))
            self.assertEqual(maint.call_count, 1)
            release.set()
            self._wait_maint()

    def test_due_trader_waits_for_maintenance_instead_of_losing_its_slot(self):
        from r20_backend.file_lock import acquire, release as release_file_lock

        lock_path = Path(self.temp.name) / ".ai_factor_trader.lock"
        maintenance_started = threading.Event()
        release_maintenance = threading.Event()
        trader_dispatched = threading.Event()
        trader_started = threading.Event()
        executions = []
        trader = next(spec for spec in JOBS if spec.name == "trader")
        boundary = self.now + timedelta(minutes=15)
        execute = self.scheduler._execute

        def maintain():
            with lock_path.open("a+") as handle:
                acquire(handle, blocking=False)
                try:
                    maintenance_started.set()
                    release_maintenance.wait(timeout=5)
                finally:
                    release_file_lock(handle)
            return {"status": "reconciled", "pending": 0, "blocked": False, "errors": []}

        def dispatch(spec):
            trader_dispatched.set()
            return execute(spec)

        def run_trader(*args, **kwargs):
            trader_started.set()
            with lock_path.open("a+") as handle:
                try:
                    acquire(handle, blocking=False)
                except BlockingIOError:
                    stdout = "[Trader] Skip: another portfolio cycle is still running"
                else:
                    release_file_lock(handle)
                    executions.append("trader")
                    stdout = "trader executed"
            return type("Result", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()

        with patch("r20_gateway.scheduler.current_jobs", return_value=(trader,)), patch(
            "r20_gateway.scheduler.load_schedule", return_value={},
        ), patch("scripts.binance_order_maintenance.run_pending_entry_maintenance", side_effect=maintain), patch(
            "r20_gateway.scheduler.subprocess.run", side_effect=run_trader,
        ), patch.object(self.scheduler, "_execute", side_effect=dispatch):
            self.scheduler.maintain_pending_entries(boundary - timedelta(seconds=1))
            self.assertTrue(maintenance_started.wait(timeout=2))
            self.assertEqual(self.scheduler.tick(boundary), ["trader"])
            future = self.scheduler.running["trader"]
            try:
                self.assertTrue(trader_dispatched.wait(timeout=2))
                self.assertFalse(trader_started.wait(timeout=0.2))
            finally:
                release_maintenance.set()
                self._wait_maint()
                future.result(timeout=2)
        self.assertEqual(executions, ["trader"])
        self.assertEqual(self.store.job_runs(1)[0]["detail"], "trader executed")


if __name__ == "__main__":
    unittest.main()

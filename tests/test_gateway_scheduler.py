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

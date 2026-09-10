"""Timezone regression checks: no trading, real network, or production data writes."""
import unittest
from datetime import datetime, timezone
from r20_backend.time_utils import parse_beijing, beijing_day, beijing_text


class BeijingTimeContractTests(unittest.TestCase):
    def test_same_instant_all_supported_sources(self):
        expected = datetime(2026, 9, 9, 23, 30, 13, tzinfo=timezone.utc)
        for value in [expected, expected.timestamp(), expected.timestamp()*1000,
                      str(expected.timestamp()), '2026-09-09T23:30:13Z',
                      '2026-09-10T07:30:13+08:00', '2026-09-09T16:30:13-07:00',
                      '2026-09-10 07:30:13', '2026-09-09 23:30:13 UTC',
                      '2026-09-10 07:30:13 (北京时间)']:
            with self.subTest(value=value):
                self.assertEqual(parse_beijing(value).timestamp(), expected.timestamp())
                self.assertEqual(beijing_text(value), '2026-09-10 07:30:13')

    def test_beijing_business_day_boundary(self):
        self.assertEqual(beijing_day('2026-09-09T15:59:59Z'), '2026-09-09')
        self.assertEqual(beijing_day('2026-09-09T16:00:00Z'), '2026-09-10')
        self.assertEqual(beijing_day('2026-12-31T16:00:00Z'), '2027-01-01')
        # Mixed-offset ledger rows must land in the same daily risk/report bucket.
        rows = [('2026-09-09T16:01:00Z', -20), ('2026-09-10 01:00:00', -10),
                ('2026-09-09T15:59:59Z', -90)]
        self.assertEqual(sum(p for t,p in rows if beijing_day(t) == '2026-09-10'), -30)

    def test_explicit_utc_legacy_fields(self):
        self.assertEqual(beijing_text('2026-09-09 23:30:13', naive_tz=timezone.utc), '2026-09-10 07:30:13')
        self.assertEqual(beijing_text('2026-09-10T07:30:13+08:00', naive_tz=timezone.utc), '2026-09-10 07:30:13')

    def test_invalid_input_and_epoch_zero(self):
        for value in [None, '', '--', 'invalid', '2026-13-01']:
            self.assertIsNone(parse_beijing(value))
        self.assertEqual(beijing_text(0), '1970-01-01 08:00:00')

    def test_gateway_legacy_and_utc_last_run_normalization(self):
        from r20_gateway.scheduler import GatewayScheduler
        from unittest.mock import Mock
        scheduler = GatewayScheduler(Mock())
        try:
            for value in ['2026-09-10 08:00:00', '2026-09-10T08:00:00+08:00', '2026-09-10T00:00:00Z']:
                scheduler.store.get_state.return_value = value
                self.assertEqual(scheduler._last_at('trader').isoformat(), '2026-09-10T08:00:00+08:00')
        finally:
            scheduler.executor.shutdown(wait=False)

    def test_evolution_join_preserves_offset_before_normalizing(self):
        from scripts.self_improvement_engine import _parse_bj
        self.assertEqual(_parse_bj('2026-09-09T23:30:13Z'), _parse_bj('2026-09-10 07:30:13'))
        self.assertEqual((_parse_bj('2026-09-10 07:30:13') - _parse_bj('2026-09-09T22:30:13Z')).total_seconds(), 3600)

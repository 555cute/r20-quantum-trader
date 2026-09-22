"""Gateway persistence and retry tests without external network calls."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path

from r20_gateway.events import GatewayEvent
from r20_gateway.store import GatewayStore


class GatewayStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GatewayStore(Path(self.temp.name) / "gateway.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_publish_creates_one_delivery_per_channel(self):
        event = GatewayEvent("trade.opened", "开仓", "BTC LONG", priority=90)
        self.store.publish(event, ["qq", "telegram"])
        stats = self.store.stats()
        self.assertEqual(stats["pending"], 2)
        due = self.store.claim_due()
        self.assertEqual({row["channel"] for row in due}, {"qq", "telegram"})

    def test_delivery_success_isolated_from_failure(self):
        event = GatewayEvent("risk.triggered", "风险", "spread", priority=100)
        self.store.publish(event, ["qq", "telegram"])
        due = self.store.claim_due()
        self.store.complete(due[0]["id"])
        self.store.fail(due[1]["id"], due[1]["attempts"], "timeout")
        stats = self.store.stats()
        self.assertEqual(stats["delivered"], 1)
        self.assertEqual(stats["retry"], 1)

    def test_processing_recovered_after_restart(self):
        event = GatewayEvent("briefing.ready", "简报", "daily")
        self.store.publish(event, ["webhook"])
        self.store.claim_due()
        self.assertEqual(self.store.stats()["processing"], 1)
        self.store.recover_processing()
        self.assertEqual(self.store.stats()["retry"], 1)

    def test_critical_event_requires_one_delivered_channel(self):
        event = GatewayEvent("trade.closed", "平仓", "done", priority=95)
        self.store.publish(event, ["qq", "telegram"])
        self.assertEqual(self.store.event_health()["critical_unmet"], 1)
        due = self.store.claim_due()
        self.store.complete(due[0]["id"])
        self.assertEqual(self.store.event_health()["critical_unmet"], 0)

    def test_failed_delivery_becomes_dead_letter(self):
        event = GatewayEvent("service.degraded", "服务", "down")
        self.store.publish(event, ["qq"])
        row = self.store.claim_due()[0]
        self.store.fail(row["id"], 5, "terminal", max_attempts=6)
        self.assertEqual(self.store.stats()["dead"], 1)
        self.assertTrue(self.store.replay_dead(row["id"]))
        self.assertEqual(self.store.stats()["pending"], 1)
        self.assertFalse(self.store.replay_dead(row["id"]))


if __name__ == "__main__":
    unittest.main()


# =====================================================================
# 第二百九十八刀补：连接契约 / 文件权限 / 作业历史 / 运行时状态 / 模型调用
# =====================================================================

import sqlite3  # noqa: E402
from unittest import mock  # noqa: E402


class _TempStore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "gateway.db"
        self.store = GatewayStore(self.path)


class ConnectContractTests(_TempStore):
    def test_a_clean_block_is_committed(self):
        with self.store.connect() as connection:
            connection.execute("INSERT INTO runtime_state(key,value) VALUES ('k','v')")
        self.assertEqual(self.store.get_state("k"), "v")

    def test_an_exception_rolls_the_transaction_back(self):
        with self.assertRaises(RuntimeError):
            with self.store.connect() as connection:
                connection.execute("INSERT INTO runtime_state(key,value) VALUES ('k','v')")
                raise RuntimeError("中途炸了")
        self.assertEqual(self.store.get_state("k"), "")

    def test_the_exception_is_re_raised_not_swallowed(self):
        with self.assertRaises(RuntimeError):
            with self.store.connect():
                raise RuntimeError("必须传出去")

    def test_the_connection_is_closed_afterwards(self):
        with self.store.connect() as connection:
            pass
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_rows_support_name_access(self):
        self.store.set_state("k", "v")
        with self.store.connect() as connection:
            row = connection.execute("SELECT value FROM runtime_state WHERE key='k'").fetchone()
        self.assertEqual(row["value"], "v")

    def test_foreign_keys_are_enforced(self):
        """★ 没有 `PRAGMA foreign_keys=ON` 时 SQLite **默认不校验**外键 —— 孤儿投递会静默入库。"""
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as connection:
                connection.execute(
                    "INSERT INTO deliveries(event_id,channel,next_attempt_at,created_at)"
                    " VALUES ('不存在的event','qq','2026-01-01 00:00:00','2026-01-01 00:00:00')")

    def test_the_parent_directory_is_created(self):
        nested = Path(self.temp.name) / "deep" / "er" / "gateway.db"
        GatewayStore(nested)
        self.assertTrue(nested.exists())


class SecureFilesTests(unittest.TestCase):
    def test_the_database_file_is_owner_only(self):
        """事件正文可能含持仓细节 ⇒ 库文件必须是 0600。"""
        with tempfile.TemporaryDirectory() as folder:
            store = GatewayStore(Path(folder) / "gateway.db")
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)

    def test_a_chmod_failure_is_swallowed(self):
        """老文件系统/只读挂载上 chmod 会失败，但库本身还能用 —— 不许因此起不来。"""
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(Path, "chmod", side_effect=OSError("只读")):
                store = GatewayStore(Path(folder) / "gateway.db")
            self.assertTrue(store.path.exists())
            store.set_state("k", "v")
            self.assertEqual(store.get_state("k"), "v")


class CompletionStatusTests(_TempStore):
    def _delivery(self):
        self.store.publish(GatewayEvent("x", "t", "m"), ["qq"])
        return self.store.claim_due()[0]["id"]

    def test_an_illegal_completion_status_is_refused(self):
        with self.assertRaises(ValueError):
            self.store.complete(self._delivery(), "pending")

    def test_delivered_is_the_default(self):
        delivery_id = self._delivery()
        self.store.complete(delivery_id)
        self.assertEqual(self.store.stats()["delivered"], 1)

    def test_an_accepted_delivery_keeps_a_truncated_detail(self):
        delivery_id = self._delivery()
        self.store.complete(delivery_id, "accepted", "收" * 2000)
        row = self.store.recent()[0]
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(len(row["last_error"]), 1000)

    def test_a_delivered_delivery_clears_the_detail(self):
        """成功投递不留错误文本 —— 否则面板会一直显示上次的失败原因。"""
        delivery_id = self._delivery()
        self.store.complete(delivery_id, "delivered", "别留我")
        self.assertEqual(self.store.recent()[0]["last_error"], "")

    def test_the_delivery_time_is_stamped(self):
        delivery_id = self._delivery()
        self.store.complete(delivery_id)
        self.assertNotEqual(self.store.recent()[0]["delivered_at"], "")


class JobRunTests(_TempStore):
    def test_begin_job_returns_a_usable_id(self):
        run_id = self.store.begin_job("trader")
        self.assertGreater(run_id, 0)

    def test_ids_increase(self):
        self.assertLess(self.store.begin_job("a"), self.store.begin_job("b"))

    def test_a_started_job_is_running(self):
        run_id = self.store.begin_job("trader")
        row = next(r for r in self.store.job_runs() if r["id"] == run_id)
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["job_name"], "trader")
        self.assertNotEqual(row["started_at"], "")

    def test_a_started_job_has_no_finish_time_yet(self):
        run_id = self.store.begin_job("trader")
        row = next(r for r in self.store.job_runs() if r["id"] == run_id)
        self.assertEqual(row["finished_at"], "")

    def test_exit_code_zero_means_success(self):
        run_id = self.store.begin_job("trader")
        self.store.finish_job(run_id, 0, "一切正常")
        row = next(r for r in self.store.job_runs() if r["id"] == run_id)
        self.assertEqual(row["status"], "success")
        self.assertEqual(row["return_code"], 0)

    def test_a_nonzero_exit_code_means_failed(self):
        run_id = self.store.begin_job("trader")
        self.store.finish_job(run_id, 3, "崩了")
        row = next(r for r in self.store.job_runs() if r["id"] == run_id)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["return_code"], 3)

    def test_the_detail_is_capped_at_two_thousand_chars(self):
        run_id = self.store.begin_job("trader")
        self.store.finish_job(run_id, 0, "x" * 5000)
        row = next(r for r in self.store.job_runs() if r["id"] == run_id)
        self.assertEqual(len(row["detail"]), 2000)

    def test_job_runs_are_newest_first(self):
        first = self.store.begin_job("a")
        second = self.store.begin_job("b")
        self.assertEqual([r["id"] for r in self.store.job_runs()][:2], [second, first])

    def test_the_job_runs_limit_is_clamped(self):
        self.store.begin_job("a")
        self.assertEqual(len(self.store.job_runs(0)), 1, "0 会被夹到 1")
        self.assertEqual(len(self.store.job_runs(-5)), 1)
        self.assertEqual(len(self.store.job_runs(9999)), 1)

    def test_an_empty_history_is_an_empty_list(self):
        self.assertEqual(self.store.job_runs(), [])


class StaleJobRunRecoveryTests(_TempStore):
    def test_a_running_row_is_adopted_as_interrupted(self):
        run_id = self.store.begin_job("trader")
        self.assertEqual(self.store.recover_stale_job_runs(), 1)
        row = next(r for r in self.store.job_runs() if r["id"] == run_id)
        self.assertEqual(row["status"], "interrupted")

    def test_the_adoption_is_honestly_labelled(self):
        """★ 不冒充 success/failed：要留得下"进程崩过"的证据。"""
        self.store.begin_job("trader")
        self.store.recover_stale_job_runs()
        self.assertIn("进程终止未收尾", self.store.job_runs()[0]["detail"])

    def test_a_finished_row_is_left_alone(self):
        run_id = self.store.begin_job("trader")
        self.store.finish_job(run_id, 0, "ok")
        self.assertEqual(self.store.recover_stale_job_runs(), 0)
        self.assertEqual(self.store.job_runs()[0]["status"], "success")

    def test_it_returns_the_number_of_adopted_rows(self):
        self.store.begin_job("a")
        self.store.begin_job("b")
        self.assertEqual(self.store.recover_stale_job_runs(), 2)

    def test_nothing_to_adopt_returns_zero(self):
        self.assertEqual(self.store.recover_stale_job_runs(), 0)

    def test_it_is_idempotent(self):
        self.store.begin_job("a")
        self.store.recover_stale_job_runs()
        self.assertEqual(self.store.recover_stale_job_runs(), 0)


class PruneVacuumFailureTests(_TempStore):
    def _old_finished_run(self):
        with self.store.connect() as connection:
            connection.execute(
                "INSERT INTO job_runs(job_name,status,started_at) VALUES ('old','success','2020-01-01 00:00:00')")

    def test_a_vacuum_failure_is_reported_not_raised(self):
        """★ VACUUM 需独占写锁，可能被并发写挤掉 —— 失败只记 `vacuumed=False`。"""
        self._old_finished_run()
        real_connect = GatewayStore.connect
        calls = {"n": 0}

        def _connect(_self):
            calls["n"] += 1
            if calls["n"] == 2:      # 第 1 次是 DELETE，第 2 次是 VACUUM
                raise sqlite3.OperationalError("database is locked")
            return real_connect(_self)

        with mock.patch.object(GatewayStore, "connect", _connect):
            result = self.store.prune_job_runs(keep_days=1)
        self.assertEqual(result["deleted"], 1, "删除本身仍要成功")
        self.assertIs(result["vacuumed"], False)

    def test_a_successful_vacuum_is_reported(self):
        self._old_finished_run()
        self.assertIs(self.store.prune_job_runs(keep_days=1)["vacuumed"], True)

    def test_nothing_deleted_skips_the_vacuum_entirely(self):
        self.assertIs(self.store.prune_job_runs(keep_days=1)["vacuumed"], False)

    def test_vacuum_can_be_disabled(self):
        self._old_finished_run()
        result = self.store.prune_job_runs(keep_days=1, vacuum=False)
        self.assertEqual(result["deleted"], 1)
        self.assertIs(result["vacuumed"], False)


class ModelCallStatsTests(_TempStore):
    def _record(self, **over):
        row = {"caller": "trader", "model": "m-1", "reasoning_effort": "high",
               "status": "success", "started_at": "2026-09-20 10:00:00",
               "duration_ms": 1000, "input_chars": 10, "output_chars": 20,
               "prompt_fingerprint": "fp", "prompt_transport": "python-direct",
               "input_tokens": 5, "output_tokens": 7, "total_tokens": 12,
               "error_type": ""}
        row.update(over)
        return self.store.record_model_call(row)

    def test_an_empty_table_reports_zeros(self):
        self.assertEqual(self.store.model_stats(),
                         {"total_calls": 0, "successful_calls": 0,
                          "avg_duration_ms": 0, "total_tokens": 0})

    def test_record_model_call_returns_an_id(self):
        self.assertGreater(self._record(), 0)

    def test_the_counts_and_averages(self):
        self._record(duration_ms=1000, total_tokens=10)
        self._record(duration_ms=3000, total_tokens=20)
        stats = self.store.model_stats()
        self.assertEqual(stats["total_calls"], 2)
        self.assertEqual(stats["successful_calls"], 2)
        self.assertEqual(stats["avg_duration_ms"], 2000)
        self.assertEqual(stats["total_tokens"], 30)

    def test_only_success_counts_as_successful(self):
        self._record()
        self._record(status="failed")
        stats = self.store.model_stats()
        self.assertEqual(stats["total_calls"], 2)
        self.assertEqual(stats["successful_calls"], 1)

    def test_null_tokens_do_not_break_the_sum(self):
        self._record(total_tokens=None)
        self._record(total_tokens=5)
        self.assertEqual(self.store.model_stats()["total_tokens"], 5)

    def test_model_calls_are_newest_first(self):
        first = self._record()
        second = self._record()
        self.assertEqual([r["id"] for r in self.store.model_calls()][:2], [second, first])

    def test_the_model_calls_limit_is_clamped(self):
        self._record()
        self.assertEqual(len(self.store.model_calls(0)), 1)
        self.assertEqual(len(self.store.model_calls(9999)), 1)

    def test_the_schema_defaults_are_unreachable_through_this_api(self):
        """🐞 如实登记：`record_model_call` 把**全部 14 列**都写进 INSERT 列表，
        缺失的列传的是**显式 `NULL`** —— 而 SQLite 的 `DEFAULT` 只在**列被整个省略**
        时才生效。于是 schema 里那两条 `NOT NULL DEFAULT ...`
        （`prompt_transport DEFAULT 'python-direct'`、`error_type DEFAULT ''`）
        **永远用不上**：调用方漏了键就吃 `IntegrityError`，而不是优雅取默认值。
        """
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.record_model_call({"caller": "c", "model": "m",
                                          "reasoning_effort": "low",
                                          "status": "success",
                                          "started_at": "2026-09-20 10:00:00",
                                          "duration_ms": 1, "input_chars": 1,
                                          "output_chars": 1,
                                          "prompt_fingerprint": "fp"})

    def test_the_three_nullable_columns_may_be_omitted(self):
        """只有 token 三兄弟是**真可空**的；其余 11 列一个都不能少。"""
        row_id = self.store.record_model_call({
            "caller": "c", "model": "m", "reasoning_effort": "low",
            "status": "success", "started_at": "2026-09-20 10:00:00",
            "duration_ms": 1, "input_chars": 1, "output_chars": 1,
            "prompt_fingerprint": "fp", "prompt_transport": "python-direct",
            "error_type": ""})
        stored = next(r for r in self.store.model_calls() if r["id"] == row_id)
        self.assertIsNone(stored["input_tokens"])
        self.assertIsNone(stored["output_tokens"])
        self.assertIsNone(stored["total_tokens"])


class RuntimeStateTests(_TempStore):
    def test_a_missing_key_returns_an_empty_string(self):
        self.assertEqual(self.store.get_state("查无此键"), "")

    def test_the_default_is_configurable(self):
        self.assertEqual(self.store.get_state("查无此键", "兜底"), "兜底")

    def test_the_default_is_returned_verbatim(self):
        """不是 `str(default)` —— 传 None 就该拿回 None（调用方可能靠它判存在性）。"""
        self.assertIsNone(self.store.get_state("查无此键", None))

    def test_set_state_then_get_state_round_trips(self):
        self.store.set_state("k", "v")
        self.assertEqual(self.store.get_state("k"), "v")

    def test_set_state_upserts(self):
        self.store.set_state("k", "v1")
        self.store.set_state("k", "v2")
        self.assertEqual(self.store.get_state("k"), "v2")

    def test_the_stored_value_is_stringified_on_read(self):
        self.store.set_state("k", "123")
        self.assertIsInstance(self.store.get_state("k"), str)

    def test_a_json_blob_round_trips(self):
        self.store.set_state("k", '{"a": [1, 2]}')
        self.assertEqual(self.store.get_state("k"), '{"a": [1, 2]}')


class RecentDeliveryTests(_TempStore):
    def test_an_empty_queue_is_an_empty_list(self):
        self.assertEqual(self.store.recent(), [])

    def test_the_event_fields_are_joined_in(self):
        event = GatewayEvent("trade.opened", "开仓", "BTC LONG", priority=80)
        self.store.publish(event, ["qq", "telegram"])
        rows = self.store.recent()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "开仓")
        self.assertEqual(rows[0]["message"], "BTC LONG")
        self.assertEqual(rows[0]["priority"], 80)
        self.assertEqual(rows[0]["event_id"], event.event_id)

    def test_the_newest_delivery_comes_first(self):
        self.store.publish(GatewayEvent("a", "第一", "m"), ["qq"])
        self.store.publish(GatewayEvent("b", "第二", "m"), ["qq"])
        self.assertEqual([r["title"] for r in self.store.recent()], ["第二", "第一"])

    def test_the_limit_is_clamped(self):
        self.store.publish(GatewayEvent("a", "t", "m"), ["qq"])
        self.assertEqual(len(self.store.recent(0)), 1)
        self.assertEqual(len(self.store.recent(-3)), 1)
        self.assertEqual(len(self.store.recent(9999)), 1)

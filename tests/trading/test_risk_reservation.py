"""US-001 · risk_reservation 测试（≥10 例）。

封闭三律：零真实网络、零真实凭证、零 data/** 写入——全部用 tempfile
临时 SQLite 库，manager 直接传临时路径，不触碰模块级 DEFAULT_DB_PATH。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from r20_backend import risk_reservation as RR
from r20_backend.risk_reservation import (
    ReservationError,
    ReservationExceeded,
    RiskReservationManager,
    STATE_CONFIRMED,
    STATE_CLOSED,
    STATE_PARTIAL,
    STATE_PENDING,
    STATE_PENDING_CLEANUP,
    STATE_REJECTED,
    STATE_UNKNOWN,
)


def make_manager(**kw) -> RiskReservationManager:
    """临时目录里的独立库；测试结束随 TemporaryDirectory 一并清理。"""
    tmp = tempfile.mkdtemp(prefix="risk_resv_test_")
    return RiskReservationManager(os.path.join(tmp, "resv.db"), **kw), tmp


class RiskReservationTestBase(unittest.TestCase):
    def setUp(self):
        self.mgr, self.tmpdir = make_manager()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmpdir, ignore_errors=True))
        self.okx_live = ("okx", "live", "fp_live_1")
        self.okx_demo = ("okx", "demo", "fp_demo_1")
        self.gate_live = ("gate", "live", "fp_gate_1")
        self.binance_live = ("binance", "live", "fp_bn_1")


class TestReserveBasics(RiskReservationTestBase):
    def test_01_reserve_idempotent_same_intent(self):
        """AC1: 同 intent_id 重复预留幂等，不双份占用。"""
        self.mgr.reserve(self.okx_live, "intent-A", 100.0, STATE_PENDING)
        snap = self.mgr.reserve(self.okx_live, "intent-A", 100.0, STATE_PENDING)
        self.assertEqual(snap["state"], STATE_PENDING)
        self.assertFalse(snap["released"])
        self.assertAlmostEqual(self.mgr.total_reserved(self.okx_live), 100.0)

    def test_02_unknown_state_occupies_and_never_releases(self):
        """AC2: unknown 未知结果全额占用，不释放预算。"""
        self.mgr.reserve(self.okx_live, "intent-U", 250.0, STATE_PENDING)
        self.mgr.reserve(self.okx_live, "intent-U", 250.0, STATE_UNKNOWN)
        snap = self.mgr.reservations(self.okx_live)[0]
        self.assertEqual(snap["state"], STATE_UNKNOWN)
        self.assertFalse(snap["released"])
        self.assertAlmostEqual(self.mgr.total_reserved(self.okx_live), 250.0)

    def test_03_partial_state_occupies(self):
        """partial 部分成交仍全额占用。"""
        self.mgr.reserve(self.okx_live, "intent-P", 80.0, STATE_PENDING)
        self.mgr.reserve(self.okx_live, "intent-P", 80.0, STATE_PARTIAL)
        self.assertAlmostEqual(self.mgr.total_reserved(self.okx_live), 80.0)
        self.assertFalse(self.mgr.reservations(self.okx_live)[0]["released"])

    def test_04_rejected_releases(self):
        """AC2: rejected 确认终态 → 释放。"""
        self.mgr.reserve(self.okx_live, "intent-R", 120.0, STATE_PENDING)
        self.mgr.reserve(self.okx_live, "intent-R", 120.0, STATE_REJECTED)
        self.assertAlmostEqual(self.mgr.total_reserved(self.okx_live), 0.0)
        self.assertTrue(self.mgr.reservations(self.okx_live)[0]["released"])

    def test_05_closed_releases_after_confirmed(self):
        """AC2: confirmed（持仓中仍占用）→ closed 平仓才释放。"""
        self.mgr.reserve(self.okx_live, "intent-C", 300.0, STATE_PENDING)
        self.mgr.reserve(self.okx_live, "intent-C", 300.0, STATE_CONFIRMED)
        self.assertAlmostEqual(self.mgr.total_reserved(self.okx_live), 300.0)
        self.mgr.reserve(self.okx_live, "intent-C", 300.0, STATE_CLOSED)
        self.assertAlmostEqual(self.mgr.total_reserved(self.okx_live), 0.0)

    def test_06_terminal_idempotent_cannot_revive(self):
        """AC8: 终态幂等——closed 后重复调用不改写、不复活、不重复释放。"""
        self.mgr.reserve(self.okx_live, "intent-T", 50.0, STATE_PENDING)
        self.mgr.reserve(self.okx_live, "intent-T", 50.0, STATE_CLOSED)
        again = self.mgr.reserve(self.okx_live, "intent-T", 999.0, STATE_PENDING)
        self.assertEqual(again["state"], STATE_CLOSED)
        self.assertTrue(again["released"])
        self.assertAlmostEqual(self.mgr.total_reserved(self.okx_live), 0.0)

    def test_07_invalid_state_rejected(self):
        """状态词表外直接拒绝。"""
        with self.assertRaises(ReservationError):
            self.mgr.reserve(self.okx_live, "intent-X", 10.0, "flying")
        with self.assertRaises(ReservationError):
            self.mgr.reserve(self.okx_live, "intent-X", 10.0, STATE_CLOSED)  # 新预留禁终态起步


class TestBudgetLimitAndIsolation(RiskReservationTestBase):
    def test_08_exceed_limit_raises_reservation_exceeded(self):
        """AC7: 超限拒，抛 ReservationExceeded，且不产生占用。"""
        mgr, tmp = make_manager(total_limit_usdt=1000.0)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        mgr.reserve(self.okx_live, "i1", 600.0, STATE_PENDING)
        with self.assertRaises(ReservationExceeded):
            mgr.reserve(self.okx_live, "i2", 500.0, STATE_PENDING)
        self.assertAlmostEqual(mgr.total_reserved(self.okx_live), 600.0)
        # 未被接纳的 i2 不留半占用行
        self.assertEqual([r["intent_id"] for r in mgr.reservations(self.okx_live)], ["i1"])

    def test_09_account_key_isolation_per_venue(self):
        """AC10: 不同 venue（及不同环境）预算独立，互不挤占。"""
        mgr, tmp = make_manager(total_limit_usdt=500.0)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        mgr.reserve(self.okx_live, "a", 400.0, STATE_PENDING)
        mgr.reserve(self.gate_live, "b", 400.0, STATE_PENDING)   # gate_live 不受 okx_live 挤占
        mgr.reserve(self.okx_demo, "c", 400.0, STATE_PENDING)    # demo 环境独立
        self.assertAlmostEqual(mgr.total_reserved(self.okx_live), 400.0)
        self.assertAlmostEqual(mgr.total_reserved(self.gate_live), 400.0)
        self.assertAlmostEqual(mgr.total_reserved(self.okx_demo), 400.0)
        with self.assertRaises(ReservationExceeded):
            mgr.reserve(self.okx_live, "d", 200.0, STATE_PENDING)

    def test_10_gross_exposure_environment_aggregate(self):
        """AC4: gross_exposure(environment) 聚合同环境跨所 + 每所明细。"""
        mgr, tmp = make_manager()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        mgr.reserve(self.okx_live, "a", 100.0, STATE_PENDING)
        mgr.reserve(self.binance_live, "b", 150.0, STATE_PENDING)
        mgr.reserve(self.gate_live, "c", 50.0, STATE_PENDING)
        mgr.reserve(self.okx_demo, "d", 777.0, STATE_PENDING)  # 另一环境不计入
        by_venue = mgr.total_reserved_by_venue("live")
        self.assertAlmostEqual(by_venue["okx"], 100.0)
        self.assertAlmostEqual(by_venue["binance"], 150.0)
        self.assertAlmostEqual(by_venue["gate"], 50.0)
        self.assertAlmostEqual(mgr.gross_exposure("live"), 300.0)
        self.assertAlmostEqual(mgr.gross_exposure("demo"), 777.0)


class TestPersistenceAndRecovery(RiskReservationTestBase):
    def test_11_restart_recovery_rebuilds_occupancy(self):
        """AC3: 进程重启（新 manager 同库）后占用视图从库重建。"""
        db = os.path.join(self.tmpdir, "resv.db")
        self.mgr.reserve(self.okx_live, "a", 200.0, STATE_PENDING)
        self.mgr.reserve(self.okx_live, "b", 300.0, STATE_CONFIRMED)
        self.mgr.reserve(self.okx_live, "dead", 100.0, STATE_PENDING)
        self.mgr.reserve(self.okx_live, "dead", 100.0, STATE_CLOSED)  # 已释放不复活
        reborn = RiskReservationManager(db)
        stats = reborn.recovery()  # 保守恢复：未知意图继续占用
        self.assertAlmostEqual(reborn.total_reserved(self.okx_live), 500.0)
        self.assertEqual(stats["recovered_active"], 2)

    def test_12_recovery_orphan_marked_pending_cleanup_still_occupied(self):
        """AC3: 孤儿预留（无对应开放意图）标 pending_cleanup，仍占用不自动释放。"""
        self.mgr.reserve(self.okx_live, "open-1", 100.0, STATE_PENDING)
        self.mgr.reserve(self.okx_live, "orphan-9", 400.0, STATE_UNKNOWN)
        stats = self.mgr.recovery(known_open_intents={"open-1"})
        self.assertEqual(stats["orphans_marked"], 1)
        self.assertIn("orphan-9", stats["orphan_intent_ids"])
        snap = {r["intent_id"]: r for r in self.mgr.reservations(self.okx_live)}
        self.assertEqual(snap["orphan-9"]["state"], STATE_PENDING_CLEANUP)
        self.assertFalse(snap["orphan-9"]["released"])
        # pending_cleanup 仍计入占用，直到显式 release
        self.assertAlmostEqual(self.mgr.total_reserved(self.okx_live), 500.0)
        self.mgr.release(self.okx_live, "orphan-9", STATE_CLOSED)
        self.assertAlmostEqual(self.mgr.total_reserved(self.okx_live), 100.0)

    def test_13_persistence_survives_across_connections_raw_sql_check(self):
        """数据确实落在 SQLite 文件（非内存态）。"""
        db = os.path.join(self.tmpdir, "resv.db")
        self.mgr.reserve(self.gate_live, "x", 42.0, STATE_PENDING)
        raw = sqlite3.connect(db)
        try:
            n = raw.execute(
                "SELECT COUNT(*) FROM risk_reservations WHERE intent_id='x'").fetchone()[0]
        finally:
            raw.close()
        self.assertEqual(n, 1)


class TestConcurrency(RiskReservationTestBase):
    def test_14_concurrent_reserve_never_exceeds_limit(self):
        """AC8: 多线程同账户并发 reserve，总额不超上限，成功数确定。"""
        mgr, tmp = make_manager(total_limit_usdt=1000.0)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        results, exceeded, errors = [], [], []
        barrier = threading.Barrier(8)
        lock = threading.Lock()

        def worker(i):
            try:
                barrier.wait(timeout=10)
                mgr.reserve(self.okx_live, f"t-{i}", 300.0, STATE_PENDING)
                with lock:
                    results.append(i)
            except ReservationExceeded:
                with lock:
                    exceeded.append(i)
            except Exception as e:  # 其他异常=测试失败
                with lock:
                    errors.append(f"unexpected:{e!r}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(len(errors), 0, f"非预期失败: {errors}")
        self.assertEqual(len(results), 3)  # 1000 上限只容 3×300
        self.assertEqual(len(exceeded), 5)  # 其余 5 线程正确被拒
        total = mgr.total_reserved(self.okx_live)
        self.assertLessEqual(total, 1000.0)
        self.assertAlmostEqual(total, 900.0)

    def test_15_concurrent_mixed_reserve_and_release_consistent(self):
        """并发 reserve/release 混合后，占用与台账一致（无幽灵占用）。"""
        mgr, tmp = make_manager(total_limit_usdt=1000.0)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        mgr.reserve(self.okx_live, "seed", 1000.0, STATE_PENDING)
        barrier = threading.Barrier(6)

        def releaser():
            barrier.wait(timeout=10)
            mgr.reserve(self.okx_live, "seed", 1000.0, STATE_REJECTED)

        def reserver(i):
            barrier.wait(timeout=10)
            try:
                mgr.reserve(self.okx_live, f"n-{i}", 500.0, STATE_PENDING)
            except ReservationExceeded:
                pass

        ts = [threading.Thread(target=releaser)] + \
             [threading.Thread(target=reserver, args=(i,)) for i in range(5)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)
        total = mgr.total_reserved(self.okx_live)
        rows = mgr.reservations(self.okx_live)
        recomputed = sum(r["amount_usdt"] for r in rows if not r["released"])
        self.assertAlmostEqual(total, recomputed)
        self.assertLessEqual(total, 1000.0)
        self.assertTrue(any(r["intent_id"] == "seed" and r["released"] for r in rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestAccountKeyNormalisation(unittest.TestCase):
    """`_normalize_account_key` 的四种入参形态（对象 / 元组 / 三段串 / 退化串）。"""

    def test_an_object_with_venue_environment_and_fingerprint(self):
        class Key:
            venue = "okx"
            environment = "live"
            fingerprint = "fp-1"

        self.assertEqual(RR._normalize_account_key(Key()),
                         ("okx:live:fp-1", "okx", "live"))

    def test_an_object_without_a_fingerprint_keeps_an_empty_tail(self):
        class Key:
            venue = "gate"
            environment = "demo"

        self.assertEqual(RR._normalize_account_key(Key()), ("gate:demo:", "gate", "demo"))

    def test_a_tuple_is_accepted(self):
        self.assertEqual(RR._normalize_account_key(("okx", "live", "fp-1")),
                         ("okx:live:fp-1", "okx", "live"))

    def test_a_list_is_accepted(self):
        self.assertEqual(RR._normalize_account_key(["okx", "live", "fp-1"]),
                         ("okx:live:fp-1", "okx", "live"))

    def test_a_three_part_string_is_accepted(self):
        self.assertEqual(RR._normalize_account_key("okx:live:fp-1"),
                         ("okx:live:fp-1", "okx", "live"))

    def test_a_degenerate_string_honestly_records_empty_parts(self):
        """拆不出 venue/environment 时**诚实记空**，不冒充一个所名。"""
        self.assertEqual(RR._normalize_account_key("solo"), ("solo", "", ""))

    def test_a_four_part_string_is_also_degenerate(self):
        self.assertEqual(RR._normalize_account_key("a:b:c:d"), ("a:b:c:d", "", ""))

    def test_a_two_part_string_is_also_degenerate(self):
        self.assertEqual(RR._normalize_account_key("okx:live"), ("okx:live", "", ""))


class TestReserveEdgeCases(RiskReservationTestBase):
    def test_a_negative_amount_is_rejected(self):
        with self.assertRaises(ReservationError) as ctx:
            self.mgr.reserve(self.okx_live, "i1", -1.0, STATE_PENDING)
        self.assertIn("不可为负", str(ctx.exception))

    def test_an_adjustment_that_breaks_the_budget_raises(self):
        """已存在行的**差额重查**：others(0) + 调整后(250) > 上限(200) ⇒ 拒。"""
        mgr, tmp = make_manager(total_limit_usdt=200.0)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        mgr.reserve(self.okx_live, "i1", 150.0, STATE_PENDING)
        with self.assertRaises(ReservationExceeded) as ctx:
            mgr.reserve(self.okx_live, "i1", 250.0, STATE_PENDING)
        self.assertIn("其他占用", str(ctx.exception))
        self.assertAlmostEqual(mgr.total_reserved(self.okx_live), 150.0,
                               msg="被拒的调整不得改动原预留额")

    def test_an_adjustment_within_the_budget_succeeds(self):
        mgr, tmp = make_manager(total_limit_usdt=200.0)
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        mgr.reserve(self.okx_live, "i1", 150.0, STATE_PENDING)
        self.assertAlmostEqual(
            mgr.reserve(self.okx_live, "i1", 180.0, STATE_PENDING)["amount_usdt"], 180.0)

    def test_a_zero_amount_keeps_the_existing_reservation(self):
        """amount=0 是"保持原额"的信号（release/confirm 靠它做语义糖）。"""
        self.mgr.reserve(self.okx_live, "i1", 120.0, STATE_PENDING)
        out = self.mgr.reserve(self.okx_live, "i1", 0.0, STATE_PARTIAL)
        self.assertAlmostEqual(out["amount_usdt"], 120.0)
        self.assertEqual(out["state"], STATE_PARTIAL)

    def test_a_rollback_failure_is_swallowed_so_the_original_error_wins(self):
        """第 194 行：回滚本身再抛错时，**原始错误必须胜出**（不能被回滚错误顶掉）。

        ⚠️ 不能 `patch.object(sqlite3.Connection, "rollback")` —— 它是**不可变的 C 类型**，
        patch 时能装上、退出时 `setattr` 还原会抛 `TypeError`。故用一个只覆写
        `rollback`、其余全部委托给真连接的包装对象。
        """

        class _RollbackBoom:
            def __init__(self, conn):
                self._conn = conn

            def __getattr__(self, item):
                return getattr(self._conn, item)

            def rollback(self):
                raise RuntimeError("回滚也坏了")

        wrapper = _RollbackBoom(self.mgr._connect())
        with mock.patch.object(self.mgr, "_connect", return_value=wrapper):
            with self.assertRaises(ReservationError) as ctx:
                self.mgr.reserve(self.okx_live, "i1", 10.0, STATE_REJECTED)
        self.assertIn("初始状态须为占用态", str(ctx.exception))

    def test_release_rejects_a_non_terminal_state(self):
        with self.assertRaises(ReservationError) as ctx:
            self.mgr.release(self.okx_live, "i1", state=STATE_PENDING)
        self.assertIn("只接受终态", str(ctx.exception))

    def test_release_defaults_to_closed(self):
        self.mgr.reserve(self.okx_live, "i1", 100.0, STATE_PENDING)
        self.assertEqual(self.mgr.release(self.okx_live, "i1")["state"], STATE_CLOSED)
        self.assertAlmostEqual(self.mgr.total_reserved(self.okx_live), 0.0)

    def test_confirm_advances_pending_to_confirmed_and_still_occupies(self):
        """confirmed 仍占预算直到终态 —— 这是设计本意（§6 状态机）。"""
        self.mgr.reserve(self.okx_live, "i1", 100.0, STATE_PENDING)
        out = self.mgr.confirm(self.okx_live, "i1")
        self.assertEqual(out["state"], STATE_CONFIRMED)
        self.assertAlmostEqual(out["amount_usdt"], 100.0)
        self.assertAlmostEqual(self.mgr.total_reserved(self.okx_live), 100.0)


class TestRecoveryFailurePath(RiskReservationTestBase):
    def test_a_failure_rolls_back_closes_and_reraises(self):
        """第 317/318/320 行：恢复失败必须**回滚 + 关连接 + 原样抛出**。"""
        fake = mock.MagicMock()
        fake.execute.side_effect = sqlite3.OperationalError("库坏了")
        with mock.patch.object(self.mgr, "_connect", return_value=fake):
            with self.assertRaises(sqlite3.OperationalError) as ctx:
                self.mgr.recovery()
        self.assertIn("库坏了", str(ctx.exception))
        self.assertTrue(fake.rollback.called, "异常路径上必须回滚")
        self.assertTrue(fake.close.called, "finally 里必须关连接")


class TestDefaultManagerCaching(unittest.TestCase):
    """模块级默认实例：**必须能被重置**，否则一次未沙箱的调用就把实例永久钉在生产库上。"""

    def setUp(self):
        self._orig = RR._default_manager
        self.tmp = tempfile.mkdtemp(prefix="resv_default_")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.addCleanup(setattr, RR, "_default_manager", self._orig)
        RR.reset_default_manager()

    def test_reset_clears_the_cache(self):
        RR.reset_default_manager()
        self.assertIsNone(RR._default_manager)

    def test_get_manager_caches_one_instance_for_the_default_path(self):
        with mock.patch.object(RR, "DEFAULT_DB_PATH", os.path.join(self.tmp, "d.db")):
            first = RR.get_manager()
            self.assertIs(RR.get_manager(), first)
            self.assertIs(RR._default_manager, first)

    def test_reset_forces_a_fresh_instance(self):
        with mock.patch.object(RR, "DEFAULT_DB_PATH", os.path.join(self.tmp, "d.db")):
            first = RR.get_manager()
            RR.reset_default_manager()
            self.assertIsNot(RR.get_manager(), first)

    def test_an_explicit_path_bypasses_the_cache(self):
        with mock.patch.object(RR, "DEFAULT_DB_PATH", os.path.join(self.tmp, "d.db")):
            cached = RR.get_manager()
            other = RR.get_manager(db_path=os.path.join(self.tmp, "other.db"))
            self.assertIsNot(other, cached)
            self.assertIs(RR._default_manager, cached, "显式路径不得污染模块级缓存")

    def test_an_explicit_path_is_usable(self):
        mgr = RR.get_manager(db_path=os.path.join(self.tmp, "other.db"),
                             total_limit_usdt=50.0)
        mgr.reserve(("okx", "live", "fp"), "i1", 10.0, STATE_PENDING)
        self.assertAlmostEqual(mgr.total_reserved(("okx", "live", "fp")), 10.0)


if __name__ == "__main__":
    unittest.main()

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

# -*- coding: utf-8 -*-
"""US-010 预留对账释放器：账实相符回笼陈旧占用（全离线，临时 DB）。

封闭三律：
① manager 落 tempfile；跨所快照由参数注入（fetch 已 patch 掉，绝不出网）；
② 时间操控直接改临时库 updated_at（UTC 字符串，与 SQLite CURRENT_TIMESTAMP 同格式）；
③ 不触碰真实 data/risk_reservation.db、不写交易所。
"""
from __future__ import annotations

import datetime
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import scripts.ai_factor_trader as trader
import shutil
from r20_backend import risk_reservation


def _utc_stamp(seconds_ago: float) -> str:
    ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds_ago)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


class ReservationReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="us010-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = os.path.join(self.tmp, "res.db")
        self.mgr = risk_reservation.get_manager(db_path=self.db)
        self.out = ""

    def _reserve(self, venue, env, intent, amount, age_s):
        key = (venue, env, "fp-test")
        self.mgr.reserve(key, intent, amount, state="pending")
        con = sqlite3.connect(self.db)
        con.execute("UPDATE risk_reservations SET updated_at = ?, created_at = ? "
                    "WHERE intent_id = ?", (_utc_stamp(age_s), _utc_stamp(age_s), intent))
        con.commit()
        con.close()

    def _run(self, real_pos, pending, env="demo", snapshot=None, verified=True,
             fetch_ok=True):
        with patch.object(trader, "reservation_manager", lambda: self.mgr), \
                patch.object(trader, "fetch_other_venue_positions",
                             lambda e: (fetch_ok, snapshot or {},
                                        "" if fetch_ok else "binance 读取失败")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                n = trader.reconcile_reservation_ledger(real_pos, pending, env,
                                                        venue_snapshot=snapshot,
                                                        venue_snapshot_verified=verified)
        self.out = buf.getvalue()
        return n

    def _unreleased(self):
        return {r["intent_id"] for r in self.mgr.list_unreleased("demo")}

    # ---- 核心矩阵 ----
    def test_stale_no_position_released(self):
        self._reserve("okx", "demo", "BTC-USDT-SWAP:BUY_LONG:111", 200.0, age_s=9999)
        n = self._run(real_pos={}, pending=set())
        self.assertEqual(n, 1)
        self.assertNotIn("BTC-USDT-SWAP:BUY_LONG:111", self._unreleased())
        self.assertIn("state=closed", self.out)
        # 释放必须落到终态而非删除：历史可审计
        snaps = [r for r in self.mgr.reservations() if r["intent_id"].endswith(":111")]
        self.assertEqual(snaps[0]["state"], risk_reservation.STATE_CLOSED)
        self.assertFalse(snaps[0]["released"] is False and snaps[0]["state"] == "pending")

    def test_recent_intent_preserved(self):
        self._reserve("okx", "demo", "ETH-USDT-SWAP:SELL_SHORT:222", 100.0, age_s=60)
        n = self._run(real_pos={}, pending=set())
        self.assertEqual(n, 0, "TTL 未到不得释放（本周期新预留/成交在途窗口）")
        self.assertIn("ETH-USDT-SWAP:SELL_SHORT:222", self._unreleased())

    def test_live_position_preserved_regardless_of_age(self):
        self._reserve("okx", "demo", "SOL-USDT-SWAP:BUY_LONG:333", 300.0, age_s=99999)
        real_pos = {"SOL-USDT-SWAP": {"posSide": "long", "pos": "5"}}
        n = self._run(real_pos=real_pos, pending=set())
        self.assertEqual(n, 0, "活仓占用必须保留——预算真实性优先")
        self.assertIn("SOL-USDT-SWAP:BUY_LONG:333", self._unreleased())

    def test_pending_order_preserves_entry_reservation(self):
        self._reserve("okx", "demo", "ADA-USDT-SWAP:SELL_SHORT:444", 250.0, age_s=99999)
        n = self._run(real_pos={}, pending={"ADA-USDT-SWAP"})
        self.assertEqual(n, 0, "挂单在途 = 意图仍活")
        self.assertIn("ADA-USDT-SWAP:SELL_SHORT:444", self._unreleased())

    def test_other_venue_position_preserves(self):
        self._reserve("gate", "demo", "BTC-USDT-SWAP:BUY_LONG:555", 150.0, age_s=99999)
        snap = {"gate": [{"base": "BTC", "side": "long", "size_signed": 2}]}
        n = self._run(real_pos={}, pending=set(), snapshot=snap)
        self.assertEqual(n, 0)
        self.assertIn("BTC-USDT-SWAP:BUY_LONG:555", self._unreleased())

    def test_env_isolation_live_never_touched(self):
        self._reserve("okx", "live", "BTC-USDT-SWAP:BUY_LONG:666", 500.0, age_s=99999)
        n = self._run(real_pos={}, pending=set(), env="demo")
        self.assertEqual(n, 0, "demo 对账绝不碰 live 占用（环境轴物理隔离）")
        con = sqlite3.connect(self.db)
        row = con.execute("SELECT released FROM risk_reservations "
                          "WHERE intent_id LIKE '%666'").fetchone()
        con.close()
        self.assertEqual(row[0], 0)

    def test_unparseable_timestamp_conservative_keep(self):
        self._reserve("okx", "demo", "DOGE-USDT-SWAP:BUY_LONG:777", 50.0, age_s=99999)
        con = sqlite3.connect(self.db)
        con.execute("UPDATE risk_reservations SET updated_at = 'garbage' "
                    "WHERE intent_id LIKE '%777'")
        con.commit()
        con.close()
        n = self._run(real_pos={}, pending=set())
        self.assertEqual(n, 0, "时间戳不可解析 → 保守保留，绝不猜龄")

    def test_release_failure_isolated(self):
        self._reserve("okx", "demo", "LINK-USDT-SWAP:SELL_SHORT:888", 90.0, age_s=99999)
        self._reserve("okx", "demo", "AVAX-USDT-SWAP:BUY_LONG:999", 90.0, age_s=99999)
        orig_release = self.mgr.release
        def flaky(account_key, intent_id, state=risk_reservation.STATE_CLOSED):
            if "888" in str(intent_id):
                raise RuntimeError("simulated sqlite busy")
            return orig_release(account_key, intent_id, state=state)
        with patch.object(self.mgr, "release", side_effect=flaky):
            n = self._run(real_pos={}, pending=set())
        self.assertEqual(n, 1, "单条失败不拖垮整批——其余照常回笼")
        self.assertIn("LINK-USDT-SWAP:SELL_SHORT:888", self._unreleased())
        self.assertNotIn("AVAX-USDT-SWAP:BUY_LONG:999", self._unreleased())

    def test_released_money_returns_to_gross_view(self):
        self._reserve("okx", "demo", "PEPE-USDT-SWAP:BUY_LONG:101", 300.0, age_s=99999)
        before = self.mgr.gross_exposure("demo")
        self._run(real_pos={}, pending=set())
        after = self.mgr.gross_exposure("demo")
        self.assertAlmostEqual(before - after, 300.0, places=4)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class CrossVenuePendingKeepTest(unittest.TestCase):
    """第一百一十五刀：**外所未成交挂单**的预留不得在 TTL 后被释放。

    ## 缺陷（读代码 + 受控 A/B 实证）

    `still_live` 原判据是 `(venue == "okx" and inst_id in pending)`：
    ① 只看 OKX；② 拿意图里的 OKX 拼写（`XRP-USDT-SWAP`）去比 `pending_inst_ids`
    里混装的各所拼写（币安 `XRPUSDT`、Gate `DOGE_USDT`）。

    同一输入（币安 XRP 空单意图、age=5h、无仓位、`pending={"XRPUSDT"}`）的 A/B：
    旧判据 → **释放**（单还挂在场内，成交后这笔占用已不在台账上 ⇒ 预算/敞口少算）；
    新判据 → **保留**。

    方向纪律：**保留是保守的**（多占只压缩可用额度），释放不可逆（活单失去登记）
    ⇒ 按基名匹配、不要求方向一致（宁多留不漏放）。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="us010-xv-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = os.path.join(self.tmp, "res.db")
        self.mgr = risk_reservation.get_manager(db_path=self.db)

    def _reserve(self, venue, intent, amount, age_s):
        self.mgr.reserve((venue, "demo", "fp-xv"), intent, amount, state="confirmed")
        con = sqlite3.connect(self.db)
        con.execute("UPDATE risk_reservations SET updated_at = ?, created_at = ? "
                    "WHERE intent_id = ?", (_utc_stamp(age_s), _utc_stamp(age_s), intent))
        con.commit(); con.close()

    def _run(self, pending, snapshot):
        with patch.object(trader, "reservation_manager", lambda: self.mgr), \
                patch.object(trader, "fetch_other_venue_positions",
                             lambda e: (True, snapshot, "")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                return trader.reconcile_reservation_ledger(
                    {}, pending, "demo", venue_snapshot=snapshot)

    def _unreleased(self):
        return {r["intent_id"] for r in self.mgr.list_unreleased("demo")}

    def test_binance_pending_order_keeps_its_reservation(self):
        """币安拼写 `XRPUSDT`（无分隔符）必须能归一到意图的 `XRP-USDT-SWAP`。"""
        intent = "XRP-USDT-SWAP:SELL_SHORT:1"
        self._reserve("binance", intent, 189.0, age_s=9999)
        n = self._run({"XRPUSDT"}, {"binance": [], "gate": []})
        self.assertEqual(n, 0, "挂单还在 ⇒ 不得释放")
        self.assertIn(intent, self._unreleased())

    def test_gate_pending_order_keeps_its_reservation(self):
        intent = "DOGE-USDT-SWAP:SELL_SHORT:2"
        self._reserve("gate", intent, 189.0, age_s=9999)
        n = self._run({"DOGE_USDT"}, {"gate": []})
        self.assertEqual(n, 0, "Gate 拼写 `DOGE_USDT` 同样必须归一")
        self.assertIn(intent, self._unreleased())

    def test_okx_spelling_still_matched(self):
        intent = "BTC-USDT-SWAP:BUY_LONG:3"
        self._reserve("okx", intent, 200.0, age_s=9999)
        self.assertEqual(self._run({"BTC-USDT-SWAP"}, {"okx": []}), 0)

    def test_truly_dead_intent_is_still_released(self):
        """关键反向断言：修复不得把"该释放的"也留下（漏放=额度被永久挤占）。"""
        intent = "ETH-USDT-SWAP:SELL_SHORT:4"
        self._reserve("binance", intent, 100.0, age_s=9999)
        n = self._run({"XRPUSDT"}, {"binance": []})     # 挂单是别的币
        self.assertEqual(n, 1, "不同币的挂单不能当挡箭牌")
        self.assertNotIn(intent, self._unreleased())

    def test_different_quote_spellings_normalize(self):
        """`ADAUSDC` / `ADA-USDC-SWAP` / `ADA_USDC` 都必须归一到 `ADA`。"""
        intent = "ADA-USDT-SWAP:SELL_SHORT:5"
        for pending_inst in ("ADAUSDC", "ADA-USDC-SWAP", "ADA_USDC"):
            with self.subTest(pending=pending_inst):
                db = os.path.join(self.tmp, f"res-{pending_inst}.db")
                self.mgr = risk_reservation.get_manager(db_path=db)
                self._reserve("binance", intent, 50.0, age_s=9999)
                self.assertEqual(self._run({pending_inst}, {"binance": []}), 0,
                                 f"{pending_inst} 未归一 ⇒ 预留被误释放")


class UnverifiedSnapshotMustNotReleaseTest(unittest.TestCase):
    """第一百二十六刀：**跨所实况未核验 ⇒ 一笔都不许释放**。

    缺陷形状（实测）：`fetch_other_venue_positions` 读取失败时返回 `(False, {}, err)`，
    调用点（`cycle_stages`）此前把那个**空字典**原样透传 ⇒ 对账器据 `{}` 判定
    "外所无仓无挂"，把**活仓的外所预留**（实测 binance 726U）按超 TTL 释放成
    `closed`，日志还打印"无仓无挂"这一**假陈述**。释放不可逆 ⇒ 预算台账少算活仓。

    方向纪律（模块 docstring）：保留是保守的（多占只压缩额度），释放不可逆 ⇒ 未知必须保留。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="us010-unv-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = os.path.join(self.tmp, "res.db")
        self.mgr = risk_reservation.get_manager(db_path=self.db)
        self.out = ""

    def _reserve_stale(self, venue="binance", intent="UNI-USDT-SWAP:SELL_SHORT:900"):
        self.mgr.reserve((venue, "demo", "fp-test"), intent, 726.0, state="pending")
        con = sqlite3.connect(self.db)
        con.execute("UPDATE risk_reservations SET updated_at = ?, created_at = ? "
                    "WHERE intent_id = ?", (_utc_stamp(99999), _utc_stamp(99999), intent))
        con.commit()
        con.close()

    def _run(self, **kw):
        # ⚠️ 先把 fetch_ok 取出来：否则 `**kw` 会在调用点被求值，
        # 把 `fetch_ok` 当成对账器的形参传进去（TypeError）。
        fetch_ok = kw.pop("fetch_ok", True)
        with patch.object(trader, "reservation_manager", lambda: self.mgr), \
                patch.object(trader, "fetch_other_venue_positions",
                             lambda e: (fetch_ok, {}, "")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                n = trader.reconcile_reservation_ledger(
                    {}, set(), "demo", **kw)
        self.out = buf.getvalue()
        return n

    def test_unverified_empty_snapshot_releases_nothing(self):
        self._reserve_stale()
        n = self._run(venue_snapshot={}, venue_snapshot_verified=False)
        self.assertEqual(n, 0, "未核验的空快照不得释放任何预留")
        self.assertIn("UNI-USDT-SWAP:SELL_SHORT:900",
                      {r["intent_id"] for r in self.mgr.list_unreleased("demo")})
        self.assertIn("未核验", self.out)
        self.assertNotIn("无仓无挂", self.out, "不得给出'无仓无挂'这种假陈述")

    def test_self_fetch_failure_releases_nothing(self):
        """`venue_snapshot=None`（自取）且读取失败 ⇒ 同样不释放。"""
        self._reserve_stale()
        n = self._run(venue_snapshot=None, fetch_ok=False)
        self.assertEqual(n, 0)
        self.assertIn("UNI-USDT-SWAP:SELL_SHORT:900",
                      {r["intent_id"] for r in self.mgr.list_unreleased("demo")})
        self.assertIn("自取失败", self.out)

    def test_verified_empty_snapshot_still_reclaims(self):
        """核验成功且**确实**无仓无挂 ⇒ 照常回笼（fail-closed 不得挡住正常回笼）。"""
        self._reserve_stale()
        n = self._run(venue_snapshot={}, venue_snapshot_verified=True)
        self.assertEqual(n, 1)
        self.assertNotIn("UNI-USDT-SWAP:SELL_SHORT:900",
                         {r["intent_id"] for r in self.mgr.list_unreleased("demo")})

    def test_default_keeps_backward_compatible_behaviour(self):
        """不传新形参时行为与既有调用方一致（默认已核验）。"""
        self._reserve_stale()
        with patch.object(trader, "reservation_manager", lambda: self.mgr), \
                patch.object(trader, "fetch_other_venue_positions", lambda e: (True, {}, "")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                n = trader.reconcile_reservation_ledger({}, set(), "demo",
                                                        venue_snapshot={})
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()

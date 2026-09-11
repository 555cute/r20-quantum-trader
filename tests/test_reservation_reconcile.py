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

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import scripts.ai_factor_trader as trader
from r20_backend import risk_reservation


def _utc_stamp(seconds_ago: float) -> str:
    ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds_ago)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


class ReservationReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="us010-")
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

    def _run(self, real_pos, pending, env="demo", snapshot=None):
        with patch.object(trader, "reservation_manager", lambda: self.mgr), \
                patch.object(trader, "fetch_other_venue_positions",
                             lambda e: (True, snapshot or {}, "")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                n = trader.reconcile_reservation_ledger(real_pos, pending, env,
                                                        venue_snapshot=snapshot)
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

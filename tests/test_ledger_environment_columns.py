"""US-003 trades 台账身份迁移测试（全 mock、零网络、零触碰生产 data/r20_quant.db）。

覆盖：幂等 ALTER/重建、unknown_legacy 诚实回填、source_bill_id 回填、
双环境同 bill_id 共存、跨所同 ID 不互抹、同身份 upsert、迁移日志幂等零 diff。
封闭三律：DB_PATH/LEDGER_JSON_FILE 全部 patch 到临时目录（迁移日志随 DB 目录联动）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import db_manager


def _v1_db(path: str, rows, with_venue: bool = False):
    """构造迁移前形态：bill_id 列级 UNIQUE（无法 ALTER DROP 的历史包袱）。"""
    con = sqlite3.connect(path)
    venue_col = "venue TEXT NOT NULL DEFAULT 'okx'," if with_venue else ""
    con.executescript(f"""
    CREATE TABLE trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_id TEXT UNIQUE, time TEXT NOT NULL, inst TEXT NOT NULL,
        action TEXT NOT NULL, direction TEXT NOT NULL, size REAL, price REAL,
        fee REAL, gross_pnl REAL, pnl REAL, comment TEXT,
        {venue_col}
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    """)
    for r in rows:
        if with_venue:
            con.execute("INSERT INTO trades (bill_id,time,inst,action,direction,venue)"
                        " VALUES (?,?,?,?,?,?)", r)
        else:
            con.execute("INSERT INTO trades (bill_id,time,inst,action,direction)"
                        " VALUES (?,?,?,?,?)", r[:5])
    con.commit()
    con.close()


class LedgerEnvColumnsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "r20_quant.test.db")
        self.ledger_json = os.path.join(self.tmp.name, "trading_ledger.json")
        self.p_db = patch.object(db_manager, "DB_PATH", self.db)
        self.p_js = patch.object(db_manager, "LEDGER_JSON_FILE", self.ledger_json)
        self.p_db.start()
        self.p_js.start()
        self.addCleanup(lambda: (self.p_db.stop(), self.p_js.stop(), self.tmp.cleanup()))

    def rows(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        out = [dict(r) for r in con.execute("SELECT * FROM trades ORDER BY source_bill_id,"
                                            " venue, environment, action")]
        con.close()
        return out

    # ---------------- AC1/AC4：新库幂等 + v1 重建 + unknown_legacy 回填 ----------------

    def test_fresh_db_idempotent(self):
        st = db_manager.init_database()
        self.assertEqual(st["mode"], "fresh")
        st2 = db_manager.init_database()
        self.assertEqual(st2["mode"], "noop")
        # 新库（无迁移事件）不写日志——防误伤临时目录巡检
        self.assertFalse(os.path.exists(db_manager.migration_log_path()))

    def test_v1_rebuild_backfills_unknown_legacy(self):
        _v1_db(self.db, [("b1", "2026-09-01", "BTC-USDT-SWAP", "closed", "long"),
                         ("b2", "2026-09-02", "ETH-USDT-SWAP", "closed", "short")])
        st = db_manager.init_database()
        self.assertEqual(st["mode"], "rebuild")
        self.assertEqual(st["migrated"], 2)
        self.assertEqual(st["defaulted"], 2)  # 历史行全部诚实标 unknown_legacy
        rs = self.rows()
        self.assertEqual(len(rs), 2)
        for r in rs:
            self.assertEqual(r["environment"], "unknown_legacy")  # 不冒充当前环境
            self.assertEqual(r["venue"], "okx")                   # 旧行缺 venue 列 → 默认 okx
            self.assertEqual(r["source_bill_id"], r["bill_id"])   # 原生 ID 回填
            self.assertEqual(r["account_id"], "")
        # 迁移可观测日志（随 DB 目录 → 临时目录，生产零接触）
        log_path = db_manager.migration_log_path()
        self.assertTrue(os.path.exists(log_path))
        with open(log_path, encoding="utf-8") as f:
            log = json.load(f)
        self.assertEqual(log["migrated"], 2)
        self.assertEqual(log["defaulted"], 2)
        # 重跑幂等零 diff：noop 不重写日志、不动行
        with open(log_path, "rb") as fh:
            before = fh.read()
        st2 = db_manager.init_database()
        self.assertEqual(st2["mode"], "noop")
        with open(log_path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(len(self.rows()), 2)

    def test_v15_venue_column_preserved(self):
        _v1_db(self.db, [("g1", "2026-09-03", "BTC_USDT", "closed", "long", "gate")],
               with_venue=True)
        st = db_manager.init_database()
        self.assertEqual(st["mode"], "rebuild")
        rs = self.rows()
        self.assertEqual(rs[0]["venue"], "gate")            # 已有 venue 值不被覆盖
        self.assertEqual(rs[0]["environment"], "unknown_legacy")

    # ---------------- AC2：身份唯一（双环境/双所共存、同身份 upsert） ----------------

    def test_same_bill_id_across_environments_coexist(self):
        db_manager.init_database()
        base = dict(bill_id="x1", time="2026-09-09", inst="BTC-USDT-SWAP",
                    action="closed", direction="long", price=100.0, pnl=1.0)
        db_manager.record_trade_sqlite({**base, "environment": "demo"})
        db_manager.record_trade_sqlite({**base, "environment": "live"})
        rs = self.rows()
        self.assertEqual(len(rs), 2)  # 旧全表 UNIQUE 时代这两行会互抹
        envs = sorted(r["environment"] for r in rs)
        self.assertEqual(envs, ["demo", "live"])

    def test_same_bill_id_across_venues_coexist(self):
        db_manager.init_database()
        base = dict(bill_id="x2", time="2026-09-09", inst="BTC", action="closed",
                    direction="short", price=99.0, environment="live")
        db_manager.record_trade_sqlite({**base, "venue": "okx"})
        db_manager.record_trade_sqlite({**base, "venue": "gate"})
        rs = self.rows()
        self.assertEqual(len(rs), 2)
        self.assertEqual(sorted(r["venue"] for r in rs), ["gate", "okx"])

    def test_same_identity_upsert_no_duplicate(self):
        db_manager.init_database()
        ident = dict(bill_id="x3", time="2026-09-09", inst="BTCUSDT", action="closed",
                     direction="long", price=100.0, venue="binance",
                     environment="demo", account_id="acct-A")
        db_manager.record_trade_sqlite({**ident, "pnl": 5.0})
        db_manager.record_trade_sqlite({**ident, "pnl": 7.5})  # 同身份重报 → 原位更新
        rs = self.rows()
        self.assertEqual(len(rs), 1)
        self.assertEqual(rs[0]["pnl"], 7.5)
        self.assertEqual(rs[0]["account_id"], "acct-A")

    def test_action_distinguishes_identity(self):
        db_manager.init_database()
        base = dict(bill_id="x4", time="2026-09-09", inst="BTC", direction="long",
                    price=1.0, venue="gate", environment="demo")
        db_manager.record_trade_sqlite({**base, "action": "open"})
        db_manager.record_trade_sqlite({**base, "action": "closed"})
        self.assertEqual(len(self.rows()), 2)  # 流水类型在身份内

    # ---------------- AC3：写路径贯通（sync 缺省诚实、显式值透传） ----------------

    def test_sync_json_default_unknown_and_explicit(self):
        with open(self.ledger_json, "w", encoding="utf-8") as f:
            json.dump([
                {"id": "s1", "time": "2026-09-01", "inst": "BTC-USDT-SWAP",
                 "status": "closed", "side": "long", "close_px": 10},      # 无环境证据
                {"id": "s2", "time": "2026-09-02", "inst": "BTC-USDT-SWAP",
                 "status": "closed", "side": "short", "close_px": 11,
                 "environment": "live", "account_id": "9518"},             # 显式值
            ], f)
        n = db_manager.sync_json_to_sqlite()
        self.assertEqual(n, 2)
        rs = {r["source_bill_id"]: r for r in self.rows()}
        self.assertEqual(rs["s1"]["environment"], "unknown_legacy")
        self.assertEqual(rs["s2"]["environment"], "live")
        self.assertEqual(rs["s2"]["account_id"], "9518")
        # 重跑 sync：幂等 upsert 不产生重复行
        self.assertEqual(db_manager.sync_json_to_sqlite(), 2)
        self.assertEqual(len(self.rows()), 2)

    def test_sync_alias_priority_preserved(self):
        """旧 sync 的别名优先级不漂移：close_time > time；side > direction；exit_reason。"""
        with open(self.ledger_json, "w", encoding="utf-8") as f:
            json.dump([{"id": "a1", "close_time": "2026-09-04", "time": "WRONG",
                        "name": "ETH-USDT-SWAP", "status": "closed",
                        "side": "short", "direction": "WRONG",
                        "close_px": 12.5, "exit_reason": "tp"}], f)
        db_manager.sync_json_to_sqlite()
        r = self.rows()[0]
        self.assertEqual(r["time"], "2026-09-04")
        self.assertEqual(r["inst"], "ETH-USDT-SWAP")
        self.assertEqual(r["action"], "closed")
        self.assertEqual(r["direction"], "short")
        self.assertEqual(r["price"], 12.5)
        self.assertEqual(r["comment"], "tp")
        self.assertEqual(r["bill_id"], "a1")
        self.assertEqual(r["environment"], "unknown_legacy")

    def test_record_environment_explicit_passthrough(self):
        db_manager.init_database()
        db_manager.record_trade_sqlite({
            "bill_id": "r1", "time": "2026-09-05", "inst": "BTC", "action": "closed",
            "direction": "long", "price": 1, "venue": "okx",
            "environment": "demo", "account_id": "sub-77"})
        r = self.rows()[0]
        self.assertEqual((r["environment"], r["account_id"]), ("demo", "sub-77"))


if __name__ == "__main__":
    unittest.main()

"""交易台账迁移器（db_manager.py）收口 —— 第 304 刀。

这个模块是 JSON 台账 → SQLite 的**唯一写入通道**，也是 US-003 身份轴迁移的落点。
它的历史包袱写在代码注释里：旧表 `bill_id` 是**列级 UNIQUE**，而 SQLite 无法
`ALTER DROP` 列约束 —— 于是"同 bill_id 跨环境两行共存"必须靠**重建表**实现。
一旦重建出错，台账就是交易历史的唯一副本。所以本刀的第一条语义是：

> **零丢失红线**：拷贝行数不等 ⇒ 抛错 + 回滚 + **原表原样**，绝不带着半迁移态继续。

其余钉的是身份轴的诚实性（历史行一律 `unknown_legacy`，**不冒充当前环境**）与
"迁移日志随 DB 路径联动"（否则测试会误写生产 `data/`）。
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts import db_manager


def _legacy_db(path: str, rows, *, with_venue: bool = False,
               with_environment: bool = False, bill_unique: bool = True):
    """构造迁移前形态。默认带列级 UNIQUE（真实历史包袱）。"""
    con = sqlite3.connect(path)
    extra = ""
    if with_venue:
        extra += " venue TEXT NOT NULL DEFAULT 'okx',"
    if with_environment:
        extra += " environment TEXT NOT NULL DEFAULT '',"
    unique = "UNIQUE" if bill_unique else ""
    con.executescript(f"""
    CREATE TABLE trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_id TEXT {unique}, time TEXT NOT NULL, inst TEXT NOT NULL,
        action TEXT NOT NULL, direction TEXT NOT NULL, size REAL, price REAL,
        fee REAL, gross_pnl REAL, pnl REAL, comment TEXT,
        {extra}
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
    """)
    cols = ["bill_id", "time", "inst", "action", "direction"]
    if with_venue:
        cols.append("venue")
    if with_environment:
        cols.append("environment")
    placeholders = ",".join("?" for _ in cols)
    for r in rows:
        values = list(r[:5])
        if with_venue:
            values.append(r[5] if len(r) > 5 else "okx")
        if with_environment:
            values.append(r[6] if len(r) > 6 else "")
        con.execute(f"INSERT INTO trades ({','.join(cols)}) VALUES ({placeholders})", values)
    con.commit()
    con.close()


class _FakeCursor:
    """受控游标：按脚本回放 `fetchone()`，其余调用只记账。"""

    def __init__(self, counts):
        self._counts = list(counts)
        self.executed: list[tuple] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchone(self):
        return (self._counts.pop(0),)


class _FakeConn:
    def __init__(self):
        self.committed = 0
        self.rolled_back = 0

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


class _DbFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "r20_quant.test.db")
        self.ledger = os.path.join(self.tmp.name, "trading_ledger.json")
        for name, value in (("DB_PATH", self.db), ("LEDGER_JSON_FILE", self.ledger)):
            p = patch.object(db_manager, name, value)
            p.start()
            self.addCleanup(p.stop)

    def rows(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute("SELECT * FROM trades ORDER BY id")]
        finally:
            con.close()

    def columns(self):
        con = sqlite3.connect(self.db)
        try:
            return [r[1] for r in con.execute("PRAGMA table_info(trades)").fetchall()]
        finally:
            con.close()


class MigrationLogPathTests(_DbFixture, unittest.TestCase):
    def test_log_sits_next_to_the_db(self):
        self.assertEqual(db_manager.migration_log_path(),
                         os.path.join(self.tmp.name, "ledger_migration_log.json"))

    def test_log_follows_a_patched_db_path(self):
        other = os.path.join(self.tmp.name, "sub", "other.db")
        with patch.object(db_manager, "DB_PATH", other):
            self.assertEqual(os.path.dirname(db_manager.migration_log_path()),
                             os.path.join(self.tmp.name, "sub"))

    def test_write_failure_is_reported_not_raised(self):
        out = io.StringIO()
        with patch.object(db_manager, "migration_log_path",
                          return_value="/proc/definitely/not/writable/x.json"), \
             contextlib.redirect_stdout(out):
            db_manager._write_migration_log({"mode": "rebuild"})   # 不许抛
        self.assertIn("migration log write failed", out.getvalue())

    def test_entry_carries_beijing_timestamp_and_db_path(self):
        db_manager._write_migration_log({"mode": "rebuild", "migrated": 3})
        payload = json.loads(Path(db_manager.migration_log_path()).read_text(encoding="utf-8"))
        self.assertEqual(payload["mode"], "rebuild")
        self.assertEqual(payload["migrated"], 3)
        self.assertEqual(payload["db"], self.db)
        self.assertIn("+08:00", payload["ts"])


class CopyExprTests(_DbFixture, unittest.TestCase):
    """v1/v1.5/v2 混合旧列形态 → v2 拷贝表达式；缺列一律给**诚实默认**。"""

    def test_required_columns_pass_through_when_present(self):
        have = {"bill_id", "time", "inst", "action", "direction", "id"}
        for col in have:
            self.assertEqual(db_manager._copy_expr(col, have), col)

    def test_missing_required_columns_become_null(self):
        for col in ("bill_id", "time", "inst", "action", "direction", "id",
                    "size", "price", "fee", "gross_pnl", "pnl", "comment", "created_at"):
            self.assertEqual(db_manager._copy_expr(col, set()), "NULL", col)

    def test_venue_defaults_to_okx_and_blanks_are_normalized(self):
        self.assertEqual(db_manager._copy_expr("venue", set()), "'okx'")
        self.assertEqual(db_manager._copy_expr("venue", {"venue"}),
                         "COALESCE(NULLIF(venue, ''), 'okx')")

    def test_environment_missing_is_honestly_unknown_legacy(self):
        # ★ 历史行没有环境证据 ⇒ 不冒充当前环境
        self.assertEqual(db_manager._copy_expr("environment", set()),
                         f"'{db_manager.UNKNOWN_LEGACY}'")

    def test_environment_present_but_blank_is_normalized_to_unknown(self):
        self.assertEqual(db_manager._copy_expr("environment", {"environment"}),
                         f"COALESCE(NULLIF(environment, ''), '{db_manager.UNKNOWN_LEGACY}')")

    def test_account_id_defaults_to_empty_string(self):
        self.assertEqual(db_manager._copy_expr("account_id", set()), "''")
        self.assertEqual(db_manager._copy_expr("account_id", {"account_id"}),
                         "COALESCE(account_id, '')")

    def test_source_bill_id_backfilled_from_bill_id(self):
        self.assertEqual(db_manager._copy_expr("source_bill_id", set()), "bill_id")
        self.assertEqual(db_manager._copy_expr("source_bill_id", {"source_bill_id"}),
                         "COALESCE(source_bill_id, bill_id)")

    def test_unknown_column_falls_back_to_presence_check(self):
        self.assertEqual(db_manager._copy_expr("mystery", {"mystery"}), "mystery")
        self.assertEqual(db_manager._copy_expr("mystery", set()), "NULL")


class ZeroLossRedlineTests(_DbFixture, unittest.TestCase):
    """★ 拷贝行数不等 ⇒ 抛错 + 回滚 + **原表原样**（台账是交易历史的唯一副本）。"""

    def test_row_count_mismatch_raises_and_rolls_back(self):
        conn, cur = _FakeConn(), _FakeCursor([5, 4])       # before=5, after=4
        with self.assertRaises(RuntimeError) as ctx:
            db_manager._rebuild_to_v2(conn, cur, set())
        self.assertIn("迁移行数不一致 before=5 after=4", str(ctx.exception))
        self.assertIn("零丢失红线", str(ctx.exception))
        self.assertEqual(conn.rolled_back, 1)
        self.assertEqual(conn.committed, 0)               # 绝不在不一致状态下提交
        # 改名从未发生
        self.assertFalse(any("RENAME" in sql for sql, _ in cur.executed))

    def test_successful_rebuild_commits_and_reports_counts(self):
        conn, cur = _FakeConn(), _FakeCursor([7, 7, 2])
        stats = db_manager._rebuild_to_v2(conn, cur, set())
        self.assertEqual(stats["mode"], "rebuild")
        self.assertEqual((stats["before"], stats["after"], stats["migrated"]), (7, 7, 7))
        self.assertEqual(stats["defaulted"], 2)
        self.assertEqual(conn.committed, 1)
        self.assertEqual(conn.rolled_back, 0)
        self.assertTrue(any("RENAME" in sql for sql, _ in cur.executed))

    def test_any_exception_rolls_back_and_reraises(self):
        class _Exploding(_FakeCursor):
            def execute(self, sql, params=None):
                if "INSERT INTO trades_new" in sql:
                    raise sqlite3.OperationalError("disk I/O error")
                return super().execute(sql, params)

        conn, cur = _FakeConn(), _Exploding([3])
        with self.assertRaises(sqlite3.OperationalError):
            db_manager._rebuild_to_v2(conn, cur, set())
        self.assertEqual(conn.rolled_back, 1)
        self.assertEqual(conn.committed, 0)


class InitDatabaseTests(_DbFixture, unittest.TestCase):
    def test_fresh_database_is_created_as_v2(self):
        stats = db_manager.init_database()
        self.assertEqual(stats["mode"], "fresh")
        self.assertEqual(stats["migrated"], 0)
        cols = self.columns()
        for col in ("environment", "account_id", "source_bill_id", "venue"):
            self.assertIn(col, cols)

    def test_fresh_database_is_a_noop_on_second_call(self):
        db_manager.init_database()
        self.assertEqual(db_manager.init_database()["mode"], "noop")

    def test_fresh_creation_writes_no_migration_log(self):
        db_manager.init_database()
        self.assertFalse(os.path.exists(db_manager.migration_log_path()))

    def test_legacy_unique_bill_id_triggers_rebuild(self):
        _legacy_db(self.db, [("b1", "2026-01-01", "BTC", "closed", "long", 1.0)],
                   bill_unique=True)
        stats = db_manager.init_database()
        self.assertEqual(stats["mode"], "rebuild")
        self.assertEqual(stats["migrated"], 1)
        self.assertTrue(os.path.exists(db_manager.migration_log_path()))
        self.assertEqual(self.rows()[0]["environment"], db_manager.UNKNOWN_LEGACY)

    def test_rebuild_is_idempotent(self):
        _legacy_db(self.db, [("b1", "2026-01-01", "BTC", "closed", "long", 1.0)])
        db_manager.init_database()
        self.assertEqual(db_manager.init_database()["mode"], "noop")

    def test_missing_identity_columns_trigger_rebuild_even_without_unique(self):
        _legacy_db(self.db, [("b1", "2026-01-01", "BTC", "closed", "long", 1.0)],
                   bill_unique=False)
        self.assertEqual(db_manager.init_database()["mode"], "rebuild")

    def test_v2_table_without_identity_index_gets_it_backfilled(self):
        # 已 v2（无 UNIQUE、四列齐全）但可能缺 uq_trades_identity ⇒ 幂等补齐
        _legacy_db(self.db, [("b1", "2026-01-01", "BTC", "closed", "long", 1.0)],
                   with_venue=True, with_environment=True, bill_unique=False)
        con = sqlite3.connect(self.db)
        con.execute("ALTER TABLE trades ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")
        con.execute("ALTER TABLE trades ADD COLUMN source_bill_id TEXT")
        con.commit()
        con.close()
        stats = db_manager.init_database()
        self.assertEqual(stats["mode"], "noop")
        con = sqlite3.connect(self.db)
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        con.close()
        self.assertIn("uq_trades_identity", names)

    def test_blank_legacy_environment_column_is_normalized(self):
        _legacy_db(self.db, [("b1", "2026-01-01", "BTC", "closed", "long", "okx", "")],
                   with_venue=True, with_environment=True, bill_unique=True)
        db_manager.init_database()
        self.assertEqual(self.rows()[0]["environment"], db_manager.UNKNOWN_LEGACY)

    def test_existing_environment_value_is_preserved(self):
        _legacy_db(self.db, [("b1", "2026-01-01", "BTC", "closed", "long", "okx", "demo")],
                   with_venue=True, with_environment=True, bill_unique=True)
        db_manager.init_database()
        self.assertEqual(self.rows()[0]["environment"], "demo")

    def test_blank_venue_is_normalized_to_okx(self):
        _legacy_db(self.db, [("b1", "2026-01-01", "BTC", "closed", "long", "")],
                   with_venue=True, bill_unique=True)
        db_manager.init_database()
        self.assertEqual(self.rows()[0]["venue"], "okx")

    def test_source_bill_id_is_backfilled_from_bill_id(self):
        _legacy_db(self.db, [("legacy-bill", "2026-01-01", "BTC", "closed", "long", 1.0)])
        db_manager.init_database()
        self.assertEqual(self.rows()[0]["source_bill_id"], "legacy-bill")


class NormalizeRowTests(unittest.TestCase):
    def test_field_aliases_and_defaults(self):
        # ★ 分层：`_normalize_row` 只认**规范名**（price/size/...）；
        #   `close_px` 那类别名是在 `sync_json_to_sqlite` 的 norm 字典里解析的
        #   （见 SyncJsonTests 的别名用例）—— 往这里塞别名不会被识别
        row = db_manager._normalize_row({
            "name": "ETH", "action_type": "closed", "side": "short", "sz": 2,
            "price": 10.0,
        })
        self.assertEqual(row[2], "ETH")
        self.assertEqual(row[3], "closed")
        self.assertEqual(row[4], "short")
        self.assertEqual(row[5], 2.0)
        self.assertEqual(row[6], 10.0)

    def test_underscore_aliases_are_not_resolved_here(self):
        # 反面：`close_px` 在 `_normalize_row` 层**不被识别** ⇒ price 落 0.0
        row = db_manager._normalize_row({"close_px": 10.0})
        self.assertEqual(row[6], 0.0)

    def test_environment_blank_becomes_unknown_legacy(self):
        row = db_manager._normalize_row({"environment": "   "})
        self.assertEqual(row[12], db_manager.UNKNOWN_LEGACY)
        self.assertEqual(db_manager._normalize_row({})[12], db_manager.UNKNOWN_LEGACY)

    def test_explicit_environment_is_kept(self):
        self.assertEqual(db_manager._normalize_row({"environment": "demo"})[12], "demo")

    def test_bill_id_is_synthesized_when_absent(self):
        row = db_manager._normalize_row({"time": "T", "inst": "BTC", "action": "closed",
                                         "price": 1.5})
        self.assertEqual(row[0], "T_BTC_closed_1.5")
        self.assertEqual(row[14], row[0])          # source_bill_id 回落到同一个

    def test_venue_defaults_to_okx(self):
        self.assertEqual(db_manager._normalize_row({})[11], "okx")
        self.assertEqual(db_manager._normalize_row({"venue": "gate"})[11], "gate")

    def test_comment_alias_priority(self):
        self.assertEqual(db_manager._normalize_row({"exit_reason": "TP"})[10], "TP")
        self.assertEqual(db_manager._normalize_row({"remark": "R"})[10], "R")
        self.assertEqual(db_manager._normalize_row({})[10], "")


class SyncJsonTests(_DbFixture, unittest.TestCase):
    def _write_ledger(self, payload):
        Path(self.ledger).write_text(json.dumps(payload), encoding="utf-8")

    def test_missing_ledger_file_returns_zero(self):
        self.assertEqual(db_manager.sync_json_to_sqlite(), 0)
        self.assertTrue(os.path.exists(self.db))     # 库仍会被初始化

    def test_corrupt_ledger_is_treated_as_empty_not_crash(self):
        Path(self.ledger).write_text("{ not json", encoding="utf-8")
        self.assertEqual(db_manager.sync_json_to_sqlite(), 0)

    def test_trades_are_upserted_and_counted(self):
        self._write_ledger([
            {"id": "b1", "close_time": "2026-01-01 10:00:00", "inst": "BTC",
             "status": "closed", "side": "long", "sz": 1, "close_px": 100.0, "pnl": 5.0},
            {"id": "b2", "close_time": "2026-01-01 11:00:00", "inst": "ETH",
             "status": "closed", "side": "short", "sz": 2, "close_px": 50.0, "pnl": -1.0},
        ])
        self.assertEqual(db_manager.sync_json_to_sqlite(), 2)
        self.assertEqual(len(self.rows()), 2)

    def test_default_environment_is_honest_unknown_legacy(self):
        self._write_ledger([{"id": "b1", "inst": "BTC", "status": "closed"}])
        db_manager.sync_json_to_sqlite()
        self.assertEqual(self.rows()[0]["environment"], db_manager.UNKNOWN_LEGACY)

    def test_caller_can_inject_a_known_default_environment(self):
        self._write_ledger([{"id": "b1", "inst": "BTC", "status": "closed"}])
        db_manager.sync_json_to_sqlite(default_environment="live")
        self.assertEqual(self.rows()[0]["environment"], "live")

    def test_entry_environment_beats_the_default(self):
        self._write_ledger([{"id": "b1", "inst": "BTC", "status": "closed",
                             "environment": "demo"}])
        db_manager.sync_json_to_sqlite(default_environment="live")
        self.assertEqual(self.rows()[0]["environment"], "demo")

    def test_same_identity_twice_converges_to_one_row(self):
        entry = {"id": "b1", "inst": "BTC", "status": "closed", "environment": "live"}
        self._write_ledger([entry, dict(entry)])
        db_manager.sync_json_to_sqlite()
        self.assertEqual(len(self.rows()), 1)

    def test_same_bill_id_across_environments_coexists(self):
        self._write_ledger([
            {"id": "b1", "inst": "BTC", "status": "closed", "environment": "live"},
            {"id": "b1", "inst": "BTC", "status": "closed", "environment": "demo"},
        ])
        db_manager.sync_json_to_sqlite()
        self.assertEqual(sorted(r["environment"] for r in self.rows()), ["demo", "live"])

    def test_close_time_alias_wins_over_time(self):
        self._write_ledger([{"id": "b1", "close_time": "CLOSE", "time": "OPEN",
                             "inst": "BTC", "status": "closed"}])
        db_manager.sync_json_to_sqlite()
        self.assertEqual(self.rows()[0]["time"], "CLOSE")

    def test_repeated_sync_is_idempotent(self):
        self._write_ledger([{"id": "b1", "inst": "BTC", "status": "closed"}])
        db_manager.sync_json_to_sqlite()
        db_manager.sync_json_to_sqlite()
        self.assertEqual(len(self.rows()), 1)


class RecordTradeSqliteTests(_DbFixture, unittest.TestCase):
    def test_single_trade_is_persisted(self):
        db_manager.record_trade_sqlite({"id": "b9", "inst": "SOL", "status": "closed",
                                        "environment": "live", "pnl": 2.0})
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["inst"], "SOL")
        self.assertEqual(rows[0]["environment"], "live")

    def test_record_does_not_duplicate_same_identity(self):
        payload = {"id": "b9", "inst": "SOL", "status": "closed", "environment": "live"}
        db_manager.record_trade_sqlite(payload)
        db_manager.record_trade_sqlite(dict(payload))
        self.assertEqual(len(self.rows()), 1)

    def test_missing_environment_is_unknown_legacy(self):
        db_manager.record_trade_sqlite({"id": "b9", "inst": "SOL", "status": "closed"})
        self.assertEqual(self.rows()[0]["environment"], db_manager.UNKNOWN_LEGACY)


class MainGuardTests(_DbFixture, unittest.TestCase):
    """`__main__` 做两件事：建库/升账 + 同步 JSON，并打印一行摘要。"""

    def _run_guard(self, stats, inserted):
        src = Path(db_manager.__file__).read_text(encoding="utf-8")
        node = next(n for n in ast.parse(src).body
                    if isinstance(n, ast.If) and n.lineno == 282)
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(compile(module, db_manager.__file__, "exec"),  # noqa: S102
                 {"__name__": "__main__", "init_database": lambda: stats,
                  "sync_json_to_sqlite": lambda: inserted, "print": print})
        return buf.getvalue()

    def test_summary_line_reports_mode_and_counts(self):
        out = self._run_guard({"mode": "rebuild", "migrated": 12, "defaulted": 3}, 12)
        self.assertIn("SQLite DB ready (rebuild, migrated=12, defaulted=3); synced 12 trades.",
                      out)

    def test_fresh_database_summary(self):
        out = self._run_guard({"mode": "fresh", "migrated": 0, "defaulted": 0}, 0)
        self.assertIn("SQLite DB ready (fresh, migrated=0, defaulted=0); synced 0 trades.",
                      out)


if __name__ == "__main__":
    unittest.main()

import sqlite3
import os
import re
import json
import datetime

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "r20_quant.db")
LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")

# ---- US-003 台账身份轴 -------------------------------------------------------
#: 历史无法证明环境的行诚实标 unknown_legacy（设计文档 §9：不一律按当前环境回填，
#: 保留原行与迁移证据）。环境轴字面值：okx 生产写方传 live|demo（current_environment
#: 同源），gate lab 传 live|demo，传不了的一律 unknown_legacy，不冒充。
UNKNOWN_LEGACY = "unknown_legacy"

#: 迁移可观测日志文件名。路径跟随 DB_PATH 所在目录：生产即
#: data/ledger_migration_log.json；测试 patch DB_PATH 钉临时库时日志自动落临时
#: 目录——绝不误写生产 data/（封闭三律）。
MIGRATION_LOG_NAME = "ledger_migration_log.json"


def migration_log_path():
    """迁移日志落点（DB 同目录，随 DB_PATH patch 联动）。"""
    return os.path.join(os.path.dirname(os.path.abspath(DB_PATH)) or ".", MIGRATION_LOG_NAME)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# trades v2 身份模型（US-003）：新增 environment / account_id / source_bill_id。
# 迁移路线抉择（记录为设计证据）：旧表 bill_id 是**列级 UNIQUE**，SQLite 无法
# ALTER DROP 列约束——不重建表则「同 bill_id 跨环境两行共存」物理上不可能，
# AC 的字面「不重建表」与「按身份唯一」不可兼得。故选 (a) trades_new 拷贝→改名
# 表重建，以实质标准满足：数据行零丢失（拷贝行数不等即整体回滚、原表原样保留）、
# 幂等可重跑（重建后检测恒为 noop）、迁移证据入日志。bill_id 列保留兼容旧读方，
# 唯一性由命名索引 (venue, environment, source_bill_id, action) 表达——所内原生
# ID 只在「所×环境×流水类型」身份空间内唯一（设计文档 §9）。
_TRADES_DDL_V2 = """CREATE TABLE {table} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id TEXT NOT NULL,
    time TEXT NOT NULL,
    inst TEXT NOT NULL,
    action TEXT NOT NULL,
    direction TEXT NOT NULL,
    size REAL,
    price REAL,
    fee REAL,
    gross_pnl REAL,
    pnl REAL,
    comment TEXT,
    venue TEXT NOT NULL DEFAULT 'okx',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    environment TEXT NOT NULL DEFAULT 'unknown_legacy',
    account_id TEXT NOT NULL DEFAULT '',
    source_bill_id TEXT
);"""

#: 拷贝/写入的完整列序（v2 单一事实）
_COLS_V2 = ["id", "bill_id", "time", "inst", "action", "direction", "size",
            "price", "fee", "gross_pnl", "pnl", "comment", "venue",
            "created_at", "environment", "account_id", "source_bill_id"]

#: 旧表 bill_id 列级 UNIQUE 的形态（sqlite_master.sql 检测用）
_LEGACY_BILL_UNIQUE_RE = re.compile(r"\bbill_id\b[^,()]*\bUNIQUE\b", re.I)


def _ensure_indexes(cur):
    """幂等索引：旧三索引 + 新 (venue, environment) + 身份唯一索引。"""
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_inst ON trades(inst);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_venue ON trades(venue);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_venue_env ON trades(venue, environment);")
    # 身份唯一：同 venue+environment+source_bill_id+action 至多一行（upsert 冲突目标）；
    # 跨环境/跨所同 ID 是不同身份，互不覆盖。
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_identity "
                "ON trades(venue, environment, source_bill_id, action);")


def _copy_expr(col, have):
    """v1*/v1.5/v2 混合旧列形态 → v2 拷贝 SELECT 表达式（缺列给诚实默认）。"""
    if col in ("bill_id", "time", "inst", "action", "direction", "id"):
        return col if col in have else "NULL"
    if col in ("size", "price", "fee", "gross_pnl", "pnl", "comment", "created_at"):
        return col if col in have else "NULL"
    if col == "venue":
        return "COALESCE(NULLIF(venue, ''), 'okx')" if col in have else "'okx'"
    if col == "environment":
        if col in have:
            return f"COALESCE(NULLIF(environment, ''), '{UNKNOWN_LEGACY}')"
        return f"'{UNKNOWN_LEGACY}'"  # 历史行无环境证据 → 诚实 unknown_legacy，不冒充
    if col == "account_id":
        return "COALESCE(account_id, '')" if col in have else "''"
    if col == "source_bill_id":
        # 从 bill_id 回填：旧行 bill_id 即其「原生 ID」语义（或合成回退串）
        return "COALESCE(source_bill_id, bill_id)" if col in have else "bill_id"
    return col if col in have else "NULL"


def _rebuild_to_v2(conn, cur, cols):
    """trades_new 拷贝→零丢失校验→改名。任一环节异常整体回滚，原表原样。"""
    before = cur.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    exprs = ", ".join(_copy_expr(c, cols) for c in _COLS_V2)
    collist = ", ".join(_COLS_V2)
    after = defaulted = 0
    try:
        cur.execute("BEGIN IMMEDIATE")  # DDL+DML 同事务：crash 不留半迁移态
        cur.execute(_TRADES_DDL_V2.format(table="trades_new"))
        cur.execute(f"INSERT INTO trades_new ({collist}) SELECT {exprs} FROM trades")
        after = cur.execute("SELECT COUNT(*) FROM trades_new").fetchone()[0]
        if after != before:
            raise RuntimeError(f"trades 迁移行数不一致 before={before} after={after}"
                               "——零丢失红线，回滚放弃改名")
        defaulted = cur.execute(
            "SELECT COUNT(*) FROM trades_new WHERE environment = ?",
            (UNKNOWN_LEGACY,)).fetchone()[0]
        cur.execute("DROP TABLE trades")
        cur.execute("ALTER TABLE trades_new RENAME TO trades")
        _ensure_indexes(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"mode": "rebuild", "migrated": after, "defaulted": defaulted,
            "before": before, "after": after}


def _write_migration_log(stats):
    """仅真实迁移事件（rebuild/alter）落日志；noop/fresh 不写——重跑幂等零 diff。"""
    entry = dict(stats)
    entry["ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    entry["db"] = DB_PATH
    try:
        path = migration_log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
    except Exception as e:
        # 观测日志不进关键路径：写失败不炸台账
        print(f"[db_manager] migration log write failed: {e}")


def init_database():
    """幂等建库/升账。返回本次观测统计 {mode, migrated, defaulted, before, after}。

    mode: fresh（新库直建 v2）| rebuild（旧表拷贝重建，含补列）| noop（已 v2）。
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = get_db()
    cur = conn.cursor()
    trow = cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table' "
                       "AND name='trades'").fetchone()
    stats = {"mode": "noop", "migrated": 0, "defaulted": 0, "before": 0, "after": 0}
    if trow is None:
        cur.execute(_TRADES_DDL_V2.format(table="trades"))
        _ensure_indexes(cur)
        conn.commit()
        stats["mode"] = "fresh"
    else:
        cols = [r[1] for r in cur.execute("PRAGMA table_info(trades)").fetchall()]
        table_sql = trow["sql"] or ""
        legacy_unique = bool(_LEGACY_BILL_UNIQUE_RE.search(table_sql))
        missing = [c for c in ("venue", "environment", "account_id", "source_bill_id")
                   if c not in cols]
        if legacy_unique or missing:
            stats = _rebuild_to_v2(conn, cur, cols)
        else:
            # 已 v2：索引兜底（若历史版本缺 uq_trades_identity 则此幂等补齐）
            _ensure_indexes(cur)
            conn.commit()
    conn.close()
    if stats.get("mode") == "rebuild":
        _write_migration_log(stats)
    return stats


def _normalize_row(t: dict):
    """dict → trades v2 全列值。environment 缺省诚实 unknown_legacy，绝不冒充。"""
    t_time = str(t.get("time", ""))
    inst = str(t.get("inst", t.get("name", "")))
    act = str(t.get("action", t.get("action_type", "")))
    px = float(t.get("price", 0.0) or 0.0)
    bill_id = t.get("bill_id") or t.get("id") or f"{t_time}_{inst}_{act}_{px}"
    environment = str(t.get("environment") or "").strip() or UNKNOWN_LEGACY
    account_id = str(t.get("account_id") or "").strip()
    return (
        str(bill_id),
        t_time,
        inst,
        act,
        str(t.get("direction", t.get("side", ""))),
        float(t.get("size") or t.get("sz") or 0.0),
        px,
        float(t.get("fee", 0.0) or 0.0),
        float(t.get("gross_pnl", 0.0) or t.get("pnl", 0.0) or 0.0),
        float(t.get("pnl", 0.0) or 0.0),
        str(t.get("comment") or t.get("remark") or t.get("exit_reason") or ""),
        str(t.get("venue") or "okx"),
        environment,
        account_id,
        str(t.get("source_bill_id") or bill_id),  # 原生 ID 身份轴
    )


# 身份元组 (venue, environment, source_bill_id, action) 冲突 → REPLACE 原位更新
# （upsert 语义：同账户同流水重复上报收敛为一行，不产生重复计费行；id 会重排，
# 消费方按身份/时间读取，不依赖 id 稳定性）。
_UPSERT_SQL = """
    INSERT OR REPLACE INTO trades
    (bill_id, time, inst, action, direction, size, price, fee, gross_pnl, pnl,
     comment, venue, environment, account_id, source_bill_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """


def sync_json_to_sqlite(default_environment=UNKNOWN_LEGACY):
    """JSON 台账 → SQLite。条目无环境证据保持 unknown_legacy，不冒充当前环境；
    可显式传 default_environment 供已知来源批量注入（默认诚实值）。"""
    init_database()
    if not os.path.exists(LEDGER_JSON_FILE):
        return 0

    try:
        with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
            trades = json.load(f)
    except Exception:
        trades = []

    conn = get_db()
    cursor = conn.cursor()

    inserted = 0
    for t in trades:
        # 逐字段保持旧 sync 的别名优先级（close_time/side/exit_reason...），
        # 统一在 v2 身份列上扩展 environment/account_id/source_bill_id。
        norm = {
            "time": str(t.get("close_time") or t.get("time") or t.get("open_time") or ""),
            "inst": str(t.get("inst") or t.get("name") or ""),
            "action": str(t.get("status") or t.get("action") or t.get("action_type") or "closed"),
            "price": t.get("close_px") or t.get("price") or t.get("open_px") or 0.0,
            "bill_id": t.get("id") or t.get("bill_id") or None,
            "direction": str(t.get("side") or t.get("direction") or ""),
            "size": t.get("sz") or t.get("size") or 0.0,
            "fee": t.get("fee", 0.0) or 0.0,
            "gross_pnl": t.get("gross_pnl", 0.0) or t.get("pnl", 0.0) or 0.0,
            "pnl": t.get("pnl", 0.0) or 0.0,
            "comment": str(t.get("exit_reason") or t.get("remark") or t.get("comment") or ""),
            "venue": str(t.get("venue") or "okx"),
            "environment": str(t.get("environment") or "").strip() or default_environment,
            "account_id": t.get("account_id") or "",
            "source_bill_id": t.get("source_bill_id") or t.get("id") or t.get("bill_id"),
        }
        cursor.execute(_UPSERT_SQL, _normalize_row(norm))
        if cursor.rowcount > 0:
            inserted += 1

    conn.commit()
    conn.close()
    return inserted


def record_trade_sqlite(trade_data: dict):
    init_database()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(_UPSERT_SQL, _normalize_row(trade_data))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    st = init_database()
    ins = sync_json_to_sqlite()
    print(f"SQLite DB ready ({st['mode']}, migrated={st['migrated']}, "
          f"defaulted={st['defaulted']}); synced {ins} trades.")

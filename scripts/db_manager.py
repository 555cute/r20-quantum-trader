import json
import os
import sqlite3
import sys

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from r20_exchange.runtime import state_path

DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "r20_quant.db")
LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")


def db_path() -> str:
    if DB_PATH != os.path.join(DATA_DIR, "r20_quant.db"):
        return DB_PATH
    path = state_path("r20_quant.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def ledger_path() -> str:
    if LEDGER_JSON_FILE != os.path.join(DATA_DIR, "trading_ledger.json"):
        return LEDGER_JSON_FILE
    return str(state_path("trading_ledger.json"))


def get_db():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    os.makedirs(os.path.dirname(db_path()) or DATA_DIR, exist_ok=True)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bill_id TEXT UNIQUE,
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_inst ON trades(inst);")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS backups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        backup_date TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_size INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()


def sync_json_to_sqlite():
    init_database()
    path = ledger_path()
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            trades = json.load(handle)
    except Exception:
        trades = []
    if not isinstance(trades, list):
        trades = []

    conn = get_db()
    cursor = conn.cursor()
    inserted = 0
    for trade in trades:
        t_time = str(trade.get("close_time") or trade.get("time") or trade.get("open_time") or "")
        inst = str(trade.get("inst") or trade.get("name") or "")
        act = str(trade.get("status") or trade.get("action") or trade.get("action_type") or "closed")
        px = float(trade.get("close_px") or trade.get("price") or trade.get("open_px") or 0.0)
        bill_id = trade.get("id") or trade.get("bill_id") or f"{t_time}_{inst}_{act}_{px}"
        cursor.execute(
            """
            INSERT OR REPLACE INTO trades
            (bill_id, time, inst, action, direction, size, price, fee, gross_pnl, pnl, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bill_id,
                t_time,
                inst,
                act,
                str(trade.get("side") or trade.get("direction") or ""),
                float(trade.get("sz") or trade.get("size") or 0.0),
                px,
                float(trade.get("fee", 0.0) or 0.0),
                float(trade.get("gross_pnl", 0.0) or trade.get("pnl", 0.0) or 0.0),
                float(trade.get("pnl", 0.0) or 0.0),
                str(trade.get("exit_reason") or trade.get("remark") or trade.get("comment") or ""),
            ),
        )
        if cursor.rowcount > 0:
            inserted += 1
    conn.commit()
    conn.close()
    return inserted


def record_trade_sqlite(trade_data: dict):
    init_database()
    conn = get_db()
    cursor = conn.cursor()
    t_time = str(trade_data.get("time", ""))
    inst = str(trade_data.get("inst", trade_data.get("name", "")))
    act = str(trade_data.get("action", trade_data.get("action_type", "")))
    px = float(trade_data.get("price", 0.0) or 0.0)
    bill_id = trade_data.get("bill_id") or f"{t_time}_{inst}_{act}_{px}"
    cursor.execute(
        """
        INSERT OR REPLACE INTO trades
        (bill_id, time, inst, action, direction, size, price, fee, gross_pnl, pnl, comment)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bill_id,
            t_time,
            inst,
            act,
            str(trade_data.get("direction", trade_data.get("side", ""))),
            float(trade_data.get("size") or trade_data.get("sz") or 0.0),
            px,
            float(trade_data.get("fee", 0.0) or 0.0),
            float(trade_data.get("gross_pnl", 0.0) or 0.0),
            float(trade_data.get("pnl", 0.0) or 0.0),
            str(trade_data.get("comment") or trade_data.get("remark") or ""),
        ),
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_database()
    ins = sync_json_to_sqlite()
    print(f"SQLite DB initialized and synced {ins} trades.")

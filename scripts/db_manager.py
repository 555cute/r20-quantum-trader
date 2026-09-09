import sqlite3
import os
import json
import datetime

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "r20_quant.db")
LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = get_db()
    cursor = conn.cursor()
    
    # 1. Trades table
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
        venue TEXT NOT NULL DEFAULT 'okx',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # 多交易所 Phase 1.5：存量库幂等补 venue 列（历史成交全部归属 okx）
    _cols = [r[1] for r in cursor.execute("PRAGMA table_info(trades)").fetchall()]
    if "venue" not in _cols:
        cursor.execute("ALTER TABLE trades ADD COLUMN venue TEXT NOT NULL DEFAULT 'okx'")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(time);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_inst ON trades(inst);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_venue ON trades(venue);")

    # 2. Daily Backups log
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
        t_time = str(t.get("close_time") or t.get("time") or t.get("open_time") or "")
        inst = str(t.get("inst") or t.get("name") or "")
        act = str(t.get("status") or t.get("action") or t.get("action_type") or "closed")
        px = float(t.get("close_px") or t.get("price") or t.get("open_px") or 0.0)
        bill_id = t.get("id") or t.get("bill_id") or f"{t_time}_{inst}_{act}_{px}"
        
        cursor.execute("""
        INSERT OR REPLACE INTO trades 
        (bill_id, time, inst, action, direction, size, price, fee, gross_pnl, pnl, comment, venue)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            bill_id,
            t_time,
            inst,
            act,
            str(t.get("side") or t.get("direction") or ""),
            float(t.get("sz") or t.get("size") or 0.0),
            px,
            float(t.get("fee", 0.0) or 0.0),
            float(t.get("gross_pnl", 0.0) or t.get("pnl", 0.0) or 0.0),
            float(t.get("pnl", 0.0) or 0.0),
            str(t.get("exit_reason") or t.get("remark") or t.get("comment") or ""),
            str(t.get("venue") or "okx"),
        ))
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

    cursor.execute("""
    INSERT OR REPLACE INTO trades 
    (bill_id, time, inst, action, direction, size, price, fee, gross_pnl, pnl, comment, venue)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
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
        str(trade_data.get("venue") or "okx"),
    ))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_database()
    ins = sync_json_to_sqlite()
    print(f"SQLite DB initialized and synced {ins} trades.")

import os
import sys
import json
import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import scripts.okx_rest as okx_rest
import scripts.okx_runtime as okx_runtime

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SNAPSHOTS_FILE = os.path.join(DATA_DIR, "snapshots.json")
ACCOUNT_INIT_FILE = os.path.join(DATA_DIR, "account_initial_state.json")

def generate_live_snapshots():
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    
    reset_time = "2026-08-29 01:11:20"
    initial_cap = float(os.getenv("INITIAL_CAPITAL", "10000.0"))
    if os.path.exists(ACCOUNT_INIT_FILE):
        try:
            with open(ACCOUNT_INIT_FILE, "r", encoding="utf-8") as f:
                acc = json.load(f)
                reset_time = acc.get("reset_time", reset_time)
                initial_cap = float(acc.get("initial_capital", initial_cap))
        except Exception:
            pass

    env = okx_runtime.current_environment()
    if not env.configured:
        # fail-closed (2026-09-09 CLI removal): never flatten snapshots.json without credentials
        raise okx_rest.OKXNotConfigured("OKX API Key 未配置 — 净值快照生成 fail-closed（保持 snapshots.json 不动）")

    # Fetch live OKX balance via direct signed V5 REST
    current_eq = initial_cap
    try:
        bal_rows = okx_rest.balances()
        for d in (bal_rows[0].get("details", []) if bal_rows else []):
            if d.get("ccy") == "USDT":
                current_eq = float(d.get("eq", initial_cap) or initial_cap)
                break
    except Exception:
        pass

    # Read OKX bills to construct intermediate equity points
    # (a fetch failure raises before the file write below — no empty-overwrite)
    bills = okx_rest.bills(limit=100)

    snapshots = [
        {
            "time": reset_time,
            "total_eq": initial_cap,
            "pnl": 0.0,
            "roi": 0.0
        }
    ]

    running_bal = initial_cap
    bills_after_reset = []
    for b in reversed(bills):
        ts = int(b.get("ts", 0) or 0) / 1000.0
        dt_bj = datetime.datetime.fromtimestamp(ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S")
        if dt_bj < reset_time:
            continue
        bal_chg = float(b.get("balChg", 0) or 0)
        running_bal += bal_chg
        pnl_val = round(running_bal - initial_cap, 2)
        roi_val = round((pnl_val / initial_cap * 100), 2)
        
        # Add snapshot point
        snapshots.append({
            "time": dt_bj,
            "total_eq": round(running_bal, 2),
            "pnl": pnl_val,
            "roi": roi_val
        })

    # Add current latest point
    now_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")
    cur_pnl = round(current_eq - initial_cap, 2)
    cur_roi = round((cur_pnl / initial_cap * 100), 2)
    snapshots.append({
        "time": now_str,
        "total_eq": round(current_eq, 2),
        "pnl": cur_pnl,
        "roi": cur_roi
    })

    with open(SNAPSHOTS_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshots, f, ensure_ascii=False, indent=2)

    print(f"✅ Generated {len(snapshots)} clean snapshots for Chart.js")

if __name__ == "__main__":
    try:
        generate_live_snapshots()
    except okx_rest.OKXNotConfigured as exc:
        print(f"[NOT READY] {exc}")
        raise SystemExit(3)

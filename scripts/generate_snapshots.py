import datetime
import json
import os
import sys

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)

from r20_backend.account_baseline import load_account_baseline
from r20_backend.account_paths import classify_bill
from r20_exchange.runtime import get_exchange, selected_environment, state_path

DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
SNAPSHOTS_FILE = os.path.join(DATA_DIR, "snapshots.json")


def snapshots_path() -> str:
    if SNAPSHOTS_FILE != os.path.join(DATA_DIR, "snapshots.json"):
        return SNAPSHOTS_FILE
    return str(state_path("snapshots.json"))


def generate_live_snapshots(exchange=None):
    env = selected_environment()
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    baseline = load_account_baseline()
    reset_time = baseline.get("reset_time", "1970-01-01 00:00:00")
    initial_cap = float(baseline.get("initial_capital") or os.getenv("INITIAL_CAPITAL", "10000.0"))

    client = exchange if exchange is not None else get_exchange(env)
    current_eq = initial_cap
    bal_data = client.balance() or []
    if isinstance(bal_data, list) and bal_data:
        for detail in bal_data[0].get("details", []):
            if detail.get("ccy") == "USDT":
                current_eq = float(detail.get("eq", initial_cap) or initial_cap)
                break

    bills = client.bills(limit=100) or []
    if not isinstance(bills, list):
        bills = []

    snapshots = [{"time": reset_time, "total_eq": initial_cap, "pnl": 0.0, "roi": 0.0}]
    running_bal = initial_cap
    for bill in reversed(bills):
        ts = int(bill.get("ts", 0) or 0) / 1000.0
        dt_bj = datetime.datetime.fromtimestamp(ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S")
        if dt_bj < reset_time:
            continue
        bal_chg = float(bill.get("balChg", 0) or 0)
        if bal_chg == 0:
            bal_chg = float(bill.get("pnl", 0) or 0) + float(bill.get("fee", 0) or 0)
            if classify_bill(bill) == "other" and bal_chg == 0:
                continue
        running_bal += bal_chg
        pnl_val = round(running_bal - initial_cap, 2)
        roi_val = round((pnl_val / initial_cap * 100), 2) if initial_cap else 0.0
        snapshots.append({
            "time": dt_bj,
            "total_eq": round(running_bal, 2),
            "pnl": pnl_val,
            "roi": roi_val,
        })

    now_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")
    cur_pnl = round(current_eq - initial_cap, 2)
    cur_roi = round((cur_pnl / initial_cap * 100), 2) if initial_cap else 0.0
    snapshots.append({
        "time": now_str,
        "total_eq": round(current_eq, 2),
        "pnl": cur_pnl,
        "roi": cur_roi,
        "exchange": env.exchange,
        "environment": env.mode,
        "quantity_unit": "base",
        "ctVal": "1",
    })

    out = snapshots_path()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(snapshots, handle, ensure_ascii=False, indent=2)
    print(f"Generated {len(snapshots)} snapshots -> {out}")
    return snapshots


if __name__ == "__main__":
    generate_live_snapshots()

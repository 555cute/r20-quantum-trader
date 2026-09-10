#!/usr/bin/env python3
"""Generate local R20 dashboard cache without an external console dependency."""

import os
import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from r20_backend.time_utils import beijing_day
from okx_runtime import replace_cli_prefix as okx_private_command
import json
import time
import subprocess
import datetime

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LOGS_DIR = os.path.join(WORKSPACE_DIR, "logs")
LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
SNAPSHOTS_JSON_FILE = os.path.join(DATA_DIR, "snapshots.json")
LOG_FILE = os.path.join(LOGS_DIR, "trading.log")
DATA_JSON_PATH = os.path.join(DATA_DIR, "trading_data.json")

from instrument_pool import load_instruments
from market_data_service import fetch_tickers_bulk, fetch_ticker

TARGET_INSTRUMENTS = load_instruments()

def run_json_cmd(cmd: str, timeout: int = 15):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        if res.stdout.strip():
            return json.loads(res.stdout.strip())
    except Exception:
        pass
    return None

def get_disk_info():
    try:
        import shutil
        total, used, free = shutil.disk_usage(WORKSPACE_DIR)
        return {
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "percent": round((used / total) * 100, 1)
        }
    except Exception:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0}

def generate_trading_data():
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    today_str = now_bj.strftime("%Y-%m-%d")

    auth_data = run_json_cmd("okx auth status --json") or {}
    is_authenticated = auth_data.get("status") == "logged_in"

    # 1. Balance
    bal_data = run_json_cmd(okx_private_command("okx account balance --json")) or []
    usdt_bal = {}
    if bal_data and isinstance(bal_data, list) and "details" in bal_data[0]:
        for d in bal_data[0]["details"]:
            if d.get("ccy") == "USDT":
                usdt_bal = d
                break

    total_eq = float(usdt_bal.get("eq", 0) or 0)
    avail_eq = float(usdt_bal.get("availEq", 0) or 0)
    cash_bal = float(usdt_bal.get("cashBal", 0) or 0)
    upl_acc = float(usdt_bal.get("upl", 0) or 0)

    # 2. Positions
    pos_data = run_json_cmd(okx_private_command("okx account positions --json")) or []
    positions = []
    long_count = 0
    short_count = 0
    total_pos_upl = 0.0

    if isinstance(pos_data, list):
        for p in pos_data:
            pos_sz = float(p.get("pos", 0) or 0)
            if pos_sz == 0:
                continue
            pos_side = p.get("posSide", "net")
            if pos_side == "long":
                long_count += 1
            elif pos_side == "short":
                short_count += 1
            upl = float(p.get("upl", 0) or 0)
            total_pos_upl += upl
            positions.append({
                "instId": p.get("instId"),
                "posSide": pos_side,
                "pos": p.get("pos"),
                "lever": p.get("lever", "3"),
                "avgPx": float(p.get("avgPx", 0) or 0),
                "markPx": float(p.get("markPx", 0) or 0),
                "upl": upl,
                "uplRatio": float(p.get("uplRatio", 0) or 0) * 100,
                "liqPx": p.get("liqPx", "--"),
                "bePx": p.get("bePx", "--")
            })

    # Fallback to last valid snapshot if balance is 0
    if total_eq == 0 and os.path.exists(SNAPSHOTS_JSON_FILE):
        try:
            with open(SNAPSHOTS_JSON_FILE, "r", encoding="utf-8") as f:
                snaps = json.load(f)
                valid_snaps = [s for s in snaps if s.get("equity", 0.0) > 0]
                if valid_snaps:
                    last_s = valid_snaps[-1]
                    total_eq = float(last_s.get("equity", 0.0))
                    avail_eq = float(last_s.get("avail", 0.0))
                    total_pos_upl = float(last_s.get("upl", 0.0))
        except Exception:
            pass

    # 3. Bills & Today PnL
    bills_data = run_json_cmd(okx_private_command("okx account bills --limit 100 --json")) or []
    today_realized_gross = 0.0
    today_fees = 0.0
    today_funding = 0.0
    today_win_trades = 0
    today_loss_trades = 0

    if isinstance(bills_data, list) and bills_data:
        for b in bills_data:
            ts = int(b.get("ts", 0) or 0) / 1000.0
            dt = datetime.datetime.fromtimestamp(ts, tz=tz_bj)
            if dt.strftime("%Y-%m-%d") == today_str:
                pnl = float(b.get("pnl", 0) or 0)
                fee = float(b.get("fee", 0) or 0)
                sub_type = str(b.get("subType", ""))
                
                today_fees += fee
                if sub_type in ["5", "6"]:  # Close
                    today_realized_gross += pnl
                    if pnl > 0:
                        today_win_trades += 1
                    elif pnl < 0:
                        today_loss_trades += 1
                elif sub_type in ["173", "174"]:  # Funding
                    today_funding += pnl
    else:
        # Load from JSON ledger
        if os.path.exists(LEDGER_JSON_FILE):
            try:
                with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
                    t_list = json.load(f)
                    for t in t_list:
                        if beijing_day(t.get("time")) == today_str:
                            p = float(t.get("pnl", 0.0) or 0)
                            if p > 0:
                                today_win_trades += 1
                                today_realized_gross += p
                            elif p < 0:
                                today_loss_trades += 1
                                today_realized_gross += p
            except Exception:
                pass

    net_realized_pnl = today_realized_gross + today_fees + today_funding
    total_closed = today_win_trades + today_loss_trades
    win_rate = round((today_win_trades / total_closed) * 100, 1) if total_closed > 0 else 0.0

    # 4. Snapshots & Trades from JSON
    snapshots = []
    trades = []
    if os.path.exists(SNAPSHOTS_JSON_FILE):
        try:
            with open(SNAPSHOTS_JSON_FILE, "r", encoding="utf-8") as f:
                snapshots = json.load(f)[-40:]
        except Exception:
            pass

    if os.path.exists(LEDGER_JSON_FILE):
        try:
            with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
                trades = list(reversed(json.load(f)))[:60]
        except Exception:
            pass

    # 5. Multi-factor signals & AI Brain Decisions
    ai_decisions_file = os.path.join(DATA_DIR, "ai_brain_decisions.json")
    ai_decisions = {}
    if os.path.exists(ai_decisions_file):
        try:
            with open(ai_decisions_file, "r", encoding="utf-8") as f:
                ai_decisions = json.load(f)
        except Exception:
            pass

    factors = []
    # Fetch all tickers in one single direct REST call (eliminates 6 repetitive Node CLI launches)
    bulk_tickers = fetch_tickers_bulk(inst_type="SWAP")
    for item in TARGET_INSTRUMENTS:
        inst_id = item["instId"]
        name = item["name"]
        ticker = bulk_tickers.get(inst_id) or fetch_ticker(inst_id) or {}
        last_px = float(ticker.get("last", 0) or 0)
        open24h = float(ticker.get("open24h", 0) or 0)
        high24h = float(ticker.get("high24h", 0) or 0)
        low24h = float(ticker.get("low24h", 0) or 0)
        chg_24h = round(((last_px - open24h) / open24h * 100) if open24h > 0 else 0, 2)
        
        ai_data = ai_decisions.get(inst_id, {})
        ai_dec = ai_data.get("decision", {})
        ai_thought = ai_data.get("thought_process", {})
        
        action = ai_dec.get("action", "WAIT")
        score = 0.0
        if action == "BUY_LONG":
            score = 2.5
        elif action == "SELL_SHORT":
            score = -2.5

        factors.append({
            "instId": inst_id,
            "name": name,
            "lastPx": last_px,
            "high24h": high24h,
            "low24h": low24h,
            "chg24h": chg_24h,
            "score": score,
            "action": action,
            "confidence": ai_dec.get("confidence"),
            "reason": ai_dec.get("summary_reason", "等待高确定性行情出现"),
            "thought_process": ai_thought,
            "ai_decision": ai_dec
        })

    # 6. Recent Logs
    logs = []
    if os.path.exists(LOG_FILE):
        try:
            res = subprocess.run(f"tail -n 60 {LOG_FILE}", shell=True, capture_output=True, text=True)
            logs = res.stdout.splitlines()
        except Exception:
            pass

    disk = get_disk_info()

    data = {
        "timestamp": now_bj.strftime("%Y-%m-%d %H:%M:%S (北京时间)"),
        "date": today_str,
        "auth": {
            "is_logged_in": is_authenticated,
            "status": auth_data.get("status", "not_logged_in"),
            "site": auth_data.get("site", "global"),
            "verificationUri": auth_data.get("verificationUri", "https://www.okx.com/account/oauth?flow=device"),
            "userCode": auth_data.get("userCode", "FSVD-HJVL")
        },
        "account": {
            "total_eq": round(total_eq, 2),
            "avail_eq": round(avail_eq, 2),
            "cash_bal": round(cash_bal, 2),
            "upl": round(upl_acc, 2),
            "pos_upl_total": round(total_pos_upl, 2),
            "margin_usage_pct": round(((total_eq - avail_eq) / total_eq * 100) if total_eq > 0 else 0, 1)
        },
        "today_stats": {
            "realized_gross": round(today_realized_gross, 2),
            "fees_paid": round(today_fees, 2),
            "funding_paid": round(today_funding, 2),
            "net_realized": round(net_realized_pnl, 2),
            "total_pnl": round(net_realized_pnl + total_pos_upl, 2),
            "win_trades": today_win_trades,
            "loss_trades": today_loss_trades,
            "win_rate": win_rate
        },
        "positions_summary": {
            "total": len(positions),
            "max": 10,
            "long_count": long_count,
            "short_count": short_count,
            "items": positions
        },
        "factors": factors,
        "snapshots": snapshots,
        "trades": trades,
        "logs": logs,
        "system": {
            "disk": disk
        }
    }

    # Write atomic JSON to local project data cache
    os.makedirs(DATA_DIR, exist_ok=True)
    temp_path = DATA_JSON_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, DATA_JSON_PATH)

if __name__ == "__main__":
    generate_trading_data()
    print("✅ Web data and JSON ledger synced successfully.")

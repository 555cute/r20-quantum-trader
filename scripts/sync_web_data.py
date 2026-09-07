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

import datetime
import json
import shutil

from instrument_pool import load_instruments
from market_data_service import fetch_ticker, fetch_tickers_bulk
from r20_backend.account_paths import classify_bill
from r20_exchange.runtime import get_exchange, selected_environment, state_path

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LOGS_DIR = os.path.join(WORKSPACE_DIR, "logs")
LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
SNAPSHOTS_JSON_FILE = os.path.join(DATA_DIR, "snapshots.json")
DATA_JSON_PATH = os.path.join(DATA_DIR, "trading_data.json")
LOG_FILE = os.path.join(LOGS_DIR, "trading.log")
TARGET_INSTRUMENTS = load_instruments()


def _scoped(current: str, name: str) -> str:
    if current == os.path.join(DATA_DIR, name):
        return str(state_path(name))
    return current


def get_disk_info():
    try:
        total, used, free = shutil.disk_usage("/")
        return {
            "total_gb": round(total / (1024 ** 3), 1),
            "used_gb": round(used / (1024 ** 3), 1),
            "free_gb": round(free / (1024 ** 3), 1),
            "percent": round(used / total * 100, 1) if total else 0,
        }
    except Exception:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0}


def generate_trading_data(exchange=None):
    env = selected_environment()
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    today_str = now_bj.strftime("%Y-%m-%d")
    client = exchange if exchange is not None else get_exchange(env)

    bal_data = client.balance() or []
    usdt_bal = {}
    if bal_data and isinstance(bal_data, list) and "details" in bal_data[0]:
        for detail in bal_data[0]["details"]:
            if detail.get("ccy") == "USDT":
                usdt_bal = detail
                break

    total_eq = float(usdt_bal.get("eq", 0) or 0)
    avail_eq = float(usdt_bal.get("availEq") or usdt_bal.get("availBal") or 0)
    cash_bal = float(usdt_bal.get("cashBal", 0) or 0)
    upl_acc = float(usdt_bal.get("upl", 0) or 0)

    pos_data = client.positions() or []
    positions = []
    long_count = 0
    short_count = 0
    total_pos_upl = 0.0
    if isinstance(pos_data, list):
        for pos in pos_data:
            pos_sz = abs(float(pos.get("pos", 0) or 0))
            if pos_sz == 0:
                continue
            pos_side = str(pos.get("posSide", "net")).lower()
            if "long" in pos_side:
                long_count += 1
            elif "short" in pos_side:
                short_count += 1
            upl = float(pos.get("upl", 0) or 0)
            total_pos_upl += upl
            mark_px = float(pos.get("markPx", 0) or 0)
            avg_px = float(pos.get("avgPx", 0) or 0)
            notional = float(pos.get("notionalUsd", 0) or 0) or pos_sz * (mark_px or avg_px)
            positions.append({
                "instId": pos.get("instId"),
                "posSide": pos_side,
                "pos": pos_sz,
                "quantity_unit": "base",
                "ctVal": "1",
                "lever": pos.get("lever", "3"),
                "avgPx": avg_px,
                "markPx": mark_px,
                "upl": upl,
                "uplRatio": float(pos.get("uplRatio", 0) or 0) * 100,
                "liqPx": pos.get("liqPx", "--"),
                "bePx": pos.get("bePx", "--"),
                "notional_usdt": round(notional, 2),
            })

    snapshots_file = _scoped(SNAPSHOTS_JSON_FILE, "snapshots.json")
    if total_eq == 0 and os.path.exists(snapshots_file):
        try:
            with open(snapshots_file, "r", encoding="utf-8") as handle:
                snaps = json.load(handle)
                valid_snaps = [s for s in snaps if s.get("equity", 0.0) > 0 or s.get("total_eq", 0.0) > 0]
                if valid_snaps:
                    last_s = valid_snaps[-1]
                    total_eq = float(last_s.get("equity", last_s.get("total_eq", 0.0)))
                    avail_eq = float(last_s.get("avail", avail_eq))
                    total_pos_upl = float(last_s.get("upl", total_pos_upl))
        except Exception:
            pass

    bills_data = client.bills(limit=100) or []
    today_realized_gross = 0.0
    today_fees = 0.0
    today_funding = 0.0
    today_win_trades = 0
    today_loss_trades = 0
    if isinstance(bills_data, list) and bills_data:
        for bill in bills_data:
            ts = int(bill.get("ts", 0) or 0) / 1000.0
            dt = datetime.datetime.fromtimestamp(ts, tz=tz_bj)
            if dt.strftime("%Y-%m-%d") != today_str:
                continue
            pnl = float(bill.get("pnl", 0) or 0)
            fee = float(bill.get("fee", 0) or 0)
            today_fees += fee
            kind = classify_bill(bill)
            if kind == "realized":
                today_realized_gross += pnl
                net = pnl + fee
                if net > 0:
                    today_win_trades += 1
                elif net < 0:
                    today_loss_trades += 1
            elif kind == "funding":
                today_funding += pnl if pnl != 0 else float(bill.get("balChg", 0) or 0)
    else:
        ledger_file = _scoped(LEDGER_JSON_FILE, "trading_ledger.json")
        if os.path.exists(ledger_file):
            try:
                with open(ledger_file, "r", encoding="utf-8") as handle:
                    for trade in json.load(handle):
                        if today_str in str(trade.get("time") or trade.get("close_time") or ""):
                            pnl = float(trade.get("pnl", 0.0) or 0)
                            if pnl > 0:
                                today_win_trades += 1
                            elif pnl < 0:
                                today_loss_trades += 1
                            today_realized_gross += pnl
            except Exception:
                pass

    net_realized_pnl = today_realized_gross + today_fees + today_funding
    total_closed = today_win_trades + today_loss_trades
    win_rate = round((today_win_trades / total_closed) * 100, 1) if total_closed > 0 else 0.0

    snapshots = []
    trades = []
    if os.path.exists(snapshots_file):
        try:
            with open(snapshots_file, "r", encoding="utf-8") as handle:
                snapshots = json.load(handle)[-40:]
        except Exception:
            pass
    ledger_file = _scoped(LEDGER_JSON_FILE, "trading_ledger.json")
    if os.path.exists(ledger_file):
        try:
            with open(ledger_file, "r", encoding="utf-8") as handle:
                trades = list(reversed(json.load(handle)))[:60]
        except Exception:
            pass

    ai_decisions_file = str(state_path("ai_brain_decisions.json"))
    ai_decisions = {}
    if os.path.exists(ai_decisions_file):
        try:
            with open(ai_decisions_file, "r", encoding="utf-8") as handle:
                ai_decisions = json.load(handle)
        except Exception:
            pass

    factors = []
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
        action = ai_dec.get("action", "WAIT")
        score = 2.5 if action == "BUY_LONG" else (-2.5 if action == "SELL_SHORT" else 0.0)
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
            "thought_process": ai_data.get("thought_process", {}),
            "ai_decision": ai_dec,
        })

    logs = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as handle:
                logs = [line.strip() for line in handle.readlines()[-60:] if line.strip()]
        except Exception:
            pass

    data = {
        "timestamp": now_bj.strftime("%Y-%m-%d %H:%M:%S (北京时间)"),
        "date": today_str,
        "exchange": env.exchange,
        "environment": env.mode,
        "identity": env.identity,
        "quantity_unit": "base",
        "ctVal": "1",
        "auth": {
            "is_logged_in": env.configured,
            "status": "configured" if env.configured else "not_configured",
            "exchange": env.exchange,
            "environment": env.mode,
        },
        "account": {
            "total_eq": round(total_eq, 2),
            "avail_eq": round(avail_eq, 2),
            "cash_bal": round(cash_bal, 2),
            "upl": round(upl_acc, 2),
            "pos_upl_total": round(total_pos_upl, 2),
            "margin_usage_pct": round(((total_eq - avail_eq) / total_eq * 100) if total_eq > 0 else 0, 1),
        },
        "today_stats": {
            "realized_gross": round(today_realized_gross, 2),
            "fees_paid": round(today_fees, 2),
            "funding_paid": round(today_funding, 2),
            "net_realized": round(net_realized_pnl, 2),
            "total_pnl": round(net_realized_pnl + total_pos_upl, 2),
            "win_trades": today_win_trades,
            "loss_trades": today_loss_trades,
            "win_rate": win_rate,
        },
        "positions_summary": {
            "total": len(positions),
            "max": 10,
            "long_count": long_count,
            "short_count": short_count,
            "items": positions,
        },
        "factors": factors,
        "snapshots": snapshots,
        "trades": trades,
        "logs": logs,
        "system": {"disk": get_disk_info()},
    }

    out = _scoped(DATA_JSON_PATH, "trading_data.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    temp_path = out + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, out)
    return data


if __name__ == "__main__":
    generate_trading_data()
    print("Web data synced.")

#!/usr/bin/env python3
"""Authentic positions-history ledger sync via get_exchange() structured methods."""
from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_DIR not in sys.path:
    sys.path.insert(0, WORKSPACE_DIR)
if os.path.join(WORKSPACE_DIR, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from r20_backend.account_baseline import load_account_baseline  # noqa: E402
from r20_backend.account_paths import history_fetch_incomplete, write_ledger_sync_status  # noqa: E402
from r20_exchange.runtime import get_exchange, selected_environment, state_path  # noqa: E402
from scripts.instrument_pool import load_instruments

DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
POSITION_TRACKER_FILE = os.path.join(DATA_DIR, "position_trackers.json")
TARGET_INSTRUMENTS = load_instruments()


def _path(legacy: str, name: str) -> str:
    if legacy == os.path.join(DATA_DIR, name):
        return str(state_path(name))
    return legacy


def ledger_path() -> str:
    return _path(LEDGER_JSON_FILE, "trading_ledger.json")


def tracker_path() -> str:
    return _path(POSITION_TRACKER_FILE, "position_trackers.json")


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _history_rows(raw):
    if raw is None:
        raise RuntimeError("Positions history response is unavailable")
    if isinstance(raw, dict):
        if (
            raw.get("incomplete")
            or raw.get("unavailable")
            or raw.get("window_limited")
            or raw.get("history_complete") is False
            or raw.get("status") in {"incomplete", "unavailable"}
        ):
            exc = RuntimeError("positions_history_incomplete")
            exc.incomplete = True  # type: ignore[attr-defined]
            exc.code = "positions_history_incomplete"  # type: ignore[attr-defined]
            exc.status = "unavailable"  # type: ignore[attr-defined]
            exc.window_limited = True  # type: ignore[attr-defined]
            raise exc
        return list(raw.get("items") or raw.get("data") or raw.get("positions") or [])
    if not isinstance(raw, list):
        raise RuntimeError("Positions history response has an invalid schema")
    return raw


def _net_pnl(gross: float, fee: float) -> float:
    """pnl is realized only; fee is signed commission. Net = pnl + fee, never fee-in-pnl twice."""
    return round(gross + fee, 2)


def build_lifecycle_ledger(exchange=None):
    env = selected_environment()
    ledger_file = ledger_path()
    trackers_file = tracker_path()
    os.makedirs(os.path.dirname(ledger_file), exist_ok=True)

    reset_time = load_account_baseline().get("reset_time", "1970-01-01 00:00:00")
    existing = _load_json(ledger_file, [])
    if not isinstance(existing, list):
        existing = []
    existing_closed_ids = {t["id"] for t in existing if isinstance(t, dict) and t.get("status") == "closed" and t.get("id")}
    trackers = _load_json(trackers_file, {})
    if not isinstance(trackers, dict):
        trackers = {}

    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    client = exchange if exchange is not None else get_exchange(env)
    try:
        pos_history = _history_rows(client.positions_history(limit=100))
    except Exception as exc:
        write_ledger_sync_status(
            {
                "incomplete": True,
                "status": "unavailable" if history_fetch_incomplete(exc) else "error",
                "reason": str(exc),
                "window_limited": bool(getattr(exc, "window_limited", False)),
                "exchange": env.exchange,
                "environment": env.mode,
                "identity": env.identity,
            },
            env,
        )
        return existing


    try:
        pos_data = client.positions()
        orders_history = client.order_history(limit=100)
        if not isinstance(pos_data, list) or not isinstance(orders_history, list):
            raise RuntimeError("Positions or orders response has an invalid schema")
    except Exception as exc:
        write_ledger_sync_status(
            {
                "incomplete": True,
                "status": "error",
                "reason": str(exc),
                "exchange": env.exchange,
                "environment": env.mode,
                "identity": env.identity,
            },
            env,
        )
        return existing

    close_orders = [o for o in orders_history if str(o.get("reduceOnly", "")).lower() == "true" and o.get("state") == "filled"]

    pool_ids = {item["instId"] for item in TARGET_INSTRUMENTS}
    trades_lifecycle = []

    for p in pos_data:
        pos_sz = abs(float(p.get("pos", 0.0) or 0.0))
        if pos_sz == 0.0:
            continue
        inst_id = p.get("instId", "")
        if inst_id not in pool_ids:
            continue
        inst = inst_id.replace("-USDT-SWAP", "")
        side_raw = str(p.get("posSide", p.get("side", ""))).lower()
        side = "多" if "long" in side_raw else "空"
        avg_px = float(p.get("avgPx", 0) or 0)
        mark_px = float(p.get("markPx", 0) or 0)
        upl = float(p.get("upl", 0) or 0)
        lever = int(float(p.get("lever", "3") or 3))
        fee = float(p.get("fee", 0.0) or 0.0)
        notional = float(p.get("notionalUsd", 0) or 0) or (pos_sz * (mark_px if mark_px > 0 else avg_px))
        margin_usdt = round(notional / lever, 2) if lever > 0 else round(notional, 2)
        roi_pct = round((upl / margin_usdt * 100) if margin_usdt > 0 else 0.0, 2)
        c_ts = int(p.get("cTime", 0) or 0) / 1000.0
        open_time = datetime.datetime.fromtimestamp(c_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if c_ts > 0 else "--"
        pos_k = f"{inst_id}_{'long' if side == '多' else 'short'}"
        t_info = trackers.get(pos_k, {})
        strat_tag = t_info.get("strategy_tag") or ("🌊 低吸" if side == "多" else "⚡ 高空")
        try:
            t1 = datetime.datetime.strptime(open_time, "%Y-%m-%d %H:%M:%S")
            now_dt = datetime.datetime.now(tz_bj).replace(tzinfo=None)
            dur_mins = int((now_dt - t1).total_seconds() / 60)
            duration_str = f"{dur_mins}分钟" if dur_mins < 60 else f"{dur_mins // 60}时{dur_mins % 60}分"
        except Exception:
            duration_str = "--"
        trades_lifecycle.append({
            "id": f"holding_{inst}_{side}",
            "inst": inst,
            "side": side,
            "lever": f"{lever}x",
            "strategy": strat_tag,
            "margin": margin_usdt,
            "sz": pos_sz,
            "quantity_unit": "base",
            "ctVal": "1",
            "open_time": open_time,
            "open_px": avg_px,
            "close_time": "持仓中...",
            "close_px": mark_px,
            "gross_pnl": round(upl, 2),
            "open_fee": round(fee, 4),
            "close_fee": 0.0,
            "fee": round(fee, 2),
            "pnl": round(upl, 2),
            "net_pnl": round(upl, 2),
            "roi_pct": roi_pct,
            "duration": duration_str,
            "status": "holding",
            "exit_reason": "⏳ 运行监控中",
        })

    for h in pos_history:
        if not isinstance(h, dict) or h.get("incomplete") or h.get("unavailable"):
            continue
        c_ts = int(h.get("cTime", 0) or 0) / 1000.0
        u_ts = int(h.get("uTime", 0) or 0) / 1000.0
        open_time = datetime.datetime.fromtimestamp(c_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if c_ts > 0 else "--"
        close_time = datetime.datetime.fromtimestamp(u_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if u_ts > 0 else "--"
        if close_time < reset_time:
            continue
        inst_id = h.get("instId", "")
        if inst_id not in pool_ids:
            continue
        inst = inst_id.replace("-USDT-SWAP", "")
        direction = str(h.get("direction", "")).lower()
        side = "多" if "long" in direction else "空"
        open_px = float(h.get("openAvgPx", 0) or 0)
        close_px = float(h.get("closeAvgPx", 0) or 0)
        gross_pnl = float(h.get("pnl", 0) or 0)
        fee = float(h.get("fee", 0) or 0)
        net_pnl = _net_pnl(gross_pnl, fee)
        raw_leverage = h.get("lever")
        lever = float(raw_leverage) if raw_leverage not in (None, "") else 0.0
        close_pos_sz = float(h.get("closeTotalPos", 0) or h.get("openMaxPos", 0) or 0)
        margin_usdt = None
        if lever > 0 and close_pos_sz > 0 and open_px > 0:
            margin_usdt = round(close_pos_sz * open_px / lever, 2)
        elif h.get("pnlRatio") not in (None, "", "0", 0):
            margin_usdt = round(abs(gross_pnl / float(h["pnlRatio"])), 2)
        roi_pct = round(net_pnl / margin_usdt * 100, 2) if margin_usdt and margin_usdt > 0 else None
        try:
            t1 = datetime.datetime.strptime(open_time, "%Y-%m-%d %H:%M:%S")
            t2 = datetime.datetime.strptime(close_time, "%Y-%m-%d %H:%M:%S")
            dur_mins = int((t2 - t1).total_seconds() / 60)
            duration_str = f"{dur_mins}分钟" if dur_mins < 60 else f"{dur_mins // 60}时{dur_mins % 60}分"
        except Exception:
            duration_str = "--"
        strat_tag = "🌊 顺势做多" if side == "多" else "⚡ 阻力高空"
        exit_type = str(h.get("type", ""))
        if exit_type == "3":
            exit_reason = "💥 强平出场"
        else:
            u_ms = int(h.get("uTime", 0) or 0)
            matched_close = next(
                (o for o in close_orders if o.get("instId") == inst_id and str(o.get("posSide", "")).lower() == direction and abs(int(o.get("uTime", 0) or 0) - u_ms) < 5000),
                None,
            )
            if matched_close:
                algo_id = matched_close.get("algoId")
                cl_ord_id = str(matched_close.get("clOrdId", ""))
                if algo_id:
                    if net_pnl > 3.0:
                        exit_reason = "🎯 目标止盈达成"
                    elif net_pnl < -1.0:
                        exit_reason = "🛑 触发云端止损"
                    else:
                        exit_reason = "🛡️ 移动止损保本出场"
                elif cl_ord_id.startswith("O") or "CLI" in str(matched_close.get("tag", "")):
                    if net_pnl > 3.0:
                        exit_reason = "✨ 移动止盈锁利"
                    elif net_pnl < -1.0:
                        exit_reason = "🛑 策略风控止损"
                    else:
                        exit_reason = "⏱️ 超时/保本平仓"
                else:
                    exit_reason = "🎯 目标止盈达成" if net_pnl > 3.0 else ("🛑 止损离场" if net_pnl < -1.0 else "🛡️ 保本平仓")
            else:
                exit_reason = "🎯 目标止盈达成" if net_pnl > 3.0 else ("🛑 止损出场" if net_pnl < -1.0 else "🛡️ 保本平仓")
        trades_lifecycle.append({
            "id": f"pos_hist_{h.get('posId') or u_ts}_{inst}_{direction}_{u_ts}",
            "inst": inst,
            "side": side,
            "lever": f"{lever:g}x" if lever > 0 else None,
            "strategy": strat_tag,
            "margin": margin_usdt,
            "sz": close_pos_sz,
            "quantity_unit": "base",
            "ctVal": "1",
            "open_time": open_time,
            "open_px": round(open_px, 4),
            "close_time": close_time,
            "close_px": round(close_px, 4),
            "gross_pnl": round(gross_pnl, 2),
            "open_fee": round(float(h["openFee"]), 4) if h.get("openFee") is not None else None,
            "close_fee": round(float(h["closeFee"]), 4) if h.get("closeFee") is not None else None,
            "fee": round(fee, 2),
            "pnl": net_pnl,
            "net_pnl": net_pnl,
            "roi": roi_pct,
            "roi_pct": roi_pct,
            "duration": duration_str,
            "status": "closed",
            "exit_reason": exit_reason,
        })

    # Exchange history is windowed; refresh known cycles without deleting older ones.
    closed_by_id = {
        row["id"]: row for row in existing
        if isinstance(row, dict) and row.get("status") == "closed" and row.get("id")
    }
    for row in trades_lifecycle:
        if row.get("status") == "closed":
            closed_by_id[row["id"]] = row
    current_holdings = [row for row in trades_lifecycle if row.get("status") != "closed"]
    trades_lifecycle = current_holdings + sorted(
        closed_by_id.values(), key=lambda row: str(row.get("close_time") or ""), reverse=True
    )

    fd, tmp_path = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=os.path.dirname(ledger_file) or DATA_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(trades_lifecycle, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, ledger_file)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    write_ledger_sync_status(
        {
            "incomplete": False,
            "status": "complete",
            "exchange": env.exchange,
            "environment": env.mode,
            "identity": env.identity,
            "trades": len(trades_lifecycle),
        },
        env,
    )

    try:
        from qq_notifier import notify_trade_close
        for trade in trades_lifecycle:
            if trade.get("status") == "closed" and trade.get("id") not in existing_closed_ids:
                notify_trade_close(
                    str(trade["inst"]), float(trade["net_pnl"]), str(trade["exit_reason"]),
                    float(trade["close_px"]), roi_pct=trade.get("roi_pct"),
                    duration_str=trade.get("duration"),
                )
    except Exception as exc:
        print(f"[Ledger Sync Notify Warning] {exc}")

    print(f"Ledger synced ({env.exchange}/{env.mode}): {len(trades_lifecycle)} trades -> {ledger_file}")
    return trades_lifecycle


if __name__ == "__main__":
    build_lifecycle_ledger()

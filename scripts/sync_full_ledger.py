#!/usr/bin/env python3
"""
R20 Authentic OKX Positions-History Ledger Synchronizer (sync_full_ledger.py)
Directly reads OKX official `account positions-history` & `account positions` API.
Eliminates bills heuristic split-error, accurately records real position-level trades!
"""

from okx_runtime import replace_cli_prefix as okx_private_command
import subprocess
import json
import os
import datetime
import tempfile

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
INITIAL_STATE_FILE = os.path.join(DATA_DIR, "account_initial_state.json")
POSITION_TRACKER_FILE = os.path.join(DATA_DIR, "position_trackers.json")

from instrument_pool import load_instruments

TARGET_INSTRUMENTS = load_instruments()

# 历史币种白名单缓存：交易所规格回退查询用（进程内一次即可）
_CTVAL_CACHE = {}

def _sqlite_traded_names():
    """SQLite 台账里出现过的币种名（已下架币种的历史事实源）。"""
    names = set()
    try:
        import sqlite3
        db = os.path.join(DATA_DIR, "r20_quant.db")
        if os.path.exists(db):
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            for (inst,) in con.execute("SELECT DISTINCT inst FROM trades"):
                if inst:
                    names.add(str(inst))
            con.close()
    except Exception:
        pass
    return names

def allowed_inst_ids(existing_ledger_trades=None):
    """台账重建允许集合 = 当前标的池 ∪ 历史留痕币种。

    修复(2026-09-09)：此前重建仅认当前池，用户从池中删除币种后，下一次同步
    会把该币种的全部已平仓历史从 trading_ledger.json 抹掉（SQLite 仍在，但页面
    台账消失）；持仓中途删币还会让在途仓位在台账里隐身。历史是交易所事实，
    不随池配置消亡；噪声过滤（拦截 R20 从未交易过的手动单）由并集继续保证。
    """
    allowed = {item["instId"] for item in TARGET_INSTRUMENTS}
    names = _sqlite_traded_names()
    for t in (existing_ledger_trades or []):
        inst = str(t.get("inst") or t.get("name") or "")
        if inst:
            names.add(inst)
    try:
        if os.path.exists(POSITION_TRACKER_FILE):
            with open(POSITION_TRACKER_FILE, "r", encoding="utf-8") as f:
                for key in json.load(f):
                    inst = str(key).rsplit("_", 1)[0]
                    if inst:
                        names.add(inst)
    except Exception:
        pass
    for n in names:
        n = n.strip()
        if not n:
            continue
        allowed.add(n if "-USDT-SWAP" in n or "-USD-SWAP" in n else f"{n}-USDT-SWAP")
    return allowed

def get_ct_val(inst_name):
    for item in TARGET_INSTRUMENTS:
        if item["name"] == inst_name or item["instId"] == inst_name:
            return item["ctVal"]
    # 已下架币种回退：查 OKX 公共合约规格（免费、无需鉴权），进程内缓存
    inst_id = inst_name if "-SWAP" in inst_name else f"{inst_name}-USDT-SWAP"
    if inst_id in _CTVAL_CACHE:
        return _CTVAL_CACHE[inst_id]
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId={inst_id}",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        rows = payload.get("data") or []
        ct = float(rows[0].get("ctVal", 1.0) or 1.0) if rows else 1.0
    except Exception:
        ct = 1.0
    _CTVAL_CACHE[inst_id] = ct
    return ct

def build_lifecycle_ledger():
    reset_time = "1970-01-01 00:00:00"
    if os.path.exists(INITIAL_STATE_FILE):
        try:
            with open(INITIAL_STATE_FILE, "r", encoding="utf-8") as f:
                acc = json.load(f)
                reset_time = acc.get("reset_time", "1970-01-01 00:00:00")
        except Exception:
            pass

    existing_closed_ids = set()
    old_trades = []
    if os.path.exists(LEDGER_JSON_FILE):
        try:
            with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
                old_trades = json.load(f)
                existing_closed_ids = {t["id"] for t in old_trades if t.get("status") == "closed"}
        except Exception:
            old_trades = []

    # 重建白名单 = 当前池 ∪ 历史留痕（SQLite/旧台账/持仓追踪），下架币种历史永久保留
    allowed = allowed_inst_ids(old_trades)

    trackers = {}
    if os.path.exists(POSITION_TRACKER_FILE):
        try:
            with open(POSITION_TRACKER_FILE, "r", encoding="utf-8") as f:
                trackers = json.load(f)
        except Exception:
            pass

    # 投委会溯源（2026-09-10）：持仓行的决策来源徽章来自最新 per-symbol 决策缓存。
    # 历史平仓行不伪造该数据——开仓当周期的委员会状态从未持久化，缺失就显示缺失。
    council_by_inst = {}
    try:
        with open(os.path.join(DATA_DIR, "ai_brain_decisions.json"), "r", encoding="utf-8") as f:
            _brain_cache = json.load(f)
        for _k, _v in (_brain_cache or {}).items():
            if not isinstance(_v, dict):
                continue
            _c = _v.get("council")
            if isinstance(_c, dict):
                _name = str(_v.get("name") or str(_k).split("-")[0])
                council_by_inst[_name] = _c
    except Exception:
        council_by_inst = {}

    tz_bj = datetime.timezone(datetime.timedelta(hours=8))

    # 1. Fetch OKX Official Positions History (Official position-level closed trades)
    res_hist = subprocess.run(okx_private_command("okx account positions-history --limit 100 --json"), shell=True, capture_output=True, text=True)
    pos_history = json.loads(res_hist.stdout) if res_hist.stdout else []

    # 2. Fetch OKX Current Live Positions (Holding trades)
    res_pos = subprocess.run(okx_private_command("okx account positions --json"), shell=True, capture_output=True, text=True)
    pos_data = json.loads(res_pos.stdout) if res_pos.stdout else []

    res_orders = subprocess.run(okx_private_command("okx swap orders --history --limit 100 --json"), shell=True, capture_output=True, text=True)
    orders_history = json.loads(res_orders.stdout) if res_orders.stdout else []
    close_orders = [o for o in orders_history if str(o.get('reduceOnly', '')).lower() == 'true' and o.get('state') == 'filled']

    trades_lifecycle = []

    # Process Active Holding Positions FIRST
    for p in pos_data:
        pos_sz = float(p.get("pos", 0.0) or 0.0)
        if pos_sz == 0.0:
            continue
        inst_id = p.get("instId", "")
        if inst_id not in allowed:
            continue
        inst = inst_id.replace("-USDT-SWAP", "")
        side_raw = p.get("posSide", p.get("side", "")).lower()
        side = "多" if "long" in side_raw else "空"
        avg_px = float(p.get("avgPx", 0) or 0)
        mark_px = float(p.get("markPx", 0) or 0)
        upl = float(p.get("upl", 0) or 0)
        lever = int(p.get("lever", "3") or 3)
        fee = float(p.get("fee", 0.0) or 0.0)
        ct_val = get_ct_val(inst)

        notional = pos_sz * ct_val * mark_px
        margin_usdt = round(notional / lever, 2)
        roi_pct = round((upl / margin_usdt * 100) if margin_usdt > 0 else 0.0, 2)

        # Time calculation
        c_ts = int(p.get("cTime", 0) or 0) / 1000.0
        open_time = datetime.datetime.fromtimestamp(c_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if c_ts > 0 else "--"

        pos_k = f"{inst_id}_{'long' if side=='多' else 'short'}"
        t_info = trackers.get(pos_k, {})
        strat_tag = t_info.get("strategy_tag") or ("🌊 低吸" if side == "多" else "⚡ 高空")

        try:
            t1 = datetime.datetime.strptime(open_time, "%Y-%m-%d %H:%M:%S")
            now_dt = datetime.datetime.now(tz_bj)
            dur_mins = int((now_dt - t1).total_seconds() / 60)
            duration_str = f"{dur_mins}分钟" if dur_mins < 60 else f"{dur_mins//60}时{dur_mins%60}分"
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
            "council": council_by_inst.get(inst),
        })

    # Process Official Closed Positions
    for h in pos_history:
        c_ts = int(h.get("cTime", 0) or 0) / 1000.0
        u_ts = int(h.get("uTime", 0) or 0) / 1000.0
        open_time = datetime.datetime.fromtimestamp(c_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if c_ts > 0 else "--"
        close_time = datetime.datetime.fromtimestamp(u_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if u_ts > 0 else "--"

        if close_time < reset_time:
            continue

        inst_id = h.get("instId", "")
        if inst_id not in allowed:
            continue
        inst = inst_id.replace("-USDT-SWAP", "")
        direction = str(h.get("direction", "")).lower()
        side = "多" if "long" in direction else "空"
        
        open_px = float(h.get("openAvgPx", 0) or 0)
        close_px = float(h.get("closeAvgPx", 0) or 0)
        gross_pnl = float(h.get("pnl", 0) or 0)
        fee = float(h.get("fee", 0) or 0)
        net_pnl = round(gross_pnl + fee, 2)
        lever = int(float(h.get("lever", "3") or 3))
        
        # Calculate Margin & Real Position Size
        ct_val = get_ct_val(inst)
        close_pos_sz = float(h.get("closeTotalPos", 0) or h.get("openMaxPos", 0) or 0)
        
        if close_pos_sz > 0 and open_px > 0 and ct_val > 0:
            notional = close_pos_sz * ct_val * open_px
            margin_usdt = round(notional / lever, 2) if lever > 0 else round(notional, 2)
        else:
            pnl_ratio = float(h.get("pnlRatio", 0) or 0)
            margin_usdt = 500.0 # Standard fallback
            if pnl_ratio != 0:
                est_margin = abs(gross_pnl / pnl_ratio)
                margin_usdt = round(est_margin, 2)
        
        roi_pct = round((net_pnl / margin_usdt * 100) if margin_usdt > 0 else 0.0, 2)

        # Duration
        try:
            t1 = datetime.datetime.strptime(open_time, "%Y-%m-%d %H:%M:%S")
            t2 = datetime.datetime.strptime(close_time, "%Y-%m-%d %H:%M:%S")
            dur_mins = int((t2 - t1).total_seconds() / 60)
            duration_str = f"{dur_mins}分钟" if dur_mins < 60 else f"{dur_mins//60}时{dur_mins%60}分"
        except Exception:
            duration_str = "--"

        # Strategy tag
        strat_tag = "🌊 顺势做多" if side == "多" else "⚡ 阻力高空"
        
        # Accurate Exit Reason Inference via Matched Close Order Attributes
        exit_type = str(h.get("type", ""))
        if exit_type == "3":
            exit_reason = "💥 强平出场"
        else:
            # Match filled close order within 5000ms window
            u_ms = int(h.get("uTime", 0) or 0)
            matched_close = next(
                (o for o in close_orders if o.get("instId") == inst_id and o.get("posSide") == direction and abs(int(o.get("uTime", 0) or 0) - u_ms) < 5000),
                None
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
                elif cl_ord_id.startswith("O") or "CLI" in matched_close.get("tag", ""):
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
            "id": f"pos_hist_{u_ts}_{inst}",
            "inst": inst,
            "side": side,
            "lever": f"{lever}x",
            "strategy": strat_tag,
            "margin": margin_usdt,
            "sz": round(close_pos_sz, 4),
            "open_time": open_time,
            "open_px": round(open_px, 4),
            "close_time": close_time,
            "close_px": round(close_px, 4),
            "gross_pnl": round(gross_pnl, 2),
            "open_fee": round(fee / 2.0, 4),
            "close_fee": round(fee / 2.0, 4),
            "fee": round(fee, 2),
            "pnl": net_pnl,
            "net_pnl": net_pnl,
            "roi": roi_pct,
            "roi_pct": roi_pct,
            "duration": duration_str,
            "status": "closed",
            "exit_reason": exit_reason
        })

    fd, tmp_path = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(trades_lifecycle, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, LEDGER_JSON_FILE)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Notify newly closed trades via QQ
    try:
        from qq_notifier import notify_trade_close
        for t in trades_lifecycle:
            if t["id"] not in existing_closed_ids and t.get("status") == "closed":
                notify_trade_close(
                    inst=t.get("inst", "CRYPTO"),
                    pnl=float(t.get("pnl", 0.0) or 0.0),
                    stage=t.get("exit_reason", "平仓结清"),
                    exit_px=float(t.get("close_px", 0.0) or 0.0),
                    roi_pct=float(t.get("roi_pct", 0.0) or 0.0),
                    duration_str=str(t.get("duration", "")),
                )
    except Exception as e:
        print(f"[Ledger Sync Notify Warning] {e}")

    print(f"✅ Authentic OKX Positions-History Ledger Generated: {len(trades_lifecycle)} total trades.")
    return trades_lifecycle

if __name__ == "__main__":
    build_lifecycle_ledger()

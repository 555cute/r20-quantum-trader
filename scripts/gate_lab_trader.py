"""Gate 平行试验田：与 OKX 主链完全隔离的第二场所闭环验证器。

数据源（全部只读复用主链产物，零共享写状态）：
- data/ai_brain_decisions.json —— 大模型每 15 分钟决策缓存（同款新鲜度门禁）；
- data/venue_routing.json      —— 分流策略（默认空池 + dry_run，未配置=不动作）；
- data/gate_lab_trackers.json  —— 试验田自有台账（独立于 OKX position_trackers）。

三重安全（缺一即 fail-safe 收敛为不交易）：
① 池配置非空；② R20_GATE_EXECUTION=1；③ Gate 凭证就绪。任一缺失 → dry_run
全链路演算（张数折算/几何/双腿触发计划全部可见，只不发单）。

用法：
    python scripts/gate_lab_trader.py            # 单轮（可挂 15 分钟 cron）
"""
from __future__ import annotations

import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
for _p in (PROJECT_ROOT, SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DECISION_FILE = os.path.join(DATA_DIR, "ai_brain_decisions.json")
LAB_TRACKER_FILE = os.path.join(DATA_DIR, "gate_lab_trackers.json")
LAB_LEDGER_FILE = os.path.join(DATA_DIR, "gate_lab_ledger.json")
DECISION_MAX_AGE_SECONDS = 300

from r20_backend import execution_router as router          # noqa: E402
from r20_backend.exchanges import get_adapter                # noqa: E402
from r20_backend.exchanges.routing_policy import (effective_mode, load_gate_pool)  # noqa: E402


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic_dump(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def fresh_decisions(pool_assets):
    """决策缓存 → {裸币名: decision dict}，同款 300s 新鲜度门禁。"""
    cache = _load_json(DECISION_FILE, {})
    out = {}
    now = int(time.time())
    for asset in pool_assets:
        item = cache.get(f"{asset}-USDT-SWAP") or cache.get(asset)
        if not isinstance(item, dict):
            continue
        if now - int(item.get("timestamp") or 0) > DECISION_MAX_AGE_SECONDS:
            continue
        dec = item.get("decision")
        if isinstance(dec, dict):
            out[asset] = dec
    return out


def _is_tighter(pos_side, new_sl, old_sl, mark_px):
    """止损只许收紧风险、绝不放宽（与主链同一铁律），且不越过现价。"""
    if new_sl <= 0:
        return False
    if pos_side == "long":
        return new_sl > old_sl and new_sl < mark_px
    return (old_sl <= 0 or new_sl < old_sl) and new_sl > mark_px


def plan_entry(dry, asset, dec, pool, ad):
    """dry=全链路演算（规格/张数/双腿触发计划），live=真实受保护开仓。"""
    margin = min(float(dec.get("margin_usdt") or pool["margin_per_trade_usdt"]),
                 float(pool["margin_per_trade_usdt"]))
    decision = {
        "asset": asset,
        "action": str(dec.get("action") or "").upper(),
        "margin_usdt": margin,
        "leverage": float(dec.get("leverage") or 3),
        "entry_price": float(dec.get("entry_price") or 0),
        "take_profit_price": float(dec.get("take_profit_price") or 0),
        "stop_loss_price": float(dec.get("stop_loss_price") or 0),
    }
    if not dry:
        return router.open_protected_position(decision, adapter=ad)
    # ---- dry_run：复刻路由的校验与折算，但不触任何私有端点 ----
    from r20_backend.exchanges.base import canonical_base
    try:
        from scripts.order_risk import validate_quote_geometry_and_rr
    except ImportError:
        from order_risk import validate_quote_geometry_and_rr
    act = decision["action"]
    if act not in ("BUY_LONG", "SELL_SHORT"):
        return {"ok": False, "stage": "validate", "detail": f"非开仓指令 {act}"}
    ok, reason, rr = validate_quote_geometry_and_rr(
        act, decision["entry_price"], decision["take_profit_price"], decision["stop_loss_price"])
    if not ok:
        return {"ok": False, "stage": "risk_gate", "detail": reason, "rr": rr}
    spec = ad.fetch_instrument_spec(canonical_base(asset))
    if spec is None:
        return {"ok": False, "stage": "specs", "detail": "合约规格不可得"}
    tick = ad.fetch_ticker(canonical_base(asset)) or {}
    ref = float(tick.get("mark_price") or tick.get("last") or 0)
    if ref <= 0:
        return {"ok": False, "stage": "price", "detail": "现价不可得"}
    contracts = ad.quote_qty_to_native(margin * decision["leverage"], ref, spec)
    if contracts <= 0:
        return {"ok": False, "stage": "sizing", "detail": "不足最小张数"}
    side = "long" if act == "BUY_LONG" else "short"
    tp_rule = ad.trigger_rule(side, "tp")
    sl_rule = ad.trigger_rule(side, "sl")
    tp_cond = "≥" if tp_rule == 1 else "≤"
    sl_cond = "≥" if sl_rule == 1 else "≤"
    return {"ok": True, "venue": "gate", "stage": "dry_run", "asset": asset,
            "action": act, "contracts": int(contracts),
            "size_signed": int(contracts) if side == "long" else -int(contracts),
            "ref_price": ref, "rr": round(rr, 3), "margin_usdt": margin,
            "detail": (f"[DRY] 若开闸将执行: set_leverage({decision['leverage']:g}) → "
                       f"限价{'多' if side == 'long' else '空'} {int(contracts)}张@{decision['entry_price']:g} → "
                       f"TP触发(price{tp_cond}{decision['take_profit_price']:g}) + "
                       f"SL触发(price{sl_cond}{decision['stop_loss_price']:g}) 双腿reduce_only")}


def run_lab_cycle(ad=None, now_ts=None):
    """单轮编排。ad 依赖注入便于全 mock 测试。返回动作列表（供日志/测试断言）。"""
    ad = ad or get_adapter("gate")
    pool = load_gate_pool()
    mode = effective_mode()
    actions = []
    if mode == "off":
        return actions
    dry = mode == "dry_run"
    trackers = _load_json(LAB_TRACKER_FILE, {})
    decisions = fresh_decisions(pool["assets"])
    live_positions = {}
    if not dry:
        try:
            for p in ad.positions():
                live_positions[p["base"]] = p
        except Exception as exc:
            actions.append(f"[GateLab] 持仓查询失败，本轮保守跳过: {exc}")
            return actions

    my_assets = set(pool["assets"])
    # ① 对账：试验田 tracker 有、交易所在途无 → 已平/被撤，落账清理
    for asset in list(trackers.keys()):
        if asset not in my_assets:
            continue
        t = trackers[asset]
        if dry and t.get("mode") != "dry":
            continue
        gone = (asset not in live_positions) if not dry and t.get("mode") == "live" else \
               (dry and t.get("mode") == "dry" and asset not in decisions)
        if gone:
            ledger = _load_json(LAB_LEDGER_FILE, [])
            ledger.append({"close_ts": now_ts or int(time.time()), "mode": t.get("mode"),
                           "asset": asset, "entry_px": t.get("entry_px"),
                           "contracts": t.get("contracts"), "reason": "reconcile_no_position"})
            _atomic_dump(LAB_LEDGER_FILE, ledger)
            del trackers[asset]
            actions.append(f"[GateLab] {asset} 试验田仓位平仓对账落账")

    open_count = sum(1 for a in trackers if a in my_assets)
    # ② 决策执行 + ③ 持仓管理 + ④ 保护缺口巡检
    for asset, dec in sorted(decisions.items()):
        act = str(dec.get("action") or "").upper()
        conf = float(dec.get("confidence") or 0)
        t = trackers.get(asset)
        if t and t.get("mode") == ("dry" if dry else "live"):
            if act == "CLOSE_MARKET" and conf >= 85:
                if dry:
                    actions.append(f"[GateLab] [DRY] {asset} CLOSE_MARKET conf={conf:.0f} → 将市价全平")
                    trackers.pop(asset, None)
                    continue
                r = router.close_position(asset, adapter=ad)
                actions.append(f"[GateLab] {asset} 平仓: {r.get('ok')} {r.get('detail', '')[:80]}")
                if r.get("ok"):
                    trackers.pop(asset, None)
                continue
            new_sl = float(dec.get("stop_loss_price") or 0)
            mark = float(live_positions.get(asset, {}).get("mark_price") or 0) if not dry else 0.0
            if dry:  # 演算态没有真实 mark，用决策入场价近似（棘轮判定偏保守）
                mark = float(dec.get("entry_price") or 0) or new_sl * 1.01
            if new_sl > 0 and _is_tighter(t.get("side", "long"), new_sl,
                                          float(t.get("sl_px") or 0), mark):
                if dry:
                    t["sl_px"] = new_sl
                    actions.append(f"[GateLab] [DRY] {asset} 止损棘轮 → {new_sl:g}")
                else:
                    try:
                        new_id = ad.amend_stop_loss(asset, t.get("side", "long"),
                                                    t.get("sl_id"), new_sl)
                        t["sl_px"], t["sl_id"] = new_sl, new_id or t.get("sl_id")
                        actions.append(f"[GateLab] {asset} 云端止损上移至 {new_sl:g}")
                    except Exception as exc:
                        actions.append(f"[GateLab] {asset} 棘轮失败(本地线保持): {exc}")
            if not dry:
                try:
                    open_ids = {str(o.get("id")) for o in ad.list_protective_orders(asset)}
                    if t.get("tp_id") not in open_ids or t.get("sl_id") not in open_ids:
                        legs = ad.attach_protective_orders(
                            asset, t.get("side", "long"),
                            tp_px=float(t.get("tp_px") or 0) or None,
                            sl_px=float(t.get("sl_px") or 0) or None)
                        t.update({"tp_id": legs.get("tp", t.get("tp_id")),
                                  "sl_id": legs.get("sl", t.get("sl_id"))})
                        actions.append(f"[GateLab] {asset} 保护缺口已补挂")
                except Exception as exc:
                    actions.append(f"[GateLab] {asset} 保护巡检失败: {exc}")
            continue

        if act in ("BUY_LONG", "SELL_SHORT") and conf >= float(pool["min_confidence"]):
            if t:
                # 已有另一模式的 tracker（如 live 仓位遇到 dry 轮）：绝不越权接管/覆盖
                actions.append(f"[GateLab] {asset} 已有 {t.get('mode')} 试验田仓位，{mode} 轮跳过")
                continue
            if open_count >= int(pool["max_open"]):
                actions.append(f"[GateLab] {asset} 试验田满仓({open_count}/{pool['max_open']})，跳过")
                continue
            r = plan_entry(dry, asset, dec, pool, ad)
            if r.get("ok"):
                trackers[asset] = {
                    "mode": "dry" if dry else "live", "venue": "gate", "asset": asset,
                    "side": "long" if act == "BUY_LONG" else "short",
                    "contracts": r.get("contracts"), "size_signed": r.get("size_signed"),
                    "entry_px": float(dec.get("entry_price") or 0),
                    "tp_px": float(dec.get("take_profit_price") or 0),
                    "sl_px": float(dec.get("stop_loss_price") or 0),
                    "order_id": r.get("order_id"), "tp_id": r.get("tp_id"), "sl_id": r.get("sl_id"),
                    "margin_usdt": r.get("margin_usdt"), "leverage": float(dec.get("leverage") or 0),
                    "entry_ts": now_ts or int(time.time()), "rr": r.get("rr"),
                }
                open_count += 1
                actions.append(f"[GateLab] {asset} 开仓{'演算' if dry else ''}: {r.get('detail', '')[:140]}")
            else:
                actions.append(f"[GateLab] {asset} 开仓被拒[{r.get('stage')}]: {r.get('detail', '')[:120]}")

    _atomic_dump(LAB_TRACKER_FILE, trackers)
    return actions


def main():
    mode = effective_mode()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[GateLab] {ts} 模式={mode} 池={load_gate_pool()['assets']}")
    if mode == "off":
        print("[GateLab] 池为空(data/venue_routing.json 配 gate.assets)——本轮无动作")
        return 0
    acts = run_lab_cycle()
    for a in acts:
        print(a)
    if not acts:
        print("[GateLab] 本轮无可执行动作（决策 WAIT/不新鲜/满仓）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

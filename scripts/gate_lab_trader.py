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
from datetime import datetime, timedelta, timezone

_BJ = timezone(timedelta(hours=8))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
for _p in (PROJECT_ROOT, SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DECISION_FILE = os.path.join(DATA_DIR, "ai_brain_decisions.json")
LAB_TRACKER_FILE = os.path.join(DATA_DIR, "gate_lab_trackers.json")
LAB_LEDGER_FILE = os.path.join(DATA_DIR, "gate_lab_ledger.json")

# G8 保护腿生命周期：触发单 expiration=7 天（Gate 默认 604800），到期即离开
# open 列表 = 保护蒸发。双保险：① 距到期 <24h 主动换腿（attach 新腿成功后才撤
# 旧腿，失败不阻塞——reduce_only 双触发短暂共存无害）；② 巡检无条件跑（决策
# 断档周期也巡检全部 live 仓，不留空窗）。
PROTECTION_TTL_SECONDS = 604800
RELEG_LEAD_SECONDS = 86400
# brain 持仓管理指令流（US-009）：与主链 execute_ai_position_management 同源同语义，
# instructions 数组含 UPDATE_SL/CLOSE_MARKET/HOLD + suggested_sl_price + confidence。
PM_FILE = os.path.join(DATA_DIR, "ai_position_management.json")
# 跨所敞口只读源（US-005）：主链本地 tracker + 合约池 ctVal——零新凭证、零新网络。
# 调研结论：无更干净的只读源（ai_position_management.json 只有指令无名义，
# position_trackers 是唯一含 currentSz/entryPx/side 的 OKX 在管仓位本地快照）。
OKX_TRACKER_FILE = os.path.join(DATA_DIR, "position_trackers.json")
INSTRUMENT_POOL_FILE = os.path.join(DATA_DIR, "instrument_pool.json")
DECISION_MAX_AGE_SECONDS = 300
DEFAULT_MAX_TOTAL_EXPOSURE_USDT = 500.0

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


def pm_instructions() -> dict:
    """读 brain 指令流（同款 300s 新鲜度），{裸币名: instruction}。损坏/过期=空。"""
    doc = _load_json(PM_FILE, {})
    try:
        if int(time.time()) - int(doc.get("timestamp") or 0) > DECISION_MAX_AGE_SECONDS:
            return {}
        out = {}
        for row in doc.get("instructions") or []:
            if not isinstance(row, dict):
                continue
            raw = str(row.get("instId") or row.get("asset") or "").strip().upper()
            asset = raw.split("-")[0].split("_")[0]
            if asset:
                out[asset] = row
        return out
    except Exception:
        return {}


def _resolve_mgmt(asset: str, dec: dict, pm: dict):
    """US-009 裁决：position_management 指令流优先、decision 兜底（与主链同语义）。

    返回 (want_close, sl_target, notes[])——notes 为冲突/抑制留痕。
    CLOSE 阈值 conf≥85 与主链一致；UPDATE_SL 取 suggested_sl_price；
    HOLD 压制一切管理动作（=指令流要求维持现状）。
    """
    notes = []
    dec_act = str(dec.get("action") or "").upper()
    if not pm:
        return (dec_act == "CLOSE_MARKET" and float(dec.get("confidence") or 0) >= 85,
                float(dec.get("stop_loss_price") or 0), notes)
    pm_act = str(pm.get("action") or "").upper()
    pm_conf = float(pm.get("confidence") or 0)
    if pm_act == "CLOSE_MARKET":
        if dec_act != "CLOSE_MARKET":
            notes.append(f"指令流 CLOSE_MARKET 覆盖 decision({dec_act})")
        if pm_conf < 85:
            notes.append(f"指令流 CLOSE conf={pm_conf:.0f}<85 压制平仓"
                         + ("（decision 同为 CLOSE，一并压制）" if dec_act == "CLOSE_MARKET" else ""))
            return False, 0.0, notes
        return True, 0.0, notes
    if pm_act == "UPDATE_SL":
        sl = float(pm.get("suggested_sl_price") or 0)
        dsl = float(dec.get("stop_loss_price") or 0)
        if dsl > 0 and sl > 0 and dsl != sl:
            notes.append(f"SL 价格冲突: decision={dsl:g} vs 指令流={sl:g}，以指令流为准")
        return False, sl, notes
    if pm_act == "HOLD":
        if dec_act == "CLOSE_MARKET" or float(dec.get("stop_loss_price") or 0) > 0:
            notes.append("指令流 HOLD 压制 decision 的平仓/止损调整")
        return False, 0.0, notes
    notes.append(f"未知指令流 action({pm_act})，忽略")
    return False, 0.0, notes


def _derive_margin_mode(acct: dict, live_positions: dict) -> str:
    """从账户实况推导保证金模式（勿硬编码 cross）：持仓行 margin_mode 字段优先
    （Gate 实测形态 cross_mode/isolated_mode，裸值也兼容），无持仓按单双向模式推
    （dual→isolated 常见配套，单向→cross），全未知回退 cross。"""
    for p in (live_positions or {}).values():
        mm = str(p.get("margin_mode") or "").lower()
        if "isolated" in mm:
            return "isolated"
        if "cross" in mm:
            return "cross"
    if acct.get("in_dual_mode"):
        return "isolated"
    return "cross"


# ---------------------------------------------------------------------------
# US-005：主台账写入 / 跨所同向敞口合并 / G7 孤儿挂单清扫
# ---------------------------------------------------------------------------

def _exposure_cap() -> float:
    try:
        return max(1.0, float(os.environ.get("R20_MAX_TOTAL_EXPOSURE_USDT",
                                             DEFAULT_MAX_TOTAL_EXPOSURE_USDT)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOTAL_EXPOSURE_USDT


def _okx_notional_for(asset: str, side: str):
    """OKX 主链同资产同向在管名义（USDT）。返回 (notional, ok)。

    只读 data/position_trackers.json（主链子进程维护的本地快照）× instrument_pool ctVal：
    notional = currentSz(张) * ctVal * entryPx。文件缺失视为 0 且 ok=True；
    任何解析异常 ok=False（live 据此保守拒开，dry 放行）。
    """
    try:
        if not os.path.exists(OKX_TRACKER_FILE):
            return 0.0, True
        try:
            with open(OKX_TRACKER_FILE, "r", encoding="utf-8") as f:
                trackers = json.load(f)
        except Exception:
            return 0.0, False   # 文件损坏 ≠ 无仓位：live 必须 fail-closed
        if not isinstance(trackers, dict):
            return 0.0, False
        if not os.path.exists(INSTRUMENT_POOL_FILE):
            pool = []
        else:
            with open(INSTRUMENT_POOL_FILE, "r", encoding="utf-8") as f:
                pool = json.load(f)
        items = pool if isinstance(pool, list) else pool.get("instruments", [])
        ct_val = {str(i.get("name") or "").upper(): float(i.get("ctVal") or 0)
                  for i in items if isinstance(i, dict)}
        target = f"{asset}-USDT-SWAP"
        want_side = "long" if str(side).lower().startswith("l") else "short"
        total = 0.0
        for key, t in trackers.items():
            if not isinstance(t, dict) or str(t.get("instId") or "") != target:
                continue
            t_side = str(t.get("side") or "").lower()
            if want_side == "long" and ("short" in t_side):
                continue
            if want_side == "short" and ("short" not in t_side):
                continue
            sz = abs(float(t.get("currentSz") or t.get("initialSz") or 0))
            total += sz * ct_val.get(asset, 0.0) * float(t.get("entryPx") or 0)
        return round(total, 2), True
    except Exception:
        return 0.0, False


def _lab_same_side_notional(trackers, asset: str, side: str) -> float:
    want = "long" if str(side).lower().startswith("l") else "short"
    total = 0.0
    for a, t in (trackers or {}).items():
        if a == asset or not isinstance(t, dict):
            continue
        if ("long" if str(t.get("side") or "long").lower().startswith("l") else "short") != want:
            continue
        try:
            total += float(t.get("margin_usdt") or 0) * float(t.get("leverage") or 0)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


def _sweep_orphan_orders(ad, asset: str, t: dict, actions):
    """G7：live 对账判「仓位消失」落账前，先撤该合约 lab 名下残留——
    未成交入场挂单逐笔撤 + 按记录的 tp_id/sl_id 尝试撤触发单。失败不阻塞，记动作行。"""
    swept = []
    try:
        for o in ad.list_open_orders(asset):
            oid = (o or {}).get("id")
            if oid in (None, ""):
                continue
            try:
                ad.cancel_order(asset, oid)
                swept.append(f"order:{oid}")
            except Exception as exc:
                actions.append(f"[GateLab] {asset} 孤儿挂单 {oid} 撤销失败(不阻塞落账): {exc}")
    except Exception as exc:
        actions.append(f"[GateLab] {asset} 孤儿挂单查询失败(不阻塞落账): {exc}")
    for leg in ("tp_id", "sl_id"):
        oid = t.get(leg)
        if not oid:
            continue
        try:
            ad.cancel_price_order(oid)
            swept.append(f"{leg}:{oid}")
        except Exception:
            pass   # 触发单多已随成交/平仓自然失效，撤不到是常态
    return swept


def _record_main_ledger(t: dict, *, close_px: float = 0.0, pnl=None,
                        reason: str = "", approx: bool = False):
    """live 试验田平仓写 trades 主台账（venue=gate）。异常只 log 不炸周期。"""
    try:
        try:
            import db_manager as dbm
        except ImportError:
            from scripts import db_manager as dbm
        asset = str(t.get("asset") or "")
        side = "long" if str(t.get("side") or "long").lower().startswith("l") else "short"
        entry = float(t.get("entry_px") or 0)
        price = float(close_px or 0) or entry
        pnl_v = 0.0 if pnl is None else round(float(pnl), 4)
        comment = f"Gate试验田平仓({reason})；lab台账口径,pnl待交易所账单核对"
        if approx:
            comment += "；成交价按mark近似"
        bill_id = f"gatelib-{t.get('mode', 'live')}-{asset}-{int(t.get('entry_ts') or time.time())}"
        _m = str(t.get("mode") or "")
        mode_env = _m if _m in ("live", "demo") else "unknown_legacy"
        dbm.record_trade_sqlite({
            "bill_id": bill_id,
            "time": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),  # trades.time 固定格式，北京时间
            "inst": f"{asset}_USDT",
            "action": "closed",
            "direction": "多" if side == "long" else "空",
            "size": float(t.get("contracts") or 0),
            "price": price,
            "pnl": pnl_v, "gross_pnl": pnl_v, "fee": 0.0,
            "comment": comment,
            "venue": "gate",
            # US-003 环境轴：lab mode 与交易所资金环境同名轴（live|demo）；
            # 意外值不冒充，落 unknown_legacy 由 db_manager 兜底。
            "environment": mode_env,
        })
        return True, bill_id
    except Exception as exc:
        return False, str(exc)


def plan_entry(dry, asset, dec, pool, ad, *, own_position=None, margin_mode=None):
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
        return router.open_protected_position(decision, adapter=ad,
                                              own_position=own_position,
                                              margin_mode=margin_mode)
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


def _inspect_protection_legs(ad, asset: str, t: dict, actions, now_ts: int,
                             *, handled: set) -> None:
    """G8 保护腿巡检 + 到期前 24h 主动换腿（live 仓专用；单仓异常不外溢）。

    - 缺腿（tp/sl 任一不在 open 列表）→ 按既有语义补挂；
    - 腿全在但距到期 <24h（attached_ts + TTL - LEAD）→ 换腿：先 attach 新腿
      （更新在册 id + attached_ts），成功后再撤旧腿（撤失败不阻塞——
      reduce_only 双触发短暂共存无害）；先撤后挂留空窗，绝不采用；
    - 任何巡检/网络异常记动作行返回，本轮其余仓位继续巡检。
    """
    handled.add(asset)
    try:
        open_ids = {str(o.get("id")) for o in ad.list_protective_orders(asset)}
    except Exception as exc:
        actions.append(f"[GateLab] {asset} 保护巡检失败: {exc}")
        return
    tp_ok = t.get("tp_id") in open_ids
    sl_ok = t.get("sl_id") in open_ids
    if tp_ok and sl_ok:
        age = now_ts - int(t.get("attached_ts") or 0)
        if age < PROTECTION_TTL_SECONDS - RELEG_LEAD_SECONDS:
            return
        actions.append(f"[GateLab] {asset} 触发单距到期不足24h，主动换腿")
    try:
        legs = ad.attach_protective_orders(
            asset, t.get("side", "long"),
            tp_px=float(t.get("tp_px") or 0) or None,
            sl_px=float(t.get("sl_px") or 0) or None)
        old_ids = [oid for oid in (t.get("tp_id"), t.get("sl_id")) if oid]
        t.update({"tp_id": legs.get("tp", t.get("tp_id")),
                  "sl_id": legs.get("sl", t.get("sl_id")),
                  "attached_ts": now_ts})
        if tp_ok and sl_ok:
            # 换腿路径：新腿挂成后才撤旧（撤失败=双触发短暂共存，reduce_only 无害）
            for oid in old_ids:
                if oid in open_ids:
                    try:
                        ad.cancel_price_order(oid)
                    except Exception as exc:
                        actions.append(f"[GateLab] {asset} 旧腿 {oid} 撤销失败(不阻塞): {exc}")
            actions.append(f"[GateLab] {asset} 换腿完成 → tp={t.get('tp_id')} sl={t.get('sl_id')}")
        else:
            actions.append(f"[GateLab] {asset} 保护缺口已补挂")
    except Exception as exc:
        actions.append(f"[GateLab] {asset} 保护巡检失败: {exc}")


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
    # ⓪ US-009 live 启动体检：dual 模式下 Gate close=true 语义不可靠（会连坐平掉
    # 非 lab 名下腿）→ 禁开新仓；快照读不到同样 fail-closed 禁开（管理动作不受限）。
    margin_mode = None
    entry_allowed = True
    if not dry:
        try:
            acct = ad.account_snapshot() or {}
            if acct.get("in_dual_mode"):
                entry_allowed = False
                actions.append("[GateLab][PRECHECK] 账户为双向(hedge)持仓模式，close=true 全平语义"
                               "在双仓下不可靠——本轮禁开新仓（请在 Gate 端切 single/net 模式）")
            margin_mode = _derive_margin_mode(acct, live_positions)
        except Exception as exc:
            entry_allowed = False
            actions.append(f"[GateLab][PRECHECK] 账户快照不可读，本轮禁开新仓(fail-closed): {exc}")
    pm_map = pm_instructions()
    # ① 对账：试验田 tracker 有、交易所在途无 → 已平/被撤，落账清理

    def _live_pnl_estimate(t):
        """平仓 pnl 估算：mark(或现价近似) 与入场差 × 带符号张数 × 每张面值。取不到返回 (0, px, True)。"""
        try:
            tick = ad.fetch_ticker(t.get("asset") or "") or {}
            px = float(tick.get("mark_price") or tick.get("last") or 0)
            spec = ad.fetch_instrument_spec(t.get("asset") or "")
            ct = float(getattr(spec, "ct_val", 0) or 0) if spec else 0.0
            signed = float(t.get("size_signed") or 0)
            entry = float(t.get("entry_px") or 0)
            if px > 0 and ct > 0 and signed != 0 and entry > 0:
                return round((px - entry) * signed * ct, 4), px, True
        except Exception:
            pass
        return 0.0, float(t.get("entry_px") or 0), True

    for asset in list(trackers.keys()):
        if asset not in my_assets:
            continue
        t = trackers[asset]
        if dry and t.get("mode") != "dry":
            continue
        gone = (asset not in live_positions) if not dry and t.get("mode") == "live" else \
               (dry and t.get("mode") == "dry" and asset not in decisions)
        if gone:
            swept: list = []
            if not dry and t.get("mode") == "live":
                swept = _sweep_orphan_orders(ad, asset, t, actions)   # G7：落账前先扫孤儿
                pnl, px, approx = _live_pnl_estimate(t)
                written, info = _record_main_ledger(t, close_px=px, pnl=pnl,
                                                    reason="reconcile_no_position",
                                                    approx=approx)
                if not written:
                    actions.append(f"[GateLab] {asset} 主台账写入失败(本地账保留): {info[:100]}")
            ledger = _load_json(LAB_LEDGER_FILE, [])
            entry_ledger = {"close_ts": now_ts or int(time.time()), "mode": t.get("mode"),
                            "asset": asset, "entry_px": t.get("entry_px"),
                            "contracts": t.get("contracts"), "reason": "reconcile_no_position"}
            if swept:
                entry_ledger["orphan_swept"] = swept
            ledger.append(entry_ledger)
            _atomic_dump(LAB_LEDGER_FILE, ledger)
            del trackers[asset]
            actions.append(f"[GateLab] {asset} 试验田仓位平仓对账落账"
                           + (f"（已撤孤儿挂单 {len(swept)} 笔）" if swept else ""))

    open_count = sum(1 for a in trackers if a in my_assets)
    # ② 决策执行 + ③ 持仓管理 + ④ 保护缺口巡检（决策驱动）+ ⑤ G8 无条件巡检
    handled_assets: set = set()
    for asset, dec in sorted(decisions.items()):
        act = str(dec.get("action") or "").upper()
        conf = float(dec.get("confidence") or 0)
        t = trackers.get(asset)
        if t and t.get("mode") == ("dry" if dry else "live"):
            # US-009：指令流优先裁决（与主链同语义），decision 兜底；冲突留痕
            want_close, sl_target, mgmt_notes = _resolve_mgmt(asset, dec, pm_map.get(asset))
            for n in mgmt_notes:
                actions.append(f"[GateLab] {asset} 指令流裁决：{n}")
            if want_close:
                if dry:
                    actions.append(f"[GateLab] [DRY] {asset} CLOSE_MARKET → 将市价全平（指令流裁决）")
                    trackers.pop(asset, None)
                    continue
                r = router.close_position(asset, adapter=ad)
                actions.append(f"[GateLab] {asset} 平仓: {r.get('ok')} {r.get('detail', '')[:80]}")
                if r.get("ok"):
                    t = trackers.pop(asset, None) or {}
                    mark = float(live_positions.get(asset, {}).get("mark_price") or 0)
                    pnl, px, approx = _live_pnl_estimate(t)
                    px = mark or px
                    written, info = _record_main_ledger(
                        t or {"asset": asset, "mode": "live"}, close_px=px, pnl=pnl,
                        reason="close_market", approx=approx)
                    if not written:
                        actions.append(f"[GateLab] {asset} 平仓主台账写入失败: {info[:100]}")
                    ledger = _load_json(LAB_LEDGER_FILE, [])
                    ledger.append({"close_ts": now_ts or int(time.time()), "mode": "live",
                                   "asset": asset, "entry_px": t.get("entry_px"),
                                   "contracts": t.get("contracts"),
                                   "pnl_estimate": pnl, "reason": "close_market"})
                    _atomic_dump(LAB_LEDGER_FILE, ledger)
                continue
            new_sl = sl_target
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
                # G8：巡检/换腿统一走 helper（缺腿补挂 + 到期前换腿），见函数文档
                _inspect_protection_legs(ad, asset, t, actions, now_ts or int(time.time()),
                                         handled=handled_assets)
            continue

        if act in ("BUY_LONG", "SELL_SHORT") and conf >= float(pool["min_confidence"]):
            if t:
                # 已有另一模式的 tracker（如 live 仓位遇到 dry 轮）：绝不越权接管/覆盖
                actions.append(f"[GateLab] {asset} 已有 {t.get('mode')} 试验田仓位，{mode} 轮跳过")
                continue
            if not dry and not entry_allowed:
                actions.append(f"[GateLab] {asset} 启动体检未通过，本轮禁开新仓")
                continue
            if open_count >= int(pool["max_open"]):
                actions.append(f"[GateLab] {asset} 试验田满仓({open_count}/{pool['max_open']})，跳过")
                continue
            # US-005：跨所同向敞口合并（OKX 在管 + lab 在途 + 本单计划），超限拒开；
            # 只拒开新仓不自动平旧仓（自动平仓属主链风控职权，试验田无此权限）
            side_want = "long" if act == "BUY_LONG" else "short"
            try:
                plan_lev = float(dec.get("leverage") or 3) or 3.0
            except (TypeError, ValueError):
                plan_lev = 3.0
            plan_margin = min(float(dec.get("margin_usdt") or pool["margin_per_trade_usdt"]),
                              float(pool["margin_per_trade_usdt"]))
            plan_notional = round(plan_margin * plan_lev, 2)
            okx_notional, okx_ok = _okx_notional_for(asset, side_want)
            lab_notional = _lab_same_side_notional(trackers, asset, side_want)
            cap = _exposure_cap()
            if not okx_ok and not dry:
                actions.append(f"[GateLab][EXPOSURE] {asset} OKX 敞口源查询失败，"
                               f"live fail-closed 拒开（dry 不受阻）")
                continue
            exposure_total = round((okx_notional if okx_ok else 0.0) + lab_notional + plan_notional, 2)
            if exposure_total > cap:
                actions.append(
                    f"[GateLab][EXPOSURE] {asset} 跨所同向敞超限拒开: "
                    f"OKX同向={okx_notional if okx_ok else '查询失败按0'} + "
                    f"lab同向={lab_notional} + 本单={plan_notional} "
                    f"= {exposure_total} > cap {cap}")
                continue
            r = plan_entry(dry, asset, dec, pool, ad, own_position=t, margin_mode=margin_mode)
            if r.get("ok"):
                trackers[asset] = {
                    "mode": "dry" if dry else "live", "venue": "gate", "asset": asset,
                    "side": "long" if act == "BUY_LONG" else "short",
                    "contracts": r.get("contracts"), "size_signed": r.get("size_signed"),
                    "entry_px": float(dec.get("entry_price") or 0),
                    "tp_px": float(dec.get("take_profit_price") or 0),
                    "sl_px": float(dec.get("stop_loss_price") or 0),
                    "order_id": r.get("order_id"), "tp_id": r.get("tp_id"), "sl_id": r.get("sl_id"),
                    "attached_ts": now_ts or int(time.time()),   # G8：换腿计时起点
                    "margin_usdt": r.get("margin_usdt"), "leverage": float(dec.get("leverage") or 0),
                    "entry_ts": now_ts or int(time.time()), "rr": r.get("rr"),
                }
                open_count += 1
                actions.append(f"[GateLab] {asset} 开仓{'演算' if dry else ''}: {r.get('detail', '')[:140]}")
            else:
                actions.append(f"[GateLab] {asset} 开仓被拒[{r.get('stage')}]: {r.get('detail', '')[:120]}")

    # ⑤ G8：保护巡检无条件跑——决策断档的 live 仓（本轮无决策/不新鲜）同样
    # 巡检 + 换腿，杜绝「只在有决策的周期才被巡检」留下的保护空窗。
    if not dry:
        for asset, t in sorted(trackers.items()):
            if asset not in handled_assets and t.get("mode") == "live":
                _inspect_protection_legs(ad, asset, t, actions, now_ts or int(time.time()),
                                         handled=handled_assets)

    _atomic_dump(LAB_TRACKER_FILE, trackers)
    return actions


def main():
    mode = effective_mode()
    ts = datetime.now(_BJ).isoformat(sep=" ", timespec="seconds")
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

"""持仓分批平仓止盈执行引擎（Scale-Out Engine）。

职责：
当持仓浮盈达到设定的门槛（如 1.2x ATR）时，自动市价平仓指定比例（如 50%）锁定现金利润，
同时原子级撤销旧的全量云端 OCO 保护单，换挂剩余仓位的新 OCO 保护单，并将止损价推进至
开仓成本保本位（entry_px + 0.25%），且对当前持仓生命周期加锁互斥金字塔加仓。

防御机制：
1. 最小合约张数与精度防御（pos_sz < 2*minSz 时优雅降级为整仓追踪止盈）；
2. 原生 reduceOnly 市价平仓委托（交易所底层物理杜绝反向开仓）；
3. 云端 OCO 覆盖单超额撤销重置（彻底消除原 100% 止损单残留导致的反向开仓）；
4. 金字塔顺势加仓单向锁（scale_count = 999，互斥锁定，防止边平边加）；
5. 幂等性守卫（scale_out_phase = 1，单次持仓生命周期内仅执行一次）。
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from scripts.risk_constants import (
        SCALE_OUT_ENABLED,
        SCALE_OUT_RATIO,
        SCALE_OUT_TRIGGER_ATR,
    )
except Exception:
    SCALE_OUT_ENABLED = True
    SCALE_OUT_RATIO = 0.50
    SCALE_OUT_TRIGGER_ATR = 1.20


def execute_scale_out_if_eligible(
    f: Dict[str, Any],
    curr_pos: Dict[str, Any],
    trackers: Dict[str, Any],
    timestamp_full: str,
    executed_actions: List[str],
    *,
    okx_rest=None,
    venue_registry=None,
    record_trade=None,
    notify_trade_close=None,
    close_fee=None,
    close_trade_payload=None,
    TAKER_FEE_RATE: float = 0.0005,
    ensure_cloud_position_protection=None,
) -> Tuple[bool, str]:
    """若当前持仓达到分批止盈条件，执行半仓市价平仓、保本移损与云端 OCO 重置。"""
    if not SCALE_OUT_ENABLED:
        return False, "分批止盈未启用"

    if not f or not curr_pos or not f.get("market_data_valid"):
        return False, "行情数据不完整"

    inst_id = str(f.get("instId", ""))
    name = str(f.get("name", inst_id.split("-")[0]))
    cur_px = float(f.get("price", 0.0) or 0.0)
    atr = max(float(f.get("atr", 0.0) or 0.0), cur_px * 0.005)
    prec = int(f.get("precision", 2) or 2)
    ct_val = float(f.get("ctVal", 1.0) or 1.0)
    min_sz = float(f.get("minSz", 0.01) or 0.01)

    pos_sz = abs(float(curr_pos.get("pos", 0.0) or 0.0))
    if pos_sz <= 0:
        return False, "无持仓"

    is_long = "long" in str(curr_pos.get("side", "")).lower()
    pos_side = "long" if is_long else "short"
    entry_px = float(curr_pos.get("avgPx", 0.0) or 0.0)
    if entry_px <= 0:
        return False, "持仓均价无效"

    pos_key = f"{inst_id}_{curr_pos.get('side', pos_side)}"
    if pos_key not in trackers:
        return False, "未找到持仓跟踪器"

    t = trackers[pos_key]

    # 1. 幂等性守卫：若已执行过分批止盈，跳过
    if int(t.get("scale_out_phase", 0) or 0) >= 1:
        return False, "已执行过分批平仓"

    # 2. 计算首批止盈目标价位（TP1）并持久化到跟踪器
    trigger_threshold = float(SCALE_OUT_TRIGGER_ATR or 1.20) * atr
    tp1_px = (entry_px + trigger_threshold) if is_long else (entry_px - trigger_threshold)
    px_prec = int(f.get("precision", 2) or 2)
    tp1_px = round(tp1_px, px_prec)
    t["scale_out_tp"] = tp1_px

    # 3. 浮盈判定
    cur_profit_px = (cur_px - entry_px) if is_long else (entry_px - cur_px)
    if cur_profit_px < trigger_threshold:
        current_desc = str(t.get("stage_desc") or "")
        if not current_desc or "监控中" in current_desc or "TP1" in current_desc:
            gain_pct = abs(tp1_px - entry_px) / entry_px * 100 if entry_px > 0 else 0.0
            t["stage_desc"] = f"持有中 (首批止盈目标 TP1: {tp1_px:g} · +{gain_pct:.1f}% · 达标平50%保本)"
        return False, "浮盈未达分批止盈门槛"

    # 4. 精度与最小张数防御
    # 若总持仓不足 2 倍 minSz，无法安全切分为两半，优雅降级
    if pos_sz < (2.0 * min_sz - 1e-12):
        msg = f"[{name}] 持仓张数 {pos_sz:g} 低于分批切分下限 (2*minSz={2*min_sz:g})，自动降级为全仓追踪"
        executed_actions.append(msg)
        t["scale_out_phase"] = -1  # 标记为已评估但不可切分，防止每轮重复提示
        return False, "张数不足以切分"

    ratio = max(0.1, min(0.9, float(SCALE_OUT_RATIO or 0.50)))
    raw_close_sz = pos_sz * ratio
    close_sz = round(raw_close_sz, prec)
    # 按 minSz 步长向下夹取对齐
    if min_sz > 0:
        close_sz = math.floor(close_sz / min_sz + 1e-9) * min_sz
        close_sz = round(close_sz, prec)

    remaining_sz = round(pos_sz - close_sz, prec)
    if close_sz < min_sz or remaining_sz < min_sz:
        msg = f"[{name}] 计算平仓切片 {close_sz:g} 或剩余张数 {remaining_sz:g} 低于最小精度 {min_sz:g}，降级全仓追踪"
        executed_actions.append(msg)
        t["scale_out_phase"] = -1
        return False, "切片张数不满足最小精度"

    # 4. 执行定向市价平仓（带有 reduceOnly=True）
    close_side = "sell" if is_long else "buy"
    pos_venue = str(curr_pos.get("venue") or curr_pos.get("exchange") or "okx").lower()
    
    order_success = False
    order_detail = ""

    if pos_venue == "okx":
        try:
            from scripts.okx_pos_mode import wire_pos_side as _wire_pos_side
            _wire = _wire_pos_side(pos_side, endpoint="order")
            res = okx_rest.place_order(
                inst_id,
                close_side,
                f"{close_sz:g}",
                pos_side=_wire,
                td_mode="cross",
                ord_type="market",
                reduce_only=True,
            )
            order_success = True
            order_detail = str(res)
        except Exception as exc:
            order_success = False
            order_detail = f"OKX分批平仓异常: {exc}"
    elif pos_venue == "binance":
        if venue_registry:
            try:
                ad = venue_registry.get_adapter(pos_venue)
                symbol_native = inst_id.split("-")[0]
                raw_pos = curr_pos.get("raw") if isinstance(curr_pos.get("raw"), dict) else {}
                ps = str(curr_pos.get("positionSide") or raw_pos.get("positionSide") or "").upper()
                if ps in ("LONG", "SHORT"):
                    res = ad.place_order(symbol_native, close_side, close_sz, position_side=ps)
                else:
                    res = ad.place_order(symbol_native, close_side, close_sz, reduce_only=True)
                order_success = True
                order_detail = str(res)
            except Exception as exc:
                order_success = False
                order_detail = f"BINANCE分批平仓异常: {exc}"
        else:
            order_success = False
            order_detail = "未提供 BINANCE 适配器注册表"
    elif pos_venue == "gate":
        if venue_registry:
            try:
                ad = venue_registry.get_adapter(pos_venue)
                symbol_native = inst_id.split("-")[0]
                res = ad.place_order(symbol_native, close_side, close_sz, reduce_only=True)
                order_success = True
                order_detail = str(res)
            except Exception as exc:
                order_success = False
                order_detail = f"GATE分批平仓异常: {exc}"
        else:
            order_success = False
            order_detail = "未提供 GATE 适配器注册表"
    else:
        # 多所适配器路径兜底
        if venue_registry:
            try:
                ad = venue_registry.get_adapter(pos_venue)
                symbol_native = inst_id.split("-")[0]
                res = ad.place_order(symbol_native, close_side, close_sz, reduce_only=True)
                order_success = True
                order_detail = str(res)
            except Exception as exc:
                order_success = False
                order_detail = f"{pos_venue.upper()}分批平仓异常: {exc}"
        else:
            order_success = False
            order_detail = f"未提供 {pos_venue.upper()} 适配器注册表"

    if not order_success:
        executed_actions.append(f"[{name}] ⚠️ 分批止盈市价平仓提交失败: {order_detail}")
        return False, "平仓提交失败"

    # 5. 原子级撤销旧 OCO / 保护单，避免超额单量穿仓反向开单与旧止损残留
    if pos_venue == "okx":
        try:
            pending_algos = okx_rest.pending_algo_orders(inst_id)
            old_algo_ids = [
                str(o.get("algoId") or "")
                for o in pending_algos
                if str(o.get("posSide", "net")).lower() in (pos_side, "net")
                and (o.get("algoId") or "")
            ]
            if old_algo_ids:
                okx_rest.cancel_algo_orders(old_algo_ids[:10], inst_id=inst_id)
        except Exception as cxl_exc:
            print(f"[Scale-Out] 清理 {inst_id} 旧OCO异常（由新保护单覆盖）: {cxl_exc}")
    elif pos_venue in ("binance", "gate") and venue_registry:
        try:
            adapter = venue_registry.get_adapter(pos_venue)
            if adapter and hasattr(adapter, "cancel_protective_orders"):
                adapter.cancel_protective_orders(name)
            elif adapter and hasattr(adapter, "cancel_all_algo_open_orders"):
                adapter.cancel_all_algo_open_orders(symbol=adapter.native_symbol(name))
        except Exception as cxl_exc:
            print(f"[Scale-Out] 清理 {pos_venue.upper()} {name} 旧保护单异常: {cxl_exc}")

    # 6. 计算保本止损线并为剩余仓位重建云端 OCO
    breakeven_cushion = 0.0025 * entry_px
    breakeven_sl = round((entry_px + breakeven_cushion) if is_long else (entry_px - breakeven_cushion), prec)
    take_profit_px = float(t.get("takeProfitPx", 0.0) or 0.0)

    if pos_venue == "okx" and ensure_cloud_position_protection and take_profit_px > 0:
        try:
            ensure_cloud_position_protection(
                inst_id, pos_side, remaining_sz, take_profit_px, breakeven_sl
            )
        except Exception as oco_exc:
            print(f"[Scale-Out] 剩余仓位云端保护更新异常: {oco_exc}")
    elif pos_venue in ("binance", "gate") and venue_registry:
        try:
            adapter = venue_registry.get_adapter(pos_venue)
            if adapter and hasattr(adapter, "attach_protective_orders"):
                adapter.attach_protective_orders(
                    name, pos_side, tp_px=take_profit_px if take_profit_px > 0 else None,
                    sl_px=breakeven_sl, contracts=remaining_sz
                )
        except Exception as oco_exc:
            print(f"[Scale-Out] 外所 {pos_venue.upper()} 剩余仓位云端保护更新异常: {oco_exc}")

    # 7. 更新本地状态机与账本
    t["scale_out_phase"] = 1
    t["scale_out_tp"] = None
    t["currentSz"] = remaining_sz
    t["trailingStopPx"] = breakeven_sl
    t["scale_count"] = 999  # 永久互斥锁定金字塔加仓
    t["stage_desc"] = f"已分批止盈50% (余{remaining_sz:g}张 · 保本止损 {breakeven_sl})"

    # 8. 记录平仓台账与通知
    realized_pnl = close_sz * ct_val * (cur_px - entry_px if is_long else entry_px - cur_px)
    fee_val = close_fee(close_sz, ct_val, cur_px, TAKER_FEE_RATE) if close_fee else (close_sz * ct_val * cur_px * TAKER_FEE_RATE)

    msg_action = f"[{name}] 🎯 达到首批止盈门槛(+{trigger_threshold:.2f})，已市价平仓 {ratio*100:.0f}% ({close_sz:g}张)，锁定盈利 +{realized_pnl:.2f}U；余 {remaining_sz:g} 张推进至保本位 {breakeven_sl}"
    executed_actions.append(msg_action)

    if record_trade and close_trade_payload:
        record_trade(close_trade_payload(
            is_long=is_long,
            timestamp_full=timestamp_full,
            name=name,
            action_type="分批止盈",
            side_suffix="首批平仓50%",
            pos_sz=close_sz,
            cur_px=cur_px,
            fee=fee_val,
            pnl=realized_pnl,
            remark=f"浮盈达到 {trigger_threshold:.2f} 触发首批止盈，锁定现金利润，余仓移损保本",
        ))

    if notify_trade_close:
        try:
            notify_trade_close(
                inst=name,
                pnl=realized_pnl,
                stage="首批分批止盈50%",
                exit_px=cur_px,
                side="多" if is_long else "空",
                entry_px=entry_px,
                fee=fee_val,
                venue=pos_venue,
                is_partial=True,
            )
        except Exception:
            pass

    return True, "首批分批平仓成功"

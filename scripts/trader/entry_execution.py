"""开仓扫描执行（execute_portfolio 相位 4 的入场循环）（B3 抽取·第九十刀）。

从 `scripts/ai_factor_trader.py::execute_portfolio`（551 行巨函数）中
**纯搬家**相位 4 的入场 `for` 循环（300 行）：

```
for f in all_factors:      # ← 搬走的就是这个循环
    ...
```

## 为什么这次抽取是安全的（抽取前实测的三条判据）

1. 循环内 **0 个 `return`、0 个 `break`**（仅 4 个 `continue`，随之迁移语义不变）
   —— 若存在 return/break，搬出函数边界会改变控制流；
2. 循环携带量只有 3 个计数器（`reserved_long/short/slot_count` 的 `+=`），
   且它们**在循环之后不再被读** ⇒ 作为入参传入即可，无需回传；
3. 其余 9 个外围局部量在循环内**只读或原地变更**（list/set/dict 的 append/add/赋值）
   —— 容器原地变更不入参也生效，但为显式起见一律入参。

## 参数（12 个外围局部量 + 29 个门面全局）

全部**同名传参**（门面调用点写 `name=name`）⇒ 循环体**一字未改**；
对拍门直接比较 `for` 语句的 AST，**不需要任何归一规则**。
`ASSET_MARGIN_CAP` 是 `execute_portfolio` 相位 1 算出的自适应上限，同样入参。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple


def execute_entry_scan(*,
        all_factors,
        brain_cache,
        cb_active,
        entries_blocked,
        executed_actions,
        pending_inst_ids,
        trackers,
        usdt_available,
        ASSET_MARGIN_CAP,
        reserved_long_count,
        reserved_short_count,
        reserved_slot_count,
        ASSET_CLASS_PROFILES,
        MAX_CONCURRENT_POSITIONS,
        MAX_LEVERAGE,
        MAX_SAME_DIRECTION_POSITIONS,
        MAX_SCALE_IN_COUNT,
        MIN_ENTRY_CONFIDENCE,
        MIN_LEVERAGE,
        MIN_SCALE_IN_CONFIDENCE,
        MIN_SCALE_IN_PROFIT_RATIO,
        build_order_intent,
        clamp_ai_leverage,
        entry_action_message,
        entry_failure_message,
        equity_margin_cap,
        evaluate_asset_signal,
        instrument_profile,
        is_tradfi_market_liquid,
        load_adaptive_config,
        max_size_within_margin,
        normalize_bracket_prices,
        notify_trade_open,
        order_margin_gate,
        pyramiding_gate,
        quantize_size,
        resolve_entry_prices,
        save_trackers,
        size_for_decision,
        submit_protected_limit_order,
        trade_open_kwargs):
    for f in all_factors:
        asset_type = f.get("type", "crypto")
        if not is_tradfi_market_liquid(asset_type):
            continue

        score, action, reasons, strat_tag, strat_desc = evaluate_asset_signal(f)
        inst_id = f["instId"]
        curr_pos = f["position"]
        prec = f["precision"]
        ct_val = f["ctVal"]
        profile = ASSET_CLASS_PROFILES.get(asset_type, ASSET_CLASS_PROFILES["crypto"])

        adaptive_cfg = load_adaptive_config()
        # P2-5：池条目 per-instrument 参数优先（旧实现只认资产类别硬编码）
        _inst_profile = instrument_profile(f, asset_type)
        tp_mult = adaptive_cfg.get("tp_atr_mult", _inst_profile.get("tp_atr_mult", 2.2))
        sl_mult = adaptive_cfg.get("sl_atr_mult", _inst_profile.get("sl_atr_mult", 1.3))

        atr = max(f["atr"], f["price"] * 0.005)
        min_prof = adaptive_cfg.get("min_profit_ratio", profile.get("min_profit_ratio", 0.008))
        tp_dist = max(atr * tp_mult, f["price"] * min_prof)
        sl_dist = atr * sl_mult

        # Gate 1: LLM AI Brain Full Execution Authority
        # When AI Brain is active, AI Brain is the SOLE decider for action, leverage, margin, and TP/SL.
        ai_info = brain_cache.get(inst_id) if isinstance(brain_cache, dict) else None
        if not ai_info or "decision" not in ai_info:
            print(f"[AI Brain 全权拦截] {f['name']} 本轮无有效新鲜 AI 决策，禁止开仓")
            continue

        ai_decision = ai_info["decision"]
        ai_act = str(ai_decision.get("action", "WAIT")).upper()
        ai_conf = float(ai_decision.get("confidence", 0) or 0)
        ai_reason = ai_decision.get("summary_reason", "")

        f["ai_thought"] = ai_info.get("thought_process", {})
        f["ai_reason"] = ai_reason
        f["ai_confidence"] = ai_conf
        f["policy_version"] = ai_info.get("policy_version", "")
        f["policy_hash"] = ai_info.get("policy_hash", "")

        # Direct Action Assignment from LLM
        if ai_act in ["BUY_LONG", "SELL_SHORT"]:
            action = ai_act
            strat_tag = f"🧠 AI大脑({ai_act})"
            strat_desc = f"【AI全权决策】{ai_reason}"
            print(f"[AI Brain 全权指令] {f['name']} AI 直接指示 {action} (置信度={ai_conf}%, 理由: {ai_reason})")
        else:
            action = "HOLD"
            strat_tag = "🤖 AI观望"
            strat_desc = f"AI大脑判定当前无高确定性机会({ai_reason})"
            continue

        # Dynamic Equal-Risk Position Size with AI Custom Margin Allocation
        actual_sz = f["sz"]
        ai_margin = float(ai_decision.get("margin_usdt", 0.0) or 0.0)
        ai_lever = float(ai_decision.get("leverage", 3) or 3)
        # 杠杆硬钳制：无论 AI 裁决多激进，执行层都夹在后台风控页配置的区间内
        # （审计 P2-8：旧实现下限写死 1.0，风控页的 MIN_LEVERAGE 在单所路径不成立）
        # 审计 P2-5：池条目的 per-instrument max_leverage（tier 派生 3x/5x）此前无人读；
        # 现在它是该标的的硬上限（与全局上限取更严者），并透传给多所路由。
        # 具体夹取顺序见 scripts/trader/leverage.py。
        try:
            _inst_lever_cap = float(f.get("max_leverage") or 0.0)
        except (TypeError, ValueError):
            _inst_lever_cap = 0.0
        _ai_lever_raw = ai_lever
        ai_lever, _lever_tightened = clamp_ai_leverage(
            ai_lever, min_leverage=MIN_LEVERAGE, max_leverage=MAX_LEVERAGE,
            inst_lever_cap=_inst_lever_cap)
        if _lever_tightened:
            print(f"[杠杆闸门] {f['name']} 池内单标的杠杆上限 {_inst_lever_cap:g}x < 全局 {_ai_lever_raw:g}x，已按池值收紧")
        if abs(ai_lever - float(ai_decision.get("leverage", 3) or 3)) > 1e-9:
            print(f"[杠杆闸门] {f['name']} AI 裁决杠杆 {ai_decision.get('leverage')}x "
                  f"超出配置区间 [{float(MIN_LEVERAGE or 0):g}x, {float(MAX_LEVERAGE or 0):g}x]，"
                  f"已夹至 {ai_lever:g}x")
        step_sz = float(f.get("minSz", 1) or 1)

        # If AI planned margin & leverage, calculate custom contract size
        # （四道钳制的顺序见 scripts/trader/sizing.py —— 顺序换了会放大仓位）
        actual_sz = size_for_decision(
            ai_margin=ai_margin, ai_lever=ai_lever, price=f["price"],
            ct_val=ct_val, step_sz=step_sz, base_sz=f["sz"],
            usdt_available=usdt_available, actual_sz=actual_sz,
            quantize_size=quantize_size,
            max_size_within_margin=max_size_within_margin)

        if actual_sz <= 0:
            if f.get("size_below_exchange_min") or ai_margin > 0:
                print(f"[仓位跳过] {f['name']} 按风险预算推导的数量低于交易所最小下单量 {step_sz} 张"
                      f"(可用余额 {usdt_available}, 单笔风险额 {f['risk_per_trade_usd']}U)，本周期不交易该标的")
            continue

        # Long Execution (Initial Entry or Strict Pyramiding Scale-In)
        if action == "BUY_LONG":
            is_scale_in = False
            allow_entry = False

            # Case A: Standard Initial Entry (No existing position & slot available)
            if not curr_pos and inst_id not in pending_inst_ids and reserved_slot_count < MAX_CONCURRENT_POSITIONS and reserved_long_count < MAX_SAME_DIRECTION_POSITIONS:
                if ai_conf >= MIN_ENTRY_CONFIDENCE:
                    allow_entry = True
                else:
                    print(f"[首发开多拦截] {f['name']} AI置信度 {ai_conf:.1f}% 未达 80% 门禁，宁缺毋滥，拦截入场")

            # Case B: Strict Pyramiding Scale-In (Existing long position in profit/breakeven)
            elif curr_pos and str(curr_pos.get("side", "")).lower() == "long" and inst_id not in pending_inst_ids:
                pos_upl = float(curr_pos.get("upl", 0.0) or 0.0)
                pos_upl_ratio = float(curr_pos.get("uplRatio", 0.0) or 0.0)
                pos_avg_px = float(curr_pos.get("avgPx", 0.0) or 0.0)
                curr_margin = float(curr_pos.get("margin", 0.0) or 0.0)
                tracker = trackers.get(f"{inst_id}_long", {})
                # ⚠️ 第一百三十八刀（用户拍板 fail-closed）：**追踪器拿不到该仓 ⇒ 视同已达上限**。
                # 读失败时 `load_trackers()` 返回标记型空字典 ⇒ 此处必然命中。
                # 为什么必须这样：`pyramiding_gate` 的「每仓最多加仓 N 次」判据是
                # `scale_count < max`，缺省 0 会让上限**静默失效**（可反复加仓、过度集中）。
                # ⚠️ 诚实说明：下一条 gate 文案会显示"已达最大加仓次数 (N/N)" ——
                # 那是**保守假设**（真实情况是"未知"），本行先把这层说清楚。
                if not tracker:
                    print(f"[Pyramiding] {f['name']} 追踪器缺失 ⇒ 无法核验已加仓次数，"
                          "按 fail-closed 视同已达上限（宁可不加，不可无限加）")
                scale_count = (int(tracker.get("scale_count", 0)) if tracker
                               else MAX_SCALE_IN_COUNT)
                trailing_sl = float(tracker.get("trailingStopPx", 0.0) or 0.0)

                # Ironclad Pyramiding Rules:
                # 1. Base position must be in profit (ROI >= +0.8%) OR stop-loss already moved to/above avg entry px (No-risk trade).
                # 2. Maximum 1 scale-in per position to prevent overconcentration.
                # 3. Combined margin must not exceed MAX_SINGLE_ASSET_MARGIN.
                # 4. AI Confidence must be >= 75%.
                # 5. Calculus Momentum & Probability Gateway: Acceleration a >= -0.25 and Continuation Prob >= 40%
                c_dyn = f.get("calculus", {})
                c_accel = float(c_dyn.get("acceleration", 0.0) or 0.0)
                p_th = c_dyn.get("probability_theory", {})
                p_cont = float(p_th.get("continuation_prob_pct", 50.0) or 50.0)
                calculus_accel_ok = (c_accel >= -0.25 and p_cont >= 40.0)

                allow_entry, is_scale_in = pyramiding_gate(
                    is_long=True, f=f, pos_upl=pos_upl, pos_upl_ratio=pos_upl_ratio,
                    pos_avg_px=pos_avg_px, curr_margin=curr_margin, trailing_sl=trailing_sl,
                    scale_count=scale_count, c_accel=c_accel, p_th=p_th,
                    ai_margin=ai_margin, actual_sz=actual_sz, ct_val=ct_val,
                    ai_lever=ai_lever, ai_conf=ai_conf,
                    min_scale_in_profit_ratio=MIN_SCALE_IN_PROFIT_RATIO,
                    max_scale_in_count=MAX_SCALE_IN_COUNT,
                    min_scale_in_confidence=MIN_SCALE_IN_CONFIDENCE,
                    asset_margin_cap=ASSET_MARGIN_CAP)

            if allow_entry and entries_blocked:
                print(f"[挂单对账] fail-closed 拦截 {f['name']} 新增多单下单（本周期对账失败）")
                allow_entry = False
            if allow_entry:
                limit_px, tp_px, sl_px = resolve_entry_prices(
                    is_long=True, ai_decision=ai_decision, f=f, prec=prec,
                    tp_dist=tp_dist, sl_dist=sl_dist)

                # Hard check: 做多须 sl_px < limit_px < tp_px（钳制见 scripts/trader/brackets.py）
                sl_px, tp_px = normalize_bracket_prices(
                    is_long=True, limit_px=limit_px, tp_px=tp_px, sl_px=sl_px,
                    sl_dist=sl_dist, tp_dist=tp_dist, price=f["price"], prec=prec)

                # US-003 决策面上下文：名义额（选所硬筛/深度需求）+ 保证金估算
                # （预算预留额）+ 意图号（同一条 AI 决策重投幂等，不重复占预算）
                # 审计 P0-1：多所路径保证金与 OKX 同尺（AI 计划额 ∩ 张数隐含额 ∩ 权益占比 ∩ 单标的封顶）
                _order_margin = order_margin_gate(
                    ai_margin, size=actual_sz, price=limit_px, ct_val=ct_val,
                    leverage=ai_lever, usdt_available=usdt_available)
                _side, _pos_side, _venue_ctx = build_order_intent(
                    is_long=True, inst_id=inst_id, actual_sz=actual_sz, ct_val=ct_val,
                    limit_px=limit_px, ai_lever=ai_lever,
                    margin_usdt=_order_margin,
                    max_margin_usdt=equity_margin_cap(usdt_available),
                    inst_lever_cap=_inst_lever_cap, ai_conf=ai_conf, ai_info=ai_info)
                accepted, order_ref = submit_protected_limit_order(
                    inst_id, _side, _pos_side, actual_sz, limit_px, tp_px, sl_px,
                    venue_ctx=_venue_ctx)
                if accepted:
                    if is_scale_in:
                        tracker = trackers.get(f"{inst_id}_long", {})
                        tracker["scale_count"] = tracker.get("scale_count", 0) + 1
                        save_trackers(trackers)
                        executed_actions.append(entry_action_message(
                            is_long=True, is_scale_in=True, name=f["name"], sz=actual_sz,
                            px=limit_px, order_ref=order_ref, tp_px=tp_px, sl_px=sl_px))
                        if notify_trade_open:
                            notify_trade_open(
                                **trade_open_kwargs(
                                    is_long=True, is_scale_in=True, name=f["name"], sz=actual_sz,
                                    px=limit_px, strat_tag=strat_tag, ai_reason=ai_reason,
                                    tp_px=tp_px, sl_px=sl_px),
                                leverage=int(ai_lever),  # 审计D(2026-09-13)：曾恒写 3——5x 仓实开也通知「3x 杠杆」，票圈谎报
                            )
                    else:
                        executed_actions.append(entry_action_message(
                            is_long=True, is_scale_in=False, name=f["name"], sz=actual_sz,
                            px=limit_px, order_ref=order_ref, tp_px=tp_px, sl_px=sl_px))
                        pending_inst_ids.add(inst_id)
                        reserved_slot_count += 1
                        reserved_long_count += 1
                        if notify_trade_open:
                            notify_trade_open(
                                **trade_open_kwargs(
                                    is_long=True, is_scale_in=False, name=f["name"], sz=actual_sz,
                                    px=limit_px, strat_tag=strat_tag, ai_reason=ai_reason,
                                    tp_px=tp_px, sl_px=sl_px),
                                leverage=int(ai_lever),  # 审计D(2026-09-13)：曾恒写 3——5x 仓实开也通知「3x 杠杆」，票圈谎报
                            )
                else:
                    executed_actions.append(entry_failure_message(
                        is_long=True, name=f["name"], order_ref=order_ref))

        # Short Execution (Initial Entry or Strict Pyramiding Scale-In)
        elif action == "SELL_SHORT":
            is_scale_in = False
            allow_entry = False

            # Case A: Standard Initial Entry
            if not curr_pos and inst_id not in pending_inst_ids and reserved_slot_count < MAX_CONCURRENT_POSITIONS and reserved_short_count < MAX_SAME_DIRECTION_POSITIONS:
                if ai_conf >= MIN_ENTRY_CONFIDENCE:
                    allow_entry = True
                else:
                    print(f"[首发开空拦截] {f['name']} AI置信度 {ai_conf:.1f}% 未达 80% 门禁，宁缺毋滥，拦截入场")

            # Case B: Strict Pyramiding Scale-In (Existing short position in profit/breakeven)
            elif curr_pos and str(curr_pos.get("side", "")).lower() == "short" and inst_id not in pending_inst_ids:
                pos_upl = float(curr_pos.get("upl", 0.0) or 0.0)
                pos_upl_ratio = float(curr_pos.get("uplRatio", 0.0) or 0.0)
                pos_avg_px = float(curr_pos.get("avgPx", 0.0) or 0.0)
                curr_margin = float(curr_pos.get("margin", 0.0) or 0.0)
                tracker = trackers.get(f"{inst_id}_short", {})
                # ⚠️ 第一百三十八刀（用户拍板 fail-closed）：**追踪器拿不到该仓 ⇒ 视同已达上限**。
                # 读失败时 `load_trackers()` 返回标记型空字典 ⇒ 此处必然命中。
                # 为什么必须这样：`pyramiding_gate` 的「每仓最多加仓 N 次」判据是
                # `scale_count < max`，缺省 0 会让上限**静默失效**（可反复加仓、过度集中）。
                # ⚠️ 诚实说明：下一条 gate 文案会显示"已达最大加仓次数 (N/N)" ——
                # 那是**保守假设**（真实情况是"未知"），本行先把这层说清楚。
                if not tracker:
                    print(f"[Pyramiding] {f['name']} 追踪器缺失 ⇒ 无法核验已加仓次数，"
                          "按 fail-closed 视同已达上限（宁可不加，不可无限加）")
                scale_count = (int(tracker.get("scale_count", 0)) if tracker
                               else MAX_SCALE_IN_COUNT)
                trailing_sl = float(tracker.get("trailingStopPx", 0.0) or 0.0)

                c_dyn = f.get("calculus", {})
                c_accel = float(c_dyn.get("acceleration", 0.0) or 0.0)
                p_th = c_dyn.get("probability_theory", {})

                allow_entry, is_scale_in = pyramiding_gate(
                    is_long=False, f=f, pos_upl=pos_upl, pos_upl_ratio=pos_upl_ratio,
                    pos_avg_px=pos_avg_px, curr_margin=curr_margin, trailing_sl=trailing_sl,
                    scale_count=scale_count, c_accel=c_accel, p_th=p_th,
                    ai_margin=ai_margin, actual_sz=actual_sz, ct_val=ct_val,
                    ai_lever=ai_lever, ai_conf=ai_conf,
                    min_scale_in_profit_ratio=MIN_SCALE_IN_PROFIT_RATIO,
                    max_scale_in_count=MAX_SCALE_IN_COUNT,
                    min_scale_in_confidence=MIN_SCALE_IN_CONFIDENCE,
                    asset_margin_cap=ASSET_MARGIN_CAP)

            if allow_entry and entries_blocked:
                print(f"[挂单对账] fail-closed 拦截 {f['name']} 新增空单下单（本周期对账失败）")
                allow_entry = False
            if allow_entry:
                limit_px, tp_px, sl_px = resolve_entry_prices(
                    is_long=False, ai_decision=ai_decision, f=f, prec=prec,
                    tp_dist=tp_dist, sl_dist=sl_dist)

                # Hard check: 做空须 tp_px < limit_px < sl_px（钳制见 scripts/trader/brackets.py）
                sl_px, tp_px = normalize_bracket_prices(
                    is_long=False, limit_px=limit_px, tp_px=tp_px, sl_px=sl_px,
                    sl_dist=sl_dist, tp_dist=tp_dist, price=f["price"], prec=prec)

                # US-003 决策面上下文：名义额（选所硬筛/深度需求）+ 保证金估算
                # （预算预留额）+ 意图号（同一条 AI 决策重投幂等，不重复占预算）
                # 审计 P0-1：多所路径保证金与 OKX 同尺（AI 计划额 ∩ 张数隐含额 ∩ 权益占比 ∩ 单标的封顶）
                _order_margin = order_margin_gate(
                    ai_margin, size=actual_sz, price=limit_px, ct_val=ct_val,
                    leverage=ai_lever, usdt_available=usdt_available)
                _side, _pos_side, _venue_ctx = build_order_intent(
                    is_long=False, inst_id=inst_id, actual_sz=actual_sz, ct_val=ct_val,
                    limit_px=limit_px, ai_lever=ai_lever,
                    margin_usdt=_order_margin,
                    max_margin_usdt=equity_margin_cap(usdt_available),
                    inst_lever_cap=_inst_lever_cap, ai_conf=ai_conf, ai_info=ai_info)
                accepted, order_ref = submit_protected_limit_order(
                    inst_id, _side, _pos_side, actual_sz, limit_px, tp_px, sl_px,
                    venue_ctx=_venue_ctx)
                if accepted:
                    if is_scale_in:
                        tracker = trackers.get(f"{inst_id}_short", {})
                        tracker["scale_count"] = tracker.get("scale_count", 0) + 1
                        save_trackers(trackers)
                        executed_actions.append(entry_action_message(
                            is_long=False, is_scale_in=True, name=f["name"], sz=actual_sz,
                            px=limit_px, order_ref=order_ref, tp_px=tp_px, sl_px=sl_px))
                        if notify_trade_open:
                            notify_trade_open(
                                **trade_open_kwargs(
                                    is_long=False, is_scale_in=True, name=f["name"], sz=actual_sz,
                                    px=limit_px, strat_tag=strat_tag, ai_reason=ai_reason,
                                    tp_px=tp_px, sl_px=sl_px),
                                leverage=int(ai_lever),  # 审计D(2026-09-13)：曾恒写 3——5x 仓实开也通知「3x 杠杆」，票圈谎报
                            )
                    else:
                        executed_actions.append(entry_action_message(
                            is_long=False, is_scale_in=False, name=f["name"], sz=actual_sz,
                            px=limit_px, order_ref=order_ref, tp_px=tp_px, sl_px=sl_px))
                        pending_inst_ids.add(inst_id)
                        reserved_slot_count += 1
                        reserved_short_count += 1
                        if notify_trade_open:
                            notify_trade_open(
                                **trade_open_kwargs(
                                    is_long=False, is_scale_in=False, name=f["name"], sz=actual_sz,
                                    px=limit_px, strat_tag=strat_tag, ai_reason=ai_reason,
                                    tp_px=tp_px, sl_px=sl_px),
                                leverage=int(ai_lever),  # 审计D(2026-09-13)：曾恒写 3——5x 仓实开也通知「3x 杠杆」，票圈谎报
                            )
                else:
                    executed_actions.append(entry_failure_message(
                        is_long=False, name=f["name"], order_ref=order_ref))


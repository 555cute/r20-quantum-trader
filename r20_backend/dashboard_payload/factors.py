"""持仓追踪器、风险字段补全与因子雷达装配（结构优化阶段 2 / B2 第三刀）。

这些函数都要读被测试 patch/沙箱重定向的路径常量（POSITION_TRACKER_FILE /
FACTOR_LIBRARY_FILE / AI_DECISIONS_FILE / STATE_JSON_FILE），故按 B4/B5/B6 确立的接缝纪律：
**门面薄壳在调用时解析门面全局并注入，核心以参数接收** —— 直接重导出会让
`patch.object(dashboard, "STATE_JSON_FILE", tmp)` 静默失效并读到真实项目文件。
"""
from __future__ import annotations

import json

from r20_backend.dashboard_payload.readers import load_json_dict_disclosed
import os

from r20_backend.dashboard_payload.market import _safe_float
from scripts.instrument_pool import load_instruments

__all__ = ["load_position_trackers", "enrich_position_risk_fields",
           "_load_local_factor_library", "_build_factors_from_local_files"]


def load_position_trackers(tracker_file: str | os.PathLike[str]):
    """读持仓追踪（面板侧）。第 52 刀起委托 **共享披露读取器**（见 `readers`）。

    行为保持兼容（读不到仍返回 `{}`），但**不再静默**：失败会打一行 warn，
    让"没有数据"与"读不到"在日志里可区分（面板字段仍旧为空）。
    """
    return load_json_dict_disclosed(tracker_file)[0]


def enrich_position_risk_fields(tracker_file: str | os.PathLike[str], positions, trackers=None):
    """Add margin and stop-line fields even when OKX protection lookup is unavailable."""
    trackers = trackers if isinstance(trackers, dict) else load_position_trackers(tracker_file)
    contract_values = {item.get("instId"): _safe_float(item.get("ctVal"), 1.0) for item in load_instruments()}
    for position in positions or []:
        inst_id = str(position.get("instId") or "")
        side = str(position.get("posSide") or position.get("side") or "net").lower()
        position["posSide"] = side
        tracker = trackers.get(f"{inst_id}_{side}", {})
        size = abs(_safe_float(position.get("pos_sz", position.get("pos"))))
        price = _safe_float(position.get("markPx")) or _safe_float(position.get("avgPx"))
        notional = abs(_safe_float(position.get("notional_usdt"))) or round(size * contract_values.get(inst_id, 1.0) * price, 2)
        leverage = abs(_safe_float(position.get("lever"), 1.0)) or 1.0
        exchange_margin = abs(_safe_float(position.get("imr")))
        existing_margin = abs(_safe_float(position.get("margin_usdt")))
        if exchange_margin > 0:
            margin = exchange_margin
            margin_source = "exchange_imr"
        elif existing_margin > 0:
            margin = existing_margin
            margin_source = str(position.get("marginSource") or "cached")
        else:
            margin = round(notional / leverage, 2) if notional > 0 else 0.0
            margin_source = "notional_div_leverage"
        exchange_stop = _safe_float(position.get("exchangeSl"))
        tracker_stop = _safe_float(position.get("trailingSl")) or _safe_float(tracker.get("trailingStopPx"))
        exchange_tp = _safe_float(position.get("exchangeTp"))
        tracker_tp = _safe_float(tracker.get("takeProfitPx"))
        position.update({
            "pos_sz": size,
            "notional_usdt": round(notional, 2),
            "margin_usdt": round(margin, 2) if margin > 0 else None,
            "marginSource": margin_source,
            "trailingSl": tracker_stop or None,
            "displayStop": exchange_stop or tracker_stop or None,
            "stopSource": "exchange_cloud" if exchange_stop else ("local_tracker" if tracker_stop else "unavailable"),
            "displayTakeProfit": exchange_tp or tracker_tp or None,
            "stageDesc": position.get("stageDesc") or tracker.get("stage_desc") or "持有监控中",
            "strategyTag": position.get("strategyTag") or tracker.get("strategy_tag") or ("顺势做多" if "long" in side else "逢高做空"),
            # 第一百九十八刀：把 tracker 里**真实存在**的切分止盈状态接到行上。
            # 面板 `PositionsOrdersPanel.vue` 一直在读 `scaleOutPhase`/`scaleOutTp`
            # （`(p.scaleOutPhase ?? 0) >= 1` 决定徽标、`p.scaleOutTp` 决定 TP 显示），
            # 而这两个键**后端从未发过** ⇒ 徽标永远不亮、TP 永远走别的来源。
            # 数据就在 tracker 里（`scripts/trader/scale_out.py` 写 `scale_out_phase`/
            # `scale_out_tp`），只是没被接出来。**缺席即缺席**：tracker 没这项就不写这一项
            # （不写 0 —— 币安/Gate 行本来就没有 tracker，写成 0 等于替它们断言"未开始"）。
            **({"scaleOutPhase": int(float(tracker.get("scale_out_phase")))} if str(tracker.get("scale_out_phase", "")).strip() not in ("", "None") else {}),
            **({"scaleOutTp": float(tracker.get("scale_out_tp"))} if str(tracker.get("scale_out_tp", "")).strip() not in ("", "None") else {}),
            "cloudProtectionLastVerified": (tracker.get("cloudProtection") or {}).get("verifiedAt"),
            "cloudProtectionLastDetail": (tracker.get("cloudProtection") or {}).get("detail"),
        })
        if position.get("protectionStatus") in {None, "unknown_stale"} and position["cloudProtectionLastVerified"]:
            position["protectionStatus"] = "verification_stale"
    return positions


def _load_local_factor_library(factor_file: str | os.PathLike[str]):
    """Load factor_library_snapshot.json — a local file independent of OKX private API."""
    if os.path.exists(factor_file):
        try:
            with open(factor_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _build_factors_from_local_files(factor_file: str | os.PathLike[str], decisions_file: str | os.PathLike[str], state_file: str | os.PathLike[str], positions, timestamp_full):
    """Build factors_list from trading_state.json + ai_brain_decisions.json.

    These local files do not depend on OKX private endpoints, so they are
    available even when the dashboard is in STALE/OFFLINE degraded mode.
    """
    factors_list = []
    pos_map = {p.get("instId"): p for p in positions} if isinstance(positions, list) else {}
    state_data = {}
    ai_decisions = {}
    factor_lib_map = {}
    active_pool = load_instruments()

    if os.path.exists(factor_file):
        try:
            with open(factor_file, "r", encoding="utf-8") as f_lib:
                lib_data = json.load(f_lib)
                for item in lib_data.get("instruments", []):
                    if isinstance(item, dict) and item.get("instId"):
                        factor_lib_map[item["instId"]] = item
        except Exception:
            pass

    if os.path.exists(decisions_file):
        try:
            with open(decisions_file, "r", encoding="utf-8") as f:
                ai_decisions = json.load(f)
        except Exception:
            pass

    inst_map = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state_data = json.load(f)
                for ins in state_data.get("instruments", []):
                    if isinstance(ins, dict) and ins.get("instId"):
                        inst_map[ins["instId"]] = ins
        except Exception:
            pass

    for target in active_pool:
        inst_id = target.get("instId")
        ins = inst_map.get(inst_id) or {}
        lib_item = factor_lib_map.get(inst_id) or {}
        ai_info = ai_decisions.get(inst_id, {})
        ai_dec = ai_info.get("decision", {})
        ai_thought = ai_info.get("thought_process", {})
        action_val = ai_dec.get("action", ins.get("action", "WAIT"))
        confidence = ai_dec.get("confidence")
        reason = ai_dec.get("summary_reason", ins.get("desc", "新组合标的，雷达与量化特征已接入"))
        v_decision = ai_info.get("venue_decision") or ai_dec.get("venue_decision")
        strategy_val = "🟢 建议做多" if action_val == "BUY_LONG" else ("🔴 建议做空" if action_val == "SELL_SHORT" else "⚪ AI观望")
        score_val = 2.5 if action_val == "BUY_LONG" else (-2.5 if action_val == "SELL_SHORT" else 0.0)
        m_struct = ai_thought.get("market_structure", f"{ins.get('market_regime', 'CHOP')} ({ins.get('trend_1h', '震荡')})")
        v_oi = ai_thought.get("volume_and_oi", f"OBV: {ins.get('obv_flow', 'NEUTRAL')}, 量能: {ins.get('vol_ratio', 1.0)}x")
        rr_ratio = ai_thought.get("risk_reward_evaluation", "盈亏比评估中")
        raw_t = ai_info.get("raw_ticker", {})
        chg_val = raw_t.get("chg24h") if raw_t.get("chg24h") is not None else lib_item.get("chg24h")
        raw_ticker_vol = raw_t.get("vol24h")
        price_val = ins.get("price") if ins.get("price") not in (None, "--") else lib_item.get("price", "--")
        rsi_val = ins.get("rsi") if ins.get("rsi") is not None else lib_item.get("trend_momentum", {}).get("rsi_14", 50.0)
        adx_val = ai_info.get("adx_1h") if ai_info.get("adx_1h") not in (None, "--") else lib_item.get("trend_momentum", {}).get("adx_1h", "--")
        sm_val = ai_info.get("smart_money") or lib_item.get("smart_money_derivatives", {})
        factors_list.append({
            "name": target.get("name") or ins.get("name"),
            "instId": inst_id,
            "position": pos_map.get(inst_id),
            "type": target.get("type", "crypto"),
            "price": price_val,
            "score": score_val,
            "chg24h": chg_val,
            "bidPx": raw_t.get("bidPx", ins.get("price", lib_item.get("microstructure", {}).get("bid_px", "--"))),
            "askPx": raw_t.get("askPx", ins.get("price", lib_item.get("microstructure", {}).get("ask_px", "--"))),
            "fundingRate": ai_info.get("raw_funding_rate") or (f"{lib_item.get('smart_money_derivatives', {}).get('funding_rate_pct', 0.0):.4f}%" if "funding_rate_pct" in lib_item.get("smart_money_derivatives", {}) else "--"),
            "oiUsd": ai_info.get("raw_oi") or lib_item.get("smart_money_derivatives", {}).get("oi_usd", "--"),
            "takerNetUsd": ai_info.get("raw_taker_vol") or lib_item.get("volume_money_flow", {}).get("taker_net_usd", "--"),
            "lsRatio": ai_info.get("raw_ls_ratio") or lib_item.get("smart_money_derivatives", {}).get("long_short_ratio", "--"),
            "rsi": rsi_val,
            "rsi_7": ins.get("rsi_7", 50.0),
            "vwap_bias": ins.get("vwap_bias", 0.0),
            "macd_hist": ins.get("macd_hist", 0.0),
            "macd_accel": ins.get("macd_accel", 0.0),
            "obv_flow": ins.get("obv_flow", lib_item.get("volume_money_flow", {}).get("obv_flow", "NEUTRAL")),
            "bb_bandwidth": ins.get("bb_bandwidth", lib_item.get("volatility_channel", {}).get("bb_width_1h", 0.0)),
            "vol_ratio": ins.get("vol_ratio", lib_item.get("volume_money_flow", {}).get("vol_ratio_15m", 1.0)),
            "trend_1h": ins.get("trend_1h", "震荡"),
            "trend_4h": ins.get("trend_4h", "震荡"),
            "market_regime": ins.get("market_regime", "CHOP"),
            "strategy_tag": strategy_val,
            "action": action_val,
            "confidence": confidence,
            "smart_money": sm_val,
            "adx_1h": adx_val,
            "atr_1h": lib_item.get("volatility_channel", {}).get("atr_1h", 0.0),
            "atr_pct": lib_item.get("volatility_channel", {}).get("atr_1h_pct", lib_item.get("volatility_channel", {}).get("atr_pct", 0.0)),
            "calculus": {
                "velocity_1h": lib_item.get("calculus_dynamics", {}).get("velocity"),
                "accel_1h": lib_item.get("calculus_dynamics", {}).get("acceleration"),
                "jerk_1h": lib_item.get("calculus_dynamics", {}).get("jerk"),
                "impulse_1h": lib_item.get("calculus_dynamics", {}).get("impulse"),
            },
            "leverage": ai_dec.get("leverage", 3),
            "margin_usdt": ai_dec.get("margin_usdt", 0.0),
            "entry_price": ai_dec.get("entry_price", 0.0),
            "take_profit_price": ai_dec.get("take_profit_price", 0.0),
            "stop_loss_price": ai_dec.get("stop_loss_price", 0.0),
            "risk_reward_ratio": ai_dec.get("risk_reward_ratio", "--"),
            "reason": reason,
            "market_structure": m_struct,
            "volume_and_oi": v_oi,
            "rr_ratio": rr_ratio,
            "thought_process": ai_thought,
            "venue_decision": v_decision,
            "desc": reason,
            "time_str": ai_info.get("time_str") or state_data.get("timestamp") or timestamp_full,
            "timestamp": ai_info.get("timestamp"),
        })
    return factors_list, state_data

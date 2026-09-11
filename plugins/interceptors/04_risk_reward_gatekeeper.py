"""
R20 物理拦截插件规范
====================
id: 04_risk_reward_gatekeeper
name: 真实 2.0R 盈亏比门禁
version: 1.0.0
author: R20 Official
description: 执行层真实风险收益比校验。入场点、止盈目标与云端止损线计算的 R:R 必须 ≥ 2.0，拒绝赔率不足的劣质交易。
tags: 盈亏比, 赔率保障, 官方预设
"""

def check_risk(package: dict, decision: dict, context: dict) -> tuple[bool, str]:
    action = str(decision.get("action", "WAIT")).upper()
    if action == "WAIT":
        return True, ""

    try:
        entry = float(decision.get("entry_price", 0) or 0)
        tp = float(decision.get("take_profit_price", 0) or 0)
        sl = float(decision.get("stop_loss_price", 0) or 0)
    except (ValueError, TypeError):
        return False, "订单价格几何参数缺失或非浮点数，安全降级为 WAIT。"

    # 动态读取全局最低盈亏比门禁（单一事实源：scripts/risk_constants.py）
    try:
        from scripts.risk_constants import MIN_RISK_REWARD_RATIO
        min_rr = float(MIN_RISK_REWARD_RATIO)
    except Exception:
        try:
            import os
            min_rr = float(os.getenv("R20_MIN_RISK_REWARD", "2.0"))
        except Exception:
            min_rr = 2.0

    rr = 0.0
    if action == "BUY_LONG" and entry > sl > 0 and tp > entry:
        rr = (tp - entry) / (entry - sl)
    elif action == "SELL_SHORT" and sl > entry > tp > 0:
        rr = (entry - tp) / (sl - entry)

    if rr < min_rr:
        return False, f"模型报价盈亏比 {rr:.2f}R 未满足全局风控 {min_rr:.1f}R 门禁，执行层降级为 WAIT。"

    return True, ""

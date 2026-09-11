"""
R20 物理拦截插件规范
====================
id: 02_confidence_gatekeeper
name: 高置信度质量门禁
version: 1.2.0
author: R20 Official
description: 兼顾开单欲望与胜率质量。置信度低于 75% 强制 WAIT；高波动动量标的(Tier-2)维持 80% 门禁，按标的分级自适应而非写死币种名。
tags: 置信度, 胜率优化, 官方预设
"""

# 高杂波/易插针标的的更严置信度门禁；优先读标的 tier，无 tier 时回退到历史白名单保持兼容。
_HIGH_NOISE_TIERS = {"tier_2_momentum"}
_LEGACY_HIGH_NOISE_SYMBOLS = {"DOGE"}


def check_risk(package: dict, decision: dict, context: dict) -> tuple[bool, str]:
    action = str(decision.get("action", "WAIT")).upper()
    if action == "WAIT":
        return True, ""

    try:
        conf = float(decision.get("confidence", 0) or 0)
    except (ValueError, TypeError):
        conf = 0.0

    # 动态读取全局最低开仓置信度（单一事实源：scripts/risk_constants.py）
    try:
        from scripts.risk_constants import MIN_ENTRY_CONFIDENCE
        base_conf = float(MIN_ENTRY_CONFIDENCE)
    except Exception:
        try:
            import os
            base_conf = float(os.getenv("R20_MIN_ENTRY_CONFIDENCE", "72.0"))
        except Exception:
            base_conf = 75.0

    name = str(package.get("name", "")).upper()
    tier = str(package.get("tier") or "")
    is_high_noise = (tier in _HIGH_NOISE_TIERS) if tier else (name in _LEGACY_HIGH_NOISE_SYMBOLS)
    high_noise_threshold = max(base_conf + 5.0, 78.0)
    if is_high_noise and conf < high_noise_threshold:
        label = f"{tier or name} 高杂波分级标的" if tier else f"{name} 高杂波标的"
        return False, f"{label}置信度 {conf:.1f}% 未达 {high_noise_threshold:.0f}% 防破位门禁，安全降级为 WAIT。"

    if conf < base_conf:
        return False, f"置信度 {conf:.1f}% 低于全局基准门禁 {base_conf:.0f}%，安全降级为 WAIT。"

    return True, ""

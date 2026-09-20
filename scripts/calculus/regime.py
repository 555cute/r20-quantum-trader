"""全市场宏观态势自适应识别引擎 (Macro Market Regime Engine).

根据标的池行情数据、多周期趋势、微积分一阶速度、二阶加速度、冲击度 (Jerk)、
定积分能量与概率论风险分布，自适应识别全市场宏观体制：
  1. TREND_EXPANSION       单边动量趋势
  2. WIDE_RANGE_OSCILLATION 宽幅上下震荡
  3. LOW_VOL_CHOPPY        窄幅低波横盘
  4. VOLATILITY_SHOCK      极端波动冲击
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence

from r20_backend.math_utils import safe_float as _shared_safe_float

try:
    from scripts.calculus.primitives import _finite
except ImportError:
    from calculus.primitives import _finite

__all__ = [
    "REGIME_TREND_EXPANSION",
    "REGIME_WIDE_OSCILLATION",
    "REGIME_LOW_VOL_CHOPPY",
    "REGIME_VOLATILITY_SHOCK",
    "detect_macro_market_regime",
]

REGIME_TREND_EXPANSION = "TREND_EXPANSION"
REGIME_WIDE_OSCILLATION = "WIDE_RANGE_OSCILLATION"
REGIME_LOW_VOL_CHOPPY = "LOW_VOL_CHOPPY"
REGIME_VOLATILITY_SHOCK = "VOLATILITY_SHOCK"

_REGIME_NAMES = {
    REGIME_TREND_EXPANSION: "单边动量趋势",
    REGIME_WIDE_OSCILLATION: "宽幅上下震荡",
    REGIME_LOW_VOL_CHOPPY: "窄幅低波横盘",
    REGIME_VOLATILITY_SHOCK: "极端波动冲击",
}

_REGIME_TAGS = {
    REGIME_TREND_EXPANSION: "顺势追随 · 波段奔跑",
    REGIME_WIDE_OSCILLATION: "高波动箱体 · 逆势防扫",
    REGIME_LOW_VOL_CHOPPY: "低波休眠 · 严守门禁",
    REGIME_VOLATILITY_SHOCK: "黑天鹅防御 · 避险锁仓",
}

_REGIME_PRESET_MAP = {
    REGIME_TREND_EXPANSION: "stable",
    REGIME_WIDE_OSCILLATION: "wide_oscillation",
    REGIME_LOW_VOL_CHOPPY: "stable",
    REGIME_VOLATILITY_SHOCK: "stable",
}


def _safe_float(val: Any, default: float = 0.0) -> float:
    """薄壳：转调单一事实源（`r20_backend.math_utils.safe_float`，第一百五十刀）。

    私有名保留在本模块（调用点按局部名引用）。语义与原内联实现逐条等价：
    `math.isfinite(f)` 与 `f == f and abs(f) != inf` 同判；`None` 提前返回同判。
    """
    return _shared_safe_float(val, default)


def detect_macro_market_regime(packages: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """根据标的池行情与动力学指标自适应识别全市场宏观体制。

    参数:
        packages: 包含行情、技术指标与微积分特征的标的数据字典序列。
    返回:
        包含体制ID、名称、标签、量化得分与策略建议的结构化字典。
    """
    if not packages:
        return {
            "regime_id": REGIME_LOW_VOL_CHOPPY,
            "regime_name": _REGIME_NAMES[REGIME_LOW_VOL_CHOPPY],
            "regime_tag": _REGIME_TAGS[REGIME_LOW_VOL_CHOPPY],
            "trend_score": 30.0,
            "volatility_score": 25.0,
            "oscillation_score": 30.0,
            "shock_risk": False,
            "dominant_direction": "NEUTRAL",
            "recommended_action": "市场数据缺失，克制交易欲望，保持观望。",
            "recommended_profile": "stable",
            "summary_text": "【市场体制识别】当前宏观体制为【窄幅低波横盘】(数据样本为空)。建议维持默认风控与稳健基座。",
        }

    total_weight = 0.0
    weighted_adx = 0.0
    weighted_atr_pct = 0.0
    bull_count = 0
    bear_count = 0
    shock_detected = False
    reversing_count = 0

    for pkg in packages:
        inst_id = str(pkg.get("instId") or pkg.get("symbol") or "")
        weight = 2.0 if ("BTC" in inst_id or "ETH" in inst_id) else 1.0
        total_weight += weight

        # 价格与波动指标
        price = _safe_float(pkg.get("price") or pkg.get("last"))
        atr = _safe_float(pkg.get("atr_1h") or pkg.get("atr"))
        atr_pct = (atr / price * 100.0) if (price > 0 and atr > 0) else 1.5
        weighted_atr_pct += min(5.0, atr_pct) * weight

        # ADX 趋势强度
        adx = _safe_float(pkg.get("adx_1h") or pkg.get("adx"), default=20.0)
        weighted_adx += min(60.0, max(5.0, adx)) * weight

        # 微积分与动力学
        calc = pkg.get("calculus") or {}
        v = _safe_float(calc.get("velocity_1h") or calc.get("velocity") or pkg.get("velocity_1h"))
        a = _safe_float(calc.get("accel_1h") or calc.get("accel") or pkg.get("accel_1h"))
        j = _safe_float(calc.get("jerk_1h") or calc.get("jerk"))
        kinematic = str(calc.get("kinematic_regime") or "")

        if abs(j) >= 1.8 or abs(v) >= 0.8:
            shock_detected = True

        if "REVERSING" in kinematic or "OVERSTRETCHED" in kinematic or (v > 0.08 and a < -0.10) or (v < -0.08 and a > 0.10):
            reversing_count += 1

        # 4H 宏观大势或 1H 动量方向
        macro_4h = str(pkg.get("macro_4h") or "")
        if "BULL" in macro_4h or v > 0.10:
            bull_count += 1
        elif "BEAR" in macro_4h or v < -0.10:
            bear_count += 1

    n_samples = max(1, len(packages))
    avg_adx = weighted_adx / max(0.1, total_weight)
    avg_atr_pct = weighted_atr_pct / max(0.1, total_weight)

    # 1. 趋势得分计算 (Trend Score: 0~100)
    # 结合 ADX 绝对值与多空方向同向度
    direction_dominance = abs(bull_count - bear_count) / n_samples
    trend_score = min(100.0, max(0.0, (avg_adx - 10.0) * 1.8 + direction_dominance * 45.0))

    # 2. 波动得分计算 (Volatility Score: 0~100)
    # ATR 百分比映射到 0~100 (1.0% -> 35分, 2.0% -> 65分, 3.0%+ -> 85分+)
    volatility_score = min(100.0, max(0.0, avg_atr_pct * 32.0))

    # 3. 震荡得分计算 (Oscillation Score: 0~100)
    # 高波动 + 趋势不明确(方向对抗) + 多标的减速/反转
    conflict_ratio = 1.0 - direction_dominance  # 多空对峙程度
    reversing_ratio = reversing_count / n_samples
    oscillation_raw = (volatility_score * 0.45) + (conflict_ratio * 35.0) + (reversing_ratio * 25.0)
    if avg_adx < 22.0:
        oscillation_raw += 15.0  # 低 ADX 增强震荡属性
    oscillation_score = min(100.0, max(0.0, oscillation_raw))

    # 4. 体制判定
    if shock_detected and volatility_score >= 65.0:
        regime_id = REGIME_VOLATILITY_SHOCK
        action = "全市场突发剧烈异常冲击，严禁盲目逆势摸顶抄底，启动避险防护模式。"
    elif trend_score >= 58.0 and trend_score > (oscillation_score - 5.0):
        regime_id = REGIME_TREND_EXPANSION
        action = "大盘单边动量趋势确立，顺应大势做主浪波段，给足呼吸空间让利润奔跑。"
    elif volatility_score >= 42.0 and oscillation_score >= 50.0:
        regime_id = REGIME_WIDE_OSCILLATION
        action = "处于宽幅上下震荡箱体，建议箱体边界高抛低吸，拉宽止损至 2.0x ATR 防插针扫损，浮盈达 1.5R 及时保本或锁利。"
    else:
        regime_id = REGIME_LOW_VOL_CHOPPY
        action = "全市场处于窄幅低波整理，严禁频繁交易磨损手续费，耐心等待放量破位信号。"

    dominant_dir = "BULL" if bull_count > bear_count and direction_dominance > 0.25 else (
        "BEAR" if bear_count > bull_count and direction_dominance > 0.25 else "NEUTRAL"
    )

    regime_name = _REGIME_NAMES[regime_id]
    regime_tag = _REGIME_TAGS[regime_id]
    recommended_profile = _REGIME_PRESET_MAP[regime_id]

    summary_text = (
        f"【市场体制自适应识别】: 当前全市场宏观体制为【{regime_name}】({regime_tag})。\n"
        f"- 核心量化指标: 趋势强度={trend_score:.1f}/100 | 波动指数={volatility_score:.1f}/100 | 震荡指数={oscillation_score:.1f}/100 | 主导方向={dominant_dir}\n"
        f"- 操盘指导建议: {action}"
    )

    return {
        "regime_id": regime_id,
        "regime_name": regime_name,
        "regime_tag": regime_tag,
        "trend_score": round(trend_score, 1),
        "volatility_score": round(volatility_score, 1),
        "oscillation_score": round(oscillation_score, 1),
        "shock_risk": shock_detected,
        "dominant_direction": dominant_dir,
        "recommended_action": action,
        "recommended_profile": recommended_profile,
        "summary_text": summary_text,
    }

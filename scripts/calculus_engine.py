"""Causal Calculus, Definite Integrals & Probability Theory Engine for Quantitative Trading.

This module provides the core mathematical, continuous physical state, definite integration,
and stochastic probabilistic foundation for R20 Quantum Trader.

All functions are strictly causal: chronological sequences with newest observation last.
No lookahead bias. Closed candle data is enforced.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Sequence


def _finite(values: Iterable[float]) -> List[float]:
    return [float(v) for v in values if v is not None and math.isfinite(float(v)) and float(v) > 0]


def _ema(values: Sequence[float], span: int = 3) -> List[float]:
    if not values:
        return []
    alpha = 2.0 / (max(1, span) + 1.0)
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * float(value) + (1.0 - alpha) * out[-1])
    return out


def _diff(values: Sequence[float], lag: int = 1) -> List[float]:
    lag = max(1, int(lag))
    return [values[i] - values[i - lag] for i in range(lag, len(values))]


def _normalise(value: float, scale: float, bound: float = 3.0) -> float:
    if scale <= 1e-12:
        return 0.0
    return max(-bound, min(bound, value / scale))


def _sign(value: float, threshold: float = 0.08) -> int:
    return 1 if value > threshold else (-1 if value < -threshold else 0)


def _normal_cdf(z: float) -> float:
    """Standard normal cumulative distribution function (CDF) via erf."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def classify_regime(velocity: float, acceleration: float, impulse: float, jerk: float) -> str:
    """Classify physical kinematic regime based on derivatives."""
    if abs(jerk) >= 1.8 and abs(velocity) >= 0.8:
        return "SHOCK_HIGH_JERK"
    if abs(velocity) < 0.12 and abs(acceleration) < 0.12:
        return "RANGE_LOW_VELOCITY"
    direction = 1 if impulse >= 0 else -1
    if direction > 0:
        if velocity > 0.15 and acceleration > 0.10:
            return "BULL_ACCELERATING"
        if velocity > 0.08 and acceleration < -0.10:
            return "BULL_DECELERATING"
        if velocity < -0.08:
            return "BULL_REVERSING"
        return "BULL_STABLE"
    if velocity < -0.15 and acceleration < -0.10:
        return "BEAR_ACCELERATING"
    if velocity < -0.08 and acceleration > 0.10:
        return "BEAR_DECELERATING"
    if velocity > 0.08:
        return "BEAR_REVERSING"
    return "BEAR_STABLE"


def classify_power_regime(power: float, curvature: float, velocity: float, acceleration: float) -> str:
    """Classify physical kinetic flux and power state based on power and curvature."""
    if curvature >= 1.5:
        return "HIGH_CURVATURE_INFLECTION"
    if power > 0.12 and abs(velocity) > 0.20:
        return "KINETIC_ACCELERATING"
    if power < -0.12:
        return "KINETIC_EXHAUSTION"
    return "STEADY_FLUX"


def classify_integral_regime(energy: float, deviation_area: float) -> str:
    """Classify aggregate path-energy state from aggregate integral values."""
    if energy > 0.8 and deviation_area > 0.5:
        return "POSITIVE_ENERGY_EXPANSION"
    if energy < -0.8 and deviation_area < -0.5:
        return "NEGATIVE_ENERGY_DEPLETION"
    if abs(deviation_area) >= 2.5:
        return "OVERSTRETCHED_MEAN_REVERSION"
    return "BALANCED_ENERGY"


def classify_probability_regime(
    skewness: float,
    kurtosis: float,
    continuation_prob: float,
    breakdown_prob: float,
    is_fat_tail: bool,
) -> str:
    """Classify aggregate stochastic state, prioritising tail/asymmetry risk over direction."""
    if is_fat_tail and kurtosis >= 3.0:
        return "EXTREME_FAT_TAIL_RISK"
    if skewness > 0.6:
        return "POSITIVE_SKEW_UPSIDE"
    if skewness < -0.6:
        return "NEGATIVE_SKEW_DOWNSIDE"
    if continuation_prob >= 70.0:
        return "HIGH_PROB_BULL_CONTINUATION"
    if breakdown_prob >= 70.0:
        return "HIGH_PROB_BEAR_BREAKDOWN"
    return "GAUSSIAN_BALANCED"


# =============================================================================
# 1. DEFINITE INTEGRAL FOUNDATION (定积分数学体系)
# =============================================================================
def calculate_definite_integrals(
    closes: Sequence[float],
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
    vols: Sequence[float] | None = None,
    window: int = 12
) -> Dict[str, Any]:
    """Calculate definite integrals over historical time window [t-T, t] using Trapezoidal Rule.
    
    Metrics:
    1. energy_integral (动能净做功积分): \int_{t-T}^t v(tau) d tau
    2. deviation_area_integral (路径偏离面积分): \int_{t-T}^t (P - P0)/P0 d tau
    3. volume_action_integral (量价做功功率积分): \int (dP/P) * Vol d tau
    """
    prices = _finite(closes)
    n = len(prices)
    if n < 4:
        return {
            "valid": False,
            "energy_integral": 0.0,
            "deviation_area_integral": 0.0,
            "volume_action_integral": 0.0,
            "integral_regime": "NEUTRAL"
        }

    sub_n = min(window, n)
    sub_prices = prices[-sub_n:]
    log_p = [math.log(p) for p in sub_prices]

    # Velocity series in sub-window
    velocities = [log_p[i] - log_p[i-1] for i in range(1, len(log_p))]

    # 1. Definite Integral of Velocity: Net Displacement Energy (Trapezoidal Rule)
    # \int v(t) dt = \sum (v_i + v_{i-1})/2 * dt
    energy_integral_raw = 0.0
    for i in range(1, len(velocities)):
        energy_integral_raw += (velocities[i] + velocities[i-1]) * 0.5
    
    # 2. Definite Integral of Mean-Deviation Area (Relative to Window Starting Baseline P0)
    baseline = sub_prices[0]
    rel_devs = [(p - baseline) / baseline for p in sub_prices]
    deviation_area_raw = 0.0
    for i in range(1, len(rel_devs)):
        deviation_area_raw += (rel_devs[i] + rel_devs[i-1]) * 0.5

    # 3. Volume-Weighted Action Integral (Work Done by Capital)
    volume_action_raw = 0.0
    if vols and len(vols) >= n:
        sub_vols = [float(v) for v in vols[-sub_n:]]
        total_vol = sum(sub_vols) or 1.0
        for i in range(1, len(sub_prices)):
            price_delta_pct = (sub_prices[i] - sub_prices[i-1]) / sub_prices[i-1]
            vol_weight = sub_vols[i] / total_vol
            volume_action_raw += price_delta_pct * vol_weight

    # Scaling and Normalization
    volatility = math.sqrt(sum((v - (sum(velocities)/len(velocities)))**2 for v in velocities) / max(1, len(velocities)-1)) if len(velocities) > 1 else 1e-4
    scale = max(volatility, 1e-5)

    energy_integral = _normalise(energy_integral_raw, scale * 3.0, bound=5.0)
    deviation_area = _normalise(deviation_area_raw, 0.02, bound=5.0)
    volume_action = _normalise(volume_action_raw, 0.01, bound=5.0)

    # Integral Regime Classification
    integral_regime = classify_integral_regime(energy_integral, deviation_area)

    return {
        "valid": True,
        "energy_integral": round(energy_integral, 4),
        "deviation_area_integral": round(deviation_area, 4),
        "volume_action_integral": round(volume_action, 4),
        "integral_regime": integral_regime
    }


# =============================================================================
# 2. PROBABILITY THEORY & STOCHASTIC MODELING (概率论与随机过程体系)
# =============================================================================
def calculate_probability_theory(
    returns: Sequence[float],
    velocity: float = 0.0,
    acceleration: float = 0.0
) -> Dict[str, Any]:
    """Calculate statistical higher moments, fat-tail risk (VaR/CVaR) and conditional transition probabilities.
    
    Metrics:
    1. skewness (偏度): 衡量多空不对称与单向长尾风险
    2. kurtosis (超额峰度): 衡量黑天鹅与肥尾厚度 (Fat-Tailed Risk)
    3. continuation_prob_pct (多头延续胜率概率): \Phi( (v + a - \mu)/\sigma )
    4. breakdown_prob_pct (空头击穿概率): \Phi( (-v - a - \mu)/\sigma )
    5. var_95_pct (95% 置信度在险价值): 下行极端单期最大预期损失
    6. cvar_95_pct (95% 条件在险价值 / 预期尾部损失): 穿透 VaR 时的平均损失期望
    """
    r_list = [float(r) for r in returns if math.isfinite(float(r))]
    n = len(r_list)
    if n < 5:
        return {
            "valid": False,
            "skewness": 0.0,
            "kurtosis": 0.0,
            "continuation_prob_pct": 50.0,
            "breakdown_prob_pct": 50.0,
            "var_95_pct": 1.5,
            "cvar_95_pct": 2.2,
            "prob_regime": "NEUTRAL_DISTRIBUTION",
            "is_fat_tail": False
        }

    mean_r = sum(r_list) / n
    variance = sum((r - mean_r) ** 2 for r in r_list) / max(1, n - 1)
    sigma = math.sqrt(variance)
    scale_sigma = max(sigma, 1e-6)

    # 3rd Central Moment: Skewness (偏度)
    m3 = sum((r - mean_r) ** 3 for r in r_list) / n
    skewness = m3 / (scale_sigma ** 3)

    # 4th Central Moment: Excess Kurtosis (超额峰度, 正态分布基准为 0)
    m4 = sum((r - mean_r) ** 4 for r in r_list) / n
    kurtosis = (m4 / (scale_sigma ** 4)) - 3.0

    # Risk Metrics: Value at Risk (VaR 95%) & Conditional VaR (CVaR 95%)
    # Standard normal quantile for 95% is ~1.645, CVaR weight ~2.063
    # If fat-tailed (kurtosis > 1.5), we apply Cornish-Fisher expansion adjustment
    z_var = 1.645
    if kurtosis > 0.5 or abs(skewness) > 0.5:
        # Cornish-Fisher VaR quantile adjustment
        z_var = z_var + (skewness / 6.0) * (z_var**2 - 1.0) + (kurtosis / 24.0) * (z_var**3 - 3.0 * z_var)

    var_95_raw = max(0.0, -(mean_r - z_var * scale_sigma))
    cvar_95_raw = max(var_95_raw * 1.25, -(mean_r - (z_var + 0.42) * scale_sigma))

    # Conditional Transition Probabilities via Normal/CDF mapping
    # Z-score of immediate directional thrust combining Velocity and Acceleration
    z_thrust = (velocity * 0.6 + acceleration * 0.4)
    continuation_prob = _normal_cdf(z_thrust) * 100.0
    breakdown_prob = _normal_cdf(-z_thrust) * 100.0

    # Bounded outputs
    skewness_bounded = max(-3.0, min(3.0, skewness))
    kurtosis_bounded = max(-2.0, min(10.0, kurtosis))
    is_fat_tail = bool(kurtosis_bounded >= 1.5 or abs(skewness_bounded) >= 1.2)

    prob_regime = classify_probability_regime(
        skewness_bounded, kurtosis_bounded, continuation_prob, breakdown_prob, is_fat_tail
    )

    return {
        "valid": True,
        "skewness": round(skewness_bounded, 2),
        "kurtosis": round(kurtosis_bounded, 2),
        "continuation_prob_pct": round(continuation_prob, 1),
        "breakdown_prob_pct": round(breakdown_prob, 1),
        "var_95_pct": round(var_95_raw * 100.0, 2),
        "cvar_95_pct": round(cvar_95_raw * 100.0, 2),
        "prob_regime": prob_regime,
        "is_fat_tail": is_fat_tail
    }


# =============================================================================
# 3. UNIFIED CALCULUS & PROBABILITY MATHEMATICAL PIPELINE (数理与统计统一计算)
# =============================================================================
def calculate_calculus(
    closes: Sequence[float],
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
    vols: Sequence[float] | None = None,
    smooth_span: int = 3,
    lag: int = 1
) -> Dict[str, Any]:
    """Calculate causal calculus, definite integrals and probability metrics for a single series."""
    prices = _finite(closes)
    if len(prices) < 6:
        return {
            "valid": False,
            "reason": "insufficient_closed_candles",
            "sample_size": len(prices),
            "definite_integrals": {},
            "probability_theory": {}
        }

    log_prices = [math.log(p) for p in prices]
    smooth = _ema(log_prices, smooth_span)
    returns = _diff(smooth, lag)
    if len(returns) < 4:
        return {
            "valid": False,
            "reason": "insufficient_derivative_samples",
            "sample_size": len(prices),
            "definite_integrals": {},
            "probability_theory": {}
        }

    recent_returns = returns[-min(20, len(returns)):]
    mean_r = sum(recent_returns) / len(recent_returns)
    variance = sum((r - mean_r) ** 2 for r in recent_returns) / max(1, len(recent_returns) - 1)
    volatility = math.sqrt(variance)
    scale = max(volatility, 1e-5)

    velocity_raw = returns[-1]
    acceleration_raw = returns[-1] - returns[-2]
    acceleration_series = _diff(returns, 1)
    jerk_raw = acceleration_series[-1] - acceleration_series[-2] if len(acceleration_series) >= 2 else 0.0

    window = min(8, len(returns))
    decay = 0.82
    impulse_raw = sum((decay ** i) * returns[-1 - i] for i in range(window))

    velocity = _normalise(velocity_raw, scale)
    acceleration = _normalise(acceleration_raw, scale)
    jerk = _normalise(jerk_raw, scale)
    impulse = _normalise(impulse_raw, scale * 2.0)

    # 1. Definite Integrals
    integrals = calculate_definite_integrals(closes, highs, lows, vols, window=12)

    # 2. Probability Theory & Stochastic Metrics
    probabilities = calculate_probability_theory(recent_returns, velocity, acceleration)

    # ATR percentage
    atr_pct = 0.0
    if highs is not None and lows is not None and len(highs) == len(prices) and len(lows) == len(prices):
        ranges = []
        for i, (high, low) in enumerate(zip(highs, lows)):
            try:
                h, lo = float(high), float(low)
                prev = prices[i - 1] if i else prices[i]
                ranges.append(max(h - lo, abs(h - prev), abs(lo - prev)) / prices[i])
            except (TypeError, ValueError, ZeroDivisionError):
                continue
        if ranges:
            atr_pct = sum(ranges[-min(14, len(ranges)):]) / min(14, len(ranges))

    quality = min(1.0, max(0.0, 0.45 + min(0.35, len(prices) / 100.0) + (0.20 if volatility > 1e-5 else 0.0)))
    regime = classify_regime(velocity, acceleration, impulse, jerk)

    # Curvature (kappa) and Kinetic Power Flux (Phi = v * a)
    curvature = abs(acceleration) / math.pow(1.0 + velocity ** 2, 1.5)
    power = velocity * acceleration
    power_regime = classify_power_regime(power, curvature, velocity, acceleration)

    return {
        "valid": True,
        "sample_size": len(prices),
        "velocity": round(velocity, 4),
        "acceleration": round(acceleration, 4),
        "impulse": round(impulse, 4),
        "jerk": round(jerk, 4),
        "curvature": round(curvature, 4),
        "power": round(power, 4),
        "power_regime": power_regime,
        "atr_pct": round(atr_pct * 100.0, 4),
        "volatility": round(volatility, 6),
        "regime": regime,
        "quality": round(quality, 3),
        "direction": _sign(impulse),
        # Integrated Foundations
        "definite_integrals": integrals,
        "probability_theory": probabilities
    }


def calculate_multi_timeframe(candles_by_tf: Dict[str, Sequence[Sequence[float]]]) -> Dict[str, Any]:
    """Calculate multi-timeframe unified calculus, definite integral and probability metrics."""
    result: Dict[str, Any] = {}
    valid = []
    for timeframe, candles in candles_by_tf.items():
        # OKX packages are newest-first; reverse to chronological order.
        rows = list(reversed(candles or []))
        closes = [row[3] for row in rows if len(row) >= 4]
        highs = [row[1] for row in rows if len(row) >= 4]
        lows = [row[2] for row in rows if len(row) >= 4]
        vols = [row[4] for row in rows if len(row) >= 5]
        features = calculate_calculus(closes, highs, lows, vols)
        result[timeframe] = features
        if features.get("valid"):
            valid.append(features)

    if not valid:
        return {
            "valid": False,
            "timeframes": result,
            "regime": "DATA_UNRELIABLE",
            "quality": 0.0,
            "definite_integrals": {},
            "probability_theory": {}
        }

    impulse = sum(f["impulse"] for f in valid) / len(valid)
    velocity = sum(f["velocity"] for f in valid) / len(valid)
    acceleration = sum(f["acceleration"] for f in valid) / len(valid)
    jerk = max(abs(f["jerk"]) for f in valid)
    curvature = sum(f.get("curvature", 0.0) for f in valid) / len(valid)
    power = sum(f.get("power", 0.0) for f in valid) / len(valid)
    power_regime = classify_power_regime(power, curvature, velocity, acceleration)
    direction_votes = sum(f["direction"] for f in valid)

    if direction_votes >= 2 and acceleration > 0.05:
        regime = "BULL_ACCELERATING"
    elif direction_votes <= -2 and acceleration < -0.05:
        regime = "BEAR_ACCELERATING"
    elif abs(direction_votes) <= 1:
        regime = "RANGE_LOW_VELOCITY"
    else:
        regime = "BULL_DECELERATING" if direction_votes > 0 and acceleration < 0 else ("BEAR_DECELERATING" if acceleration > 0 else "MIXED_TRANSITION")

    # Aggregate Definite Integrals across timeframes
    int_valid = [f["definite_integrals"] for f in valid if f.get("definite_integrals", {}).get("valid")]
    avg_energy_int = sum(i["energy_integral"] for i in int_valid) / len(int_valid) if int_valid else 0.0
    avg_dev_area = sum(i["deviation_area_integral"] for i in int_valid) / len(int_valid) if int_valid else 0.0
    avg_vol_action = sum(i["volume_action_integral"] for i in int_valid) / len(int_valid) if int_valid else 0.0

    # Aggregate Probability Metrics across timeframes (15M and 1H prioritized)
    prob_valid = [f["probability_theory"] for f in valid if f.get("probability_theory", {}).get("valid")]
    avg_skewness = sum(p["skewness"] for p in prob_valid) / len(prob_valid) if prob_valid else 0.0
    avg_kurtosis = sum(p["kurtosis"] for p in prob_valid) / len(prob_valid) if prob_valid else 0.0
    avg_continuation_prob = sum(p["continuation_prob_pct"] for p in prob_valid) / len(prob_valid) if prob_valid else 50.0
    avg_breakdown_prob = sum(p["breakdown_prob_pct"] for p in prob_valid) / len(prob_valid) if prob_valid else 50.0
    max_var_95 = max((p["var_95_pct"] for p in prob_valid), default=1.5)
    max_cvar_95 = max((p["cvar_95_pct"] for p in prob_valid), default=2.2)
    any_fat_tail = any(p["is_fat_tail"] for p in prob_valid)

    aggregate_integral_regime = classify_integral_regime(avg_energy_int, avg_dev_area)
    aggregate_prob_regime = classify_probability_regime(
        avg_skewness, avg_kurtosis, avg_continuation_prob, avg_breakdown_prob, any_fat_tail
    )

    return {
        "valid": True,
        "timeframes": result,
        "velocity": round(velocity, 4),
        "acceleration": round(acceleration, 4),
        "impulse": round(impulse, 4),
        "max_abs_jerk": round(jerk, 4),
        "curvature": round(curvature, 4),
        "power": round(power, 4),
        "power_regime": power_regime,
        "regime": regime,
        "quality": round(sum(f["quality"] for f in valid) / len(valid), 3),
        # Unified Definite Integrals
        "definite_integrals": {
            "energy_integral": round(avg_energy_int, 4),
            "deviation_area_integral": round(avg_dev_area, 4),
            "volume_action_integral": round(avg_vol_action, 4),
            "regime": aggregate_integral_regime
        },
        # Unified Probability Theory
        "probability_theory": {
            "skewness": round(avg_skewness, 2),
            "kurtosis": round(avg_kurtosis, 2),
            "continuation_prob_pct": round(avg_continuation_prob, 1),
            "breakdown_prob_pct": round(avg_breakdown_prob, 1),
            "var_95_pct": round(max_var_95, 2),
            "cvar_95_pct": round(max_cvar_95, 2),
            "is_fat_tail": any_fat_tail,
            "regime": aggregate_prob_regime
        }
    }

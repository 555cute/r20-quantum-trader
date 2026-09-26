"""Causal Calculus, Definite Integrals & Probability Theory Engine for Quantitative Trading.

This module provides the core mathematical, continuous physical state, definite integration,
and stochastic probabilistic foundation for AstraQuant.

All functions are strictly causal: chronological sequences with newest observation last.
No lookahead bias. Closed candle data is enforced.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Sequence


# =============================================================================
# 结构优化阶段 4·B3 第四十五刀：实现已外提到 `scripts/calculus/` 子包，
# 本文件只作**再导出**（不是搬空）—— 有 4 个外部调用方按裸名导入：
#   scripts/factor_library.py            from calculus_engine import calculate_calculus
#   scripts/trader/factors.py            from calculus_engine import calculate_multi_timeframe
#   scripts/brain/packages.py            from calculus_engine import calculate_multi_timeframe
#   scripts/factors/candles_15m.py       （同上）
# 以及 tests/llm/test_calculus_engine.py 与 tests/llm/test_quant_system_calculus.py
# 直接 `from calculus_engine import (...)`（含 `_normal_cdf` / `_ema` / `_diff`
# / `_normalise` 等私有名，故私有名也必须再导出）。
#
# 分层（依赖单向，无环）：
#   calculus/primitives.py  数学原语 + 状态分级器
#   calculus/calculate.py   定积分 / 概率论 / 因果微积分 / 多周期聚合
#   calculus_engine.py      ← 本文件，纯再导出
#
# ⚠️ `scripts/` 不是 Python 包，调用方以裸名导入且不保证 repo 根在 sys.path，
# 故用 try/except 双模导入（已实测三种 sys.path 布局均可解析）。
# =============================================================================
try:  # repo 根在 sys.path
    from scripts.calculus.primitives import (  # noqa: E402,F401
        _diff,
        _ema,
        _finite,
        _normal_cdf,
        _normalise,
        _sign,
        classify_integral_regime,
        classify_power_regime,
        classify_probability_regime,
        classify_regime,
    )
    from scripts.calculus.calculate import (  # noqa: E402,F401
        calculate_calculus,
        calculate_definite_integrals,
        calculate_multi_timeframe,
        calculate_probability_theory,
    )
    from scripts.calculus.regime import (  # noqa: E402,F401
        REGIME_LOW_VOL_CHOPPY,
        REGIME_TREND_EXPANSION,
        REGIME_VOLATILITY_SHOCK,
        REGIME_WIDE_OSCILLATION,
        detect_macro_market_regime,
    )
except ImportError:  # scripts/ 在 sys.path（真实运行时的布局）
    from calculus.primitives import (  # noqa: E402,F401
        _diff,
        _ema,
        _finite,
        _normal_cdf,
        _normalise,
        _sign,
        classify_integral_regime,
        classify_power_regime,
        classify_probability_regime,
        classify_regime,
    )
    from calculus.calculate import (  # noqa: E402,F401
        calculate_calculus,
        calculate_definite_integrals,
        calculate_multi_timeframe,
        calculate_probability_theory,
    )
    from calculus.regime import (  # noqa: E402,F401
        REGIME_LOW_VOL_CHOPPY,
        REGIME_TREND_EXPANSION,
        REGIME_VOLATILITY_SHOCK,
        REGIME_WIDE_OSCILLATION,
        detect_macro_market_regime,
    )

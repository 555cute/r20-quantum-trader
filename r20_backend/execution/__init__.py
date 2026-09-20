"""Modular Execution Engine and Quantitative Pipeline for R20 Quantum Trader.

## 模块清单

| 模块 | 职责 | 注入面 |
|---|---|---|
| `indicators.py` | 纯技术指标计算（EMA / RSI / ATR / MACD 柱加速度 / OBV 趋势 / 布林挤压） | 无（纯函数） |
| `sizing.py` | 按 AI 决策与账户实况推导下单张数并套四道钳制 | 由调用方传入上限常量 |
| `circuit_breaker.py` | 黑天鹅哨兵、熔断态、止损冷却期读写 | 无（读自身状态文件） |
| `risk_gates.py` | **发送前三道风控闸门**：杠杆区间夹取 / 单笔保证金夹取 / 跨所同向敞口拒开 | 无（纯计算；常量与 `_fail` 由调用方注入） |
| `own_records.py` | **己仓对账**：交易所实况 × 本方台账 holding 行 → 逐仓归属判定（`own` / `stale_closed` 账实不符 / `mismatch` / `untracked` / `ledger_unavailable`）。**`untracked` 与 `ledger_unavailable` 是『不可判定』，绝不是『外部仓』** | 无（台账路径可注入；纯计算） |
| `cooldowns.py` | **止损冷却单一事实源**：读冷却状态（损坏≠缺失）/ 是否仍在冷却 / 只读展示面 | 无（路径与时长由调用方传入） |

> `cooldowns.py`（结构优化阶段 4·B3 第五十刀）原为
> `scripts/ai_factor_trader.py` 与 `r20_backend/execution/circuit_breaker.py`
> **各自内联**的同一套止损冷却读写（`is_in_stop_cooldown` 逐字节相同、
> `_read_stop_cooldowns_state` / `load_stop_cooldowns` 等价）。
> 冷却判定直接决定「硬止损后能否立即同向重进」，两份拷贝漂移会造成
> 交易侧与风控面判断不一致。**冷却文件路径与冷却时长一律由调用方在调用时传入**
> （两侧的 `STOP_COOLDOWN_FILE` 都可被测试 patch；`aft` 用 str、`cb` 用 Path）。

> `risk_gates.py`（结构优化阶段 4·B3 第三十八刀）原为
> `r20_backend/execution_router.py::open_protected_position` 的 L108–173，
> 是该函数最大的一块内聚逻辑。抽取时发现并修复了一个真实 bug：
> 敞口闸门的大小写比较永不相等（详见该模块文档串）。

## 铁律

1. **子模块不得在 import 期绑定调用方的名字**（`MAX_*` 常量会被
   `patch.object(execution_router, "MAX_SINGLE_ASSET_MARGIN", …)` 这类测试缝改写）。
   一律**调用期注入** —— 见 `risk_gates` 的 `min_leverage` / `max_leverage` /
   `max_single_asset_margin` / `total_exposure_cap` / `fail_factory` 形参。
2. `risk_gates` **不得** import `execution_router`（会成环）：`_fail` 由调用方注入。
"""
from __future__ import annotations

from .indicators import (
    calc_ema,
    calc_rsi,
    calc_atr,
    calc_macd_histogram_acceleration,
    calc_obv_trend,
    calc_bollinger_squeeze,
)
from .sizing import (
    quantize_size,
    max_size_within_margin,
    effective_daily_loss_limit,
    effective_single_asset_margin,
    effective_risk_per_trade,
)
from .circuit_breaker import (
    check_black_swan_sentinel,
    is_circuit_breaker_active,
    is_in_stop_cooldown,
    add_stop_cooldown,
    load_stop_cooldowns,
)
from .risk_gates import (
    clamp_leverage,
    clamp_margin,
    check_total_exposure,
)
# 结构优化阶段 4·B3 第五十刀：止损冷却的**单一事实源**。
# `circuit_breaker` 与 `scripts/ai_factor_trader` 的同名函数现在都是转调这里的薄壳。
from .cooldowns import (
    read_stop_cooldowns_state,
    is_in_stop_cooldown as is_in_stop_cooldown_impl,
    load_stop_cooldowns as load_stop_cooldowns_impl,
)
# 审计④#7(2026-09-13)：删除 execution/reservation.py 幻影孪生——它按不存在的 API 写
# （mgr.reserve(venue=…, ttl_seconds=…) / res.ok / DEFAULT_RESERVATION_TTL_SECONDS /
# list_active 均不存在），任何调用即 TypeError，却挂在包导出面上「谁接谁炸」。
# 选所/预留的**活实现**在 scripts/ai_factor_trader.py（route_and_reserve_signal 等，
# API 对齐、tests/test_venue_wiring 钉死）；未来单源迁移属结构工程批次，勿再复制副本。

__all__ = [
    "calc_ema",
    "calc_rsi",
    "calc_atr",
    "calc_macd_histogram_acceleration",
    "calc_obv_trend",
    "calc_bollinger_squeeze",
    "quantize_size",
    "max_size_within_margin",
    "effective_daily_loss_limit",
    "effective_single_asset_margin",
    "effective_risk_per_trade",
    "check_black_swan_sentinel",
    "is_circuit_breaker_active",
    "is_in_stop_cooldown",
    "read_stop_cooldowns_state",
    "is_in_stop_cooldown_impl",
    "load_stop_cooldowns_impl",
    "add_stop_cooldown",
    "load_stop_cooldowns",
    "clamp_leverage",
    "clamp_margin",
    "check_total_exposure",
]

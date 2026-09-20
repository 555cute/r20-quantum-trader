"""多所路径的**发送前三道风控闸门**（结构优化阶段 4·B3 第三十八刀）。

原样搬自 `r20_backend/execution_router.py::open_protected_position` 的 L108–173
（约 66 行）—— 该函数 293 行里最大的一块**内聚逻辑**。

| 闸门 | 作用 |
|---|---|
| `clamp_leverage` | 杠杆夹到 `[MIN_LEVERAGE, min(MAX_LEVERAGE, 池内单标的上限)]` |
| `clamp_margin` | 单笔保证金夹到三道上限的**最小值**（全局单标的封顶 / 调用方权益顶 / 该所预算） |
| `check_total_exposure` | 跨所**同向**名义额合计超限则**拒开**（不是夹） |

## 为什么值得单独成模块

这三道闸门是本模块**唯一**在发送前约束"下多大"的地方，而它们此前埋在 293 行
编排代码中间、靠缩进区分。抽出来后：

- 每道闸门可以**脱离 adapter / 交易所**单测（全是纯计算 + 一次可选取数）；
- "闸门到底按哪个上限夹"这件事在签名上就看得见。

## 三处易错点（均原样保留）

1. **杠杆的两端夹取顺序**：先取 `_upper = MAX_LEVERAGE or leverage`，
   再 `if _inst_cap > 0: _upper = min(_upper, _inst_cap)`，
   最后 `min(max(leverage, MIN_LEVERAGE or leverage), _upper)`。
   注意 `MIN_LEVERAGE or leverage` / `MAX_LEVERAGE or leverage`：**配置为 0/None 时
   退化为"不夹"（用原值）**，不是退化为 0 —— 写成 `MIN_LEVERAGE` 会把杠杆夹成 0。
2. **保证金上限只取 `> 0` 且有限的那些**（`math.isfinite(c) and c > 0`）。
   `0` 表示"该上限不可用"，**不参与求最小** —— 否则会把保证金夹成 0。
3. **敞口超限是"拒"不是"夹"**：`_fail(...)` 早退，绝不静默缩量。
   注释写明理由："敞口超限意味着不该再开"。

## 失败语义（与调用方约定）

`check_total_exposure` 读不到持仓时**不是** fail-open 也不是抛异常，
而是返回一个 `_fail("exposure", ...)` 的结果对象 —— 由调用方决定怎么返回。
本模块不 import `execution_router`，`_fail` 由调用方注入（见 `fail_factory` 形参）。

同理 `clamp_*` 的 `print` 文案**原样保留**：它们是运维在日志里定位"为什么这单只开了
这么多"的依据，措辞不得改动。
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "clamp_leverage",
    "clamp_margin",
    "check_total_exposure",
]


def clamp_leverage(
    *,
    venue: str,
    asset: str,
    decision: Dict[str, Any],
    leverage: float,
    min_leverage: Any,
    max_leverage: Any,
) -> Tuple[float, Dict[str, Any]]:
    """把杠杆夹到 `[min_leverage, min(max_leverage, 决策自带 max_leverage)]`。

    返回 `(夹取后的 leverage, 可能被改写过的 decision)`。

    ⚠️ `MIN_LEVERAGE or leverage` / `MAX_LEVERAGE or leverage`：配置为 0/None 时
    **退化为不夹**（用原值），而不是退化为 0。
    """
    leverage_before_clamp = leverage
    try:
        _inst_cap = float(decision.get("max_leverage") or 0.0)
    except (TypeError, ValueError):
        _inst_cap = 0.0
    _upper = float(max_leverage or 0.0) or leverage
    if _inst_cap > 0:
        _upper = min(_upper, _inst_cap)  # 池内单标的硬上限（tier 派生），比全局更严时生效
    leverage = min(max(leverage, float(min_leverage or 0.0) or leverage), _upper)
    if abs(leverage - leverage_before_clamp) > 1e-9:
        decision = {**decision, "leverage": leverage}
        print(f"[杠杆闸门] {venue.upper()} {asset} 杠杆 {leverage_before_clamp:g}x 超出配置区间 "
              f"[{float(min_leverage or 0):g}x, {float(max_leverage or 0):g}x]，已夹至 {leverage:g}x")
    return leverage, decision


def clamp_margin(
    *,
    venue: str,
    asset: str,
    decision: Dict[str, Any],
    margin: float,
    max_margin_usdt: Any,
    max_single_asset_margin: Any,
    max_margin_equity_ratio: Any,
    pool: Optional[Dict[str, Any]],
) -> Tuple[float, Dict[str, Any], float]:
    """把单笔保证金夹到三道上限的**最小值**。

    返回 `(夹取后的 margin, 可能被改写过的 decision, 夹取前的值或 0.0)`。

    ⚠️ 只取 `> 0` 且**有限**的上限（`0` = 该上限不可用，不参与求最小，
    否则会把保证金夹成 0）。
    """
    margin_clamped_from = 0.0
    _caps = [float(max_single_asset_margin or 0.0),
             float(max_margin_usdt or decision.get("max_margin_usdt") or 0.0)]
    # 语义：pool 为空 dict = 池配置读不到（加固层不可用）→ 不阻拦；有池则逐条落实。
    if pool:
        _caps.append(float(pool.get("margin_per_trade_usdt") or 0.0))
    _margin_caps = [c for c in _caps if math.isfinite(c) and c > 0]
    if _margin_caps and margin > min(_margin_caps):
        margin_clamped_from = round(margin, 4)
        margin = round(min(_margin_caps), 4)
        decision = {**decision, "margin_usdt": margin}
        print(f"[保证金闸门] {venue.upper()} {asset} 单笔保证金 {margin_clamped_from}U "
              f"超上限 {margin}U（权益占比 {max_margin_equity_ratio:.0%} / 单标的封顶 "
              f"{float(max_single_asset_margin or 0):.0f}U / 该所预算 "
              f"{float((pool or {}).get('margin_per_trade_usdt') or 0):.0f}U），已夹至上限")
    return margin, decision, margin_clamped_from


def check_total_exposure(
    *,
    venue: str,
    asset: str,
    action: str,
    margin: float,
    leverage: float,
    total_exposure_cap: Any,
    all_positions: Optional[List[Dict[str, Any]]],
    positions_reader: Callable[[], List[Dict[str, Any]]],
    fail_factory: Callable[..., Any],
) -> Optional[Any]:
    """跨所**同向**合并敞口超限则返回拒开结果；否则返回 `None`（表示放行）。

    ⚠️ 超限是**拒**不是**夹** —— 敞口超限意味着不该再开，静默缩量会让
    "为什么只开了一半"无从解释。

    `all_positions` 为 `None` 时用 `positions_reader()` 现取；取数抛异常 →
    返回 `fail_factory("exposure", ...)`（**不是** fail-open）。
    """
    exposure_cap = float(total_exposure_cap or 0.0)
    if exposure_cap <= 0:
        return None
    try:
        positions = all_positions if all_positions is not None else (positions_reader() or [])
    except Exception as exc:
        return fail_factory("exposure", f"无法读取持仓以核算跨所敞口: {exc}", venue=venue)
    same_side = 0.0
    #: 实际贡献同向敞口的**场所**集合（行由调用方打 `venue` 标签）。
    #: 这是"名字必须与实现相符"的可解释性证据：detail 里直接写明统计了哪些场所，
    #: 免得再出现"名叫跨所、实际只算一所"（2026-09-20 实测修复的那处）。
    contributing: List[str] = []
    for row in positions:
        if str(row.get("base") or "").upper() != asset:
            continue
        row_side = str(row.get("side") or "").lower()
        row_action = "buy" if row_side in ("long", "buy") else "sell" if row_side in ("short", "sell") else ""
        # ⚠️ **修正既有 bug**（结构优化阶段 4·B3 第三十八刀，抽取时发现）：
        # 调用方传进来的 `action` 是 `decision["action"].upper()`，即
        # `"BUY_LONG"` / `"SELL_SHORT"`；而 `row_action` 只有 `"buy"` / `"sell"`。
        # 原实现直接比较两者，**永远不相等** → 每条同向持仓都被 `continue` 跳过 →
        # `same_side` 恒为 0 → 敞口闸门**从未真正生效过**（是段死代码）。
        #
        # 修法：把两侧都归一到小写后比较，并**显式判定多空**（`BUY_LONG` 记 buy、
        # `SELL_SHORT` 记 sell）。**不改 `action` 本身** —— 它下游还要与
        # `"BUY_LONG"` 比较来决定 `side`（见 `open_protected_position`）。
        #
        # 实盘影响：生产 `R20_MAX_TOTAL_EXPOSURE_USDT` 未配置 → `TOTAL_EXPOSURE_CAP=0.0`
        # → 本闸门**仍然停用**，故修复不改变当前实盘行为；只有管理员显式配置了
        # 该上限时才会真正开始拦截（这正是该配置当初被加入的**本意**）。
        _a = str(action or "").lower()
        _want = "buy" if _a in ("buy_long", "buy", "long") else "sell" if _a in ("sell_short", "sell", "short") else ""
        if not _want or row_action != _want:
            continue
        _row_venue = str(row.get("venue") or "").strip().lower()
        if _row_venue and _row_venue not in contributing:
            contributing.append(_row_venue)
        same_side += abs(float(row.get("size_signed") or 0)) * float(row.get("mark_price") or row.get("entry_price") or 0)
    projected = same_side + margin * leverage
    if projected > exposure_cap:
        _src = f"（同向来自 {'/'.join(contributing)}）" if contributing else ""
        return fail_factory("exposure",
                            f"跨所同向敞口将达 {projected:.0f}U，超上限 {exposure_cap:.0f}U"
                            f"（已持有同向 {same_side:.0f}U{_src} + 本单名义 {margin * leverage:.0f}U）",
                            venue=venue, projected_exposure=round(projected, 2),
                            cap=exposure_cap, contributing_venues=list(contributing))
    return None

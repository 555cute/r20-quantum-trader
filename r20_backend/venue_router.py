"""US-002 可解释选所路由核心（venue_router）。

把一个信号路由到最合适的交易所：
- 第一层硬筛：执行开闸、listing gate、精度/最小量、行情新鲜度、预算；
- 第二层评分：价差 + 深度冲击 + 双腿手续费 + 按持仓周期估计的资金费
  + 稳定性惩罚 + 现任所 bonus，每因子分项写入 reasons（可解释）；
- 滞回防抖：挑战者领先幅度不足 hysteresis_pct 时保留现任所；
- 多所分配：显式开关（默认关），按评分反比拆分总预算。

设计铁律（测试封闭三律）：
- 纯函数：本模块不持有任何全局状态，滞回与分配所需上下文全部由参数传入
  （现任所在 signal/candidates 的 current_venue 字段里）；
- 零真实网络：listing 对账走 r20_backend.exchanges.listing.ensure_contract_listed
  （测试 patch 模块绑定名）；零凭证；零临时文件。
- 不真实下单，不触碰执行开关——本模块只做「选所决策」。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .exchanges import listing

#: listing fail-open 语义的注记关键词（与 listing.py 的 reason 措辞对齐）
_LISTING_FAILOPEN_MARK = "跳过对账"

#: 深度不足线性惩罚上限（bps）——depth_usd=0 时打满
DEPTH_PENALTY_MAX_BPS = 50.0

#: 现任所 bonus（bps）——滞回之外的成本侧粘性
INCUMBENT_BONUS_BPS = 5.0


@dataclass
class RouteDecision:
    """选所决策结果（全程可解释）。"""
    venue: Optional[str]                 # 中选所；None = 全部被硬筛淘汰
    reason_code: str                     # OK / OK_HYSTERESIS / ALL_REJECTED / NO_CANDIDATES
    reasons: List[str] = field(default_factory=list)
    rejected: List[Dict[str, str]] = field(default_factory=list)  # {venue,stage,reason}
    hysteresis_applied: bool = False
    allocation: Optional[List[Dict[str, Any]]] = None  # [{venue, amount_usdt}] | None


@dataclass
class RouterConfig:
    """路由配置（全部可由调用方覆盖；纯参数注入，无全局态）。"""
    holding_hours: float = 8.0          # 预计持仓周期（h），资金费按此估计
    funding_interval_hours: float = 8.0  # 资金费结算周期（h）
    hysteresis_pct: float = 0.15        # 滞回阈值（挑战者领先幅度比例）
    split_enabled: bool = False         # 多所分配显式开关（默认关）
    min_slice_usdt: float = 100.0       # 分配切片下限（以下不拆）
    depth_penalty_max_bps: float = DEPTH_PENALTY_MAX_BPS
    incumbent_bonus_bps: float = INCUMBENT_BONUS_BPS
    health_max_age_s_default: float = 900.0
    now_utc: Optional[str] = None       # 测试注入「当前时刻」（ISO8601）；None=真实时钟


def _coerce_config(config) -> RouterConfig:
    if config is None:
        return RouterConfig()
    if isinstance(config, RouterConfig):
        return config
    if isinstance(config, dict):
        return RouterConfig(**{
            k: v for k, v in config.items()
            if k in RouterConfig.__dataclass_fields__})
    raise TypeError(f"config 支持 RouterConfig/dict/None，收到 {type(config)!r}")


def _parse_iso_utc(ts: str) -> Optional[float]:
    try:
        s = str(ts).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def _now_epoch(cfg: RouterConfig) -> float:
    if cfg.now_utc:
        parsed = _parse_iso_utc(cfg.now_utc)
        if parsed is not None:
            return parsed
    return time.time()


# ---------------------------------------------------------------- 硬筛（第一层）

def _hard_filters(signal: Dict[str, Any], cand: Dict[str, Any],
                  cfg: RouterConfig, budget_view,
                  now_epoch: float = 0.0) -> List[str]:
    """返回该候选所的硬筛淘汰原因列表；空列表 = 通过。"""
    venue = str(cand.get("venue", "?"))
    fails: List[str] = []

    if cand.get("executable") is not True:
        fails.append("执行开闸关：executable=False")

    check = listing.ensure_contract_listed(
        venue, str(cand.get("environment", "live")),
        str(signal.get("inst_id") or signal.get("symbol_canonical") or ""))
    if not check.ok:
        fails.append(f"listing gate 拒：{check.reason}")
    elif check.reason and _LISTING_FAILOPEN_MARK in check.reason:
        # fail-open：不淘汰，但 reasons 注明（由调用方拼入 reasons）
        fails.append(f"__FAILOPEN__{check.reason}")

    size_usdt = float(signal.get("size_usdt") or 0.0)
    min_notional = float(cand.get("min_notional") or 0.0)
    if min_notional > 0 and size_usdt < min_notional:
        fails.append(f"最小名义额不足：size {size_usdt} < min_notional {min_notional}")

    # 精度/最小量：需要价格折算 qty；无价格时跳过步进核对（注记）
    price = signal.get("price") or cand.get("price")
    min_qty = float(cand.get("min_qty") or 0.0)
    precision = float(cand.get("precision") or 0.0)
    if price:
        qty = size_usdt / float(price)
        if min_qty > 0 and qty < min_qty:
            fails.append(f"最小量不足：qty {qty} < min_qty {min_qty}")
        if precision > 0:
            steps = qty / precision
            if abs(steps - round(steps)) > 1e-6:
                fails.append(
                    f"数量步进不符：qty {qty} 与精度 {precision} 不对齐")
    elif min_qty > 0 or precision > 0:
        fails.append("__NOTE__无价格无法核对最小量/步进（未淘汰）")

    updated = cand.get("health_updated_utc")
    max_age = float(cand.get("health_max_age_s") or cfg.health_max_age_s_default)
    if not updated:
        fails.append(f"健康新鲜度失效：无 health_updated_utc（上限 {max_age}s）")
    else:
        ts = _parse_iso_utc(updated)
        if ts is None:
            fails.append(f"健康新鲜度失效：health_updated_utc 无法解析（上限 {max_age}s）")
        elif (now_epoch - ts) > max_age:
            fails.append(
                f"健康新鲜度失效：数据年龄 {now_epoch - ts:.0f}s > 上限 {max_age}s")

    if budget_view is not None:
        available = getattr(budget_view, "available", None)
        if available is not None and size_usdt > float(available):
            fails.append(
                f"预算不足：size {size_usdt} > 可用 {float(available)}")

    return fails


# ---------------------------------------------------------------- 评分（第二层）

def _score(cand: Dict[str, Any], signal: Dict[str, Any],
           cfg: RouterConfig, reasons: List[str]) -> float:
    """成本评分（bps，越低越好）；每个因子分项写入 reasons。"""
    venue = str(cand.get("venue", "?"))
    size_usdt = float(signal.get("size_usdt") or 0.0)
    side = str(signal.get("side", "long")).lower()
    items: List[str] = []
    total = 0.0

    spread = float(cand.get("spread_bps") or 0.0)
    total += spread
    items.append(f"价差 {spread:.2f}bps")

    depth = float(cand.get("depth_usd") or 0.0)
    depth_need = size_usdt * 10.0
    if depth < depth_need:
        ratio = 0.0 if depth_need <= 0 else max(0.0, 1.0 - depth / depth_need)
        pen = ratio * cfg.depth_penalty_max_bps
        total += pen
        items.append(f"深度不足惩罚 {pen:.2f}bps（depth {depth:.0f} < 需求 {depth_need:.0f}）")
    else:
        items.append(f"深度充足（{depth:.0f} ≥ 需求 {depth_need:.0f}），无惩罚")

    fee_rate = float(cand.get("fee_rate") or 0.0)
    fee_bps = fee_rate * 2 * 10000.0  # open+close 两腿
    total += fee_bps
    items.append(f"手续费双腿 {fee_bps:.2f}bps（rate {fee_rate} ×2 腿）")

    funding = float(cand.get("funding_rate") or 0.0)
    periods = cfg.holding_hours / cfg.funding_interval_hours
    # 方向语义：多头在正资金费时付费；空头反向（可能收费）
    funding_bps = funding * periods * (1.0 if side == "long" else -1.0) * 10000.0
    total += funding_bps
    items.append(
        f"资金费估计 {funding_bps:.2f}bps（rate {funding} × {periods:.2f} 期，{side} 方向）")

    stability = float(cand.get("stability_penalty") or 0.0)
    total += stability
    items.append(f"稳定性惩罚 {stability:.2f}bps")

    if cand.get("current_venue"):
        total -= cfg.incumbent_bonus_bps
        items.append(f"现任所 bonus -{cfg.incumbent_bonus_bps:.2f}bps")

    reasons.append(f"[{venue}] 评分 {total:.2f}bps：" + "；".join(items))
    return total


# ---------------------------------------------------------------- 滞回 + 分配

def _apply_hysteresis(candidates: List[Dict[str, Any]],
                      scores: Dict[str, float], cfg: RouterConfig,
                      reasons: List[str]) -> tuple:
    """返回 (winner, hysteresis_applied)。现任所在场且挑战者领先不足时保留现任。"""
    best = min(scores, key=lambda v: scores[v])
    incumbent = next(
        (str(c.get("venue")) for c in candidates if c.get("current_venue")), None)
    if (incumbent is not None and incumbent in scores and incumbent != best):
        inc_score = scores[incumbent]
        best_score = scores[best]
        denom = max(abs(inc_score), 1e-9)
        lead = (inc_score - best_score) / denom
        if lead < cfg.hysteresis_pct:
            reasons.append(
                f"滞回防抖：挑战者 {best} 仅领先 {lead:.1%} < 阈值 "
                f"{cfg.hysteresis_pct:.0%}，保留现任所 {incumbent}")
            return incumbent, True
    return best, False


def split_allocation(signal: Dict[str, Any], candidates: List[Dict[str, Any]],
                     budget, config=None, pre_alive: Optional[List[Dict[str, Any]]] = None) -> Optional[List[Dict[str, Any]]]:
    """多所分配（显式开关，默认关）。

    - split_enabled=False → 永远单所（返回 None，由主路由单所执行）；
    - True → 在通过硬筛的候选中按评分反比拆分总预算
      （min_slice_usdt 以下切片丢弃）；不足两个有效切片 → 单所（None）。
    pre_alive：主路由已算好的硬筛通过集（避免重复 listing 对账）；独立调用时为 None。
    """
    cfg = _coerce_config(config)
    if not cfg.split_enabled:
        return None

    if pre_alive is not None:
        eligible = list(pre_alive)
    else:
        now_epoch = _now_epoch(cfg)
        eligible = []
        for cand in candidates:
            fails = [f for f in _hard_filters(signal, cand, cfg, budget, now_epoch)
                     if not f.startswith("__")]
            if not fails:
                eligible.append(cand)
    if not eligible:
        return None

    total = float(signal.get("size_usdt") or 0.0)
    if budget is not None:
        available = getattr(budget, "available", None)
        if available is not None:
            total = min(total, float(available))
    if total <= 0:
        return None

    tmp_reasons: List[str] = []
    scores = {str(c["venue"]): _score(c, signal, cfg, tmp_reasons)
              for c in eligible}
    # 评分反比权重（成本低者权重高；评分 ≤0 时加地板防负权重）
    floor = (min(scores.values()) if scores else 0.0)
    weights = {v: 1.0 / (s - floor + 1.0) for v, s in scores.items()}
    wsum = sum(weights.values())

    slices = [{"venue": v, "amount_usdt": round(total * w / wsum, 2)}
              for v, w in weights.items()]
    slices = [s for s in slices if s["amount_usdt"] >= cfg.min_slice_usdt]
    if len(slices) < 2:
        return None  # 拆不出 ≥2 个有效切片 → 单所执行
    return slices


# ---------------------------------------------------------------- 主入口

def route_signal(signal: Dict[str, Any], candidates: List[Dict[str, Any]],
                 budget_view=None, config=None) -> RouteDecision:
    """可解释选所路由：硬筛 → 评分 → 滞回 → （可选）多所分配。"""
    cfg = _coerce_config(config)
    reasons: List[str] = []
    rejected: List[Dict[str, str]] = []
    now_epoch = _now_epoch(cfg)

    if not candidates:
        return RouteDecision(venue=None, reason_code="NO_CANDIDATES",
                             reasons=["无候选所"], rejected=[])

    for cand in candidates:
        venue = str(cand.get("venue", "?"))
        fails = _hard_filters(signal, cand, cfg, budget_view, now_epoch)
        for f in fails:
            if f.startswith("__FAILOPEN__"):
                reasons.append(f"[{venue}] listing fail-open：{f[len('__FAILOPEN__'):]}，不淘汰")
            elif f.startswith("__NOTE__"):
                reasons.append(f"[{venue}] {f[len('__NOTE__'):]}")
            else:
                rejected.append({"venue": venue, "stage": _stage_of(f), "reason": f})

    alive = [c for c in candidates
             if not any(r["venue"] == str(c.get("venue")) for r in rejected)]
    if not alive:
        return RouteDecision(venue=None, reason_code="ALL_REJECTED",
                             reasons=["所有候选所均被硬筛淘汰"],
                             rejected=rejected)

    scores = {str(c["venue"]): _score(c, signal, cfg, reasons) for c in alive}
    winner, hyst = _apply_hysteresis(alive, scores, cfg, reasons)
    reasons.append(f"选中 {winner}（成本 {scores[winner]:.2f}bps，最低者"
                   + ("，经滞回保留现任" if hyst else "）"))

    allocation = split_allocation(signal, alive, budget_view, cfg,
                                  pre_alive=alive)
    if allocation is not None:
        reasons.append("多所分配开启：按评分反比拆分预算 "
                       + ", ".join(f"{s['venue']}={s['amount_usdt']}" for s in allocation))

    return RouteDecision(
        venue=winner,
        reason_code="OK_HYSTERESIS" if hyst else "OK",
        reasons=reasons,
        rejected=rejected,
        hysteresis_applied=hyst,
        allocation=allocation,
    )


def _stage_of(fail_reason: str) -> str:
    if "开闸" in fail_reason:
        return "executable"
    if "listing" in fail_reason:
        return "listing"
    if "名义额" in fail_reason or "最小量" in fail_reason or "步进" in fail_reason:
        return "precision"
    if "新鲜度" in fail_reason:
        return "freshness"
    if "预算" in fail_reason:
        return "budget"
    return "unknown"

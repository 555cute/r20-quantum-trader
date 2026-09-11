"""多所 6 账户权益聚合与组合风险隔离引擎（US-006）。

设计原则与铁律：
1. 严格分区：demo 与 live 两环境绝对物理隔离，严禁跨环境混合相加。
2. 未知≠0：读不到或非 ready 状态的交易所不计入聚合分母，同时在 reporting_venues 中明确标示参与所。
3. 纯函数：本模块不持有全局锁或网络 IO，便于端点复用与 100% 封闭单测。
4. 资产分布与风控利用率：
   - asset_distribution: 各所在当前环境总权益中的占比 (0.0 ~ 100.0%)
   - portfolio_risk_utilization: 保证金占用额与利用率百分比，给出 LOW/MEDIUM/HIGH 风险级别。
"""
from __future__ import annotations

from typing import Any, Dict, List


def aggregate_venue_accounts(venues_dict: Dict[str, Any], environment: str) -> Dict[str, Any]:
    """按资金环境聚合 OKX / Binance / Gate 权益与组合风险。

    入参：
    - venues_dict: {"okx": {...}, "binance": {...}, "gate": {...}} 各所只读卡片
    - environment: "demo" | "live"
    """
    env = str(environment or "").strip().lower()
    if env not in ("demo", "live"):
        raise ValueError(f"environment 必须为 'demo' 或 'live'，收到: {environment!r}")

    total_equity = 0.0
    total_available = 0.0
    total_positions = 0
    total_open_orders = 0
    active_venues: List[str] = []
    venue_equities: Dict[str, float] = {}

    target_venues = ("okx", "binance", "gate")
    for v in target_venues:
        card = venues_dict.get(v)
        if not isinstance(card, dict):
            continue
        # 仅当状态为 ready 且具有有效数值时计入聚合
        if card.get("status") == "ready":
            eq = card.get("equity")
            avail = card.get("available")
            if eq is not None and isinstance(eq, (int, float)) and eq >= 0:
                eq_val = float(eq)
                total_equity += eq_val
                venue_equities[v] = eq_val
                active_venues.append(v)
            if avail is not None and isinstance(avail, (int, float)) and avail >= 0:
                total_available += float(avail)
            pos_cnt = card.get("positions_count")
            if isinstance(pos_cnt, int) and pos_cnt >= 0:
                total_positions += pos_cnt
            ord_cnt = card.get("open_orders_count")
            if isinstance(ord_cnt, int) and ord_cnt >= 0:
                total_open_orders += ord_cnt

    total_equity = round(total_equity, 2)
    total_available = round(total_available, 2)
    margin_used = round(max(0.0, total_equity - total_available), 2)
    utilization_pct = round((margin_used / total_equity) * 100.0, 2) if total_equity > 0 else 0.0

    # 风险等级判定
    if utilization_pct > 70.0:
        risk_level = "HIGH"
    elif utilization_pct > 30.0:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    # 资产分布百分比
    distribution: Dict[str, Dict[str, float]] = {}
    for v in target_venues:
        v_eq = venue_equities.get(v, 0.0)
        share = round((v_eq / total_equity) * 100.0, 2) if total_equity > 0 else 0.0
        distribution[v] = {
            "equity": round(v_eq, 2),
            "share_pct": share,
        }

    return {
        "environment": env,
        "total_equity": total_equity,
        "total_available": total_available,
        "margin_used": margin_used,
        "utilization_pct": utilization_pct,
        "risk_level": risk_level,
        "positions_count": total_positions,
        "open_orders_count": total_open_orders,
        "active_venues_count": len(active_venues),
        "reporting_venues": active_venues,
        "asset_distribution": distribution,
    }

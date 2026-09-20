"""市场/组合数据装配（结构优化阶段 2 / B2 第二刀）。

从 r20_backend/dashboard_cache.py 迁出的**无接缝依赖**的一组函数：它们不读 LOG_FILE / AI_*_FILE /
STATE_JSON_FILE / DATA_DIR / CACHE_DATA 等会被测试 patch 的门面常量，只做纯计算或
走 scripts.okx_rest，故门面直接重导出即可（无需薄壳）。
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from scripts import okx_rest
from scripts.instrument_pool import load_instruments

__all__ = [
    "get_target_instruments",
    "_TRADER_CYCLE_MINUTES_MEMO",
    "_trader_cycle_minutes",
    "_safe_float",
    "_is_meaningful_dashboard_snapshot",
    "_global_env_axis",
    "_load_portfolio_risk_data",
    "_load_multi_venue_portfolio",
]


def get_target_instruments() -> list[dict[str, Any]]:
    return load_instruments()


_TRADER_CYCLE_MINUTES_MEMO: list = []   # 非 None 才缓存（trader 周期是代码常量）


def _trader_cycle_minutes():
    """网关调度器 trader 作业的真实周期（分钟）；不可得返回 None。

    批B(2026-09-13)：前台曾把「决策周期 15 分钟」写死当事实展示（后端降频/改周期后
    照旧宣称）。单一事实源=调度器 JobSpec，此处读真值；任何异常一律 None，由前端
    决定不渲染——宁缺勿假。
    """
    if _TRADER_CYCLE_MINUTES_MEMO:
        return _TRADER_CYCLE_MINUTES_MEMO[0]
    try:
        from r20_gateway.scheduler import current_jobs
        for _j in current_jobs():
            if str(getattr(_j, "name", "")) == "trader":
                _iv = getattr(_j, "interval_seconds", None)
                if _iv:
                    _m = max(1, int(round(int(_iv) / 60)))
                    _TRADER_CYCLE_MINUTES_MEMO.append(_m)
                    return _m
                break
    except Exception:
        pass
    return None


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_meaningful_dashboard_snapshot(data):
    return isinstance(data, dict) and isinstance(data.get("account"), dict) and bool(data.get("account")) and "total_eq" in data["account"]


def _global_env_axis() -> str:
    """审计 B2：资金/行情环境轴统一走全站唯一事实源（R20_OKX_ENV → okx_runtime），
    不再私读遗留 OKX_IS_SIMULATED——两轴失同步时曾把 LIVE 所数据并进 DEMO 板。
    解析失败保守取 demo（与 okx_runtime 未知档默认一致，绝不抬到 live）。"""
    try:
        from scripts.okx_runtime import current_environment
        return str(current_environment().mode)
    except Exception:
        return "demo"


def _load_portfolio_risk_data() -> dict:
    """透传组合风险预留层状态给前台三所面板（零网络，纯本地只读）。

    契约对齐（2026-09-11 修复面板恒显「—」）：
    - 字段名对齐前端 PortfolioRiskRow：total_budget_usdt / reserved_usdt /
      available_usdt / environment / updated_utc；
    - 资金环境轴与 _load_multi_venue_portfolio 同源（_global_env_axis →
      okx_runtime 单源，2026-09-13 审计 B2 起不再读遗留 OKX_IS_SIMULATED）；
    - 总预算单源 R20_PORTFOLIO_RISK_BUDGET_USDT（与 trader 同口径）；未配置
      = 无上限模式 → 诚实 None（前端显「—」），绝不编 10000 假预算；
    - 读层异常不再裸吞成 {}：返回 status=unavailable + 空值结构，error 留痕。
    """
    try:
        budget_raw = str(os.environ.get("R20_PORTFOLIO_RISK_BUDGET_USDT") or "").strip()
        try:
            budget_val = float(budget_raw) if budget_raw else 0.0
        except ValueError:
            budget_val = 0.0

        # 审计 P1-6(2026-09-13)：env=0 的语义是「引擎不封顶」（risk_constants 自注：派生
        # 公式只存在于 UI 展示，属展示启发式而非引擎策略）。旧实现在这里编出
        # max_pos × MAX_SINGLE_ASSET_MARGIN = 8×600 = 4800（异常还兜 5000），前端据此画
        # 占用率进度条 —— 管理员"以为有 4800 的总闸，实际引擎没有总闸"。
        # 现在：未配置 → total_budget_usdt/available/utilization 一律 null（前端显「—」），
        # 派生值只作为展示参考单独返回，绝不冒充预算；Configured 时才给真实数值。
        reference_cap = None
        if budget_val <= 0:
            try:
                from scripts.risk_constants import MAX_CONCURRENT_POSITIONS_CAP, MAX_SINGLE_ASSET_MARGIN
                from scripts.instrument_pool import load_instruments
                pool_len = len(load_instruments() or []) or 8
                max_pos = MAX_CONCURRENT_POSITIONS_CAP if MAX_CONCURRENT_POSITIONS_CAP > 0 else pool_len
                reference_cap = round(float(max_pos) * float(MAX_SINGLE_ASSET_MARGIN or 0.0), 4)
            except Exception:
                reference_cap = None

        budget_mode = "configured" if budget_val > 0 else "uncapped"
        total_budget = budget_val if budget_val > 0 else None
        env = _global_env_axis()
        from r20_backend.risk_reservation import get_manager
        mgr = get_manager()
        reserved = float(mgr.gross_exposure(env) or 0.0)
        by_venue = mgr.total_reserved_by_venue(env)
        avail = round(max(0.0, total_budget - reserved), 4) if total_budget is not None else None
        utilization = round((reserved / total_budget) * 100.0, 1) if total_budget else None
        return {
            "environment": env,
            "status": "ok",
            "budget_mode": budget_mode,
            "total_budget_usdt": round(total_budget, 4) if total_budget is not None else None,
            "reference_cap_usdt": reference_cap,
            "reserved_usdt": round(reserved, 4),
            "available_usdt": avail,
            "utilization_pct": utilization,
            "by_venue": {str(k): round(float(v), 4) for k, v in by_venue.items()},
            "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    except Exception as exc:
        return {
            "environment": _global_env_axis(),
            "status": "unavailable",
            "budget_mode": None,
            "total_budget_usdt": None, "reference_cap_usdt": None, "reserved_usdt": None,
            "available_usdt": None, "utilization_pct": None, "by_venue": {},
            "error": str(exc)[:160],
        }


def _load_multi_venue_portfolio(total_eq: float, avail_eq: float, positions: list, orders: list) -> dict:
    """US-006/US-007：dashboard /api/all 组合多所资产与权益快照（OKX + Gate + Binance 全量对账）。"""
    try:
        from r20_backend.portfolio_aggregator import aggregate_venue_accounts
        env = _global_env_axis()
        venues_map = {
            "okx": {
                "status": "ready" if total_eq > 0 else "unavailable",
                "equity": total_eq,
                "available": avail_eq,
                "positions_count": len(positions) if isinstance(positions, list) else 0,
                "open_orders_count": len(orders) if isinstance(orders, list) else 0,
            },
        }
        try:
            # 审计 A1：33cc95d 拆分把两函数移入 routers/exchanges.py，此处旧引用
            # ImportError 被吞 → gate/binance 永远伪报 unavailable。改指真源。
            from r20_backend.routers.exchanges import _venue_accounts_gate, _venue_accounts_binance
        except Exception as exc:
            venues_map["gate"] = {"status": "unavailable", "equity": None, "reason": f"账户模块缺失: {exc}"}
            venues_map["binance"] = {"status": "unavailable", "equity": None, "reason": f"账户模块缺失: {exc}"}
        else:
            try:
                venues_map["gate"] = _venue_accounts_gate(env)
            except Exception as exc:
                venues_map["gate"] = {"status": "unavailable", "equity": None, "reason": f"Gate 账户面异常: {str(exc)[:180]}"}
            try:
                venues_map["binance"] = _venue_accounts_binance(env)
            except Exception as exc:
                venues_map["binance"] = {"status": "unavailable", "equity": None, "reason": f"Binance 账户面异常: {str(exc)[:180]}"}

        return aggregate_venue_accounts(venues_map, env)
    except Exception as exc:      # noqa: BLE001 - 面板侧不得因一处异常炸掉整个载荷
        # 第 52 刀：原先静默 `return {}` ⇒ 面板把"多所组合读取失败"渲染成**空组合**
        # （读者会以为"没有跨所仓位"）。返回空值不变，但必须披露。
        print(f"[面板] warn 多所组合读取失败: {exc!r}（本次将显示为空组合 —— "
              "请勿据此判断\"没有跨所仓位\"）")
        return {}

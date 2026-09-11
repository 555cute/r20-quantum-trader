"""多所分流策略（单一事实文件 data/venue_routing.json）。

安全设计：
- 默认空池 + dry_run=true——不开配置什么都不会发生；
- gate 池的币即使配了，也必须同时满足**执行许可开关**（live 档
  R20_GATE_EXECUTION / 沙盒档 R20_GATE_DEMO_EXECUTION，US-002 双轴）
  与凭证就绪才会真实下单，否则自动降级为 dry_run（fail-safe 向安全侧收敛）；
- 本模块只读配置与状态，永不写交易所。

两条独立轴（US-002 命名拆分，纠正旧「dry_run=false 名称变 live」混淆）：
- **本地执行模式**（pool.dry_run）：演算不发单 vs 真实发送；
- **交易所资金环境**（R20_GATE_TESTNET 档位轴）：live 实盘 vs demo 模拟盘。
effective_mode 输出 {off | dry_run | demo | live}：gate 沙盒档 + 执行开闸 =
真实发送**模拟盘订单**，标注 demo——依时效审计字面「Gate demo + enabled 是
真实发送模拟盘订单，绝不能标成 LIVE 实盘」。旧配置无字段、无新开关时
输出与现状逐位一致（无 TESTNET 布尔 → live 轴）。

配置形状：
{
  "preferred_venue": "auto",            # US-003 手动选所优先：okx|binance|gate|auto
                                        # （缺字段/非法值 → 回退 auto + warn）
  "gate": {
    "assets": ["BTC"],                 # 准入币种（canonical 裸币名）
    "margin_per_trade_usdt": 50.0,     # 每笔保证金上限（该所独立预算）
    "max_open": 2,                     # 同时最大持仓笔数
    "min_confidence": 80.0,            # 决策置信度门禁（与主链同尺）
    "dry_run": true                    # true=全链路演算不发单（本地轴）
  }
}
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from .registry import (execution_open, gate_environment_axis,
                       registered_venues)

ROOT = Path(__file__).resolve().parents[2]
ROUTING_FILE = ROOT / "data" / "venue_routing.json"

DEFAULT_GATE_POOL: Dict[str, Any] = {
    "assets": [],
    "margin_per_trade_usdt": 50.0,
    "max_open": 2,
    "min_confidence": 80.0,
    "dry_run": True,
}


def load_gate_pool() -> Dict[str, Any]:
    pool = dict(DEFAULT_GATE_POOL)
    try:
        if ROUTING_FILE.exists():
            raw = json.loads(ROUTING_FILE.read_text(encoding="utf-8"))
            gate = raw.get("gate") or {}
            if isinstance(gate, dict):
                for k in DEFAULT_GATE_POOL:
                    if k in gate:
                        pool[k] = gate[k]
    except Exception:
        pass
    assets = [str(a).upper() for a in (pool.get("assets") or []) if str(a).strip()]
    pool["assets"] = sorted(set(assets))
    try:
        pool["margin_per_trade_usdt"] = max(0.0, float(pool.get("margin_per_trade_usdt") or 0))
        pool["max_open"] = max(0, int(pool.get("max_open") or 0))
        pool["min_confidence"] = min(100.0, max(0.0, float(pool.get("min_confidence") or 80.0)))
    except (TypeError, ValueError):
        pass
    # fail-safe：执行未开闸（按当前资金环境轴判定）或凭证缺失 → 强制 dry_run
    if not _gate_execution_ready():
        pool["dry_run"] = True
    return pool


def _gate_execution_ready() -> bool:
    """真实发送前置条件：当前资金环境轴的执行许可开闸 + 凭证就绪。

    轴取法（US-002 双轴）：live 轴查 R20_GATE_EXECUTION（语义与旧逐字节一致）；
    demo 轴（R20_GATE_TESTNET=1 沙盒档）查独立开关 R20_GATE_DEMO_EXECUTION——
    两把闸互不越权，任一缺失 fail-closed 向 dry_run 收敛。
    """
    axis = gate_environment_axis()
    if not execution_open("gate", axis):
        return False
    try:
        from r20_gateway.secrets import load_secrets
        vals = load_secrets()
        return bool(vals.get("GATE_API_KEY") and vals.get("GATE_SECRET_KEY"))
    except Exception:
        return False


def _read_raw_routing() -> Dict[str, Any]:
    """原始配置 dict（读不到/损坏 → 空 dict；永不抛穿）。"""
    try:
        if ROUTING_FILE.exists():
            raw = json.loads(ROUTING_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
    except Exception:
        pass
    return {}


#: 手动选所合法值 = 注册表已登记场所 + auto（不硬编码场所名单，新所登记即生效）
VALID_PREFERRED_VENUES = tuple(sorted(set(registered_venues()) | {"auto"}))

#: 选所路由模式（架构 A/B/C 三档；split 执行面接线前仅在证据中给出拆单方案）
VALID_ROUTING_MODES = ("auto", "balanced", "split")


def load_preferred_venue(raw: Dict[str, Any] = None) -> str:
    """US-003 手动选所优先项：顶层 preferred_venue ∈ {okx,binance,gate,auto}。

    向后兼容铁律：老配置文件没有该键 → 返回 'auto'（评分路由，行为与 US-003 前
    逐位一致）；非法值 → warn + 回退 'auto'（fail-safe：宁可回到评分路由，
    绝不因为一个写错的配置字符串把交易链断掉，也绝不猜某个所）。
    """
    data = _read_raw_routing() if raw is None else raw
    if "preferred_venue" not in data:
        print("[venue_routing] warn: 配置缺 preferred_venue 字段，回退 auto（评分路由）")
        return "auto"
    value = data.get("preferred_venue")
    key = str(value or "").strip().lower()
    if key in VALID_PREFERRED_VENUES:
        return key
    print(f"[venue_routing] warn: preferred_venue 非法值 {value!r}"
          f"（允许 {list(VALID_PREFERRED_VENUES)}），回退 auto")
    return "auto"


def load_routing_mode(raw: Dict[str, Any] = None) -> str:
    """选所路由模式：'auto'(最优执行B) | 'balanced'(均衡轮换A) | 'split'(资金拆分C)。

    非法值/缺失回退 'balanced'（三所平权改造后的系统基线）并 warn——
    与 preferred_venue 同族 fail-safe：配置写错绝不阻断交易链，也绝不猜。
    """
    data = _read_raw_routing() if raw is None else raw
    key = str(data.get("routing_mode") or "").strip().lower()
    if key in VALID_ROUTING_MODES:
        return key
    if key:
        print(f"[venue_routing] warn: routing_mode 非法值 {data.get('routing_mode')!r}"
              f"（允许 {list(VALID_ROUTING_MODES)}），回退 balanced")
    else:
        print("[venue_routing] warn: 配置缺 routing_mode 字段，回退 balanced（均衡轮动基线）")
    return "balanced"


def save_routing_mode(mode: str) -> bool:
    """写顶层 routing_mode（读-改-写原子替换，其余键原样保留）。"""
    key = str(mode or "").strip().lower()
    if key not in VALID_ROUTING_MODES:
        print(f"[venue_routing] 拒绝写入非法 routing_mode: {mode!r}")
        return False
    data = _read_raw_routing()
    data["routing_mode"] = key
    tmp = ROUTING_FILE.with_suffix(".json.tmp")
    try:
        ROUTING_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        os.replace(tmp, ROUTING_FILE)
        return True
    except Exception as exc:
        print(f"[venue_routing] 写盘失败（不改动原配置）: {exc}")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


def save_preferred_venue(venue: str) -> bool:
    """写顶层 preferred_venue（读-改-写原子替换，gate 等其余键原样保留）。

    老 writer 兼容性：本函数只新增/覆盖一个顶层键，不改 'gate' 子树形状，
    load_gate_pool 逐键取默认表内的字段，多余键忽略——双向都不崩。
    """
    key = str(venue or "").strip().lower()
    if key not in VALID_PREFERRED_VENUES:
        print(f"[venue_routing] 拒绝写入非法 preferred_venue: {venue!r}")
        return False
    data = _read_raw_routing()
    data["preferred_venue"] = key
    tmp = ROUTING_FILE.with_suffix(".json.tmp")
    try:
        ROUTING_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        os.replace(tmp, ROUTING_FILE)
        return True
    except Exception as exc:
        print(f"[venue_routing] 写盘失败（不改动原配置）: {exc}")
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


def gate_execution_axis() -> str:
    """对外披露当前资金环境轴（live|demo），供面板/审计显示，纯读。"""
    return gate_environment_axis()


def gate_pool_assets() -> List[str]:
    return list(load_gate_pool()["assets"])


def effective_mode() -> str:
    """'off'（无池）| 'dry_run'（本地演算，不涉所环境）| 'demo'（模拟盘真实发送）
    | 'live'（实盘验田真实发送）。

    dry_run 是**本地行为轴**；demo/live 是**交易所资金环境轴**——Gate 沙盒档 +
    执行开闸 = 真实发送模拟盘订单，标 demo 绝不标 LIVE（时效审计字面）。
    兼容钉：无 R20_GATE_TESTNET 布尔时轴恒为 live，旧配置输出与现状逐位一致。
    """
    pool = load_gate_pool()
    if not pool["assets"]:
        return "off"
    if pool["dry_run"]:
        return "dry_run"
    return "demo" if gate_environment_axis() == "demo" else "live"

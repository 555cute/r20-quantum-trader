"""多所分流策略（Gate 平行试验田配置源，单一事实文件 data/venue_routing.json）。

安全设计：
- 默认空池 + dry_run=true——不开配置什么都不会发生；
- gate 池的币即使配了，也必须同时满足 execution_open(R20_GATE_EXECUTION=1)
  与凭证就绪才会真实下单，否则自动降级为 dry_run（fail-safe 向安全侧收敛）；
- 本模块只读配置与状态，永不写交易所。

配置形状：
{
  "gate": {
    "assets": ["BTC"],                 # 试验田币种（canonical 裸币名）
    "margin_per_trade_usdt": 50.0,     # 每笔保证金上限（试验田独立预算）
    "max_open": 2,                     # 同时最大持仓笔数
    "min_confidence": 80.0,            # 决策置信度门禁（与主链同尺）
    "dry_run": true                    # true=全链路演算不发单
  }
}
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from .registry import execution_open

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
    # fail-safe：执行未开闸或凭证缺失 → 强制 dry_run
    if not _gate_live_ready():
        pool["dry_run"] = True
    return pool


def _gate_live_ready() -> bool:
    if not execution_open("gate"):
        return False
    try:
        from r20_gateway.secrets import load_secrets
        vals = load_secrets()
        return bool(vals.get("GATE_API_KEY") and vals.get("GATE_SECRET_KEY"))
    except Exception:
        return False


def gate_pool_assets() -> List[str]:
    return list(load_gate_pool()["assets"])


def effective_mode() -> str:
    """'off'（无池） | 'dry_run'（演算） | 'live'（真实验田下单）"""
    pool = load_gate_pool()
    if not pool["assets"]:
        return "off"
    return "dry_run" if pool["dry_run"] else "live"

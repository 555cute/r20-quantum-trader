"""环境维合约存在性对账（三所对等化 P0 · US-007）。

真因案例（2026-09-10）：OKX demo 已下架 SUI-USDT-SWAP，下单报
"Listing canceled for this crypto"——**模拟盘合约集合 ≠ 实盘，且随时变动**。
本模块用各所公共目录（零凭证、零签名、零写请求）在下单前做存在性对账，
把这类失败前置为结构化拒绝 + 中文原因。

铁律（契约，测试逐条钉）：
1. 只发公共端点：OKX ``/api/v5/public/instruments``、Binance ``/fapi/v1/exchangeInfo``、
   Gate ``/api/v4/futures/usdt/contracts``。域名/档位一律经
   ``env_profiles.resolve_base_url``（Gate 沙盒双域择优/持久化/全不可达
   fail-closed 语义随之继承，绝不跨环境回退）。
2. 目录**拉取失败 → fail-open**：返回 ok=True + reason 注明跳过——对账是
   增强不是风控闸门，绝不冒充风控（COORDINATION_DESIGN §5）。但 resolve
   抛出的沙盒 fail-closed 属环境档不可用，同样按 fail-open 处理并留原因。
3. 缓存 TTL 600s，key=(venue, environment)；提供 ``reset_cache_for_testing``。
4. trader 侧接入（下单前调用）由后续故事完成——本模块只交付对账能力本体。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.request import Request, urlopen

from .env_profiles import get_profile, resolve_base_url

#: 目录缓存 TTL（秒）。测试可 patch 本值（或注入 _now）。
LISTING_TTL_SECONDS = 600.0
#: 单次目录拉取超时（秒）
LISTING_TIMEOUT_SECONDS = 6.0

_UA = "R20-listing-gate/1.0"


@dataclass(frozen=True)
class ListingCheck:
    venue: str
    environment: str
    contract: str
    ok: bool
    reason: Optional[str]        # None = 放行；否则中文原因
    checked_at: float            # epoch 秒
    source: str                  # 'cache' | 'fresh' | 'skip-unavailable'


_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}
#: 测试可注入时钟（time.time 默认）
_now = time.time


def reset_cache_for_testing() -> None:
    """测试专用：清空进程内目录缓存。"""
    _CACHE.clear()


# ----------------------------------------------------------------------
# 各所目录拉取与判定
# ----------------------------------------------------------------------
def _fetch_dir_okx(base: str, simulated: bool) -> Dict[str, Tuple[bool, str]]:
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    if simulated:
        headers["x-simulated-trading"] = "1"   # demo 同域头开关（env_profiles 结构位）
    req = Request(base + "/api/v5/public/instruments?instType=SWAP",
                  headers=headers, method="GET")
    with urlopen(req, timeout=LISTING_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8") or "{}")
    rows = payload.get("data") or [] if isinstance(payload, dict) else []
    out: Dict[str, Tuple[bool, str]] = {}
    for row in rows:
        inst = str(row.get("instId") or "")
        if not inst:
            continue
        state = str(row.get("state") or "")
        out[inst] = ((state == "live"), (None if state == "live" else f"合约状态 {state or '未知'}（非交易中）"))
    return out


def _fetch_dir_binance(base: str, simulated: bool) -> Dict[str, Tuple[bool, str]]:
    req = Request(base + "/fapi/v1/exchangeInfo",
                  headers={"User-Agent": _UA, "Accept": "application/json"}, method="GET")
    with urlopen(req, timeout=LISTING_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8") or "{}")
    out: Dict[str, Tuple[bool, str]] = {}
    for row in (payload.get("symbols") or []):
        sym = str(row.get("symbol") or "")
        if not sym:
            continue
        status = str(row.get("status") or "")
        out[sym] = ((status == "TRADING"), (None if status == "TRADING" else f"合约状态 {status or '未知'}（非交易中）"))
    return out


def _fetch_dir_gate(base: str, simulated: bool) -> Dict[str, Tuple[bool, str]]:
    req = Request(base + "/api/v4/futures/usdt/contracts",
                  headers={"User-Agent": _UA, "Accept": "application/json"}, method="GET")
    with urlopen(req, timeout=LISTING_TIMEOUT_SECONDS) as resp:
        rows = json.loads(resp.read().decode("utf-8") or "[]")
    out: Dict[str, Tuple[bool, str]] = {}
    for row in (rows if isinstance(rows, list) else []):
        name = str(row.get("name") or "")
        if not name:
            continue
        delisting = bool(row.get("in_delisting"))
        out[name] = ((not delisting), (None if not delisting else "合约已进入下架流程（in_delisting）"))
    return out


_FETCHERS = {"okx": _fetch_dir_okx, "binance": _fetch_dir_binance, "gate": _fetch_dir_gate}


def _load_directory(venue: str, environment: str) -> Dict[str, Tuple[bool, str]]:
    """(venue, environment) → {合约: (可交易, 原因)}。域名解析失败/拉取失败向上抛。"""
    prof = get_profile(venue, environment)
    base = resolve_base_url(prof.venue, prof.environment)
    return _FETCHERS[prof.venue](base, prof.simulated_trading)


def ensure_contract_listed(venue: str, environment: str, contract_native: str) -> ListingCheck:
    """对账合约在 (venue, environment) 目录中是否可交易。零凭证、零写、失败 fail-open。"""
    v = str(venue).strip().lower()
    e = str(environment).strip().lower()
    contract = str(contract_native or "").strip().upper()
    checked = _now()
    if v not in _FETCHERS:
        return ListingCheck(v, e, contract, True, f"未知 venue={venue}，跳过对账", checked, "skip-unavailable")

    key = (v, e)
    entry = _CACHE.get(key)
    if entry is not None and (checked - float(entry["ts"])) <= LISTING_TTL_SECONDS:
        return _judge(v, e, contract, entry["dir"], checked, "cache")

    try:
        directory = _load_directory(v, e)
    except Exception as exc:                      # 目录不可用 → fail-open（增强非闸门）
        return ListingCheck(v, e, contract, True,
                            f"行情目录不可用，跳过对账：{type(exc).__name__}: {exc}"[:220],
                            checked, "skip-unavailable")
    _CACHE[key] = {"ts": checked, "dir": directory}
    return _judge(v, e, contract, directory, checked, "fresh")


def _judge(venue: str, environment: str, contract: str,
           directory: Dict[str, Tuple[bool, str]], checked: float, source: str) -> ListingCheck:
    hit = directory.get(contract)
    if hit is None:
        return ListingCheck(venue, environment, contract, False,
                            "合约未在目录中（可能已下架或未上市）", checked, source)
    ok, reason = hit
    return ListingCheck(venue, environment, contract, ok, reason, checked, source)

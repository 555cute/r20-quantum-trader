"""US-007 环境维合约存在性对账（listing gate）。

真实案例：OKX demo 已下架 SUI-USDT-SWAP，下单报
``'Listing canceled for this crypto'``——下单前用本模块按
(venue, environment) 对**该环境的合约目录**做存在性/状态对账，
把「已下架/未上市」在发送前拦下。

设计铁律：
- 纯检查模块：只调公共目录端点（零凭证、零写请求），严禁任何私有端点，
  绝不真实下单；
- 缓存 = 进程内 dict，key=(venue, environment)，TTL 600s；
- 判定：不存在 / OKX state!=live / Binance status!=TRADING / Gate
  in_delisting=true → ok=False + 中文 reason；
- 拉取失败/超时 → **fail-open** + warn（reason='行情目录不可用，跳过对账'）
  ——对账是增强不是风控闸门，不阻塞交易；
- 域名解析复用 env_profiles 单一入口（binance demo-fapi vs fapi、
  gate testnet 候选域按 registry 环境语义、OKX demo 同域 +
  ``x-simulated-trading:1`` 头）。

trader 侧接入（下单前调用 ``ensure_contract_listed``）由后续故事完成，
本文件不触碰 scripts/。
"""
from __future__ import annotations

import json
import time
import warnings
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from urllib.request import Request, urlopen

from . import env_profiles
from .base import ExchangeCapabilityError
from .identity import is_sandbox_environment

TTL_SECONDS = 600.0
_TIMEOUT_SECONDS = 8.0

_UA = "R20-Quant-Desk/7.8-listing-gate"

#: 进程内合约目录缓存：key=(venue, environment) → (fetched_at_monotonic, {contract: state})
_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, Dict[str, str]]]] = {}


@dataclass(frozen=True)
class ListingCheck:
    ok: bool
    reason: Optional[str]
    checked_at: str            # UTC ISO8601
    source: str                # 'cache' | 'fresh'


def _norm(contract_native: str) -> str:
    return str(contract_native or "").strip().upper()


def _fetch_directory(venue: str, environment: str) -> Dict[str, Dict[str, str]]:
    """拉取该 (venue, environment) 的公共合约目录。

    返回 {合约名(大写): {"state": ..., "status": ..., "in_delisting": ...}}。
    网络失败/超时/解析失败抛异常（调用方 fail-open）。
    """
    prof = env_profiles.get_profile(venue, environment)
    base_url = env_profiles.resolve_base_url(venue, environment)

    headers = {"User-Agent": _UA, "Accept": "application/json"}
    if venue == "okx":
        path = "/api/v5/public/instruments?instType=SWAP"
        if prof.simulated_trading:
            # OKX demo = 同域 + 模拟盘头（env_profiles 结构位）
            headers["x-simulated-trading"] = "1"
    elif venue == "binance":
        path = "/fapi/v1/exchangeInfo"
    elif venue == "gate":
        path = "/api/v4/futures/usdt/contracts"
    else:
        raise ExchangeCapabilityError(f"listing gate 未支持的 venue={venue!r}")

    req = Request(base_url + path, headers=headers, method="GET")
    with urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    directory: Dict[str, Dict[str, str]] = {}
    if venue == "okx":
        for inst in payload.get("data", []):
            directory[_norm(inst.get("instId", ""))] = {"state": str(inst.get("state", ""))}
    elif venue == "binance":
        for sym in payload.get("symbols", []):
            directory[_norm(sym.get("symbol", ""))] = {"status": str(sym.get("status", ""))}
    elif venue == "gate":
        for c in payload:
            directory[_norm(c.get("name", ""))] = {
                "in_delisting": str(bool(c.get("in_delisting"))).lower()}
    return directory


def _get_directory(venue: str, environment: str,
                   fetch_fn=None) -> Tuple[Dict[str, Dict[str, str]], str]:
    """带 TTL 缓存的目录获取；source ∈ {'cache','fresh'}。"""
    key = (str(venue).lower(), str(environment).lower())
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit is not None and (now - hit[0]) < TTL_SECONDS:
        return hit[1], "cache"
    fetch = fetch_fn or _fetch_directory
    directory = fetch(venue, environment)
    _CACHE[key] = (now, directory)
    return directory, "fresh"


def ensure_contract_listed(venue: str, environment: str,
                           contract_native: str) -> ListingCheck:
    """环境维合约存在性对账（下单前调用；fail-open，不抛网络异常）。

    - 不存在 → ok=False（「合约已下架」/「沙盒未上市」按环境措辞）；
    - OKX state!=live / Binance status!=TRADING / Gate in_delisting=true
      → ok=False + 具体 reason；
    - 目录拉取失败/超时 → fail-open：ok=True + warn +
      reason='行情目录不可用，跳过对账'。
    """
    vkey = str(venue or "").strip().lower()
    ekey = str(environment or "").strip().lower()
    checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    native = _norm(contract_native)

    try:
        directory, source = _get_directory(vkey, ekey)
    except Exception as exc:  # fail-open：对账是增强不是风控闸门
        warnings.warn(f"[listing-gate] {vkey}/{ekey} 行情目录拉取失败，跳过对账: {exc}")
        return ListingCheck(ok=True, reason="行情目录不可用，跳过对账",
                            checked_at=checked_at, source="cache")

    info = directory.get(native)
    if info is None:
        if is_sandbox_environment(ekey):
            reason = f"沙盒未上市：{vkey} {ekey} 目录中无 {native}"
        else:
            reason = f"合约已下架：{vkey} {ekey} 目录中无 {native}"
        return ListingCheck(ok=False, reason=reason, checked_at=checked_at, source=source)

    if vkey == "okx":
        state = info.get("state", "")
        if state != "live":
            return ListingCheck(ok=False, reason=f"合约已下架：OKX state={state}",
                                checked_at=checked_at, source=source)
    elif vkey == "binance":
        status = info.get("status", "")
        if status != "TRADING":
            return ListingCheck(ok=False, reason=f"合约已下架：Binance status={status}",
                                checked_at=checked_at, source=source)
    elif vkey == "gate":
        if info.get("in_delisting") == "true":
            return ListingCheck(ok=False, reason="合约已下架：Gate in_delisting=true",
                                checked_at=checked_at, source=source)

    return ListingCheck(ok=True, reason=None, checked_at=checked_at, source=source)

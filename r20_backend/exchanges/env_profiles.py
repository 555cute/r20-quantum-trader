"""(venue, environment) → 端点环境 profile（三所对等化 P0 · US-001）。

事实源 = plan_local/THREE_VENUE_API_FRESHNESS_AUDIT_20260910.md §2（冲突以审计为准）：

- Binance USDⓈ-M：官方当前 SDK 常量 PROD / TESTNET / DEMO 三域**并存**——
  不得把「另一个环境」表述成「已全球废弃」。
- Gate：官方当前 SDK 仍列 ``fx-api-testnet.gateio.ws``；既往用户 Key 在
  ``api-testnet.gateapi.io`` 只读成功、旧域对其失败是实测事实，但两者都推不出
  「官方已全面废弃」→ 沙盒档 = 两候选域做**无凭证连通性择优探测**并持久化选择。
- OKX：demo 是「同域 + x-simulated-trading 头」开关（独立 Demo Key 另论），
  不属域名切换——profile 用 ``simulated_trading`` 位结构性表达，
  执行/签名头接线属 US-004/后续（OKX 执行现居 ai_factor_trader 直签链路）。

钉死铁律（本文件契约，测试逐条钉）：
1. 签名/带凭证请求**只发持久化选择域**；候选 A 4xx/5xx/网络错一律 fail-closed，
   绝不自动跨域重试，绝不回退 live。
2. 探测仅公共端点、零凭证、零写请求。
3. 探测全失败 → 沙盒档显式不可用异常；live 档解析不受任何影响。
4. 公共行情容灾（OKX www→aws 等）在各自适配器内部，属只读面，不在钉死范围。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

from .base import ExchangeCapabilityError

ROOT = Path(__file__).resolve().parents[2]
#: 选择结果持久化文件（测试必须 patch 本模块属性钉到临时目录）
PROFILE_FILE = ROOT / "data" / "venue_env_profile.json"

PROBE_PATH = "/api/v4/futures/usdt/contracts"   # Gate 公共端点：无凭证、只读
PROBE_TIMEOUT_SECONDS = 4.0


@dataclass(frozen=True)
class EnvProfile:
    venue: str
    environment: str                 # live | demo | testnet | sandbox
    urls: Tuple[str, ...]            # 长度 >1 → 需择优探测（首个成功持久化）
    simulated_trading: bool = False  # OKX demo 同域头开关（结构位，US-004 接线）

    @property
    def needs_probe(self) -> bool:
        return len(self.urls) > 1


PROFILES: Dict[Tuple[str, str], EnvProfile] = {
    ("okx", "live"): EnvProfile("okx", "live", ("https://www.okx.com",)),
    ("okx", "demo"): EnvProfile("okx", "demo", ("https://www.okx.com",), simulated_trading=True),
    # 三域并存（审计 §2 Binance：当前官方常量明确三项，旧测试域未被简单删除）
    ("binance", "live"): EnvProfile("binance", "live", ("https://fapi.binance.com",)),
    ("binance", "demo"): EnvProfile("binance", "demo", ("https://demo-fapi.binance.com",)),
    ("binance", "testnet"): EnvProfile("binance", "testnet", ("https://testnet.binancefuture.com",)),
    ("gate", "live"): EnvProfile("gate", "live", ("https://api.gateio.ws",)),
    # 两候选并列：官方 SDK 仍列旧域在前？——否：探测择优与本顺序无关，
    # 列表顺序仅作稳定性 tie-break（延迟相同取先者）。
    ("gate", "sandbox"): EnvProfile("gate", "sandbox", (
        "https://fx-api-testnet.gateio.ws",   # 官方当前 SDK 仍列（不得宣称废弃）
        "https://api-testnet.gateapi.io",     # 既往用户 Key 实测只读成功的新域
    )),
}

#: R20_{VENUE}_TESTNET=1 旧布尔开关 → 沙盒档映射（US-001 兼容层）。
#: binance 映射到 demo：与旧实现逐字节同 URL（test_url=demo-fapi，
#: tests/test_multi_exchange_admin 钉死），TESTNET 域自此可显式 environment 请求。
#: okx 无旧沙盒档——flag 对其维持现状无效果（live_url 未声明，base 不动）。
LEGACY_FLAG_ENV: Dict[str, str] = {"binance": "demo", "gate": "sandbox"}

_TRUE = ("1", "true", "yes", "on")


def has_env(venue: str, environment: str) -> bool:
    return (str(venue).lower(), str(environment).lower()) in PROFILES


def get_profile(venue: str, environment: str) -> EnvProfile:
    vkey = str(venue).lower()
    ekey = str(environment).lower()
    prof = PROFILES.get((vkey, ekey))
    if prof is None:
        # 沙盒环境名别名兼容（demo / sandbox / testnet 自动对齐）
        from .identity import is_sandbox_environment
        if is_sandbox_environment(ekey):
            for candidate in ("demo", "sandbox", "testnet"):
                if (vkey, candidate) in PROFILES:
                    return PROFILES[(vkey, candidate)]
        raise ExchangeCapabilityError(
            f"未知环境档 venue={venue!r} environment={environment!r}"
            f"，可用: {sorted(PROFILES)}")
    return prof


def environments_of(venue: str) -> List[str]:
    return sorted(env for (v, env) in PROFILES if v == str(venue).lower())


# ----------------------------------------------------------------------
# 探测（无凭证公共 GET）与持久化
# ----------------------------------------------------------------------
def _probe_candidate(url: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> Optional[int]:
    """返回延迟毫秒；任何不可达/非 200 → None。零凭证、零写。"""
    t0 = time.monotonic()
    try:
        req = Request(url + PROBE_PATH,
                      headers={"User-Agent": "R20-env-profile-probe/1.0",
                               "Accept": "application/json"},
                      method="GET")
        with urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            resp.read(2048)
        return max(1, round((time.monotonic() - t0) * 1000))
    except Exception:
        return None


def _load_persisted() -> Dict:
    try:
        if PROFILE_FILE.exists():
            data = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    return {}


def _persist(venue: str, environment: str, base_url: str, evidence: Dict[str, Optional[int]]) -> None:
    doc = _load_persisted()
    doc.setdefault(str(venue).lower(), {})[str(environment).lower()] = {
        "base_url": base_url,
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "evidence_ms": evidence,
    }
    tmp = Path(str(PROFILE_FILE) + ".tmp")
    try:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, PROFILE_FILE)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def pinned_base_url(venue: str, environment: str) -> Optional[str]:
    """已持久化的选择域；不在当前候选集（profile 更新后）视为失效。"""
    prof = get_profile(venue, environment)
    pinned = (((_load_persisted().get(str(venue).lower()) or {})
               .get(str(environment).lower()) or {})
              .get("base_url") or "")
    return pinned if pinned and pinned in prof.urls else None


def resolve_base_url(venue: str, environment: str,
                     probe_fn=None) -> str:
    """(venue, environment) → 唯一 base_url。多候选 = 择优 + 持久化钉死。

    probe_fn(url)->ms|None 仅供测试注入；生产默认 _probe_candidate。
    """
    prof = get_profile(venue, environment)
    if not prof.needs_probe:
        return prof.urls[0]
    pinned = pinned_base_url(prof.venue, prof.environment)
    if pinned:
        return pinned
    probe = probe_fn or _probe_candidate
    evidence = {url: probe(url) for url in prof.urls}
    reachable = sorted(((ms, url) for url, ms in evidence.items() if ms is not None))
    if not reachable:
        # fail-closed：沙盒不可用绝不回退 live（审计字面），live 档不受影响
        raise ExchangeCapabilityError(
            f"{venue} {environment} 档全部候选域不可达（fail-closed，禁止回退 live）："
            f"{evidence}。请检查网络或改用 live 档。")
    chosen = reachable[0][1]
    _persist(prof.venue, prof.environment, chosen, evidence)
    return chosen


def legacy_environment_for(venue: str) -> str:
    """R20_{VENUE}_TESTNET 布尔 → 档位名（无声明档 → 维持 live 现状）。"""
    vkey = str(venue or "").strip().lower()
    on = str(os.environ.get(f"R20_{vkey.upper()}_TESTNET", "0")).strip().lower() in _TRUE
    if not on:
        return "live"
    env = LEGACY_FLAG_ENV.get(vkey)
    if env and has_env(vkey, env):
        return env
    return "live"

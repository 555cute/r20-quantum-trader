"""AccountKey 身份模型（US-002 · 三所对等化 P0 地基）。

registry 适配器缓存键从 (venue, environment) 升级为 **AccountKey =
(venue, environment, credential_fingerprint)** 三元身份：

- 同所 live / 沙盒实例并存且互不串台（环境轴隔离，US-001 已有）；
- **凭证代际隔离**：轮换 API Key 后 fingerprint 变化 → 缓存自然失配 →
  下次 get_adapter 按新凭证重建实例，旧实例不残留（旧令牌不因缓存被复用）。

fingerprint 风格参照 ``scripts/okx_runtime.py:32-48`` OKXEnvironment：
``sha256(api_key or 'anon')`` 前 12 位 hex；凭证未配置显式 ANON 哨兵，
**绝不冒充已认证账户**。指纹是单向摘要，仅用于缓存键与身份比对，
永不回显到任何 API 响应/日志。

环境轴词表：``SANDBOX_ENVIRONMENTS`` 把 profile 档名（sandbox/demo/testnet）
归并为「交易所资金环境 = demo」，与执行许可开关的两条轴在此收敛。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: 凭证未配置的显式哨兵（不参与任何真实账户语义）
ANON_CREDENTIAL = "anon"

#: profile 档名中属于「沙盒/模拟资金环境」的三个取值（执行许可走独立开关）
SANDBOX_ENVIRONMENTS = frozenset({"sandbox", "demo", "testnet"})


def credential_fingerprint(api_key: str) -> str:
    """(api_key or anon) → sha256 前 12 位 hex。纯函数，零 IO。"""
    seed = str(api_key or "").strip() or ANON_CREDENTIAL
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def is_sandbox_environment(environment: str) -> bool:
    return str(environment or "").strip().lower() in SANDBOX_ENVIRONMENTS


@dataclass(frozen=True)
class AccountKey:
    """适配器实例缓存的三元身份键。frozen+hashable，直接做 dict key。"""

    venue: str
    environment: str
    fingerprint: str

    def __str__(self) -> str:  # 诊断用；fingerprint 已是单向摘要
        return f"{self.venue}:{self.environment}:{self.fingerprint}"

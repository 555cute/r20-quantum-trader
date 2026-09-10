"""Single source of truth for OKX live/demo credentials used by signed REST."""
from __future__ import annotations
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ENVIRONMENTS = {"demo", "live"}


def _load_dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            key, value = line.split("=", 1); values[key.strip()] = value.strip().strip('"').strip("'")
    try:
        from r20_gateway.secrets import load_secrets
        values.update(load_secrets())
    except Exception:
        pass
    # Dynamic project configuration and encrypted secrets override stale inherited process values.
    return {**os.environ, **values}


@dataclass(frozen=True)
class OKXEnvironment:
    mode: str
    api_key: str
    secret_key: str
    passphrase: str
    base_url: str = "https://www.okx.com"
    source: str = "environment"

    @property
    def simulated(self) -> bool: return self.mode == "demo"
    @property
    def configured(self) -> bool: return bool(self.api_key and self.secret_key and self.passphrase)
    @property
    def fingerprint(self) -> str:
        seed = f"{self.mode}:{self.api_key}".encode()
        return hashlib.sha256(seed).hexdigest()[:12] if self.api_key else f"{self.mode}-not-configured"
    @property
    def identity(self) -> str: return f"okx:{self.mode}:{self.fingerprint}"


def selected_environment(values: Mapping[str, str] | None = None) -> OKXEnvironment:
    env = dict(_load_dotenv() if values is None else values)
    legacy_simulated = str(env.get("OKX_IS_SIMULATED", "1")).lower() in {"1", "true", "yes"}
    mode = str(env.get("R20_OKX_ENV") or ("demo" if legacy_simulated else "live")).lower()
    if mode not in ALLOWED_ENVIRONMENTS: mode = "demo"
    prefix = "OKX_DEMO" if mode == "demo" else "OKX_LIVE"
    # A profile is an atomic credential group. A partially entered profile must
    # never borrow individual fields from a different (legacy) identity.
    fields = ("API_KEY", "SECRET_KEY", "PASSPHRASE")
    profile = tuple(str(env.get(f"{prefix}_{field}") or "") for field in fields)
    legacy = tuple(str(env.get(f"OKX_{field}") or "") for field in fields)
    api_key, secret_key, passphrase = profile if any(profile) else legacy
    base_url = str(env.get("OKX_BASE_URL") or "https://www.okx.com").rstrip("/")
    if base_url != "https://www.okx.com": raise ValueError("OKX REST Base URL 只允许 https://www.okx.com")
    return OKXEnvironment(mode, api_key, secret_key, passphrase, base_url, "separate-credentials" if env.get(f"{prefix}_API_KEY") else "legacy-shared-key")


_FROZEN_ENVIRONMENT: OKXEnvironment | None = None


def freeze_environment(values: Mapping[str, str] | None = None) -> OKXEnvironment:
    """Freeze LIVE/DEMO and credentials for one trading cycle."""
    global _FROZEN_ENVIRONMENT
    _FROZEN_ENVIRONMENT = selected_environment(values)
    return _FROZEN_ENVIRONMENT


def unfreeze_environment() -> None:
    global _FROZEN_ENVIRONMENT
    _FROZEN_ENVIRONMENT = None


def current_environment(values: Mapping[str, str] | None = None) -> OKXEnvironment:
    """The environment the process must use right now: a frozen cycle env wins,
    otherwise the live LIVE/DEMO selection. Signed REST callers must use this
    instead of calling selected_environment() directly."""
    return _FROZEN_ENVIRONMENT or selected_environment(values)

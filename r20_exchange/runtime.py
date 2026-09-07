"""Exchange selection, cycle snapshots and account-scoped runtime state."""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from scripts import okx_runtime

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


@dataclass(frozen=True)
class ExchangeEnvironment:
    exchange: str
    mode: str
    api_key: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    passphrase: str = field(default="", repr=False)
    base_url: str = ""
    source: str = "separate-credentials"

    def __post_init__(self) -> None:
        hosts = {
            ("okx", "demo"): "https://www.okx.com",
            ("okx", "live"): "https://www.okx.com",
            ("binance", "demo"): "https://demo-fapi.binance.com",
            ("binance", "live"): "https://fapi.binance.com",
        }
        expected = hosts.get((self.exchange, self.mode))
        if expected is None:
            raise ValueError("Unsupported exchange or environment")
        if self.base_url and self.base_url != expected:
            raise ValueError("Exchange endpoint does not match the selected environment")
        object.__setattr__(self, "base_url", expected)

    @property
    def simulated(self) -> bool:
        return self.mode == "demo"

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key and (self.passphrase or self.exchange == "binance"))

    @property
    def fingerprint(self) -> str:
        material = "\0".join((self.exchange, self.mode, self.api_key, self.secret_key, self.passphrase))
        return hashlib.sha256(material.encode()).hexdigest()[:24]

    @property
    def identity(self) -> str:
        return f"{self.exchange}:{self.mode}:{self.fingerprint}"


_frozen: ExchangeEnvironment | None = None
_environment_lock = threading.RLock()
_clients = threading.local()


def selected_environment(values: Mapping[str, str] | None = None) -> ExchangeEnvironment:
    if values is None:
        with _environment_lock:
            if _frozen is not None:
                return _frozen
        values = okx_runtime._load_dotenv()
    effective = dict(values)
    exchange = str(effective.get("R20_EXCHANGE", "okx")).strip().lower()
    if exchange == "okx":
        selected = okx_runtime.selected_environment(effective)
        return ExchangeEnvironment("okx", selected.mode, selected.api_key, selected.secret_key, selected.passphrase, selected.base_url, selected.source)
    if exchange != "binance":
        raise ValueError("R20_EXCHANGE must be okx or binance")
    mode = str(effective.get("R20_BINANCE_ENV", "demo")).strip().lower()
    if mode not in {"live", "demo"}:
        raise ValueError("R20_BINANCE_ENV must be demo or live")
    prefix = f"BINANCE_{mode.upper()}"
    return ExchangeEnvironment(
        "binance", mode,
        str(effective.get(f"{prefix}_API_KEY") or ""),
        str(effective.get(f"{prefix}_SECRET_KEY") or ""),
    )


def freeze_environment(values: Mapping[str, str] | None = None) -> ExchangeEnvironment:
    """Keep exchange, credentials and data scope stable throughout one trading cycle."""
    global _frozen
    with _environment_lock:
        if _frozen is not None:
            raise RuntimeError("An exchange environment is already frozen")
        _frozen = selected_environment(values)
        return _frozen


def unfreeze_environment() -> None:
    global _frozen
    with _environment_lock:
        _frozen = None


def get_exchange(env: ExchangeEnvironment | None = None) -> Any:
    """One connection-pooling adapter per thread and selected credential identity."""
    selected = env if env is not None else selected_environment()
    cached = getattr(_clients, "current", None)
    if cached is not None and cached[0] == selected:
        return cached[1]
    if cached is not None:
        close = getattr(cached[1], "close", None)
        if close is not None:
            close()
    if selected.exchange == "binance":
        from r20_exchange.binance import BinanceExchange
        client = BinanceExchange(selected)
    else:
        from r20_exchange.okx import OKXExchange
        client = OKXExchange(selected)
    _clients.current = (selected, client)
    return client


def state_path(name: str, env: ExchangeEnvironment | None = None) -> Path:
    """Return an account-specific path; never silently import another account's state."""
    if not name or Path(name).name != name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("State name must be a single file name")
    selected = env if env is not None else selected_environment()
    return DATA_DIR / "exchanges" / selected.exchange / selected.mode / selected.fingerprint / name

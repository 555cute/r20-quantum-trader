"""Local encrypted secret store with explicit migration only."""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).resolve().parents[1]
KEY_FILE = ROOT / "data" / ".r20_secret_key"
STORE_FILE = ROOT / "data" / "r20_secrets.enc"
SECRET_KEYS = {
    "OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE",
    "OKX_LIVE_API_KEY", "OKX_LIVE_SECRET_KEY", "OKX_LIVE_PASSPHRASE",
    "OKX_DEMO_API_KEY", "OKX_DEMO_SECRET_KEY", "OKX_DEMO_PASSPHRASE", "LLM_API_KEY",
    "R20_NOTIFICATION_WEBHOOK", "R20_WECHAT_WEBHOOK",
    "R20_TELEGRAM_BOT_TOKEN", "R20_QQ_CLIENT_SECRET",
    "R20_ADMIN_TOKEN", "R20_SETUP_TOKEN",
    # 多交易所凭证（US-003：三所 6 账户独立凭证加密存储）
    "BINANCE_API_KEY", "BINANCE_SECRET_KEY",
    "BINANCE_LIVE_API_KEY", "BINANCE_LIVE_SECRET_KEY",
    "BINANCE_DEMO_API_KEY", "BINANCE_DEMO_SECRET_KEY",
    "BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_SECRET_KEY",
    "GATE_API_KEY", "GATE_SECRET_KEY",
    "GATE_LIVE_API_KEY", "GATE_LIVE_SECRET_KEY",
    "GATE_DEMO_API_KEY", "GATE_DEMO_SECRET_KEY",
    "GATE_TESTNET_API_KEY", "GATE_TESTNET_SECRET_KEY",
    "GATE_SANDBOX_API_KEY", "GATE_SANDBOX_SECRET_KEY",
}


def _atomic_write(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
        os.chmod(path, mode)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _fernet(create: bool = False) -> Fernet | None:
    if not KEY_FILE.exists():
        if not create:
            return None
        _atomic_write(KEY_FILE, Fernet.generate_key())
    return Fernet(KEY_FILE.read_bytes().strip())


def load_secrets() -> dict[str, str]:
    fernet = _fernet(False)
    if not fernet or not STORE_FILE.exists():
        return {}
    try:
        payload = json.loads(fernet.decrypt(STORE_FILE.read_bytes()).decode("utf-8"))
        return {key: str(value) for key, value in payload.items() if key in SECRET_KEYS and value}
    except (InvalidToken, OSError, json.JSONDecodeError):
        return {}


def save_secrets(values: Mapping[str, str]) -> None:
    current = load_secrets()
    current.update({key: value for key, value in values.items() if key in SECRET_KEYS and value})
    fernet = _fernet(True)
    assert fernet is not None
    ciphertext = fernet.encrypt(json.dumps(current, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    _atomic_write(STORE_FILE, ciphertext)


def delete_secrets(keys: list[str] | tuple[str, ...] | set[str]) -> None:
    current = load_secrets()
    for key in keys:
        current.pop(str(key), None)
        os.environ.pop(str(key), None)
    fernet = _fernet(True)
    assert fernet is not None
    ciphertext = fernet.encrypt(json.dumps(current, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    _atomic_write(STORE_FILE, ciphertext)


def inject_into_environment() -> int:
    secrets = load_secrets()
    for key, value in secrets.items():
        os.environ[key] = value
    return len(secrets)


def status() -> dict[str, object]:
    secrets = load_secrets()
    return {
        "initialized": KEY_FILE.exists() and STORE_FILE.exists(),
        "count": len(secrets),
        "keys": sorted(secrets),
        "key_mode": oct(KEY_FILE.stat().st_mode & 0o777) if KEY_FILE.exists() else "",
        "store_mode": oct(STORE_FILE.stat().st_mode & 0o777) if STORE_FILE.exists() else "",
        "source_priority": "encrypted-store-over-env",
    }

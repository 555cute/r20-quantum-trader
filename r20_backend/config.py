"""Environment-only configuration for the standalone R20 backend."""
from dataclasses import dataclass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Service-manager ownership must outrank .env refreshes.
_STARTUP_WORKER_OVERRIDES = {
    key: os.environ[key]
    for key in ("R20_GATEWAY_WORKER_ENABLED", "R20_DASHBOARD_WORKER_ENABLED")
    if key in os.environ
}


def load_encrypted_secrets() -> None:
    try:
        from r20_gateway.secrets import inject_into_environment
        inject_into_environment()
    except Exception:
        pass


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        os.environ[key] = _STARTUP_WORKER_OVERRIDES.get(key, value.strip().strip('"').strip("'"))


load_dotenv(ROOT / ".env")
load_encrypted_secrets()


@dataclass
class Settings:
    root: Path = ROOT
    host: str = "0.0.0.0"
    port: int = 8080
    exchange: str = "okx"
    okx_base_url: str = "https://www.okx.com"
    okx_environment: str = "demo"
    okx_api_key: str = ""
    okx_secret_key: str = ""
    okx_passphrase: str = ""
    okx_live_configured: bool = False
    okx_demo_configured: bool = False
    okx_simulated: bool = True
    binance_environment: str = "demo"
    binance_live_configured: bool = False
    binance_demo_configured: bool = False
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gemini-3.7-flash-high"
    llm_reasoning_effort: str = "high"
    llm_thinking_timeout: float = 120.0
    notification_webhook: str = ""
    setup_token: str = ""
    admin_token: str = ""
    manual_close_enabled: bool = False


def refresh_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    load_encrypted_secrets()
    settings.host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    settings.port = int(os.getenv("DASHBOARD_PORT", "8080"))
    from r20_exchange.runtime import selected_environment
    from scripts.okx_runtime import selected_environment as selected_okx_environment
    selected = selected_environment()
    okx_selected = selected_okx_environment()
    settings.exchange = selected.exchange
    settings.okx_base_url = okx_selected.base_url
    settings.okx_environment = okx_selected.mode
    settings.okx_api_key = okx_selected.api_key
    settings.okx_secret_key = okx_selected.secret_key
    settings.okx_passphrase = okx_selected.passphrase
    try:
        from r20_gateway.secrets import load_secrets
        secret_values = load_secrets()
    except Exception: secret_values = {}
    effective = {**os.environ, **secret_values}
    settings.okx_live_configured = bool(effective.get("OKX_LIVE_API_KEY") and effective.get("OKX_LIVE_SECRET_KEY") and effective.get("OKX_LIVE_PASSPHRASE"))
    settings.okx_demo_configured = bool(effective.get("OKX_DEMO_API_KEY") and effective.get("OKX_DEMO_SECRET_KEY") and effective.get("OKX_DEMO_PASSPHRASE"))
    binance_mode = str(effective.get("R20_BINANCE_ENV") or "demo").strip().lower()
    if binance_mode not in {"demo", "live"}:
        binance_mode = "demo"
    settings.binance_environment = selected.mode if selected.exchange == "binance" else binance_mode
    settings.binance_live_configured = bool(effective.get("BINANCE_LIVE_API_KEY") and effective.get("BINANCE_LIVE_SECRET_KEY"))
    settings.binance_demo_configured = bool(effective.get("BINANCE_DEMO_API_KEY") and effective.get("BINANCE_DEMO_SECRET_KEY"))
    settings.okx_simulated = selected.simulated
    settings.llm_base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    settings.llm_api_key = os.getenv("LLM_API_KEY", "")
    settings.llm_model = os.getenv("LLM_MODEL", "gemini-3.7-flash-high")
    settings.llm_reasoning_effort = os.getenv("LLM_REASONING_EFFORT", "high")
    settings.llm_thinking_timeout = float(os.getenv("LLM_THINKING_TIMEOUT", os.getenv("LLM_TIMEOUT_SECONDS", "120.0")))
    settings.notification_webhook = os.getenv("R20_NOTIFICATION_WEBHOOK", "")
    settings.setup_token = os.getenv("R20_SETUP_TOKEN", "")
    settings.admin_token = os.getenv("R20_ADMIN_TOKEN", "")
    settings.manual_close_enabled = os.getenv("R20_MANUAL_CLOSE_ENABLED", "0") == "1"
    return settings


settings = Settings()
refresh_settings()

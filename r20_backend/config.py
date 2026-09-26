"""Environment-only configuration for the standalone R20 backend."""
from dataclasses import dataclass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


load_dotenv(ROOT / ".env")
load_encrypted_secrets()


@dataclass
class Settings:
    root: Path = ROOT
    host: str = "0.0.0.0"
    port: int = 8080
    okx_base_url: str = "https://www.okx.com"
    okx_environment: str = "demo"
    okx_api_key: str = ""
    okx_secret_key: str = ""
    okx_passphrase: str = ""
    okx_live_configured: bool = False
    okx_demo_configured: bool = False
    okx_simulated: bool = True
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gemini-3.7-flash-high"
    llm_reasoning_effort: str = "high"
    llm_thinking_timeout: float = 120.0
    notification_webhook: str = ""
    setup_token: str = ""
    admin_token: str = ""
    manual_close_enabled: bool = False
    order_mode: str = "limit"
    #: ⚠️ 上报/展示用，**不是发单时的权威来源** —— 真正挂到订单上的 tag
    #: 取自 `scripts/okx_rest.py::effective_broker_tag()`（同一环境变量 + 同一个默认值）。
    #: 改这里的字面量不会影响发单；展示侧请调那个函数，免得显示值与实发值漂移。
    okx_broker_tag: str = "6e2191f027c6SUDE"
    #: 下面三条是注册/返佣通道的**展示**用地址（后台「关于」页渲染成可复制入口）。
    #: 均可用同名环境变量覆盖，便于分发副本时替换成自己的通道。
    okx_invite_url: str = "https://www.mitxcqvwnhj.com/join/48039151"
    gate_invite_url: str = "https://www.gatesites.net/share/MCHDBKYF"
    binance_invite_url: str = "https://www.bsmkweb.cc/activity/referral-entry/CPA?ref=CPA_00N8UVQ2OG"


def refresh_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    load_encrypted_secrets()
    settings.host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    settings.port = int(os.getenv("DASHBOARD_PORT", "8080"))
    from scripts.okx_runtime import selected_environment
    selected = selected_environment()
    settings.okx_base_url = selected.base_url
    settings.okx_environment = selected.mode
    settings.okx_api_key = selected.api_key
    settings.okx_secret_key = selected.secret_key
    settings.okx_passphrase = selected.passphrase
    try:
        from r20_gateway.secrets import load_secrets
        secret_values = load_secrets()
    except Exception: secret_values = {}
    effective = {**os.environ, **secret_values}
    settings.okx_live_configured = bool(effective.get("OKX_LIVE_API_KEY") and effective.get("OKX_LIVE_SECRET_KEY") and effective.get("OKX_LIVE_PASSPHRASE"))
    settings.okx_demo_configured = bool(effective.get("OKX_DEMO_API_KEY") and effective.get("OKX_DEMO_SECRET_KEY") and effective.get("OKX_DEMO_PASSPHRASE"))
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
    settings.order_mode = os.getenv("R20_ORDER_MODE", "limit").strip().lower() or "limit"
    settings.okx_broker_tag = os.getenv("OKX_BROKER_TAG", "6e2191f027c6SUDE")
    settings.okx_invite_url = os.getenv("OKX_INVITE_URL", "https://www.mitxcqvwnhj.com/join/48039151")
    settings.gate_invite_url = os.getenv("GATE_INVITE_URL", "https://www.gatesites.net/share/MCHDBKYF")
    settings.binance_invite_url = os.getenv("BINANCE_INVITE_URL", "https://www.bsmkweb.cc/activity/referral-entry/CPA?ref=CPA_00N8UVQ2OG")
    return settings


settings = Settings()
refresh_settings()

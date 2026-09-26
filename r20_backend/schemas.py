"""Request and response schemas for R20 backend."""
from __future__ import annotations
import re
from typing import Any
from pydantic import BaseModel, Field, field_validator, model_validator


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class AdminCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=12, max_length=128)
    role: str = Field(default="admin", pattern=r"^(superadmin|admin)$")


class AdminPasswordRequest(BaseModel):
    current_password: str = Field(default="", max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class AdminEnabledRequest(BaseModel):
    enabled: bool


class AdminUnlockRequest(BaseModel):
    confirmation: str = Field(min_length=12, max_length=100)


class MultiExchangeUpdate(BaseModel):
    binance_api_key: str | None = None
    binance_secret_key: str | None = None
    binance_live_api_key: str | None = None
    binance_live_secret_key: str | None = None
    binance_demo_api_key: str | None = None
    binance_demo_secret_key: str | None = None
    gate_api_key: str | None = None
    gate_secret_key: str | None = None
    gate_live_api_key: str | None = None
    gate_live_secret_key: str | None = None
    gate_demo_api_key: str | None = None
    gate_demo_secret_key: str | None = None
    okx_live_api_key: str | None = None
    okx_live_secret_key: str | None = None
    okx_live_passphrase: str | None = None
    okx_demo_api_key: str | None = None
    okx_demo_secret_key: str | None = None
    okx_demo_passphrase: str | None = None
    binance_testnet: bool | None = None
    gate_testnet: bool | None = None
    gate_execution: bool | None = None   # R20_GATE_EXECUTION 总开关
    binance_execution: bool | None = None  # R20_BINANCE_EXECUTION 总开关
    okx_execution: bool | None = None      # R20_OKX_EXECUTION 总开关
    okx_environment: str | None = None     # OKX 资金环境：demo|live
    preferred_venue: str | None = None  # 全局路由首选：okx|binance|gate|auto
    routing_mode: str | None = None     # 选所路由模式：auto|balanced|split
    confirmation: str = ""               # 变更执行开关必须精确确认短语


class VenueTestConnectionRequest(BaseModel):
    venue: str = Field(..., pattern=r"^(okx|binance|gate)$")
    environment: str = Field(default="live", pattern=r"^(live|demo|testnet|sandbox)$")
    api_key: str | None = None
    secret_key: str | None = None
    passphrase: str | None = None
    timeout: float = Field(default=8.0, ge=1.0, le=30.0)


class AdminConfigUpdate(BaseModel):
    okx_environment: str | None = Field(default=None, pattern=r"^(demo|live)$")
    okx_live_api_key: str | None = None
    okx_live_secret_key: str | None = None
    okx_live_passphrase: str | None = None
    okx_demo_api_key: str | None = None
    okx_demo_secret_key: str | None = None
    okx_demo_passphrase: str | None = None
    okx_api_key: str | None = None
    okx_secret_key: str | None = None
    okx_passphrase: str | None = None
    okx_simulated: bool | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_reasoning_effort: str | None = Field(default=None, pattern=r"^(low|medium|high|minimal|none|auto)$")
    notification_webhook: str | None = None
    manual_close_enabled: bool | None = None
    order_mode: str | None = Field(default=None, pattern=r"^(limit|market)$")


class LLMActivateRequest(BaseModel):
    model_id: str
    provider_id: str | None = None
    reasoning_effort: str | None = Field(default=None, pattern=r"^(low|medium|high|minimal|none|auto|max|xhigh)$")
    thinking_timeout: float | None = Field(default=None, ge=5.0, le=1800.0)


class LLMSettingsUpdateRequest(BaseModel):
    thinking_timeout: float | None = Field(default=None, ge=5.0, le=1800.0)
    active_model_id: str | None = None
    reasoning_effort: str | None = None
    request_attempts: int | None = Field(default=None, ge=1, le=10)
    fallback_model_ids: list[str] | None = None


class LLMTestRequest(BaseModel):
    model: str
    provider_id: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_format: str = "openai_chat"
    reasoning_effort: str = Field(default="auto", pattern=r"^(low|medium|high|minimal|none|auto)$")
    reasoning_type: str = "auto"


class LLMProviderUpsertRequest(BaseModel):
    id: str | None = None
    name: str
    type: str | None = None
    group: str | None = "其他"
    enabled: bool | None = False
    multi_key_enabled: bool | None = False
    response_api_enabled: bool | None = False
    base_url: str
    api_key: str | None = None
    api_format: str | None = "openai_chat"
    api_path: str | None = "/chat/completions"
    description: str | None = ""
    models: list[dict[str, Any]] | None = None


class LLMProviderToggleRequest(BaseModel):
    enabled: bool | None = None


class LLMModelUpsertRequest(BaseModel):
    id: str
    name: str | None = None
    provider_id: str | None = None
    provider_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_format: str = "openai_chat"
    reasoning_type: str = "auto"
    default_effort: str | None = None
    reasoning_effort: str | None = None
    capabilities: list[str] | None = None
    context_length: int | None = None
    description: str | None = ""


class LLMFetchModelsRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    provider_id: str | None = None


class CouncilConfigUpdateRequest(BaseModel):
    enabled: bool
    consensus_mode: str = Field(default="standard")
    # 审计 P2-13：与 council_manager.MIN/MAX/DEFAULT_COUNCIL_TIMEOUT 同源
    # （旧默认 60 与引擎默认 240 不一致，管理员不改这一项时前后端口径就不同）
    timeout_seconds: float = Field(default=240.0, ge=30.0, le=420.0)
    roles: dict[str, Any]


class CouncilApplySuiteRequest(BaseModel):
    suite_id: str


class CouncilResetRoleRequest(BaseModel):
    role_id: str


class CouncilImportRequest(BaseModel):
    payload: dict[str, Any]


class CouncilTestRequest(BaseModel):
    mock_market_prompt: str | None = None


class InitialCapitalUpdate(BaseModel):
    initial_capital: float = Field(gt=0, le=1_000_000_000)
    confirmation: str = Field(min_length=1, max_length=80)


class GatewayReplayRequest(BaseModel):
    confirmation: str


class InstrumentAddRequest(BaseModel):
    inst_id: str = Field(pattern=r"^[A-Z0-9]{2,15}-USDT-SWAP$")


class InstrumentDeleteRequest(BaseModel):
    confirmation: str = ""


class ManualCloseRequest(BaseModel):
    close_token: str = Field(min_length=20, max_length=200)
    admin_password: str = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=8, max_length=200)
    venue: str = Field(default="okx")


class UpdateRequest(BaseModel):
    confirmation: str


class PromptOverrideRequest(BaseModel):
    content: str = Field(max_length=12000)


class PromptLibraryUpdate(BaseModel):
    active_style: str = Field(pattern=r"^(stable|aggressive|custom)$")
    trading_system: str = Field(default="", max_length=12000)
    trading_user: str = Field(default="", max_length=12000)
    evolution_system: str = Field(default="", max_length=12000)
    evolution_user: str = Field(default="", max_length=12000)


class PromptProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=240)
    source_id: str = Field(default="stable", max_length=80)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("方案名称不能为空")
        return clean


class PromptProfileUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=240)
    enabled: bool | None = None
    editor_mode: str | None = Field(default=None, pattern=r"^(simple|advanced|modules)$")
    simple_policy: dict[str, Any] | None = None
    pipelines: dict[str, list[dict[str, Any]]] | None = None
    trading_system: str | None = Field(default=None, max_length=12000)
    trading_user: str | None = Field(default=None, max_length=12000)
    evolution_system: str | None = Field(default=None, max_length=12000)
    evolution_user: str | None = Field(default=None, max_length=12000)
    note: str = Field(default="后台更新", max_length=240)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is not None:
            clean = v.strip()
            if not clean:
                raise ValueError("方案名称不能为空")
            return clean
        return v


class PromptImportRequest(BaseModel):
    payload: dict[str, Any]
    name_override: str = Field(default="", max_length=60)


class PromptRollbackRequest(BaseModel):
    revision_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")


class InterceptorToggleRequest(BaseModel):
    enabled: bool


class InterceptorCodeRequest(BaseModel):
    code: str


class InterceptorCreateRequest(BaseModel):
    filename: str = Field(min_length=3, max_length=100)
    code: str


class InterceptorReorderRequest(BaseModel):
    pipeline_order: list[str]


class InterceptorTestRequest(BaseModel):
    scenario: dict[str, Any] | None = None


class BackupJobCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    source_id: str = Field(default="nightly-default", max_length=80)


class BackupJobUpdateRequest(BaseModel):
    job: dict[str, Any]


class SimpleBackupUpdateRequest(BaseModel):
    enabled: bool = True
    schedule_time: str = Field(default="02:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    destination: str = Field(pattern=r"^(local|s3|oss|webdav|baidu_oauth)$")
    retention: int = Field(default=3, ge=1, le=365)
    endpoint: str = Field(default="", max_length=300)
    bucket: str = Field(default="", max_length=120)
    credentials: dict[str, str] = Field(default_factory=dict)


class BackupCredentialUpdateRequest(BaseModel):
    credential_ref: str = Field(min_length=1, max_length=100)
    credentials: dict[str, str]


class BackupJobRunRequest(BaseModel):
    confirmation: str


class BackupJobImportRequest(BaseModel):
    payload: dict[str, Any]
    name_override: str = Field(default="", max_length=80)


class BackupVerifyRequest(BaseModel):
    archive_path: str = Field(min_length=1, max_length=300)
    expected_sha256: str = Field(default="", max_length=64)
    key_env: str = Field(default="", max_length=64)


class ChannelToggleRequest(BaseModel):
    enabled: bool
    webhook_url: str | None = None
    wechat_webhook: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_api_base: str | None = None
    qq_app_id: str | None = None
    qq_client_secret: str | None = None
    qq_openid: str | None = None


class BackupMethodsUpdate(BaseModel):
    baidu_enabled: bool
    local_enabled: bool
    local_retention: int = Field(ge=1, le=30)
    sqlite_enabled: bool
    sqlite_retention: int = Field(ge=1, le=90)


class NotificationConfigUpdate(BaseModel):
    webhook_enabled: bool = False
    webhook_url: str = ""
    wechat_enabled: bool = False
    wechat_webhook: str = ""
    telegram_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str = ""
    telegram_api_base: str | None = None
    qq_enabled: bool = False
    qq_app_id: str = ""
    qq_client_secret: str | None = None
    qq_openid: str = ""


class QQOpenIDCaptureStartRequest(BaseModel):
    app_id: str | None = None
    client_secret: str | None = None
    timeout: int = 60


class NotificationTestRequest(BaseModel):
    channel: str = Field(pattern=r"^(webhook|wechat|telegram|qq)$")
    confirmation: str = ""


class NotificationScheduleUpdate(BaseModel):
    briefing_times: list[str] = Field(min_length=1, max_length=6)


class BackupRequest(BaseModel):
    confirmation: str


class BackupRestoreRequest(BaseModel):
    archive_name: str
    confirmation: str
    key_env: str = Field(default="", max_length=64)


class MemoryItemRequest(BaseModel):
    expected_version: str | None = None
    text: str = Field(min_length=1, max_length=1000)


class MemoryUpdateAllRequest(BaseModel):
    expected_version: str | None = None
    items: list[str]


class RiskConfigUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    suite_id: str = ""
    # 审计 P2-9：越过"极端值"线时须逐字提交 HIGH RISK（前端弹逐字确认框）
    confirmation: str = ""


class RiskResetRequest(BaseModel):
    confirmation: str = ""


class PolicyArchiveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=240)
    tags: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("快照名称不能为空")
        return clean


class PolicyRestoreRequest(BaseModel):
    policy_hash: str = Field(default="", min_length=0, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def unify_hash(cls, data: Any) -> Any:
        if isinstance(data, dict):
            val = data.get("policy_hash") or data.get("hash") or ""
            if not re.match(r"^[a-zA-Z0-9_-]{6,64}$", val):
                raise ValueError("policy_hash 必须为 6~64 位的字母、数字、下划线或短横线")
            data["policy_hash"] = val
        return data

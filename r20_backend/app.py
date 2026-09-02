"""Standalone control plane: read-only monitoring plus process health."""
from __future__ import annotations
import ast
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pydantic import BaseModel, Field
from r20_backend.config import refresh_settings, settings
from r20_backend.okx_client import OKXClient
from r20_backend.okx_trade_service import account_snapshot as okx_account_snapshot, fast_close_confirmed
from r20_backend.backup_secrets import credential_status as backup_credential_status, save_credentials as save_backup_credentials
from r20_backend.prompt_views import EVOLUTION_USER_TEMPLATE, TRADING_USER_TEMPLATE, rendered_snapshots
from r20_backend.settings_store import mask, remove_env, update_env
from r20_backend.notifications import _env as notification_env, diagnose_channel, test_channel
from r20_backend.audit import recent as recent_audit, record as audit_record
from r20_backend.admin_auth import AdminAuthStore
from r20_backend.backup_store import (
    create_job as create_backup_job, delete_job as delete_backup_job, export_job as export_backup_job,
    get_job as get_backup_job, import_job as import_backup_job, list_jobs as list_backup_jobs,
    load_backup_methods, save_backup_methods, update_job as update_backup_job, validate_backup_job,
)
from r20_backend.schedule_store import load_schedule, save_schedule
from r20_gateway.agents import agent_statuses
from r20_gateway.publisher import DB_PATH as GATEWAY_DB_PATH
from r20_gateway.plugins import plugin_statuses
from r20_gateway import __version__ as GATEWAY_VERSION
from r20_gateway.scheduler import scheduler_snapshot
from r20_gateway.secrets import save_secrets, status as secret_store_status
from r20_gateway.store import GatewayStore
from r20_gateway.supervisor import start_supervisor as start_gateway_supervisor, stop_supervisor as stop_gateway_supervisor
from scripts.instrument_pool import from_okx_instrument, load_instruments, save_instruments
from scripts.prompt_library import (
    PRESETS, TEMPLATE_KEYS, active_profile, activate_profile, all_profiles, apply_module_layout,
    create_profile, delete_profile, export_profile, get_profile, import_profile,
    load_library, pipeline_view, profile_history, resolve_profile, rollback_profile, save_library, update_profile, validate_profile,
)

PROMPT_OVERRIDE_FILE = DATA_DIR / "system_prompt_override.txt"
BACKUP_LOG_FILE = ROOT / "logs" / "r20_backup_manual.log"
STARTED_AT = time.time()
REQUEST_SESSION: ContextVar[str] = ContextVar("r20_admin_session", default="")

@asynccontextmanager
async def lifespan(_: FastAPI):
    refresh_settings()
    admin_auth.initialize_from_legacy(settings.admin_token or settings.setup_token)
    embedded_gateway = os.getenv("R20_GATEWAY_MODE", "embedded").strip().lower() == "embedded"
    if embedded_gateway:
        start_gateway_supervisor()
    yield
    if embedded_gateway:
        stop_gateway_supervisor()


app = FastAPI(title="R20 Quantum Trader Standalone Backend", version="6.0.0-preview", lifespan=lifespan)


@app.middleware("http")
async def admin_session_context(request: Request, call_next):
    token = REQUEST_SESSION.set(request.headers.get("X-R20-Session", ""))
    try:
        return await call_next(request)
    finally:
        REQUEST_SESSION.reset(token)


okx = OKXClient()
admin_auth = AdminAuthStore()
LEGACY_DASHBOARD_HTML = ROOT / "dashboard" / "templates" / "index.html"
FRONTEND_DIST = ROOT / "r20_frontend" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"


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
    llm_reasoning_effort: str | None = Field(default=None, pattern=r"^(low|medium|high)$")
    notification_webhook: str | None = None
    manual_close_enabled: bool | None = None


class GatewayReplayRequest(BaseModel):
    confirmation: str


class InstrumentAddRequest(BaseModel):
    inst_id: str = Field(pattern=r"^[A-Z0-9]{2,15}-USDT-SWAP$")


class InstrumentDeleteRequest(BaseModel):
    confirmation: str


class ManualCloseRequest(BaseModel):
    close_token: str = Field(min_length=20, max_length=200)
    admin_password: str = Field(min_length=1, max_length=128)
    confirmation: str = Field(min_length=8, max_length=200)


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


class PromptProfileUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=240)
    enabled: bool = True
    editor_mode: str = Field(default="modules", pattern=r"^(simple|advanced|modules)$")
    simple_policy: dict[str, Any] = Field(default_factory=dict)
    pipelines: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    trading_system: str = Field(default="", max_length=12000)
    trading_user: str = Field(default="", max_length=12000)
    evolution_system: str = Field(default="", max_length=12000)
    evolution_user: str = Field(default="", max_length=12000)
    note: str = Field(default="后台更新", max_length=240)


class PromptImportRequest(BaseModel):
    payload: dict[str, Any]
    name_override: str = Field(default="", max_length=60)


class PromptRollbackRequest(BaseModel):
    revision_id: str = Field(min_length=1, max_length=80)


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
    qq_enabled: bool = False
    qq_app_id: str = ""
    qq_client_secret: str | None = None
    qq_openid: str = ""


class NotificationTestRequest(BaseModel):
    channel: str = Field(pattern=r"^(webhook|wechat|telegram|qq)$")
    confirmation: str = ""


class NotificationScheduleUpdate(BaseModel):
    briefing_times: list[str] = Field(min_length=1, max_length=6)


class BackupRequest(BaseModel):
    confirmation: str


def require_admin_token(token: str) -> None:
    expected = settings.admin_token or settings.setup_token
    if not expected:
        raise HTTPException(status_code=503, detail="后台尚未设置 R20_SETUP_TOKEN 或 R20_ADMIN_TOKEN")
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="管理员令牌无效")


def current_admin(x_r20_session: str | None = None, x_r20_admin_token: str | None = None) -> dict[str, Any]:
    user = admin_auth.validate_session(x_r20_session or "")
    if user:
        return user
    if x_r20_admin_token and not admin_auth.has_users():
        require_admin_token(x_r20_admin_token)
        return {"id": 0, "username": "legacy-token", "role": "legacy", "enabled": 1}
    raise HTTPException(status_code=401, detail="管理员会话已失效，请重新登录")


def require_admin_header(x_r20_admin_token: str | None = None, x_r20_session: str | None = None) -> dict[str, Any]:
    return current_admin(x_r20_session or REQUEST_SESSION.get(), x_r20_admin_token)


def require_superadmin(x_r20_session: str | None = None) -> dict[str, Any]:
    user = admin_auth.validate_session(x_r20_session or REQUEST_SESSION.get())
    if not user:
        raise HTTPException(status_code=401, detail="管理员会话已失效，请重新登录")
    if user["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="仅超级管理员可以执行此操作")
    return user


@lru_cache(maxsize=8)
def read_literal_string_constant(path: Path, constant_name: str) -> str:
    """Read a top-level string constant without importing platform-specific script modules."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id == constant_name for target in targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise RuntimeError(f"Prompt constant {constant_name} not found in {path.name}")


def read_json(filename: str, default: Any) -> Any:
    path = DATA_DIR / filename
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def script_state(script_name: str) -> dict[str, Any]:
    path = SCRIPTS_DIR / script_name
    return {"name": script_name, "exists": path.exists(), "path": str(path)}


def file_health(filename: str, expected_interval: int) -> dict[str, Any]:
    path = DATA_DIR / filename
    if not path.exists():
        return {"name": filename, "exists": False, "age_seconds": None, "fresh": False}
    age = max(0, int(time.time() - path.stat().st_mtime))
    return {"name": filename, "exists": True, "age_seconds": age, "fresh": age <= expected_interval * 2, "bytes": path.stat().st_size}


def log_tail(filename: str, lines: int = 30) -> str:
    path = ROOT / "logs" / filename
    if not path.exists():
        return "暂无日志"
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(lines, 200)):])


def decision_summary() -> list[dict[str, Any]]:
    raw = read_json("ai_brain_decisions.json", {})
    result = []
    for inst_id, item in raw.items():
        decision = item.get("decision", {}) if isinstance(item, dict) else {}
        result.append({
            "instId": inst_id,
            "action": decision.get("action", "WAIT"),
            "confidence": decision.get("confidence", 0),
            "summary": decision.get("summary_reason", ""),
            "updated_at": item.get("time_str", "") if isinstance(item, dict) else "",
        })
    return result


def runtime_overview() -> dict[str, Any]:
    health_files = [
        file_health("ai_brain_decisions.json", 15 * 60),
        file_health("factor_library_snapshot.json", 60),
        file_health("news_sentiment.json", 10 * 60),
        file_health("trading_ledger.json", 15 * 60),
    ]
    positions_payload = read_json("position_trackers.json", {})
    return {
        "service": {"version": "6.0.0-preview", "pid": os.getpid(), "uptime_seconds": int(time.time() - STARTED_AT)},
        "credentials": {"okx": bool(settings.okx_api_key and settings.okx_secret_key and settings.okx_passphrase), "llm": bool(settings.llm_api_key)},
        "data_health": health_files,
        "decisions": decision_summary(),
        "trackers": len(positions_payload) if isinstance(positions_payload, dict) else 0,
        "logs": {
            "trader": log_tail("ai_factor_trader.log", 18),
            "backend": log_tail("r20_backend.log", 18),
            "scheduler": log_tail("r20_scheduler.log", 18),
        },
        "audit": recent_audit(20),
    }


def git(command: list[str]) -> str:
    try:
        result = subprocess.run(["git", *command], cwd=ROOT, text=True, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git command timed out after {exc.timeout}s") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout.strip()


def update_status() -> dict[str, Any]:
    deployment_mode = os.getenv("R20_DEPLOYMENT_MODE", "local").strip().lower()
    if deployment_mode == "docker" or not (ROOT / ".git").exists():
        return {
            "deployment_mode": "docker",
            "branch": "container-image",
            "local": os.getenv("R20_BUILD_REVISION", "local-build"),
            "remote": "",
            "behind": 0,
            "ahead": 0,
            "dirty": False,
            "managed_by": "docker-compose",
            "update_hint": "docker compose build --pull && docker compose up -d",
        }
    try:
        local = git(["rev-parse", "--short", "HEAD"])
        branch = git(["branch", "--show-current"])
        dirty = bool(git(["status", "--porcelain"]))
        remote = ""
        behind = ahead = 0
        try:
            git(["fetch", "--quiet", "origin", branch])
            remote = git(["rev-parse", "--short", f"origin/{branch}"])
            ahead, behind = [int(item) for item in git(["rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"]).split()]
        except RuntimeError:
            pass
        return {"branch": branch, "local": local, "remote": remote, "behind": behind, "ahead": ahead, "dirty": dirty}
    except RuntimeError as exc:
        return {"error": str(exc)}



def vue_index(fallback: Path | None = None) -> FileResponse:
    if FRONTEND_INDEX.exists():
        return FileResponse(FRONTEND_INDEX)
    if fallback is not None:
        return FileResponse(fallback)
    raise HTTPException(status_code=503, detail="Vue 管理后台尚未构建，请运行 cd r20_frontend && npm run build")


@app.get("/admin", include_in_schema=False)
def admin_page() -> FileResponse:
    return vue_index()


@app.get("/admin/legacy", include_in_schema=False)
def legacy_admin_page() -> RedirectResponse:
    return RedirectResponse(url="/admin", status_code=307)


@app.get("/api/v1/admin/auth/status")
def admin_auth_status() -> dict[str, Any]:
    return {"initialized": admin_auth.has_users(), "mode": "account-password", "session_hours": 12}


@app.post("/api/v1/admin/auth/login")
def admin_login(payload: AdminLoginRequest) -> dict[str, Any]:
    try:
        result = admin_auth.login(payload.username, payload.password)
    except PermissionError as exc:
        audit_record("admin.login", "failed", {"username": payload.username})
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    audit_record("admin.login", "success", {"username": result["user"]["username"]})
    return result


@app.post("/api/v1/admin/auth/logout")
def admin_logout(x_r20_session: str | None = Header(default=None)) -> dict[str, Any]:
    user = admin_auth.validate_session(x_r20_session or "")
    admin_auth.logout(x_r20_session or "")
    if user:
        audit_record("admin.logout", "success", {"username": user["username"]})
    return {"logged_out": True}


@app.get("/api/v1/admin/auth/me")
def admin_me(x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    user = current_admin(x_r20_session, None)
    if user.get("role") == "legacy":
        raise HTTPException(status_code=401, detail="请使用管理员账号密码登录")
    return {"user": user}


@app.get("/api/v1/admin/users")
def admin_users(x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    return {"users": admin_auth.list_users(), "current_user_id": actor["id"]}


@app.post("/api/v1/admin/users")
def create_admin_user(payload: AdminCreateRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    try:
        user = admin_auth.create_user(payload.username, payload.password, payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("admin.user.create", "success", {"actor": actor["username"], "username": user["username"], "role": user["role"]})
    return {"created": user}


@app.put("/api/v1/admin/users/{user_id}/enabled")
def update_admin_enabled(user_id: int, payload: AdminEnabledRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    try:
        admin_auth.set_enabled(user_id, payload.enabled, actor["id"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_record("admin.user.enabled", "success", {"actor": actor["username"], "user_id": user_id, "enabled": payload.enabled})
    return {"user": admin_auth.get_user(user_id)}


@app.post("/api/v1/admin/users/{user_id}/unlock")
def unlock_admin_user(user_id: int, payload: AdminUnlockRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    expected = f"UNLOCK ADMIN {user_id}"
    if payload.confirmation.strip().upper() != expected:
        raise HTTPException(status_code=400, detail=f"确认短语必须精确为：{expected}")
    try:
        admin_auth.unlock_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit_record("admin.user.unlock", "success", {"actor": actor["username"], "user_id": user_id})
    return {"user": admin_auth.get_user(user_id)}


@app.put("/api/v1/admin/users/{user_id}/password")
def update_admin_password(user_id: int, payload: AdminPasswordRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = current_admin(x_r20_session, None)
    if actor.get("role") == "legacy":
        raise HTTPException(status_code=401, detail="请使用管理员账号密码登录")
    if actor["id"] != user_id and actor["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="只能修改自己的密码")
    if actor["id"] == user_id and not admin_auth.verify_password(actor["id"], payload.current_password):
        raise HTTPException(status_code=403, detail="当前密码不正确")
    try:
        admin_auth.change_password(user_id, payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("admin.password.update", "success", {"actor": actor["username"], "user_id": user_id})
    return {"changed": True, "reauthenticate": True}


@app.get("/api/v1/admin/overview")
def admin_overview(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    return runtime_overview()


@app.get("/api/v1/admin/audit")
def admin_audit(x_r20_admin_token: str | None = Header(default=None), limit: int = 50) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    return {"records": recent_audit(limit)}


@app.get("/api/v1/admin/gateway")
def gateway_status(x_r20_admin_token: str | None = Header(default=None), limit: int = 50) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    store = GatewayStore(GATEWAY_DB_PATH)
    pid_file = DATA_DIR / "r20_gateway.pid"
    pid = int(pid_file.read_text().strip()) if pid_file.exists() and pid_file.read_text().strip().isdigit() else 0
    running = False
    if pid:
        try:
            os.kill(pid, 0)
            running = True
        except OSError:
            pass
    return {"version": GATEWAY_VERSION, "running": running, "pid": pid or None, "stats": store.stats(), "event_health": store.event_health(), "deliveries": store.recent(limit), "scheduler": scheduler_snapshot(store)}


@app.post("/api/v1/admin/gateway/deliveries/{delivery_id}/replay")
def replay_gateway_delivery(delivery_id: int, payload: GatewayReplayRequest, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    if payload.confirmation.strip().upper() != f"REPLAY {delivery_id}":
        raise HTTPException(status_code=400, detail=f"确认短语必须精确为：REPLAY {delivery_id}")
    store = GatewayStore(GATEWAY_DB_PATH)
    if not store.replay_dead(delivery_id):
        raise HTTPException(status_code=409, detail="仅允许重放当前处于 dead 状态的投递")
    audit_record("gateway.delivery.replay", "accepted", {"delivery_id": delivery_id})
    return {"accepted": True, "delivery_id": delivery_id, "status": "pending"}


@app.get("/api/v1/admin/agents")
def admin_agents(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    store = GatewayStore(GATEWAY_DB_PATH)
    return {
        "agents": agent_statuses(store.job_runs(100)),
        "model_stats": store.model_stats(),
        "model_calls": store.model_calls(50),
        "prompt_policy": "交易主脑和自进化均由 Python 直接构造并传输提示词；Gateway 只记录无内容遥测。",
        "secret_store": secret_store_status(),
    }


@app.get("/api/v1/admin/plugins")
def admin_plugins(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    return {"plugins": plugin_statuses(), "installation_policy": "builtin-only", "reason": "实盘控制面不允许远程上传或执行任意插件代码"}


@app.get("/api/v1/admin/config")
def admin_config(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    return {
        "authentication_mode": "account-password",
        "configuration": {
            "OKX 当前环境": "模拟盘 DEMO" if settings.okx_simulated else "实盘 LIVE",
            "OKX 实盘凭证": "已完整配置" if settings.okx_live_configured else "未完整配置",
            "OKX 模拟盘凭证": "已完整配置" if settings.okx_demo_configured else "使用 OAuth/旧凭证或未配置",
            "LLM API Key": "已设置" if settings.llm_api_key else "未设置",
            "管理员系统": "账号密码 + 服务端会话" if admin_auth.has_users() else "尚未初始化",
            "通知 Webhook": "已设置" if settings.notification_webhook else "未设置",
            "手动平仓": "已启用" if settings.manual_close_enabled else "已禁用",
        },
        "editable": {
            "okx_environment": settings.okx_environment,
            "okx_live_configured": settings.okx_live_configured,
            "okx_demo_configured": settings.okx_demo_configured,
            "okx_simulated": settings.okx_simulated,
            "llm_base_url": settings.llm_base_url,
            "llm_model": settings.llm_model,
            "llm_reasoning_effort": settings.llm_reasoning_effort,
            "notification_webhook": settings.notification_webhook,
            "manual_close_enabled": settings.manual_close_enabled,
        },
    }


@app.put("/api/v1/admin/config")
def update_admin_config(payload: AdminConfigUpdate, x_r20_admin_token: str | None = Header(default=None), x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    refresh_settings()
    data = payload.model_dump(exclude_none=True)
    sensitive = any(key.startswith("okx_") or key == "manual_close_enabled" for key in data)
    if sensitive: require_superadmin(x_r20_session)
    else: require_admin_header(x_r20_admin_token)
    if "llm_base_url" in data and data["llm_base_url"] and not data["llm_base_url"].startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="LLM Base URL 必须以 http:// 或 https:// 开头")
    if "notification_webhook" in data and data["notification_webhook"] and not data["notification_webhook"].startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="Webhook 必须以 http:// 或 https:// 开头")
    selected_mode = data.get("okx_environment") or ("demo" if data.get("okx_simulated") else "live" if "okx_simulated" in data else None)
    if selected_mode and selected_mode != settings.okx_environment:
        import fcntl
        lock_path = DATA_DIR / ".ai_factor_trader.lock"; lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            try: fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise HTTPException(status_code=409, detail="交易周期正在执行，OKX 环境已冻结；请等待本周期结束后再切换")
            finally:
                try: fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                except OSError: pass
    secret_values = {
        "OKX_LIVE_API_KEY": data.get("okx_live_api_key"), "OKX_LIVE_SECRET_KEY": data.get("okx_live_secret_key"), "OKX_LIVE_PASSPHRASE": data.get("okx_live_passphrase"),
        "OKX_DEMO_API_KEY": data.get("okx_demo_api_key"), "OKX_DEMO_SECRET_KEY": data.get("okx_demo_secret_key"), "OKX_DEMO_PASSPHRASE": data.get("okx_demo_passphrase"),
        "OKX_API_KEY": data.get("okx_api_key"), "OKX_SECRET_KEY": data.get("okx_secret_key"), "OKX_PASSPHRASE": data.get("okx_passphrase"),
        "LLM_API_KEY": data.get("llm_api_key"),
    }
    save_secrets({key: value for key, value in secret_values.items() if value})
    env_values = {
        "R20_OKX_ENV": selected_mode,
        "OKX_IS_SIMULATED": "1" if selected_mode == "demo" else "0" if selected_mode else None,
        "LLM_BASE_URL": data.get("llm_base_url"),
        "LLM_MODEL": data.get("llm_model"),
        "LLM_REASONING_EFFORT": data.get("llm_reasoning_effort"),
        "R20_NOTIFICATION_WEBHOOK": data.get("notification_webhook"),
        "R20_MANUAL_CLOSE_ENABLED": "1" if data.get("manual_close_enabled") else "0" if "manual_close_enabled" in data else None,
    }
    update_env(env_values)
    refresh_settings()
    audit_record("config.update", "success", {"fields": sorted(data.keys())})
    return {
        "updated": True,
        "restart_note": "Long-running strategy processes read updated .env on their next execution cycle.",
        "manual_close_enabled": settings.manual_close_enabled,
    }


@app.get("/api/v1/admin/okx/account-snapshot")
def admin_okx_account_snapshot(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    try: return okx_account_snapshot()
    except Exception as exc: raise HTTPException(status_code=502, detail=f"获取 OKX 当前订单失败：{exc}") from exc


@app.post("/api/v1/admin/positions/close")
def manual_close_position(payload: ManualCloseRequest) -> dict[str, Any]:
    actor = require_superadmin(REQUEST_SESSION.get())
    refresh_settings()
    if not settings.manual_close_enabled:
        raise HTTPException(status_code=403, detail="后台手动平仓功能未启用")
    import fcntl
    lock_path = DATA_DIR / ".ai_factor_trader.lock"; lock_path.parent.mkdir(parents=True, exist_ok=True)
    if actor.get("role") == "legacy" or not admin_auth.verify_password(int(actor["id"]), payload.admin_password):
        raise HTTPException(status_code=403, detail="管理员密码验证失败")
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try: fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise HTTPException(status_code=409, detail="交易主循环正在执行，暂不允许后台快速平仓；请等待本周期结束")
        try:
            result = fast_close_confirmed(payload.close_token, payload.confirmation)
            audit_record("position.close", "confirmed_closed", {"instId": result.get("instId"), "side": result.get("posSide"), "environment": result.get("environment"), "size": result.get("closed_size")})
            return result
        except ValueError as exc:
            audit_record("position.close", "rejected", {"error": str(exc)[:300]})
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            audit_record("position.close", "verification_failed", {"error": str(exc)[:300]})
            raise HTTPException(status_code=502, detail=f"OKX 快速平仓未完成确认：{exc}") from exc
        finally: fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


@app.get("/api/v1/admin/instruments")
def admin_instruments(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    trackers = read_json("position_trackers.json", {})
    active = set(trackers.keys()) if isinstance(trackers, dict) else set()
    return {
        "instruments": [{**item, "protected": item["instId"] == "BTC-USDT-SWAP", "has_tracker": item["instId"] in active or item["name"] in active} for item in load_instruments()],
        "limits": {"minimum": 1, "maximum": 6, "btc_required": True},
    }


@app.post("/api/v1/admin/instruments")
def add_admin_instrument(payload: InstrumentAddRequest, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    inst_id = payload.inst_id.upper()
    current = load_instruments()
    if any(item["instId"] == inst_id for item in current):
        raise HTTPException(status_code=409, detail="该币种已在交易池中")
    if len(current) >= 6:
        raise HTTPException(status_code=409, detail="交易池最多允许 6 个币种；请先删除一个无持仓币种")
    try:
        matches = okx.instruments("SWAP", inst_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OKX 合约校验失败：{exc}") from exc
    raw = matches[0] if matches else {}
    if raw.get("instId") != inst_id or raw.get("settleCcy") != "USDT" or raw.get("state") != "live":
        raise HTTPException(status_code=400, detail="仅允许添加 OKX 在线可交易的 USDT 永续合约")
    item = from_okx_instrument(raw)
    save_instruments([*current, item])
    audit_record("instrument.add", "success", {"instId": inst_id})
    return {"added": item, "count": len(current) + 1, "effective": "next_process_cycle"}


@app.delete("/api/v1/admin/instruments/{inst_id}")
def delete_admin_instrument(inst_id: str, payload: InstrumentDeleteRequest, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    inst_id = inst_id.upper()
    if payload.confirmation.strip().upper() != f"REMOVE {inst_id}":
        raise HTTPException(status_code=400, detail=f"确认短语必须精确为：REMOVE {inst_id}")
    if inst_id == "BTC-USDT-SWAP":
        raise HTTPException(status_code=403, detail="BTC 是全局黑天鹅哨兵基准，不允许从交易池删除")
    current = load_instruments()
    if len(current) <= 1:
        raise HTTPException(status_code=409, detail="交易池至少保留 1 个币种")
    if not any(item["instId"] == inst_id for item in current):
        raise HTTPException(status_code=404, detail="该币种不在交易池中")
    trackers = read_json("position_trackers.json", {})
    coin = inst_id.split("-", 1)[0]
    if isinstance(trackers, dict) and (inst_id in trackers or coin in trackers):
        raise HTTPException(status_code=409, detail="该币种存在持仓追踪记录，为防止失去风控接管，禁止删除")
    updated = [item for item in current if item["instId"] != inst_id]
    save_instruments(updated)
    audit_record("instrument.remove", "success", {"instId": inst_id})
    return {"removed": inst_id, "count": len(updated), "effective": "next_process_cycle"}


@app.get("/api/v1/admin/about")
def admin_about(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    import platform
    store = GatewayStore(GATEWAY_DB_PATH)
    return {
        "product": {"name": "R20 Quantum Trader", "version": "6.0.0-preview", "control_plane": "R20 Gateway Runtime", "gateway_version": GATEWAY_VERSION},
        "runtime": {"python": platform.python_version(), "platform": platform.platform(), "backend_pid": os.getpid(), "gateway": gateway_status(x_r20_admin_token)},
        "components": [
            {"name": "FastAPI Control Plane", "version": "6.0.0-preview"},
            {"name": "Gateway Event Runtime", "version": GATEWAY_VERSION},
            {"name": "Tencent iLink Protocol", "version": "2.4.8"},
            {"name": "SQLite", "version": __import__("sqlite3").sqlite_version},
        ],
        "repository": {
            "url": "https://github.com/555cute/r20-quantum-trader",
            "branch": update_status().get("branch", ""),
            "commit": update_status().get("local", ""),
            "deployment_mode": update_status().get("deployment_mode", "local"),
        },
        "update": update_status(),
        "security": {"authentication": "PBKDF2-SHA256 + server-side sessions", "session_hours": 12, "plugin_policy": "builtin-only", "prompt_transport": "python-direct"},
    }


@app.get("/api/v1/admin/update-status")
def admin_update_status(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    return update_status()


@app.post("/api/v1/admin/update")
def update_application(payload: UpdateRequest, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    if payload.confirmation.strip().upper() != "UPDATE R20":
        raise HTTPException(status_code=400, detail="确认短语必须精确为：UPDATE R20")
    status_before = update_status()
    if status_before.get("deployment_mode") == "docker":
        raise HTTPException(status_code=409, detail="Docker 部署不支持容器内 git pull；请在宿主机执行 docker compose build --pull && docker compose up -d")
    if status_before.get("error"):
        raise HTTPException(status_code=502, detail=status_before["error"])
    if status_before["dirty"]:
        raise HTTPException(status_code=409, detail="工作区存在未提交修改；为防止覆盖本地改动，后台拒绝更新")
    if not status_before["remote"]:
        raise HTTPException(status_code=502, detail="无法读取远程仓库状态")
    try:
        output = git(["pull", "--ff-only", "origin", status_before["branch"]])
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"更新失败：{exc}") from exc
    status_after = update_status()
    audit_record("application.update", "success", {"before": status_before.get("local"), "after": status_after.get("local")})
    return {
        "updated": status_before["local"] != status_after.get("local"),
        "before": status_before,
        "after": status_after,
        "git_output": output,
        "restart_required": True,
        "restart_note": "请重启 r20-quantum 与 r20-scheduler 服务，让新代码接管后台与调度。",
    }


@app.get("/api/v1/admin/prompt-library")
def prompt_library(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    system_prompt = read_literal_string_constant(SCRIPTS_DIR / "ai_brain_trader.py", "SYSTEM_PROMPT")
    evolution_system_prompt = read_literal_string_constant(SCRIPTS_DIR / "self_improvement_engine.py", "EVOLUTION_SYSTEM_PROMPT")
    library = load_library()
    profile = active_profile()
    return {
        "active_style": library["active_style"],
        "active_profile_id": library["active_profile_id"],
        "profiles": [{**item, "pipeline_views": {
            "trading_system": pipeline_view(system_prompt, item, "trading_system"),
            "trading_user": pipeline_view(TRADING_USER_TEMPLATE, item, "trading_user"),
            "evolution_system": pipeline_view(evolution_system_prompt, item, "evolution_system"),
            "evolution_user": pipeline_view(EVOLUTION_USER_TEMPLATE, item, "evolution_user"),
        }} for item in all_profiles()],
        "base_templates": {
            "trading_system": system_prompt,
            "trading_user": TRADING_USER_TEMPLATE,
            "evolution_system": evolution_system_prompt,
            "evolution_user": EVOLUTION_USER_TEMPLATE,
        },
        "pipelines": {
            "trading_system": pipeline_view(system_prompt, profile, "trading_system"),
            "trading_user": pipeline_view(TRADING_USER_TEMPLATE, profile, "trading_user"),
            "evolution_system": pipeline_view(evolution_system_prompt, profile, "evolution_system"),
            "evolution_user": pipeline_view(EVOLUTION_USER_TEMPLATE, profile, "evolution_user"),
        },
        "effective_templates": {
            "trading_system": apply_module_layout(system_prompt, profile, "trading_system", "交易 System"),
            "trading_user": apply_module_layout(TRADING_USER_TEMPLATE, profile, "trading_user", "交易 User"),
            "evolution_system": apply_module_layout(evolution_system_prompt, profile, "evolution_system", "自进化 System"),
            "evolution_user": apply_module_layout(EVOLUTION_USER_TEMPLATE, profile, "evolution_user", "自进化 User"),
        },
        "snapshots": rendered_snapshots(),
        "transport": "python-direct",
    }


@app.put("/api/v1/admin/prompt-library")
def update_prompt_library(payload: PromptLibraryUpdate, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    library = load_library()
    library["active_style"] = payload.active_style
    library["custom"] = {
        "id": "custom", "name": "自定义", "description": "管理员自定义风格附加层。", "editable": True,
        "trading_system": payload.trading_system.strip(), "trading_user": payload.trading_user.strip(),
        "evolution_system": payload.evolution_system.strip(), "evolution_user": payload.evolution_user.strip(),
    }
    save_library(library)
    audit_record("prompt.library.update", "success", {"active_style": payload.active_style, "custom_characters": sum(len(getattr(payload, key)) for key in ("trading_system", "trading_user", "evolution_system", "evolution_user"))})
    return {"saved": True, "active_style": payload.active_style, "restart_note": "下一次 Python 交易主脑与自进化进程自动读取选中风格。"}


@app.get("/api/v1/admin/prompt-profiles")
def prompt_profiles(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    library = load_library()
    return {"active_profile_id": library["active_profile_id"], "profiles": all_profiles(), "allowed_variables": sorted(__import__("scripts.prompt_library", fromlist=["ALLOWED_VARIABLES"]).ALLOWED_VARIABLES)}


@app.post("/api/v1/admin/prompt-profiles")
def create_prompt_profile_api(payload: PromptProfileCreateRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    try: profile = create_profile(payload.name, payload.description, payload.source_id)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("prompt.profile.create", "success", {"actor": actor["username"], "profile_id": profile["id"]})
    return {"profile": profile}


@app.put("/api/v1/admin/prompt-profiles/{profile_id}")
def update_prompt_profile_api(profile_id: str, payload: PromptProfileUpdateRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    try: profile = update_profile(profile_id, payload.model_dump(exclude={"note"}), payload.note)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("prompt.profile.update", "success", {"actor": actor["username"], "profile_id": profile_id})
    return {"profile": profile, "validation": validate_profile(profile)}


@app.post("/api/v1/admin/prompt-profiles/{profile_id}/activate")
def activate_prompt_profile_api(profile_id: str, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    try: profile = activate_profile(profile_id)
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_record("prompt.profile.activate", "success", {"actor": actor["username"], "profile_id": profile_id})
    return {"active_profile_id": profile_id, "profile": profile}


@app.delete("/api/v1/admin/prompt-profiles/{profile_id}")
def delete_prompt_profile_api(profile_id: str, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    try: delete_profile(profile_id)
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_record("prompt.profile.delete", "success", {"actor": actor["username"], "profile_id": profile_id})
    return {"deleted": True}


@app.post("/api/v1/admin/prompt-profiles/validate")
def validate_prompt_profile_api(payload: PromptProfileUpdateRequest, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    return validate_profile(payload.model_dump())


@app.get("/api/v1/admin/prompt-profiles/{profile_id}/history")
def prompt_profile_history_api(profile_id: str, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    return {"history": profile_history(profile_id)}


@app.post("/api/v1/admin/prompt-profiles/{profile_id}/rollback")
def rollback_prompt_profile_api(profile_id: str, payload: PromptRollbackRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    try: profile = rollback_profile(profile_id, payload.revision_id)
    except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
    audit_record("prompt.profile.rollback", "success", {"actor": actor["username"], "profile_id": profile_id, "revision_id": payload.revision_id})
    return {"profile": profile}


@app.get("/api/v1/admin/prompt-profiles/{profile_id}/export")
def export_prompt_profile_api(profile_id: str, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    try: return export_profile(profile_id)
    except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/admin/prompt-profiles/import")
def import_prompt_profile_api(payload: PromptImportRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    try: profile = import_profile(payload.payload, payload.name_override)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("prompt.profile.import", "success", {"actor": actor["username"], "profile_id": profile["id"]})
    return {"profile": profile}


@app.get("/api/v1/admin/prompts")
def prompt_override(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    system_prompt = read_literal_string_constant(SCRIPTS_DIR / "ai_brain_trader.py", "SYSTEM_PROMPT")
    content = PROMPT_OVERRIDE_FILE.read_text(encoding="utf-8") if PROMPT_OVERRIDE_FILE.exists() else ""
    effective = system_prompt if not content.strip() else f"{system_prompt}\n\n【管理员提示词覆盖层（同样必须遵守上述风控和 JSON 约束）】\n{content.strip()}"
    return {
        "content": content,
        "enabled": bool(content.strip()),
        "base_prompt": system_prompt,
        "effective_prompt": effective,
        "path": str(PROMPT_OVERRIDE_FILE),
    }


@app.put("/api/v1/admin/prompts")
def update_prompt_override(payload: PromptOverrideRequest, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    content = payload.content.strip()
    if content:
        temp = PROMPT_OVERRIDE_FILE.with_suffix(".tmp")
        temp.write_text(content + "\n", encoding="utf-8")
        os.replace(temp, PROMPT_OVERRIDE_FILE)
    elif PROMPT_OVERRIDE_FILE.exists():
        PROMPT_OVERRIDE_FILE.unlink()
    audit_record("prompt.update", "success", {"enabled": bool(content), "characters": len(content)})
    return {"saved": True, "enabled": bool(content), "restart_note": "下一次 AI 推演循环将自动叠加此提示词覆盖层。"}


@app.get("/api/v1/admin/notifications")
def notification_config(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    env = notification_env()
    return {
        "webhook": {"enabled": env.get("R20_NOTIFY_WEBHOOK_ENABLED", "0") == "1", "url": env.get("R20_NOTIFICATION_WEBHOOK", "")},
        "wechat": {"enabled": env.get("R20_NOTIFY_WECHAT_ENABLED", "0") == "1", "webhook": env.get("R20_WECHAT_WEBHOOK", "")},
        "telegram": {"enabled": env.get("R20_NOTIFY_TELEGRAM_ENABLED", "0") == "1", "bot_token": mask(env.get("R20_TELEGRAM_BOT_TOKEN", "")), "chat_id": env.get("R20_TELEGRAM_CHAT_ID", "")},
        "qq": {"enabled": env.get("R20_NOTIFY_QQ_ENABLED", "0") == "1", "app_id": env.get("R20_QQ_APP_ID", ""), "client_secret": mask(env.get("R20_QQ_CLIENT_SECRET", "")), "openid": env.get("R20_QQ_OPENID", "")},
    }


@app.put("/api/v1/admin/channels/{channel}/toggle")
def toggle_channel(channel: str, payload: ChannelToggleRequest, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    keys = {
        "qq": "R20_NOTIFY_QQ_ENABLED",
        "telegram": "R20_NOTIFY_TELEGRAM_ENABLED", "wechat": "R20_NOTIFY_WECHAT_ENABLED",
        "webhook": "R20_NOTIFY_WEBHOOK_ENABLED",
    }
    if channel not in keys:
        raise HTTPException(status_code=404, detail="未知频道")
    if payload.enabled:
        env = notification_env()
        readiness = {
            "qq": bool(env.get("R20_QQ_APP_ID") and env.get("R20_QQ_CLIENT_SECRET") and env.get("R20_QQ_OPENID")),
            "telegram": bool(env.get("R20_TELEGRAM_BOT_TOKEN") and env.get("R20_TELEGRAM_CHAT_ID")),
            "wechat": bool(env.get("R20_WECHAT_WEBHOOK")),
            "webhook": bool(env.get("R20_NOTIFICATION_WEBHOOK")),
        }
        if not readiness[channel]:
            raise HTTPException(status_code=409, detail="频道凭证或目标未配置完整，请先保存配置再启用")
    update_env({keys[channel]: "1" if payload.enabled else "0"})
    audit_record("channel.toggle", "success", {"channel": channel, "enabled": payload.enabled})
    return {"channel": channel, "enabled": payload.enabled}


@app.put("/api/v1/admin/notifications")
def update_notification_config(payload: NotificationConfigUpdate, x_r20_admin_token: str | None = Header(default=None), x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    refresh_settings()
    require_superadmin(x_r20_session)
    for value, title in ((payload.webhook_url, "通用 Webhook"), (payload.wechat_webhook, "企业微信 Webhook")):
        if value and not value.startswith(("https://", "http://")):
            raise HTTPException(status_code=400, detail=f"{title} 必须以 http:// 或 https:// 开头")
    existing = notification_env()
    readiness = {
        "QQ": (payload.qq_enabled, bool(payload.qq_app_id and (payload.qq_client_secret or existing.get("R20_QQ_CLIENT_SECRET")) and payload.qq_openid)),
        "Telegram": (payload.telegram_enabled, bool((payload.telegram_bot_token or existing.get("R20_TELEGRAM_BOT_TOKEN")) and payload.telegram_chat_id)),
        "企业微信": (payload.wechat_enabled, bool(payload.wechat_webhook)),
        "通用 Webhook": (payload.webhook_enabled, bool(payload.webhook_url)),
    }
    incomplete = [name for name, (enabled, ready) in readiness.items() if enabled and not ready]
    if incomplete:
        raise HTTPException(status_code=409, detail=f"以下频道配置不完整，无法启用：{'、'.join(incomplete)}")
    secret_values = {
        "R20_NOTIFICATION_WEBHOOK": payload.webhook_url, "R20_WECHAT_WEBHOOK": payload.wechat_webhook,
        "R20_TELEGRAM_BOT_TOKEN": payload.telegram_bot_token, "R20_QQ_CLIENT_SECRET": payload.qq_client_secret,
    }
    save_secrets({key: value for key, value in secret_values.items() if value})
    remove_env(set(secret_values))
    update_env({
        "R20_NOTIFY_WEBHOOK_ENABLED": "1" if payload.webhook_enabled else "0",
        "R20_NOTIFY_WECHAT_ENABLED": "1" if payload.wechat_enabled else "0",
        "R20_NOTIFY_TELEGRAM_ENABLED": "1" if payload.telegram_enabled else "0", "R20_TELEGRAM_CHAT_ID": payload.telegram_chat_id,
        "R20_NOTIFY_QQ_ENABLED": "1" if payload.qq_enabled else "0", "R20_QQ_APP_ID": payload.qq_app_id, "R20_QQ_OPENID": payload.qq_openid,
    })
    audit_record("notifications.update", "success", {
        "webhook": payload.webhook_enabled,
        "wechat": payload.wechat_enabled,
        "telegram": payload.telegram_enabled,
        "qq": payload.qq_enabled,
    })
    return {"saved": True, "restart_note": "通知配置已写入 .env；下一轮脚本执行会读取新通道。"}


@app.post("/api/v1/admin/notifications/diagnose")
def diagnose_notification(payload: NotificationTestRequest, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings(); require_admin_header(x_r20_admin_token)
    result = diagnose_channel(payload.channel)
    audit_record("notifications.diagnose", "completed", {"channel": payload.channel, "status": result.get("status")})
    return {"channel": payload.channel, "result": result, "sent": False}


@app.post("/api/v1/admin/notifications/test")
def send_notification_test(payload: NotificationTestRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    refresh_settings(); require_superadmin(x_r20_session)
    if payload.confirmation != f"SEND TEST {payload.channel.upper()}": raise HTTPException(status_code=400, detail=f"确认短语必须为：SEND TEST {payload.channel.upper()}")
    result = test_channel(payload.channel)
    audit_record("notifications.test", "completed", {"channel": payload.channel, "result": result})
    return {"channel": payload.channel, "result": result, "sent": True, "meaning": "远端接口已受理不等于用户客户端已读"}


@app.get("/api/v1/admin/notifications/schedule")
def notification_schedule(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    schedule = load_schedule()
    return {
        **schedule,
        "event_notifications": "开仓、平仓与风险事件实时推送，不受每日简报时间限制",
        "restart_note": "保存后调度器将在 60 秒内读取新时间，无需重启。",
    }


@app.put("/api/v1/admin/notifications/schedule")
def update_notification_schedule(payload: NotificationScheduleUpdate, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    normalized: list[str] = []
    for value in payload.briefing_times:
        value = value.strip()
        try:
            parsed = time.strptime(value, "%H:%M")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"无效时间：{value}；必须使用 HH:MM 24 小时格式") from exc
        canonical = f"{parsed.tm_hour:02d}:{parsed.tm_min:02d}"
        if canonical not in normalized:
            normalized.append(canonical)
    normalized.sort()
    schedule = load_schedule()
    schedule["briefing_times"] = normalized
    save_schedule(schedule)
    audit_record("notifications.schedule", "success", {"briefing_times": normalized, "timezone": "Asia/Shanghai"})
    return {**schedule, "saved": True, "restart_note": "调度器将在 60 秒内读取新时间。"}


@app.get("/api/v1/admin/backups/simple")
def simple_backup_config(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    jobs = list_backup_jobs(); job = next((x for x in jobs if x.get("id") == "nightly-default"), jobs[0] if jobs else None)
    if not job: raise HTTPException(status_code=404, detail="主灾备任务不存在")
    target = next((x for x in job.get("targets", []) if x.get("enabled")), None)
    if not target: target = next((x for x in job.get("targets", []) if x.get("type") == "local"), None)
    target_type = str((target or {}).get("type") or "local"); auth_mode = str((target or {}).get("auth_mode") or "")
    legacy_bypy = target_type == "baidu" and auth_mode != "oauth"
    destination = "baidu_oauth" if target_type == "baidu" and not legacy_bypy else target_type if target_type in {"local","s3","oss","webdav"} else "local"
    validation = validate_backup_job(job); latest = None
    manifests_dir = ROOT / "backups" / "manifests"
    for path in sorted(manifests_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30] if manifests_dir.exists() else []:
        try:
            item=json.loads(path.read_text(encoding="utf-8"))
            if item.get("job_id")==job["id"]: latest=item; break
        except (OSError,json.JSONDecodeError): pass
    return {"job_id":job["id"],"target":target or {},"enabled":job["enabled"],"schedule_time":job["schedule_times"][0],"destination":destination,"retention":int((target or {}).get("retention") or 3),"legacy_bypy":legacy_bypy,"migration_note":"当前为旧版 ByPy 配置，请选择新的保存位置后保存完成迁移" if legacy_bypy else "","configured":bool((target or {}).get("credential_status",{}).get("configured")) if target else destination=="local","validation":validation,"latest":latest,"advanced_preserved":True}


@app.put("/api/v1/admin/backups/simple")
def update_simple_backup(payload: SimpleBackupUpdateRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor=require_superadmin(x_r20_session); jobs=list_backup_jobs(); job=next((x for x in jobs if x.get("id")=="nightly-default"), jobs[0] if jobs else None)
    if not job: raise HTTPException(status_code=404,detail="主灾备任务不存在")
    wanted_type="baidu" if payload.destination=="baidu_oauth" else payload.destination
    existing=next((x for x in job.get("targets",[]) if x.get("type")==wanted_type and (wanted_type!="baidu" or x.get("auth_mode")=="oauth")),None)
    if not existing:
        target_id=f"{wanted_type}-{__import__('uuid').uuid4().hex[:10]}"; existing={"id":target_id,"type":wanted_type,"label":{"local":"本地归档","s3":"S3存储","oss":"阿里云OSS","webdav":"WebDAV/OpenList","baidu":"百度网盘"}[wanted_type],"credential_ref":f"backup:{target_id}","enabled":False,"remote_path":"R20_Backups","path":"backups/local","retention":3,"retries":3,"auth_mode":"oauth" if wanted_type=="baidu" else "native"}; job.setdefault("targets",[]).append(existing)
    for target in job.get("targets",[]): target["enabled"] = target is existing
    existing["retention"] = payload.retention if wanted_type=="local" else 0
    if wanted_type in {"s3","oss","webdav"}: existing["endpoint"] = payload.endpoint.strip()
    if wanted_type in {"s3","oss"}: existing["bucket"] = payload.bucket.strip()
    if wanted_type=="baidu": existing["auth_mode"]="oauth"
    if payload.credentials: save_backup_credentials(existing["credential_ref"], payload.credentials)
    job["enabled"]=payload.enabled; job["schedule_times"]=[payload.schedule_time]
    try: saved=update_backup_job(job["id"],job)
    except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    audit_record("backup.simple.update","success",{"actor":actor["username"],"destination":payload.destination,"enabled":payload.enabled})
    return {"saved":True,"job":saved}


@app.post("/api/v1/admin/backups/simple/test")
def test_simple_backup(payload: SimpleBackupUpdateRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    require_superadmin(x_r20_session)
    if payload.destination == "local":
        directory=(ROOT/"backups"/"local").resolve(); directory.mkdir(parents=True,exist_ok=True)
        if not directory.is_relative_to((ROOT/"backups").resolve()): raise HTTPException(status_code=400,detail="本地灾备目录无效")
        return {"status":"ready","sent":False,"detail":"本地目录可写；未生成或上传归档"}
    wanted_type="baidu" if payload.destination=="baidu_oauth" else payload.destination
    target={"type":wanted_type,"endpoint":payload.endpoint.strip(),"bucket":payload.bucket.strip(),"auth_mode":"oauth" if wanted_type=="baidu" else "native"}
    required={"s3":{"access_key_id","secret_access_key"},"oss":{"access_key_id","secret_access_key"},"webdav":set(),"baidu":{"app_key","app_secret","refresh_token"}}[wanted_type]
    missing=sorted(key for key in required if not payload.credentials.get(key))
    if missing: raise HTTPException(status_code=400,detail=f"连接信息不完整：{', '.join(missing)}")
    if wanted_type in {"s3","oss","webdav"}:
        try: target["endpoint"]=__import__("r20_backend.net_security",fromlist=["validate_outbound_url"]).validate_outbound_url(target["endpoint"])
        except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc)) from exc
    if wanted_type in {"s3","oss"} and not target["bucket"]: raise HTTPException(status_code=400,detail="Bucket 不能为空")
    # Intentionally no upload and no OAuth token exchange: this endpoint validates
    # configuration and safe reachability only, never creates remote objects.
    return {"status":"ready","sent":False,"detail":"配置格式与目标地址校验通过；未上传任何文件","destination":payload.destination}


@app.get("/api/v1/admin/backup-target-types")
def backup_target_types(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    return {"target_types": [
        {"type":"local","label":"本地归档","auth":"none","description":"项目 backups/ 内滚动保留"},
        {"type":"baidu","label":"百度网盘","auth":"oauth","description":"仅支持官方 OAuth，新配置不再提供 ByPy"},
        {"type":"s3","label":"S3 兼容存储","auth":"access-key","description":"AWS S3、R2、MinIO、COS 等 S3 兼容端点"},
        {"type":"oss","label":"阿里云 OSS","auth":"access-key","description":"官方 oss2 SDK"},
        {"type":"webdav","label":"WebDAV / NAS / OpenList","auth":"basic","description":"标准 WebDAV PUT/MKCOL"},
        {"type":"aliyundrive","label":"阿里云盘","auth":"webdav-or-oauth","description":"推荐开放平台或 OpenList WebDAV 桥接"},
        {"type":"quark","label":"夸克网盘（实验性）","auth":"webdav-or-experimental-oauth","description":"官方开放平台仍在内测，推荐 OpenList WebDAV 桥接"},
    ]}


@app.put("/api/v1/admin/backup-credentials")
def update_backup_credentials(payload: BackupCredentialUpdateRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    try: status = save_backup_credentials(payload.credential_ref, payload.credentials)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("backup.credentials.update", "success", {"actor": actor["username"], "credential_ref": payload.credential_ref, "fields": status["fields"]})
    return {"saved": True, "credential_ref": payload.credential_ref, "status": status}


@app.get("/api/v1/admin/backup-jobs")
def backup_jobs_api(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    manifests_dir = ROOT / "backups" / "manifests"
    manifests = []
    for path in sorted(manifests_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50] if manifests_dir.exists() else []:
        try:
            item = json.loads(path.read_text(encoding="utf-8")); item["manifest_file"] = path.name; manifests.append(item)
        except (OSError, json.JSONDecodeError): pass
    jobs = list_backup_jobs()
    for job in jobs:
        for target in job.get("targets", []): target["credential_status"] = backup_credential_status(str(target.get("credential_ref") or ""))
    return {"jobs": jobs, "validations": {job["id"]: validate_backup_job(job) for job in jobs}, "recent_manifests": manifests, "timezone": "Asia/Shanghai", "limits": {"maximum_jobs": 12}}


@app.post("/api/v1/admin/backup-jobs")
def create_backup_job_api(payload: BackupJobCreateRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    try: job = create_backup_job(payload.name, payload.source_id)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("backup.job.create", "success", {"actor": actor["username"], "job_id": job["id"]})
    return {"job": job}


@app.put("/api/v1/admin/backup-jobs/{job_id}")
def update_backup_job_api(job_id: str, payload: BackupJobUpdateRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    try: job = update_backup_job(job_id, payload.job)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("backup.job.update", "success", {"actor": actor["username"], "job_id": job_id, "enabled": job["enabled"]})
    return {"job": job, "validation": validate_backup_job(job)}


@app.delete("/api/v1/admin/backup-jobs/{job_id}")
def delete_backup_job_api(job_id: str, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    try: delete_backup_job(job_id)
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_record("backup.job.delete", "success", {"actor": actor["username"], "job_id": job_id})
    return {"deleted": True}


@app.post("/api/v1/admin/backup-jobs/validate")
def validate_backup_job_api(payload: BackupJobUpdateRequest, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    return validate_backup_job(payload.job)


@app.post("/api/v1/admin/backup-jobs/{job_id}/run")
def run_backup_job_api(job_id: str, payload: BackupJobRunRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    if payload.confirmation.strip().upper() != f"BACKUP {job_id}".upper():
        raise HTTPException(status_code=400, detail=f"确认短语必须精确为：BACKUP {job_id}")
    try: get_backup_job(job_id)
    except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc
    script = SCRIPTS_DIR / "nightly_backup_and_clean.py"
    result = subprocess.run([sys.executable, str(script), "--job-id", job_id], cwd=ROOT, text=True, capture_output=True, timeout=1800)
    BACKUP_LOG_FILE.parent.mkdir(exist_ok=True); BACKUP_LOG_FILE.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    audit_record("backup.job.run", "success" if result.returncode == 0 else "failed", {"actor": actor["username"], "job_id": job_id, "returncode": result.returncode})
    if result.returncode: raise HTTPException(status_code=502, detail=f"灾备任务失败：{result.stderr[-800:] or result.stdout[-800:]}")
    return {"completed": True, "output": result.stdout[-4000:]}


@app.get("/api/v1/admin/backup-jobs/{job_id}/export")
def export_backup_job_api(job_id: str, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    try: return export_backup_job(job_id)
    except ValueError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/admin/backup-jobs/import")
def import_backup_job_api(payload: BackupJobImportRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    try: job = import_backup_job(payload.payload, payload.name_override)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record("backup.job.import", "success", {"actor": actor["username"], "job_id": job["id"]})
    return {"job": job}


@app.post("/api/v1/admin/backup-jobs/verify")
def verify_backup_archive_api(payload: BackupVerifyRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    candidate = (ROOT / payload.archive_path).resolve()
    if not candidate.is_relative_to((ROOT / "backups").resolve()): raise HTTPException(status_code=400, detail="只能验证项目 backups/ 目录内的归档")
    from scripts.backup_runtime import verify_archive
    try: result = verify_archive(candidate, payload.expected_sha256, payload.key_env)
    except Exception as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit_record("backup.archive.verify", "success", {"actor": actor["username"], "archive": str(candidate.relative_to(ROOT)), "members": result["members"]})
    return result


@app.put("/api/v1/admin/backups/methods")
def update_backup_methods(payload: BackupMethodsUpdate, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    methods = {
        "baidu": {"enabled": payload.baidu_enabled, "retention": 0},
        "local": {"enabled": payload.local_enabled, "retention": payload.local_retention},
        "sqlite": {"enabled": payload.sqlite_enabled, "retention": payload.sqlite_retention},
    }
    if not any(item["enabled"] for item in methods.values()):
        raise HTTPException(status_code=400, detail="至少启用一种灾备方式")
    save_backup_methods(methods)
    audit_record("backup.methods.update", "success", {key: value["enabled"] for key, value in methods.items()})
    return {"saved": True, "methods": load_backup_methods()}


@app.get("/api/v1/admin/backups")
def backup_status(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    backups_dir = ROOT / "backups"
    archive_paths = list(backups_dir.glob("*.tar.gz")) + list((backups_dir / "local").glob("*.tar.gz")) if backups_dir.exists() else []
    local_archives = [{"name": str(item.relative_to(backups_dir)), "bytes": item.stat().st_size, "mtime": int(item.stat().st_mtime)} for item in archive_paths]
    sqlite_snapshots = [{"name": item.name, "bytes": item.stat().st_size, "mtime": int(item.stat().st_mtime)} for item in (backups_dir / "sqlite").glob("*.db")] if (backups_dir / "sqlite").exists() else []
    return {
        "schedule": "每天北京时间 02:00，由 Gateway Scheduler 执行全部已启用灾备方式",
        "script": str(SCRIPTS_DIR / "nightly_backup_and_clean.py"),
        "methods": load_backup_methods(),
        "jobs": list_backup_jobs(),
        "local_archives": sorted(local_archives, key=lambda item: item["mtime"], reverse=True),
        "sqlite_snapshots": sorted(sqlite_snapshots, key=lambda item: item["mtime"], reverse=True),
        "last_log": BACKUP_LOG_FILE.read_text(encoding="utf-8")[-4000:] if BACKUP_LOG_FILE.exists() else "尚无后台手动灾备日志",
    }


@app.post("/api/v1/admin/backups/run")
def run_backup(payload: BackupRequest, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    if payload.confirmation.strip().upper() != "BACKUP R20":
        raise HTTPException(status_code=400, detail="确认短语必须精确为：BACKUP R20")
    script = SCRIPTS_DIR / "nightly_backup_and_clean.py"
    result = subprocess.run([sys.executable, str(script)], cwd=ROOT, text=True, capture_output=True, timeout=600)
    BACKUP_LOG_FILE.parent.mkdir(exist_ok=True)
    BACKUP_LOG_FILE.write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    if result.returncode:
        audit_record("backup.run", "failed", {"returncode": result.returncode})
        raise HTTPException(status_code=502, detail=f"灾备任务失败：{result.stderr[-800:] or result.stdout[-800:]}")
    audit_record("backup.run", "success", {})
    return {"completed": True, "output": result.stdout[-2500:]}


@app.get("/health", include_in_schema=False)
@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    return {
        "service": "r20-standalone-backend",
        "version": "6.0.0-preview",
        "status": "ok",
        "timestamp": int(time.time()),
        "credentials": {
            "okx_configured": bool(settings.okx_api_key and settings.okx_secret_key and settings.okx_passphrase),
            "llm_configured": bool(settings.llm_api_key),
            "simulated_trading": settings.okx_simulated,
        },
    }


@app.get("/api/v1/status")
def status() -> dict[str, Any]:
    return {
        "version": "6.0.0-preview",
        "mode": "read_only_control_plane",
        "scripts": [
            script_state("ai_factor_trader.py"),
            script_state("ai_brain_trader.py"),
            script_state("daemon_web_sync.py"),
            script_state("self_improvement_engine.py"),
            script_state("nightly_backup_and_clean.py"),
        ],
        "last_decisions": read_json("ai_brain_decisions.json", {}),
        "position_trackers": read_json("position_trackers.json", {}),
    }


@app.get("/api/v1/cache/{resource}")
def cache(resource: str) -> JSONResponse:
    allowed = {
        "decisions": "ai_brain_decisions.json",
        "factors": "factor_library_snapshot.json",
        "ledger": "trading_ledger.json",
        "sentiment": "news_sentiment.json",
        "self-improvement": "self_improvement_report.json",
    }
    filename = allowed.get(resource)
    if not filename:
        raise HTTPException(status_code=404, detail="unknown cache resource")
    return JSONResponse(read_json(filename, {} if resource != "ledger" else []))


@app.get("/api/v1/market/{inst_id}")
def market(inst_id: str) -> dict[str, Any]:
    if not inst_id.endswith("-SWAP"):
        raise HTTPException(status_code=400, detail="only SWAP instrument ids are accepted")
    try:
        ticker = okx.ticker(inst_id)
        return {"instId": inst_id, "ticker": ticker[0] if ticker else {}, "source": "OKX REST"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OKX market request failed: {exc}") from exc


@app.get("/api/v1/account/positions")
def positions(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    if not settings.okx_api_key:
        raise HTTPException(status_code=503, detail="OKX credentials are not configured in .env")
    try:
        return {"positions": okx.positions(), "source": "OKX REST"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OKX account request failed: {exc}") from exc


# Serve the Vue production bundle while preserving the legacy dashboard API contract.
if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="vue-assets")


@app.get("/", include_in_schema=False)
def vue_root() -> FileResponse:
    return vue_index()


@app.get("/terminal", include_in_schema=False)
@app.get("/terminal/{page}", include_in_schema=False)
def vue_terminal(page: str = "trading") -> FileResponse:
    return vue_index()


from dashboard.app import app as dashboard_app
app.mount("/legacy", dashboard_app)
app.mount("/", dashboard_app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)

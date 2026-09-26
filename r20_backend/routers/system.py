"""System health, audit, overview, logs, and configuration routes."""
from __future__ import annotations
import os
import platform
import subprocess
import time
from urllib.parse import urlparse
from typing import Any
from fastapi import APIRouter, Header, HTTPException, Request

from r20_backend.version import __version__, APP_NAME, get_version
from r20_backend.config import settings, refresh_settings
from r20_backend.settings_store import update_env
from r20_backend.account_baseline import load_account_baseline
from r20_backend.audit import recent as recent_audit, record as audit_record
from r20_backend.dependencies import (
    ROOT, DATA_DIR, SCRIPTS_DIR, STARTED_AT,
    app_attr, admin_auth, require_admin_header, require_superadmin, read_json, script_state,
)
from r20_backend.schemas import AdminConfigUpdate, UpdateRequest
from r20_backend.llm_manager import get_active_llm_runtime, init_llm_providers, save_llm_config
from r20_gateway import __version__ as GATEWAY_VERSION
from r20_gateway.publisher import DB_PATH as GATEWAY_DB_PATH
from r20_gateway.plugins import plugin_statuses
from r20_gateway.agents import agent_statuses
from r20_gateway.secrets import save_secrets, status as secret_store_status
from r20_gateway.pidfile import process_running, read_pid
from r20_gateway.store import GatewayStore
from r20_gateway.scheduler import scheduler_snapshot

router = APIRouter(tags=["system"])

ADMIN_LOG_SOURCES = {
    "trader": "ai_factor_trader.log",
    # 审计①#6(2026-09-13)：r20_backend.log 从无写入方（uvicorn 直起写 uvicorn.log，
    # start.sh/systemd 亦然）——DecisionsPage「后台」页签因此永远空。指向真实文件。
    "backend": "uvicorn.log",
    "scheduler": "r20_gateway.log",
    "gateway": "r20_gateway.log",
}


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
    if isinstance(raw, dict):
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


def get_admin_configuration() -> dict[str, str]:
    baseline = load_account_baseline()
    try:
        llm_rt = get_active_llm_runtime()
        model_name = llm_rt.get("model") or settings.llm_model or "默认主脑"
        effort = (llm_rt.get("reasoning_effort") or settings.llm_reasoning_effort or "HIGH").upper()
    except Exception:
        model_name = settings.llm_model or "默认主脑"
        effort = (settings.llm_reasoning_effort or "HIGH").upper()

    has_notify = bool(settings.notification_webhook or getattr(settings, "qq_bot_app_id", None) or getattr(settings, "tg_bot_token", None) or getattr(settings, "wechat_webhook", None))

    pref_venue = "auto"
    try:
        from r20_backend.exchanges import routing_policy
        pref_venue = routing_policy.load_preferred_venue()
    except Exception:
        pass

    return {
        "交易场所与路由": f"{'模拟盘 DEMO' if settings.okx_simulated else '实盘 LIVE'} · 选所模式: {pref_venue.upper()}",
        "OKX 当前环境": "模拟盘 DEMO" if settings.okx_simulated else "实盘 LIVE",
        "OKX 实盘凭证": "已完整配置" if settings.okx_live_configured else "未配置",
        "OKX 模拟盘凭证": "已配置" if settings.okx_demo_configured else "未配置",
        "LLM 决策主脑": model_name,
        "LLM 思考强度": effort,
        "模型委员会": "加权共识机制 (ACTIVE)",
        "物理拦截管线": "5大物理拦截器 (FAIL-CLOSED)",
        "云端 OCO 覆盖": "100% 交易所云端挂载",
        "初始本金基准": f"{baseline.get('initial_capital', 4061.04):,.2f} USDT",
        "管理员系统": "账号密码 + 服务端会话" if admin_auth.has_users() else "尚未初始化",
        "通知告警通道": "已配置多通道" if has_notify else "未配置",
        "应急平仓机制": "一次性 Token 复核已就绪" if settings.manual_close_enabled else "已禁用",
    }


def runtime_overview() -> dict[str, Any]:
    health_files = [
        file_health("ai_brain_decisions.json", 15 * 60),
        file_health("factor_library_snapshot.json", 60),
        file_health("news_sentiment.json", 10 * 60),
        file_health("trading_ledger.json", 15 * 60),
    ]
    all_fresh = all(h.get("fresh", False) for h in health_files)
    health_payload = {
        "overall": "LIVE" if all_fresh else "STALE",
        "files": health_files,
    }
    positions_payload = read_json("position_trackers.json", {})
    return {
        "service": {"version": __version__, "pid": os.getpid(), "uptime_seconds": int(time.time() - STARTED_AT)},
        "credentials": {"okx": bool(settings.okx_api_key and settings.okx_secret_key and settings.okx_passphrase), "llm": bool(settings.llm_api_key)},
        "configuration": get_admin_configuration(),
        "data_health": health_payload,
        "decisions": decision_summary(),
        "trackers": len(positions_payload) if isinstance(positions_payload, dict) else 0,
        "logs": {
            "trader": log_tail("ai_factor_trader.log", 18),
            "backend": log_tail("r20_backend.log", 18),
            "scheduler": log_tail("r20_scheduler.log", 18),
        },
        "audit": recent_audit(20),
    }


def _effective_broker_tag() -> str:
    """透传 `scripts.okx_rest.effective_broker_tag()`（懒导入）。

    后台「关于」页展示的经纪商 code 必须与**实发订单上的 tag 同源** ——
    页面显示一个值、订单带另一个值，正是"以为带着 tag 其实没带"这类事故的温床。
    """
    from scripts import okx_rest
    return okx_rest.effective_broker_tag()


def git(command: list[str]) -> str:
    try:
        result = subprocess.run(["git", *command], cwd=ROOT, text=True, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"git command timed out after {exc.timeout}s") from exc
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout.strip()


def update_status() -> dict[str, Any]:
    try:
        local = git(["rev-parse", "--short", "HEAD"])
        branch = git(["branch", "--show-current"])
        dirty = bool(git(["status", "--porcelain", "-uno"]))
        remote = ""
        behind = ahead = 0
        try:
            git(["fetch", "--quiet", "origin", branch])
            remote = git(["rev-parse", "--short", f"origin/{branch}"])
            ahead, behind = [int(item) for item in git(["rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"]).split()]
        except RuntimeError:
            pass
        return {"branch": branch, "local": local, "remote": remote, "behind": behind, "ahead": ahead, "dirty": dirty}
    except Exception as exc:
        return {"branch": "", "local": "", "remote": "", "behind": 0, "ahead": 0, "dirty": False, "error": str(exc)}


@router.get("/api/v1/health")
def health() -> dict[str, Any]:
    _raw_base = settings.llm_base_url or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or ""
    _host = ""
    try:
        _host = urlparse(_raw_base).netloc if _raw_base else ""
    except Exception:
        _host = ""
    return {
        "service": "r20-standalone-backend",
        "version": get_version(),
        "status": "ok",
        "timestamp": int(time.time()),
        "credentials": {
            "okx_configured": bool(settings.okx_api_key and settings.okx_secret_key and settings.okx_passphrase),
            "llm_configured": bool(settings.llm_api_key),
            "simulated_trading": settings.okx_simulated,
        },
        "data_flow": {
            "llm_endpoint_host": _host or "NOT_CONFIGURED",
            "note": "所有决策提示词（含持仓与权益）仅发送至该主机；系统不内置任何默认第三方中继。",
        },
    }


@router.get("/api/v1/status")
def status() -> dict[str, Any]:
    # 审计修复B(2026-09-13)：此端点无鉴权（公开面），旧版整包吐出 position_trackers
    # （全套止损/止盈/云端OCO参数）与 last_decisions，匿名 curl 即可读仓位底牌。
    # 前端/脚本零消费者实证后仅保留无害外壳；决策与tracker走各自的鉴权admin面。
    return {
        "version": get_version(),
        "mode": "read_only_control_plane",
        "scripts": [
            script_state("ai_factor_trader.py"),
            script_state("ai_brain_trader.py"),
            script_state("daemon_web_sync.py"),
            script_state("self_improvement_engine.py"),
            script_state("nightly_backup_and_clean.py"),
        ],
    }


@router.get("/api/v1/admin/overview")
def admin_overview(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    return runtime_overview()


@router.get("/api/v1/admin/metrics")
def admin_metrics(
    format: str = "prometheus",
    x_r20_admin_token: str | None = Header(default=None),
    x_r20_session: str | None = Header(default=None, alias="X-R20-Session"),
):
    """机构级指标：Prometheus 文本 exposition（`?format=json` 给结构化快照）。

    ⚠️ **必须管理员鉴权**：指标里含账户规模、风控阈值与场所状态，属控制面数据。
    抓取器（Prometheus/Grafana Agent）请在 Header 里带 `X-R20-Admin-Token`
    （或已登录的 `X-R20-Session`）：

    ```yaml
    scrape_configs:
      - job_name: r20
        metrics_path: /api/v1/admin/metrics
        static_configs: [{targets: ["127.0.0.1:8080"]}]
        authorization: {credentials: "<admin token>"}
    ```

    fail-soft：某个数据源读不到时不会 500，而是发 `r20_metrics_source_ok{source="…"} 0`。
    """
    require_admin_header(x_r20_admin_token, x_r20_session)
    from r20_backend import metrics as metrics_mod
    snapshot = metrics_mod.build_snapshot()
    if str(format or "").strip().lower() == "json":
        return snapshot
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        metrics_mod.render_prometheus(snapshot),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/api/v1/admin/audit")
def admin_audit(x_r20_admin_token: str | None = Header(default=None), limit: int = 50) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    return {"records": recent_audit(limit)}


@router.get("/api/v1/admin/runtime")
def admin_runtime(x_r20_admin_token: str | None = Header(default=None), x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token, x_r20_session)
    payload = runtime_overview()
    raw_decisions = read_json("ai_brain_decisions.json", {})
    if isinstance(raw_decisions, dict):
        full_list = []
        for k, v in raw_decisions.items():
            if isinstance(v, dict):
                full_list.append({
                    "instId": v.get("instId", k),
                    "action": v.get("decision", {}).get("action", v.get("action", "WAIT")),
                    "confidence": (v.get("decision", {}).get("confidence", v.get("confidence", 0.0)) or 0.0) / (100.0 if (v.get("decision", {}).get("confidence", 0) or 0) > 1 else 1.0),
                    "timestamp": v.get("time_str", str(v.get("timestamp", ""))),
                    "reason": v.get("decision", {}).get("summary_reason") or v.get("thought_process", {}).get("market_structure") or v.get("reason", ""),
                })
        payload["full_decisions"] = full_list
    elif isinstance(raw_decisions, list):
        payload["full_decisions"] = raw_decisions
    else:
        payload["full_decisions"] = []
    # 审计修复A1(2026-09-13)：get_active_llm_runtime() 返回含明文 api_key/base_url，
    # 禁止整包入响应——白名单四键（与 dashboard /api/all 同口径），未配置时兜底不 500。
    try:
        _rt = get_active_llm_runtime()
        payload["llm_runtime"] = {
            "model": _rt.get("model", ""),
            "provider_name": _rt.get("provider_name", "默认"),
            "reasoning_effort": _rt.get("reasoning_effort", "high"),
            "api_format": _rt.get("api_format", "openai_chat"),
        }
    except Exception:
        payload["llm_runtime"] = {"model": "", "provider_name": "默认", "reasoning_effort": "high", "api_format": "openai_chat"}
    return payload


@router.get("/api/v1/admin/logs")
def admin_logs(source: str = "trader", lines: int = 100, x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    filename = ADMIN_LOG_SOURCES.get(source)
    if not filename:
        raise HTTPException(status_code=400, detail=f"日志来源仅支持：{', '.join(ADMIN_LOG_SOURCES)}")
    return {"source": source, "file": filename, "content": log_tail(filename, lines)}


@router.get("/api/v1/admin/config")
def admin_config(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    from r20_backend.dependencies import app_attr
    baseline_fn = app_attr("load_account_baseline", load_account_baseline)
    baseline = baseline_fn()
    return {
        "authentication_mode": "account-password",
        "configuration": get_admin_configuration(),
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
            "order_mode": settings.order_mode,
            "initial_capital": baseline.get("initial_capital", 4061.04),
            "initial_capital_reset_time": baseline.get("reset_time", ""),
        },
    }


@router.put("/api/v1/admin/config")
def update_admin_config(payload: AdminConfigUpdate, x_r20_admin_token: str | None = Header(default=None), x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    refresh_settings()
    data = payload.model_dump(exclude_none=True)
    sensitive = any(key.startswith("okx_") or key in ("manual_close_enabled",) for key in data)
    if sensitive:
        require_superadmin(x_r20_session)
    else:
        require_admin_header(x_r20_admin_token, x_r20_session)
    if "llm_base_url" in data and data["llm_base_url"] and not data["llm_base_url"].startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="LLM Base URL 必须以 http:// 或 https:// 开头")
    if "notification_webhook" in data and data["notification_webhook"] and not data["notification_webhook"].startswith(("https://", "http://")):
        raise HTTPException(status_code=400, detail="Webhook 必须以 http:// 或 https:// 开头")
    selected_mode = data.get("okx_environment") or ("demo" if data.get("okx_simulated") else "live" if "okx_simulated" in data else None)
    if selected_mode and selected_mode != settings.okx_environment:
        import fcntl
        lock_path = DATA_DIR / ".ai_factor_trader.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HTTPException(status_code=409, detail="交易周期正在执行，OKX 环境已冻结；请等待本周期结束后再切换")
            finally:
                try:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
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
        "R20_ORDER_MODE": data.get("order_mode"),
    }
    update_env(env_values)
    if data.get("order_mode"):
        os.environ["R20_ORDER_MODE"] = data["order_mode"]
        settings.order_mode = data["order_mode"]
    refresh_settings()
    if any(k.startswith("llm_") for k in data):
        try:
            cfg = init_llm_providers()
            active_mid = cfg.get("active_model_id", "")
            active_model = next((m for m in cfg.get("models", []) if m.get("id") == active_mid), None)
            active_pid = (active_model or {}).get("provider_id")
            active_p = next((p for p in cfg.get("providers", []) if p.get("id") == active_pid), None) if active_pid else None
            dirty = False
            if active_p:
                if "llm_base_url" in data and data["llm_base_url"]:
                    active_p["base_url"] = data["llm_base_url"].rstrip("/")
                    dirty = True
                if "llm_api_key" in data and data["llm_api_key"]:
                    active_p["api_key"] = data["llm_api_key"]
                    dirty = True
            if "llm_model" in data and data["llm_model"]:
                cfg["active_model_id"] = data["llm_model"]
                dirty = True
            if "llm_reasoning_effort" in data and data["llm_reasoning_effort"]:
                cfg["active_reasoning_effort"] = data["llm_reasoning_effort"]
                dirty = True
            if dirty:
                if active_p:
                    for mm in cfg.get("models", []):
                        if mm.get("provider_id") == active_p.get("id"):
                            mm["base_url"] = active_p.get("base_url", mm.get("base_url", ""))
                            mm["api_key"] = active_p.get("api_key", mm.get("api_key", ""))
                save_llm_config(cfg)
        except Exception:
            pass
    return admin_config(x_r20_admin_token)


@router.get("/api/v1/admin/agents")
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


@router.get("/api/v1/admin/plugins")
def admin_plugins(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token)
    return {"plugins": plugin_statuses(), "installation_policy": "builtin-only", "reason": "实盘控制面不允许远程上传或执行任意插件代码"}


@router.get("/api/v1/admin/about")
def admin_about(
    x_r20_admin_token: str | None = Header(default=None),
    x_r20_session: str | None = Header(default=None, alias="X-R20-Session"),
) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token, x_r20_session)
    refresh_settings()
    pid = read_pid()
    gw_running = process_running(pid)
    store = GatewayStore(GATEWAY_DB_PATH)
    gw_status = {"version": GATEWAY_VERSION, "running": gw_running, "pid": pid or None, "stats": store.stats(), "event_health": store.event_health(), "scheduler": scheduler_snapshot(store)}
    current_v = get_version()
    return {
        "product": {"name": APP_NAME, "version": current_v, "control_plane": "R20 Gateway Runtime", "gateway_version": GATEWAY_VERSION},
        "runtime": {"python": platform.python_version(), "platform": platform.platform(), "backend_pid": os.getpid(), "gateway": gw_status},
        "components": [
            {"name": "FastAPI Control Plane", "version": current_v},
            {"name": "Gateway Event Runtime", "version": GATEWAY_VERSION},
            {"name": "SQLite", "version": __import__("sqlite3").sqlite_version},
        ],
        "repository": {"url": "https://github.com/555cute/r20-quantum-trader", "branch": app_attr("git", git)(["branch", "--show-current"]), "commit": app_attr("git", git)(["rev-parse", "--short", "HEAD"])},
        "update": app_attr("update_status", update_status)(),
        # 注册/返佣通道（后台「关于」页渲染成可复制入口）。
        # ⚠️ OKX 的经纪商 code 取 `okx_rest.effective_broker_tag()` —— 与**实发订单上的
        # tag 同源**，避免"页面显示一个、订单带另一个"。三条 URL 走 settings（可被
        # OKX_INVITE_URL / GATE_INVITE_URL / BINANCE_INVITE_URL 覆盖，便于分发副本替换）。
        "channels": {
            "okx": {"name": "OKX", "invite_url": settings.okx_invite_url,
                    "broker_code": app_attr("effective_broker_tag",
                                            _effective_broker_tag)()},
            "gate": {"name": "Gate", "invite_url": settings.gate_invite_url, "broker_code": ""},
            "binance": {"name": "Binance", "invite_url": settings.binance_invite_url, "broker_code": ""},
        },
        "security": {"authentication": "PBKDF2-SHA256 + server-side sessions", "session_hours": 12, "plugin_policy": "builtin-only", "prompt_transport": "python-direct"},
    }


@router.get("/api/v1/admin/update-status")
@router.post("/api/v1/admin/update/check")
@router.get("/api/v1/admin/update/check")
def admin_update_status(
    x_r20_admin_token: str | None = Header(default=None),
    x_r20_session: str | None = Header(default=None, alias="X-R20-Session"),
) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_admin_token, x_r20_session)
    return app_attr("update_status", update_status)()


@router.post("/api/v1/admin/update")
def update_application(
    payload: UpdateRequest,
    x_r20_admin_token: str | None = Header(default=None),
    x_r20_session: str | None = Header(default=None, alias="X-R20-Session"),
) -> dict[str, Any]:
    refresh_settings()
    actor = require_admin_header(x_r20_admin_token, x_r20_session)
    if payload.confirmation.strip().upper() != "UPDATE R20":
        raise HTTPException(status_code=400, detail="确认短语必须精确为：UPDATE R20")
    fn_status = app_attr("update_status", update_status)
    fn_git = app_attr("git", git)
    status_before = fn_status()
    if status_before.get("error"):
        raise HTTPException(status_code=502, detail=status_before["error"])
    if status_before["dirty"]:
        raise HTTPException(status_code=409, detail="工作区存在未提交修改；为防止覆盖本地改动，后台拒绝更新")
    if not status_before["remote"]:
        raise HTTPException(status_code=502, detail="无法读取远程仓库状态")
    try:
        output = fn_git(["pull", "--ff-only", "origin", status_before["branch"]])
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=f"更新失败：{exc}") from exc
    status_after = fn_status()
    updated = status_before["local"] != status_after.get("local")
    if updated:
        try:
            subprocess.run(["npm", "run", "build"], cwd=str(ROOT / "frontend"), timeout=60, check=False)
        except Exception:
            pass
    rec_audit = app_attr("audit_record", audit_record)
    rec_audit("application.update", "success", {"actor": actor.get("username", "admin"), "before": status_before.get("local"), "after": status_after.get("local")})
    return {
        "updated": updated,
        "before": status_before,
        "after": status_after,
        "git_output": output,
        "restart_required": updated,
        "restart_note": "请重启 r20-quantum 与 r20-scheduler 服务，让新代码接管后台与调度。" if updated else "当前代码已是最新，无需重启服务。",
    }

"""Exchanges credentials, multi-venue status, and account snapshot routes."""
from __future__ import annotations
import json
import time
from typing import Any
from fastapi import APIRouter, Header, HTTPException, Query

from r20_backend.config import settings, refresh_settings
from r20_backend.settings_store import update_env
from r20_backend.audit import record as audit_record
from r20_backend.dependencies import (
    DATA_DIR, app_attr, require_admin_header, require_superadmin,
)
from r20_backend.schemas import (
    MultiExchangeUpdate,
    VenueTestConnectionRequest,
)
from r20_backend.okx_trade_service import account_snapshot as okx_account_snapshot
from scripts.okx_rest import OKXNotConfigured
from r20_gateway.secrets import save_secrets

router = APIRouter(tags=["exchanges"])


@router.get("/api/v1/account/positions")
def positions(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    from scripts.okx_runtime import current_environment
    from r20_backend.dependencies import okx
    env = current_environment()
    if not env.configured:
        raise HTTPException(status_code=503, detail=f"OKX {env.mode.upper()} NOT READY：请在后台配置完整 API Key 三件套")
    try:
        return {"positions": okx.positions(env=env), "source": "OKX REST"}
    except OKXNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OKX account request failed: {exc}") from exc


_VENUE_ACCOUNT_FIELDS = ("equity", "available", "positions_count", "open_orders_count")

_LISTING_ENV_MAP: dict[str, dict[str, str]] = {
    "okx": {"demo": "demo", "live": "live"},
    "binance": {"demo": "demo", "live": "live"},
    "gate": {"demo": "sandbox", "live": "live"},
}


def _venue_account_unknown(status: str, reason: str) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status, "reason": reason}
    for f in _VENUE_ACCOUNT_FIELDS:
        out[f] = None
    # 第一百六十九刀：`last_sync_ts` → **`last_sync_ms`**（值一直是毫秒，名字在说谎）。
    # 本字段只出现在本接口的响应里（不落盘），仓库内无旧名读者；前端 store 已同步改名。
    out["last_sync_ms"] = None
    return out


def _venue_accounts_okx(environment: str) -> dict[str, Any]:
    from scripts.okx_runtime import current_environment
    try:
        env = current_environment()
    except Exception as exc:
        return _venue_account_unknown("unavailable", f"OKX 环境解析失败: {type(exc).__name__}: {exc}")
    if env.mode != environment:
        return _venue_account_unknown(
            "unavailable",
            f"系统当前 OKX 档位为 {env.mode.upper()}，与请求环境 {environment.upper()} 不符——"
            "只读卡拒绝跨档读取（防止实盘/模拟混线；档位切换属后台配置动作）")
    if not env.configured:
        return _venue_account_unknown(
            "unavailable",
            f"OKX {env.mode.upper()} 静态 API Key 三件套未配置，未发起任何请求；"
            "请在后台「账户接入」录入后自动出现")
    try:
        from scripts import okx_rest
        bal = okx_rest.request("GET", "/api/v5/account/balance", {"ccy": "USDT"}, env=env)
        pos = okx_rest.request("GET", "/api/v5/account/positions", {"instType": "SWAP"}, env=env)
        pend = okx_rest.request("GET", "/api/v5/trade/orders-pending", {"instType": "SWAP"}, env=env)
    except OKXNotConfigured as exc:
        return _venue_account_unknown("unavailable", f"OKX 凭证失效：{exc}")
    except Exception as exc:
        return _venue_account_unknown("degraded", f"OKX 账户读取失败: {type(exc).__name__}: {exc}")
    out = {"status": "ready", "reason": f"OKX {env.mode.upper()} 直签只读"}
    try:
        row = (bal or [{}])[0]
        usdt_detail = None
        for d in (row.get("details") or []):
            if str(d.get("ccy", "")).upper() == "USDT":
                usdt_detail = d
                break

        if usdt_detail and (usdt_detail.get("eq") not in (None, "") or usdt_detail.get("cashBal") not in (None, "")):
            eq_val = float(usdt_detail.get("eq") or usdt_detail.get("cashBal") or 0.0)
            avail_val = float(usdt_detail.get("availEq") or usdt_detail.get("availBal") or eq_val)
        else:
            eq_val = float(row.get("totalEq") or row.get("eq") or 0.0)
            avail_val = float(usdt_detail.get("availEq") or usdt_detail.get("availBal") or eq_val) if usdt_detail else eq_val

        out["equity"] = eq_val if eq_val > 0 else None
        out["available"] = avail_val
        out["positions_count"] = sum(1 for p in (pos or []) if abs(float(p.get("pos") or 0)) > 1e-12)
        out["open_orders_count"] = len(pend or [])
        out["last_sync_ms"] = int(time.time() * 1000)
    except Exception as exc:
        return _venue_account_unknown("degraded", f"OKX 返回解析失败: {type(exc).__name__}: {exc}")
    return out


def _venue_accounts_gate(environment: str) -> dict[str, Any]:
    from r20_backend.exchanges import ExchangeCapabilityError, get_adapter, venue_credentials
    from r20_backend.close_intent import adapter_environment as _ae
    # 审计 C7：预检必须按档位轴取凭证（与 binance :151 同权），否则仅有分层
    # GATE_DEMO/LIVE 键时误报"未配置"，或反向放行 generic LIVE 键打错主机
    api_key, secret = venue_credentials("gate", _ae("gate", environment))
    if not api_key or not secret:
        return _venue_account_unknown(
            "unavailable", f"Gate {_ae('gate', environment)} 档 API Key/Secret 未配置，未发起任何请求；请在后台「多交易所凭证」录入")
    gate_env = "sandbox" if environment == "demo" else "live"
    try:
        ad = get_adapter("gate", environment=gate_env)
        acct = ad.account_snapshot()
        positions = [p for p in ad.positions() if abs(float(p.get("size_signed") or 0)) > 1e-12]
        open_rows = ad.signed_request("GET", "/api/v4/futures/usdt/orders",
                                      {"status": "open", "limit": "100"})
    except ExchangeCapabilityError as exc:
        return _venue_account_unknown("unavailable", f"Gate 账户面不可用：{exc}")
    except Exception as exc:
        return _venue_account_unknown("degraded", f"Gate 账户读取失败: {type(exc).__name__}: {exc}")
    try:
        return {
            "status": "ready",
            "equity": float(acct.get("equity_usdt") or 0),
            "available": float(acct.get("available_usdt") or 0),
            "positions_count": len(positions),
            "open_orders_count": len(open_rows if isinstance(open_rows, list) else []),
            "last_sync_ms": int(time.time() * 1000),
            "reason": f"Gate {gate_env} 档适配器直读",
        }
    except Exception as exc:
        return _venue_account_unknown("degraded", f"Gate 返回解析失败: {type(exc).__name__}: {exc}")


def _venue_accounts_binance(environment: str = "demo") -> dict[str, Any]:
    try:
        from r20_backend.exchanges import ExchangeCapabilityError, get_adapter, venue_credentials
        from r20_backend.exchanges.binance import BinanceAdapter
        supports = bool(getattr(BinanceAdapter.capabilities, "supports_account", False))
    except Exception as exc:
        return _venue_account_unknown("degraded", f"Binance 能力表读取失败: {type(exc).__name__}: {exc}")
    if not supports:
        return _venue_account_unknown(
            "not_implemented", "Binance 适配器账户面未实装（supports_account=False）")

    api_key, secret = venue_credentials("binance", environment)
    if not api_key or not secret:
        return _venue_account_unknown(
            "unavailable", f"Binance {environment.upper()} API Key/Secret 未配置，未发起任何请求；请在后台「多交易所凭证」录入")

    bn_env = "demo" if environment == "demo" else "live"
    try:
        ad = get_adapter("binance", environment=bn_env)
        acct = ad.account_snapshot()
        positions = [p for p in ad.positions() if abs(float(p.get("size_signed") or 0)) > 1e-12]
        open_rows = ad.open_orders()
    except ExchangeCapabilityError as exc:
        return _venue_account_unknown("unavailable", f"Binance 账户面不可用：{exc}")
    except Exception as exc:
        return _venue_account_unknown("degraded", f"Binance 账户读取失败: {type(exc).__name__}: {exc}")

    try:
        return {
            "status": "ready",
            "equity": float(acct.get("equity_usdt") or 0.0),
            "available": float(acct.get("available_usdt") or 0.0),
            "positions_count": len(positions),
            "open_orders_count": len(open_rows if isinstance(open_rows, list) else []),
            "last_sync_ms": int(time.time() * 1000),
            "reason": f"Binance {bn_env} 档适配器直读",
        }
    except Exception as exc:
        return _venue_account_unknown("degraded", f"Binance 返回解析失败: {type(exc).__name__}: {exc}")


@router.get("/api/v1/admin/multi-exchange")
def admin_multi_exchange_status(x_r20_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    from r20_backend.exchanges import (
        execution_open, registered_venues,
        venue_credentials, venue_passphrase, venue_testnet_enabled
    )
    venues: dict[str, Any] = {}
    def _read_creds(v: str, env: str | None = None) -> tuple[str, str]:
        try:
            return venue_credentials(v, env)
        except TypeError:
            return venue_credentials(v)

    for v in registered_venues():
        if v == "okx":
            continue
        api_key, secret = _read_creds(v)
        live_ak, live_sk = _read_creds(v, "live")
        demo_ak, demo_sk = _read_creds(v, "demo")
        venues[v] = {
            "has_api_key": bool(api_key),
            "has_secret": bool(secret),
            "live": {
                "has_api_key": bool(live_ak),
                "has_secret": bool(live_sk),
            },
            "demo": {
                "has_api_key": bool(demo_ak),
                "has_secret": bool(demo_sk),
            },
            "testnet": venue_testnet_enabled(v),
            "execution_open": execution_open(v),
        }

    from scripts.okx_runtime import current_environment
    okx_env = current_environment()
    okx_live_ak, okx_live_sk = _read_creds("okx", "live")
    okx_demo_ak, okx_demo_sk = _read_creds("okx", "demo")
    try:
        okx_live_pp = venue_passphrase("okx", "live")
        okx_demo_pp = venue_passphrase("okx", "demo")
    except Exception:
        okx_live_pp, okx_demo_pp = "", ""
    accounts_status = {
        "okx": {
            "live": {"has_api_key": bool(okx_live_ak), "has_secret": bool(okx_live_sk), "has_passphrase": bool(okx_live_pp)},
            "demo": {"has_api_key": bool(okx_demo_ak), "has_secret": bool(okx_demo_sk), "has_passphrase": bool(okx_demo_pp)},
        },
        "binance": {
            "live": venues.get("binance", {}).get("live", {}),
            "demo": venues.get("binance", {}).get("demo", {}),
        },
        "gate": {
            "live": venues.get("gate", {}).get("live", {}),
            "demo": venues.get("gate", {}).get("demo", {}),
        },
    }

    health: dict[str, Any] = {}
    try:
        health_path = DATA_DIR / "venue_health.json"
        if health_path.exists():
            health = json.loads(health_path.read_text(encoding="utf-8"))
    except Exception:
        health = {}

    # 保证 OKX 健康度与延迟展示（与 Binance / Gate 对齐）
    if "venues" in health and "okx" in health["venues"]:
        okx_h = health["venues"]["okx"]
        okx_h["testnet"] = bool(okx_env.simulated)
        if not okx_h.get("avg_ms"):
            try:
                from r20_backend.exchanges.diagnostics import diagnose_venue_connection
                diag = diagnose_venue_connection("okx", "demo" if okx_env.simulated else "live", timeout=2.5)
                if diag.get("latency_ms"):
                    okx_h["avg_ms"] = diag["latency_ms"]
            except Exception:
                pass

    from r20_backend.exchanges import routing_policy
    pref = routing_policy.load_preferred_venue()
    return {"venues": venues, "health": health, "preferred_venue": pref,
            "routing_mode": routing_policy.load_routing_mode(),
            "accounts_status": accounts_status}


@router.put("/api/v1/admin/multi-exchange")
def admin_multi_exchange_update(payload: MultiExchangeUpdate,
                                x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    secret_map = {
        "BINANCE_API_KEY": payload.binance_api_key,
        "BINANCE_SECRET_KEY": payload.binance_secret_key,
        "BINANCE_LIVE_API_KEY": payload.binance_live_api_key,
        "BINANCE_LIVE_SECRET_KEY": payload.binance_live_secret_key,
        "BINANCE_DEMO_API_KEY": payload.binance_demo_api_key,
        "BINANCE_DEMO_SECRET_KEY": payload.binance_demo_secret_key,
        "GATE_API_KEY": payload.gate_api_key,
        "GATE_SECRET_KEY": payload.gate_secret_key,
        "GATE_LIVE_API_KEY": payload.gate_live_api_key,
        "GATE_LIVE_SECRET_KEY": payload.gate_live_secret_key,
        "GATE_DEMO_API_KEY": payload.gate_demo_api_key,
        "GATE_DEMO_SECRET_KEY": payload.gate_demo_secret_key,
        "OKX_LIVE_API_KEY": payload.okx_live_api_key,
        "OKX_LIVE_SECRET_KEY": payload.okx_live_secret_key,
        "OKX_LIVE_PASSPHRASE": payload.okx_live_passphrase,
        "OKX_DEMO_API_KEY": payload.okx_demo_api_key,
        "OKX_DEMO_SECRET_KEY": payload.okx_demo_secret_key,
        "OKX_DEMO_PASSPHRASE": payload.okx_demo_passphrase,
    }
    secret_values = {k: str(v).strip() for k, v in secret_map.items() if v and str(v).strip()}
    fn_save_secrets = app_attr("save_secrets", save_secrets)
    fn_update_env = app_attr("update_env", update_env)
    fn_refresh_settings = app_attr("refresh_settings", refresh_settings)
    rec_audit = app_attr("audit_record", audit_record)
    if secret_values:
        fn_save_secrets(secret_values)
    env_values: dict[str, Any] = {}
    if payload.binance_testnet is not None:
        env_values["R20_BINANCE_TESTNET"] = "1" if payload.binance_testnet else "0"
    if payload.gate_testnet is not None:
        env_values["R20_GATE_TESTNET"] = "1" if payload.gate_testnet else "0"
    if payload.gate_execution is not None:
        if payload.confirmation.strip().upper() != "OPEN GATE EXECUTION":
            raise HTTPException(status_code=400,
                                detail="变更执行开关确认短语必须精确为：OPEN GATE EXECUTION")
        env_values["R20_GATE_EXECUTION"] = "1" if payload.gate_execution else "0"
    if payload.binance_execution is not None:
        if payload.confirmation.strip().upper() != "OPEN BINANCE EXECUTION":
            raise HTTPException(status_code=400,
                                detail="变更执行开关确认短语必须精确为：OPEN BINANCE EXECUTION")
        env_values["R20_BINANCE_EXECUTION"] = "1" if payload.binance_execution else "0"
    if payload.okx_execution is not None:
        if payload.confirmation.strip().upper() != "OPEN OKX EXECUTION":
            raise HTTPException(status_code=400,
                                detail="变更执行开关确认短语必须精确为：OPEN OKX EXECUTION")
        env_values["R20_OKX_EXECUTION"] = "1" if payload.okx_execution else "0"
    if payload.okx_environment is not None:
        env_values["R20_OKX_ENV"] = "demo" if payload.okx_environment.lower() == "demo" else "live"
    if env_values:
        fn_update_env(env_values)
    fn_refresh_settings()
    if payload.preferred_venue is not None:
        from r20_backend.exchanges import routing_policy
        routing_policy.save_preferred_venue(payload.preferred_venue)
    if payload.routing_mode is not None:
        from r20_backend.exchanges import routing_policy
        if not routing_policy.save_routing_mode(payload.routing_mode):
            raise HTTPException(
                status_code=400,
                detail=f"非法 routing_mode，允许 {list(routing_policy.VALID_ROUTING_MODES)}")
    try:
        from r20_backend.exchanges import clear_instances
        clear_instances()
    except Exception:
        pass
    rec_audit("multi_exchange.update", "success", {
        "actor": actor["username"],
        "secret_keys_saved": sorted(secret_values.keys()),
        "env_updated": sorted(env_values.keys()),
        "preferred_venue": payload.preferred_venue,
        "routing_mode": payload.routing_mode,
    })
    refresh_settings()
    return {"ok": True, "saved_secret_keys": sorted(secret_values.keys())}


@router.post("/api/v1/admin/multi-exchange/test-connection")
def admin_multi_exchange_test_connection(
    payload: VenueTestConnectionRequest,
    x_r20_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token)
    from r20_backend.exchanges.diagnostics import diagnose_venue_connection

    result = diagnose_venue_connection(
        venue=payload.venue,
        environment=payload.environment,
        api_key=payload.api_key,
        secret_key=payload.secret_key,
        passphrase=payload.passphrase,
        timeout=payload.timeout,
    )
    audit_record("multi_exchange.test_connection", "success" if result.get("ok") else "failed", {
        "venue": payload.venue,
        "environment": payload.environment,
        "authenticated": result.get("authenticated"),
        "mode": result.get("mode"),
        "ok": result.get("ok"),
    })
    return result


@router.get("/api/v1/admin/okx/runtime")
def admin_okx_runtime(x_r20_session: str | None = Header(default=None, alias="X-R20-Session"), refresh: int = 0) -> dict[str, Any]:
    refresh_settings()
    require_admin_header(x_r20_session=x_r20_session)
    from scripts.okx_runtime import current_environment
    env = current_environment()
    mode_configured = env.configured
    payload: dict[str, Any] = {
        "environment": env.mode,
        "mode_configured": bool(mode_configured),
        "live_configured": bool(settings.okx_live_configured),
        "demo_configured": bool(settings.okx_demo_configured),
        "fingerprint": env.fingerprint,
        "base_url": env.base_url,
        "connection": "static-v5-key",
        "status": "READY" if mode_configured else "NOT_READY",
    }
    if not mode_configured:
        payload["not_ready_reason"] = "未配置 OKX API Key：请在后台「账户接入」填入当前档位（DEMO/LIVE）的 API Key / Secret Key / Passphrase 三件套；未配置时系统禁止一切交易。"
    return payload


@router.get("/api/v1/admin/okx/account-snapshot")
def admin_okx_account_snapshot(
    x_r20_admin_token: str | None = Header(default=None),
    x_r20_session: str | None = Header(default=None, alias="X-R20-Session")
) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token, x_r20_session)
    from scripts.okx_runtime import current_environment
    env = current_environment()

    combined_positions: list[dict[str, Any]] = []
    combined_orders: list[dict[str, Any]] = []
    venue_errors: dict[str, str] = {}

    try:
        fn_snap = app_attr("okx_account_snapshot", okx_account_snapshot)
        okx_snap = fn_snap()
        for p in (okx_snap.get("positions") or []):
            p_copy = dict(p)
            p_copy.setdefault("venue", "okx")
            imr = float(p.get("imr", 0) or p.get("margin", 0) or 0)
            if imr <= 0 and float(p.get("notionalUsd", 0) or 0) > 0 and float(p.get("lever", 0) or 0) > 0:
                imr = round(float(p.get("notionalUsd")) / float(p.get("lever")), 2)
            p_copy["margin"] = imr
            combined_positions.append(p_copy)
        for o in (okx_snap.get("orders") or []):
            o_copy = dict(o)
            o_copy.setdefault("venue", "okx")
            combined_orders.append(o_copy)
    except OKXNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # 审计 C6：所失败显式化，不再抹平为"完整"
        venue_errors["okx"] = f"{type(exc).__name__}: {str(exc)[:180]}"

    from r20_backend.close_intent import (
        create as _create_close_intent, INTENT_TTL_SECONDS as _CLOSE_INTENT_TTL,
        adapter_environment as _close_adapter_env, _current_credential_fp as _close_cred_fp,
    )
    for venue in ("binance", "gate"):
        try:
            from r20_backend.exchanges import get_adapter
            adapter_env = _close_adapter_env(venue, env.mode)
            ad = get_adapter(venue, environment=adapter_env)
            cred_fp = _close_cred_fp(venue, adapter_env)  # 审计 B3：令牌钉住凭证身份
            if hasattr(ad, "positions"):
                for p in (ad.positions() or []):
                    amt = float(p.get("size_signed", 0) or 0)
                    if abs(amt) < 1e-12:
                        continue
                    sym = str(p.get("symbol") or p.get("base") or "").split("-")[0]
                    if not sym:
                        continue
                    inst_display = f"{sym}-USDT-SWAP"
                    pos_side = "long" if amt > 0 else "short"
                    close_token, close_confirmation = _create_close_intent(
                        venue=venue, environment=env.mode, display_inst=inst_display,
                        symbol=sym, pos_side=pos_side, expected_size=abs(amt),
                        credential_fingerprint=cred_fp)
                    # 保证金口径与 OKX 段对齐：优先交易所给的 margin，缺失时用
                    # 名义额/杠杆推（两者都来自交易所实况）；都拿不到就是 0，
                    # 前端据此回落显示原生张数，不显示捏造的数字。
                    _pos_margin = float(p.get("margin") or 0.0)
                    if _pos_margin <= 0:
                        _pos_lev = float(p.get("leverage") or 0.0)
                        _pos_notional = float(p.get("notional") or 0.0)
                        if _pos_lev > 0 and _pos_notional > 0:
                            _pos_margin = round(_pos_notional / _pos_lev, 2)
                    combined_positions.append({
                        "venue": venue,
                        "exchange": venue,
                        "instId": inst_display,
                        "posSide": pos_side,
                        "pos": str(abs(amt)),
                        "margin": _pos_margin,
                        "mgnMode": "cross",
                        "upl": float(p.get("unrealized_pnl", 0) or 0),
                        "close_confirmation": close_confirmation,
                        "close_token": close_token,
                        "close_token_expires_in": _CLOSE_INTENT_TTL,
                    })
            if hasattr(ad, "open_orders"):
                for o in (ad.open_orders() or []):
                    o_copy = dict(o)
                    o_copy.setdefault("venue", venue)
                    combined_orders.append(o_copy)
        except Exception as exc:
            venue_errors[venue] = f"{type(exc).__name__}: {str(exc)[:180]}"

    if not env.configured and not combined_positions and not combined_orders:
        raise HTTPException(status_code=503, detail=f"OKX {str(env.mode).upper()} API Key 未配置：V5 直签是唯一私有通道（fail-closed，无 CLI 回退）")

    return {
        "environment": env.mode,
        "environment_id": f"okx:{env.mode}:{env.fingerprint}",
        "credential_source": "multi-venue-aggregator",
        "positions": combined_positions,
        "orders": combined_orders,
        "venue_errors": venue_errors,
        "captured_at_ms": int(time.time() * 1000),
    }


@router.get("/api/v1/venue_accounts")
def venue_accounts(environment: str = Query(default="demo"),
                   x_r20_admin_token: str | None = Header(default=None),
                   x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    env_key = str(environment or "").strip().lower()
    if env_key not in ("demo", "live"):
        raise HTTPException(status_code=400, detail="environment 只允许 demo 或 live")
    require_admin_header(x_r20_admin_token, x_r20_session)
    venues_map = {
        "okx": _venue_accounts_okx(env_key),
        "gate": _venue_accounts_gate(env_key),
        "binance": _venue_accounts_binance(env_key),
    }
    from r20_backend.portfolio_aggregator import aggregate_venue_accounts
    summary = aggregate_venue_accounts(venues_map, env_key)
    return {
        "environment": env_key,
        "venues": venues_map,
        "portfolio_summary": summary,
        "captured_at_ms": int(time.time() * 1000),
    }


@router.get("/api/v1/listing_status")
def listing_status(environment: str = Query(default="demo"),
                   x_r20_admin_token: str | None = Header(default=None),
                   x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    require_admin_header(x_r20_admin_token, x_r20_session)
    env_key = str(environment or "").strip().lower()
    if env_key not in ("demo", "live"):
        raise HTTPException(status_code=400, detail="environment 只允许 demo 或 live")
    from r20_backend.exchanges.listing import listing_snapshot
    venues: dict[str, dict[str, Any]] = {}
    for venue in ("okx", "gate", "binance"):
        snap = listing_snapshot(venue, _LISTING_ENV_MAP[venue][env_key])
        venues[venue] = {
            "ok": snap.ok,
            "reason": snap.reason,
            "listed_count": snap.listed_count,
            "sample_symbols": getattr(snap, "sample_symbols", []),
            "source": snap.source,
            "checked_at": snap.checked_at,
        }
    return {
        "environment": env_key,
        "venues": venues,
        "captured_at_ms": int(time.time() * 1000),
    }


@router.get("/api/v1/admin/venue-protection/scan")
def venue_protection_scan(
    x_r20_admin_token: str | None = Header(default=None),
    x_r20_session: str | None = Header(default=None, alias="X-R20-Session"),
) -> dict[str, Any]:
    """跨所保护单**只读预演**（roadmap G8）：开闸前先看"这一轮会做什么"。

    - **绝不下单、绝不撤单**：走 `audit_cross_venue_protection(dry_run=True)`，
      只读交易所的保护单列表并判定（缺口/临期/不可判定）；
    - 需要 live 网络（每所一次持仓读取 + 每仓一次保护单列表），故需管理员鉴权；
    - 输出 `would`（本该做什么：renew/repair/verify/noop）、`critical`（完全没有止损腿）、
      `errors`（逐所隔离的失败）。`R20_VENUE_PROTECTION_WATCHDOG` 的开关状态一并回传，
      便于区分"巡检没开"与"巡检开了但没发现问题"。
    """
    require_admin_header(x_r20_admin_token, x_r20_session)
    from scripts.okx_runtime import current_environment
    from r20_backend.close_intent import adapter_environment
    from r20_backend.exchanges import get_adapter
    from scripts.trader.venue_protection import audit_cross_venue_protection

    env = current_environment()
    snapshot: dict[str, Any] = {}
    snapshot_errors: dict[str, str] = {}
    for venue in ("gate", "binance"):
        try:
            ad = get_adapter(venue, environment=adapter_environment(venue, env.mode))
            rows = [p for p in (ad.positions() or [])
                    if abs(float(p.get("size_signed") or 0) or 0) > 0]
            snapshot[venue] = rows
        except Exception as exc:
            # 与巡检同一纪律：读不到就如实登记，绝不假装"该所干净"
            snapshot_errors[venue] = f"{type(exc).__name__}: {exc}"
            snapshot[venue] = []

    class _Registry:
        @staticmethod
        def get_adapter(v: str, environment: str | None = None):
            return get_adapter(v, environment=environment or adapter_environment(v, env.mode))

    # 第一百七十四刀：把台账行交给归属层做**取证**（`ledger` 档证据＝同币同向同量已平记录）。
    # 读不到 ⇒ None ⇒ 不产生证据（腿留在"归属不可判定"，绝不自动撤）。
    from scripts.trader.venue_protection import read_ledger_rows
    report = audit_cross_venue_protection(snapshot, venue_registry=_Registry,
                                          environment=env.mode, dry_run=True,
                                          ledger_rows=read_ledger_rows(DATA_DIR / "trading_ledger.json"))
    report["environment"] = env.mode
    report["snapshot_errors"] = snapshot_errors
    try:
        from scripts import ai_factor_trader as _aft
        report["watchdog_enabled"] = bool(getattr(_aft, "R20_VENUE_PROTECTION_WATCHDOG", False))
    except Exception:
        report["watchdog_enabled"] = None
    return report

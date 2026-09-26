"""议会（council）配置与辩论测试端点。

从 `routers/strategy.py`（775 行 / 35 端点）按域拆出（B8）。
URL、方法、处理器名与 tags 一字未改 —— 对拍门 `tests/trading/test_strategy_router_split.py` 比对路由表。
"""
from __future__ import annotations

import json
from typing import Any
from fastapi import Header, HTTPException
from r20_backend.audit import record as audit_record
from r20_backend.dependencies import ROOT, require_admin_header, require_superadmin
from r20_backend.schemas import CouncilConfigUpdateRequest, CouncilApplySuiteRequest, CouncilResetRoleRequest, CouncilImportRequest, CouncilTestRequest
from scripts.prompt_library import active_profile, apply_module_layout

from fastapi import APIRouter

router = APIRouter(tags=["strategy"])


@router.get("/api/v1/admin/council/config")
def admin_get_council_config(x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    require_admin_header(x_r20_session=x_r20_session)
    from r20_backend.council_manager import load_council_config, get_available_presets, get_preset_suites, seat_model_health
    cfg = load_council_config()
    cfg["available_presets"] = get_available_presets()
    cfg["available_suites"] = get_preset_suites()
    # 审计 P1-4b：把"席位绑定的模型是否已登记"摊开给 UI（旧版静默回落主脑，页面照旧宣称多模型）
    cfg["model_health"] = seat_model_health(cfg.get("roles"))
    cfg["model_health_note"] = (
        "席位绑定未登记模型时，该席位由主脑模型代答（载荷带 model_fallback 标记）"
    )
    return cfg


@router.put("/api/v1/admin/council/config")
def admin_update_council_config(payload: CouncilConfigUpdateRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    from r20_backend.council_manager import save_council_config
    saved = save_council_config({
        "enabled": payload.enabled,
        "consensus_mode": payload.consensus_mode,
        "timeout_seconds": payload.timeout_seconds,
        "roles": payload.roles,
    })
    audit_record("council.config.update", "success", {
        "actor": actor["username"],
        "enabled": payload.enabled,
        "consensus_mode": payload.consensus_mode,
        "timeout_seconds": payload.timeout_seconds,
    })
    return {"status": "ok", "config": saved}


@router.post("/api/v1/admin/council/apply-suite")
def admin_apply_council_suite(payload: CouncilApplySuiteRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    from r20_backend.council_manager import apply_preset_suite
    try:
        saved = apply_preset_suite(payload.suite_id)
        audit_record("council.suite.apply", "success", {"actor": actor["username"], "suite_id": payload.suite_id})
        return {"status": "ok", "suite_id": payload.suite_id, "config": saved}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/v1/admin/council/reset-role")
def admin_reset_council_role(payload: CouncilResetRoleRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    from r20_backend.council_manager import reset_role_template
    saved = reset_role_template(payload.role_id)
    audit_record("council.role.reset", "success", {"actor": actor["username"], "role_id": payload.role_id})
    return {"status": "ok", "role_id": payload.role_id, "config": saved}


@router.get("/api/v1/admin/council/export")
def admin_export_council_config(x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    require_admin_header(x_r20_session=x_r20_session)
    from r20_backend.council_manager import export_council_config
    return export_council_config()


@router.post("/api/v1/admin/council/import")
def admin_import_council_config(payload: CouncilImportRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    actor = require_superadmin(x_r20_session)
    from r20_backend.council_manager import import_council_config
    try:
        result = import_council_config(payload.payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit_record("council.config.import", "success", {
        "actor": actor["username"],
        "roles": result.get("roles"),
        "backup_file": result.get("backup_file"),
    })
    return {"status": "ok", **result}


@router.post("/api/v1/admin/council/test")
def admin_test_council_debate(payload: CouncilTestRequest, x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> dict[str, Any]:
    require_admin_header(x_r20_session=x_r20_session)
    from r20_backend.council_manager import execute_council_debate, load_council_config
    from scripts.instrument_pool import load_instruments
    c_cfg = load_council_config()

    context_meta = {"source": "live", "missing": []}
    if payload.mock_market_prompt:
        test_market = payload.mock_market_prompt
        context_meta = {"source": "manual_mock", "missing": [], "note": "管理员显式提供的演练文本"}
    else:
        # 审计 P1-4c：旧实现在这里整套编造（固定时间 2026-09-05、可用资金 1450U、BTC 多单 77200、
        # 挂单 ord_10283），且按 factor_data[sym] 取值——真实文件结构是
        # {timestamp,time_str,instruments[]}，于是每个字段都落到 fallback：现价 $0、ADX 22.5、ATR 0。
        # 而 UI 写着"投委会正在全息审阅资金与行情并组织交易员辩论"。现在只用真实快照，
        # 缺什么就写"不可用"，不再编任何数字。
        lines = ["======================= 【推演基准与资金持仓（真实快照）】 ======================="]
        snap_ts = ""
        instruments = []
        try:
            factor_snap_file = ROOT / "data" / "factor_library_snapshot.json"
            if factor_snap_file.is_file():
                payload_snap = json.loads(factor_snap_file.read_text(encoding="utf-8"))
                if isinstance(payload_snap, dict):
                    snap_ts = str(payload_snap.get("time_str") or payload_snap.get("timestamp") or "")
                    instruments = [i for i in (payload_snap.get("instruments") or []) if isinstance(i, dict)]
        except Exception as exc:
            context_meta["missing"].append(f"因子快照读取失败: {str(exc)[:80]}")
        lines.append(f"【推演基准时间】: {snap_ts or '—（因子快照不可用）'}")

        account = positions = pending = None
        try:
            import r20_backend.dashboard_cache as dashboard_app
            cache = getattr(dashboard_app, "CACHE_DATA", None)
            if isinstance(cache, dict):
                account = cache.get("account") if isinstance(cache.get("account"), dict) else None
                positions = cache.get("positions") if isinstance(cache.get("positions"), list) else None
                pending = cache.get("pending_orders") if isinstance(cache.get("pending_orders"), list) else None
            else:
                context_meta["missing"].append("dashboard 缓存不可用")
        except Exception as exc:
            context_meta["missing"].append(f"dashboard 缓存读取失败: {str(exc)[:80]}")

        if account:
            lines.append(f"【当前账户可用资金】: {account.get('avail_eq', '—')} USDT"
                         f"（总权益 {account.get('total_eq', '—')} USDT）")
        else:
            context_meta["missing"].append("账户快照")
            lines.append("【当前账户可用资金】: —（账户快照不可用）")
        if positions is None:
            context_meta["missing"].append("持仓快照")
            lines.append("【当前活动在途持仓明细】: —（持仓快照不可用）")
        else:
            lines.append(f"【当前活动在途持仓明细】(共 {len(positions)} 笔):")
            for pos in positions[:8]:
                if not isinstance(pos, dict):
                    continue
                lines.append(
                    f"- 标的: {pos.get('instId', '—')} | 场所: {pos.get('venue', '—')} | 方向: {pos.get('posSide', '—')}"
                    f" | 持仓量: {pos.get('pos', '—')} | 未结浮盈: {pos.get('upl', '—')}"
                )
            if not positions:
                lines.append("- （当前无持仓）")
        if pending is None:
            context_meta["missing"].append("挂单快照")
            lines.append("【当前在途挂单列表】: —（挂单快照不可用）")
        else:
            lines.append(f"【当前在途挂单列表】(共 {len(pending)} 笔):")
            for od in pending[:8]:
                if not isinstance(od, dict):
                    continue
                lines.append(f"- [挂单ID: {od.get('ordId') or od.get('id') or '—'}] {od.get('instId', '—')}"
                             f" | 价格: {od.get('px', '—')} | 数量: {od.get('sz', '—')}")
            if not pending:
                lines.append("- （当前无挂单）")

        lines.append("")
        lines.append(f"======================= 【市场全要素动力学与微结构实时快照 ({len(instruments)} 大主力标的)】 =======================")
        if not instruments:
            lines.append("- —（因子快照不可用，禁止臆造行情）")
        for item in instruments:
            momentum = item.get("trend_momentum") if isinstance(item.get("trend_momentum"), dict) else {}
            vol = item.get("volatility_channel") if isinstance(item.get("volatility_channel"), dict) else {}
            flow = item.get("volume_money_flow") if isinstance(item.get("volume_money_flow"), dict) else {}
            smart = item.get("smart_money_derivatives") if isinstance(item.get("smart_money_derivatives"), dict) else {}
            lines.append(
                f"- {item.get('instId', '—')}: 现价 ${item.get('price', '—')} ({item.get('chg24h', '—')}%), "
                f"ADX={momentum.get('adx_1h', '—')}, RSI={momentum.get('rsi_14', '—')}, 趋势={momentum.get('trend_regime', '—')}, "
                f"1H ATR={vol.get('atr_1h', '—')}, CMF={flow.get('cmf_1h', '—')}, "
                f"聪明钱多头={smart.get('weighted_long_pct', '—')}%"
            )
        context_meta["factor_time"] = snap_ts or None
        context_meta["instruments"] = len(instruments)
        test_market = "\n".join(lines)

    from scripts.prompt_library import active_profile, compile_modules, apply_module_layout
    try:
        prof = active_profile()
        sys_mods = prof.get("pipelines", {}).get("trading_system", [])
        test_sys = apply_module_layout(compile_modules(sys_mods), {}, "trading_system", "委员会测试", context={"market_matrix": test_market, "profile_name": prof.get("name", "")}) if sys_mods else "你是 AstraQuant 首席量化官，执行多空对称顺势战法与 2.0x ATR 宽止损。"
    except Exception:
        test_sys = "你是一个遵循多空对称顺势、1.8~2.2x ATR 宽止损与 0.8R 保本锁利的量化交易系统。"

    try:
        brain_output, transcript = execute_council_debate(
            market_prompt=test_market,
            original_system_prompt=test_sys,
            timeout=float(c_cfg.get("timeout_seconds", 60.0)),
            # 审计 P1-4d：席位提示词变量用与交易侧同名的上下文渲染（缺值标 MISSING，不吞）
            runtime_context={
                "market_matrix": test_market,
                "profile_name": str((c_cfg.get("roles") or {}).get("cio", {}).get("name") or ""),
                "trading_memory": "",
            },
        )
        return {
            "status": "ok",
            "brain_output": brain_output,
            "transcript": transcript,
            # 审计 P1-4c：本次辩论用的上下文到底来自哪里、缺了什么，一并如实返回
            "market_context": context_meta,
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "market_context": context_meta,
        }

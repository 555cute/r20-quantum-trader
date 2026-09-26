"""Public dashboard, real-time market data, and cache endpoints."""
from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

from r20_backend.config import settings, refresh_settings
from r20_backend.time_utils import beijing_day
from r20_backend.version import APP_SITE
from r20_backend.dependencies import (
    ROOT, DATA_DIR, VUE_DIST, okx, read_json, require_admin_header,
)
import r20_backend.dashboard_cache as dash_app
from r20_backend.web_shell import serve_vue_spa, templates

router = APIRouter(tags=["dashboard"])

_CANDLES_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


@router.get("/api/all")
async def get_all_data(full: bool = Query(False, description="返回完整载荷（含全部历史明细/台账/日志）")):
    """默认瘦身载荷（体积约为完整版的 1/2），省略项见响应里的 `_meta.omitted`。

    需要旧版逐字节一致的行为时用 `?full=1`；历史明细走 `/api/v1/cache/brain-history`。
    """
    return await dash_app.get_all_data(full=full)


@router.get("/api/overview")
async def get_overview():
    return await dash_app.get_overview()


@router.get("/api/v1/cache/{resource}")
def cache(resource: str, x_r20_admin_token: str | None = Header(default=None), x_r20_session: str | None = Header(default=None, alias="X-R20-Session")) -> JSONResponse:
    allowed = {
        "decisions": "ai_brain_decisions.json",
        "factors": "factor_library_snapshot.json",
        "ledger": "trading_ledger.json",
        "sentiment": "news_sentiment.json",
        "self-improvement": "self_improvement_report.json",
        # 审计#1：/api/all 默认只带最近 8 条完整明细，完整历史由此端点按需取
        # （与 /api/all 同级的公开面——该文件本就是首页时间线的数据源，不含密钥）
        "brain-history": "ai_brain_history.json",
    }
    filename = allowed.get(resource)
    if not filename:
        raise HTTPException(status_code=404, detail="unknown cache resource")
    if resource == "ledger":
        require_admin_header(x_r20_admin_token, x_r20_session)
    return JSONResponse(read_json(filename, {} if resource != "ledger" else []))


@router.get("/api/v1/market/{inst_id}")
def market(inst_id: str) -> dict[str, Any]:
    if not inst_id.endswith("-SWAP"):
        raise HTTPException(status_code=400, detail="only SWAP instrument ids are accepted")
    try:
        ticker = okx.ticker(inst_id)
        return {"instId": inst_id, "ticker": ticker[0] if ticker else {}, "source": "OKX REST"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OKX market request failed: {exc}") from exc


@router.get("/api/v1/market/{inst_id}/candles")
def market_candles(inst_id: str, bar: str = "1H", limit: int = 150, response: Response = None) -> dict[str, Any]:
    if response:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    if not inst_id.endswith("-SWAP"):
        raise HTTPException(status_code=400, detail="only SWAP instrument ids are accepted")
    try:
        from scripts.market_data_service import normalize_bar as _nb
        bar = _nb(bar)
    except Exception:
        pass
    valid_bars = {"1m", "3m", "5m", "15m", "30m", "1H", "2H", "4H", "6H", "12H", "1D"}
    if bar not in valid_bars:
        bar = "1H"
    limit = max(10, min(limit, 300))
    cache_key = f"{inst_id}:{bar}:{limit}"
    now_ts = time.time()
    cached = _CANDLES_CACHE.get(cache_key)
    if cached and (now_ts - cached[0] < 1.0):
        return {"instId": inst_id, "bar": bar, "candles": cached[1], "source": "cache"}
    try:
        from scripts.market_data_service import fetch_candles as _fetch_candles
        raw = _fetch_candles(inst_id, bar=bar, limit=limit, timeout=5.0)
        candles = []
        for item in reversed(raw or []):
            try:
                candles.append({
                    "ts": int(item[0]),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "vol": float(item[5]),
                })
            except (ValueError, IndexError):
                continue
        if candles:
            _CANDLES_CACHE[cache_key] = (now_ts, candles)
            return {"instId": inst_id, "bar": bar, "candles": candles, "source": "OKX REST"}
        raise RuntimeError("upstream returned no candles (all fallback levels exhausted)")
    except Exception as exc:
        # 审计 C5（数据诚实）：删除旧「factor_fallback 合成 OHLC 爬升曲线」分支——
        # 行情全链失败时渲染形状与真 K 线不可分辨，是「错误数据伪装错误提示」。
        # 兜底改为：有界陈旧缓存（≤5 分钟，显式 stale_cache+warn）否则空数据+error。
        if cached and (now_ts - cached[0] <= 300.0):
            return {"instId": inst_id, "bar": bar, "candles": cached[1], "source": "stale_cache",
                    "cache_age_seconds": round(now_ts - cached[0], 1), "warn": str(exc)}
        return {"instId": inst_id, "bar": bar, "candles": [], "source": "error", "detail": str(exc)}


@router.get("/api/v1/equity_history")
def equity_history(days: int = 14) -> dict[str, Any]:
    """权益迷你曲线（公开，与 /api/all 同级暴露）：
    初始资金 + 按北京时区自然日累计的已实现净盈亏（含手续费口径以台账 net_pnl 为准）。
    仅用于前端 sparkline 形状，精确数值以账户接口为准。"""
    import datetime as _dt
    try:
        days = max(5, min(60, int(days)))
    except Exception:
        days = 14
    tz8 = _dt.timezone(_dt.timedelta(hours=8))
    out: dict[str, Any] = {"days": [], "initial_capital": None, "source": "trading_ledger"}
    try:
        try:
            from scripts.okx_runtime import current_environment
            _env_mode = str(current_environment().mode).strip().lower()
        except Exception:
            _env_mode = ""
        out["environment"] = _env_mode or None
        init_cap = None
        try:
            p_init = ROOT / "data" / "account_initial_state.json"
            if p_init.exists():
                init_cap = float(json.loads(p_init.read_text("utf-8")).get("initial_capital") or 0) or None
        except Exception:
            init_cap = None
        p_led = ROOT / "data" / "trading_ledger.json"
        daily: dict[str, float] = {}
        excluded_env = 0
        legacy_unlabeled = 0
        if p_led.exists():
            data = json.loads(p_led.read_text("utf-8"))
            rows = data if isinstance(data, list) else data.get("trades", []) or data.get("records", [])
            for r in rows:
                if not isinstance(r, dict):
                    continue
                # 审计 C4-1：只认显式结清状态；旧实现给「状态未知但有 close_time」
                # 的行留了后门（重命名/新增状态会被当已平计入曲线）。
                if str(r.get("status", "")).strip().lower() not in ("closed", "已平仓", "completed"):
                    continue
                # 审计 C4-2：环境隔离——demo↔live 切换后旧环境行不再混算进当前曲线；
                # 无环境列的旧行保留但计数暴露（数据诚实）。
                row_env = str(r.get("environment") or "").strip().lower()
                if _env_mode:
                    if row_env and row_env != _env_mode:
                        excluded_env += 1
                        continue
                    if not row_env:
                        legacy_unlabeled += 1
                ct = str(r.get("close_time") or "")
                if len(ct) < 10:
                    continue
                day = beijing_day(ct)
                if not day:
                    continue
                try:
                    daily[day] = daily.get(day, 0.0) + float(r.get("net_pnl") or r.get("pnl") or 0.0)
                except (TypeError, ValueError):
                    pass
        out["excluded_rows"] = {"other_environment": excluded_env, "unlabeled_environment": legacy_unlabeled}
        base = init_cap if init_cap is not None else 0.0
        out["initial_capital"] = init_cap
        today = _dt.datetime.now(tz8).date()
        start = today - _dt.timedelta(days=days - 1)
        if daily:
            earliest = min(daily.keys())
            try:
                ed = _dt.date.fromisoformat(earliest)
                if ed < start:
                    base = base + sum(v for k, v in daily.items() if k < start.isoformat())
            except ValueError:
                pass
        cum = base
        series = []
        d = start
        while d <= today:
            cum += daily.get(d.isoformat(), 0.0)
            series.append({"date": d.isoformat(), "equity": round(cum, 2)})
            d += _dt.timedelta(days=1)
        out["days"] = series
    except Exception as exc:
        out["error"] = str(exc)
    return out


@router.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    f = VUE_DIST / "robots.txt"
    if f.is_file():
        return FileResponse(str(f), media_type="text/plain", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    pf = ROOT / "frontend" / "public" / "robots.txt"
    if pf.is_file():
        return FileResponse(str(pf), media_type="text/plain", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    return PlainTextResponse(
        "User-agent: *\nAllow: /\nAllow: /docs\nAllow: /images/\n"
        "Disallow: /admin/\nDisallow: /api/\n"
        f"Sitemap: {APP_SITE}/sitemap.xml\n"
    )


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    f = VUE_DIST / "sitemap.xml"
    if f.is_file():
        return FileResponse(str(f), media_type="application/xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    pf = ROOT / "frontend" / "public" / "sitemap.xml"
    if pf.is_file():
        return FileResponse(str(pf), media_type="application/xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=604800"})
    return Response(content=f"""<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{APP_SITE}/</loc><priority>1.0</priority></url><url><loc>{APP_SITE}/docs</loc><priority>0.8</priority></url></urlset>""", media_type="application/xml")


@router.get("/docs/images/{img_name}", include_in_schema=False)
def docs_images(img_name: str):
    clean_name = Path(img_name).name
    img_path = ROOT / "docs" / "images" / clean_name
    if img_path.exists() and img_path.is_file():
        media_type = "image/png" if clean_name.endswith(".png") else "image/jpeg" if clean_name.endswith((".jpg", ".jpeg")) else "image/svg+xml" if clean_name.endswith(".svg") else "application/octet-stream"
        return FileResponse(str(img_path), media_type=media_type, headers={"Cache-Control": "public, max-age=604800, s-maxage=86400"})
    raise HTTPException(status_code=404, detail="图片不存在")


@router.api_route("/trading", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/factors", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/news", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/lab", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/history", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/docs", methods=["GET", "HEAD"], include_in_schema=False)
@router.api_route("/docs/{subpath:path}", methods=["GET", "HEAD"], include_in_schema=False)
def serve_vue_spa_subroutes(subpath: str = "") -> Response:
    vue_index = VUE_DIST / "index.html"
    if vue_index.is_file():
        return serve_vue_spa(str(vue_index), is_public=True)
    fallback_index = ROOT / "frontend" / "index.html"
    return serve_vue_spa(str(fallback_index), is_public=True)


@router.get("/admin", include_in_schema=False)
@router.get("/admin/{subpath:path}", include_in_schema=False)
def admin_page(subpath: str = "") -> Response:
    # 审计⑤#6(2026-09-13)：旧实现对一切 /admin/* 无脑回 Vue 壳，把 mount 副本里
    # 「真实文件优先」分支压死——LegacyRedirect.vue 专门跳转的 /admin/legacy.html
    # （磁盘真实 126KB 文件）永远打不开且无 404 信号。现真实文件优先（含路径逃逸
    # 校验），目录/未知子路径仍回 Vue 壳交给前端路由。
    if subpath:
        try:
            admin_root = (VUE_DIST / "admin").resolve()
            cand = (admin_root / subpath).resolve()
            if cand.is_file() and cand.is_relative_to(admin_root):
                return FileResponse(str(cand), headers={"Cache-Control": "no-cache"})
        except (OSError, ValueError):
            pass
    vue_index = VUE_DIST / "index.html"
    if vue_index.is_file():
        return serve_vue_spa(str(vue_index), is_public=False)
    fallback_index = ROOT / "frontend" / "index.html"
    return serve_vue_spa(str(fallback_index), is_public=False)


# ─────────────────────────────────────────────────────────────────────────────
# Web 外壳路由（结构优化阶段 2·B2 收尾，从 r20_backend/dashboard_cache.py 迁入）
#
# 这四条是阶段 1 拆除 15 条「被本 router 遮蔽、永不命中」的重复注册后，**仅存于
# r20_backend/dashboard_cache.py** 的真实路由。现随外壳一起搬到 router：
#   /favicon.svg  仅此处实现
#   /             仅此处实现（先给 Vue 壳，dist 缺失时回退 Jinja 模板）
#   /doc          路由器只注册了 /docs，没有 /doc（线上实测 200），故保留
#   /login        前端 router 里 /login 不是公开 tab，实际跳后台登录页
#                 （线上实测 307 → /admin/login）
# 注册顺序不变：本 router 在主应用里最后 include，故 / 仍落在所有其他路由之后
# （与原先 `mount("/", dashboard_app)` 的位置语义一致）。
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    f = os.path.join(str(VUE_DIST), "favicon.svg")
    if os.path.isfile(f):
        return FileResponse(f, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=2592000, immutable"})
    pf = os.path.join(str(ROOT), "frontend", "public", "favicon.svg")
    if os.path.isfile(pf):
        return FileResponse(pf, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400, s-maxage=2592000, immutable"})
    return Response(status_code=404)


@router.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def index(request: Request):
    vue_index_file = os.path.join(str(VUE_DIST), "index.html")
    if os.path.isfile(vue_index_file):
        return serve_vue_spa(vue_index_file, is_public=True)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        headers={"Cache-Control": "public, max-age=0, s-maxage=60, stale-while-revalidate=300"},
    )


@router.get("/doc", response_class=HTMLResponse, include_in_schema=False)
async def docs_spa_root(request: Request, subpath: str = ""):
    """Serve the public system documentation page in Vue SPA."""
    vue_index_file = os.path.join(str(VUE_DIST), "index.html")
    if os.path.isfile(vue_index_file):
        return serve_vue_spa(vue_index_file, is_public=True)
    return HTMLResponse("Vue build not found. Run `npm run build` in frontend/.", status_code=503)


@router.get("/login", include_in_schema=False)
async def login_redirect(request: Request):
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/admin/login")

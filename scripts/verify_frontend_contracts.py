"""Static contract audit between the Vue application and FastAPI routes."""
from __future__ import annotations
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.routing import APIRoute
from starlette.routing import Mount
from r20_backend.app import AdminConfigUpdate, FRONTEND_INDEX, app

FRONTEND_CONTRACTS = {
    ("GET", "/api/all"),
    ("POST", "/api/v1/admin/auth/login"),
    ("POST", "/api/v1/admin/auth/logout"),
    ("GET", "/api/v1/admin/auth/me"),
    ("GET", "/api/v1/admin/overview"),
    ("GET", "/api/v1/admin/gateway"),
    ("GET", "/api/v1/admin/agents"),
    ("GET", "/api/v1/admin/plugins"),
    ("GET", "/api/v1/admin/config"),
    ("PUT", "/api/v1/admin/config"),
    ("GET", "/api/v1/admin/audit"),
    ("GET", "/api/v1/admin/instruments"),
    ("GET", "/api/v1/admin/notifications"),
    ("GET", "/api/v1/admin/backups/simple"),
    ("GET", "/api/v1/admin/update-status"),
}
SPA_PATHS = {
    "/", "/admin", "/terminal", "/terminal/{page}",
}
CONFIG_PAYLOAD_FIELDS = {
    "okx_environment", "llm_base_url", "llm_model", "llm_reasoning_effort",
    "notification_webhook", "manual_close_enabled", "llm_api_key",
    "okx_demo_api_key", "okx_demo_secret_key", "okx_demo_passphrase",
}


def collect_routes(router: object, prefix: str = "") -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for route in getattr(router, "routes", []):
        if isinstance(route, APIRoute):
            path = f"{prefix}{route.path}" or "/"
            result.update((method, path) for method in route.methods if method != "HEAD")
        elif isinstance(route, Mount) and hasattr(route.app, "routes"):
            mount_path = route.path.rstrip("/")
            result.update(collect_routes(route.app, f"{prefix}{mount_path}"))
    return result


def main() -> int:
    routes = collect_routes(app)
    missing = sorted(FRONTEND_CONTRACTS - routes)
    available_spa = {path for method, path in routes if method == "GET" and path in SPA_PATHS}
    missing_spa = sorted(SPA_PATHS - available_spa)
    model_fields = set(AdminConfigUpdate.model_fields)
    unknown_config_fields = sorted(CONFIG_PAYLOAD_FIELDS - model_fields)
    frontend_sources = list((ROOT / "r20_frontend" / "src").rglob("*.vue")) + list((ROOT / "r20_frontend" / "src").rglob("*.ts"))
    missing_sources = not frontend_sources

    print("R20 frontend/backend contract audit")
    print(f"  FastAPI routes discovered: {len(routes)}")
    print(f"  Vue API contracts checked: {len(FRONTEND_CONTRACTS)}")
    print(f"  Vue source files discovered: {len(frontend_sources)}")
    print(f"  Frontend production index: {'OK' if FRONTEND_INDEX.exists() else 'MISSING'}")
    for method, path in sorted(FRONTEND_CONTRACTS):
        print(f"  {'OK' if (method, path) in routes else 'MISS':4} {method:6} {path}")

    errors = []
    if missing: errors.append(f"missing API routes: {missing}")
    if missing_spa: errors.append(f"missing SPA routes: {missing_spa}")
    if unknown_config_fields: errors.append(f"unknown admin config fields: {unknown_config_fields}")
    if missing_sources: errors.append("Vue sources are missing")
    if not FRONTEND_INDEX.exists(): errors.append("Vue production build is missing; run npm run build")
    if errors:
        print("CONTRACT AUDIT FAILED")
        for error in errors: print(f"  - {error}")
        return 1
    print("CONTRACT AUDIT PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

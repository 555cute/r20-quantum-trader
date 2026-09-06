"""Local OKX CLI/OAuth dependency diagnostics for standalone R20 deployments."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

MIN_OKX_CLI = (1, 4, 4)
INSTALL_COMMAND = "npm install -g @okx_ai/okx-trade-cli@^1.4.4"
LATEST_VERSION = "1.4.5"


def check_node_npm() -> dict[str, Any]:
    """Check Node.js and npm availability without leaking anything."""
    node = shutil.which("node")
    npm = shutil.which("npm")
    node_version = ""
    npm_version = ""
    if node:
        r = _run([node, "--version"], timeout=8)
        node_version = r["stdout"].replace("v", "") if r["ok"] else ""
    if npm:
        r = _run([npm, "--version"], timeout=8)
        npm_version = r["stdout"] if r["ok"] else ""
    okx = shutil.which("okx")
    okx_version = ""
    if okx:
        r = _run([okx, "--version"], timeout=8)
        okx_version = r["stdout"].splitlines()[0] if r["ok"] and r["stdout"] else ""
    return {
        "node_installed": bool(node),
        "node_path": node or "",
        "node_version": node_version,
        "npm_installed": bool(npm),
        "npm_path": npm or "",
        "npm_version": npm_version,
        "prerequisites_ready": bool(node and npm),
        "okx_installed": bool(okx),
        "okx_path": okx or "",
        "okx_version": okx_version,
        "okx_supported": _version_tuple(okx_version) >= MIN_OKX_CLI,
        "ready": bool(node and npm and okx and _version_tuple(okx_version) >= MIN_OKX_CLI),
    }


def install_okx_cli() -> dict[str, Any]:
    """Install or upgrade OKX CLI via npm. Returns the result and post-install diagnostics."""
    prereq = check_node_npm()
    if not prereq.get("prerequisites_ready", prereq.get("ready", False)):
        return {
            "ok": False,
            "detail": f"Node.js/npm 未安装（node={prereq['node_path']}, npm={prereq['npm_path']}）",
            "prerequisite": prereq,
            "install_command": INSTALL_COMMAND,
        }
    npm_bin = prereq["npm_path"]
    existing_binary = shutil.which("okx")
    existing_version = ""
    if existing_binary:
        current = _run([existing_binary, "--version"], timeout=8)
        existing_version = current["stdout"].splitlines()[0] if current["stdout"] else ""
    r = _run([npm_bin, "install", "-g", "@okx_ai/okx-trade-cli@^1.4.4"], timeout=120)
    if not r["ok"]:
        return {
            "ok": False,
            "detail": f"npm install 失败：{r['stderr'] or r['stdout'] or 'unknown error'[:500]}",
            "prerequisite": prereq,
            "install_command": INSTALL_COMMAND,
        }
    # Verify post-install
    binary = shutil.which("okx")
    if not binary:
        return {
            "ok": False,
            "detail": "安装完成但 PATH 中仍找不到 okx；请检查 npm global bin 目录是否在服务 PATH 中",
            "prerequisite": prereq,
            "install_command": INSTALL_COMMAND,
        }
    vr = _run([binary, "--version"], timeout=8)
    version = vr["stdout"].splitlines()[0] if vr["stdout"] else ""
    operation = "升级" if existing_binary and _version_tuple(version) > _version_tuple(existing_version) else "安装/校验"
    return {
        "ok": True,
        "detail": f"OKX CLI {operation}成功：{binary} v{version}",
        "path": binary,
        "version": version,
        "previous_version": existing_version,
        "restart_gateway_recommended": bool(existing_binary and _version_tuple(version) != _version_tuple(existing_version)),
        "prerequisite": prereq,
    }


def _run(command: list[str], timeout: int = 12, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=dict(env or os.environ))
    except FileNotFoundError:
        return {"ok": False, "returncode": 127, "stdout": "", "stderr": f"{command[0]} not found"}
    except Exception as exc:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _json_output(result: dict[str, Any]) -> Any:
    if not result.get("ok") or not result.get("stdout"):
        return None
    try:
        return json.loads(result["stdout"])
    except json.JSONDecodeError:
        return None


def _oauth_identity(payload: Any) -> str:
    """Return a non-sensitive account label only when the auth binary exposes one."""
    if not isinstance(payload, dict):
        return ""
    for key in ("account", "accountName", "nickname", "emailMasked", "uidMasked", "uid"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _is_upstream_unavailable(detail: str) -> bool:
    text = detail.lower()
    return any(token in text for token in ("http 503", "50001", "50013", "systems are busy", "service temporarily unavailable"))


def _clean_probe_detail(result: Mapping[str, Any]) -> str:
    """Keep the actionable OKX error while removing the CLI upgrade advertisement."""
    detail = str(result.get("stderr") or result.get("stdout") or "invalid response")
    lines = detail.splitlines()
    filtered: list[str] = []
    skip_upgrade_command = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Update available for @okx_ai/okx-trade-cli:"):
            skip_upgrade_command = True
            continue
        if skip_upgrade_command and stripped.startswith("Run: npm install -g @okx_ai/okx-trade-cli"):
            skip_upgrade_command = False
            continue
        if stripped:
            filtered.append(stripped)
    return "\n".join(filtered)[:500] or "invalid response"


def start_oauth_device_login(site: str, *, force_relogin: bool = False, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Start OKX OAuth RFC8628 device flow and return the public verification fields."""
    if site not in {"global", "eea", "us", "tr"}:
        raise ValueError("不支持的 OKX 站点")
    binary = shutil.which("okx", path=(env or os.environ).get("PATH"))
    if not binary:
        raise RuntimeError("OKX CLI 未安装或服务 PATH 中不可见")
    config = _json_output(_run([binary, "config", "show", "--json"], env=env))
    profiles = _api_key_profiles(config)
    if profiles:
        raise RuntimeError(f"已配置 API Key Profile（{', '.join(profiles)}）；CLI 会优先使用 API Key，请先删除或切换该 Profile 后再授权 OAuth")
    current = _json_output(_run([binary, "auth", "status", "--json"], env=env))
    if isinstance(current, dict) and current.get("status") == "logged_in":
        if force_relogin:
            oauth_logout(env=env)
        else:
            return {
                "status": "already_logged_in",
                "site": str(current.get("site") or site),
                "scopes": [str(item) for item in current.get("scopes", []) if item],
                "account_label": _oauth_identity(current),
            }
    result = _run([binary, "auth", "login", "--manual", "--site", site], timeout=30, env=env)
    payload = _json_output(result)
    if not result["ok"] or not isinstance(payload, dict):
        raise RuntimeError(result["stderr"] or result["stdout"] or "无法启动 OKX OAuth 授权")
    verification_uri = str(payload.get("verificationUri") or payload.get("verification_uri") or "")
    user_code = str(payload.get("userCode") or payload.get("user_code") or "")
    expires_in = int(payload.get("expiresIn") or payload.get("expires_in") or 0)
    if not verification_uri or not user_code or expires_in <= 0:
        raise RuntimeError("OKX OAuth 未返回有效授权链接或授权码")
    return {"status": "pending", "site": site, "verification_uri": verification_uri, "user_code": user_code, "expires_in": expires_in}


def oauth_status(*, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    binary = shutil.which("okx", path=(env or os.environ).get("PATH"))
    if not binary:
        return {"status": "unavailable", "site": "", "scopes": [], "account_label": ""}
    result = _run([binary, "auth", "status", "--json"], env=env)
    payload = _json_output(result)
    if not isinstance(payload, dict):
        return {"status": "unavailable", "site": "", "scopes": [], "account_label": "", "detail": result["stderr"] or "OAuth 状态不可读"}
    return {
        "status": str(payload.get("status") or "unknown"),
        "site": str(payload.get("site") or ""),
        "scopes": [str(item) for item in payload.get("scopes", []) if item],
        "account_label": _oauth_identity(payload),
    }


def oauth_logout(*, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Clear OKX OAuth tokens and session state via the official OKX CLI / okx-auth broker.
    This unbinds the current OKX account so a new account can be connected or re-authorized."""
    binary = shutil.which("okx", path=(env or os.environ).get("PATH"))
    if not binary:
        raise RuntimeError("OKX CLI 未安装或服务 PATH 中不可见")
    result = _run([binary, "auth", "logout"], env=env, timeout=15)
    # Also clean up token file directly if it exists to guarantee clean unbind
    cred_file = Path.home() / ".okx" / "oauth" / "profiles" / "oauth" / "credentials.enc"
    try:
        if cred_file.exists():
            cred_file.unlink()
    except Exception:
        pass
    if not result["ok"] and "not logged in" not in (result["stderr"] + result["stdout"]).lower():
        # If logout command failed for an unexpected reason, report it
        raise RuntimeError(result["stderr"] or result["stdout"] or "OKX 授权解绑失败")
    return {"status": "logged_out", "message": "OKX OAuth 账号已成功解绑"}


def _version_tuple(text: str) -> tuple[int, int, int]:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(value) for value in match.groups()) if match else (0, 0, 0)


def _api_key_profiles(payload: Any) -> list[str]:
    profiles = payload.get("profiles", {}) if isinstance(payload, dict) else {}
    if isinstance(profiles, list):
        return [str(item.get("name") or item.get("profile") or "default") for item in profiles if isinstance(item, dict) and item.get("api_key")]
    if isinstance(profiles, dict):
        return [str(name) for name, item in profiles.items() if isinstance(item, dict) and item.get("api_key")]
    return []


def diagnose_okx_runtime(selected_mode: str, static_configured: bool, *, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return secret-free readiness information; performs authenticated read-only probes only."""
    selected_mode = "live" if selected_mode == "live" else "demo"
    binary = shutil.which("okx", path=(env or os.environ).get("PATH"))
    status: dict[str, Any] = {
        "selected_mode": selected_mode,
        "cli": {"installed": bool(binary), "path": binary or "", "version": "", "supported": False},
        "oauth": {"status": "unavailable", "site": "", "scopes": [], "ready_for_selected_mode": False, "account_label": ""},
        "api_key_profiles": [],
        "static_credentials_configured": bool(static_configured),
        "credential_source": "static-v5-key" if static_configured else "none",
        "read_probe": {"ok": False, "detail": "not run"},
        "ready": False,
        "issues": [],
        "steps": [],
        "install_command": INSTALL_COMMAND,
    }
    if not binary:
        status["issues"].append("OKX CLI 未安装或服务 PATH 中不可见")
        status["steps"].extend(["安装 Node.js 18+ 与 npm", f"执行：{INSTALL_COMMAND}", "安装后重启 r20-backend 与 r20-gateway"])
        return status

    version_result = _run([binary, "--version"], env=env)
    version = version_result["stdout"].splitlines()[0] if version_result["stdout"] else ""
    status["cli"].update({"version": version, "supported": _version_tuple(version) >= MIN_OKX_CLI})
    if not status["cli"]["supported"]:
        status["issues"].append(f"OKX CLI 版本过旧：{version or 'unknown'}，最低需要 1.4.4")
        status["steps"].append(f"执行：{INSTALL_COMMAND}")

    config_payload = _json_output(_run([binary, "config", "show", "--json"], env=env))
    status["api_key_profiles"] = _api_key_profiles(config_payload)
    auth_result = _run([binary, "auth", "status", "--json"], env=env)
    auth_payload = _json_output(auth_result)
    if isinstance(auth_payload, dict):
        oauth_status = str(auth_payload.get("status") or "unknown")
        scopes = [str(item) for item in auth_payload.get("scopes", []) if item]
        required = {"market:read", f"{selected_mode}:read", f"{selected_mode}:trade"}
        oauth_ready = oauth_status == "logged_in" and required.issubset(set(scopes))
        status["oauth"] = {
            "status": oauth_status,
            "site": str(auth_payload.get("site") or ""),
            "scopes": scopes,
            "ready_for_selected_mode": oauth_ready,
            "account_label": _oauth_identity(auth_payload),
        }
    else:
        oauth_ready = False
        status["oauth"]["detail"] = auth_result["stderr"] or "无法读取 OAuth 状态"

    if static_configured:
        status["credential_source"] = "static-v5-key"
    elif oauth_ready:
        status["credential_source"] = "cli-oauth"
    elif status["api_key_profiles"]:
        status["credential_source"] = "cli-api-key-profile"

    mode_flag = f"--{selected_mode}"
    if status["cli"]["supported"] and (static_configured or oauth_ready or status["api_key_profiles"]):
        probe_env = dict(env or os.environ)
        if static_configured:
            prefix = "OKX_DEMO" if selected_mode == "demo" else "OKX_LIVE"
            mappings = {"OKX_API_KEY": f"{prefix}_API_KEY", "OKX_SECRET_KEY": f"{prefix}_SECRET_KEY", "OKX_PASSPHRASE": f"{prefix}_PASSPHRASE"}
            for target, source in mappings.items():
                if probe_env.get(source):
                    probe_env[target] = str(probe_env[source])
            probe_env["OKX_DEMO"] = "1" if selected_mode == "demo" else "0"
        probe = _run([binary, mode_flag, "account", "positions", "--json"], env=probe_env)
        payload = _json_output(probe)
        status["read_probe"] = {
            "ok": probe["ok"] and isinstance(payload, list),
            "detail": "OKX 私有只读探针通过" if probe["ok"] and isinstance(payload, list) else _clean_probe_detail(probe),
        }

        # OAuth itself may be healthy while OKX rejects only the simulated
        # private environment. A LIVE read is a safe control probe: it never
        # changes R20's selected environment and never submits an order.
        if selected_mode == "demo" and oauth_ready and not static_configured and not status["read_probe"]["ok"]:
            control = _run([binary, "--live", "account", "positions", "--json"], env=probe_env)
            control_payload = _json_output(control)
            control_ok = bool(control["ok"] and isinstance(control_payload, list))
            status["live_control_probe"] = {
                "ok": control_ok,
                "detail": "同一 OAuth 的 LIVE 私有只读探针通过" if control_ok else _clean_probe_detail(control),
            }
            status["demo_oauth_unavailable"] = bool(control_ok and _is_upstream_unavailable(status["read_probe"]["detail"]))

    status["degraded"] = bool(oauth_ready and not status["read_probe"]["ok"] and _is_upstream_unavailable(status["read_probe"]["detail"]))
    status["auth_ready"] = bool(status["cli"]["supported"] and status["credential_source"] != "none")
    status["ready"] = bool(status["auth_ready"] and status["read_probe"]["ok"])
    if not static_configured and not oauth_ready and not status["api_key_profiles"]:
        status["issues"].append(f"未找到可用于 {selected_mode.upper()} 的 OKX 凭证或 OAuth 授权")
        status["steps"].extend([
            "在部署 R20 的同一 Linux 用户下执行 OAuth 登录，或在后台填写对应环境 API Key",
            "OAuth：先运行 okx config show --json 与 okx auth status --json，再明确选择 global/eea/us/tr 站点",
            "随后运行：okx auth login --manual --site <站点>，在浏览器完成授权",
        ])
    elif not status["read_probe"]["ok"]:
        detail = status["read_probe"]["detail"]
        if status.get("demo_oauth_unavailable"):
            status["issues"].append("OAuth 授权本身正常，但 OKX 当前拒绝 DEMO 模拟盘私有接口；LIVE 只读对照探针已通过")
            status["steps"].extend([
                "系统保持 DEMO 且 Fail-Closed，不会自动切换 LIVE 或在状态不明时下单",
                "这不是 CLI 安装或 OAuth 登录失败，重复安装、重复授权或短时间反复重试无法修复",
                "若必须继续使用模拟盘，请改用 OKX 模拟盘 API Key；否则等待 OKX 修复 DEMO OAuth 服务",
            ])
        elif _is_upstream_unavailable(detail):
            status["issues"].append("OKX 当前环境私有接口暂不可用；系统已 Fail-Closed，不会在状态不明时下单")
            status["steps"].append("稍后重新诊断；无需重新安装 CLI 或重新授权 OAuth")
        else:
            status["issues"].append("OKX 凭证存在，但当前环境私有只读探针失败")
            status["steps"].append("检查授权是否包含当前环境 read/trade 权限，以及服务 HOME/PATH 是否与登录用户一致")
    if status["ready"]:
        status["steps"].append("运行依赖与当前环境授权已就绪；建议先在 DEMO 完成下单、撤单、平仓和 OCO 验证")
    return status

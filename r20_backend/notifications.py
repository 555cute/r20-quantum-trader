"""R20-native notification fan-out. No QwenPaw/OpenClaw runtime dependency."""
from __future__ import annotations
import base64
import datetime
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from r20_backend.net_security import validate_outbound_url
from r20_backend.config_path import env_file_path

ROOT = Path(__file__).resolve().parents[1]
SECRET_LOADER = None
QQ_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
QQ_API_BASE = "https://api.sgroup.qq.com"


def _env() -> dict[str, str]:
    values: dict[str, str] = {}
    path = env_file_path(ROOT)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    # Dynamic encrypted secrets override both stale inherited values and legacy .env values.
    try:
        if SECRET_LOADER is not None:
            encrypted = SECRET_LOADER()
        elif ROOT == Path(__file__).resolve().parents[1]:
            from r20_gateway.secrets import load_secrets
            encrypted = load_secrets()
        else:
            encrypted = {}
    except Exception: encrypted = {}
    return {**os.environ, **values, **encrypted}


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[bool, str, dict[str, Any]]:
    request_headers = {"Content-Type": "application/json", "User-Agent": "R20-Standalone/6.6.2"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                data = {"raw": raw}
            return 200 <= response.status < 300, f"HTTP {response.status}", data
    except Exception as exc:
        return False, str(exc), {}


def _send_qq(env: dict[str, str], message: str) -> tuple[bool, str]:
    app_id = env.get("R20_QQ_APP_ID", "").strip()
    secret = env.get("R20_QQ_CLIENT_SECRET", "").strip()
    openid = env.get("R20_QQ_OPENID", "").strip()
    if not app_id or not secret or not openid:
        return False, "QQ App ID / Client Secret / OpenID 未完整配置；可在后台点击「⚡ 自动获取 OpenID」完成绑定"
    ok, detail, token_data = _post_json(QQ_TOKEN_URL, {"appId": app_id, "clientSecret": secret})
    access_token = token_data.get("access_token") if ok else ""
    if not access_token:
        return False, f"QQ access token 获取失败：{detail} code={token_data.get('code','')} message={token_data.get('message','')}"
    sequence = int(datetime.datetime.now().timestamp() * 1000) % 1_000_000

    # Differentiate between Group OpenID and C2C User OpenID
    is_group = openid.startswith(("group_", "GRP_", "group-")) or len(openid) > 40
    endpoint = f"{QQ_API_BASE}/v2/groups/{urllib.parse.quote(openid, safe='')}/messages" if is_group else f"{QQ_API_BASE}/v2/users/{urllib.parse.quote(openid, safe='')}/messages"

    ok, detail, response = _post_json(
        endpoint,
        {"content": message, "msg_type": 0, "msg_seq": sequence},
        {"Authorization": f"QQBot {access_token}"},
    )
    if not ok:
        err_code = response.get("code")
        if err_code == 11255:
            return False, "QQ 拒绝发送：目标用户 OpenID 不存在或与当前 Bot 不匹配 (错误码 11255)；请在后台点击「⚡ 自动获取 OpenID」向机器人发送消息重新捕获"
        return False, f"{detail} {response}"
    if response.get("code") not in (None, 0, "0") or (response.get("message") and not response.get("id")):
        err_code = response.get("code")
        if err_code == 11255:
            return False, "QQ 拒绝发送：目标用户 OpenID 不存在或与当前 Bot 不匹配 (错误码 11255)；请在后台点击「⚡ 自动获取 OpenID」向机器人发送消息重新捕获"
        return False, f"QQ 业务拒绝 code={err_code} message={response.get('message','')}"
    return True, f"accepted: id={response.get('id') or response.get('message_id') or '--'}"


def enabled_channels(env: dict[str, str] | None = None) -> list[str]:
    env = env or _env()
    channels = []
    for channel in ("webhook", "wechat", "telegram", "qq"):
        if env.get(f"R20_NOTIFY_{channel.upper()}_ENABLED") == "1":
            channels.append(channel)
    return channels


def diagnose_channel(channel: str, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Configuration-only diagnosis. It never sends a user-visible message."""
    env = env or _env()
    required = {
        "webhook": ("R20_NOTIFICATION_WEBHOOK",),
        "wechat": ("R20_WECHAT_WEBHOOK",),
        "telegram": ("R20_TELEGRAM_BOT_TOKEN", "R20_TELEGRAM_CHAT_ID"),
        "qq": ("R20_QQ_APP_ID", "R20_QQ_CLIENT_SECRET", "R20_QQ_OPENID"),
    }
    if channel not in required:
        return {"status": "failed", "detail": "未知通知通道"}
    missing = [key for key in required[channel] if not env.get(key)]
    if missing:
        if channel == "qq" and missing == ["R20_QQ_OPENID"]:
            return {
                "status": "incomplete",
                "missing": missing,
                "detail": "QQ AppID 与 Secret 已就绪，但尚未获取目标 OpenID；请点击「⚡ 自动获取 OpenID」向机器人发消息完成自动绑定",
            }
        return {"status": "incomplete", "missing": missing, "detail": f"配置不完整，缺少：{', '.join(missing)}"}
    return {"status": "ready", "detail": "必要配置完整；可点击「发送测试」验证连通性"}


def send_channel(channel: str, message: str, env: dict[str, str] | None = None) -> tuple[bool, str]:
    env = env or _env()
    if channel == "webhook":
        url = env.get("R20_NOTIFICATION_WEBHOOK", "").strip()
        if not url:
            return False, "通用 Webhook URL 未配置"
        try:
            url = validate_outbound_url(url, allow_private=True)
        except ValueError as exc:
            return False, f"通用 Webhook URL 无效：{exc}"

        # Smart multi-service webhook adapter
        u_lower = url.lower()
        if "dingtalk" in u_lower or "oapi.dingtalk.com" in u_lower:
            payload = {"msgtype": "text", "text": {"content": message}}
        elif "feishu" in u_lower or "larksuite" in u_lower or "open.feishu.cn" in u_lower:
            payload = {"msg_type": "text", "content": {"text": message}}
        elif "discord.com" in u_lower or "discordapp.com" in u_lower:
            payload = {"content": message}
        elif "qyapi.weixin.qq.com" in u_lower:
            payload = {"msgtype": "text", "text": {"content": message}}
        elif "serverchan" in u_lower or "sctapi.ftqq.com" in u_lower or "sc.ftqq.com" in u_lower:
            payload = {"title": "【R20 量化通知】", "desp": message}
        elif "pushdeer" in u_lower:
            payload = {"text": "【R20 量化通知】", "desp": message}
        else:
            payload = {
                "source": "R20 Quantum Trader",
                "message": message,
                "text": message,
                "content": message,
            }

        ok, detail, response = _post_json(url, payload)
        if not ok:
            return False, f"{detail} {response}"
        if isinstance(response, dict):
            if response.get("success") is False or response.get("errcode", 0) not in (0, "0", None) or response.get("code", 0) not in (0, "0", None):
                return False, f"Webhook 业务返回异常：{response}"
        return True, f"accepted: {detail}"

    if channel == "wechat":
        url = env.get("R20_WECHAT_WEBHOOK", "").strip()
        if not url:
            return False, "企业微信 Webhook 未配置"
        try:
            url = validate_outbound_url(url, allow_private=True)
        except ValueError as exc:
            return False, f"企业微信 Webhook 无效：{exc}"
        ok, detail, response = _post_json(url, {"msgtype": "text", "text": {"content": message}})
        if not ok:
            return False, f"{detail} {response}"
        if int(response.get("errcode", -1)) != 0:
            return False, f"企业微信业务拒绝 errcode={response.get('errcode')} errmsg={response.get('errmsg','')}"
        return True, "accepted: HTTP 200 errcode=0"

    if channel == "telegram":
        token = env.get("R20_TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = env.get("R20_TELEGRAM_CHAT_ID", "").strip()
        tg_base = (env.get("R20_TELEGRAM_API_BASE", "") or "https://api.telegram.org").strip().rstrip("/")
        if not token or not chat_id:
            return False, "Telegram Bot Token / Chat ID 未完整配置"
        target_url = f"{tg_base}/bot{token}/sendMessage"
        ok, detail, response = _post_json(target_url, {"chat_id": chat_id, "text": message})
        if not ok:
            d_lower = detail.lower()
            if any(kw in d_lower for kw in ["timed out", "connection refused", "urlopen error", "temporary failure in name resolution"]):
                detail += " (排查建议：国内服务器直连 api.telegram.org 会超时，建议在后台配置 Telegram 反代 Base URL)"
            return False, f"{detail} {response}"
        if response.get("ok") is not True:
            return False, f"Telegram 业务拒绝：{response.get('description') or response}"
        return True, f"accepted: message_id={((response.get('result') or {}).get('message_id',''))}"

    if channel == "qq":
        return _send_qq(env, message)
    return False, f"未知通知通道：{channel}"



def notify(text: str) -> dict[str, str]:
    env = _env(); timestamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    message = f"【R20 Quantum Trader】{timestamp}\n{text.strip()}"; result: dict[str, str] = {}
    for channel in enabled_channels(env):
        ok, detail = send_channel(channel, message, env)
        result[channel] = f"accepted: {detail}" if ok else f"failed: {detail}"
    return result


def send_qq_message(text: str) -> bool:
    """Compatibility symbol retained for existing strategy scripts."""
    return any(value.startswith("accepted:") for value in notify(text).values())


def test_channel(channel: str) -> dict[str, str]:
    """Strictly test only the selected channel; another channel cannot mask failure."""
    env = _env()
    timestamp = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    ok, detail = send_channel(channel, f"【R20 Quantum Trader】{timestamp}\n🔔 {channel.upper()} 通知测试：指定通道连接正常。", env)
    prefix = "accepted:" if ok else "failed:"
    return {channel: f"{prefix} {detail}"}

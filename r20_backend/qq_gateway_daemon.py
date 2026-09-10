"""Persistent QQ Bot WebSocket Gateway Worker.

Maintains an active connection to Tencent's official WebSocket Gateway (wss://api.sgroup.qq.com/websocket)
so that:
1. Tencent marks the bot as ONLINE (online_state: 1), preventing mobile QQ from getting stuck at "连接中".
2. Automatically captures the user's OpenID from any private (C2C) message, group @ mention, or friend-add event.
3. Automatically saves R20_QQ_OPENID to the encrypted secret store and .env.
4. Auto-reconnects with exponential backoff on network blips.
"""
from __future__ import annotations

import asyncio
import datetime
import fcntl
import json
import os
import signal
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

import websockets

ROOT = Path(__file__).resolve().parents[1]
LOG_FILE = ROOT / "logs" / "qq_gateway.log"
QQ_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
QQ_API_BASE = "https://api.sgroup.qq.com"
RUNNING = True

# 单实例锁：qq_bind.ensure_qq_gateway_daemon_running() 的 ps 检查是无锁
# check-then-act，并发调用（前端轮询/测试/多 worker）会同时 spawn 一批 daemon，
# 每个都持有一条 QQ 官方网关长连接（实测曾积累 111 个孤儿、3.4GB RSS）。
# 这里用 flock 兜底：fd 永不关闭，进程退出（含崩溃）自动释放；抢不到锁的实例立即退出。
LOCK_FILE = ROOT / "data" / ".qq_gateway_daemon.lock"
_LOCK_FD: Optional[int] = None


def acquire_single_instance_lock() -> bool:
    global _LOCK_FD
    fd: Optional[int] = None
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if fd is not None:
            os.close(fd)
        return False
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
    except OSError:
        pass
    _LOCK_FD = fd
    return True


def log(msg: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def stop_handler(*_: Any) -> None:
    global RUNNING
    log("收到终止信号，正在退出 QQ Gateway Worker...")
    RUNNING = False


def _get_credentials() -> tuple[str, str, str]:
    from r20_backend.notifications import _env
    env = _env()
    app_id = env.get("R20_QQ_APP_ID", "").strip()
    secret = env.get("R20_QQ_CLIENT_SECRET", "").strip()
    openid = env.get("R20_QQ_OPENID", "").strip()
    return app_id, secret, openid


def _get_access_token(app_id: str, secret: str) -> tuple[bool, str, str]:
    req = urllib.request.Request(
        QQ_TOKEN_URL,
        data=json.dumps({"appId": app_id, "clientSecret": secret}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "R20-Standalone/6.6.2"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            res = json.loads(response.read().decode("utf-8"))
            token = res.get("access_token", "")
            if token:
                return True, token, ""
            return False, "", f"Token 失败 code={res.get('code')} msg={res.get('message')}"
    except Exception as exc:
        return False, "", f"请求 Token 异常: {exc}"


def _get_ws_url(access_token: str) -> tuple[bool, str]:
    req = urllib.request.Request(
        f"{QQ_API_BASE}/gateway",
        headers={"Authorization": f"QQBot {access_token}", "User-Agent": "R20-Standalone/6.2.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            res = json.loads(response.read().decode("utf-8"))
            ws_url = res.get("url")
            if ws_url:
                return True, ws_url
            return False, "QQ 网关未返回 URL"
    except Exception as exc:
        return False, f"请求网关异常: {exc}"


def _send_ack(access_token: str, openid: str, content: str) -> bool:
    try:
        seq = int(time.time() * 1000) % 1_000_000
        req = urllib.request.Request(
            f"{QQ_API_BASE}/v2/users/{urllib.parse.quote(openid, safe='')}/messages",
            data=json.dumps({
                "content": content,
                "msg_type": 0,
                "msg_seq": seq,
            }).encode("utf-8"),
            headers={
                "Authorization": f"QQBot {access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("id"))
    except Exception as exc:
        log(f"发送确认消息失败: {exc}")
        return False


def _save_openid(app_id: str, openid: str) -> None:
    from r20_gateway.secrets import save_secrets
    from r20_backend.settings_store import update_env
    from r20_backend.audit import record as audit_record

    save_secrets({"R20_QQ_OPENID": openid})
    update_env({"R20_QQ_APP_ID": app_id, "R20_QQ_OPENID": openid})
    audit_record("qq.openid.captured", "success", {"app_id": app_id, "openid": openid})
    log(f"🎉 成功持久化 OpenID: {openid}")


async def _run_session():
    global RUNNING
    backoff = 2

    while RUNNING:
        app_id, secret, existing_openid = _get_credentials()
        if not app_id or not secret:
            log("等待 QQ AppID 和 Client Secret 配置...")
            await asyncio.sleep(5)
            continue

        ok, token, err = _get_access_token(app_id, secret)
        if not ok:
            log(f"获取 Access Token 失败: {err}，{backoff}s 后重试...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue

        ok_ws, ws_url = _get_ws_url(token)
        if not ok_ws:
            log(f"获取 WebSocket 网关失败: {ws_url}，{backoff}s 后重试...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue

        log(f"正在连接 QQ 官方网关: {ws_url} (AppID: {app_id})...")
        try:
            async with websockets.connect(ws_url, close_timeout=5, ping_interval=None) as ws:
                # 1. Hello
                hello_raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                hello = json.loads(hello_raw)
                heartbeat_interval = hello.get("d", {}).get("heartbeat_interval", 40000) / 1000.0
                log(f"网关已连接，心跳间隔: {heartbeat_interval}s")

                # 2. Identify
                intents = (1 << 0) | (1 << 12) | (1 << 25) | (1 << 30)
                auth_payload = {
                    "op": 2,
                    "d": {
                        "token": f"QQBot {token}",
                        "intents": intents,
                        "shard": [0, 1],
                        "properties": {"$os": "linux", "$browser": "r20", "$device": "r20"},
                    },
                }
                await ws.send(json.dumps(auth_payload))

                # 3. Ready
                ready_raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                ready = json.loads(ready_raw)
                bot_user = ready.get("d", {}).get("user", {})
                bot_name = bot_user.get("username", f"机器人{app_id}")
                log(f"✅ QQ 机器人已上线 (ONLINE)：{bot_name} (ID: {bot_user.get('id')})")
                backoff = 2

                last_seq = None
                next_heartbeat = time.time() + heartbeat_interval

                while RUNNING:
                    timeout = max(0.5, min(next_heartbeat - time.time(), 5.0))
                    try:
                        msg_raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                        data = json.loads(msg_raw)
                        op = data.get("op", -1)
                        seq = data.get("s")
                        if seq is not None:
                            last_seq = seq
                        event_type = data.get("t", "")

                        # Handle Dispatch Event
                        if op == 0 and event_type in ("C2C_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE", "FRIEND_ADD", "DIRECT_MESSAGE_CREATE"):
                            d = data.get("d", {})
                            author = d.get("author", {})
                            openid = (
                                author.get("user_openid")
                                or author.get("id")
                                or author.get("member_openid")
                                or d.get("openid")
                            )
                            content = str(d.get("content", "")).strip()
                            log(f"📩 收到用户消息 [事件: {event_type}]: openid={openid} 内容={content[:50]}")

                            if openid:
                                _, _, current_openid = _get_credentials()
                                # Auto-capture if empty or user explicitly requests /bind or 绑定
                                if not current_openid or content in ("/bind", "绑定", "bind", "重置绑定"):
                                    _save_openid(app_id, str(openid).strip())
                                    _send_ack(
                                        token,
                                        str(openid).strip(),
                                        "【R20 Quantum Trader】✅ 机器人已成功绑定您的 OpenID！量化交易、平仓与风险预警通知将实时推送到此会话。",
                                    )
                                elif content in ("ping", "Ping", "测试", "test"):
                                    _send_ack(token, str(openid).strip(), "【R20 Quantum Trader】🏓 Pong! 机器人通信链路正常。")

                    except asyncio.TimeoutError:
                        pass

                    # Heartbeat
                    if time.time() >= next_heartbeat:
                        await ws.send(json.dumps({"op": 1, "d": last_seq}))
                        next_heartbeat = time.time() + heartbeat_interval

        except (websockets.ConnectionClosed, asyncio.TimeoutError, OSError) as exc:
            log(f"WebSocket 断开: {exc}，{backoff}s 后尝试重连...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as exc:
            log(f"网关异常: {exc}，{backoff}s 后尝试重连...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def main():
    if not acquire_single_instance_lock():
        # 已有存活实例持有该 AppID 的网关长连接，静默让位（不打扰主日志）
        print(f"[qq_gateway_daemon] 已有实例在运行（{LOCK_FILE}），本进程退出。", flush=True)
        return
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    log("QQ Gateway Daemon 启动中...")
    asyncio.run(_run_session())
    log("QQ Gateway Daemon 已安全停止。")


if __name__ == "__main__":
    main()

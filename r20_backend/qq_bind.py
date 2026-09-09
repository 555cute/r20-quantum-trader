"""QQ Bot official integration: QR binding and Gateway WebSocket OpenID auto-capture.

Supports:
1. Official q.qq.com /lite QR binding for AppID and Client Secret.
2. Official api.sgroup.qq.com/gateway WebSocket listener for automatic OpenID capture
   from user C2C private messages, Group @ mentions, and Friend Add events.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import datetime
import json
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

QQ_HOST = "q.qq.com"
QQ_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
QQ_API_BASE = "https://api.sgroup.qq.com"
BIND_STATUS = {"NONE": 0, "PENDING": 1, "COMPLETED": 2, "EXPIRED": 3}
TASK_TTL_SECONDS = 300
MAX_ACTIVE_TASKS = 3


class _BindTask:
    def __init__(self, task_id: str, key_b64: str, connect_url: str):
        self.task_id = task_id
        self.key_b64 = key_b64
        self.connect_url = connect_url
        self.created_at = time.time()
        self.status = "pending"  # pending | bound | expired | failed
        self.error = ""
        self.app_id = ""
        self.openid = ""
        self.capture_id = ""
        self.last_poll = 0.0
        self.lock = threading.Lock()


class _OpenidCaptureSession:
    def __init__(self, capture_id: str, app_id: str, client_secret: str, timeout: int = 60):
        self.capture_id = capture_id
        self.app_id = app_id
        self.client_secret = client_secret
        self.timeout = timeout
        self.created_at = time.time()
        self.status = "listening"  # listening | captured | expired | failed
        self.bot_name = ""
        self.bot_id = ""
        self.openid = ""
        self.message_preview = ""
        self.error = ""
        self.lock = threading.Lock()
        self.stop_event = threading.Event()


_TASKS: dict[str, _BindTask] = {}
_TASKS_LOCK = threading.Lock()

_CAPTURE_SESSIONS: dict[str, _OpenidCaptureSession] = {}
_CAPTURE_LOCK = threading.Lock()


def _get_qq_token(app_id: str, client_secret: str) -> tuple[bool, str, str]:
    """Obtain access_token from QQ official bot auth endpoint."""
    req = urllib.request.Request(
        QQ_TOKEN_URL,
        data=json.dumps({"appId": app_id, "clientSecret": client_secret}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "R20-Standalone/6.6.2"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            res = json.loads(response.read().decode("utf-8"))
            token = res.get("access_token", "")
            if token:
                return True, token, ""
            return False, "", f"获取 Token 失败 code={res.get('code')} msg={res.get('message')}"
    except Exception as exc:
        return False, "", f"请求 QQ Token 接口异常: {exc}"


def _get_gateway_ws_url(access_token: str) -> tuple[bool, str]:
    """Retrieve official gateway websocket URL."""
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
            return False, "QQ 网关未返回 WebSocket URL"
    except Exception as exc:
        return False, f"获取 QQ WebSocket 网关异常: {exc}"


def _post_qq(path: str, payload: dict[str, Any], timeout: int = 12) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"https://{QQ_HOST}{path}",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "R20-Standalone/6.2.0"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    data = json.loads(raw) if raw else {}
    if data.get("retcode") != 0:
        raise RuntimeError(str(data.get("msg") or "QQ 绑定接口返回异常"))
    return data.get("data") or {}


def _decrypt_secret(encrypt_secret_b64: str, key_b64: str) -> str:
    """AES-256-GCM with the 32-byte base64 key; layout: iv(12) || ciphertext || tag(16)."""
    try:
        key = base64.b64decode(key_b64)
        blob = base64.b64decode(encrypt_secret_b64)
        if len(key) != 32 or len(blob) < 28:
            raise ValueError("bad key/blob length")
        plain = AESGCM(key).decrypt(blob[:12], blob[12:], None)
        return plain.decode("utf-8")
    except Exception as exc:
        raise RuntimeError(f"QQ 密钥解密失败：{exc}") from exc


def _gc_tasks() -> None:
    now = time.time()
    stale = [tid for tid, task in _TASKS.items() if now - task.created_at > TASK_TTL_SECONDS + 60 or (task.status in {"bound", "failed"} and now - task.created_at > 120)]
    for tid in stale:
        _TASKS.pop(tid, None)

    stale_caps = [cid for cid, s in _CAPTURE_SESSIONS.items() if now - s.created_at > s.timeout + 120]
    for cid in stale_caps:
        _CAPTURE_SESSIONS.pop(cid, None)


_DAEMON_SPAWN_LOCK = threading.Lock()


def _stable_python() -> str:
    """spawn 长驻 daemon 必须用不会被回收的解释器路径。
    uv run 下 sys.executable 指向 /root/.cache/uv/builds-v0/.tmpXXXX/，
    目录被清理后进程变成"可执行文件已消失"的孤儿，无法按路径识别与治理。"""
    exe = sys.executable or "python3"
    resolved = str(Path(exe).resolve()) if Path(exe).exists() else ""
    if resolved and "/.cache/uv/" not in resolved and "/tmp/" not in resolved and ".venv" not in resolved:
        return exe
    for cand in ("/app/venv/bin/python3", "/usr/bin/python3"):
        if Path(cand).exists():
            return cand
    return "python3"


def ensure_qq_gateway_daemon_running() -> None:
    """Ensure the persistent QQ Gateway daemon is active in background.

    并发防护三层：①本进程 threading.Lock 串行化 check-then-act；
    ②ps 预检去重（廉价快路径）；③daemon 自身 flock 单实例锁兜底——
    即使竞态漏网 spawn 出多个，除第一个外全部立即自退。"""
    import subprocess
    root = Path(__file__).resolve().parents[1]
    log_file = root / "logs" / "qq_gateway.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _DAEMON_SPAWN_LOCK:
            output = subprocess.check_output(["ps", "-ef"], text=True)
            if "r20_backend.qq_gateway_daemon" in output:
                return
            with open(log_file, "a", encoding="utf-8") as f:
                subprocess.Popen(
                    [_stable_python(), "-m", "r20_backend.qq_gateway_daemon"],
                    cwd=root,
                    stdin=subprocess.DEVNULL,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                )
    except Exception:
        pass


def create_bind_task(source: str = "R20 Quantum Trader") -> dict[str, Any]:
    ensure_qq_gateway_daemon_running()
    with _TASKS_LOCK:
        _gc_tasks()
        active = [task for task in _TASKS.values() if task.status == "pending" and time.time() - task.created_at < TASK_TTL_SECONDS]
        if len(active) >= MAX_ACTIVE_TASKS:
            raise RuntimeError("已有 3 个进行中的绑定任务，请先完成或等待过期")
        key_b64 = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        data = _post_qq("/lite/create_bind_task", {"key": key_b64})
        task_id = str(data.get("task_id") or "")
        if not task_id:
            raise RuntimeError("QQ 未返回 task_id")
        connect_url = f"https://{QQ_HOST}/qqbot/openclaw/connect.html?task_id={task_id}&source={source}&_wv=2"
        task = _BindTask(task_id, key_b64, connect_url)
        _TASKS[task_id] = task
    return {"task_id": task_id, "connect_url": connect_url, "expires_in": TASK_TTL_SECONDS}


def _public_view(task: _BindTask) -> dict[str, Any]:
    return {
        "status": task.status,
        "error": task.error,
        "app_id": task.app_id if task.status in ("bound", "awaiting_message") else "",
        "openid": task.openid if task.status == "bound" else "",
        "capture_id": task.capture_id,
        "expires_in": max(0, round(TASK_TTL_SECONDS - (time.time() - task.created_at))),
    }


def poll_bind_task(task_id: str) -> dict[str, Any]:
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
    if not task:
        raise RuntimeError("绑定任务不存在或已过期，请重新生成二维码")
    with task.lock:
        if task.status in {"bound", "failed"}:
            return _public_view(task)
        if time.time() - task.created_at > TASK_TTL_SECONDS:
            task.status = "expired"
            return _public_view(task)
        # Rate-limit upstream polling to one request per second per task.
        if time.time() - task.last_poll < 1.0:
            return _public_view(task)
        task.last_poll = time.time()
        try:
            data = _post_qq("/lite/poll_bind_result", {"task_id": task_id})
        except Exception as exc:
            task.error = str(exc)[:200]
            return _public_view(task)
        status = int(data.get("status") or 0)
        if status == BIND_STATUS["COMPLETED"]:
            app_id = str(data.get("bot_appid") or "")
            encrypted = str(data.get("bot_encrypt_secret") or "")
            openid = str(data.get("user_openid") or "")
            if not app_id or not encrypted:
                task.status = "failed"
                task.error = "QQ 返回的绑定结果缺少 AppID 或密钥"
                return _public_view(task)
            try:
                client_secret = _decrypt_secret(encrypted, task.key_b64)
            except RuntimeError as exc:
                task.status = "failed"
                task.error = str(exc)
                return _public_view(task)

            # Persist app_id and client_secret
            try:
                _persist(app_id, client_secret, openid)
                ensure_qq_gateway_daemon_running()
            except Exception as exc:
                task.status = "failed"
                task.error = f"凭证保存失败：{exc}"
                return _public_view(task)

            task.app_id = app_id
            if openid:
                task.openid = openid
                task.status = "bound"
            else:
                # Tencent /lite protocol does not return user_openid. Start automatic gateway capture immediately!
                task.status = "awaiting_message"
                try:
                    cap = start_openid_capture(app_id, client_secret, timeout=90)
                    task.capture_id = cap["capture_id"]
                except Exception as exc:
                    task.status = "bound"  # fallback to manual entry
                    task.error = f"已绑定 AppID，但启动自动捕获失败: {exc}"

        elif status == BIND_STATUS["EXPIRED"]:
            task.status = "expired"
        else:
            task.status = "pending"
        return _public_view(task)


def _persist(app_id: str, client_secret: str, openid: str) -> None:
    from r20_gateway.secrets import save_secrets
    from r20_backend.settings_store import update_env

    values: dict[str, str] = {}
    if client_secret:
        values["R20_QQ_CLIENT_SECRET"] = client_secret
    if openid:
        values["R20_QQ_OPENID"] = openid
    if values:
        save_secrets(values)

    env_update = {"R20_QQ_APP_ID": app_id}
    if openid:
        env_update["R20_QQ_OPENID"] = openid
    update_env(env_update)


# =====================================================================
# Gateway WebSocket OpenID Auto-Capture Engine
# =====================================================================

async def _ws_capture_coroutine(session: _OpenidCaptureSession, access_token: str, ws_url: str):
    import websockets

    try:
        async with websockets.connect(ws_url, close_timeout=5, ping_interval=None) as ws:
            # Step 1: Wait for opcode 10 (Hello)
            hello_raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            hello = json.loads(hello_raw)
            heartbeat_interval = hello.get("d", {}).get("heartbeat_interval", 40000) / 1000.0

            # Step 2: Send opcode 2 (Identify) with all user-interaction intents
            intents = (1 << 0) | (1 << 12) | (1 << 25) | (1 << 30)
            auth_payload = {
                "op": 2,
                "d": {
                    "token": f"QQBot {access_token}",
                    "intents": intents,
                    "shard": [0, 1],
                    "properties": {"$os": "linux", "$browser": "r20", "$device": "r20"},
                },
            }
            await ws.send(json.dumps(auth_payload))

            # Step 3: Wait for opcode 0 READY
            ready_raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            ready = json.loads(ready_raw)
            bot_user = ready.get("d", {}).get("user", {})
            with session.lock:
                session.bot_name = bot_user.get("username", f"机器人{session.app_id}")
                session.bot_id = bot_user.get("id", "")

            # Heartbeat task loop
            last_seq = None

            async def _heartbeat_loop():
                while not session.stop_event.is_set():
                    try:
                        await asyncio.sleep(heartbeat_interval)
                        if session.stop_event.is_set():
                            break
                        await ws.send(json.dumps({"op": 1, "d": last_seq}))
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        break

            hb_task = asyncio.create_task(_heartbeat_loop())

            try:
                # Step 4: Listen for incoming messages from user until timeout or captured
                end_time = session.created_at + session.timeout
                while time.time() < end_time and not session.stop_event.is_set():
                    remaining = max(1.0, end_time - time.time())
                    try:
                        msg_raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        break

                    data = json.loads(msg_raw)
                    op = data.get("op", -1)
                    seq = data.get("s")
                    if seq is not None:
                        last_seq = seq
                    event_type = data.get("t", "")

                    # Check for user message events
                    if op == 0 and event_type in (
                        "C2C_MESSAGE_CREATE",
                        "GROUP_AT_MESSAGE_CREATE",
                        "FRIEND_ADD",
                        "DIRECT_MESSAGE_CREATE",
                    ):
                        d = data.get("d", {})
                        author = d.get("author", {})
                        openid = (
                            author.get("user_openid")
                            or author.get("id")
                            or author.get("member_openid")
                            or d.get("openid")
                        )
                        content = d.get("content", "")

                        if openid:
                            with session.lock:
                                session.openid = str(openid).strip()
                                session.message_preview = str(content)[:100]
                                session.status = "captured"
                                session.stop_event.set()

                            # Persist OpenID immediately
                            try:
                                _persist(session.app_id, session.client_secret, session.openid)
                            except Exception:
                                pass

                            # Send immediate acknowledgement back to the user via QQ C2C
                            try:
                                seq_num = int(time.time() * 1000) % 1_000_000
                                ack_req = urllib.request.Request(
                                    f"{QQ_API_BASE}/v2/users/{urllib.parse.quote(session.openid, safe='')}/messages",
                                    data=json.dumps({
                                        "content": "【R20 Quantum Trader】✅ OpenID 自动捕获并绑定成功！此账号已设为交易通知接收目标。",
                                        "msg_type": 0,
                                        "msg_seq": seq_num,
                                    }).encode("utf-8"),
                                    headers={
                                        "Authorization": f"QQBot {access_token}",
                                        "Content-Type": "application/json",
                                    },
                                    method="POST",
                                )
                                with urllib.request.urlopen(ack_req, timeout=5):
                                    pass
                            except Exception:
                                pass
                            break

            finally:
                hb_task.cancel()

    except Exception as exc:
        with session.lock:
            if session.status == "listening":
                session.status = "failed"
                session.error = f"WebSocket 监听中断: {exc}"
    finally:
        with session.lock:
            if session.status == "listening":
                session.status = "expired"


def _run_capture_thread(session: _OpenidCaptureSession):
    ok, access_token, err = _get_qq_token(session.app_id, session.client_secret)
    if not ok:
        with session.lock:
            session.status = "failed"
            session.error = f"无法获取 QQ Token：{err}"
        return

    ok_gw, ws_url = _get_gateway_ws_url(access_token)
    if not ok_gw:
        with session.lock:
            session.status = "failed"
            session.error = f"获取网关失败：{ws_url}"
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_ws_capture_coroutine(session, access_token, ws_url))
    finally:
        loop.close()


def start_openid_capture(app_id: Optional[str] = None, client_secret: Optional[str] = None, timeout: int = 60) -> dict[str, Any]:
    """Start an on-demand Gateway WebSocket listener to capture the user's OpenID."""
    from r20_backend.notifications import _env
    env = _env()
    effective_app_id = (app_id or env.get("R20_QQ_APP_ID", "")).strip()
    effective_secret = (client_secret or env.get("R20_QQ_CLIENT_SECRET", "")).strip()

    if not effective_app_id or not effective_secret:
        raise ValueError("缺少 QQ App ID 或 Client Secret，请先填写并保存或扫码绑定 Bot")

    with _CAPTURE_LOCK:
        _gc_tasks()
        # Check active session for this app_id
        for s in _CAPTURE_SESSIONS.values():
            if s.app_id == effective_app_id and s.status == "listening":
                expires_in = max(0, round(s.timeout - (time.time() - s.created_at)))
                return {
                    "capture_id": s.capture_id,
                    "app_id": s.app_id,
                    "bot_name": s.bot_name or f"机器人{s.app_id}",
                    "status": "listening",
                    "expires_in": expires_in,
                }

        capture_id = f"cap_{secrets.token_hex(6)}"
        session = _OpenidCaptureSession(capture_id, effective_app_id, effective_secret, timeout=timeout)
        _CAPTURE_SESSIONS[capture_id] = session

    t = threading.Thread(target=_run_capture_thread, args=(session,), daemon=True, name=f"qq_openid_{capture_id}")
    t.start()

    return {
        "capture_id": capture_id,
        "app_id": effective_app_id,
        "bot_name": f"机器人{effective_app_id}",
        "status": "listening",
        "expires_in": timeout,
    }


def poll_openid_capture(capture_id: str) -> dict[str, Any]:
    """Poll the status of an ongoing OpenID capture session."""
    with _CAPTURE_LOCK:
        session = _CAPTURE_SESSIONS.get(capture_id)
    if not session:
        return {"status": "expired", "error": "捕获会话已过期或不存在", "expires_in": 0}

    with session.lock:
        now = time.time()
        expires_in = max(0, round(session.timeout - (now - session.created_at)))
        if session.status == "listening" and expires_in == 0:
            session.status = "expired"

        return {
            "status": session.status,
            "capture_id": session.capture_id,
            "app_id": session.app_id,
            "bot_name": session.bot_name or f"机器人{session.app_id}",
            "openid": session.openid,
            "message_preview": session.message_preview,
            "error": session.error,
            "expires_in": expires_in,
        }

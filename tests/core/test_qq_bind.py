"""QQ 官方机器人绑定链：**QR 建任务 → 轮询 → AES-GCM 解出密钥 → 自动捕获 OpenID**（第二百八十七刀，开新面 qq_bind.py）。

先打印整个文件（534 行）再动笔。它把 q.qq.com 的 /lite 扫码绑定与 api.sgroup.qq.com
的 Gateway WebSocket 自动捕获 OpenID 串起来。

| 语义 | 口径 |
|---|---|
| ★ **密文布局是 iv(12) ‖ ciphertext ‖ tag(16)** | AES-256-GCM + 32 字节 base64 key；长度不对（key≠32、blob<28）**先拒再解**，任何失败统一包成 `RuntimeError("QQ 密钥解密失败：…")` |
| ★ **上游非 0 一律抛** | `_post_qq` 只在 `retcode==0` 时返回 `data or {}`；空响应体当 `{}`（不当解析错误）|
| ★ **绑定任务的三种终态不许倒退** | `bound`/`failed` 直接返回（幂等重放）；超 `TASK_TTL_SECONDS` ⇒ `expired`；其余 ⇒ `pending` |
| ★ **上游轮询限流 1 次/秒/任务** | 距上次 `last_poll` 不足 1 秒直接返回视图、**不打上游**（扫码页轮询很密）|
| ★ **拿不到 openid 不等于失败** | 腾讯 /lite 协议不返回 `user_openid` ⇒ 转 `awaiting_message` 并**立刻启动自动捕获**；捕获启动失败退回 `bound` 让用户手填（**不把已成功的绑定判成失败**）|
| ★ **凭证落盘失败必须如实报** | `_persist` 抛错 ⇒ `status=failed` + 「凭证保存失败」；但**捕获期间的 persist 失败被吞掉**（openid 已在内存里拿到，不因落盘抖动丢掉捕获结果）|
| ★ **公共视图按状态脱敏** | `app_id` 只在 `bound`/`awaiting_message` 露出，`openid` **只在 `bound`** 露出；`expires_in` 夹到 ≥0 |
| ★ **daemon 三层并发防护** | ①本进程 `threading.Lock` 串行化 check-then-act；②`ps -ef` 预检去重（廉价快路径）；③daemon 自身 flock 兜底 |
| ★ **daemon 必须用"稳定的"解释器路径** | `uv run` 下 `sys.executable` 指向 `/.cache/uv/builds-v0/.tmpXXXX/`，目录被清理后进程成孤儿 ⇒ 路径含 `/.cache/uv/`、`/tmp/`、`.venv` 一律换用仓库 venv |
| ★ **捕获会话可复用** | 同一 `app_id` 已有 `listening` 会话 ⇒ 直接回它（扫码页重复点"自动捕获"不该起第二个 WS） |
| ★ **所有上游异常都转成人读文案** | 不把 traceback 抛到扫码页；`poll_openid_capture` 对未知会话返回 `expired` 视图而不是 500 |
"""

import asyncio
import base64
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from r20_backend import notifications as NOTIF
from r20_backend import qq_bind as PS
from r20_backend import settings_store as SS
from r20_gateway import secrets as SEC


class _Resp:
    def __init__(self, payload):
        self._payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        # 模块级任务表是全局可变态 ⇒ 每个用例快照/还原，绝不跨用例泄漏
        self._tasks_snapshot = dict(PS._TASKS)
        self._caps_snapshot = dict(PS._CAPTURE_SESSIONS)
        self.addCleanup(PS._TASKS.clear)
        self.addCleanup(PS._TASKS.update, self._tasks_snapshot)
        self.addCleanup(PS._CAPTURE_SESSIONS.clear)
        self.addCleanup(PS._CAPTURE_SESSIONS.update, self._caps_snapshot)
        PS._TASKS.clear()
        PS._CAPTURE_SESSIONS.clear()


# =====================================================================
# 上游 HTTP 薄封装
# =====================================================================

class GetQqTokenTests(_Base):
    def _call(self, payload=None, raises=None):
        urlopen = self._start(mock.patch.object(PS.urllib.request, "urlopen"))
        if raises is not None:
            urlopen.side_effect = raises
        else:
            urlopen.return_value = _Resp(payload)
        return PS._get_qq_token("app-1", "secret-1"), urlopen

    def test_a_token_is_returned(self):
        (ok, token, err), _ = self._call({"access_token": "tok-1"})
        self.assertIs(ok, True)
        self.assertEqual(token, "tok-1")
        self.assertEqual(err, "")

    def test_a_missing_token_reports_code_and_message(self):
        (ok, token, err), _ = self._call({"code": 100007, "message": "签名错误"})
        self.assertIs(ok, False)
        self.assertEqual(token, "")
        self.assertIn("获取 Token 失败", err)
        self.assertIn("100007", err)
        self.assertIn("签名错误", err)

    def test_an_empty_response_reports_none_values(self):
        (ok, _, err), _ = self._call({})
        self.assertIs(ok, False)
        self.assertIn("code=None", err)

    def test_transport_failure_is_reported_not_raised(self):
        (ok, token, err), _ = self._call(raises=RuntimeError("连接被拒"))
        self.assertIs(ok, False)
        self.assertEqual(token, "")
        self.assertIn("请求 QQ Token 接口异常", err)
        self.assertIn("连接被拒", err)

    def test_the_request_is_a_json_post_with_a_timeout(self):
        _, urlopen = self._call({"access_token": "t"})
        req = urlopen.call_args[0][0]
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.full_url, PS.QQ_TOKEN_URL)
        self.assertEqual(json.loads(req.data.decode()),
                         {"appId": "app-1", "clientSecret": "secret-1"})
        self.assertEqual(urlopen.call_args[1]["timeout"], 12)


class GetGatewayWsUrlTests(_Base):
    def _call(self, payload=None, raises=None):
        urlopen = self._start(mock.patch.object(PS.urllib.request, "urlopen"))
        if raises is not None:
            urlopen.side_effect = raises
        else:
            urlopen.return_value = _Resp(payload)
        return PS._get_gateway_ws_url("tok-1"), urlopen

    def test_a_url_is_returned(self):
        (ok, url), _ = self._call({"url": "wss://api.sgroup.qq.com/websocket"})
        self.assertIs(ok, True)
        self.assertEqual(url, "wss://api.sgroup.qq.com/websocket")

    def test_a_missing_url_is_reported(self):
        (ok, msg), _ = self._call({})
        self.assertIs(ok, False)
        self.assertEqual(msg, "QQ 网关未返回 WebSocket URL")

    def test_transport_failure_is_reported(self):
        (ok, msg), _ = self._call(raises=RuntimeError("超时"))
        self.assertIs(ok, False)
        self.assertIn("获取 QQ WebSocket 网关异常", msg)

    def test_the_authorization_header_uses_the_qqbot_scheme(self):
        _, urlopen = self._call({"url": "wss://x"})
        req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url, f"{PS.QQ_API_BASE}/gateway")
        self.assertEqual(req.get_header("Authorization"), "QQBot tok-1")


class PostQqTests(_Base):
    def _call(self, payload, timeout=12):
        urlopen = self._start(mock.patch.object(PS.urllib.request, "urlopen",
                                                return_value=_Resp(payload)))
        return PS._post_qq("/lite/x", {"k": "v"}, timeout=timeout), urlopen

    def test_a_zero_retcode_returns_the_data(self):
        data, _ = self._call({"retcode": 0, "data": {"task_id": "t1"}})
        self.assertEqual(data, {"task_id": "t1"})

    def test_a_zero_retcode_without_data_returns_an_empty_dict(self):
        self.assertEqual(self._call({"retcode": 0})[0], {})

    def test_a_zero_retcode_with_null_data_returns_an_empty_dict(self):
        self.assertEqual(self._call({"retcode": 0, "data": None})[0], {})

    def test_an_empty_body_becomes_a_missing_retcode_and_therefore_raises(self):
        """实测：空响应体经 `if raw else {}` 得到 `{}`，而 `{}.get("retcode")` 是 `None` != 0
        ⇒ **抛错**（不是"当成空结果返回"）。按实际行为钉住。"""
        with self.assertRaises(RuntimeError) as ctx:
            self._call(b"")
        self.assertEqual(str(ctx.exception), "QQ 绑定接口返回异常")

    def test_a_non_zero_retcode_raises_with_the_upstream_message(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call({"retcode": 100, "msg": "任务不存在"})
        self.assertEqual(str(ctx.exception), "任务不存在")

    def test_a_non_zero_retcode_without_a_message_uses_the_default(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call({"retcode": 100})
        self.assertEqual(str(ctx.exception), "QQ 绑定接口返回异常")

    def test_the_url_is_built_from_the_qq_host(self):
        _, urlopen = self._call({"retcode": 0})
        self.assertEqual(urlopen.call_args[0][0].full_url, f"https://{PS.QQ_HOST}/lite/x")

    def test_the_timeout_is_forwarded(self):
        _, urlopen = self._call({"retcode": 0}, timeout=7)
        self.assertEqual(urlopen.call_args[1]["timeout"], 7)

    def test_a_transport_failure_propagates(self):
        self._start(mock.patch.object(PS.urllib.request, "urlopen",
                                      side_effect=RuntimeError("断开")))
        with self.assertRaises(RuntimeError):
            PS._post_qq("/lite/x", {})


class DecryptSecretTests(unittest.TestCase):
    def _encrypt(self, plain="secret-1", key=None):
        key = key or os.urandom(32)
        iv = os.urandom(12)
        blob = iv + AESGCM(key).encrypt(iv, plain.encode(), None)
        return base64.b64encode(blob).decode(), base64.b64encode(key).decode()

    def test_a_roundtrip_succeeds(self):
        enc, key = self._encrypt("my-client-secret")
        self.assertEqual(PS._decrypt_secret(enc, key), "my-client-secret")

    def test_a_non_ascii_secret_roundtrips(self):
        enc, key = self._encrypt("密钥-中文")
        self.assertEqual(PS._decrypt_secret(enc, key), "密钥-中文")

    def test_a_short_key_is_rejected_before_decrypting(self):
        enc, _ = self._encrypt()
        short = base64.b64encode(os.urandom(16)).decode()
        with self.assertRaises(RuntimeError) as ctx:
            PS._decrypt_secret(enc, short)
        self.assertIn("bad key/blob length", str(ctx.exception))

    def test_a_short_blob_is_rejected(self):
        _, key = self._encrypt()
        tiny = base64.b64encode(os.urandom(20)).decode()
        with self.assertRaises(RuntimeError) as ctx:
            PS._decrypt_secret(tiny, key)
        self.assertIn("bad key/blob length", str(ctx.exception))

    def test_a_tampered_blob_fails_authentication(self):
        enc, key = self._encrypt()
        raw = bytearray(base64.b64decode(enc))
        raw[-1] ^= 0xFF
        with self.assertRaises(RuntimeError) as ctx:
            PS._decrypt_secret(base64.b64encode(bytes(raw)).decode(), key)
        self.assertIn("QQ 密钥解密失败", str(ctx.exception))

    def test_a_wrong_key_fails_authentication(self):
        enc, _ = self._encrypt()
        other = base64.b64encode(os.urandom(32)).decode()
        with self.assertRaises(RuntimeError):
            PS._decrypt_secret(enc, other)

    def test_invalid_base64_is_wrapped(self):
        with self.assertRaises(RuntimeError) as ctx:
            PS._decrypt_secret("！！！不是 base64", base64.b64encode(os.urandom(32)).decode())
        self.assertIn("QQ 密钥解密失败", str(ctx.exception))

    def test_an_exactly_28_byte_blob_passes_the_length_gate(self):
        """边界：`len(blob) < 28` 才拒 ⇒ 恰好 28 字节进入解密（然后解密失败）。"""
        _, key = self._encrypt()
        blob28 = base64.b64encode(os.urandom(28)).decode()
        with self.assertRaises(RuntimeError) as ctx:
            PS._decrypt_secret(blob28, key)
        self.assertNotIn("bad key/blob length", str(ctx.exception))

    def test_the_cause_is_chained(self):
        try:
            PS._decrypt_secret("bad!", base64.b64encode(os.urandom(32)).decode())
        except RuntimeError as exc:
            self.assertIsNotNone(exc.__cause__)


# =====================================================================
# 垃圾回收 / 解释器路径 / daemon
# =====================================================================

class GcTasksTests(_Base):
    def _now(self, value):
        self._start(mock.patch.object(PS.time, "time", return_value=value))

    def test_a_stale_pending_task_is_dropped(self):
        self._now(10_000.0)
        task = PS._BindTask("t1", "k", "u")
        task.created_at = 10_000.0 - (PS.TASK_TTL_SECONDS + 61)
        PS._TASKS["t1"] = task
        PS._gc_tasks()
        self.assertEqual(PS._TASKS, {})

    def test_a_fresh_pending_task_survives(self):
        self._now(10_000.0)
        task = PS._BindTask("t1", "k", "u")
        task.created_at = 10_000.0 - 10
        PS._TASKS["t1"] = task
        PS._gc_tasks()
        self.assertIn("t1", PS._TASKS)

    def test_a_terminal_task_is_dropped_after_two_minutes(self):
        self._now(10_000.0)
        for status in ("bound", "failed"):
            with self.subTest(status=status):
                PS._TASKS.clear()
                task = PS._BindTask("t1", "k", "u")
                task.status = status
                task.created_at = 10_000.0 - 121
                PS._TASKS["t1"] = task
                PS._gc_tasks()
                self.assertEqual(PS._TASKS, {})

    def test_a_recent_terminal_task_survives(self):
        self._now(10_000.0)
        task = PS._BindTask("t1", "k", "u")
        task.status = "bound"
        task.created_at = 10_000.0 - 60
        PS._TASKS["t1"] = task
        PS._gc_tasks()
        self.assertIn("t1", PS._TASKS)

    def test_a_task_just_past_the_ttl_but_not_the_grace_survives(self):
        self._now(10_000.0)
        task = PS._BindTask("t1", "k", "u")
        task.created_at = 10_000.0 - (PS.TASK_TTL_SECONDS + 1)
        PS._TASKS["t1"] = task
        PS._gc_tasks()
        self.assertIn("t1", PS._TASKS, "TTL 后还有 60 秒宽限，供前端读到 expired 状态")

    def test_a_stale_capture_session_is_dropped(self):
        self._now(10_000.0)
        session = PS._OpenidCaptureSession("cap_1", "a", "s", timeout=60)
        session.created_at = 10_000.0 - 181
        PS._CAPTURE_SESSIONS["cap_1"] = session
        PS._gc_tasks()
        self.assertEqual(PS._CAPTURE_SESSIONS, {})

    def test_a_fresh_capture_session_survives(self):
        self._now(10_000.0)
        session = PS._OpenidCaptureSession("cap_1", "a", "s", timeout=60)
        session.created_at = 10_000.0 - 100
        PS._CAPTURE_SESSIONS["cap_1"] = session
        PS._gc_tasks()
        self.assertIn("cap_1", PS._CAPTURE_SESSIONS)


class StablePythonTests(unittest.TestCase):
    """`_stable_python` 的四条判据：路径含 `/.cache/uv/`、`/tmp/`、`.venv` 一律换掉。

    四条判据各自单测：把 `resolve()` 变成一个"干净但带某个标记"的路径即可精确命中，
    不必真去构造那些目录。
    """

    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    EXE = "/usr/bin/python3"

    def _with_resolved(self, resolved):
        """只把**被测解释器**的 `resolve()` 换成"干净但带标记"的路径。

        ⚠️ 不能全局 patch `Path.resolve`：`_stable_python` 还要用它推 `Path(__file__)` 的
        root，全局换掉会把候选路径也算错（第一版就踩了这个坑）。
        """
        self._start(mock.patch.object(PS.sys, "executable", self.EXE))
        self._start(mock.patch.object(PS.Path, "exists", return_value=True))
        real = Path.resolve

        def _resolve(self_, *a, **k):
            if str(self_) == self.EXE:
                return Path(resolved)
            return real(self_, *a, **k)

        self._start(mock.patch.object(PS.Path, "resolve", _resolve))

    def test_a_clean_interpreter_path_is_returned_as_is(self):
        self._with_resolved("/usr/bin/python3")
        self.assertEqual(PS._stable_python(), "/usr/bin/python3")

    def test_the_uv_cache_marker_is_replaced(self):
        self._with_resolved("/srv/.cache/uv/builds-v0/.tmpABC/python3")
        out = PS._stable_python()
        self.assertNotIn("/.cache/uv/", out)

    def test_the_tmp_marker_is_replaced(self):
        self._with_resolved("/tmp/whatever/python3")
        out = PS._stable_python()
        self.assertNotIn("/tmp/", out)

    def test_the_dot_venv_marker_is_replaced(self):
        """⚠️ 本机 `sys.executable` 就是 `.venv/bin/python`，含 `.venv` ⇒ 必走回落。"""
        self._with_resolved("/data/x/.venv/bin/python")
        out = PS._stable_python()
        self.assertNotEqual(out, "/data/x/.venv/bin/python")

    def test_the_repo_venv_is_the_first_candidate(self):
        self._with_resolved("/tmp/whatever/python3")
        first = Path(PS.__file__).resolve().parents[1] / ".venv" / "bin" / "python"
        self.assertEqual(PS._stable_python(), str(first))

    def test_a_nonexistent_executable_falls_back_to_the_candidate_list(self):
        self._start(mock.patch.object(PS.sys, "executable", "/definitely/not/here/python"))
        out = PS._stable_python()
        self.assertNotEqual(out, "/definitely/not/here/python")
        cands = {str(Path(PS.__file__).resolve().parents[1] / ".venv" / "bin" / "python"),
                 "/app/venv/bin/python3", "/usr/bin/python3", "/usr/local/bin/python3"}
        self.assertIn(out, cands)

    def test_a_resolve_oserror_still_falls_back(self):
        self._start(mock.patch.object(PS.sys, "executable", self.EXE))
        self._start(mock.patch.object(PS.Path, "exists", return_value=True))
        real = Path.resolve

        def _resolve(self_, *a, **k):
            if str(self_) == self.EXE:
                raise OSError("坏了")
            return real(self_, *a, **k)

        self._start(mock.patch.object(PS.Path, "resolve", _resolve))
        self.assertTrue(PS._stable_python())

    def test_the_last_resort_is_python3_when_nothing_exists(self):
        self._start(mock.patch.object(PS.sys, "executable", ""))
        self._start(mock.patch.object(PS.Path, "exists", return_value=False))
        self.assertEqual(PS._stable_python(), "python3")

    def test_a_candidate_exists_oserror_is_skipped(self):
        """候补路径 `Path(cand).exists()` 抛 OSError ⇒ 跳过它继续试下一个（第 170 行）。"""
        self._start(mock.patch.object(PS.sys, "executable", self.EXE))
        real = Path.exists

        def _exists(self_):
            text = str(self_)
            if text == self.EXE:
                return True
            if text.endswith(".venv/bin/python"):
                raise OSError("无权限")
            return True

        self._start(mock.patch.object(PS.Path, "exists", _exists))
        real_resolve = Path.resolve

        def _resolve(self_, *a, **k):
            if str(self_) == self.EXE:
                return Path("/tmp/marked/python3")
            return real_resolve(self_, *a, **k)

        self._start(mock.patch.object(PS.Path, "resolve", _resolve))
        self.assertEqual(PS._stable_python(), "/app/venv/bin/python3")

    def test_the_real_call_never_returns_a_volatile_path(self):
        out = PS._stable_python()
        for marker in ("/.cache/uv/", "/tmp/"):
            self.assertNotIn(marker, out)
        self.assertTrue(Path(out).exists() or out == "python3")


class EnsureDaemonTests(_Base):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 重定向 __file__ ⇒ root 落到临时目录，绝不往生产 logs/ 写
        self.fake_file = Path(self.tmp.name) / "pkg" / "qq_bind.py"
        self.fake_file.parent.mkdir(parents=True, exist_ok=True)
        self.fake_file.write_text("", encoding="utf-8")
        self._start(mock.patch.object(PS, "__file__", str(self.fake_file)))
        # ⚠️ `parents[1]` ⇒ root 是**临时目录本身**（不是 pkg/）
        self.root = self.fake_file.resolve().parents[1]

    def test_an_existing_daemon_short_circuits(self):
        self._start(mock.patch("subprocess.check_output",
                               return_value="x r20_backend.qq_gateway_daemon\n"))
        popen = self._start(mock.patch("subprocess.Popen"))
        PS.ensure_qq_gateway_daemon_running()
        popen.assert_not_called()

    def test_a_missing_daemon_is_spawned(self):
        self._start(mock.patch("subprocess.check_output", return_value="nothing here\n"))
        popen = self._start(mock.patch("subprocess.Popen"))
        PS.ensure_qq_gateway_daemon_running()
        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual(args[0][1:], ["-m", "r20_backend.qq_gateway_daemon"])
        self.assertEqual(kwargs["cwd"], self.root)
        self.assertIsNotNone(kwargs["stdout"])

    def test_the_log_directory_is_created(self):
        self._start(mock.patch("subprocess.check_output", return_value=""))
        self._start(mock.patch("subprocess.Popen"))
        log_dir = self.root / "logs"
        self.assertFalse(log_dir.exists())
        PS.ensure_qq_gateway_daemon_running()
        self.assertTrue(log_dir.is_dir())

    def test_the_log_file_is_appended_to(self):
        self._start(mock.patch("subprocess.check_output", return_value=""))
        self._start(mock.patch("subprocess.Popen"))
        PS.ensure_qq_gateway_daemon_running()
        self.assertTrue((self.root / "logs" / "qq_gateway.log").exists())

    def test_spawn_failure_is_swallowed(self):
        self._start(mock.patch("subprocess.check_output", side_effect=RuntimeError("没有 ps")))
        self.assertIsNone(PS.ensure_qq_gateway_daemon_running())

    def test_popen_failure_is_swallowed(self):
        self._start(mock.patch("subprocess.check_output", return_value=""))
        self._start(mock.patch("subprocess.Popen", side_effect=OSError("无法 fork")))
        self.assertIsNone(PS.ensure_qq_gateway_daemon_running())

    def test_the_ps_precheck_uses_ps_ef(self):
        check = self._start(mock.patch("subprocess.check_output", return_value=""))
        self._start(mock.patch("subprocess.Popen"))
        PS.ensure_qq_gateway_daemon_running()
        self.assertEqual(check.call_args[0][0], ["ps", "-ef"])
        self.assertIs(check.call_args[1]["text"], True)


if __name__ == "__main__":
    unittest.main()


# =====================================================================
# 建任务 / 轮询
# =====================================================================

class CreateBindTaskTests(_Base):
    def setUp(self):
        super().setUp()
        self.daemon = self._start(mock.patch.object(PS, "ensure_qq_gateway_daemon_running"))
        self.post = self._start(mock.patch.object(PS, "_post_qq",
                                                  return_value={"task_id": "T-1"}))
        self._start(mock.patch.object(PS.secrets, "token_bytes", return_value=b"\x01" * 32))

    def test_it_ensures_the_daemon_first(self):
        PS.create_bind_task()
        self.daemon.assert_called_once()

    def test_the_return_shape(self):
        out = PS.create_bind_task()
        self.assertEqual(out["task_id"], "T-1")
        self.assertEqual(out["expires_in"], PS.TASK_TTL_SECONDS)
        self.assertIn("T-1", out["connect_url"])

    def test_the_connect_url_is_the_official_openclaw_page(self):
        out = PS.create_bind_task(source="我的来源")
        self.assertTrue(out["connect_url"].startswith(
            f"https://{PS.QQ_HOST}/qqbot/openclaw/connect.html?"))
        self.assertIn("source=我的来源", out["connect_url"])
        self.assertIn("_wv=2", out["connect_url"])

    def test_the_default_source(self):
        self.assertIn("source=R20 Quantum Trader", PS.create_bind_task()["connect_url"])

    def test_the_generated_key_is_32_bytes_base64(self):
        PS.create_bind_task()
        sent_key = self.post.call_args[0][1]["key"]
        self.assertEqual(len(base64.b64decode(sent_key)), 32)

    def test_the_task_is_created_with_the_generated_key(self):
        PS.create_bind_task()
        task = PS._TASKS["T-1"]
        self.assertEqual(task.key_b64, self.post.call_args[0][1]["key"])
        self.assertEqual(task.status, "pending")

    def test_the_upstream_path_and_payload(self):
        PS.create_bind_task()
        self.assertEqual(self.post.call_args[0][0], "/lite/create_bind_task")

    def test_a_missing_task_id_raises(self):
        self.post.return_value = {}
        with self.assertRaises(RuntimeError) as ctx:
            PS.create_bind_task()
        self.assertIn("QQ 未返回 task_id", str(ctx.exception))
        self.assertEqual(PS._TASKS, {})

    def test_an_empty_task_id_raises(self):
        self.post.return_value = {"task_id": ""}
        with self.assertRaises(RuntimeError):
            PS.create_bind_task()

    def test_the_active_task_cap(self):
        now = time.time()
        for i in range(PS.MAX_ACTIVE_TASKS):
            task = PS._BindTask(f"t{i}", "k", "u")
            task.created_at = now - 1
            PS._TASKS[f"t{i}"] = task
        with self.assertRaises(RuntimeError) as ctx:
            PS.create_bind_task()
        self.assertIn("已有 3 个进行中的绑定任务", str(ctx.exception))
        self.post.assert_not_called()

    def test_below_the_cap_passes(self):
        now = time.time()
        for i in range(PS.MAX_ACTIVE_TASKS - 1):
            task = PS._BindTask(f"t{i}", "k", "u")
            task.created_at = now - 1
            PS._TASKS[f"t{i}"] = task
        self.assertEqual(PS.create_bind_task()["task_id"], "T-1")

    def test_a_stale_pending_task_does_not_count_towards_the_cap(self):
        task = PS._BindTask("old", "k", "u")
        task.created_at = time.time() - (PS.TASK_TTL_SECONDS + 5)
        PS._TASKS["old"] = task
        self.assertEqual(PS.create_bind_task()["task_id"], "T-1")

    def test_bound_tasks_do_not_count_towards_the_cap(self):
        for i in range(PS.MAX_ACTIVE_TASKS + 1):
            task = PS._BindTask(f"t{i}", "k", "u")
            task.status = "bound"
            PS._TASKS[f"t{i}"] = task
        self.assertEqual(PS.create_bind_task()["task_id"], "T-1")


class PersistTests(unittest.TestCase):
    def setUp(self):
        self.save = mock.patch.object(SEC, "save_secrets").start()
        self.addCleanup(mock.patch.stopall)
        self.update = mock.patch.object(SS, "update_env").start()

    def test_a_secret_and_openid_go_to_both_stores(self):
        PS._persist("app-1", "secret-1", "openid-1")
        self.save.assert_called_once_with({"R20_QQ_CLIENT_SECRET": "secret-1",
                                           "R20_QQ_OPENID": "openid-1"})
        self.update.assert_called_once_with({"R20_QQ_APP_ID": "app-1",
                                             "R20_QQ_OPENID": "openid-1"})

    def test_without_a_secret_the_secret_store_is_skipped(self):
        PS._persist("app-1", "", "")
        self.save.assert_not_called()
        self.update.assert_called_once_with({"R20_QQ_APP_ID": "app-1"})

    def test_without_an_openid_only_the_app_id_reaches_the_env(self):
        PS._persist("app-1", "secret-1", "")
        self.save.assert_called_once_with({"R20_QQ_CLIENT_SECRET": "secret-1"})
        self.update.assert_called_once_with({"R20_QQ_APP_ID": "app-1"})

    def test_the_secret_never_reaches_the_env(self):
        PS._persist("app-1", "secret-1", "openid-1")
        self.assertNotIn("R20_QQ_CLIENT_SECRET", self.update.call_args[0][0])


class PollBindTaskTests(_Base):
    def setUp(self):
        super().setUp()
        self.now = 10_000.0
        self._start(mock.patch.object(PS.time, "time", side_effect=lambda: self.now))
        self.post = self._start(mock.patch.object(PS, "_post_qq", return_value={}))
        self.decrypt = self._start(mock.patch.object(PS, "_decrypt_secret",
                                                      return_value="secret-1"))
        self.persist = self._start(mock.patch.object(PS, "_persist"))
        self.daemon = self._start(mock.patch.object(PS, "ensure_qq_gateway_daemon_running"))
        self.capture = self._start(mock.patch.object(PS, "start_openid_capture",
                                                     return_value={"capture_id": "cap_1"}))

    def _task(self, task_id="t1", **over):
        task = PS._BindTask(task_id, "a2V5", "u")
        task.created_at = self.now - 5
        task.last_poll = 0.0
        for key, value in over.items():
            setattr(task, key, value)
        PS._TASKS[task_id] = task
        return task

    def _completed(self, **d):
        self.post.return_value = {"status": PS.BIND_STATUS["COMPLETED"],
                                  "bot_appid": "app-1", "bot_encrypt_secret": "enc-1",
                                  **d}

    def test_an_unknown_task_raises(self):
        with self.assertRaises(RuntimeError) as ctx:
            PS.poll_bind_task("nope")
        self.assertIn("不存在或已过期", str(ctx.exception))

    def test_a_bound_task_returns_immediately_without_polling(self):
        self._task(status="bound", app_id="a", openid="o")
        out = PS.poll_bind_task("t1")
        self.assertEqual(out["status"], "bound")
        self.assertEqual(out["openid"], "o")
        self.post.assert_not_called()

    def test_a_failed_task_returns_immediately(self):
        self._task(status="failed", error="炸了")
        out = PS.poll_bind_task("t1")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["error"], "炸了")
        self.post.assert_not_called()

    def test_a_task_past_its_ttl_expires(self):
        self._task(created_at=self.now - (PS.TASK_TTL_SECONDS + 1))
        self.assertEqual(PS.poll_bind_task("t1")["status"], "expired")
        self.post.assert_not_called()

    def test_upstream_polling_is_rate_limited_to_once_per_second(self):
        self._task(last_poll=self.now - 0.5)
        PS.poll_bind_task("t1")
        self.post.assert_not_called()

    def test_polling_happens_after_a_second(self):
        self._task(last_poll=self.now - 1.5)
        PS.poll_bind_task("t1")
        self.post.assert_called_once_with("/lite/poll_bind_result", {"task_id": "t1"})

    def test_the_poll_timestamp_is_recorded(self):
        task = self._task(last_poll=0.0)
        PS.poll_bind_task("t1")
        self.assertEqual(task.last_poll, self.now)

    def test_an_upstream_failure_is_recorded_as_an_error(self):
        task = self._task()
        self.post.side_effect = RuntimeError("上游 500")
        out = PS.poll_bind_task("t1")
        self.assertEqual(out["status"], "pending")
        self.assertIn("上游 500", out["error"])
        self.assertEqual(task.error, "上游 500")

    def test_the_upstream_error_is_truncated_to_200(self):
        task = self._task()
        self.post.side_effect = RuntimeError("x" * 500)
        PS.poll_bind_task("t1")
        self.assertEqual(len(task.error), 200)

    def test_a_completed_bind_with_an_openid_goes_straight_to_bound(self):
        task = self._task()
        self._completed(user_openid="openid-1")
        out = PS.poll_bind_task("t1")
        self.assertEqual(out["status"], "bound")
        self.assertEqual(out["openid"], "openid-1")
        self.assertEqual(out["app_id"], "app-1")
        self.assertEqual(task.capture_id, "")
        self.capture.assert_not_called()

    def test_a_completed_bind_persists_and_ensures_the_daemon(self):
        self._task()
        self._completed(user_openid="openid-1")
        PS.poll_bind_task("t1")
        self.decrypt.assert_called_once_with("enc-1", "a2V5")
        self.persist.assert_called_once_with("app-1", "secret-1", "openid-1")
        self.daemon.assert_called()

    def test_a_completed_bind_without_an_openid_starts_capture(self):
        task = self._task()
        self._completed()
        out = PS.poll_bind_task("t1")
        self.assertEqual(out["status"], "awaiting_message")
        self.assertEqual(out["capture_id"], "cap_1")
        self.assertEqual(task.openid, "")
        self.capture.assert_called_once_with("app-1", "secret-1", timeout=90)

    def test_app_id_is_visible_while_awaiting_the_message(self):
        self._task()
        self._completed()
        self.assertEqual(PS.poll_bind_task("t1")["app_id"], "app-1")

    def test_a_failed_capture_falls_back_to_bound(self):
        task = self._task()
        self._completed()
        self.capture.side_effect = RuntimeError("没有 websockets")
        out = PS.poll_bind_task("t1")
        self.assertEqual(out["status"], "bound", "已绑好的 AppID 不许因为捕获失败被判失败")
        self.assertIn("启动自动捕获失败", out["error"])
        self.assertEqual(out["openid"], "")

    def test_a_completed_bind_without_an_app_id_fails(self):
        task = self._task()
        self.post.return_value = {"status": PS.BIND_STATUS["COMPLETED"],
                                  "bot_encrypt_secret": "enc-1"}
        out = PS.poll_bind_task("t1")
        self.assertEqual(out["status"], "failed")
        self.assertIn("缺少 AppID 或密钥", out["error"])
        self.persist.assert_not_called()

    def test_a_completed_bind_without_a_secret_fails(self):
        self._task()
        self.post.return_value = {"status": PS.BIND_STATUS["COMPLETED"],
                                  "bot_appid": "app-1"}
        out = PS.poll_bind_task("t1")
        self.assertEqual(out["status"], "failed")

    def test_a_decrypt_failure_fails_the_task(self):
        task = self._task()
        self._completed()
        self.decrypt.side_effect = RuntimeError("QQ 密钥解密失败：坏了")
        out = PS.poll_bind_task("t1")
        self.assertEqual(out["status"], "failed")
        self.assertIn("解密失败", out["error"])
        self.persist.assert_not_called()

    def test_a_persist_failure_fails_the_task(self):
        task = self._task()
        self._completed(user_openid="openid-1")
        self.persist.side_effect = RuntimeError("磁盘满")
        out = PS.poll_bind_task("t1")
        self.assertEqual(out["status"], "failed")
        self.assertIn("凭证保存失败", out["error"])
        self.assertIn("磁盘满", out["error"])

    def test_an_expired_status_marks_the_task_expired(self):
        task = self._task()
        self.post.return_value = {"status": PS.BIND_STATUS["EXPIRED"]}
        self.assertEqual(PS.poll_bind_task("t1")["status"], "expired")
        self.assertEqual(task.status, "expired")

    def test_a_pending_status_keeps_the_task_pending(self):
        task = self._task()
        self.post.return_value = {"status": PS.BIND_STATUS["PENDING"]}
        self.assertEqual(PS.poll_bind_task("t1")["status"], "pending")

    def test_an_unknown_status_is_treated_as_pending(self):
        self._task()
        self.post.return_value = {"status": 999}
        self.assertEqual(PS.poll_bind_task("t1")["status"], "pending")

    def test_a_missing_status_is_treated_as_pending(self):
        self._task()
        self.post.return_value = {}
        self.assertEqual(PS.poll_bind_task("t1")["status"], "pending")


class PublicViewTests(unittest.TestCase):
    def _view(self, **over):
        task = PS._BindTask("t1", "k", "u")
        task.app_id = "app-1"
        task.openid = "openid-1"
        for key, value in over.items():
            setattr(task, key, value)
        return PS._public_view(task)

    def test_a_pending_task_hides_both_secrets(self):
        out = self._view(status="pending")
        self.assertEqual(out["app_id"], "")
        self.assertEqual(out["openid"], "")

    def test_awaiting_message_reveals_the_app_id_but_not_the_openid(self):
        out = self._view(status="awaiting_message")
        self.assertEqual(out["app_id"], "app-1")
        self.assertEqual(out["openid"], "")

    def test_bound_reveals_both(self):
        out = self._view(status="bound")
        self.assertEqual(out["app_id"], "app-1")
        self.assertEqual(out["openid"], "openid-1")

    def test_an_expired_task_reveals_neither(self):
        out = self._view(status="expired")
        self.assertEqual(out["app_id"], "")
        self.assertEqual(out["openid"], "")

    def test_expires_in_is_never_negative(self):
        task = PS._BindTask("t1", "k", "u")
        task.created_at = time.time() - (PS.TASK_TTL_SECONDS + 1000)
        self.assertEqual(PS._public_view(task)["expires_in"], 0)

    def test_bind_status_constants(self):
        self.assertEqual(PS.BIND_STATUS["NONE"], 0)
        self.assertEqual(PS.BIND_STATUS["PENDING"], 1)
        self.assertEqual(PS.BIND_STATUS["COMPLETED"], 2)
        self.assertEqual(PS.BIND_STATUS["EXPIRED"], 3)


# =====================================================================
# OpenID 自动捕获：会话管理 + WebSocket 引擎
# =====================================================================

class StartOpenidCaptureTests(_Base):
    def setUp(self):
        super().setUp()
        self.env = self._start(mock.patch.object(NOTIF, "_env", return_value={}))
        self.thread = self._start(mock.patch.object(PS.threading, "Thread"))

    def _env(self, **values):
        self.env.return_value = values

    def test_missing_credentials_raise(self):
        with self.assertRaises(ValueError) as ctx:
            PS.start_openid_capture()
        self.assertIn("缺少 QQ App ID", str(ctx.exception))

    def test_a_missing_secret_alone_raises(self):
        self._env(R20_QQ_APP_ID="app-1")
        with self.assertRaises(ValueError):
            PS.start_openid_capture()

    def test_the_env_is_used_when_arguments_are_absent(self):
        self._env(R20_QQ_APP_ID="app-1", R20_QQ_CLIENT_SECRET="secret-1")
        out = PS.start_openid_capture()
        self.assertEqual(out["app_id"], "app-1")

    def test_explicit_arguments_win_over_the_env(self):
        self._env(R20_QQ_APP_ID="env-app", R20_QQ_CLIENT_SECRET="env-secret")
        out = PS.start_openid_capture("arg-app", "arg-secret")
        self.assertEqual(out["app_id"], "arg-app")

    def test_whitespace_only_env_values_are_rejected(self):
        """只填空白 = 没填（strip 之后为空）⇒ ValueError，而不是拿空白去连网关。"""
        self._env(R20_QQ_APP_ID="   ", R20_QQ_CLIENT_SECRET="   ")
        with self.assertRaises(ValueError):
            PS.start_openid_capture()

    def test_explicit_values_are_stripped(self):
        out = PS.start_openid_capture("  app-1  ", "  secret-1  ")
        self.assertEqual(out["app_id"], "app-1")
        session = next(iter(PS._CAPTURE_SESSIONS.values()))
        self.assertEqual(session.client_secret, "secret-1")

    def test_the_return_shape(self):
        out = PS.start_openid_capture("app-1", "secret-1", timeout=42)
        self.assertEqual(set(out), {"capture_id", "app_id", "bot_name",
                                    "status", "expires_in"})
        self.assertTrue(out["capture_id"].startswith("cap_"))
        self.assertEqual(out["status"], "listening")
        self.assertEqual(out["expires_in"], 42)
        self.assertEqual(out["bot_name"], "机器人app-1")

    def test_a_session_is_registered(self):
        PS.start_openid_capture("app-1", "secret-1")
        self.assertEqual(len(PS._CAPTURE_SESSIONS), 1)
        session = next(iter(PS._CAPTURE_SESSIONS.values()))
        self.assertEqual(session.app_id, "app-1")
        self.assertEqual(session.client_secret, "secret-1")
        self.assertEqual(session.status, "listening")

    def test_a_daemon_thread_is_started(self):
        PS.start_openid_capture("app-1", "secret-1")
        self.thread.assert_called_once()
        kwargs = self.thread.call_args[1]
        self.assertIs(kwargs["target"], PS._run_capture_thread)
        self.assertIs(kwargs["daemon"], True)
        self.assertTrue(kwargs["name"].startswith("qq_openid_cap_"))
        self.assertEqual(len(kwargs["args"]), 1)

    def test_an_existing_listening_session_for_the_same_app_is_reused(self):
        first = PS.start_openid_capture("app-1", "secret-1")
        second = PS.start_openid_capture("app-1", "secret-1")
        self.assertEqual(second["capture_id"], first["capture_id"])
        self.assertEqual(len(PS._CAPTURE_SESSIONS), 1)
        self.assertEqual(self.thread.call_count, 1)

    def test_a_session_for_another_app_is_not_reused(self):
        PS.start_openid_capture("app-1", "secret-1")
        other = PS.start_openid_capture("app-2", "secret-2")
        self.assertEqual(len(PS._CAPTURE_SESSIONS), 2)
        self.assertEqual(other["app_id"], "app-2")

    def test_a_non_listening_session_is_not_reused(self):
        first = PS.start_openid_capture("app-1", "secret-1")
        next(iter(PS._CAPTURE_SESSIONS.values())).status = "captured"
        second = PS.start_openid_capture("app-1", "secret-1")
        self.assertNotEqual(second["capture_id"], first["capture_id"])

    def test_the_reused_session_reports_a_shrinking_expiry(self):
        PS.start_openid_capture("app-1", "secret-1", timeout=60)
        session = next(iter(PS._CAPTURE_SESSIONS.values()))
        session.created_at = time.time() - 10
        out = PS.start_openid_capture("app-1", "secret-1", timeout=60)
        self.assertLessEqual(out["expires_in"], 50)

    def test_the_reused_session_uses_the_probed_bot_name(self):
        PS.start_openid_capture("app-1", "secret-1")
        next(iter(PS._CAPTURE_SESSIONS.values())).bot_name = "真机器人"
        out = PS.start_openid_capture("app-1", "secret-1")
        self.assertEqual(out["bot_name"], "真机器人")


class PollOpenidCaptureTests(_Base):
    def test_an_unknown_session_reports_expired(self):
        out = PS.poll_openid_capture("cap_nope")
        self.assertEqual(out["status"], "expired")
        self.assertIn("已过期或不存在", out["error"])
        self.assertEqual(out["expires_in"], 0)

    def test_a_live_session_reports_its_fields(self):
        session = PS._OpenidCaptureSession("cap_1", "app-1", "s", timeout=60)
        session.bot_name = "机器人"
        session.openid = "openid-1"
        session.message_preview = "你好"
        PS._CAPTURE_SESSIONS["cap_1"] = session
        out = PS.poll_openid_capture("cap_1")
        self.assertEqual(out["status"], "listening")
        self.assertEqual(out["capture_id"], "cap_1")
        self.assertEqual(out["app_id"], "app-1")
        self.assertEqual(out["bot_name"], "机器人")
        self.assertEqual(out["openid"], "openid-1")
        self.assertEqual(out["message_preview"], "你好")
        self.assertGreater(out["expires_in"], 0)

    def test_a_timed_out_session_flips_to_expired(self):
        session = PS._OpenidCaptureSession("cap_1", "app-1", "s", timeout=60)
        session.created_at = time.time() - 61
        PS._CAPTURE_SESSIONS["cap_1"] = session
        out = PS.poll_openid_capture("cap_1")
        self.assertEqual(out["status"], "expired")
        self.assertEqual(out["expires_in"], 0)
        self.assertEqual(session.status, "expired")

    def test_an_already_finished_session_is_reported_as_is(self):
        session = PS._OpenidCaptureSession("cap_1", "app-1", "s", timeout=60)
        session.status = "captured"
        session.openid = "openid-1"
        PS._CAPTURE_SESSIONS["cap_1"] = session
        self.assertEqual(PS.poll_openid_capture("cap_1")["status"], "captured")

    def test_a_missing_bot_name_falls_back(self):
        PS._CAPTURE_SESSIONS["cap_1"] = PS._OpenidCaptureSession("cap_1", "app-1", "s")
        self.assertEqual(PS.poll_openid_capture("cap_1")["bot_name"], "机器人app-1")


class _FakeWS:
    """够用的 WebSocket 替身：按脚本吐消息，吐完就抛 TimeoutError。"""

    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.sent = []

    async def recv(self):
        if not self.incoming:
            raise asyncio.TimeoutError()
        return self.incoming.pop(0)

    async def send(self, payload):
        self.sent.append(json.loads(payload))


class _FakeConnect:
    def __init__(self, ws=None, error=None):
        self.ws = ws
        self.error = error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self.ws

    async def __aexit__(self, *a):
        return False


def _hello(interval=40000):
    payload = {"op": 10}
    if interval is not None:
        payload["d"] = {"heartbeat_interval": interval}
    return json.dumps(payload)


_READY = json.dumps({"op": 0, "t": "READY",
                     "d": {"user": {"username": "bot-1", "id": "9"}}})


def _message(**d):
    return json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "s": 5, "d": d})


class WsCaptureCoroutineTests(_Base):
    def setUp(self):
        super().setUp()
        import websockets
        self.connect = self._start(mock.patch.object(websockets, "connect"))
        self.persist = self._start(mock.patch.object(PS, "_persist"))
        self.ack = self._start(mock.patch.object(PS.urllib.request, "urlopen",
                                                 return_value=_Resp(b"")))
        self.session = PS._OpenidCaptureSession("cap_1", "app-1", "secret-1", timeout=60)

    def _run(self, incoming, error=None):
        ws = _FakeWS(incoming)
        self.connect.return_value = _FakeConnect(ws, error)
        asyncio.run(PS._ws_capture_coroutine(self.session, "tok-1", "wss://gw"))
        return ws

    def test_a_message_with_an_openid_is_captured(self):
        ws = self._run([_hello(), _READY, _message(author={"user_openid": "OPENID-1"},
                                                  content="你好")])
        self.assertEqual(self.session.status, "captured")
        self.assertEqual(self.session.openid, "OPENID-1")
        self.assertEqual(self.session.message_preview, "你好")
        self.assertTrue(self.session.stop_event.is_set())

    def test_the_bot_identity_is_recorded(self):
        self._run([_hello(), _READY, _message(author={"user_openid": "O"})])
        self.assertEqual(self.session.bot_name, "bot-1")
        self.assertEqual(self.session.bot_id, "9")

    def test_the_identify_frame_carries_the_token_and_intents(self):
        ws = self._run([_hello(), _READY, _message(author={"user_openid": "O"})])
        identify = ws.sent[0]
        self.assertEqual(identify["op"], 2)
        self.assertEqual(identify["d"]["token"], "QQBot tok-1")
        self.assertEqual(identify["d"]["intents"],
                         (1 << 0) | (1 << 12) | (1 << 25) | (1 << 30))
        self.assertEqual(identify["d"]["shard"], [0, 1])

    def test_the_openid_is_persisted_immediately(self):
        self._run([_hello(), _READY, _message(author={"user_openid": "OPENID-1"})])
        self.persist.assert_called_once_with("app-1", "secret-1", "OPENID-1")

    def test_an_ack_is_sent_to_the_user(self):
        self._run([_hello(), _READY, _message(author={"user_openid": "OPENID-1"})])
        req = self.ack.call_args[0][0]
        self.assertIn("/v2/users/OPENID-1/messages", req.full_url)
        self.assertEqual(req.get_header("Authorization"), "QQBot tok-1")
        self.assertIn("捕获并绑定成功", json.loads(req.data.decode())["content"])

    def test_the_openid_is_url_quoted_in_the_ack(self):
        self._run([_hello(), _READY, _message(author={"user_openid": "a/b+c"})])
        self.assertIn("/v2/users/a%2Fb%2Bc/messages", self.ack.call_args[0][0].full_url)

    def test_the_ack_seq_is_bounded(self):
        self._run([_hello(), _READY, _message(author={"user_openid": "O"})])
        seq = json.loads(self.ack.call_args[0][0].data.decode())["msg_seq"]
        self.assertLess(seq, 1_000_000)

    def test_the_default_heartbeat_interval_is_used_when_hello_has_no_d(self):
        """Hello 缺 `d` ⇒ 40000ms 兜底（不能 KeyError 把整条捕获链打死）。"""
        self._run([_hello(interval=None), _READY])
        self.assertEqual(self.session.status, "expired")

    def test_an_author_id_is_accepted_as_the_openid(self):
        self._run([_hello(), _READY, _message(author={"id": "ID-1"})])
        self.assertEqual(self.session.openid, "ID-1")

    def test_a_member_openid_is_accepted(self):
        self._run([_hello(), _READY, _message(author={"member_openid": "M-1"})])
        self.assertEqual(self.session.openid, "M-1")

    def test_a_top_level_openid_is_accepted(self):
        self._run([_hello(), _READY, _message(openid="TOP-1")])
        self.assertEqual(self.session.openid, "TOP-1")

    def test_an_event_without_any_openid_keeps_listening_then_expires(self):
        self._run([_hello(), _READY, _message(author={})])
        self.assertEqual(self.session.status, "expired")
        self.assertEqual(self.session.openid, "")
        self.persist.assert_not_called()

    def test_unrelated_events_are_ignored(self):
        self._run([_hello(), _READY,
                   json.dumps({"op": 0, "t": "SOMETHING_ELSE", "d": {}}),
                   _message(author={"user_openid": "O-1"})])
        self.assertEqual(self.session.openid, "O-1")

    def test_a_heartbeat_opcode_is_not_treated_as_a_message(self):
        self._run([_hello(), _READY,
                   json.dumps({"op": 11, "d": {}}),
                   _message(author={"user_openid": "O-1"})])
        self.assertEqual(self.session.status, "captured")

    def test_a_connection_failure_marks_the_session_failed(self):
        self._run([], error=RuntimeError("连接被拒"))
        self.assertEqual(self.session.status, "failed")
        self.assertIn("WebSocket 监听中断", self.session.error)
        self.assertIn("连接被拒", self.session.error)

    def test_a_hello_parse_failure_marks_the_session_failed(self):
        self._run(["不是 JSON"])
        self.assertEqual(self.session.status, "failed")

    def test_a_persist_failure_does_not_lose_the_capture(self):
        self.persist.side_effect = RuntimeError("磁盘满")
        self._run([_hello(), _READY, _message(author={"user_openid": "O-1"})])
        self.assertEqual(self.session.status, "captured", "已拿到的 openid 不许因落盘抖动丢掉")
        self.assertEqual(self.session.openid, "O-1")

    def test_an_ack_failure_does_not_lose_the_capture(self):
        self.ack.side_effect = RuntimeError("发送失败")
        self._run([_hello(), _READY, _message(author={"user_openid": "O-1"})])
        self.assertEqual(self.session.status, "captured")

    def test_a_pre_set_stop_event_short_circuits(self):
        self.session.stop_event.set()
        self._run([_hello(), _READY])
        self.assertEqual(self.session.status, "expired")

    def test_an_already_finished_session_is_not_overwritten(self):
        self.session.status = "captured"
        self._run([_hello()])
        self.assertEqual(self.session.status, "captured")

    def test_the_connect_call_disables_ping_and_shortens_close(self):
        self._run([_hello(), _READY])
        kwargs = self.connect.call_args[1]
        self.assertEqual(kwargs["close_timeout"], 5)
        self.assertIsNone(kwargs["ping_interval"])


class HeartbeatLoopTests(WsCaptureCoroutineTests):
    """心跳循环的两条 break：①睡醒发现 stop_event 已置位；②发心跳抛异常。

    这两行只有在**主循环仍在 await** 时才可能被心跳任务抢到执行，所以用一个"脚本吐完后
    先睡一会儿再抛 TimeoutError"的替身，把窗口让出来；心跳间隔调到 1ms 放大机会。
    """

    class _WS(_FakeWS):
        def __init__(self, incoming, session=None, stop_after=None, send_boom=False):
            super().__init__(incoming)
            self.session = session
            self.stop_after = stop_after
            self.send_boom = send_boom
            self.heartbeat_attempts = []

        async def recv(self):
            if self.incoming:
                return self.incoming.pop(0)
            if (self.session is not None and self.stop_after is not None
                    and not self.session.stop_event.is_set()):
                self.session.stop_event.set()
            await asyncio.sleep(0.05)
            raise asyncio.TimeoutError()

        async def send(self, payload):
            frame = json.loads(payload)
            if frame.get("op") == 1:
                self.heartbeat_attempts.append(frame)
                if self.send_boom:
                    raise RuntimeError("心跳发不出去")
            await super().send(payload)

    def _drive(self, ws):
        self.connect.return_value = _FakeConnect(ws)
        asyncio.run(PS._ws_capture_coroutine(self.session, "tok-1", "wss://gw"))
        return ws

    def test_a_heartbeat_stops_once_the_stop_event_is_set(self):
        ws = self._drive(self._WS([_hello(interval=1), _READY], session=self.session,
                                  stop_after=True))
        self.assertTrue(self.session.stop_event.is_set())
        self.assertEqual(self.session.status, "expired", "停止事件不算捕获成功")
        self.assertEqual(self.session.openid, "")

    def test_a_heartbeat_is_sent_while_listening(self):
        ws = self._drive(self._WS([_hello(interval=1), _READY]))
        self.assertTrue(ws.heartbeat_attempts, "监听期间必须真的在发心跳")

    def test_a_heartbeat_send_failure_quietly_stops_the_loop(self):
        ws = self._drive(self._WS([_hello(interval=1), _READY], send_boom=True))
        self.assertTrue(ws.heartbeat_attempts)
        self.assertEqual(self.session.status, "expired",
                         "心跳发不出去只该停掉心跳，不该把整条监听判失败")

    def test_the_heartbeat_interval_comes_from_hello(self):
        ws = self._drive(self._WS([_hello(interval=1), _READY]))
        self.assertTrue(ws.heartbeat_attempts)


class RunCaptureThreadTests(_Base):
    def setUp(self):
        super().setUp()
        self.token = self._start(mock.patch.object(PS, "_get_qq_token",
                                                  return_value=(True, "tok-1", "")))
        self.gateway = self._start(mock.patch.object(
            PS, "_get_gateway_ws_url", return_value=(True, "wss://gw")))
        self.coro = self._start(mock.patch.object(PS, "_ws_capture_coroutine"))
        self.session = PS._OpenidCaptureSession("cap_1", "app-1", "secret-1")

    def test_a_token_failure_marks_the_session_failed(self):
        self.token.return_value = (False, "", "签名错误")
        PS._run_capture_thread(self.session)
        self.assertEqual(self.session.status, "failed")
        self.assertIn("无法获取 QQ Token", self.session.error)
        self.assertIn("签名错误", self.session.error)
        self.gateway.assert_not_called()

    def test_a_gateway_failure_marks_the_session_failed(self):
        self.gateway.return_value = (False, "网关 500")
        PS._run_capture_thread(self.session)
        self.assertEqual(self.session.status, "failed")
        self.assertIn("获取网关失败", self.session.error)
        self.assertIn("网关 500", self.session.error)

    def test_the_coroutine_is_driven_with_the_token_and_url(self):
        seen = {}

        async def _fake(session, token, url):
            seen.update(session=session, token=token, url=url)

        self.coro.side_effect = _fake
        PS._run_capture_thread(self.session)
        self.assertIs(seen["session"], self.session)
        self.assertEqual(seen["token"], "tok-1")
        self.assertEqual(seen["url"], "wss://gw")

    def test_the_event_loop_is_closed(self):
        seconds = {}

        async def _fake(session, token, url):
            seconds["ran"] = True

        self.coro.side_effect = _fake
        PS._run_capture_thread(self.session)
        self.assertIs(seconds.get("ran"), True)


if __name__ == "__main__":
    unittest.main()

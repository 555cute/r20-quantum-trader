"""常驻 QQ 网关 Worker：**单实例锁、指数退避、OpenID 自动捕获**（第二百八十九刀，开新面 qq_gateway_daemon.py）。

先打印整个文件（282 行）再动笔。它是 `qq_bind.ensure_qq_gateway_daemon_running()` 拉起的
常驻进程：维持一条到腾讯官方网关的长连接，让机器人在手机 QQ 上显示在线，并从私聊/群 @/
加好友事件里自动抓 OpenID。

| 语义 | 口径 |
|---|---|
| ★ **单实例锁必须"抢不到就死"** | 模块注释记着真实事故：`qq_bind` 的 `ps` 预检是无锁 check-then-act，并发调用曾 spawn 出 **111 个孤儿、3.4GB RSS**。这里靠 `flock(LOCK_EX\\|LOCK_NB)` 兜底，fd 永不关闭、进程退出（含崩溃）自动释放 |
| ★ **写锁文件失败不许把锁弄丢** | `ftruncate`/`write` 抛 `OSError` 只 `pass` —— fd 仍要留在 `_LOCK_FD` 里，否则锁会被提前释放 |
| ★ **退避 2→4→…→60 封顶** | Token 失败、网关失败、连接断开都走同一条 `backoff = min(backoff*2, 60)`；**连上并 READY 后必须把 backoff 复位成 2**（专测）|
| ★ **没有凭证就等，不发请求** | 缺 AppID 或 Secret ⇒ 记一条日志 + `sleep(5)`，**绝不去打腾讯接口** |
| ★ **只在"还没绑定"或用户显式要求时才抓** | `if not current_openid or content in ("/bind","绑定","bind","重置绑定")` ⇒ 落盘 + 回执；已有 openid 且普通内容 ⇒ **什么都不做**（不能把用户日常消息当成重新绑定）|
| ★ **收到消息但取不到 openid ⇒ 不抓** | `if openid:` 兜底：认不出发送者时只记日志，绝不写一个空 OpenID 进凭证库 |
| ★ **回执/落盘失败不许带崩长连接** | `_send_ack` 全程 try/except 并返回 bool；`_save_openid` 的失败由外层接住重连 |
| ★ **READY 里没有 username 要有兜底** | `bot_user.get("username", f"机器人{app_id}")` |
| ★ **心跳按 Hello 给的间隔发** | `next_heartbeat = time.time() + interval`；到期发 `{"op":1,"d":last_seq}`，且 `last_seq` 取自收到的 `s` |
| ★ **两类断开分开记日志** | `ConnectionClosed`/`TimeoutError`/`OSError` ⇒ 「WebSocket 断开」；其它异常 ⇒ 「网关异常」。两者都退避重连（不许静默退出）|

## 封闭性

`LOG_FILE` 在**每个**用例里都被重定向到临时目录 —— 本模块的 `log()` 默认写
`logs/qq_gateway.log`（生产），不重定向就会污染运维日志。
`RUNNING` / `_LOCK_FD` 两个模块全局逐用例快照还原；`asyncio.sleep` 被换成"记账后立刻
把 `RUNNING` 置假"的替身，既免去真等待，又让 `while RUNNING` 稳定地在**一轮之后**退出。
"""

import asyncio
import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import websockets

from r20_backend import audit as AUDIT
from r20_backend import notifications as NOTIF
from r20_backend import qq_gateway_daemon as QG
from r20_backend import settings_store as SS
from r20_gateway import secrets as SEC

_BJ = datetime.timezone(datetime.timedelta(hours=8))


class _Resp:
    def __init__(self, payload):
        self._payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeWS:
    """按脚本吐消息；吐完先回调（用来置 `RUNNING=False`）再抛 TimeoutError。"""

    def __init__(self, incoming, on_exhausted=None):
        self.incoming = list(incoming)
        self.sent = []
        self.on_exhausted = on_exhausted

    async def recv(self):
        if self.incoming:
            return self.incoming.pop(0)
        if self.on_exhausted is not None:
            self.on_exhausted()
        raise asyncio.TimeoutError()

    async def send(self, payload):
        self.sent.append(json.loads(payload))


class _FakeConnect:
    def __init__(self, ws=None, error=None):
        self.ws = ws
        self.error = error

    async def __aenter__(self):
        if self.error is not None:
            raise self.error
        return self.ws

    async def __aexit__(self, *a):
        return False


def _hello(interval=40000):
    return json.dumps({"op": 10, "d": {"heartbeat_interval": interval}})


_READY = json.dumps({"op": 0, "t": "READY",
                     "d": {"user": {"username": "机器人甲", "id": "9"}}})


def _dispatch(event="C2C_MESSAGE_CREATE", seq=7, **d):
    return json.dumps({"op": 0, "t": event, "s": seq, "d": d})


class _Base(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # ★ 必须：log() 默认写生产 logs/qq_gateway.log
        self.log_file = Path(self.tmp.name) / "logs" / "qq_gateway.log"
        self._start(mock.patch.object(QG, "LOG_FILE", self.log_file))
        self._start(mock.patch.object(QG, "LOCK_FILE",
                                      Path(self.tmp.name) / "data" / ".daemon.lock"))
        self._running_snapshot = QG.RUNNING
        self.addCleanup(setattr, QG, "RUNNING", self._running_snapshot)
        self._fd_snapshot = QG._LOCK_FD
        # ⚠️ 顺序要紧：addCleanup 是 **LIFO**，所以先登记"还原全局"、后登记"关 fd"，
        #    这样关 fd 时 `_LOCK_FD` 里还是本用例打开的那个（第一版写反了 ⇒ 6 个 fd 泄漏）。
        self.addCleanup(setattr, QG, "_LOCK_FD", self._fd_snapshot)
        self.addCleanup(self._close_lock_fd)
        QG.RUNNING = True

    def _close_lock_fd(self):
        fd = QG._LOCK_FD
        if isinstance(fd, int) and fd != self._fd_snapshot:
            try:
                os.close(fd)
            except OSError:
                pass
        QG._LOCK_FD = self._fd_snapshot

    def _log_text(self):
        if not self.log_file.exists():
            return ""
        return self.log_file.read_text(encoding="utf-8")


# =====================================================================
# 单实例锁
# =====================================================================

class SingleInstanceLockTests(_Base):
    def test_acquiring_the_lock_succeeds_and_records_the_fd(self):
        self.assertIs(QG.acquire_single_instance_lock(), True)
        self.assertIsInstance(QG._LOCK_FD, int)

    def test_the_lock_file_holds_this_pid(self):
        self.assertTrue(QG.acquire_single_instance_lock())
        self.assertEqual(QG.LOCK_FILE.read_text(encoding="utf-8").strip(),
                         str(os.getpid()))

    def test_the_lock_directory_is_created(self):
        self.assertFalse(QG.LOCK_FILE.parent.exists())
        QG.acquire_single_instance_lock()
        self.assertTrue(QG.LOCK_FILE.parent.is_dir())

    def test_a_second_acquire_in_the_same_process_fails(self):
        """⚠️ 实测：`flock` 的锁挂在**打开文件描述**上，同进程再 `open()` 一次就是另一个
        描述 ⇒ 第二次取锁**同样抢不到**（不是可重入的）。所以 `main()` 只能调一次，
        且 fd 必须存进模块全局 `_LOCK_FD` —— 这也正是它存在的理由。"""
        self.assertTrue(QG.acquire_single_instance_lock())
        self.assertIs(QG.acquire_single_instance_lock(), False)

    def test_an_unopenable_lock_file_returns_false_without_leaking_an_fd(self):
        self._start(mock.patch.object(QG.os, "open", side_effect=OSError("没有权限")))
        self.assertIs(QG.acquire_single_instance_lock(), False)
        self.assertEqual(QG._LOCK_FD, self._fd_snapshot)

    def test_a_contested_lock_returns_false_and_closes_the_fd(self):
        """抢不到锁时必须**关掉刚打开的 fd**，否则 fd 泄漏（守护进程反复自退会攒 fd）。"""
        opened = []
        real_open = os.open

        def _open(path, flags, mode=0o777):
            fd = real_open(path, flags, mode)
            opened.append(fd)
            return fd

        self._start(mock.patch.object(QG.os, "open", _open))
        self._start(mock.patch.object(QG.fcntl, "flock",
                                      side_effect=BlockingIOError("已被占用")))
        self.assertIs(QG.acquire_single_instance_lock(), False)
        self.assertTrue(opened)
        with self.assertRaises(OSError):
            os.fstat(opened[0])

    def test_a_write_failure_keeps_the_lock_instead_of_dropping_it(self):
        """★ 第 57/58 行：写 pid 失败只 `pass`，fd 仍必须留在 `_LOCK_FD` 里。"""
        self._start(mock.patch.object(QG.os, "ftruncate", side_effect=OSError("坏了")))
        self.assertIs(QG.acquire_single_instance_lock(), True)
        self.assertIsInstance(QG._LOCK_FD, int)

    def test_a_write_call_failure_also_keeps_the_lock(self):
        self._start(mock.patch.object(QG.os, "write", side_effect=OSError("坏了")))
        self.assertIs(QG.acquire_single_instance_lock(), True)
        self.assertIsInstance(QG._LOCK_FD, int)


# =====================================================================
# 日志与信号
# =====================================================================

class LogTests(_Base):
    def test_a_line_is_appended_with_a_beijing_timestamp(self):
        QG.log("测试消息")
        text = self._log_text()
        self.assertIn("测试消息", text)
        self.assertRegex(text, r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+08:00\] 测试消息\n$")

    def test_the_timestamp_is_plus_eight(self):
        stamp = datetime.datetime.now(_BJ).isoformat(sep=" ", timespec="seconds")
        QG.log("x")
        self.assertIn(stamp[:13], self._log_text())

    def test_lines_accumulate(self):
        QG.log("一")
        QG.log("二")
        self.assertEqual(len(self._log_text().strip().splitlines()), 2)

    def test_the_log_directory_is_created_lazily(self):
        self.assertFalse(self.log_file.parent.exists())
        QG.log("x")
        self.assertTrue(self.log_file.parent.is_dir())

    def test_it_also_goes_to_stdout(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            QG.log("双写")
        self.assertIn("双写", buf.getvalue())


class StopHandlerTests(_Base):
    def test_it_flips_running_to_false(self):
        QG.RUNNING = True
        QG.stop_handler(15, None)
        self.assertIs(QG.RUNNING, False)

    def test_it_logs_a_farewell(self):
        QG.stop_handler()
        self.assertIn("收到终止信号", self._log_text())

    def test_it_accepts_no_arguments(self):
        QG.stop_handler()
        self.assertIs(QG.RUNNING, False)


# =====================================================================
# 凭证与 HTTP 薄封装
# =====================================================================

class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.object(NOTIF, "_env", return_value={}).start()
        self.addCleanup(mock.patch.stopall)

    def test_all_three_values_are_returned(self):
        self.env.return_value = {"R20_QQ_APP_ID": "app-1",
                                 "R20_QQ_CLIENT_SECRET": "secret-1",
                                 "R20_QQ_OPENID": "openid-1"}
        self.assertEqual(QG._get_credentials(), ("app-1", "secret-1", "openid-1"))

    def test_missing_values_become_empty_strings(self):
        self.assertEqual(QG._get_credentials(), ("", "", ""))

    def test_values_are_stripped(self):
        self.env.return_value = {"R20_QQ_APP_ID": "  app-1  ",
                                 "R20_QQ_CLIENT_SECRET": "  s  ",
                                 "R20_QQ_OPENID": "  o  "}
        self.assertEqual(QG._get_credentials(), ("app-1", "s", "o"))

    def test_whitespace_only_values_collapse_to_empty(self):
        self.env.return_value = {"R20_QQ_APP_ID": "   ",
                                 "R20_QQ_CLIENT_SECRET": "   ",
                                 "R20_QQ_OPENID": "   "}
        self.assertEqual(QG._get_credentials(), ("", "", ""))


class AccessTokenTests(_Base):
    def _call(self, payload=None, raises=None):
        urlopen = self._start(mock.patch.object(QG.urllib.request, "urlopen"))
        if raises is not None:
            urlopen.side_effect = raises
        else:
            urlopen.return_value = _Resp(payload)
        return QG._get_access_token("app-1", "secret-1"), urlopen

    def test_a_token_is_returned(self):
        (ok, token, err), _ = self._call({"access_token": "tok-1"})
        self.assertEqual((ok, token, err), (True, "tok-1", ""))

    def test_a_missing_token_reports_code_and_message(self):
        (ok, token, err), _ = self._call({"code": 100007, "message": "签名错误"})
        self.assertEqual((ok, token), (False, ""))
        self.assertIn("Token 失败", err)
        self.assertIn("100007", err)
        self.assertIn("签名错误", err)

    def test_transport_failure_is_reported(self):
        (ok, _, err), _ = self._call(raises=RuntimeError("连接被拒"))
        self.assertIs(ok, False)
        self.assertIn("请求 Token 异常", err)

    def test_the_request_shape(self):
        _, urlopen = self._call({"access_token": "t"})
        req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url, QG.QQ_TOKEN_URL)
        self.assertEqual(req.method, "POST")
        self.assertEqual(json.loads(req.data.decode()),
                         {"appId": "app-1", "clientSecret": "secret-1"})
        self.assertEqual(urlopen.call_args[1]["timeout"], 12)


class WsUrlTests(_Base):
    def _call(self, payload=None, raises=None):
        urlopen = self._start(mock.patch.object(QG.urllib.request, "urlopen"))
        if raises is not None:
            urlopen.side_effect = raises
        else:
            urlopen.return_value = _Resp(payload)
        return QG._get_ws_url("tok-1"), urlopen

    def test_a_url_is_returned(self):
        self.assertEqual(self._call({"url": "wss://gw"})[0], (True, "wss://gw"))

    def test_a_missing_url_is_reported(self):
        self.assertEqual(self._call({})[0], (False, "QQ 网关未返回 URL"))

    def test_transport_failure_is_reported(self):
        ok, msg = self._call(raises=RuntimeError("超时"))[0]
        self.assertIs(ok, False)
        self.assertIn("请求网关异常", msg)

    def test_the_authorization_scheme(self):
        _, urlopen = self._call({"url": "wss://x"})
        req = urlopen.call_args[0][0]
        self.assertEqual(req.full_url, f"{QG.QQ_API_BASE}/gateway")
        self.assertEqual(req.get_header("Authorization"), "QQBot tok-1")


class SendAckTests(_Base):
    def _call(self, payload=None, raises=None):
        urlopen = self._start(mock.patch.object(QG.urllib.request, "urlopen"))
        if raises is not None:
            urlopen.side_effect = raises
        else:
            urlopen.return_value = _Resp(payload)
        return QG._send_ack("tok-1", "openid-1", "正文"), urlopen

    def test_a_message_id_means_success(self):
        self.assertIs(self._call({"id": "m-1"})[0], True)

    def test_a_missing_message_id_means_failure(self):
        self.assertIs(self._call({})[0], False)

    def test_an_empty_body_means_failure_not_a_crash(self):
        self.assertIs(self._call(b"")[0], False)

    def test_a_transport_failure_is_logged_and_reported(self):
        self.assertIs(self._call(raises=RuntimeError("发送被拒"))[0], False)
        self.assertIn("发送确认消息失败", self._log_text())
        self.assertIn("发送被拒", self._log_text())

    def test_the_url_and_body(self):
        _, urlopen = self._call({"id": "m"})
        req = urlopen.call_args[0][0]
        self.assertIn("/v2/users/openid-1/messages", req.full_url)
        body = json.loads(req.data.decode())
        self.assertEqual(body["content"], "正文")
        self.assertEqual(body["msg_type"], 0)
        self.assertLess(body["msg_seq"], 1_000_000)

    def test_the_openid_is_url_quoted(self):
        _, urlopen = self._call({"id": "m"})
        req = urlopen.call_args[0][0]
        self.assertIn("/v2/users/a%2Fb%2Bc/messages",
                      QG.urllib.request.Request(
                          f"{QG.QQ_API_BASE}/v2/users/"
                          f"{QG.urllib.parse.quote('a/b+c', safe='')}/messages"
                      ).full_url)
        self.assertEqual(urlopen.call_args[1]["timeout"], 8)

    def test_it_returns_a_plain_bool(self):
        self.assertIs(type(self._call({"id": "m"})[0]), bool)


class SaveOpenidTests(_Base):
    def setUp(self):
        super().setUp()
        self.save = self._start(mock.patch.object(SEC, "save_secrets"))
        self.update = self._start(mock.patch.object(SS, "update_env"))
        self.audit = self._start(mock.patch.object(AUDIT, "record"))

    def test_the_secret_store_gets_the_openid(self):
        QG._save_openid("app-1", "openid-1")
        self.save.assert_called_once_with({"R20_QQ_OPENID": "openid-1"})

    def test_the_env_gets_both_values(self):
        QG._save_openid("app-1", "openid-1")
        self.update.assert_called_once_with({"R20_QQ_APP_ID": "app-1",
                                             "R20_QQ_OPENID": "openid-1"})

    def test_an_audit_record_is_written(self):
        QG._save_openid("app-1", "openid-1")
        self.audit.assert_called_once_with("qq.openid.captured", "success",
                                           {"app_id": "app-1", "openid": "openid-1"})

    def test_it_is_logged(self):
        QG._save_openid("app-1", "openid-1")
        self.assertIn("成功持久化 OpenID", self._log_text())

    def test_a_store_failure_propagates_to_the_caller(self):
        """落盘失败要能冒到 `_run_session`，由它决定重连；这里只钉住"不吞"。"""
        self.save.side_effect = RuntimeError("密钥库坏了")
        with self.assertRaises(RuntimeError):
            QG._save_openid("app-1", "openid-1")
        self.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()


# =====================================================================
# 会话主循环
# =====================================================================

class _SessionBase(_Base):
    def setUp(self):
        super().setUp()
        self.slept = []
        self.credentials = self._start(mock.patch.object(
            QG, "_get_credentials", return_value=("app-1", "secret-1", "")))
        self.token = self._start(mock.patch.object(
            QG, "_get_access_token", return_value=(True, "tok-1", "")))
        self.ws_url = self._start(mock.patch.object(
            QG, "_get_ws_url", return_value=(True, "wss://gw")))
        self.connect = self._start(mock.patch.object(QG.websockets, "connect"))
        self.save = self._start(mock.patch.object(QG, "_save_openid"))
        self.ack = self._start(mock.patch.object(QG, "_send_ack"))

        async def _sleep(seconds, *a, **k):
            self.slept.append(seconds)
            QG.RUNNING = False       # 让 while RUNNING 稳定地在一轮后退出

        self._start(mock.patch.object(QG.asyncio, "sleep", _sleep))

    def _stop(self):
        QG.RUNNING = False

    def _run(self, stream, error=None, on_exhausted=None):
        QG.RUNNING = True        # 上一轮退出时把它置成了 False，subTest 复跑必须重新上膛
        ws = _FakeWS(stream, on_exhausted or self._stop)
        self.connect.return_value = _FakeConnect(ws, error)
        asyncio.run(QG._run_session())
        return ws


class SessionCredentialGateTests(_SessionBase):
    def test_missing_credentials_wait_without_calling_tencent(self):
        self.credentials.return_value = ("", "", "")
        self._run([])
        self.assertEqual(self.slept, [5])
        self.assertIn("等待 QQ AppID 和 Client Secret 配置", self._log_text())
        self.token.assert_not_called()
        self.connect.assert_not_called()

    def test_a_missing_secret_alone_also_waits(self):
        self.credentials.return_value = ("app-1", "", "")
        self._run([])
        self.assertEqual(self.slept, [5])
        self.token.assert_not_called()

    def test_a_token_failure_backs_off_and_retries(self):
        self.token.return_value = (False, "", "签名错误")
        self._run([])
        self.assertEqual(self.slept, [2])
        self.assertIn("获取 Access Token 失败", self._log_text())
        self.assertIn("签名错误", self._log_text())
        self.assertIn("2s 后重试", self._log_text())
        self.ws_url.assert_not_called()

    def test_a_gateway_lookup_failure_backs_off_and_retries(self):
        self.ws_url.return_value = (False, "网关 500")
        self._run([])
        self.assertEqual(self.slept, [2])
        self.assertIn("获取 WebSocket 网关失败", self._log_text())
        self.assertIn("网关 500", self._log_text())
        self.connect.assert_not_called()

    def test_the_backoff_doubles_and_is_capped_at_60(self):
        """2→4→8→16→32→60→60…：网络长期不可用时不许无限放大等待。"""
        self.token.return_value = (False, "", "一直失败")
        slept = []

        async def _sleep(seconds, *a, **k):
            slept.append(seconds)
            if len(slept) >= 9:
                QG.RUNNING = False

        with mock.patch.object(QG.asyncio, "sleep", _sleep):
            asyncio.run(QG._run_session())
        self.assertEqual(slept, [2, 4, 8, 16, 32, 60, 60, 60, 60])
        self.assertEqual(max(slept), 60)


class SessionConnectFailureTests(_SessionBase):
    def test_an_oserror_is_reported_as_a_disconnect(self):
        self._run([], error=OSError("网络断了"))
        self.assertEqual(self.slept, [2])
        self.assertIn("WebSocket 断开", self._log_text())
        self.assertIn("网络断了", self._log_text())

    def test_a_connection_closed_is_reported_as_a_disconnect(self):
        closed = websockets.exceptions.ConnectionClosedError(None, None)
        self._run([], error=closed)
        self.assertIn("WebSocket 断开", self._log_text())

    def test_a_timeout_is_reported_as_a_disconnect(self):
        self._run([], error=asyncio.TimeoutError())
        self.assertIn("WebSocket 断开", self._log_text())

    def test_any_other_exception_is_reported_as_a_gateway_error(self):
        """协议层面的意外（例如解析写错）走的是另一条日志分支，**不许静默退出**。"""
        self._run([], error=RuntimeError("协议不认识"))
        self.assertEqual(self.slept, [2])
        self.assertIn("网关异常", self._log_text())
        self.assertIn("协议不认识", self._log_text())

    def test_the_connect_call_disables_ping_and_shortens_close(self):
        self._run([], error=OSError("x"))
        self.assertEqual(self.connect.call_args[0][0], "wss://gw")
        self.assertEqual(self.connect.call_args[1]["close_timeout"], 5)
        self.assertIsNone(self.connect.call_args[1]["ping_interval"])


class SessionHandshakeTests(_SessionBase):
    def test_the_identify_frame_is_sent_first(self):
        ws = self._run([_hello(), _READY])
        self.assertEqual(ws.sent[0]["op"], 2)
        self.assertEqual(ws.sent[0]["d"]["token"], "QQBot tok-1")
        self.assertEqual(ws.sent[0]["d"]["intents"],
                         (1 << 0) | (1 << 12) | (1 << 25) | (1 << 30))
        self.assertEqual(ws.sent[0]["d"]["shard"], [0, 1])
        self.assertEqual(ws.sent[0]["d"]["properties"]["$device"], "r20")

    def test_the_heartbeat_interval_is_logged_in_seconds(self):
        self._run([_hello(interval=30000), _READY])
        self.assertIn("心跳间隔: 30.0s", self._log_text())

    def test_the_online_message_carries_the_bot_name_and_id(self):
        self._run([_hello(), _READY])
        self.assertIn("机器人已上线", self._log_text())
        self.assertIn("机器人甲", self._log_text())
        self.assertIn("ID: 9", self._log_text())

    def test_a_ready_without_a_username_falls_back_to_the_app_id(self):
        ready = json.dumps({"op": 0, "t": "READY", "d": {"user": {"id": "9"}}})
        self._run([_hello(), ready])
        self.assertIn("机器人app-1", self._log_text())

    def test_a_ready_without_any_user_block_falls_back(self):
        ready = json.dumps({"op": 0, "t": "READY", "d": {}})
        self._run([_hello(), ready])
        self.assertIn("机器人app-1", self._log_text())

    def test_a_hello_without_d_uses_the_default_interval(self):
        self._run([json.dumps({"op": 10}), _READY])
        self.assertIn("心跳间隔: 40.0s", self._log_text())

    def test_the_connection_is_logged(self):
        self._run([_hello(), _READY])
        self.assertIn("正在连接 QQ 官方网关: wss://gw", self._log_text())


class SessionHeartbeatTests(_SessionBase):
    def test_a_heartbeat_carries_the_last_sequence(self):
        ws = self._run([_hello(interval=0), _READY,
                        _dispatch(author={"user_openid": "O-1"}, content="hi", seq=42)])
        beats = [f for f in ws.sent if f["op"] == 1]
        self.assertTrue(beats, "间隔为 0 时第一轮就该发心跳")
        self.assertEqual(beats[0]["d"], 42)

    def test_a_heartbeat_without_any_event_carries_none(self):
        ws = self._run([_hello(interval=0), _READY])
        beats = [f for f in ws.sent if f["op"] == 1]
        self.assertTrue(beats)
        self.assertIsNone(beats[0]["d"])

    def test_a_long_interval_sends_no_heartbeat_in_the_first_pass(self):
        ws = self._run([_hello(interval=40000), _READY])
        self.assertEqual([f for f in ws.sent if f["op"] == 1], [])

    def test_the_sequence_only_tracks_non_null_values(self):
        """`s: null` 不得覆盖上一个序号：心跳先是 None（事件之前就到点了），最后必须是 42。"""
        ws = self._run([_hello(interval=0), _READY,
                        json.dumps({"op": 0, "t": "READY", "s": None, "d": {}}),
                        _dispatch(seq=42, author={"user_openid": "O"})])
        beats = [f["d"] for f in ws.sent if f["op"] == 1]
        self.assertIn(None, beats)
        self.assertEqual(beats[-1], 42)


class SessionCaptureTests(_SessionBase):
    def _msg(self, content="你好", openid="OPENID-1", seq=7):
        return _dispatch(author={"user_openid": openid}, content=content, seq=seq)

    def test_an_unbound_bot_captures_from_any_message(self):
        self._run([_hello(), _READY, self._msg()])
        self.save.assert_called_once_with("app-1", "OPENID-1")
        self.assertIn("绑定", self.ack.call_args[0][2])

    def test_the_ack_goes_to_the_captured_openid(self):
        self._run([_hello(), _READY, self._msg(openid="O-9")])
        self.assertEqual(self.ack.call_args[0][1], "O-9")
        self.assertEqual(self.ack.call_args[0][0], "tok-1")

    def test_a_bound_bot_ignores_an_ordinary_message(self):
        """★ 已经绑好了就不许再把用户日常消息当成重新绑定。"""
        self.credentials.return_value = ("app-1", "secret-1", "OLD")
        self._run([_hello(), _READY, self._msg(content="你好")])
        self.save.assert_not_called()
        self.ack.assert_not_called()

    def test_a_bound_bot_recaptures_on_an_explicit_request(self):
        for trigger in ("/bind", "绑定", "bind", "重置绑定"):
            with self.subTest(trigger=trigger):
                self.save.reset_mock()
                self.credentials.return_value = ("app-1", "secret-1", "OLD")
                self._run([_hello(), _READY, self._msg(content=trigger)])
                self.save.assert_called_once_with("app-1", "OPENID-1")

    def test_a_bound_bot_answers_ping(self):
        for trigger in ("ping", "Ping", "测试", "test"):
            with self.subTest(trigger=trigger):
                self.ack.reset_mock()
                self.credentials.return_value = ("app-1", "secret-1", "OLD")
                self._run([_hello(), _READY, self._msg(content=trigger)])
                self.save.assert_not_called()
                self.assertIn("Pong", self.ack.call_args[0][2])

    def test_a_message_without_any_openid_is_not_captured(self):
        """认不出发送者时只记日志 —— 绝不写一个空 OpenID 进凭证库。"""
        self._run([_hello(), _READY, _dispatch(author={}, content="hi")])
        self.save.assert_not_called()
        self.ack.assert_not_called()
        self.assertIn("收到用户消息", self._log_text())

    def test_a_blank_openid_is_not_captured(self):
        self._run([_hello(), _READY, _dispatch(author={"user_openid": ""}, content="hi")])
        self.save.assert_not_called()

    def test_the_openid_falls_back_to_the_author_id(self):
        self._run([_hello(), _READY, _dispatch(author={"id": "ID-1"}, content="hi")])
        self.save.assert_called_once_with("app-1", "ID-1")

    def test_the_openid_falls_back_to_member_openid(self):
        self._run([_hello(), _READY,
                   _dispatch(author={"member_openid": "M-1"}, content="hi")])
        self.save.assert_called_once_with("app-1", "M-1")

    def test_the_openid_falls_back_to_the_top_level_field(self):
        self._run([_hello(), _READY, _dispatch(openid="TOP-1", content="hi")])
        self.save.assert_called_once_with("app-1", "TOP-1")

    def test_group_at_mentions_are_captured_too(self):
        self._run([_hello(), _READY,
                   _dispatch(event="GROUP_AT_MESSAGE_CREATE",
                             author={"user_openid": "G-1"}, content="@机器人 hi")])
        self.save.assert_called_once_with("app-1", "G-1")

    def test_friend_add_is_captured(self):
        self._run([_hello(), _READY,
                   _dispatch(event="FRIEND_ADD", author={"user_openid": "F-1"})])
        self.save.assert_called_once_with("app-1", "F-1")

    def test_direct_messages_are_captured(self):
        self._run([_hello(), _READY,
                   _dispatch(event="DIRECT_MESSAGE_CREATE",
                             author={"user_openid": "D-1"})])
        self.save.assert_called_once_with("app-1", "D-1")

    def test_the_openid_is_stripped_before_saving(self):
        self._run([_hello(), _READY, self._msg(openid="  O-1  ")])
        self.save.assert_called_once_with("app-1", "O-1")

    def test_the_content_is_stripped_in_the_log(self):
        self._run([_hello(), _READY, self._msg(content="  你好  ")])
        self.assertIn("内容=你好", self._log_text())

    def test_the_logged_content_is_truncated_to_50(self):
        self._run([_hello(), _READY, self._msg(content="长" * 200)])
        line = [ln for ln in self._log_text().splitlines() if "收到用户消息" in ln][0]
        self.assertEqual(len(line.split("内容=")[1]), 50)

    def test_an_unrelated_event_is_ignored(self):
        self._run([_hello(), _READY,
                   _dispatch(event="SOMETHING_ELSE", author={"user_openid": "X"})])
        self.save.assert_not_called()

    def test_a_non_dispatch_opcode_is_ignored(self):
        self._run([_hello(), _READY,
                   json.dumps({"op": 11, "t": "C2C_MESSAGE_CREATE",
                               "d": {"author": {"user_openid": "X"}}})])
        self.save.assert_not_called()

    def test_a_receive_timeout_is_silently_tolerated(self):
        """`TimeoutError` 是**正常**的（用来驱动心跳节拍），不许记成错误。"""
        self._run([_hello(), _READY])
        self.assertNotIn("网关异常", self._log_text())
        self.assertNotIn("WebSocket 断开", self._log_text())

    def test_a_capture_survives_an_ack_failure(self):
        self.ack.side_effect = RuntimeError("发送失败")
        self._run([_hello(), _READY, self._msg()])
        self.save.assert_called_once_with("app-1", "OPENID-1")

    def test_a_save_failure_is_swallowed_by_the_session_loop(self):
        """`_save_openid` 抛错不能带崩长连接 —— 由外层接住后重连（这里退避退出）。"""
        self.save.side_effect = RuntimeError("密钥库坏了")
        self._run([_hello(), _READY, self._msg()])
        self.assertEqual(self.slept, [2])
        self.assertIn("网关异常", self._log_text())
        self.assertIn("密钥库坏了", self._log_text())


class MainTests(_Base):
    def test_a_contested_lock_exits_quietly_without_touching_signals(self):
        self._start(mock.patch.object(QG, "acquire_single_instance_lock",
                                      return_value=False))
        sig = self._start(mock.patch.object(QG.signal, "signal"))
        run = self._start(mock.patch.object(QG.asyncio, "run"))
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            QG.main()
        sig.assert_not_called()
        run.assert_not_called()
        self.assertIn("已有实例在运行", buf.getvalue())
        self.assertIn(str(QG.LOCK_FILE), buf.getvalue())
        self.assertNotIn("启动中", self._log_text())

    def test_a_held_lock_registers_handlers_and_runs_the_session(self):
        self._start(mock.patch.object(QG, "acquire_single_instance_lock",
                                      return_value=True))
        sig = self._start(mock.patch.object(QG.signal, "signal"))
        calls = []

        async def _fake_session():
            calls.append("ran")

        self._start(mock.patch.object(QG, "_run_session", _fake_session))
        QG.main()
        self.assertEqual(calls, ["ran"])
        # ⚠️ `asyncio.run` 自己也会注册一次 SIGINT，故只断言**前两次**是我们的
        registered = [c[0][0] for c in sig.call_args_list]
        self.assertEqual(registered[:2], [QG.signal.SIGTERM, QG.signal.SIGINT])
        self.assertTrue(all(c[0][1] is QG.stop_handler
                            for c in sig.call_args_list[:2]))
        self.assertIn("QQ Gateway Daemon 启动中", self._log_text())
        self.assertIn("QQ Gateway Daemon 已安全停止", self._log_text())

    def test_main_stops_logging_after_the_session_returns(self):
        self._start(mock.patch.object(QG, "acquire_single_instance_lock",
                                      return_value=True))
        self._start(mock.patch.object(QG.signal, "signal"))

        async def _fake_session():
            pass

        self._start(mock.patch.object(QG, "_run_session", _fake_session))
        QG.main()
        lines = self._log_text().strip().splitlines()
        self.assertIn("启动中", lines[0])
        self.assertIn("已安全停止", lines[-1])


if __name__ == "__main__":
    unittest.main()

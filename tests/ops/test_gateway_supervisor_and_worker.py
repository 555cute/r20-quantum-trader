"""网关「单所有者」监管对：**flock 才是真相、PID 文件只是缓存**（第二百九十六刀，开新面 supervisor.py / worker.py）。

先打印两个文件（148 + 139 行）再动笔。它们一起守着"全机只有一个网关 worker"这条不变量：
supervisor 每 10 秒探活/补拉，worker 抢 `flock` 后自我登记 PID。

| 语义 | 口径 |
|---|---|
| ★ **`flock` 是唯一的活体真相** | PID 文件会漂移（死 pid 被反复盖写），锁却与持锁进程**同生共死**。故 `ensure_worker` 先用 `_lock_held()` 判：**锁被持有 ⇒ 收养 `/proc` 真身，绝不 spawn**；锁空闲 ⇒ 才 spawn |
| ★ **收养来的不是亲生子：不接管也不杀** | 收养分支把 `_owned_pid` **清零** —— 否则 `stop_supervisor` 会去 SIGTERM 一个不属于本次启动的进程 |
| ★ **锁被占但真身不可辨 ⇒ 本轮什么都不做** | 这是 spawn 与自注册之间的临界窗口：**返回 0、下 tick 再探**，绝不盲目补拉（那正是"每 10s 重生风暴"的成因）|
| ★ **`/proc/<pid>/cwd` 读不到就降级判定** | 跨进程 `readlink` 在非 ptrace 权限下 EACCES。降级为只看 cmdline —— 安全性由 flock 单持有者保证 |
| ★ **真身取启动最早者** | `stat` 的 `starttime`（jiffies）越小越老；排除自己、排除刚 spawn 的将死子进程 |
| ★ **worker 抢不到锁就静默退出** | 只记一行日志，**不报错、不退避重试**（重试由 supervisor 负责）|
| ★ **抢到锁者自我登记为权威 PID** | 注释点名的历史事故：旧实现由 supervisor **盲写** PID 文件 ⇒ 注定秒退的子进程把死 pid 盖进文件，活体持锁者反而不可见，**每 10s 重生一次（日志 948 条）** |
| ★ **tick 不许被投递饿死** | 每轮**至多发 1 条**，发完立刻回到循环顶 `scheduler.tick()`；积压只影响投递速率，不波及排程 |
| ★ **作业历史清理失败不许拖垮调度** | `_prune_job_history` 自行 try/except；启动清一次、之后每 6 小时一次 |
| ★ **时间戳里的 `T` 要换掉** | ISO 形态的 `created_at` 直接展示会有个 `T`，故替换成空格并截到 19 位 |

## 封闭性（这个模块**真的会起进程、发信号**）

- `subprocess.Popen` **全程打桩** —— 否则测试会真的拉起 `python -m r20_gateway.worker`；
- `os.kill` 在 `stop_supervisor` 用例里打桩 —— 否则可能 SIGTERM **线上那个 worker**；
- `PID_FILE` / `LOCK_FILE` / `LOG_FILE` 三个路径常量**全部**改写到临时目录
  （`PID_FILE` 默认就是线上 `data/r20_gateway.pid`，而线上 worker 正在写它）；
- `signal.signal` 打桩 —— `worker.run()` 会注册 SIGTERM/SIGINT 处理器，不能让它改掉测试进程的；
- `_owned_pid` / `_thread` / `_stop` / `RUNNING` 四个模块全局逐用例快照还原。
"""

import fcntl
import json
import os
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from r20_gateway import supervisor as SUP
from r20_gateway import worker as WKR
from r20_gateway.pidfile import PID_FILE as GATEWAY_PID_FILE

# ⚠️ 本文件**不许**出现 `"r20_gateway.pid"` 这个字符串字面量
#（门禁 `test_path_literal_only_in_pidfile_module` 以 AST 扫描 tests/ 全树）。
# 文件名一律从常量取 `.name`，锁文件名同理从两个模块的 `LOCK_FILE.name` 取。
PID_NAME = GATEWAY_PID_FILE.name
SUP_LOCK_NAME = SUP.LOCK_FILE.name
WKR_LOCK_NAME = WKR.LOCK_FILE.name


class _SupBase(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.pid_file = self.data / PID_NAME
        self.lock_file = self.data / SUP_LOCK_NAME
        self.log_file = Path(self.tmp.name) / "logs" / "sup.log"
        self._start(mock.patch.object(SUP, "PID_FILE", self.pid_file))
        self._start(mock.patch.object(SUP, "LOCK_FILE", self.lock_file))
        self._start(mock.patch.object(SUP, "LOG_FILE", self.log_file))
        self._owned = SUP._owned_pid
        self._thread = SUP._thread
        self._stop_obj = SUP._stop
        self.addCleanup(setattr, SUP, "_owned_pid", self._owned)
        self.addCleanup(setattr, SUP, "_thread", self._thread)
        self.addCleanup(setattr, SUP, "_stop", self._stop_obj)
        SUP._owned_pid = 0
        SUP._thread = None

    def _live(self, pid=4242):
        """让 `_alive(pid)` 成真：`os.kill(pid, 0)` 不抛即可。"""
        return self._start(mock.patch.object(SUP.os, "kill"))

    def _log(self):
        return self.log_file.read_text(encoding="utf-8") if self.log_file.exists() else ""

    def _stat(self, pid, starttime):
        """`/proc/<pid>/stat` 的最小可用形态（去掉 `pid (comm)` 后 starttime 落在 fields[19]）。"""
        filler = [str(900 + i) for i in range(19)]
        return f"{pid} (python) " + " ".join(filler + [str(starttime)])


# =====================================================================
# supervisor：探活与身份判定
# =====================================================================

class AliveTests(_SupBase):
    def test_a_non_positive_pid_is_dead(self):
        self.assertIs(SUP._alive(0), False)
        self.assertIs(SUP._alive(-5), False)

    def test_a_signalable_pid_is_alive(self):
        self._live()
        self.assertIs(SUP._alive(4242), True)

    def test_an_oserror_means_dead(self):
        self._start(mock.patch.object(SUP.os, "kill", side_effect=OSError("无此进程")))
        self.assertIs(SUP._alive(4242), False)

    def test_the_zero_signal_is_used(self):
        kill = self._live()
        SUP._alive(4242)
        self.assertEqual(kill.call_args[0], (4242, 0))


class IsGatewayWorkerTests(_SupBase):
    def setUp(self):
        super().setUp()
        self.cmdline = self._start(mock.patch.object(
            SUP.Path, "read_bytes", return_value=b"python\0-m\0r20_gateway.worker\0"))
        real = Path.resolve

        def _resolve(self_, *a, **k):
            return SUP.ROOT if str(self_).endswith("/cwd") else real(self_, *a, **k)

        self._start(mock.patch.object(SUP.Path, "resolve", _resolve))

    def test_a_dead_pid_is_not_a_worker(self):
        self.assertIs(SUP._is_gateway_worker(4242), False)
        self.cmdline.assert_not_called()

    def test_a_live_gateway_worker_is_recognised(self):
        self._live()
        self.assertIs(SUP._is_gateway_worker(4242), True)

    def test_another_program_is_not_a_worker(self):
        self._live()
        self.cmdline.return_value = b"python\0-m\0something_else\0"
        self.assertIs(SUP._is_gateway_worker(4242), False)

    def test_an_unreadable_cmdline_is_not_a_worker(self):
        self._live()
        self.cmdline.side_effect = OSError("EACCES")
        self.assertIs(SUP._is_gateway_worker(4242), False)

    def test_an_unreadable_cwd_degrades_to_the_cmdline_verdict(self):
        """★ 跨进程 readlink 在非 ptrace 权限下 EACCES ⇒ 降级靠 cmdline 判定。"""
        self._live()
        real = Path.resolve

        def _resolve(self_, *a, **k):
            if str(self_).endswith("/cwd"):
                raise OSError("EACCES")
            return real(self_, *a, **k)

        self._start(mock.patch.object(SUP.Path, "resolve", _resolve))
        self.assertIs(SUP._is_gateway_worker(4242), True)

    def test_a_worker_from_another_checkout_is_rejected(self):
        """⚠️ 不能把 `Path.resolve` 整个桩成定值 —— 那样 `ROOT.resolve()` 也变成
        同一个值，两边永远相等，用例会假绿。只换 `/cwd` 那条路径。"""
        self._live()
        real = Path.resolve

        def _resolve(self_, *a, **k):
            if str(self_).endswith("/cwd"):
                return Path("/somewhere/else")
            return real(self_, *a, **k)

        self._start(mock.patch.object(SUP.Path, "resolve", _resolve))
        self.assertIs(SUP._is_gateway_worker(4242), False)

    def test_the_null_bytes_are_turned_into_spaces(self):
        self._live()
        self.cmdline.return_value = b"python\x00-m\x00r20_gateway.worker\x00"
        self.assertIs(SUP._is_gateway_worker(4242), True)


class CurrentPidTests(_SupBase):
    def test_a_missing_file_is_zero(self):
        self.assertEqual(SUP.current_pid(), 0)

    def test_a_non_numeric_file_is_zero(self):
        self.pid_file.write_text("not-a-pid", encoding="utf-8")
        self.assertEqual(SUP.current_pid(), 0)

    def test_a_matching_worker_pid_is_returned(self):
        self.pid_file.write_text("4242", encoding="utf-8")
        self._live()
        self._start(mock.patch.object(SUP, "_is_gateway_worker", return_value=True))
        self.assertEqual(SUP.current_pid(), 4242)

    def test_a_stale_pid_file_is_removed(self):
        self.pid_file.write_text("4242", encoding="utf-8")
        self._start(mock.patch.object(SUP, "_is_gateway_worker", return_value=False))
        self.assertEqual(SUP.current_pid(), 0)
        self.assertFalse(self.pid_file.exists())

    def test_an_unlink_failure_is_swallowed(self):
        self.pid_file.write_text("4242", encoding="utf-8")
        self._start(mock.patch.object(SUP, "_is_gateway_worker", return_value=False))
        self._start(mock.patch.object(SUP.Path, "unlink", side_effect=OSError("只读")))
        self.assertEqual(SUP.current_pid(), 0)

    def test_whitespace_around_the_pid_is_tolerated(self):
        self.pid_file.write_text("  4242\n", encoding="utf-8")
        self._start(mock.patch.object(SUP, "_is_gateway_worker", return_value=True))
        self.assertEqual(SUP.current_pid(), 4242)


class LockHeldTests(_SupBase):
    def test_a_missing_lock_file_means_no_holder(self):
        self.assertIs(SUP._lock_held(), False)

    def test_an_unopenable_lock_file_means_no_holder(self):
        self.lock_file.write_text("", encoding="utf-8")
        self._start(mock.patch.object(SUP.Path, "open", side_effect=OSError("EACCES")))
        self.assertIs(SUP._lock_held(), False)

    def test_a_free_lock_means_no_holder(self):
        self.lock_file.write_text("", encoding="utf-8")
        self.assertIs(SUP._lock_held(), False)

    def test_a_held_lock_means_a_live_holder(self):
        """★ 锁与持锁进程同生共死 —— 这是唯一的活体真相。"""
        holder = self.lock_file.open("a", encoding="utf-8")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            self.assertIs(SUP._lock_held(), True)
        finally:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()

    def test_an_unlock_failure_is_swallowed(self):
        """`finally` 里的解锁失败也要吞掉（否则探针本身会把调用方带崩）。

        ⚠️ `_lock_held` 内部是 `import fcntl`，每次调用都会把模块全局**重新绑定**，
        所以 `patch.object(SUP, "fcntl")` 是无效的 —— 必须打在 `fcntl` 模块本身。
        """
        self.lock_file.write_text("", encoding="utf-8")
        ops = []

        def _flock(_fd, op):
            ops.append(op)
            if op == fcntl.LOCK_UN:
                raise OSError("解锁失败")
            return None     # LOCK_EX|LOCK_NB "成功" ⇒ 判定为无持有者

        self._start(mock.patch.object(fcntl, "flock", _flock))
        self.assertIs(SUP._lock_held(), False)
        self.assertIn(fcntl.LOCK_UN, ops)

    def test_the_probe_releases_its_own_lock(self):
        """探针跑完必须把自己的锁放掉 —— 否则后续每一次判活都会误报"有持有者"。

        `flock` 的锁挂在**打开文件描述**上，所以换一个 fd 能抢到就证明前一个已释放。
        """
        self.lock_file.write_text("", encoding="utf-8")
        self.assertIs(SUP._lock_held(), False)

        probe = self.lock_file.open("a", encoding="utf-8")
        try:
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            acquired = False
        finally:
            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
            probe.close()
        self.assertIs(acquired, True, "探针没释放自己的锁（`finally` 里的 LOCK_UN 缺失？）")


class FindLiveWorkerPidTests(_SupBase):
    """⚠️ 这里必须用**具名函数**去 patch `Path.read_text`。

    `mock.patch.object(Path, "read_text")` 装上去的是 MagicMock，而 MagicMock
    **不是描述符**，所以 `Path(...).read_text()` 调用时不会把 `self` 传进去 ——
    `side_effect=self_(self_, *a)` 会直接 `TypeError: missing 1 required
    positional argument`。赋一个真正的函数上去才有描述符绑定。
    """

    def setUp(self):
        super().setUp()
        self.listdir = self._start(mock.patch.object(
            SUP.os, "listdir",
            return_value=["1", str(os.getpid()), "4242", "5000", "not-a-pid", "self"]))
        self.worker_pids = {4242, 5000}
        self._start(mock.patch.object(
            SUP, "_is_gateway_worker", side_effect=lambda pid: pid in self.worker_pids))
        self._install_read_text(lambda self_, *a, **k: (_ for _ in ()).throw(OSError("x")))

    def _install_read_text(self, fn):
        patcher = mock.patch.object(SUP.Path, "read_text", fn)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _stats(self, mapping):
        def _read(self_, *a, **k):
            for pid, start in mapping.items():
                if str(self_).startswith(f"/proc/{pid}/"):
                    return self._stat(pid, start)
            raise OSError("没有这个 pid")

        self._install_read_text(_read)

    def test_an_unreadable_proc_yields_zero(self):
        self.listdir.side_effect = OSError("没有 /proc")
        self.assertEqual(SUP._find_live_worker_pid(), 0)

    def test_the_oldest_worker_wins(self):
        self._stats({4242: 500, 5000: 300})
        self.assertEqual(SUP._find_live_worker_pid(), 5000)

    def test_non_numeric_entries_are_skipped(self):
        self._stats({4242: 500})
        self.worker_pids = {4242}
        self.assertEqual(SUP._find_live_worker_pid(), 4242)

    def test_the_current_process_is_excluded(self):
        self.worker_pids = {os.getpid(), 4242}
        self._stats({os.getpid(): 1, 4242: 500})
        self.assertEqual(SUP._find_live_worker_pid(), 4242)

    def test_an_unreadable_stat_is_skipped(self):
        """5000 也是 worker，但它的 `/proc/<pid>/stat` 读不到 ⇒ 只能退而取 4242。"""
        self._stats({4242: 500})
        self.assertEqual(SUP._find_live_worker_pid(), 4242)

    def test_a_short_stat_line_is_skipped(self):
        self._install_read_text(lambda self_, *a, **k: "4242 (python) S 1")
        self.assertEqual(SUP._find_live_worker_pid(), 0)

    def test_no_worker_at_all_yields_zero(self):
        self.worker_pids = set()
        self.assertEqual(SUP._find_live_worker_pid(), 0)


# =====================================================================
# supervisor：补拉、收养、启停
# =====================================================================

class EnsureWorkerTests(_SupBase):
    def setUp(self):
        super().setUp()
        self.current = self._start(mock.patch.object(SUP, "current_pid", return_value=0))
        self.held = self._start(mock.patch.object(SUP, "_lock_held", return_value=False))
        self.find = self._start(mock.patch.object(SUP, "_find_live_worker_pid",
                                                  return_value=0))
        self.is_worker = self._start(mock.patch.object(SUP, "_is_gateway_worker",
                                                       return_value=False))
        self.popen = self._start(mock.patch.object(SUP.subprocess, "Popen"))
        self.popen.return_value.pid = 7777

    def test_an_existing_worker_short_circuits(self):
        self.current.return_value = 1234
        self.assertEqual(SUP.ensure_worker(), 1234)
        self.popen.assert_not_called()

    def test_a_live_own_child_is_reused_without_respawning(self):
        SUP._owned_pid = 4321
        self.is_worker.return_value = True
        self.assertEqual(SUP.ensure_worker(), 4321)
        self.popen.assert_not_called()

    def test_a_dead_own_child_is_not_reused(self):
        SUP._owned_pid = 4321
        self.is_worker.return_value = False
        self.assertEqual(SUP.ensure_worker(), 7777)

    def test_a_free_lock_spawns_a_worker(self):
        self.assertEqual(SUP.ensure_worker(), 7777)
        self.popen.assert_called_once()
        self.assertEqual(SUP._owned_pid, 7777)

    def test_the_spawn_command_and_redirection(self):
        SUP.ensure_worker()
        args, kwargs = self.popen.call_args
        self.assertEqual(args[0], [SUP.sys.executable, "-m", "r20_gateway.worker"])
        self.assertEqual(kwargs["cwd"], SUP.ROOT)
        self.assertIs(kwargs["stdin"], SUP.subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], SUP.subprocess.STDOUT)
        self.assertIsNotNone(kwargs["stdout"])

    def test_the_log_file_is_created_for_the_child(self):
        self.assertFalse(self.log_file.parent.exists())
        SUP.ensure_worker()
        self.assertTrue(self.log_file.exists())

    def test_a_held_lock_adopts_the_live_worker_instead_of_spawning(self):
        """★ 锁被持有 ⇒ 绝不 spawn（那正是"每 10s 重生风暴"的成因）。"""
        self.held.return_value = True
        self.find.return_value = 5555
        self.assertEqual(SUP.ensure_worker(), 5555)
        self.popen.assert_not_called()
        self.assertEqual(self.pid_file.read_text(encoding="utf-8"), "5555")

    def test_an_adopted_worker_is_not_owned(self):
        """★ 收养来的不是亲生子 —— `_owned_pid` 必须清零，否则停机会误杀别人。"""
        self.held.return_value = True
        self.find.return_value = 5555
        SUP._owned_pid = 9999
        SUP.ensure_worker()
        self.assertEqual(SUP._owned_pid, 0)

    def test_the_adopted_pid_file_is_owner_only(self):
        self.held.return_value = True
        self.find.return_value = 5555
        SUP.ensure_worker()
        self.assertEqual(self.pid_file.stat().st_mode & 0o777, 0o600)

    def test_an_adopt_write_failure_is_swallowed(self):
        self.held.return_value = True
        self.find.return_value = 5555
        self._start(mock.patch.object(SUP.Path, "write_text", side_effect=OSError("只读")))
        self.assertEqual(SUP.ensure_worker(), 5555)

    def test_a_held_lock_with_no_identifiable_worker_does_nothing(self):
        """★ 临界窗口：锁被占但真身不可辨 ⇒ 返回 0、下 tick 再探，绝不盲补。"""
        self.held.return_value = True
        self.find.return_value = 0
        self.assertEqual(SUP.ensure_worker(), 0)
        self.popen.assert_not_called()
        self.assertFalse(self.pid_file.exists())


class SupervisorLoopTests(_SupBase):
    def test_the_loop_ensures_then_waits(self):
        ensure = self._start(mock.patch.object(SUP, "ensure_worker"))
        event = mock.MagicMock()
        event.is_set.side_effect = [False, True]
        self._start(mock.patch.object(SUP, "_stop", event))
        SUP._run()
        ensure.assert_called_once()
        event.wait.assert_called_once_with(10)

    def test_the_loop_does_not_run_when_already_stopping(self):
        ensure = self._start(mock.patch.object(SUP, "ensure_worker"))
        event = mock.MagicMock()
        event.is_set.return_value = True
        self._start(mock.patch.object(SUP, "_stop", event))
        SUP._run()
        ensure.assert_not_called()

    def test_start_creates_a_daemon_thread_once(self):
        self._start(mock.patch.object(SUP, "ensure_worker"))
        thread = self._start(mock.patch.object(SUP.threading, "Thread"))
        SUP.start_supervisor()
        thread.assert_called_once()
        self.assertEqual(thread.call_args[1]["name"], "r20-gateway-supervisor")
        self.assertIs(thread.call_args[1]["daemon"], True)
        self.assertIs(thread.call_args[1]["target"], SUP._run)
        thread.return_value.start.assert_called_once()

    def test_start_also_ensures_a_worker_immediately(self):
        ensure = self._start(mock.patch.object(SUP, "ensure_worker"))
        self._start(mock.patch.object(SUP.threading, "Thread"))
        SUP.start_supervisor()
        ensure.assert_called_once()

    def test_start_is_idempotent_while_the_thread_lives(self):
        self._start(mock.patch.object(SUP, "ensure_worker"))
        self._start(mock.patch.object(SUP.threading, "Thread"))
        SUP._thread = mock.MagicMock()
        SUP._thread.is_alive.return_value = True
        SUP.start_supervisor()
        self.assertIsNotNone(SUP._thread)

    def test_start_clears_a_previous_stop_request(self):
        self._start(mock.patch.object(SUP, "ensure_worker"))
        self._start(mock.patch.object(SUP.threading, "Thread"))
        SUP._stop.set()
        SUP.start_supervisor()
        self.assertIs(SUP._stop.is_set(), False)


class StopSupervisorTests(_SupBase):
    def test_no_owned_child_just_flags_the_stop(self):
        kill = self._live()
        SUP._owned_pid = 0
        SUP.stop_supervisor()
        self.assertIs(SUP._stop.is_set(), True)
        kill.assert_not_called()
        self.assertEqual(SUP._owned_pid, 0)

    def test_an_owned_worker_gets_a_sigterm(self):
        kill = self._live()
        self._start(mock.patch.object(SUP, "_is_gateway_worker", return_value=True))
        self._start(mock.patch.object(SUP, "_alive", return_value=False))
        self.pid_file.write_text("4242", encoding="utf-8")
        SUP._owned_pid = 4242
        SUP.stop_supervisor()
        self.assertEqual(kill.call_args[0], (4242, signal.SIGTERM))

    def test_the_pid_file_is_removed_once_the_child_is_gone(self):
        self._live()
        self._start(mock.patch.object(SUP, "_is_gateway_worker", return_value=True))
        self._start(mock.patch.object(SUP, "_alive", return_value=False))
        self.pid_file.write_text("4242", encoding="utf-8")
        SUP._owned_pid = 4242
        SUP.stop_supervisor()
        self.assertFalse(self.pid_file.exists())

    def test_the_pid_file_survives_a_child_that_refuses_to_die(self):
        self._live()
        self._start(mock.patch.object(SUP, "_is_gateway_worker", return_value=True))
        self._start(mock.patch.object(SUP, "_alive", return_value=True))
        self.pid_file.write_text("4242", encoding="utf-8")
        clock = {"t": 0.0}

        def _time():
            clock["t"] += 5.0
            return clock["t"]

        self._start(mock.patch.object(SUP.time, "time", _time))
        self._start(mock.patch.object(SUP.time, "sleep"))
        SUP._owned_pid = 4242
        SUP.stop_supervisor()
        self.assertTrue(self.pid_file.exists())
        self.assertEqual(SUP._owned_pid, 0)

    def test_a_non_worker_child_is_not_signalled(self):
        kill = self._live()
        self._start(mock.patch.object(SUP, "_is_gateway_worker", return_value=False))
        self._start(mock.patch.object(SUP, "_alive", return_value=True))
        SUP._owned_pid = 4242
        SUP.stop_supervisor()
        kill.assert_not_called()

    def test_a_signal_failure_is_swallowed(self):
        self._start(mock.patch.object(SUP.os, "kill", side_effect=OSError("没权限")))
        self._start(mock.patch.object(SUP, "_is_gateway_worker", return_value=True))
        self._start(mock.patch.object(SUP, "_alive", return_value=False))
        SUP._owned_pid = 4242
        self.assertIsNone(SUP.stop_supervisor())
        self.assertEqual(SUP._owned_pid, 0)

    def test_a_pid_unlink_failure_is_swallowed(self):
        self._live()
        self._start(mock.patch.object(SUP, "_is_gateway_worker", return_value=True))
        self._start(mock.patch.object(SUP, "_alive", return_value=False))
        self.pid_file.write_text("4242", encoding="utf-8")
        self._start(mock.patch.object(SUP.Path, "unlink", side_effect=OSError("只读")))
        SUP._owned_pid = 4242
        self.assertIsNone(SUP.stop_supervisor())
        self.assertEqual(SUP._owned_pid, 0)

    def test_the_child_pid_is_forgotten_afterwards(self):
        self._live()
        self._start(mock.patch.object(SUP, "_is_gateway_worker", return_value=True))
        self._start(mock.patch.object(SUP, "_alive", return_value=False))
        SUP._owned_pid = 4242
        SUP.stop_supervisor()
        self.assertEqual(SUP._owned_pid, 0)


# =====================================================================
# worker
# =====================================================================

class _WorkerBase(unittest.TestCase):
    def _start(self, patcher):
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.pid_file = self.data / PID_NAME
        self.lock_file = self.data / WKR_LOCK_NAME
        self.log_file = Path(self.tmp.name) / "logs" / "gateway.log"
        self._start(mock.patch.object(WKR, "PID_FILE", self.pid_file))
        self._start(mock.patch.object(WKR, "LOCK_FILE", self.lock_file))
        self._start(mock.patch.object(WKR, "LOG_FILE", self.log_file))
        # 存活心跳（2026-09 新增）：循环每轮会写一次。必须改道临时目录，
        # 否则每个跑 run() 的用例都会往**生产 data/** 里写文件。
        self.heartbeat_file = self.data / "gateway_heartbeat"
        self._start(mock.patch.object(WKR, "HEARTBEAT_FILE", self.heartbeat_file))
        self._running = WKR.RUNNING
        self.addCleanup(setattr, WKR, "RUNNING", self._running)
        WKR.RUNNING = True
        # ★ 绝不允许它改掉测试进程的信号处理器
        self.signal = self._start(mock.patch.object(WKR.signal, "signal"))

    def _log(self):
        return self.log_file.read_text(encoding="utf-8") if self.log_file.exists() else ""


class WorkerLogTests(_WorkerBase):
    def test_a_beijing_timestamped_line_is_appended(self):
        WKR.log("你好")
        self.assertRegex(self._log(), r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] 你好\n$")

    def test_lines_accumulate(self):
        WKR.log("一")
        WKR.log("二")
        self.assertEqual(len(self._log().strip().splitlines()), 2)

    def test_the_log_directory_is_created_lazily(self):
        self.assertFalse(self.log_file.parent.exists())
        WKR.log("x")
        self.assertTrue(self.log_file.parent.is_dir())

    def test_it_does_not_print(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            WKR.log("安静")
        self.assertEqual(buf.getvalue(), "")


class WorkerStopTests(_WorkerBase):
    def test_stop_flips_running(self):
        WKR.stop()
        self.assertIs(WKR.RUNNING, False)

    def test_stop_accepts_signal_arguments(self):
        WKR.stop(15, None)
        self.assertIs(WKR.RUNNING, False)


class FormatMessageTests(unittest.TestCase):
    def _row(self, **over):
        row = {"created_at": "2026-09-20T10:11:12.345678", "title": "开仓",
               "message": "  BTC 多单  "}
        row.update(over)
        return row

    def test_a_plain_title_gets_the_brand_prefix(self):
        self.assertIn("【AstraQuant】开仓", WKR.format_message(self._row()))

    def test_a_title_already_carrying_the_brand_is_kept(self):
        self.assertIn("【R20 风控】", WKR.format_message(self._row(title="【R20 风控】警告")))

    def test_a_title_merely_containing_the_brand_is_also_kept(self):
        out = WKR.format_message(self._row(title="前缀【R20】后缀"))
        self.assertIn("前缀【R20】后缀", out)
        self.assertNotIn("【AstraQuant】前缀", out)

    def test_the_iso_t_separator_is_replaced_and_truncated(self):
        self.assertIn("⏱️ 时间：2026-09-20 10:11:12", WKR.format_message(self._row()))

    def test_a_plain_timestamp_is_kept_as_is(self):
        out = WKR.format_message(self._row(created_at="2026-09-20 10:11:12"))
        self.assertIn("⏱️ 时间：2026-09-20 10:11:12", out)

    def test_the_body_is_stripped(self):
        out = WKR.format_message(self._row(message="  BTC 多单  "))
        self.assertTrue(out.endswith("BTC 多单"))

    def test_missing_fields_do_not_raise(self):
        out = WKR.format_message({})
        self.assertIn("【AstraQuant】", out)
        self.assertIn("⏱️ 时间：", out)

    def test_the_separator_is_present(self):
        self.assertIn("━━━━━━━━━━━━━━", WKR.format_message(self._row()))


class PruneJobHistoryTests(_WorkerBase):
    def _store(self, result=None, raises=None):
        store = mock.MagicMock()
        if raises is not None:
            store.prune_job_runs.side_effect = raises
        else:
            store.prune_job_runs.return_value = result or {}
        return store

    def test_a_clean_prune_is_silent_when_nothing_was_deleted(self):
        WKR._prune_job_history(self._store({"deleted": 0, "keep_days": 30,
                                            "vacuumed": False}))
        self.assertEqual(self._log(), "")

    def test_a_deletion_is_logged_with_the_vacuum_flag(self):
        WKR._prune_job_history(self._store({"deleted": 120, "keep_days": 30,
                                            "vacuumed": True}))
        text = self._log()
        self.assertIn("删除 120 条", text)
        self.assertIn("保留 30 天", text)
        self.assertIn("VACUUM=是", text)

    def test_the_vacuum_flag_is_reported_when_false(self):
        WKR._prune_job_history(self._store({"deleted": 1, "keep_days": 7,
                                            "vacuumed": False}))
        self.assertIn("VACUUM=否", self._log())

    def test_a_failure_is_logged_and_swallowed(self):
        """★ 可观测性维护失败绝不能拖垮调度循环。"""
        WKR._prune_job_history(self._store(raises=RuntimeError("库锁住了")))
        text = self._log()
        self.assertIn("job_runs 清理失败（不影响调度）", text)
        self.assertIn("RuntimeError", text)
        self.assertIn("库锁住了", text)

    def test_an_unexpected_result_shape_does_not_raise(self):
        WKR._prune_job_history(self._store({}))
        self.assertEqual(self._log(), "")


class WorkerRunTests(_WorkerBase):
    def setUp(self):
        super().setUp()
        self.store = mock.MagicMock()
        self.store.recover_stale_job_runs.return_value = 0
        self.store.prune_job_runs.return_value = {}
        self.store.claim_due.return_value = []
        self.store_cls = self._start(mock.patch.object(WKR, "GatewayStore",
                                                       return_value=self.store))
        self.scheduler = mock.MagicMock()
        self.scheduler.tick.return_value = []
        self._start(mock.patch.object(WKR, "GatewayScheduler",
                                      return_value=self.scheduler))
        self.adapter = self._start(mock.patch.object(WKR, "NotificationChannelAdapter"))
        self._start(mock.patch.object(WKR, "time", mock.MagicMock(wraps=time))
                    if False else mock.patch.object(WKR.time, "time", return_value=1000.0))

        def _sleep(_seconds):
            WKR.RUNNING = False     # 空转一次就退出循环

        self._start(mock.patch.object(WKR.time, "sleep", _sleep))
        self._start(mock.patch.object(WKR, "_prune_job_history"))
        import scripts.instrument_pool as pool
        self.pool_file = self.data / "instrument_pool.json"
        self.pool_file.write_text("[]", encoding="utf-8")
        self._start(mock.patch.object(pool, "POOL_FILE", self.pool_file))
        self.save_instruments = self._start(mock.patch.object(pool, "save_instruments"))

    def test_the_lock_is_taken_and_the_pid_is_self_registered(self):
        """★ 抢到锁者自我登记 —— 这是"唯一确知我持锁"的实体。"""
        WKR.run()
        self.assertEqual(self.pid_file.read_text(encoding="utf-8"), str(os.getpid()))

    def test_the_pid_file_is_owner_only(self):
        WKR.run()
        self.assertEqual(self.pid_file.stat().st_mode & 0o777, 0o600)

    def test_a_contended_lock_exits_quietly(self):
        holder = self.lock_file.open("a", encoding="utf-8")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            WKR.run()
        finally:
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()
        self.assertIn("already running; exiting", self._log())
        self.store_cls.assert_not_called()

    def test_a_pid_write_failure_does_not_stop_the_worker(self):
        self._start(mock.patch.object(WKR.Path, "write_text", side_effect=OSError("只读")))
        WKR.run()
        self.assertIn("gateway worker started", self._log())

    def test_the_signal_handlers_are_registered(self):
        WKR.run()
        self.assertEqual([c[0][0] for c in self.signal.call_args_list],
                         [signal.SIGTERM, signal.SIGINT])
        self.assertTrue(all(c[0][1] is WKR.stop for c in self.signal.call_args_list))

    def test_the_store_is_recovered_before_the_loop(self):
        WKR.run()
        self.store.recover_processing.assert_called_once()
        self.store.recover_stale_job_runs.assert_called_once()

    def test_adopted_zombie_jobs_are_logged(self):
        self.store.recover_stale_job_runs.return_value = 3
        WKR.run()
        self.assertIn("收编僵尸 running 作业行 3 条", self._log())

    def test_no_zombies_means_no_adoption_log(self):
        WKR.run()
        self.assertNotIn("收编僵尸", self._log())

    def test_the_scheduler_baseline_is_initialised(self):
        WKR.run()
        self.scheduler.initialize_migration_baseline.assert_called_once()

    def test_the_scheduler_is_shut_down_on_exit(self):
        WKR.run()
        self.scheduler.shutdown.assert_called_once()

    def test_the_start_and_stop_lines_are_logged(self):
        WKR.run()
        text = self._log()
        self.assertIn("gateway worker started with scheduler ownership", text)
        self.assertIn("gateway worker stopped", text)

    def test_launched_jobs_are_logged(self):
        self.scheduler.tick.return_value = ["trader"]
        WKR.run()
        self.assertIn("scheduled job=trader", self._log())

    def test_a_missing_instrument_pool_is_seeded(self):
        self.pool_file.unlink()
        WKR.run()
        self.save_instruments.assert_called_once()
        self.assertIn("初始化生成出厂默认标的池", self._log())

    def test_an_existing_instrument_pool_is_left_alone(self):
        WKR.run()
        self.save_instruments.assert_not_called()

    def test_an_instrument_pool_failure_is_logged(self):
        self.pool_file.unlink()
        self.save_instruments.side_effect = RuntimeError("磁盘满了")
        WKR.run()
        self.assertIn("标的池初始化检查异常", self._log())

    def test_an_empty_queue_sleeps_one_second(self):
        WKR.run()
        self.store.claim_due.assert_called_with(1)
        self.assertIn("gateway worker stopped", self._log())

    def _delivery(self):
        return {"id": 5, "attempts": 1, "channel": "qq", "event_id": "e-1",
                "created_at": "2026-09-20T10:00:00", "title": "开仓",
                "message": "BTC 多单"}

    def test_a_successful_delivery_is_completed_and_logged(self):
        self.store.claim_due.side_effect = [[self._delivery()], []]
        result = mock.MagicMock()
        result.success = True
        result.status = "sent"
        result.detail = "ok"
        self.adapter.return_value.send.return_value = result
        WKR.run()
        self.store.complete.assert_called_once_with(5, "sent", "ok")
        self.assertIn("sent event=e-1 channel=qq detail=ok", self._log())

    def test_a_rejected_delivery_is_failed_and_logged(self):
        self.store.claim_due.side_effect = [[self._delivery()], []]
        result = mock.MagicMock()
        result.success = False
        result.status = "rejected"
        result.detail = "上游 400"
        self.adapter.return_value.send.return_value = result
        WKR.run()
        self.store.fail.assert_called_once_with(5, 1, "上游 400")
        self.assertIn("delivery failed event=e-1", self._log())

    def test_a_delivery_exception_is_failed_and_logged(self):
        self.store.claim_due.side_effect = [[self._delivery()], []]
        self.adapter.return_value.send.side_effect = RuntimeError("连接被拒")
        WKR.run()
        self.store.fail.assert_called_once_with(5, 1, "连接被拒")
        self.assertIn("delivery exception event=e-1", self._log())
        self.assertIn("type=RuntimeError", self._log())

    def test_the_delivery_leases_only_one_row_at_a_time(self):
        """★ tick 饥饿修复：每轮至多发 1 条，发完立刻回到循环顶。"""
        self.store.claim_due.side_effect = [[self._delivery()], []]
        result = mock.MagicMock()
        result.success = True
        result.status = "sent"
        result.detail = "ok"
        self.adapter.return_value.send.return_value = result
        WKR.run()
        self.assertTrue(all(c.args == (1,) for c in self.store.claim_due.call_args_list))

    def test_the_formatted_message_is_what_gets_sent(self):
        self.store.claim_due.side_effect = [[self._delivery()], []]
        result = mock.MagicMock()
        result.success = True
        result.status = "sent"
        result.detail = "ok"
        self.adapter.return_value.send.return_value = result
        WKR.run()
        sent = self.adapter.return_value.send.call_args[0][0]
        self.assertIn("【AstraQuant】开仓", sent)


class WorkerPruneIntervalTests(_WorkerBase):
    def test_the_prune_interval_is_six_hours(self):
        self.assertEqual(WKR.PRUNE_INTERVAL_SECONDS, 6 * 3600)

    def test_the_periodic_prune_fires_after_the_interval(self):
        store = mock.MagicMock()
        store.recover_stale_job_runs.return_value = 0
        store.prune_job_runs.return_value = {}
        store.claim_due.return_value = []
        self._start(mock.patch.object(WKR, "GatewayStore", return_value=store))
        scheduler = mock.MagicMock()
        scheduler.tick.return_value = []
        self._start(mock.patch.object(WKR, "GatewayScheduler", return_value=scheduler))
        _start = self._start
        import scripts.instrument_pool as pool
        pool_file = self.data / "instrument_pool.json"
        pool_file.write_text("[]", encoding="utf-8")
        _start(mock.patch.object(pool, "POOL_FILE", pool_file))
        _start(mock.patch.object(pool, "save_instruments"))
        real_prune = self._start(mock.patch.object(WKR, "_prune_job_history"))

        # 假时钟按"睡一觉时间就前进"建模，**不**依赖 `time.time()` 被调用的次数。
        # 旧版写的是"第 1 次调用返回 0、之后返回 10^9"，暗含调用次数固定不变；
        # 2026-09 给调度循环加上存活心跳（多了一次 time() 调用）后序号错位，
        # 清理判据随即失效 —— 那不是心跳写错了，是这个假设太脆。
        clock = {"t": 0.0}
        _start(mock.patch.object(WKR.time, "time", lambda: clock["t"]))

        def _sleep(_seconds):
            # 一跳越过 6 小时的清理间隔；等"启动一次 + 到点一次"都发生后再让循环退出。
            # 这样循环里再增删多少次 time() 调用，本用例都成立。
            clock["t"] = 10 ** 9
            if real_prune.call_count >= 2:
                WKR.RUNNING = False

        _start(mock.patch.object(WKR.time, "sleep", _sleep))
        WKR.run()
        self.assertEqual(real_prune.call_count, 2, "启动一次 + 到点一次")


if __name__ == "__main__":
    unittest.main()

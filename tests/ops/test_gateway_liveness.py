"""网关存活判据（`scripts/gateway_liveness.py`）—— 2026-09。

## 为什么这个判据值得一整套门

它决定「要不要**杀掉并重启一个正在管钱的进程**」。误判的代价是两个方向都贵：

- **该杀没杀**：调度循环卡死而容器照常 Up，后端也健康（看板能开），
  表现是"一切正常，就是不下单" —— 用户不会发现；
- **不该杀却杀了**：把健康的 worker 反复杀掉重启，交易周期被打断。

故四种状态（新鲜 / 陈旧 / 缺文件 / 损坏 / 无进程）逐一钉住，外加一个容易忽略的
边界：**时钟回拨**（NTP 校正）会让 age 变负数，此时必须判"新鲜"而不是去杀好人。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import gateway_liveness as gl  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
SCRIPT = str(ROOT / "scripts" / "gateway_liveness.py")

#: 保证匹配不到任何真实进程的"进程判据"
NO_SUCH = "-m definitely_not_a_real_module_9f3a2c"


class TestEvaluate:
    def test_fresh_heartbeat_is_healthy(self, tmp_path):
        hb = tmp_path / "hb"
        hb.write_text(str(int(time.time())), encoding="utf-8")
        code, reason = gl.evaluate(heartbeat=hb, timeout=90, fragment=NO_SUCH)
        # 无进程优先于心跳：先撞上"进程不存在"
        assert code == gl.EXIT_NO_PROCESS, reason

    def test_stale_heartbeat_is_reported_as_a_hang(self, tmp_path):
        hb = tmp_path / "hb"
        hb.write_text(str(int(time.time()) - 3600), encoding="utf-8")
        age = gl.heartbeat_age(hb)
        assert age is not None and age >= 3595, f"停更秒数算错：{age}"

    def test_missing_and_corrupt_files_are_distinguished_from_stale(self, tmp_path):
        """缺文件/坏文件必须与"陈旧"分开 —— 前者多半是**部署问题**（路径挂错、
        新版本还没写过），后者才是"循环卡死"。混在一起会让排障指向错误方向。"""
        missing = tmp_path / "nope"
        assert gl.heartbeat_age(missing) is None

        corrupt = tmp_path / "corrupt"
        for bad in ("", "   ", "not-a-number", "12.5", "-3"):
            corrupt.write_text(bad, encoding="utf-8")
            assert gl.heartbeat_age(corrupt) is None, f"{bad!r} 应判为损坏"

    def test_clock_skew_never_makes_a_healthy_worker_look_stale(self, tmp_path):
        """时钟回拨（NTP 校正、宿主休眠恢复）会让"心跳时间戳 > 现在" ⇒ age 为负。

        此时若按负数判定，就会把一个**完全健康**的 worker 判成卡死并杀掉 ——
        而且每轮都杀，形成"越修越坏"的循环。故负数一律夹到 0。
        """
        hb = tmp_path / "hb"
        hb.write_text(str(int(time.time()) + 600), encoding="utf-8")
        assert gl.heartbeat_age(hb) == 0, "未来时间戳必须按'刚刚'处理，而不是负年龄"

    def test_process_probe_finds_this_test_run(self):
        """进程判据认的是 cmdline 片段 —— 用本测试进程自己验证它真的能匹配到东西。"""
        pid = gl.find_worker_pid("pytest")
        assert pid is not None, "进程判据匹配不到任何东西时，会永远报'进程不存在'"


class TestCli:
    """退出码即语义 —— bash 侧只看退出码，故这层契约必须锁死。"""

    def _run(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([PY, SCRIPT, *extra], capture_output=True, text=True)

    def test_exit_codes_are_stable(self, tmp_path):
        """⚠️ 这里**不能**用"随便编一个 fragment 来制造无进程"的手法：
        命令行参数本身就在 /proc/<pid>/cmdline 里，编出来的那个哨兵会**匹配到自己**
        （本文件第一版就这么栽了，判据于是返回"健康"）。故"无进程"一态在进程内测
        （见 TestEvaluate），CLI 这层只覆盖有真实进程在跑的三态。
        """
        hb = tmp_path / "hb"
        # 进程判据用本测试进程自己能匹配到的片段
        live = ["--fragment", "pytest"]

        # 心跳缺失
        r = self._run("--heartbeat", str(tmp_path / "nope"), *live)
        assert r.returncode == gl.EXIT_NO_HEARTBEAT, r.stdout + r.stderr
        assert "缺失或损坏" in r.stdout

        # 心跳陈旧
        hb.write_text(str(int(time.time()) - 3600), encoding="utf-8")
        r = self._run("--heartbeat", str(hb), "--timeout", "90", *live)
        assert r.returncode == gl.EXIT_STALE, r.stdout + r.stderr
        assert "心跳停更" in r.stdout, "原因文案要能让排障者分清'进程死了'还是'循环卡死了'"

        # 健康
        hb.write_text(str(int(time.time())), encoding="utf-8")
        r = self._run("--heartbeat", str(hb), "--timeout", "90", *live)
        assert r.returncode == gl.EXIT_HEALTHY, r.stdout + r.stderr

    def test_no_process_state_is_covered_in_process(self, tmp_path):
        """无进程一态只在进程内可测（见上条注释），但它是真实存在的一态 —— 必须覆盖。"""
        hb = tmp_path / "hb"
        hb.write_text(str(int(time.time())), encoding="utf-8")
        code, reason = gl.evaluate(heartbeat=hb, timeout=90, fragment=NO_SUCH)
        assert code == gl.EXIT_NO_PROCESS, reason

    def test_reason_goes_to_stdout_for_the_watchdog_log(self, tmp_path):
        """看门狗把 stdout 原样抄进日志 —— 这是事后判因的唯一线索，不能只给退出码。"""
        hb = tmp_path / "hb"
        hb.write_text(str(int(time.time()) - 3600), encoding="utf-8")
        r = self._run("--heartbeat", str(hb), "--timeout", "90", "--fragment", "pytest")
        assert r.stdout.strip(), "stdout 为空 ⇒ 看门狗日志里只会看到'探测失败'，无从判因"


class TestWatchdogWiring:
    """看门狗的 gateway 模式必须真的调用本判据（否则判据写得再好也没人用）。"""

    def test_watchdog_gateway_mode_uses_this_module(self):
        src = (ROOT / "scripts" / "r20_watchdog.sh").read_text(encoding="utf-8")
        assert "gateway_liveness.py" in src, "看门狗不再调用存活判据"
        assert "--timeout" in src and "--heartbeat" in src, "调用时没把心跳路径/窗口传进去"

    def test_watchdog_does_not_pass_a_fragment_on_the_command_line(self):
        """生产路径必须用**默认** fragment：一旦把它当参数传，那条命令行就会出现在
        /proc 里，判据会匹配到自己 ⇒ 永远返回"进程存在"、卡死永不触发。
        （本文件第一版正是栽在这个自匹配上。）"""
        src = (ROOT / "scripts" / "r20_watchdog.sh").read_text(encoding="utf-8")
        call = src[src.index("gateway_liveness.py"):]
        call = call[:call.index("\n", call.index("--timeout"))]
        assert "--fragment" not in call, (
            "看门狗把 fragment 传到命令行上了 —— 那条命令行会自匹配，判据失效")

    def test_watchdog_locks_are_per_mode(self):
        """两个容器共用同一个 `./data` 挂载 ⇒ 锁必须按模式分开。

        共用一把锁时，第二个容器里的看门狗一启动就 "already running" 退出 ——
        那台就彻底没人看护了（正是本次要修的那类"静默失效"）。
        """
        src = (ROOT / "scripts" / "r20_watchdog.sh").read_text(encoding="utf-8")
        assert ".r20_watchdog.gateway.lock" in src, "网关模式没有独立锁文件"

    def test_shutdown_only_kills_what_this_mode_manages(self):
        """停止信号只该收掉**本模式在管**的进程。

        旧版不分模式地把后端与 worker 一起杀：Docker 里两模式各占一个容器、看不见彼此，
        问题被掩盖；但同机同时跑后端 + 一个网关模式看门狗时，停后者会顺手杀掉共享的后端
        （本地验证时真实发生过：后端被杀、约一分钟才被生产看门狗拉起）。
        """
        src = (ROOT / "scripts" / "r20_watchdog.sh").read_text(encoding="utf-8")
        body = src[src.index("shutdown() {"):]
        body = body[:body.index("\n}")]
        assert 'if [ "$MODE" = "gateway" ]' in body, (
            "shutdown 没按模式分支 —— 网关模式会误杀后端")
        gateway_branch = body[body.index('if [ "$MODE" = "gateway" ]'):body.index("else")]
        assert "find_worker_pid" in gateway_branch, "网关模式应只收 worker"
        assert "find_backend_pid" not in gateway_branch, "网关模式不得碰后端"

    def test_watchdog_forwards_stop_signals(self):
        """容器里它可能是 PID 1，而内核会让 PID 1 忽略未装处理函数的 SIGTERM ⇒
        不装 trap 则 `docker stop` 会等满 10 秒超时才 SIGKILL，受管进程拿不到优雅退出。"""
        src = (ROOT / "scripts" / "r20_watchdog.sh").read_text(encoding="utf-8")
        assert "trap shutdown TERM INT" in src, "看门狗没装停止信号处理"

    def test_watchdog_does_not_block_signals_behind_one_long_sleep(self):
        """bash 只在前台命令结束后才执行 trap ⇒ 一整段 `sleep 30` 会把 SIGTERM
        最多推迟 30 秒，超过 Docker 的 10 秒宽限。必须拆短。"""
        src = (ROOT / "scripts" / "r20_watchdog.sh").read_text(encoding="utf-8")
        assert "for _ in 1 2 3 4 5 6; do sleep 5; done" in src, "长 sleep 会拖住停止信号"
        assert "    sleep 30\n" not in src, "还留着整段 sleep 30"


class TestHeartbeatWriter:
    """心跳写入端（`r20_gateway/worker.py`）的两条硬要求。"""

    def test_worker_writes_heartbeat_every_loop_and_right_after_taking_the_lock(self):
        src = (ROOT / "r20_gateway" / "worker.py").read_text(encoding="utf-8")
        assert "write_heartbeat()" in src
        loop = src[src.index("while RUNNING:"):]
        assert "write_heartbeat()" in loop, "循环里没写心跳 ⇒ 判据永远判陈旧"
        head = src[:src.index("while RUNNING:")]
        assert "write_heartbeat()" in head, (
            "抢到锁后没立刻写一次心跳 —— 看护进程可能读到上一代的旧时间戳，"
            "把刚起来的 worker 误判为卡死而杀掉")

    def test_heartbeat_write_failure_never_breaks_scheduling(self, tmp_path):
        """心跳写不进去（磁盘满/权限）远不如"调度因此停摆"严重 ⇒ 必须吞掉异常。"""
        from r20_gateway import worker as w

        # 指向一个不可能写入的路径：父级是文件
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        assert w.write_heartbeat(blocker / "sub" / "hb") is False, "写入失败必须返回 False 而非抛异常"

    def test_heartbeat_round_trips_through_the_probe(self, tmp_path):
        """写入端与判据端必须能对上（两处各写一份路径常量是漂移温床）。"""
        from r20_gateway import worker as w

        hb = tmp_path / "hb"
        assert w.write_heartbeat(hb) is True
        age = gl.heartbeat_age(hb)
        assert age is not None and age <= 2, f"刚写完的心跳被读成 {age} 秒前"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

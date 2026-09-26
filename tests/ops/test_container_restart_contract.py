"""容器部署的**重启安全契约**（2026-09）。

## 这个门在防什么

对分发的产品来说，"用户 `docker restart` 或宿主机重启一次，程序就再也起不来/静默不下单"
是最伤留存的一类故障 —— 而且它**不会**在本机开发时暴露（本机从来是手工起进程）。

本文件把已经逐条核对过的性质钉住，任何一条被改动都会红：

1. **两个服务都必须有重启策略** —— 没有 `restart:` 则容器退出后不会自己回来；
2. **`.env` 被挂成目录时必须拒绝启动** —— Docker 的经典陷阱：`./.env` 在宿主机不存在时
   `compose up` 会创建同名**目录**。旧行为只打一行 warning 就继续，结果是"跑成半个程序"：
   看板永远未就绪、后台保存配置写进目录里永远存不上。起不来至少有 `logs` 可看，
   静默半死没有；
3. **网关必须有健康探测，且探针不得自匹配** —— 网关是本拓扑最危险的单点：后端容器
   照常绿着，调度 worker 却可能已死（看板一切正常、就是不下单）。
   而 `CMD-SHELL` 若用裸字面量做判据，探针**自己的命令行**就含该字符串、永远判"健康"
   （本仓当天刚踩过同类自匹配）；
4. **调度所有权必须以 flock 为真相、而非 PID 文件** —— 容器重启后 /proc 里 PID 会重用，
   若拿 PID 文件当真相，可能误收养一个陌生进程而永不启动调度。flock 与持锁进程同生共死，
   内核自动释放，是唯一可靠判据；
5. **状态目录必须挂载** —— 否则重启即丢账本/配置。

## 不在本门范围（已记录、尚未解决）

"活着但卡死"的进程，Docker **不会**自动重启（健康检查只影响 `docker ps` 的显示，
`restart:` 策略只对"退出"生效）。要真正自愈需配合 autoheal 一类工具，
或由应用侧写一个与交易开关无关的存活心跳 —— 后者是应用层改动，未在本轮做。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.yml"
ENTRYPOINT = ROOT / "deploy" / "docker-entrypoint.sh"
SUPERVISOR = ROOT / "r20_gateway" / "supervisor.py"


def _service_block(name: str) -> str:
    """按缩进切出某个 service 的配置块（不引 yaml 依赖，纯文本足够）。"""
    text = COMPOSE.read_text(encoding="utf-8")
    m = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z]|\Z)", text, re.M | re.S)
    assert m, f"docker-compose.yml 里找不到服务 {name}"
    return m.group(1)


class RestartPolicyTest(unittest.TestCase):
    def test_both_services_come_back_after_a_restart(self):
        """没有 restart 策略 ⇒ 用户 docker 重启后容器不会自己回来。"""
        for name in ("backend", "gateway"):
            block = _service_block(name)
            policy = re.search(r"^\s+restart:\s*(\S+)", block, re.M)
            assert policy, f"{name} 服务没有 restart 策略，重启后不会自动拉起"
            assert policy.group(1) in ("unless-stopped", "always"), (
                f"{name} 的 restart={policy.group(1)} 不足以在重启后自动恢复")

    def test_state_directories_are_mounted(self):
        """状态目录没挂出来 ⇒ 重启即丢账本与配置。"""
        for name in ("backend", "gateway"):
            block = _service_block(name)
            for vol in ("./data:/app/data", "./.env:/app/.env", "./logs:/app/logs"):
                assert vol in block, f"{name} 服务缺少挂载 {vol}"


class HealthProbeTest(unittest.TestCase):
    def test_gateway_has_a_health_probe(self):
        """网关此前**没有**任何健康探测 —— 它死了没人知道（看板照常绿）。"""
        block = _service_block("gateway")
        assert "healthcheck:" in block, "网关服务缺少 healthcheck：worker 死掉将无人察觉"

    def test_gateway_probe_cannot_match_itself(self):
        """探针若含裸字面量，`/bin/sh -c` 自己的命令行会自匹配 ⇒ 永远判健康、探针失效。"""
        block = _service_block("gateway")
        # 整行匹配：不能用 `\[.*?\]` —— 探针里 `r20_gateway[.]worker` 那个 `[.]`
        # 会让非贪婪匹配提前收尾（本门第一版就栽在这里，把判据截成了一小段）。
        probe = re.search(r"^\s*test:.*$", block, re.M)
        assert probe, "找不到网关健康探测的 test 命令"
        cmd = probe.group(0)
        # 进程判据必须以 `[.]` 形态出现（正则与被匹配字面量不同形）
        assert "r20_gateway[.]worker" in cmd, (
            "网关探针的进程判据必须写成 r20_gateway[.]worker，否则会匹配到探针自身")
        assert "r20_gateway.worker /proc" not in cmd, (
            "探针里出现了裸的 r20_gateway.worker —— 它会匹配 /bin/sh -c 自己的命令行，"
            "导致探针永远返回健康")

    def test_backend_health_probe_targets_the_health_endpoint(self):
        block = _service_block("backend")
        assert "/api/v1/health" in block, "后端健康探测必须打 /api/v1/health"


class EntrypointTest(unittest.TestCase):
    def test_env_as_directory_fails_closed(self):
        """`.env` 是目录时必须**拒绝启动**（旧行为只 warning 然后跑成半个程序）。"""
        text = ENTRYPOINT.read_text(encoding="utf-8")
        assert "-d \"$ROOT_DIR/.env\"" in text, "入口脚本不再检测 .env 被挂成目录的情况"
        branch = text[text.index('-d "$ROOT_DIR/.env"'):]
        branch = branch[:branch.index("elif")]
        assert "exit 1" in branch, (
            ".env 为目录的分支必须 exit 1 —— 静默跑成'未就绪 + 存不上配置'比直接不起来糟得多")
        assert "mounted as a directory" not in text, "旧的'只警告'文案还在，说明分支未改彻底"

    def test_fix_instructions_are_actionable(self):
        """光报错没用：必须给出**逐字可复制**的修复命令。"""
        text = ENTRYPOINT.read_text(encoding="utf-8")
        branch = text[text.index('-d "$ROOT_DIR/.env"'):]
        branch = branch[:branch.index("elif")]
        for needle in ("rm -rf .env", "cp env.example .env", "docker compose up -d"):
            assert needle in branch, f"修复指引缺少 `{needle}`"

    def test_both_modes_are_delegated_to_the_watchdog(self):
        """Docker 的 `restart:` 只对**退出**生效；"进程还在但卡死"它不会重启。

        故两种模式都必须交给容器内的看门狗托管（后端按 /api/v1/health 探测，
        网关按存活心跳判定），把"卡死"一并覆盖。
        """
        text = ENTRYPOINT.read_text(encoding="utf-8")
        assert 'exec bash "$ROOT_DIR/scripts/r20_watchdog.sh"' in text, "后端模式没交给看门狗"
        assert 'exec bash "$ROOT_DIR/scripts/r20_watchdog.sh" gateway' in text, "网关模式没交给看门狗"

    def test_supervision_has_a_fallback(self):
        """看门狗脚本缺失时不能把容器搞成起不来 —— 必须有直起进程的兜底。"""
        text = ENTRYPOINT.read_text(encoding="utf-8")
        assert "uvicorn r20_backend.app:app" in text
        assert "r20_gateway.worker" in text
        # exec 而非裸调用：容器 PID 1 必须是被 exec 的进程，否则信号传递不到
        assert "exec python3 -m uvicorn" in text, "后端缺少直起兜底"
        assert "exec python3 -m r20_gateway.worker" in text, "网关缺少直起兜底"


class SchedulerOwnershipTest(unittest.TestCase):
    def test_flock_is_the_source_of_truth_not_the_pid_file(self):
        """容器重启后 /proc 里 PID 会重用 —— 拿 PID 文件当真相会误收养陌生进程。

        flock 与持锁进程同生共死（内核在进程消亡时释放），故它是唯一可靠判据：
        锁空闲 ⇒ 前代确已消亡 ⇒ 放心 spawn。
        """
        src = SUPERVISOR.read_text(encoding="utf-8")
        assert "def _lock_held()" in src, "调度所有权判据不见了"
        assert "fcntl.flock" in src, "所有权判据不再是 flock —— 重启后可能误判"
        body = src[src.index("def ensure_worker("):]
        body = body[:body.index("def _run(")]
        assert "_lock_held()" in body, "ensure_worker 不再用 flock 判活，可能凭 PID 文件误收养"
        # PID 文件只能当缓存提示：读出来后必须用 cmdline 校验它确实是 worker
        assert "_is_gateway_worker" in src, "PID 文件读出后缺少 cmdline 校验"


class OrderProtectionAtomicityTest(unittest.TestCase):
    def test_entry_orders_carry_protection_in_the_same_request(self):
        """重启可能落在"下完进场单、还没挂止损"之间 ⇒ 进场单必须**原子**带上保护腿。

        `attachAlgoOrds` 与进场单在同一个请求里，交易所侧一并受理；
        否则中途被杀会留下**无保护的裸仓** —— 对交易系统这是最贵的那个故障。
        """
        src = (ROOT / "scripts" / "okx_rest.py").read_text(encoding="utf-8")
        body = src[src.index("def place_order("):]
        body = body[:body.index("\ndef ", 10)]
        assert "attachAlgoOrds" in body, (
            "place_order 不再把止盈止损腿随进场单一起提交 —— 周期中途被杀会留裸仓")


if __name__ == "__main__":
    unittest.main()

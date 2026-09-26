"""网关存活判据 —— 供看门狗的 `gateway` 模式调用（2026-09）。

## 为什么单独成一个模块

容器部署下最危险的故障是「**进程还在，调度循环不转了**」：网关容器照常 Up，
后端容器也健康（看板能开、数据能看），但再也不会下单 —— 用户不会发现。
Docker 自己的健康检查看不出这种死法（只影响 `docker ps` 的显示，且 `restart:`
策略只对"退出"生效），所以必须由容器内的看门狗来判定并拉起。

判据之所以值得单独一个文件、而不是在 bash 里写几行 `test`：

1. **可测**。存活判据是"要不要杀掉并重启一个正在管钱的进程"的依据，误判的代价是
   交易中断。它需要能对着"新鲜心跳 / 陈旧心跳 / 缺文件 / 无进程"四种状态逐一验证，
   而这在 bash 里只能靠起进程、看日志间接测；
2. **退出码即语义**，bash 侧只做 `case $?`，判据本身不进 shell。

## 判据为什么是"循环心跳"而不是"周期产物够不够新"

用户**可以合法关停交易**，那时账本/情绪/决策这些周期产物本就不更新 —— 拿它们当
存活判据会造成**误杀循环**（把好好的 worker 反复杀掉重启）。心跳只反映
"调度循环还在转"，与交易开关无关（见 `r20_gateway/worker.py::write_heartbeat`）。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 健康（进程在，且心跳在容忍窗口内） |
| 1 | 进程不存在 |
| 2 | 心跳停更（循环卡死）—— stdout 给出已停更秒数 |
| 3 | 心跳文件缺失或损坏 |

用法::

    python3 scripts/gateway_liveness.py                 # 默认路径与 90s 窗口
    python3 scripts/gateway_liveness.py --timeout 120 --heartbeat /app/data/.r20_gateway_heartbeat
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 默认心跳文件（与 `r20_gateway/worker.py::HEARTBEAT_FILE` 同址）。
DEFAULT_HEARTBEAT = ROOT / "data" / ".r20_gateway_heartbeat"

#: 进程判据：worker 的命令行里必含这段（`python3 -m r20_gateway.worker`）。
WORKER_CMD_FRAGMENT = "-m r20_gateway.worker"

#: 心跳容忍窗口（秒）。worker 主循环空闲时约 1 秒一轮，`scheduler.tick()` 把作业提交
#: 线程池后立即返回（交易子进程另有 `spec.timeout_seconds` 兜底），故 90s 停更即为真卡死。
DEFAULT_TIMEOUT_SECONDS = 90

EXIT_HEALTHY = 0
EXIT_NO_PROCESS = 1
EXIT_STALE = 2
EXIT_NO_HEARTBEAT = 3


def find_worker_pid(fragment: str = WORKER_CMD_FRAGMENT) -> int | None:
    """扫 /proc 找出命令含 `fragment` 的进程（不看 PID 文件 —— 那个只是缓存提示）。"""
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if fragment in cmdline:
            return int(entry.name)
    return None


def heartbeat_age(path: Path) -> int | None:
    """心跳已停更多少秒；文件缺失/空/非数字时返回 None。"""
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.isdigit():
        return None
    # 时钟回拨（NTP 校正）时 age 会是负数 —— 视为新鲜，不要因此去杀一个健康的 worker
    return max(0, int(time.time()) - int(raw))


def evaluate(*, heartbeat: Path = DEFAULT_HEARTBEAT, timeout: int = DEFAULT_TIMEOUT_SECONDS,
             fragment: str = WORKER_CMD_FRAGMENT) -> tuple[int, str]:
    """返回 (退出码, 人类可读原因)。"""
    pid = find_worker_pid(fragment)
    if pid is None:
        return EXIT_NO_PROCESS, "进程不存在"
    age = heartbeat_age(heartbeat)
    if age is None:
        return EXIT_NO_HEARTBEAT, f"心跳文件缺失或损坏（{heartbeat}）"
    if age > timeout:
        return EXIT_STALE, f"心跳停更（循环卡死，已 {age} 秒未更新，窗口 {timeout}s）"
    return EXIT_HEALTHY, f"健康（PID={pid}，心跳 {age}s 前）"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R20 网关存活判据（退出码即语义）")
    parser.add_argument("--heartbeat", default=str(DEFAULT_HEARTBEAT))
    parser.add_argument("--timeout", type=int,
                        default=int(os.getenv("R20_GATEWAY_LIVENESS_TIMEOUT_SECONDS",
                                              DEFAULT_TIMEOUT_SECONDS)))
    parser.add_argument("--fragment", default=WORKER_CMD_FRAGMENT)
    args = parser.parse_args(argv)

    code, reason = evaluate(heartbeat=Path(args.heartbeat), timeout=args.timeout,
                            fragment=args.fragment)
    # 原因走 stdout：由看门狗原样抄进日志，便于事后判"是进程死了还是循环卡死了"
    print(reason)
    return code


if __name__ == "__main__":
    sys.exit(main())

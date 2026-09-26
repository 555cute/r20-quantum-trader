"""Single-owner R20 Gateway delivery worker."""
from __future__ import annotations
import fcntl
import os
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from r20_gateway.channels import NotificationChannelAdapter
from r20_gateway.publisher import DB_PATH
from r20_gateway.scheduler import GatewayScheduler
from r20_gateway.store import GatewayStore

ROOT = Path(__file__).resolve().parents[1]
from r20_gateway.pidfile import PID_FILE

LOCK_FILE = ROOT / "data" / ".r20_gateway.lock"

#: 存活心跳文件（2026-09）。每轮调度循环写一次当前时间戳。
#:
#: ## 为什么需要它
#:
#: 容器部署下最危险的单点故障是"**进程还在、循环不转了**"：网关容器照常 Up，
#: 后端容器也健康（看板能开、数据能看），但调度已死 —— 表现就是"一切正常，就是不下单"，
#: 用户不会发现。而 Docker 的健康检查只能看进程是否存在，看不出循环死没死。
#:
#: ## 为什么判据是"循环心跳"而不是"周期产物够不够新"
#:
#: 用户**可以合法关停交易**，那时周期产物（账本/情绪/决策）本就不更新，拿它当存活
#: 判据会造成**误杀循环**。心跳只反映"调度循环还在转"，与交易开关无关。
#:
#: ## 为什么阈值可以很小
#:
#: `scheduler.tick()` 把作业提交到线程池后**立即返回**（`subprocess.run` 另有
#: `spec.timeout_seconds` 兜底），故循环每轮耗时在秒级、空闲时约 1 秒一轮。
#: 心跳一旦停更，就是循环真的卡住了 —— 90 秒阈值足够宽松也不会误报。
HEARTBEAT_FILE = ROOT / "data" / ".r20_gateway_heartbeat"

#: 心跳写入失败只报第一次，避免磁盘故障时每秒钟刷一条日志。
_heartbeat_failed_once = False


def write_heartbeat(path: Path | None = None) -> bool:
    """把当前时间戳写进存活心跳文件，返回是否写成功。

    ⚠️ 心跳**绝不能拖垮调度**：任何写入异常都被吞掉（只记一次日志）——
    "心跳写不进去"远不如"调度因此停摆"严重。
    """
    global _heartbeat_failed_once
    target = path or HEARTBEAT_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(int(time.time())), encoding="utf-8")
        return True
    except OSError as exc:
        if not _heartbeat_failed_once:
            _heartbeat_failed_once = True
            log(f"存活心跳写入失败（不影响调度，仅外部看护失效）: {type(exc).__name__}: {exc}")
        return False
LOG_FILE = ROOT / "logs" / "r20_gateway.log"
BJ_TZ = timezone(timedelta(hours=8))
RUNNING = True


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


def stop(*_: object) -> None:
    global RUNNING
    RUNNING = False



#: 作业历史清理周期（worker 启动时清一次，之后每 6 小时一次）。
PRUNE_INTERVAL_SECONDS = 6 * 3600


def _prune_job_history(store: GatewayStore) -> None:
    """清理过期作业历史（可观测性维护）——**失败绝不能拖垮调度循环**。

    该表按分钟级增长（`factor_library` 每分钟一条）：没有任何清理机制时
    15 天即 2.2 万行 / 23M（实测）。保留窗口见 `GatewayStore.prune_job_runs`。
    """
    try:
        result = store.prune_job_runs()
    except Exception as exc:
        log(f"job_runs 清理失败（不影响调度）: {type(exc).__name__}: {exc}")
        return
    if result.get("deleted"):
        log(f"job_runs 清理：删除 {result['deleted']} 条"
            f"（保留 {result['keep_days']} 天，VACUUM={'是' if result['vacuumed'] else '否'}）")


def format_message(row: dict[str, object]) -> str:
    created = str(row.get("created_at", ""))
    # Format cleaner timestamp if ISO format
    if "T" in created:
        created = created.replace("T", " ")[:19]
    title = str(row.get("title", "")).strip()
    body = str(row.get("message", "")).strip()
    if title.startswith("【R20") or "【R20" in title:
        header = title
    else:
        header = f"【R20 Quantum】{title}"
    return f"{header}\n⏱️ 时间：{created}\n━━━━━━━━━━━━━━\n{body}"


def run() -> None:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_FILE.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("gateway worker already running; exiting")
        return
    # 审计风暴修复：抢到锁者自我登记为权威 PID（唯一确知「我持锁」的实体）。
    # supervisor 旧实现对注定秒退的子进程盲写 PID 文件 → 文件长期指向死 pid，
    # 活体持锁者反而不可见，每 10s 重生一次（logs/r20_gateway.log 948 条）。
    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        os.chmod(PID_FILE, 0o600)
    except OSError:
        pass
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    store = GatewayStore(DB_PATH)
    store.recover_processing()
    _adopted = store.recover_stale_job_runs()
    if _adopted:
        log(f"收编僵尸 running 作业行 {_adopted} 条（→ interrupted）")
    scheduler = GatewayScheduler(store)
    scheduler.initialize_migration_baseline()
    try:
        from scripts.instrument_pool import POOL_FILE, save_instruments, DEFAULT_INSTRUMENTS
        if not POOL_FILE.exists():
            save_instruments(DEFAULT_INSTRUMENTS)
            log("初始化生成出厂默认标的池 data/instrument_pool.json")
    except Exception as _init_exc:
        log(f"标的池初始化检查异常: {_init_exc}")
    log("gateway worker started with scheduler ownership")
    # 抢到锁后**立刻**写一次心跳：否则看护进程可能在重启瞬间读到上一代留下的旧时间戳，
    # 误判"刚起来的这个 worker 已经卡死"而把它杀掉（互相打架的经典形状）。
    write_heartbeat()
    _prune_job_history(store)                       # 启动清一次
    _next_prune_at = time.time() + PRUNE_INTERVAL_SECONDS
    while RUNNING:
        # 存活心跳：只要循环还在转就保持新鲜，供容器看护判定"循环是否卡死"。
        write_heartbeat()
        # 审计#4(2026-09-13)·tick 饥饿修复：旧循环一次批量领 20 条投递并**串行**
        # HTTP 发送（webhook 卡死时每发可达超时秒级），期间 scheduler.tick() 无人喂——
        # 有积压时交易周期任务可被投递重试饿死数分钟。现每轮至多发 1 条，发完立即
        # 回到循环顶 tick；积压只影响投递速率，不再波及排程。配合 claim_due 租约
        # 老化（#11），崩溃遗留 processing 行过 120s 也可被本循环重领。
        launched = scheduler.tick()
        for job_name in launched:
            log(f"scheduled job={job_name}")
        if time.time() >= _next_prune_at:           # 之后每 6 小时一次
            _next_prune_at = time.time() + PRUNE_INTERVAL_SECONDS
            _prune_job_history(store)
        deliveries = store.claim_due(1)
        if not deliveries:
            time.sleep(1)
            continue
        delivery = deliveries[0]
        try:
            result = NotificationChannelAdapter(str(delivery["channel"])).send(format_message(delivery))
            if result.success:
                store.complete(int(delivery["id"]), result.status, result.detail)
                log(f"{result.status} event={delivery['event_id']} channel={delivery['channel']} detail={result.detail}")
            else:
                store.fail(int(delivery["id"]), int(delivery["attempts"]), result.detail)
                log(f"delivery failed event={delivery['event_id']} channel={delivery['channel']} detail={result.detail}")
        except Exception as exc:
            store.fail(int(delivery["id"]), int(delivery["attempts"]), str(exc))
            log(f"delivery exception event={delivery['event_id']} channel={delivery['channel']} type={type(exc).__name__}")
    scheduler.shutdown()
    log("gateway worker stopped")


if __name__ == "__main__":
    run()

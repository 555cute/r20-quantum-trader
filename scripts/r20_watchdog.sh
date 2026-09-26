#!/bin/bash
# ============================================================
# R20 看门狗（两种模式）
#
#   scripts/r20_watchdog.sh            # 后端模式（默认，行为不变）
#       健康探测 r20_backend 的 /api/v1/health，死亡/卡死自动拉起；
#       清理孤儿 gateway worker，让新后端重新取得调度所有权。
#
#   scripts/r20_watchdog.sh gateway    # 网关模式（2026-09 新增）
#       看护调度 worker：判据是 worker 每轮循环写的**存活心跳**是否新鲜。
#       动机：容器部署下最危险的故障是"进程还在、循环不转"—— 网关容器照常 Up、
#       后端也健康（看板能开），但调度已死，表现是"一切正常，就是不下单"。
#       Docker 健康检查看不出这种死法（只影响 `docker ps` 显示，且 restart 策略
#       只对"退出"生效），故由容器内的本脚本负责真正拉起。
#
# 部署：容器内常驻(setsid)；容器重启后由 supervisord [program:r20-watchdog] 拉起。
# 暂停：touch data/.r20_watchdog.pause   恢复：rm 该文件
# ============================================================
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-backend}"
# ⚠️ 锁必须**按模式分开**：Docker 部署下 backend 与 gateway 是两个容器，但都挂载同一个
# `./data` ⇒ 同一个锁文件在容器之间也互斥（同内核、同 inode）。共用一把锁会导致
# 第二个容器里的看门狗一启动就 "already running" 直接退出 —— 那台就彻底没人看护了。
case "$MODE" in
    gateway) LOCK="$ROOT/data/.r20_watchdog.gateway.lock" ;;
    *)       LOCK="$ROOT/data/.r20_watchdog.lock" ;;
esac
LOG="$ROOT/logs/r20_watchdog.log"
PAUSE="$ROOT/data/.r20_watchdog.pause"
HEALTH_URL="http://127.0.0.1:8080/api/v1/health"

# Python 解释器自适应：专用 venv 优先，回退系统 python3
PY="$ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY="/app/venv/bin/python3"
[ -x "$PY" ] || PY="python3"

# 单实例：已有看门狗在跑则直接退出
exec 9>"$LOCK"
flock -n 9 || { echo "watchdog already running"; exit 0; }

log() { echo "[$(TZ=Asia/Shanghai date '+%F %T +08:00')] $*" >> "$LOG"; }

find_backend_pid() {
    local p cmd
    for p in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
        cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)
        case "$cmd" in *"m uvicorn r20_backend.app:app"*) echo "$p"; return;; esac
    done
}

kill_stale() {  # 杀掉卡死但不再应答的旧后端与孤儿 worker
    local p pid="$1"
    [ -n "$pid" ] && kill "$pid" 2>/dev/null && log "已终止旧后端 PID=$pid"
    sleep 2
    kill "$pid" 2>/dev/null
    for p in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
        cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)
        case "$cmd" in *"m r20_gateway.worker"*)
            # 后端已死/未建立父子关系，孤儿 worker 必须让出调度锁
            kill "$p" 2>/dev/null && log "已清理孤儿 worker PID=$p"
        ;; esac
    done
    sleep 1
}

restart_backend() {
    kill_stale "$(find_backend_pid)"
    cd "$ROOT" || return 1
    setsid "$PY" -m uvicorn r20_backend.app:app --host 0.0.0.0 --port 8080 \
        < /dev/null >> "$ROOT/logs/r20_backend.log" 2>&1 &
    sleep 5
    local np; np="$(find_backend_pid)"
    if [ -n "$np" ]; then
        echo "$np" > "$ROOT/data/r20_backend.pid"
        log "✅ 后端已拉起 PID=$np"
    else
        log "❌ 拉起失败，30 秒后重试"
    fi
}

# ---------------------------------------------------------------
# 网关模式：看护 r20_gateway.worker
# ---------------------------------------------------------------
HEARTBEAT="$ROOT/data/.r20_gateway_heartbeat"
# 心跳超时阈值。默认 90s：worker 主循环空闲时约 1 秒一轮，`scheduler.tick()` 把作业
# 提交线程池后立即返回（子进程另有 spec.timeout_seconds 兜底），故 90s 停更即为真卡死。
LIVENESS_TIMEOUT="${R20_GATEWAY_LIVENESS_TIMEOUT_SECONDS:-90}"

find_worker_pid() {
    local p cmd
    for p in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
        cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)
        case "$cmd" in *"m r20_gateway.worker"*) echo "$p"; return;; esac
    done
}

# 判据本体在 scripts/gateway_liveness.py（退出码即语义，且可对着四种状态逐一测），
# bash 侧只取退出码并把原因原样抄进日志 —— 事后能分清"进程死了"还是"循环卡死了"。
GATEWAY_REASON=""
gateway_health() {
    GATEWAY_REASON="$("$PY" "$ROOT/scripts/gateway_liveness.py" \
        --heartbeat "$HEARTBEAT" --timeout "$LIVENESS_TIMEOUT" 2>&1)"
    return $?
}

restart_gateway() {
    local pid
    pid="$(find_worker_pid)"
    if [ -n "$pid" ]; then
        # 卡死时 SIGTERM 常常无效（连信号处理线程都卡住了），故补 SIGKILL；
        # flock 由内核在进程消亡时释放，新 worker 必能接管，不会出现"锁被死人占着"。
        kill "$pid" 2>/dev/null
        sleep 2
        kill -9 "$pid" 2>/dev/null
        log "已终止卡死的网关 worker PID=$pid"
    fi
    cd "$ROOT" || return 1
    setsid "$PY" -m r20_gateway.worker < /dev/null >> "$ROOT/logs/r20_gateway.log" 2>&1 &
    sleep 5
    local np; np="$(find_worker_pid)"
    if [ -n "$np" ]; then
        log "✅ 网关 worker 已拉起 PID=$np"
    else
        log "❌ 网关 worker 拉起失败，30 秒后重试"
    fi
}

# ⚠️ 容器里本脚本可能是 PID 1，而**内核会让 PID 1 忽略未装处理函数的 SIGTERM** ——
# 不装 trap 的话 `docker stop` 会一直等满 10 秒超时才被 SIGKILL，受管进程拿不到优雅退出。
# 故显式转发停止信号后自退（宿主机部署下同样受益）。
# ⚠️ **只终止本模式真正在管的东西**（2026-09 实测踩到）：旧版不分模式地把
# 后端与 worker 一起杀。在 Docker 里两种模式各占一个容器、互不可见，看不出问题；
# 但同一台机器上同时跑着后端与一个网关模式看门狗时，停掉后者会**顺手杀掉共享的后端**
# —— 这正是我本地验证时造成的真事故（后端被杀、约一分钟才被生产看门狗拉起）。
shutdown() {
    log "收到停止信号，正在终止受管进程…"
    local pid
    if [ "$MODE" = "gateway" ]; then
        pid="$(find_worker_pid)"; [ -n "$pid" ] && kill "$pid" 2>/dev/null
    else
        # 后端模式在本机同时看护 worker（看门狗头注释里的职责之一），故两个都收。
        pid="$(find_backend_pid)"; [ -n "$pid" ] && kill "$pid" 2>/dev/null
        pid="$(find_worker_pid)";  [ -n "$pid" ] && kill "$pid" 2>/dev/null
    fi
    sleep 1
    exit 0
}
trap shutdown TERM INT

log "看门狗启动 (模式=$MODE, PY=$PY, 探测间隔 30s, 连续 2 次失败触发拉起)"
FAILS=0
while true; do
    if [ -f "$PAUSE" ]; then
        FAILS=0
    elif [ "$MODE" = "gateway" ]; then
        gateway_health; rc=$?
        if [ "$rc" -eq 0 ]; then
            FAILS=0
        else
            FAILS=$((FAILS + 1))
            log "网关健康探测失败 ($FAILS/2)：$GATEWAY_REASON"
            if [ "$FAILS" -ge 2 ]; then
                restart_gateway
                FAILS=0
            fi
        fi
    elif curl -sf -m 5 "$HEALTH_URL" > /dev/null 2>&1; then
        FAILS=0
    else
        FAILS=$((FAILS + 1))
        log "健康探测失败 ($FAILS/2)"
        if [ "$FAILS" -ge 2 ]; then
            restart_backend
            FAILS=0
        fi
    fi
    # ⚠️ 拆成 6×5s 而不是一个 `sleep 30`：bash 只会在**当前前台命令结束**后才执行 trap，
    # 一整段 sleep 30 会让 SIGTERM 最多被推迟 30 秒 —— 而 Docker 的 `docker stop`
    # 只等 10 秒就 SIGKILL，届时受管进程拿不到优雅退出。拆短后最坏延迟 5 秒。
    for _ in 1 2 3 4 5 6; do sleep 5; done
done

#!/bin/bash
# ============================================================
# R20 展示站 8080 看门狗
# 职责：健康探测 r20_backend，死亡自动拉起；清理孤儿 gateway worker
#       让新后端重新取得调度所有权；维护期可暂停。
# 部署：容器内常驻(setsid)；容器重启后由 supervisord [program:r20-watchdog] 拉起。
# 暂停：touch data/.r20_watchdog.pause   恢复：rm 该文件
# ============================================================
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK="$ROOT/data/.r20_watchdog.lock"
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

log "看门狗启动 (PY=$PY, 探测间隔 30s, 连续 2 次失败触发拉起)"
FAILS=0
while true; do
    if [ -f "$PAUSE" ]; then
        FAILS=0
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
    sleep 30
done

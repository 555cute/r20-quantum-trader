#!/usr/bin/env bash
set -e

# ==============================================================================
# AstraQuant - Container Entrypoint
# ==============================================================================

ROOT_DIR="/app"
cd "$ROOT_DIR"

# 1. 确保必要运行时目录存在
mkdir -p "$ROOT_DIR/data" "$ROOT_DIR/logs" "$ROOT_DIR/backups"

# 2. 如果缺少 .env，从 env.example 自动生成一份最小兜底（提醒用户尽快配置）
#
# ⚠️ 2026-09：`.env` 被挂成**目录**时改为**拒绝启动**（此前只打一行 warning 就继续）。
#
# 成因是 Docker 的经典陷阱：`docker-compose.yml` 里写的是 `./.env:/app/.env`，若宿主机上
# 该路径**不存在**，`docker compose up` 会在宿主机上**创建一个同名目录**，然后把它挂进来。
# 旧行为下容器照常起来，但：
#   ① 没有任何配置 ⇒ 看板永远"未就绪"，用户以为是程序坏了；
#   ② 更坑的是后台"保存配置"会往 `/app/.env` 写文件 ⇒ 写进一个目录里，**永远存不上**，
#      用户改了又改、以为生效了，实际什么都没保存。
# 静默地跑成"半个程序"比直接不起来糟得多（起不来至少 `docker compose logs` 一眼看到原因），
# 故在此 fail-closed，并把**逐字可复制**的修复命令打出来。
if [ -d "$ROOT_DIR/.env" ]; then
    echo "❌ [Entrypoint] 致命：/app/.env 是一个**目录**，不是配置文件。" >&2
    echo "   成因：docker compose 挂载 ./.env 时该路径在宿主机上不存在，Docker 会自动创建同名目录。" >&2
    echo "   在**宿主机**的仓库根目录执行以下命令即可修复：" >&2
    echo "     rm -rf .env && cp env.example .env && chmod 600 .env" >&2
    echo "     docker compose up -d --force-recreate" >&2
    echo "   （或直接用一键脚本 ./deploy/docker-start.sh，它会自动纠正这一情况）" >&2
    exit 1
elif [ ! -f "$ROOT_DIR/.env" ] && [ -f "$ROOT_DIR/env.example" ]; then
    echo "⚠️ [Entrypoint] .env not found. Generating default .env from env.example..."
    cp "$ROOT_DIR/env.example" "$ROOT_DIR/.env"
    chmod 600 "$ROOT_DIR/.env"
fi

# 3. 初始化默认标的池（如果不存在，避免冷启动阻断）
if [ ! -f "$ROOT_DIR/data/instrument_pool.json" ]; then
    echo "📋 [Entrypoint] Initializing default instrument pool in data/..."
    python3 -c "from scripts.instrument_pool import save_instruments, DEFAULT_INSTRUMENTS; save_instruments(DEFAULT_INSTRUMENTS)" 2>/dev/null || true
fi

MODE="${1:-backend}"

case "$MODE" in
    backend|web)
        # 2026-09：改为由看门狗托管，而不是直接 exec uvicorn。
        # 理由：Docker 的 `restart:` 策略只对**退出**生效；"进程还在、HTTP 卡死"这种死法
        # Docker 根本不会重启（健康检查只影响 `docker ps` 的显示）。看门狗按
        # /api/v1/health 探测并拉起，把"卡死"一并覆盖。看门狗缺失时退回直接起 uvicorn。
        if [ -f "$ROOT_DIR/scripts/r20_watchdog.sh" ]; then
            echo "✨ [R20] Starting Web Engine & Control Plane on 0.0.0.0:8080 (supervised)..."
            exec bash "$ROOT_DIR/scripts/r20_watchdog.sh"
        fi
        echo "✨ [R20] Starting Web Engine & Control Plane on 0.0.0.0:8080..."
        exec python3 -m uvicorn r20_backend.app:app --host 0.0.0.0 --port 8080
        ;;
    gateway|worker)
        # 同理：网关的死法更隐蔽 —— 后端照常绿着、看板能开，调度却已停。
        # 判据是 worker 每轮循环写的存活心跳（见 scripts/gateway_liveness.py）。
        if [ -f "$ROOT_DIR/scripts/r20_watchdog.sh" ]; then
            echo "🚀 [R20] Starting Quantitative Gateway & Dispatch Worker (supervised)..."
            exec bash "$ROOT_DIR/scripts/r20_watchdog.sh" gateway
        fi
        echo "🚀 [R20] Starting Quantitative Gateway & Dispatch Worker..."
        exec python3 -m r20_gateway.worker
        ;;
    all)
        echo "✨ [R20] Starting All-in-One Mode (Web + Gateway Worker)..."
        python3 -m uvicorn r20_backend.app:app --host 0.0.0.0 --port 8080 &
        BACKEND_PID=$!
        python3 -m r20_gateway.worker &
        GATEWAY_PID=$!

        trap 'echo "🛑 Stopping services..."; kill -TERM $BACKEND_PID $GATEWAY_PID 2>/dev/null' TERM INT
        wait -n $BACKEND_PID $GATEWAY_PID
        EXIT_CODE=$?
        kill -TERM $BACKEND_PID $GATEWAY_PID 2>/dev/null || true
        exit $EXIT_CODE
        ;;
    *)
        exec "$@"
        ;;
esac

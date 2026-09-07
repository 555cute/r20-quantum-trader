# R20 灾备恢复手册

本手册适用于当前 standalone 架构。旧 QwenPaw cron、`daemon_web_sync.py` 和独立 `dashboard.app` 启动方式不能与当前 Gateway 调度混用。部署入口及环境说明见 [STANDALONE.md](STANDALONE.md)。

## 1. 恢复前先停止交易

停止该安装的 backend、Gateway 和旧调度任务。确认没有其他主机或进程使用同一交易账户执行相同策略。不要在恢复数据库或配置时保留交易 worker 运行。

保留原安装副本，在独立目录核对备份范围、校验和、版本及解密能力；不要直接覆盖唯一副本。归档可能包含敏感策略、账户流水和凭证，禁止公开上传。

## 2. 需要恢复的资产

- `.env`：非公开配置与启动设置。
- `data/.r20_secret_key` 和 `data/r20_secrets.enc`：主密钥及加密凭证，必须匹配。
- 备份凭证库与对应解密密钥：参照实际备份任务配置恢复。
- `data/r20_admin.db`：管理员与会话。
- `data/r20_gateway.db`：调度记录、通知队列和调用遥测。
- `data/r20_quant.db`：交易台账数据库。
- 策略配置：提示词库、投委会、拦截器配置及 `policy_archives/`。
- 运行状态：台账、仓位追踪、熔断/冷却、决策记录和权益快照。
- `structured_trading_memory.json`：长期记忆权威库；Markdown 心法文件是兼容镜像，不能代替权威库。

通过项目备份功能取得一致的 SQLite 副本。不要仅复制正在写入的 `.db` 并忽略 WAL。备份任务的范围不一定包含上面所有资产，恢复前逐项确认。

不要恢复旧 PID/锁文件作为运行中进程的证据，不要复制他人的 `~/.okx/` 授权状态。使用新的独立 API 凭证或由实际服务用户重新完成 CLI OAuth。

## 3. 安装依赖并只启动控制面

按 `STANDALONE.md` 安装 Python、Node 和项目依赖，重新构建前端。先保持两个自动 worker 关闭：

```sh
R20_GATEWAY_WORKER_ENABLED=0 R20_DASHBOARD_WORKER_ENABLED=0 \
  python -m uvicorn r20_backend.app:app --host 127.0.0.1 --port 8080
```

PowerShell 对应设置 `$env:R20_GATEWAY_WORKER_ENABLED="0"`、`$env:R20_DASHBOARD_WORKER_ENABLED="0"` 后再启动。

首先访问 `/api/v1/health` 和 `/admin/`，核对管理员、策略、交易环境及账户归属。控制面包含会访问外部系统的操作；关闭自动 worker 不等于所有 HTTP 端点只读。

## 4. 恢复交易所授权与对账

确认使用正确的 DEMO/LIVE 凭证、服务用户 HOME 和 PATH。OKX 使用官方 CLI 时，应在实际服务用户下授权；不要把开发者的 `.okx` 目录拷到服务器。任何诊断、账户刷新或对账都会访问交易所，需明确执行。

对账以交易所当前持仓、挂单和保护委托为准，不能只信恢复出的本地 JSON。旧通知队列的重投递可能重复发送通知，处理前先检查其状态。

## 5. 选择唯一调度所有者

- **后端托管**：仅启动 `r20_backend.app`，启用其 Gateway 开关，不再启动独立 Gateway。
- **systemd 托管**：使用配套 `r20-quantum.service` 和 `r20-gateway.service`。后端 unit 已显式关闭内置 Gateway，独立 unit 才是调度所有者。

不要同时启用 `r20-scheduler.service`、QwenPaw 交易 cron 或旧 `dashboard/start.sh`。当前默认自进化时段是北京时间 02:00、08:00、14:00、20:00，以恢复后的排程配置为准。

在确认交易所状态、保护委托、风险配置和调度所有权后，再启用自动交易及账户轮询。`ai_brain_trader.py` 可能撤单，`ai_factor_trader.py` 会执行交易；它们都不是无副作用的诊断命令。

# R20 AI 自进化量化交易系统 — 完整部署与灾备恢复手册 (QwenPaw)

> 本文档用于在任何全新环境（新云服务器 / 重新安装的 QwenPaw）中，100% 快速恢复本套基于 Gemini 3.7 Flash High（Reasoning High）深度思考的大模型全权决策自进化量化交易系统。

---

## 1. 灾备架构概览

- **交易核心**：`scripts/ai_brain_trader.py` + `scripts/ai_factor_trader.py`（聚焦 BTC/ETH/SOL/DOGE/SUI/LINK 6 标的，LLM 全权决策，Maker 限价挂单，OKX 云端 OCO 止盈止损 100% 保护）
- **因子计算与同步守护**：`scripts/daemon_web_sync.py`（60 秒并发计算动量趋势、波动通道、资金流向、微观盘口与 Top100 聪明钱五大量化因子库）
- **自进化心法引擎**：`scripts/self_improvement_engine.py`（每日 20:00 深度复盘真实流水，提炼 3 大启发式心法沉淀至 `data/AI_TRADING_MEMORY.md`）
- **全网快讯情报流**：`scripts/news_sentiment_harvester.py`（OKX 最新与重大快讯双路聚合）
- **Web 监控大屏**：`dashboard/app.py` + `dashboard/templates/index.html`（Bloomberg/Linear 级 Dark Glassmorphism 极客交易终端，支持全局 Prompt 悬浮透视抽屉）
- **插件化灾备**：`scripts/backup_runtime.py` + 后台“灾备中心”（按任务配置本地、百度官方 OAuth/ByPy、S3 兼容、阿里云 OSS、WebDAV/OpenList、阿里云盘桥接与实验性夸克桥接；凭证独立加密，任务导出不含密钥）
- **核心数据资产清单**：
  - `data/trading_ledger.json` & `trading_ledger.xlsx`（全量交易流水账本与资金费记录）
  - `data/AI_TRADING_MEMORY.md`（QwenPaw 原生带时间戳启发式实战心法长期记忆）
  - `data/ai_brain_history.json`（AI 大脑每 15 分钟全市场宏观推演与在途持仓审计日志）
  - `data/ai_brain_last_prompt.txt`（15,500+ 字符真实 System + User Prompt 快照）
  - `data/factor_library_snapshot.json`（五大核心量化因子库快照）
  - `data/snapshots.json`（历史权益与回撤走势快照）
  - `data/news_sentiment.json`（全网实时快讯舆情库）
  - `data/quant_trader.db`（SQLite 核心审计数据库）

---

## 2. 新环境一键恢复步骤

### 步骤 1：解压最新灾备包
从后台配置的任一成功灾备目标下载最新归档。百度 ByPy 兼容目录通常为 `/我的应用数据/bypy/R20_Backups/`；官方 OAuth 默认应用目录为 `/apps/R20QuantumTrader/R20_Backups/`；S3/OSS/WebDAV 使用任务中配置的远程前缀。上传至工作区并解压：
```bash
cd /app/working/workspaces/default
tar -zxvf r20_system_backup_*.tar.gz
```

### 步骤 2：恢复 OKX V5 API Key 配置
先保持交易调度器停止。运行 `sh deploy/install.sh` 安装后端 Python 依赖（已有 `.env` 不会被覆盖），检查 `R20_OKX_ENV=demo`。

- 推荐在步骤 3 启动控制面后，通过 `/admin` →「账户接入」重新填入 DEMO / LIVE 各自的 API Key、Secret Key、Passphrase 三件套。后台采用 **Fernet 本地加密存储，留空不改**；只授予必要读取/交易权限，禁用提款权限。
- 若从受信任的私密备份恢复凭证，必须保留**成对匹配**的 `data/r20_secrets.enc` 与 `data/.r20_secret_key`，并让服务账户有读取权限。不要假定普通数据归档已包含密钥；缺失或无法解密时，优先在后台重新配置，禁止把密钥提交到代码仓库。
- 选中档位未配齐时为 **NOT READY**，OKX 私有调用及交易 fail-closed；公共行情仍可免凭证读取。服务器重启不再丢失短期登录授权：持久化密文和匹配加密密钥即可重新加载，但 API Key 被撤销、IP 白名单变化仍须处理。
- 登录后查看 `GET /api/v1/admin/okx/runtime`：`environment` 确认档位，`mode_configured` 确认三件套，`status` 为 `READY` 或 `NOT_READY`，未配置时看 `not_ready_reason`。**READY 仅表示本地配置，不代表交易所鉴权已通过**；恢复调度前再用只读账户快照核对账户与持仓。

### 步骤 3：一键启动 Web 监控大屏 (端口 8080)
```bash
cd /app/working/workspaces/default
. .venv/bin/activate
python -m uvicorn r20_backend.app:app --host 0.0.0.0 --port 8080
```
验证访问：`http://<你的服务器IP>:8080`

### 步骤 4：恢复单一调度器
确认 DEMO 账户与只读快照正常、复核风控与遗留仓位后，再在另一终端启动：
```bash
cd /app/working/workspaces/default
. .venv/bin/activate
python -m r20_gateway.worker
```
生产环境使用 `STANDALONE.md` 的 systemd 配置托管。恢复前停用旧 QwenPaw 交易定时任务及重复守护进程，**不得同时运行两套交易调度器**，避免重复下单。

---

## 3. 常用维护与测试命令

- **查看 Web 监控状态**：`curl -s http://127.0.0.1:8080/api/all | head -c 100`
- **手工执行一次全系统云端备份**：`python3 scripts/nightly_backup_and_clean.py`
- **手工触发一次 AI 交易大脑推演**：`python3 scripts/ai_brain_trader.py`
- **强制触发每日 AI 策略自进化复盘**：`python3 scripts/self_improvement_engine.py`
- **手工全量对账同步 OKX 账本**：`python3 scripts/sync_full_ledger.py`


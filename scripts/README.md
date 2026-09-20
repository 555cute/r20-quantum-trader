# `scripts/` 导航

本目录是 R20 的**运行时代码**：实盘 worker、后台守护进程、以及它们共用的库。
它**不是**一个 Python 包（没有 `__init__.py`），模块之间以
`sys.path` 上的顶层名互相 `import`，同时也支持 `scripts.xxx` 双拼写
（见本文末「双拼写」一节）。

> ⚠️ **为什么要有这份文档**：本目录根层有 **33 个 `.py`**，
> 此前**没有任何 README**，其中 23 个在全仓文档里连一次都没被提到。
> 新人（或下一个 Agent）只能靠逐个打开文件猜哪个是入口、哪个是库。
>
> 第六十六刀补上这份导航后，`tests/audit/test_directory_docs_current.py`
> 会把「磁盘上的根层模块」与「本文档提到的模块」**双向对照**，
> 漏登记或指向不存在的文件都会翻红 —— 与前几刀给 `r20_backend/` 加的是同一道闸。

## 入口 / 调度（由外部按周期拉起）

| 模块 | 行数 | 何时跑 | 说明 |
|---|---|---|---|
| `ai_factor_trader.py` | 2801 | **每 15 分钟**（:00/:15/:30/:45） | 实盘主脚本。因子→选所→下单→持仓管理全链路 |
| `daemon_web_sync.py` | 47 | 常驻循环 | 拉起 `sync_web_data.generate_trading_data()`，并定时采集新闻/因子 |
| `news_sentiment_harvester.py` | 453 | 由上面那个守护调用 | 多源新闻采集 + 黑天鹅熔断哨兵 |
| `daily_summary_and_backup.py` | 124 | 每日 | 每日量化简报 |
| `nightly_backup_and_clean.py` | 86 | 每日 02:00 | 跑配置好的备份作业 + 清理 |
| `cleanup_disk.py` | 125 | 按需/定时 | 磁盘与日志清理 |
| `generate_snapshots.py` | 102 | 定时 | 生成 `snapshots.json` 供前端曲线 |
| `sync_web_data.py` | 346 | 由 `daemon_web_sync.py` 每轮调用 | 生成前端 `trading_data.json` 缓存（凭证未配则 **fail-closed**，绝不用 0 覆盖好缓存） |

## 交易核心

| 模块 | 行数 | 说明 |
|---|---|---|
| `ai_brain_trader.py` | 1117 | AI 主脑全标的池决策引擎（与主脚本共用风控常量） |
| `factor_library.py` | 298 | 多因子库：`compute_instrument_factors()` 逐标的装配因子 |
| `instrument_pool.py` | 409 | 交易宇宙（标的池）的**校验后**单一来源 |
| `market_data_service.py` | 562 | 零进程直连公共行情服务；`_public_get/_public_post` 带**耗时/成败埋点**（调用期 `note_call`/`note_failure`，取值行为一字不变） |
| `market_data_health.py` | 266 | 行情取数可观测性：失败**计数 + 每类一次性告警**（第 137 刀）+ 调用**耗时/成功率/百分位**与跨进程快照 `data/market_data_health.json`（第 138 刀；worker 每周期写、后端 `/metrics` 读） |
| `calculus_engine.py` | 71 | 因果微积分 / 定积分 / 概率论引擎 |
| `order_risk.py` | 60 | 报价与风控的**确定性**安全检查（共享） |
| `backtest_engine.py` | 382 | 多资产回测与统计验证引擎（门面，部件在 `backtest/`） |

## 执行 / 台账

| 模块 | 行数 | 说明 |
|---|---|---|
| `okx_rest.py` | 552 | OKX V5 签名 REST 客户端（请求契约显式） |
| `okx_runtime.py` | 87 | OKX 实盘/模拟盘凭证的单一来源 |
| `sync_full_ledger.py` | 724 | OKX 持仓历史 → 本地台账同步 |
| `archive_ledger.py` | 198 | 台账分片归档（**默认 dry-run**） |
| `risk_constants.py` | 175 | 执行层风控参数单一事实源 |
| `policy_snapshot.py` | 53 | 策略快照代理模块 |
| `evolution_shield.py` | 551 | 演化盾：防投毒认知守护 |

## 子包（按域拆分，各有自己的文档）

| 子包 | 内容 |
|---|---|
| `trader/` | 从 `ai_factor_trader.py` 抽出的纯逻辑（信号 / 保护单 / 闸门 / 仓位 / 通知 / 预留对账…） |
| `brain/` | 主脑周期部件 |
| `factors/` | 因子评分与取值 |
| `backtest/` | 回测引擎部件 |
| `calculus/` | 微积分引擎部件 |
| `evolution/` | 演化盾部件 |
| `ledger/` | 台账同步部件 |
| `news/` | 新闻采集部件 |

> 各子包的**模块清单**写在它自己的 `__init__.py` 里，
> 并由 `tests/audit/test_directory_docs_current.py` 强制（漏登记必红）。

## 提示词 / LLM

| 模块 | 行数 | 说明 |
|---|---|---|
| `prompt_library.py` | 1028 | 版本化提示词库（Python 交易侧直接使用） |
| `prompt_templates.py` | 193 | 提示词模板编译：文本 ⇄ 模块 ⇄ 管线布局 |
| `llm_credentials.py` | 93 | LLM 客户端凭据解析单一事实源 |

## 其它

| 模块 | 行数 | 说明 |
|---|---|---|
| `backup_runtime.py` | 481 | 备份作业运行时：打包 / 加密 / 校验 / 投递 |
| `backup_upload.py` | 282 | 备份上传目标：S3 / OSS / WebDAV / 百度网盘 |
| `self_improvement_engine.py` | 782 | LLM 原生自省与策略演化引擎 |
| `local_lock.py` | 114 | `file_lock` 的**本地兜底**实现（跨主机锁的降级路径） |
| `qq_notifier.py` | 157 | 通知发布器，桥接 R20 网关 |
| `db_manager.py` | 282 | SQLite 连接/建表管理 |
| `debug_aggregate_orders.py` | 54 | **调试脚本**：聚合挂单排查 |
| `debug_audit_bills.py` | 48 | **调试脚本**：账单审计排查 |

## ⚠️ 双拼写（改动 import 前必读）

这些模块**同时**以两种名字被导入：

```python
import okx_rest                 # 顶层名（scripts/ 在 sys.path 上）
from scripts.okx_rest import …  # 包路径名（仓库根在 sys.path 上）
```

两者在 `sys.modules` 里是**不同的模块实例**。由此派生两条铁律：

1. **新增的抽取子模块**若可能被两种拼写导入，必须写成
   `try: from scripts.X import … except ImportError: from X import …`
   （第四十八 / 五十三刀都在这上面栽过：`ModuleNotFoundError` 让测试翻红）；
2. **测试里 `patch.object(module, name)` 只影响它拿到的那一个实例** ——
   所以子模块**不得在 import 期绑定**任何会被 patch 的名字，
   一律**调用期注入**（这是本仓第五十刀以来反复确认的手法）。

## 判绿命令

```bash
# 全量套件（当前基线见 r20_backend/README.md §6）
.venv/bin/python -m unittest discover -s tests -t .
```

> 注意 `python -m tests.offline_suite` 会主动拦截未白名单的外部子进程
> （`git` / `python` / `node` 等），那是守卫自身的产物，**不是回归**。
> 详见 `r20_backend/README.md` §6。

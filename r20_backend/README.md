# `r20_backend/` 分层与归档约定

> 本文是**约定文档，不是目录搬迁计划**。结构优化研究（`plan_local/R20_STRUCTURE_OPTIMIZATION_20260914.md`
> §2 B7 / §10.5）得出的结论是：**不为目录整齐去搬已上线的启动路径模块**，
> 而是把"哪个模块属于哪一层、新文件该进哪个子包"固化成文字约定。
> 读完这一页，你应该能在 30 秒内回答"我这个新文件该放哪"。

## 1. 为什么这里不是"一个包一个域"的整齐目录

根层 35 个模块 + 9 个子包**看起来**扁平，但那是**有代价的取舍**，不是没整理：

1. **路径锚点会被踩。** 仓里有一批审计测试按**文件路径**钉死读取
   （`r20_backend/execution_router.py` 5 处、`policy_snapshot.py` 3 处、
   `qq_gateway_daemon.py` 2 处、`llm_manager.py` 2 处、`council_manager.py` 2 处、
   `app.py` 2 处、`scheduler.py` 1 处）。为目录整齐去改 17 处锚点，
   正是研究文档 §4 明确反对的方向（"先设计抽取边界去迁就锚点，而不是反过来"）。
2. **import 面很宽。** 例如 `llm_manager` 有 16 处引用、`time_utils` 12 处、
   `notifications` 10 处、`council_manager` 10 处 —— 搬迁是 30+ 个 import 点的
   纯机械改动，功能收益为零。
3. **都是线上启动路径模块。** `app.py` / `scheduler.py` / worker 在启动时导入它们。
   对一个正在跑实盘的进程做强搬迁，风险与收益不成比例。
4. **B7 的实质收益已提前拿到。** 四个真正膨胀的巨型模块已经拆成子包，
   根文件从 2105 / 1194 / 1101 / 1968 行降到 281 / 416 / 295 / 904 行
   （`llm_manager.py` / `council_manager.py` / `policy_snapshot.py` / `r20_backend/dashboard_cache.py`）。
   **"根目录不再膨胀、新代码进子包"这条实质目标已经实现。**

## 2. 分层（按职责，不按目录）

### L0 门面 / 兼容表面 — 保持文件名与位置不变

这些模块**故意**留在根层，且**只做转发**。它们的存在就是为了让老 import 路径与
测试锚点继续成立。**不要往门面里加新逻辑。**

| 门面模块 | 行数 | 真实实现位置 |
|---|---|---|
| `llm_manager.py` | ~281 | `llm/`（util / capabilities / providers / transport / policy / store / failover / call） |
| `council_manager.py` | ~416 | `council/`（debate / policy / roster / presets） |
| `policy_snapshot.py` | ~295 | `policy/`（paths / schema / fingerprints / io / capture / restore / archive） |
| `dashboard_cache.py` | 536 | `dashboard_payload/`（12 模块）；**0 条路由**（纯库，路由在 `routers/dashboard.py`） |

**判据**：一个模块如果"只剩转发/薄壳"，它就是门面，新逻辑一律进它对应的子包。

### L1 启动与装配

`app.py`（FastAPI 装配，`include_router` 8 个路由）、`scheduler.py`、
`spawn.py`、`web_shell.py`、`config.py`、`settings_store.py`、`version.py`、
`dependencies.py`、`routers/`。

**这一层不要拆。** 它们是进程入口，改动收益低、回归面是整个服务。

### L2 HTTP 边界 — `routers/`

`auth` / `system` / `exchanges` / `risk` / `strategy` / `llm` / `gateway` /
`dashboard`。**路由层只做参数校验与调用编排，不放业务逻辑。**
路由瘦身的正确做法是把业务下沉到 `exchanges/`、`execution/`、
`dashboard_payload/`，路由保留薄壳（与 `r20_backend/dashboard_cache.py` 降为纯库同一手法）。

`gateway` 也已按此手法拆成**包**（第九十七刀）：`gateway/` =
`gateway/channels.py`（渠道开关）/ `gateway/gateway_ops.py`（状态·投递重放·作业执行）/
`gateway/notifications.py`（通知配置·诊断·QQ 绑定·计划）/ `gateway/backups.py`
（备份目标·凭据·作业·归档·恢复）+ `gateway/_shared.py`（`_get_root` 注入缝），
`gateway/__init__.py` 只做**按原顺序**聚合。34 条 URL/方法/处理器名/tags 一字未改，
路由表对拍门见 `tests/ops/test_gateway_router_split.py`。

`strategy` 已按此手法拆成**包**（第九十六刀）：`strategy/` =
`strategy/council.py`（议会配置与辩论）/ `strategy/interceptors.py`（拦截器 CRUD）/
`strategy/policy.py`（策略快照）/ `strategy/prompts.py`（提示词库与档案），
`strategy/__init__.py` 只做**按原顺序**聚合
（顺序即匹配优先级）。35 条 URL/方法/处理器名/tags 一字未改，
路由表对拍门见 `tests/trading/test_strategy_router_split.py`。

### L3 领域服务（根层，按域成组）

| 域 | 模块 |
|---|---|
| 交易执行 | `execution_router.py`、`okx_trade_service.py`、`okx_client.py`、`close_intent.py`、`risk_reservation.py`、`venue_router.py`、`exchanges/`、`execution/`、`sandbox/` |
| 风控与安全 | `risk_config.py`、`net_security.py`、`login_guard.py`、`client_ip.py`、`admin_auth.py`、`interceptor_manager.py`、`redact.py` |
| 通知与外部通道 | `notifications.py`、`qq_bind.py`、`qq_gateway_daemon.py` |
| 审计与备份 | `audit.py`、`backup_store.py`、`backup_secrets.py`、`file_locks.py` |
| 组合与账户 | `portfolio_aggregator.py`、`account_baseline.py` |
| 提示词 | `prompt_views.py`、`dashboard_payload/prompts*` |
| 通用 | `time_utils.py`、`math_utils.py`、`schemas.py`、`schedule_store.py` |
| 可观测性 | `metrics.py`（Prometheus 文本 exposition 的唯一渲染点；路由薄壳在 `routers/system.py::admin_metrics`） |

### L4 纯计算/载荷子包（新代码的默认去处）

`llm/`、`council/`、`policy/`、`dashboard_payload/`、`execution/`、`exchanges/`、
`routers/`、`sandbox/`。

#### `dashboard_payload/` 模块清单

`r20_backend/dashboard_cache.py::update_cache_cycle` 曾是 581 行的单函数，载荷各段按域搬进此处。
**新加的载荷段请进这个子包，不要再往 `app.py` 堆。**

| 模块 | 内容 |
|---|---|
| `cache_payload.py` | `build_live_cache_payload(...)` —— LIVE 载荷装配（27 顶层字段，56 入参**显式**列在签名里） |
| `slim.py` | 瘦身载荷（默认返回；`?full=1` 才给全量） |
| `market.py` | 行情/盘口片段 |
| `factors.py` | 因子库快照片段 |
| `factors_view.py` | 因子视图（Pillar 展开） |
| `health.py` | `data_health` 与缓存年龄 |
| `cache.py` | 缓存读写与原子落盘 |
| `readers.py` | 本地文件读取的容错包装（存在性 → 解析 → 降级） |
| `local_reads.py` | 本地只读数据装配 |
| `bills.py` | `aggregate_bills` —— OKX 账单聚合 |
| `trade_stats.py` | `aggregate_trade_stats` —— 累计胜率/盈亏/分币种；`aggregate_bills_and_metrics` —— `update_cache_cycle` **相位 4 聚合段**：票据聚合 + 交易统计派生指标（胜率/盈亏比/均值/累计 ROI，含全部除零分支）+ 标的排行榜（B2 第九十五刀；段体 AST 逐字、11 项同名注入、22 项输出） |
| `trader_leaderboard.py` | `build_inst_leaderboard` —— 分币种战绩榜（按 pnl 降序） |
| `position_view.py` | `collect_position_rows` —— 持仓行（含 `ctVal` 折算与 `lever<=0` 守卫） |
| `order_view.py` | `collect_pending_order_rows` —— 在途挂单行 |
| `collect.py` | `collect_core_account_state` —— `update_cache_cycle` **相位 1**：余额/持仓/挂单三路并发抓取 + 单项失败降级 + 「三项同时 NOT_READY ⇒ 连接方式缺失」判定 + USDT 余额解析 + 追踪器/持仓行/挂单行装配（B2 第九十四刀；段体 AST 逐字、11 项同名注入、14 项输出） |
| `algo_protection.py` | 算法保护单视图 |
| `ledger_view.py` | 台账视图 |
| `multi_venue.py` | 三所组合视图 |
| `integrity_sidecars.py` | 完整性旁车并入 `source_errors`（台账同步状态 / AI 连败） |
| `reset_state.py` | 状态重置 |

> 约定的"注入面"铁律见 §5：这些模块**不得**在 import 期绑定 `r20_backend.dashboard_cache`
> 的模块级名字（它们会被测试 `patch.object`）。

## 3. 新文件该放哪：决策树

```
新代码要做什么？
├─ 纯计算 / 无 I/O / 无模块状态        → 对应子包的独立模块（L4）
├─ 组装 HTTP 响应载荷                  → dashboard_payload/
├─ 新增一个 HTTP 端点                  → routers/<域>.py（薄壳，逻辑下沉）
├─ 新增交易所适配                     → exchanges/<venue>.py
└─ 已有模块太长，想拆
   ├─ 该模块是 L0 门面                → 拆进它的子包，门面只留转发
   ├─ 该模块在 L1 启动层              → 不要拆
   └─ 该模块在 L3 领域层              → 新建同名子包（见 §4）
```

## 4. 门面 + 子包的抽取约定（本仓既定手法）

阶段 2 起，所有大文件拆分都遵循同一套动作，照抄即可：

1. **子包名 ≠ 门面名时**，在 `tests/source_scan.py::source_area()` 里用
   `pkg_name=` 显式指定（例如 `source_area("scripts/ai_factor_trader.py", pkg_name="trader")`），
   这样**后续再往该子包搬文件时，源码锚点断言自动覆盖**，不用回来改测试。
   已有的"门面 → 子包"映射：

   | 门面 | 子包 |
   |---|---|
   | `scripts/ai_factor_trader.py`（交易执行） | `scripts/trader/` |
   | `scripts/ai_brain_trader.py`（AI 主脑） | `scripts/brain/` |
   | `scripts/sync_full_ledger.py`（台账同步） | `scripts/ledger/` |
2. **被测试 patch 的全局（路径、配置、可替换函数）一律走调用期注入**，
   绝不在子模块 import 期烘焙。原因见 §5。
3. **门面保留同名薄壳**（若调用点走全局名查找，调用点可以一行都不改）。
4. **搬完必须**：全量离线套件绿 + 旧实现差分对拍（见 §6）。

## 5. 铁律：子模块不得 import 期烘焙任何可被 patch / 可重载的值

`tests/risk_test_env.py::pin_baseline_risk_env()` 的**原地 reload 名单只有**
`risk_constants` / `ai_factor_trader` / `ai_brain_trader` —— **不含任何子模块**。

因此：**子模块在 import 期绑定的任何风控/配置值都不会被刷新**，
会让基线风控用例随机翻红。凡读配置、读被 patch 路径的东西，**一律走调用期注入**。

## 6. 每次拆分后必须过的两道闸

```bash
# 1) 全量套件（当前基线：7719 例 OK, skipped=1）
#    ⚠️ 这个数字由 tests/core/test_readme_baseline_numbers.py 钉住：
#    它用 AST 数出仓里 test_* 方法数，再要求本行数字与之同量级。
#    超过 ±10% 就会翻红 —— 忘了更新这里会当场被抓住，不会静默漂移。
.venv/bin/python -m unittest discover -s tests -t .

# 2) 纯逻辑搬家：旧实现差分对拍（抽到哪块，就为哪块写一条）
#    参考 tests/extraction/test_trader_protection_extraction.py（把旧代码内联为 _legacy_* 逐值对拍）
```

> 注意 `python -m tests.offline_suite` 会**主动拦截 `git` / `python` / `node`
> 等未白名单的外部子进程**，因此在该守卫下会有相当数量的
> "Offline suite blocked external child process" 报错 ——
> 那是守卫本身的产物，**不是回归**（2026-09-15 实测：`git` 16 次、
> `python` 55 次，`node` 0 次）。判绿请用上面的 `unittest discover`。
>
> ⚠️ 由此派生一条**测试编写规矩**：凡测试会 spawn 子进程
> （`node` / `vite` / `vue-tsc` / 独立 `python` 探针）的，
> 必须在 spawn **之前**调 `_guard_offline()`（判据为套件预设的
> `OFFLINE_SUITE_RUNNING`），否则会污染离线基线、让人误判回归。
> 参考第六十一刀：新增的 5 个 node 套件漏了这道守卫，
> 使 `external child process: node` 计数从 0 变回 5。
> 已知**尚未**补齐的存量文件另见台账 §75（刻意不动，避免无谓改动面）。

## 7. 源码锚点的三类陷阱（搬文件前必查）

搬任何函数之前，把这三类都查一遍，缺一类都会翻车：

1. **结构性 split**：`src.split("def X")[1].split("\ndef ")[0]` —— 函数搬走即 `IndexError`，
   且**紧随其后的那个顶层 `def` 也不能搬**（窗口会延伸，断言结果改变）。
2. **函数名文本锚点**：`assertIn("def X", src)`。
3. **函数体内的字符串锚点**（最隐蔽）：不出现函数名，例如
   `assertIn('item.get("minSz"', src)`。查法：把该函数体里所有字符串字面量
   拿去 `grep` 一遍 `tests/`。

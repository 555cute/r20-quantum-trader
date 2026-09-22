# R20 代码结构优化总结（阶段 4 收口）

> 分支：`refactor/phase4-frontend-modernization`　收口日期：2026-09-15（Asia/Shanghai）
> 完整逐刀记录：`plan_local/records/structure-01..05.md`（本地 gitignore）
> —— 2026-09-22 起该记录**已分片为 5 片**，原单文件
> `plan_local/R20_STRUCTURE_OPTIMIZATION_20260914.md` 已删除（内容全部迁入分片）。
> **全量逐刀索引**（含每刀落在哪个文件）见 `plan_local/records/index.tsv`，
> 导航总入口 `plan_local/README.md`。
> 本文件是**对外的收口说明**：量化结果、问题清单状态、目录约定、后续维护须知。

## 1. 一句话结论

**接口、页面、业务逻辑零变更**：全部改动都是「同一棵 AST 的搬家 + 同名注入」，
每刀都有对拍门（段体与改造前逐字同源、调用点传参完整、行为例、负向验证）兜底。
后端测试从基线 **1299 → 3133 例**，前端 27 用例 / 136 断言全绿。重构期间实盘交易周期**零异常**。

## 2. 验证矩阵（收口实测）

| 检查 | 结果 |
|---|---|
| 后端全量 `unittest discover -s tests -t .` | **3133 OK**（skipped=1） |
| 离线套件 `tests/offline_suite.py` | **3114 OK**（skipped=27）；`CONFIG_WRITE_ATTEMPTS: []`；EGRESS 自检通过 |
| 前端 `node --test tests/*.test.mjs` | **27 用例 / 136 断言，0 失败** |
| 前端 `npx vue-tsc --noEmit` | 干净（无输出） |
| 前端 `npx vite build` | 成功（1.64s） |
| 实盘交易周期（13:00 / 13:15 / 13:30 / 13:45 / 14:00） | 异常行 **0** |
| 标的池 `data/instrument_pool.json` | 未变（sha256 前缀 `a08684e8`） |
| 工作树 | 干净 |

> 命令与数字口径：抽取门文件 **75** 个、测试文件 **211** 个（另含 1 个结构门 `test_module_free_names.py` 与其扫描器 `_free_names_scan.py`）。

## 3. 量化对照（改造前 → 现在）

| 文件 | 前 | 后 | 说明 |
|---|---:|---:|---|
| `scripts/ai_factor_trader.py` | 3565 | 1108 | 最大函数 1021 行级 → 142 行 |
| `r20_backend/dashboard_cache.py` | 2047 | 533 | `update_cache_cycle` **1021 → 185 行**（全仓最大单函数） |
| `r20_backend/llm_manager.py` | 2105 | 281 | 最大函数 19 行 |
| `r20_backend/council_manager.py` | 1194 | 416 | 最大函数 66 行 |
| `r20_backend/policy_snapshot.py` | 1101 | 295 | 最大函数 61 行 |
| `r20_backend/llm/store.py` | 997 | 883 | |
| `scripts/ai_brain_trader.py` | 748 | 953 | 期间含功能新增 |
| `scripts/sync_full_ledger.py` | 715 | 690 | |
| `scripts/self_improvement_engine.py` | 752 | 738 | |
| `r20_backend/exchanges/binance.py` | 638 | 604 | |
| `frontend/src/views/admin/LlmPage.vue` | 1762 | **39** | 拆为组件 + `useLlmConfig` 等 composable |
| `frontend/src/components/dashboard/ChartWorkstation.vue` | 1285 | 902 | 叠加层计算拆到 `chartLiveLevels.ts` |
| `frontend/src/locales/legacy/` | 648（100% 死键） | **已删除** | |

## 4. 问题清单状态（对应结构报告 §0）

| 编号 | 问题 | 状态 |
|---|---|---|
| B1 | 双路由层：`r20_backend/dashboard_cache.py` 影子 handler 永不执行 | ✅ 已修 |
| B2 | `update_cache_cycle` 1021 行单函数 | ✅ 已拆（现 185 行，载荷字节基准回归） |
| B3 | 实盘交易员单文件 3565 行 | ✅ 已拆（现 1108 行，`scripts/trader/` 27 模块） |
| B4 | LLM 管理器三段混住 | ✅ 薄壳 + 核心抽离（现 281 行，`r20_backend/llm/` 12 模块） |
| B5 | 委员会配置与辩论引擎混住 | ✅ 已拆（现 416 行，`r20_backend/council/` 6 模块） |
| B6 | 策略快照三域混住 | ✅ 已拆（现 295 行，`r20_backend/policy/` 8 模块） |
| B7 | 后端根与 `scripts/` 扁平无分组 | ✅ 已按域建目录（见 §5） |
| B8 | 单 router 承载 30+ 端点 | ✅ 已拆（`routers/strategy/` 5 模块、`routers/gateway/` 6 模块） |
| F1 | `DataTable.vue` 只有 1 页用 | ✅ 现 **10** 处使用 |
| F2 | 管理页重复 `useApi`/loading/catch 样板 | ✅ 抽 `composables/`（`useApi`/`useAsyncAction` 等 8 个） |
| F3 | `LlmPage.vue` 1762 行 | ✅ 现 **39** 行 |
| F4 | `ChartWorkstation.vue` 1285 行 | ✅ 现 902 行（叠加层计算出表 + 12 例前端测试） |
| F5 | `locales/legacy/` 648 行死键 | ✅ 已删除（含死键检测） |
| F6 | `views/` 布局不一致 | ✅ 现只有 `admin/` `dashboard/` `docs/` |
| F7 | `components/admin/` 名为共享实则单用 | ✅ 已按域归位（该目录现 2 个真共享组件 + README 导航） |
| D1 | zh 侧 `promptElided` 键路径错位（中文界面显示裸键名） | ✅ 已修（zh 键路径已对齐） |

## 5. 目录约定（新增/整理的域目录）

| 目录 | 模块数 | 职责 | 文档位置 |
|---|---:|---|---|
| `scripts/trader/` | 27 | 交易员主循环各步（信号/风控/下单/记账…） | `scripts/README.md` + 各 `__init__.py` 模块表 |
| `scripts/brain/` | 10 | AI 大脑决策链路 | 同上 |
| `scripts/ledger/` | 5 | 台账行构建/清理/通知 | 同上 |
| `scripts/evolution/` | 5 | 自进化上下文、解析、报告载荷 | 同上 |
| `r20_backend/dashboard_payload/` | 21 | 看板载荷按数据域装配 | `r20_backend/README.md` + 模块表 |
| `r20_backend/routers/strategy/` `gateway/` | 5 / 6 | 超大 router 按域拆分（URL/方法/处理器名/ tags 一字未改） | 同上 |
| `r20_backend/council/` | 6 | 委员会配置 + 辩论引擎 | 同上 |
| `r20_backend/policy/` | 8 | 策略快照生成/归档/恢复 | 同上 |
| `r20_backend/llm/` | 12 | LLM 配置存储 / 能力探测 / 传输派发 | 同上 |
| `r20_backend/exchanges/` | 14 | 三所适配器 + 订单/签名/诊断 | 同上 |
| `r20_backend/execution/` | 6 | 执行闸门与路由 | 同上 |
| `frontend/src/components/dashboard/` | — | 图表与叠加层（计算逻辑出表为 `.ts`） | `frontend/README.md`、`components/admin/README.md` |

> `docs/BEIJING_TIME_CONTRACT.md`、`docs/exchange_support_matrix.md` 为既有契约文档，未改动。

## 6. 维护须知（本阶段沉淀的硬约束）

> 测试目录已于 §138 按域分子目录，约定见 `tests/README.md`（域名不得与标准库/顶层包重名已由门钉死）。

1. **抽段手法**：只搬「同一棵 AST 的语句段」，调用点用**同名关键字注入**（`name=name`），
   门面保留调用期全局查找 ⇒ `patch.object(模块, "名字")` 等既有测试接缝不失效。
2. **结构门是自动的**：`tests/extraction/test_extraction_call_site_names.py` 自动发现全部
   "参数全为 `name=name`" 的调用点（9 个门面 / 33+ 处），校验参数可解析 **且被调函数名可解析**
   —— 忘写 import 会被当场拦下。
3. **两类 pin 必须区别对待**：
   - **设计边界 pin**（如 `_holding_row` 必须是门面的 `def`、`_pos_id_seen` 跨行状态、
     接缝模块的 `PUBLIC_SURFACE`）⇒ **不可迁移**，抽段前先 grep 该段的关键局部名；
   - **行程式清单 pin**（如"dashboard 下应有 N 个 `.ts` 模块"）⇒ 可按 pin 自带提示更新。
4. **门的子进程限制**：离线套件只放行 `git show <rev>:<path>` 与 `git rev-parse --short <ref>`；
   结构检查请用纯 Python/AST，不要起 `git grep` 之类子进程。
5. **提交前跑两套件**：`unittest discover`（全量）与 `offline_suite.py`（离线/无外泄/无写盘）
   —— 守卫专属检查只在离线套件里生效。
6. **负向验证是门的一部分**：每条新门都要做"注入一处篡改 ⇒ 精确翻红"的验证；
   **在任何篡改下都绿的门等于没写**（本阶段靠这条抓出过"没牙的测试"）。
7. **子模块内部自由名也必须静态可验**（`tests/audit/test_module_free_names.py`，扫全仓 236 个模块）：
   整段搬迁若漏带模块级 import，而该模块的 IO 又包在静默 `except: pass` 里，
   会**编译通过、导入通过、单测全绿、运行时静默归零**。
   这条门正是因一次真实事故（见 §9）而补的 —— 只验"调用点完整"不够。

## 7. 剩余（刻意未做，属收益递减）

- `scripts/sync_full_ledger.py` 690（`build_lifecycle_ledger` 191）：剩余段落多为 IO 编排，
  或受设计边界 pin 约束（`_pos_id_seen` 跨行状态、官方平仓行构建）。
- `scripts/self_improvement_engine.py` 738（`run_self_evolution` 149）：剩余为 LLM 调用与落盘编排。
- `r20_backend/exchanges/binance.py` 604（`positions`/`fetch_ticker` 各 28、`place_order` 51）：
  属响应字段映射，抽取收益已不明显。
- 前端 F2 剩余样板、F7 收尾。

## 8. 未决事项（需人工拍板）

- **API 服务重启**：`uvicorn r20_backend.app:app`（PID 328199，未带 `--reload`）仍在跑旧代码；
  `routers/` 拆分（阶段 4 前期）需重启后生效 —— 未擅自重启，等你决定窗口。
- **自进化复验**：14:00 调度器的自进化复盘结果由你复查（已从代理待办中移除）。

## 9. 事故记录：主脑行情静默失败（2026-09-15，已修复）

**现象**：看板报「现价为 0 且数据 invalid，触发 P0 数据有效性拦截」，主脑每轮只做持仓风控、禁止开新仓。

**根因**：本阶段 B3 抽取 `scripts/brain/packages.py`（2026-09-14 08:41，`b53ba13`）时，
**模块级 `import json` / `import urllib.request` 没跟着搬过去**；该模块每处取数都包在静默
`except: pass` 里 ⇒ `NameError` 被吞 ⇒ 现价恒为 0 ⇒ `data_quality: invalid` ⇒ P0 拦截。

**影响窗口**：2026-09-14 08:41 → 2026-09-15 14:52（约 30 小时）。
**未影响**：本地灾备（同类缺陷在 `backup_upload.py`，但只落在未启用的百度网盘目标里）、
量子交易员的持仓风控（走 `market_data_service`，独立路径）。

**修复**：补齐 import（3 个文件）；新增全仓自由名静态门 + 白名单自检；实盘实证 15:00 周期
**`data_quality=valid` 9/9** 且主脑恢复真实决策（`UNI-USDT-SWAP: BUY_LONG @ 6.585`）。

**为什么既有防线全失效**（详版见 `plan_local/` 台账 §136）：编译/导入/单测都碰不到运行期
`NameError`；该模块当时零测试覆盖；抽取对拍门只验"段体 AST + 调用点传参 + 被调名可解析"，
**没验子模块内部自由名**；静默 `except` 让失败零信号。

**后续加固（§137）**：6 处静默取数 `except` 已接入 `scripts/market_data_health.py` 的
**失败计数 + 每类一次性告警**（只加可观测性，取值行为一字不变），并有门用"6 处全失败必须
留下 6 条痕"把这类"零信号"反过来钉死。

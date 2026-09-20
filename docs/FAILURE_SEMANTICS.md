# 失败语义手册（输入读不到时该往哪边倒）

> 适用对象：`scripts/ai_factor_trader.py` 周期链路（相位 1–5）、跨所保护加固层、
> 挂单对账/存量回收、风控与预算预留。
> 这页不是设计宣言，而是**已落地行为的清单**——每行都绑定了代码锚点与门禁用例，
> 由 `tests/audit/test_failure_semantics_doc.py` 校验：锚点路径存在、
> **锚点指向的用例是可加载的真实 TestCase**（不是注释里的同名文本）、
> 且每行的「方向」出自受认可词表（保留 / 不做 / 披露 / 吼 / 防）——
> 因此「悄悄改成静默忽略」或「悄悄改名用例」都会当场翻红。

## 0. 为什么会有这页

本仓反复出现的**同一个根因**是：

> **同一语义写在两处 ⇒ 必然漂移**；而其中危害最大的一类是
> **"读取失败被渲染成没有/为空"**（read failure rendered as absence）。

它的形状永远是三步：`读取异常 → 被 except 吞掉 → 返回空值 → 下游把"空"当成事实`
（"没有持仓""没有挂单""没有意图""没有加仓过"），于是做出**不可逆**的动作：
撤单、按孤儿回收、重复加仓、把水位/归属依据覆盖成空。

因此本页的第一条纪律不是"多打日志"，而是：**分清"没有"与"读不到"**。

## 1. 三条方向纪律

| 纪律 | 含义 | 反例（本仓真实踩过） |
|---|---|---|
| **读不到 ≠ 没有** | "文件不存在/合法为空"与"存在却读不出来"必须分开处理 | 意图文件读失败返回 `[]` ⇒ 每笔挂单失去归属 ⇒ 按孤儿**逐个撤销** |
| **不可判定 ≠ 安全** | 判不了就按"最保守的那边"倒，或至少**大声**说明未知 | 加仓上限 `scale_count=0` 缺省 ⇒ 上限静默失效（可反复加仓） |
| **日志不说谎** | 保守假设不得伪装成事实 | 追踪器缺失时复用 gate 的"已达最大加仓次数 (N/N)"文案 ⇒ 该行先声明"这是保守假设，真实情况是未知" |

**方向选择取决于动作是否可逆**：

- 动作**不可逆**（撤单、平仓、覆盖落盘）⇒ 未知时**保留现场**，宁可不动；
- 动作**可逆且有成本**（新开仓、加仓）⇒ 未知时倾向 **fail-closed（不做）**，
  但必须打印原因，避免"静默停摆"；
- 动作**只是展示/计数**（面板、提示词、配额口径）⇒ 允许继续，但必须**披露**
  （"少算/未计入"），不得让口径看起来完整。

## 2. 已落地的输入失败语义总表

<!-- anchors:begin -->
| 输入 | 读不到时 | 方向 | 代码锚点 | 门禁锚点 |
|---|---|---|---|---|
| 开仓意图 `open_order_intents.json` | 抛 `OpenIntentsUnreadable`；调用方 **不撤任何单** + fail-closed | 不可逆⇒保留 | `scripts/ai_factor_trader.py` | `tests/extraction/test_trader_order_lifecycle_extraction.py::UnreadableIntentsFailClosedTest` |
| 意图落盘 | 原子替换（mkstemp+fsync+`os.replace`），失败**保全旧文件** | 防制造坏状态 | `scripts/trader/ledger_writer.py` | `tests/extraction/test_trader_ledger_writer_extraction.py::LedgerWriterVerbatimTest` |
| 持仓追踪 `position_trackers.json` | 返回 `UnreadableTrackers`（仍是空 dict）；`save_trackers` **拒绝覆盖** | 不可逆⇒保留 | `scripts/ai_factor_trader.py` | `tests/trading/test_silent_degradation_telemetry.py::UnreadableTrackersMustNotClobberTest` |
| 加仓次数（追踪器缺该仓） | 视同**已达上限** ⇒ 本轮不加仓（fail-closed） | 可逆⇒不做 | `scripts/trader/entry_execution.py` | `tests/extraction/test_trader_entry_execution_extraction.py::test_missing_tracker_is_treated_as_cap_reached` |
| 同槽去重状态 `.ai_factor_trader_slot.json` | 告警但仍放行（不停实盘）；随后原子覆写自愈 | 可用性优先+吼 | `scripts/ai_factor_trader.py` | `tests/audit/test_audit_batch3_persistence_atomic.py::TraderSlotGuardAtomicityTest` |
| 预算预留台账 | 未核验 ⇒ **不释放**任何预留 | 不可逆⇒保留 | `scripts/trader/reservation_reconcile.py` | `tests/core/test_reservation_reconcile.py::UnverifiedSnapshotMustNotReleaseTest` |
| 挂单枚举（对账/配额） | 枚举失败 ⇒ 标记未核验；配额**少算要披露** | 披露 | `scripts/trader/cycle_stages.py` | `tests/extraction/test_trader_cycle_stages_extraction.py::QuotaUnderCountIsDisclosedTest` |
| 跨所持仓快照 | 该所被跳过时**每周期告警"未计入"** | 披露 | `scripts/trader/cycle_snapshot.py` | `tests/extraction/test_trader_cycle_snapshot_extraction.py::BrokenExecutionVenuesTest` |
| AI 决策缓存 `brain_cache` | 无有效新鲜决策 ⇒ **禁止开仓**（`continue`） | 可逆⇒不做 | `scripts/trader/entry_execution.py` | `tests/extraction/test_trader_entry_execution_extraction.py::test_missing_tracker_is_treated_as_cap_reached` |
| 因子字典形状 | 消费相位直接下标 ⇒ 基座必须提供；门禁自动核对 | 防周期中途崩 | `scripts/trader/factors.py` | `tests/extraction/test_trader_entry_execution_extraction.py::test_factor_schema_covers_every_consumer_phase` |
| 跨所保护报告 item | 渲染处直接下标 ⇒ 生产侧必须覆盖；门禁自动核对 | 防中途崩 | `scripts/trader/venue_protection.py` | `tests/trading/test_venue_protection.py::WatchdogReportShapeContractTest` |
| 保护单扫描 `scan` 字典 | 同上（加固层不该拖垮周期） | 防中途崩 | `scripts/trader/venue_protection.py` | `tests/trading/test_venue_protection.py::ScanDictShapeContractTest` |
| 仓位块形状 `f["position"]` | 消费相位（管理相，在入场之前）直接下标 ⇒ 生产侧那**一处**字面量必须覆盖；门禁自动核对 | 防周期中途崩 | `scripts/trader/factors.py` | `tests/extraction/test_trader_entry_execution_extraction.py::test_position_payload_shape_covers_manage_phase` |
| 活体数据形状（意图/追踪器文件） | **两层**：提交前只读预检（测试层）+ 周期开跑前**只读预检并打印**（运行时层，**只警告不阻断**，行为判定仍在加载侧）| 防误判/防崩 | `scripts/trader/data_shape.py` | `tests/core/test_live_artifact_shape.py::RuntimePreflightStageTest` |
| 周期披露汇总（每轮一条） | 周期收尾固定打印 `[周期披露] …`：列出本轮**凭证坏所/对账失败/形状违规/跨所保护错误**；未开闸的加固层也如实标注 | 披露 | `scripts/trader/cycle_stages.py` | `tests/trading/test_silent_degradation_telemetry.py::CycleDisclosureSummaryTest` |
| 周期披露指标（`/metrics`） | worker 落盘快照、后端读取；**快照缺失只标 `source_ok=0`，绝不发零值计数**（读不到 ≠ 很干净；未开闸时也不发错误计数）| 披露 | `r20_backend/metrics.py` | `tests/ops/test_metrics_exposition.py::CycleDisclosureMetricsTest` |
| 面板侧 JSON 读取（追踪器/多所组合） | 收敛到**共享披露读取器**：文件不存在⇒静默空态；读不出来/形状不对⇒返回空**但打印 warn 并点明"空不等于没有"** | 披露 | `r20_backend/dashboard_payload/readers.py` | `tests/ui/test_dashboard_payload_seam.py::DisclosedJsonReaderTest` |
| 台账同步旁车（跨所同步完整性） | 旁车**缺失**⇒`([],"")`（全新环境）；**损坏/过旧**（>45min）⇒ 标记不可判定并**禁本周期开仓**（用户拍板 fail-closed），同时打印原因 | 不做（fail-closed）+ 披露 | `r20_backend/execution/circuit_breaker.py` | `tests/ops/test_ledger_sync_sidecar.py::SidecarUnknownIsFailClosedTest` |
| 席位绑定**写闸**（模型库读取） | 模型库**读不出来** ⇒ 返回问题 ⇒ 调用方 `raise ValueError` **拒绝保存**；合法为空（全新环境）仍放行 | 不做（fail-closed）| `r20_backend/council/roster.py` | `tests/llm/test_council_manager.py::SeatBindingWriteGateFailClosedTest` |
| 舆情采集的熔断状态读取（**展示/提示词**侧第二份实现） | 熔断文件**损坏/读不到** ⇒ 标 `unknown` 并把 `macro_sentiment` 写成"熔断状态不可判"（**不得报平安**）；从未写过文件 ⇒ 合法"未熔断" | 披露 | `scripts/news_sentiment_harvester.py` | `tests/core/test_news_sentiment_harvester.py::CircuitBreakerUnknownStateTest` |
| 熔断判定**孪生对拍**（后端风控面 vs trader 活路径） | 两份实现只允许**形式差异**，抽取后的决策序列必须逐项相等（含 try 保护范围）；"加固只改一边" ⇒ 门翻红 | 防 | `r20_backend/execution/circuit_breaker.py` | `tests/audit/test_circuit_breaker_twin_parity.py::TwinParityTest` |
| 止损冷却**写入路径** | 只有一处实现：损坏 ⇒ **拒绝写回保全现场**（读取侧按"仍在冷却"兜底），落盘失败 ⇒ 只告警；两个公开入口必须是**薄壳** | 防 | `r20_backend/execution/cooldowns.py` | `tests/audit/test_audit_batch3_persistence_atomic.py::StopCooldownWriterSingleSourceTest` |
| 面板侧**读取器家族**（json / 文本 / 文本尾） | 三者统一失败语义：文件**不存在** ⇒ 静默默认/空；**读不出来** ⇒ 默认/空 + 一行 warn（点明"空不等于没有"）| 披露 | `r20_backend/dashboard_payload/readers.py` | `tests/ui/test_dashboard_payload_seam.py::ReaderFamilyFailureSemanticsTest` |
| 数值强转 `safe_float`（**三处**：两公开 + 一私有） | 单一事实源在 `math_utils`；三入口必须是**薄壳**且 20 组边界输入**同判**（`nan`/`±inf`/`None`/空串/列表/bool…）；`nan`/`inf`⇒`default`、`True→1.0` 属既有语义，改动须有意识 | 防 | `r20_backend/math_utils.py` | `tests/core/test_numeric_coercion_single_source.py::SafeFloatSingleSourceTest` |
| 日切键（`strftime("%Y-%m-%d")`）| 基座必须**可证明**是 +08:00：无参 `now()`／`utcnow()`／`timezone.utc`／本地时区一律翻红（日切错了不报错，只在 00:00–08:00 静默偏一天）| 防 | `scripts/trader/circuit_guard.py` | `tests/core/test_day_key_uses_beijing_tz.py::DayKeyUsesBeijingTzTest` |
| 时间戳字段单位（`_ms`=毫秒 / `_ts`=秒）| 生产与消费两侧都必须自洽；`_ms` 字段写 `*1000`、`_ts` 字段写秒；已知例外（`last_sync_ts`）必须有**伴生不变式**（写入者全为毫秒或 None，绝不混入秒）| 防 | `tests/core/test_timestamp_unit_convention.py` | `tests/core/test_timestamp_unit_convention.py::TimestampUnitConventionTest` |
| 下单量换算的方向（币数 / 张数）| **两条分支一律向下取整**（币数截断到 step、张数 `floor`）⇒ 换算名义**永不超出**目标；与实盘路径 `quantize_size` 同向。取整代价：可能更常低于最小张数而被拒（少下单，不超买）| 不做（fail-closed）| `r20_backend/exchanges/base.py` | `tests/venues/test_exchanges_adapter.py::ContractsRoundingBoundTest` |
| 原子写辅助与敏感状态文件 | 凡 `_atomic_write*` 必须**临时文件 + fsync + 原子替换**（否则 rename 后断电可留空/截断）；`circuit_breaker.json` 等**读者按默认值降级**的文件**不得**被直写 | 防 | `r20_backend/policy/io.py` | `tests/core/test_atomic_write_invariant.py::AtomicWriteInvariantTest` |
| 文档引用 ⇒ 必须已提交 | 失败语义手册与**全仓文档**引用的**源码树**路径，"磁盘上存在即必须被 git 跟踪"（`data/`、`plan_local/` 等运行态不在范围）——防 `.gitignore` 过宽造成"磁盘有、仓库无"而本地测试照常绿 | 防 | `tests/audit/test_doc_paths_are_committed.py` | `tests/audit/test_doc_paths_are_committed.py::BroadDocReferencesTest` |
| `.gitignore` 裸词 vs 源码目录 | 裸词模式（无 `/`、无 `.`、无 `*`）按**路径组件**匹配，会静默吞掉同名源码目录（`core` 吞掉 `tests/core/` 的事故根因）——与源码树里真实存在的目录同名即翻红 | 防 | `tests/audit/test_gitignore_bare_word_collisions.py` | `tests/audit/test_gitignore_bare_word_collisions.py::GitignoreBareWordCollisionTest` |
| 保护腿触发价类型（`tp/slTriggerPxType`）| 入场附着路径**不发送**该字段（类型由交易所默认决定，仓内**未核实**该默认值 ⇒ 不写死）；调用方可用 `attach_algo_ords` 显式指定并被取值域校验；修正路径 `amend_algo_sl` 透传类型 ⇒ **同一条腿可能被修正改变触发语义**，而面板不显示当前类型 | 披露 | `scripts/okx_rest.py` | `tests/venues/test_okx_trigger_price_type_semantics.py::TriggerPriceTypeSemanticsTest` |
<!-- anchors:end -->

## 3. 抽取门与"文档化差异"

本仓把大函数**纯搬家**到子包，并用 AST **逐字**门钉住"搬家不是重写"。代价是：
有意的行为修复会被门拦下。处理方式**不是**放宽门，而是登记**文档化差异**：

- 门内维护 `DELTA_*` 表（`新文本 → 旧文本`），比较前先把新文本还原成旧文本；
- **锚点必须唯一**（计数自检）；表外的任何改动照旧翻红；
- 已具备差异机制的门：`tests/extraction/test_reservation_reconcile_extraction.py`、
  `test_trader_cycle_stages_extraction.py`、`test_trader_order_lifecycle_extraction.py`、
  `test_trader_ledger_writer_extraction.py`、`test_trader_entry_execution_extraction.py`。

原则：**冻结要可审计，不要不可维护**——没有差异机制的门会让实盘修复永远进不去
（入场循环此前正是如此）。

## 4. 形状契约门（防"周期中途 KeyError"）

周期链路里大量使用 `x["key"]` **直接下标**（非 `.get`）。键只由单一生产者的字面量提供；
一旦基座少一个键或出现第二个生产者，就会在**周期中途**抛 `KeyError`
⇒ 其后的落盘/台账/面板相位**整段跳过**。

门禁做法（`tests/source_scan.py` 提供共享扫描器）：

1. 从**生产侧**收集字面量键；从**消费侧**收集 `var["常数"]` 的**读取**下标；
2. 断言 `需要 ⊆ 提供`，失败信息给出**文件:行号 + 缺哪些键 + 后果**；
3. 自带**失效自检**（断言"确实抓到了预期键"），防止空集恒过；
4. 消费相位/消费点**登记成清单**——新相位消费同一形状时补登记（否则漏检）。

两个已实测的坑（Python 3.11）：

- **f-string 内的表达式不是 AST 节点**（3.12 起才是）⇒ 必须 AST + 文本双扫；
- 文本扫描**分不清读/写** ⇒ 必须减掉函数**自己写入**的键。

## 5. 验证纪律

- **反向验证（negative proof）**：修完不只看"绿"，还要**临时破坏该不变量**，
  确认对应的门**当场翻红**，再恢复。没有反向验证的"绿"不算证据。
- **失效自检**：任何"从代码推导判据"的门都要断言"我确实抓到了东西"。
- **如实登记证伪**：假设被推翻时照样写进提交/台账（例如"`pos_sz` 类型冲突不可达"、
  "扫描路径 item 永远进不了 actions"），避免下一个人重新怀疑同一处。
- **门禁必须跑在"将要提交的那棵树"上**（2026-09 事故条款）：`pytest` 读的是**工作树**，
  被 `.gitignore` 吞掉的新文件照样能让本地全绿，而仓库里什么都没有 —— 本仓真实发生过：
  `.gitignore` 一行**裸 `core`**（本意是忽略仓库根的崩溃转储）按"路径组件"匹配连带忽略了
  `tests/core/`，5 个新门长期未提交，其中一个提交只改了本文档却在信息里宣称新增了那个门。
  提交前逐项确认：`git status --short` 干净、每个新文件 `git ls-files --error-unmatch` 能对上、
  提交信息点名的文件确实出现在 `git show --stat`。
- **引用即提交**：文档里引用的源码路径必须"存在 ⇒ 已被跟踪"，
  由 `tests/audit/test_doc_paths_are_committed.py` 强制（含全仓文档的宽扫描）。
  新的仓库级纪律要写进**被跟踪的**文档 —— 注意 `AGENTS.md` 在本仓是被**有意忽略**的本地文件
  （`.gitignore` 里与 `SOUL.md`/`MEMORY.md` 同属"agent 运行态"块），写在那里不随仓库走。
  `.gitignore` 的**裸词**模式另有专检：`tests/audit/test_gitignore_bare_word_collisions.py`
  （裸词按路径组件匹配，会静默吞掉同名源码目录 —— `core` 事故的根因）。

## 6. 明确残留（未决，需人工拍板）

| 项 | 现状 | 为什么没动 |
|---|---|---|
| 凭证已死场所是否拦新开仓 | 仅披露，不拦 | 与既有审计结论冲突（拿凭证错误拦全链＝交易停摆）|
| 跨所保护 watchdog（G8）开闸 | 代码就绪（预演 + 防抖 + 形状已钉），**默认关闭** | 属实盘行为变更，需人工置 env 并重启 worker |
| 意图文件**被删**且仍有在场挂单 | 仍按"没有意图"处理 | 需独立的"挂单在场 + 文件缺失"判定，未开刀 |
| 允许名单外的实盘持仓是否进风险视图 | 现状保留 | 属风控口径变更，需人工拍板 |
| 入场保护腿的**触发价类型**（`tp/slTriggerPxType`）| attach 路径**不发送**该字段 ⇒ 类型由交易所默认决定；修正路径（amend）**支持并校验** `last/index/mark`；面板不显示当前类型 ⇒ 操作员无法分辨 | ⚠️ 本仓**未核实**交易所默认值（本环境 web 搜索不可用，不凭记忆写死）⇒ 先登记；改 `mark`/显式钉 `last` 都属**实盘行为变更**，需人工拍板 |

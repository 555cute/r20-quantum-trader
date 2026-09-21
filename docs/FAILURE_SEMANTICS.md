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
| 时间戳字段单位（`_ms`=毫秒 / `_ts`=秒）| 生产与消费两侧都必须自洽；`_ms` 字段写 `*1000`、`_ts` 字段写秒；**例外表已清空**（第一百六十九刀把最后一个 `last_sync_ts` 正名为 `last_sync_ms`：值一直是毫秒、名字在说谎 ⇒ 按「名字即语义」改名，而非改值去迁就错名）；旧名**不得回流**，显式 `None`（未知/尚未同步）允许、秒值必红 | 防 | `tests/core/test_timestamp_unit_convention.py` | `tests/core/test_timestamp_unit_convention.py::TimestampUnitConventionTest` |
| 下单量换算的方向（币数 / 张数）| **两条分支一律向下取整**（币数截断到 step、张数 `floor`）⇒ 换算名义**永不超出**目标；与实盘路径 `quantize_size` 同向。取整代价：可能更常低于最小张数而被拒（少下单，不超买）| 不做（fail-closed）| `r20_backend/exchanges/base.py` | `tests/venues/test_exchanges_adapter.py::ContractsRoundingBoundTest` |
| 原子写辅助与敏感状态文件 | 凡 `_atomic_write*` 必须**临时文件 + fsync + 原子替换**（否则 rename 后断电可留空/截断）；`circuit_breaker.json` 等**读者按默认值降级**的文件**不得**被直写 | 防 | `r20_backend/policy/io.py` | `tests/core/test_atomic_write_invariant.py::AtomicWriteInvariantTest` |
| 文档引用 ⇒ 必须已提交 | 失败语义手册与**全仓文档**引用的**源码树**路径，"磁盘上存在即必须被 git 跟踪"（`data/`、`plan_local/` 等运行态不在范围）——防 `.gitignore` 过宽造成"磁盘有、仓库无"而本地测试照常绿 | 防 | `tests/audit/test_doc_paths_are_committed.py` | `tests/audit/test_doc_paths_are_committed.py::BroadDocReferencesTest` |
| `.gitignore` 裸词 vs 源码目录 | 裸词模式（无 `/`、无 `.`、无 `*`）按**路径组件**匹配，会静默吞掉同名源码目录（`core` 吞掉 `tests/core/` 的事故根因）——与源码树里真实存在的目录同名即翻红 | 防 | `tests/audit/test_gitignore_bare_word_collisions.py` | `tests/audit/test_gitignore_bare_word_collisions.py::GitignoreBareWordCollisionTest` |
| 保护腿触发价类型（`tp/slTriggerPxType`）| **显式发送 `mark`（标记价触发）**：入场附着腿与云端棘轮腿（`place_algo_oco`）同一口径，不再依赖未核实的交易所默认值；`last`/`index` 可显式覆盖，修正路径仍可改类型（会改变触发语义）| 防 | `scripts/okx_rest.py` | `tests/venues/test_okx_trigger_price_type_semantics.py::TriggerPriceTypeSemanticsTest` |
| 保护腿触发价类型（展示口径）| 两个生产者（OKX `algo_protection` / 跨所 `multi_venue`）必须**同名同三态**输出 `protectionSl/TpTriggerPxType`：取值含 OKX `mark/last/index`、Binance `mark_price/contract_price`（交易所自描述字面量）、Gate `price_type:<码>`（**原样不解释** —— 官方映射本仓未核实）；读不到 ⇒ `"unknown"`、该类腿不存在 ⇒ `None`、上报值原样透传；提示词如实转述（未上报就说未上报）；**前端**展示同样分四态、`null` 不显示（不得把「未上报」画成「标记价」，也不得把「没有该类腿」画成「未上报」，由 `frontend/tests/protectionTriggerType.test.mjs` 盯住）| 披露 | `r20_backend/dashboard_payload/multi_venue.py` | `tests/ui/test_protection_contract.py::TriggerTypeDisclosureTest` |
| 孤儿保护腿的清理边界 | 只撤 `attribute_protective_orders` 的 `orphan_attributed`（证据 `tag`＝本方标签 / `ledger`＝台账同向同量已平）；`orphan_unattributed`/`side_mismatch`/`size_mismatch` **一律不碰**（可能是用户手单）；该合约**仍有活动持仓** ⇒ 整合约跳过；逐腿按 **id** 撤，绝不用「按合约撤全部」| 防 | `scripts/trader/venue_protection.py` | `tests/trading/test_venue_protection.py::CancelOrphanAttributedLegsTest` |
| 台账取证（归属层的 `ledger` 档）| 只读台账行供归属取证：文件不存在 / JSON 坏 / 结构认不出 ⇒ **`None`＋告警**＝不产生证据（腿留在「归属不可判定」⇒ 绝不自动撤）；**绝不**把读失败当空台账（那会把可证明的腿降级，取反则可能撤掉用户手单）| 防 | `scripts/trader/venue_protection.py` | `tests/trading/test_venue_protection.py::LedgerEvidenceTest` |
| 孤儿腿候选进面板（只报告）| 面板载荷 `protectionOrphans`：`attributed`（证据 tag/ledger ⇒ **可复核候选**）与 `unattributed`（一律不碰）分列；读腿失败 ⇒ `readable:false`＝**不可判定**（不是「没有孤儿腿」）；取证依据 `ledgerRows` 如实说明 ok/unavailable；面板**不得**出现任何撤销调用 | 披露 | `r20_backend/dashboard_payload/multi_venue.py` | `tests/ui/test_protection_contract.py::OrphanCandidatesPayloadTest` |
| 孤儿腿的可观测与提示| `/metrics`：`r20_protection_orphans_readable{venue}` + 可判定时才发 `..._candidates`/`..._unattributed`/`..._ledger_evidence`（**读不到不发计数**，不可判定≠0）；提示词按所**只提示一次**并点明"同币再开仓可能按旧触发价减仓"；两处都只报告、**绝不**暗示会自动撤 | 披露 | `r20_backend/metrics.py` | `tests/ops/test_metrics_exposition.py::ProtectionOrphanMetricsTest` |
| 触发价类型与保护判定**分开回答** | 类型回答「按什么价触发」；保护状态回答「腿在不在/量够不够/活不活」。类型**不得**进入任何判定条件：未上报 ⇒ 只 disclose `unknown`，**不**降级保护状态；类型是 `mark` 也**不**给覆盖背书（防过度保守与虚假安心两个方向）| 防 | `scripts/trader/venue_protection.py` | `tests/trading/test_trigger_type_verdict_boundary.py::TriggerTypeStaysOutOfVerdictsTest` |
| 已过期 ≠ 覆盖 | 到期时间**确知已过**的腿不得计入 `covered_size`、不得算 `has_live_sl`、不得让整仓平腿补满覆盖（否则 `protected_now=True`、`needs_repair=False`，审计只报 renew 而**不进 critical** ⇒ 裸奔仓位被报成已保护）；`never`（显式 0/GTC）算活且不复验；到期**缺字段**算活但必须 `needs_verify`（不可判定≠安全，也不许过度报警）| 吼 | `scripts/trader/venue_protection.py` | `tests/trading/test_venue_protection.py::ExpiredLegIsNotCoverageTest` |
| 覆盖链的方向判据只有一处 | 覆盖必须用 `_leg_position_side`（真单核对过 Gate `auto_size`/`direction` 与 Binance `side`），**不得**再写第二份只看 `side` 的比较：真机实测 Gate 6/6 条腿读不出 `side` ⇒ 旧过滤对 Gate **完全失效**，平空腿会被算进多仓覆盖（attribution 早在报 side_mismatch，只有覆盖链在瞎）；方向**读不出**时沿用既有"照旧计入"口径（残留口子已登记：接入不报方向的所须改判 `coverage_unknown`）| 防 | `scripts/trader/venue_protection.py` | `tests/trading/test_venue_protection.py::LegDirectionIsCoverageTest` |
| 串币覆盖（合约匹配）| 覆盖必须**只**统计本仓合约的腿：`_base_of` 先剥合成 id 的场所前缀（`GATE:BTC_USDT` → `BTC`，否则一开过滤就全丢 ⇒ **假缺口** ⇒ 重复挂腿），再按**归一后前缀相等**比较（`WBTCUSDT` 不得当成 `BTC`）；`ensure`/审计两个写单相关站点都显式 `require_symbol_match=True`（不假定适配器会尊重 symbol 参数）| 防 | `scripts/trader/venue_protection.py` | `tests/trading/test_venue_protection.py::ContractMatchRobustnessTest` |
| 覆盖不足必须被看见 | 有活止损但**量不够/方向不覆盖** ⇒ `would` 取 `repair` 且进 `would` 列表（此前只看 `has_live_sl` ⇒ 落成 `noop`，既不进 `critical` 也不进 `would` ⇒ 静默缺口）；`critical` 语义**不变**（＝完全没有止损腿）| 吼 | `scripts/trader/venue_protection.py` | `tests/trading/test_venue_protection.py::ContractMatchRobustnessTest` |
| 归属不符的腿要看得见（两种语义分开）| 面板载荷/提示词/指标都要披露 mismatch 腿，且**语义必须分开**：`sideMismatch`（方向与本仓不符 ⇒ **不计入覆盖**，反向腿保护不了本仓）与 `sizeMismatch`（量对不上任何持仓 ⇒ **正被计入覆盖**，归属存疑，价格触及仍会减仓）；指标用**两个名字**（一个名字只能有一个 HELP，共用会把两种语义混成一个数）；读腿失败 ⇒ `readable:false`，两边都不得给「0 条」的假精确 | 披露 | `r20_backend/dashboard_payload/multi_venue.py` | `tests/ui/test_protection_contract.py::MismatchLegsDisclosureTest` |
| 一个指标名一个语义 | `emit` 用 `setdefault` 登记 family ⇒ 同名第二次带不同 HELP **不会出现在输出里**（静默丢失）；标签集不一致同属非法 exposition。不同语义必须**不同名字**（门用 AST 扫 `r20_backend/metrics.py`：同名多 HELP/TYPE/标签集即红）| 防 | `r20_backend/metrics.py` | `tests/ops/test_metrics_name_semantics.py::MetricsNameSemanticsTest` |
| 认不出的腿也要看得见 | `foreign`（认不出类型）/`unparsed`（行解析不了）的腿**不计入覆盖** ⇒ 若其实是保护腿，覆盖被**低估**（可能触发重复挂腿）。两者语义不同 ⇒ 载荷/提示词分开给数、指标各自一个名字；读腿失败 ⇒ 不给「0 条」的假精确 | 披露 | `r20_backend/dashboard_payload/multi_venue.py` | `tests/ui/test_protection_contract.py::UnclassifiedLegsDisclosureTest` |
| 首值优先的登记表不得静默任意 | 同币多合约时 `pool_by_base.setdefault(base, iid)` 让「选哪个合约」取决于配置**排列顺序**（改一行配置就换下单标的）⇒ 改为与顺序无关的确定性优选（USDT 永续优先，其次字典序）；真机核对当前 9 个目标合约同币重复为 0（潜在风险，非现行错误）| 防 | `scripts/ai_brain_trader.py` | `tests/audit/test_audit_config_p4_cleanup.py::InstrumentPoolTrustTests` |
| 同币多仓必须披露 | 归属层 `pos_by_base.setdefault(base, p)` 在同币**多仓**（对冲模式/异常数据）时只留首个 ⇒ 腿会全对到那一个仓位上、另一侧**静默消失**；现披露为 `ambiguous_positions`（审计 attribution 段返回，运营可在预演接口看到）| 披露 | `scripts/trader/venue_protection.py` | `tests/trading/test_venue_protection.py::AmbiguousPositionsTest` |
| 指标名的键只能有一种拼法 | `market_data_service` 原有**三种**规范化（MCP 去横杠、REST 保留横杠、本地计算去横杠与下划线）⇒ 同一指标可能有两个键：读者按一种取值而生产者按另一种存（读不到≠没有），且 `missing` 判定失明 ⇒ 每轮白算本地指标。现统一到`_indicator_key`（唯一一处，源码扫描门钉住）| 防 | `scripts/market_data_service.py` | `tests/audit/test_indicator_key_single_spelling.py::IndicatorKeySingleSpellingTest` |
| 币种基名只有一处实现 | `canonical_base` 违背自身 docstring（"任意写法→裸币种"）：合成 id 得 `GATE:BTC`、`BTC_USDC` 得 `BTCUSDC`；`_base_of`/`_leg_symbol` 各写一份 ⇒ 三处互相矛盾（按币种匹配静默失配）。修法：`canonical_base` 补前缀剥离与计价币剥离，两处**委派**给它；且池映射必须加"标准形态"闸，否则币本位 `BTC-USD-SWAP` 会因币种相同被换到池内 `BTC-USDT-SWAP`（换下单标的）| 防 | `r20_backend/exchanges/base.py` | `tests/audit/test_symbol_base_consistency.py::BaseNameConsistencyTest` |
| posSide 比较必须净持仓容错 | OKX 净持仓（one-way）账户返回 `posSide="net"`，精确相等会**假阴性**：云端止损找不到活单 ⇒ 收紧静默失效（`cloud_protection`/`position_mgmt`）；平仓核验匹配不上 ⇒ `remaining` 保持 0 ⇒ **仓位还开着却宣称已平**（`venue_query`）。统一为 `in {pos_side, "net"}`；自洽站点（两侧都取自 OKX 自身）与"是否显式侧向"判断列入带理由的允许清单；门按 AST 扫描全仓 | 吼 | `scripts/trader/venue_query.py` | `tests/audit/test_posside_net_convention.py::PosSideNetConventionTest` |
| posSide 缺失默认值必须一致 | 缺字段时默认 `"net"`（全仓 11 处），不得写 `""`：`""` 与 `"net"` 配不上 ⇒ 明明有仓位却"匹配不到"（净持仓模式更易触发）。回退链末端（`side → posSide → ""`）与展示文案（`"—"`/`"long"`）以及"上游模式闸已拦"的位置列入带理由的允许清单；另钉住 OKX **刻意**的端点字段差异（close-position 用 `mgnMode`、下单用 `tdMode`，统一则 400）| 防 | `r20_backend/okx_trade_service.py` | `tests/audit/test_posside_net_convention.py::PosSideDefaultConsistencyTest` |
| 秒/毫秒分界只有一处实现 | 判据 `>= 1e11 ⇒ 毫秒` 此前有**四处**写法（`time_utils.parse_beijing`、`multi_venue`、`venue_protection` 的函数版与内联版）⇒ 改一处忘三处；且分界写成 `1e9` 会把 epoch 秒误判成毫秒再除 1000（"还剩 7 天"算成"已过期"，本仓真实踩过）。唯一实现＝`r20_backend/time_utils.py` 的 `EPOCH_MS_THRESHOLD`/`to_seconds`/`to_millis`，其余委派；门按 AST 钉住"数值常量 `1e11` 只许出现在该文件"| 防 | `r20_backend/time_utils.py` | `tests/audit/test_epoch_unit_converter.py::EpochUnitConverterTest` |
| 腿的合约可能在嵌套字段，不许只读扁平键 | Gate 保护腿的合约在 `initial.contract`（真机核对：扁平 `symbol` 为空）⇒ 接线层只读 `l.get("symbol")` 会**静默排除全部 Gate 腿**，「平仓后撤掉可证明属于自己的腿」这条链从未覆盖 Gate（与线上遗留腿一致）。改用腿基名访问器（依次探 `initial.contract`/`contract`/`symbol`/`raw.*`）；同时 `canonical_inst` 补齐场所前缀剥离，与 `canonical_base` 同口径（一致性门含四个提取器）| 防 | `r20_backend/execution_router.py` | `tests/trading/test_close_cancels_protection.py::RouterCloseTest` |
| 定义不得写在 `if __name__` 块内 | 粘在 `unittest.main()` 之后且保持缩进 ⇒ 该定义成为`if` 块体里的嵌套函数：语法合法、AST 正常、**pytest 永不收集**。第一百八十九刀真实发生：新用例一个都没跑，而"反向验证"依旧全绿（差点据此宣称验证通过）。门按 AST 钉住"块内不得有 def/class"，并以**真跑 pytest --collect-only** 证明块内用例收集数为 0（正常缩进者为 1）| 防 | `tests/trading/test_close_cancels_protection.py` | `tests/audit/test_defs_not_inside_main_block.py::DefsNotInsideMainBlockTest` |
| 撤腿必须按能力探针 | 三所撤腿能力不同：`cancel_price_order` **只有 Gate** 有、`cancel_algo_order` **只有 Binance** 有。跨所孤儿腿清理此前直接调 `cancel_price_order` ⇒ 对 Binance 恒 `AttributeError`（腿留在场内；而 Binance 恰是孤儿腿最多的所，真机 17 条）。统一为 `cancel_protective_leg` 探针（price→algo→order），并把本文件另一处的重复阶梯也并入；三者全无 ⇒ 报错（不许静默当作已撤）| 防 | `scripts/trader/venue_protection.py` | `tests/trading/test_venue_protection.py::CancelOrphanAttributedLegsTest` |
| 按所分流的能力调用必须带守卫 | 三所适配器能力不一致（`cancel_price_order` 仅 Gate、`cancel_algo_order` 仅 Binance、`list_open_orders` 仅 Gate…）⇒ 直接调某所专有方法会在另一所 `AttributeError`，而这类失败常被 `except` 吞掉（"做不到"静默变"没有"）。门在测试期实算能力矩阵，要求每个此类调用具备**结构化守卫**：`hasattr` 探针／按所分流比较／接收者由 `get_adapter("<venue>")` 绑定（且该所确有该方法）/`self.` 内部调用 | 防 | `scripts/trader/venue_protection.py` | `tests/audit/test_venue_capability_calls.py::VenueCapabilityCallsTest` |
| 声明了持仓模式就必须能探测 | 模式闸判据是 `if declared_modes and callable(probe)` ⇒ **声明了模式却没有探测方法**的所被整段跳过：看起来有护栏，实际没有。OKX 此前正是如此（全仓唯一无探测的所，而它恰是持仓最多的所）。补 `interpret_position_mode` + `detect_position_mode`（**只读** `GET /api/v5/account/config`，失败 ⇒ `unknown` ⇒ 禁开仓）并显式声明 `entry_ready_position_modes=("long_short",)`（净持仓载荷未核验 ⇒ 不列入准入）| 吼 | `r20_backend/exchanges/okx.py` | `tests/venues/test_position_mode_guard.py::ModeDeclarationConsistencyTest` |
| 能力声明必须被执行或登记出处 | `ExchangeCapabilities` 有 **11 个字段在生产代码里零读者**（`order_id_type`/`signed_size`/`conditional_family`/`supports_attached_tp_sl`/`protection_semantics`/`rate_limit_note`…）—— 声明本身不产生行为，而「声明了」很容易被后来的读者当成「有护栏」（doctrine：声明≠执行）。门要求每个字段**二选一**：有生产读者（`x.<field>` 或 `getattr`；声明行不算，按 (字段,行号) 精确比对），或登记**行为真正落在哪里**；且登记表有防腐（已出现读者⇒登记过期）| 防 | `r20_backend/exchanges/base.py` | `tests/audit/test_capability_declarations_are_classified.py::CapabilityDeclarationsClassifiedTest` |
| 代码会读的环境键必须在模板里可发现 | `env.example` 是操作者唯一清单：代码会读、模板不提 ⇒ 开关无法被发现（安全开关尤其致命）。实测 71 个环境访问点里 **24 个键完全缺席**，含预演档 `R20_VENUE_PROTECTION_WATCHDOG_DRY_RUN`（1=只报不做／0=真实写单）、防抖窗口、价格理智闸 `R20_MAX_PRICE_CROSS_PCT`/`FAR_PCT`、台账总闸 `R20_LEDGER_SYNC_DISABLED`。已全部补进模板（仅占位与**安全档**默认，绝不写本机真实值），并加门：AST 收集真实环境访问点 ⇒ 每键须在模板可发现；另钉「安全开关必须写明档位语义」| 防 | `env.example` | `tests/audit/test_env_keys_are_documented.py::EnvKeysAreDocumentedTest` |
| 模板声明的键必须真的被消费 | 反向方向：`env.example` 里的键若无代码消费，操作者拧的是**什么都不做的旋钮**（配置假象，比没有更坏）。判据必须覆盖本仓三条真实通道：直接读、**键表**（`settings_store.MANAGED_KEYS` 存键名，凭证键走它）、**f-string 派生**（`f"R20_NOTIFY_{channel.upper()}_ENABLED"`，通知开关走它）；并**排除 docstring**（把键名写进文档字符串不算代码会用它）。实测模板 90 键**全部被消费**（0 个假旋钮）| 防 | `env.example` | `tests/audit/test_env_keys_are_documented.py::TemplateKeysAreConsumedTest` |
| TS 契约声明的载荷字段必须真的被后端发出 | 跨层静默空洞：`frontend/src/types/dashboard.ts` 声明、`stores/dashboard.ts` 直接读**载荷根**，而后端那一层根本没有。实测两处：① `is_stale` **全仓从未发出** ⇒ `?? false` 恒假，面板陈旧分支只剩 `status==='STALE'` 一条腿；② `macro_assessment` 只发在 `ai_brain_history[0]`（真机缓存有真内容）⇒ 根级永远取不到、面板宏观一行永远显示「扫描中…」。修复：后端按契约在根上发出两者（`is_stale` 由同一状态词表推导；`macro_assessment` 取最新脑内记录的同源别名），**陈旧分支是拷贝故必须显式改真** | 防 | `r20_backend/dashboard_payload/cache_payload.py` | `tests/audit/test_payload_contract_cross_layer.py::PayloadContractCrossLayerTest` |
| 行契约的字段必须真的被后端产出 | 根级契约的同类扫描下沉到**行**（持仓/挂单/账户/因子）。实测：① `scaleOutPhase`/`scaleOutTp` 被面板读来决定切分徽标与 TP，而后端**从未发过**（数据一直在 tracker 的 `scale_out_phase`/`scale_out_tp` 里）⇒ 徽标永远不亮；② `c_1h_ret` 被图表当无蜡烛时的兜底乘出来 ⇒ 把「没有行情」渲染成精确的 +0.00%（读不到 ≠ 没有）⇒ 改为占位 `--`；③ 死声明/错名声明 `trend_direction`、`margin_ratio`、`margin_source`（后端真键是驼峰 `marginSource`）、`c_1h_ret` 一律删除并留痕。修复：`enrich_position_risk_fields` 从 tracker 接出切分状态，**缺席即缺席**（不写 0）| 防 | `r20_backend/dashboard_payload/factors.py` | `tests/audit/test_payload_contract_cross_layer.py::RowContractCrossLayerTest` |
| 环境键扫描必须覆盖 env dict 传参读法 | **门自己的假绿**：本仓大量代码把环境做成 dict 传参（`env.get("R20_XXX")`，见 `notifications.py`、`routers/gateway/*`），而第一版扫描器只认 `os.getenv`/`environ`/`_env_*` ⇒ 这类读点**完全隐形**，实测漏 16 键，其中 4 键（`OKX_BASE_URL`、`R20_TELEGRAM_API_BASE`、`R20_DINGTALK_SECRET`、`R20_FEISHU_SECRET`）**连模板都没有**却门是绿的。修：接收者名字表 `env/environment/env_vars/…` 也算环境读取，并把该读法钉成牙齿用例；补 4 键进模板（语义取自代码：OKX 基址默认 okx.com、Telegram 默认 api.telegram.org、钉钉/飞书留空=不加签）| 防 | `env.example` | `tests/audit/test_env_keys_are_documented.py::EnvKeysAreDocumentedTest` |
| 配置扫描必须覆盖全部代码根与动态档位读法 | 第二百刀自查出**两个召回缺口**：① 扫描范围只写了 `scripts/`+`r20_backend/`，而本仓还有 **`r20_gateway/`**（网关：凭证库、发布器、任务存储）与 `plugins/` ⇒ 那两处的读点隐形，`R20_GATEWAY_DB`、`R20_ALLOW_TEST_PUBLISH`、`R20_JOB_RUNS_KEEP_DAYS` **模板里没有而门是绿的（假绿）**；② 凭证键是**动态档位**取的（`registry.py` 按 `[f"{key}_DEMO", f"{key}_TESTNET", f"{key}_SANDBOX", key]` 与 `[f"{key}_LIVE", key]` 取 `f"{tier}_API_KEY"`），不建模就误报 **18 个假死键**。修：扫描根列全（并加「漏根即判红」的牙齿；隐藏目录与构建产物排除）、配置表门按**后缀+场所白名单**判定档位链（`BINANCE_TESTNET_API_KEY` 按 `_` 切分末 token 是 `KEY` 不是 `API_KEY`——第一版这么错判过）。三张表（`MANAGED_KEYS`/`SECRET_KEYS`/`RISK_ENV_KEYS`）逐键要求可消费，**表自身那行不算证据** | 防 | `env.example` | `tests/audit/test_config_tables_are_consumed.py::ConfigTablesAreConsumedTest` |
| 扫描型门的判据范围必须显式且完整 | 上一刀的「范围缺根」是**一类**失效，不是一次事故：全仓 13 道扫描型门里，**11 道**当时只扫 `scripts/`+`r20_backend/`（漏 `r20_gateway/` 与 `plugins/`）—— 那些地方的违规它们看不见。本轮把 11 道门的根全部扩到四个代码根（epoch 单位、北京时间日键、原子写、defs-in-main-block、posSide 约定、能力声明、能力调用、模块自由名、doc 路径、gitignore 裸词、环境键/配置表），扩后**全部通过**（= 网关与 plugins 本就合规，覆盖面得到确认）。并加**元门**：任何声明了扫描根的测试，只要扫了至少一个代码根，就必须扫**全部**代码根（否则登记豁免并写理由）；代码根**动态推导** ⇒ 将来新增代码根会把所有扫描门一次性判红，逼维护者扩范围 | 防 | `tests/audit/test_scan_scope_is_explicit.py` | `tests/audit/test_scan_scope_is_explicit.py::ScanScopeIsExplicitTest` |
| 门的判据要抽成可测函数并造牙齿 | 本会话反复吃到「判据能不能咬只能靠人读」的亏，本轮把两条**源码级规则**抽成纯函数（`adhoc_normalization_offenders(src)`、`compare_extractors(extractors, spellings)`）并补牙齿用例：合成一份「第二处指标名规范化」、一个「漂移的提取器」、一个「抛错的提取器」（抛错也必须算不一致 —— 读不到 ≠ 没有），同时保留真代码断言（规则仍在**真文件**上判）。牙齿均经**真代码反向验证**：真加第二处实现 ⇒ 判红；让 `_base_of` 不再委派 ⇒ 判红。另**自查出我自己写的假断言**（把要断言的字拼进了被检查的字符串 ⇒ 恒真），已改为断言具体行号 | 防 | `tests/audit/test_indicator_key_single_spelling.py` | `tests/audit/test_symbol_base_consistency.py::BaseNameConsistencyTest` |
| 不许有恒真断言 | 上一刀我自己写出假牙齿（把要断言的关键字拼进被检查的字符串 ⇒ 恒真），本轮当**一类**问题全仓扫，实测 **4 处真恒真**（3 个文件）：`assertIn(该所预算, 该所预算 120U)` （字面量自证）、`assertTrue(True)`（`except` 分支本身就是断言，改为控制流注释）、`assertIn(TODAY, TODAY)`/`assertEqual(TODAY, TODAY)`（改为常量前提断言）、并把 p1b 那处升级为**捕获真日志**（要求夹仓消息点名上限，让用例名成真）。判据三条：两侧同一纯表达式（**仅当两侧无函数调用**，否则会误杀纯度检查）、`assertTrue/False(常量真值)`、字面量自证（被检查表达式的**非调用部分**已含该子串）；启发式误报面如实登记（字面量只出现在下标键名里属误报）| 防 | `tests/audit/test_no_vacuous_assertions.py` | `tests/audit/test_no_vacuous_assertions.py::NoVacuousAssertionsTest` |
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


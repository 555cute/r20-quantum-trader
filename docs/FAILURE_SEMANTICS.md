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
| 用例不许看着在测其实永远绿 | 两种形态：**吞掉异常**（`try: … except Exception: pass` 把断言抛错也吃掉）与**整条无断言证据**。实测结论是**假设被推翻**：吞异常全仓只有 **1 处**且是有意的（`test_sync_web_data_single_read.py` 要在被拦下的写盘异常之后继续断言「每个数据文件恰好读一次」，注释已写明，try 之后仍有真断言）⇒ 登记而非改造；无断言用例初版报 15 条，逐条看全是**误报**（断言在 helper 里如 `_assert_rows`/`_check`，或属「必须不抛错」型`never_raises`/`do_not_crash`/`kill_switch`/`..._import`）⇒ **精化判据后剩 0 条**。门把两种形态钉死：新用例若吞异常或无断言证据，必须登记理由才能过；三次判据精化的成果（helper 委派、必须不抛错型、具体异常类型的有意过滤）全部钉成不得误杀的用例 | 防 | `tests/audit/test_tests_cannot_pass_vacuously.py` | `tests/audit/test_tests_cannot_pass_vacuously.py::TestsCannotPassVacuouslyTest` |
| 跳过必须有理由、有账、不能静默吃掉覆盖面 | skip 是「覆盖面静默消失」的藏身处（用例永远绿因为压根没跑）。实测 37 个跳过点：**没给理由的 0 个**；调用点无条件的 14 个**全部**是 `skip_if_offline_suite()`（条件 `OFFLINE_SUITE_RUNNING` 在 helper **内部**，只有「离线守护基线」那一轮置位，普通 pytest 不置位）⇒ **真正永远不跑的用例 0 条**；本容器实际只 skip 1 条（`test_self_evolution_safety.py` 的 `/dev/shm` 不可写 ⇒ 多进程信号量建不起来，属**环境能力**而非代码问题）。门把三件事钉死：每个跳过点必须有理由（f-string/变量/helper 默认理由都算，空字符串不算）、调用点无条件者必须是登记的条件式 helper、环境能力 skip 的解释句必须留在文件里（防「理由被改没」）| 防 | `tests/audit/test_skip_census.py` | `tests/audit/test_skip_census.py::SkipCensusTest` |
| 覆盖面不许只在有数据时才存在 | 三条假设**全部被推翻**（如实记录）：① 整文件都可条件跳过的文件 **0 个**；② 「断言全在 if/except 里」初版报 432 条，逐类看全是误报 —— `for x in 固定数据: assert` 的循环体**照常执行**断言（第一版把 For/While 也当条件），而 `for node in ast.walk(tree): if isinstance(node, ast.Import): assertNotIn(...)` 是「**扫违规**」惯用法（`if` 用来挑违规，「没违规」本身就是通过）；剔除后剩 41 条仍无真问题；③ 「数据存在性守卫下静默通过」（`if os.path.exists(data): assert…` 且无 `else`）实测 **0 条**。门保留两条**可静态判定**的判据：任何文件不许「每条用例都可跳过」（干净检出下贡献 0 验证）、任何用例的断言不许全落在数据存在性守卫下且无 `else`（有 `else` 披露即合法）；并把三类误报形态钉成「不得误杀」用例 | 防 | `tests/audit/test_coverage_never_depends_on_data.py` | `tests/audit/test_coverage_never_depends_on_data.py::CoverageNeverDependsOnDataTest` |
| 安全函数的分支要被真的走到（运行时覆盖率）| 静态门再密也看不到「哪些分支从未执行」。本刀用 **stdlib 行轨迹器**（`coverage` 未安装，不上网装依赖）只记目标文件的命中行，对 `scripts/trader/venue_protection.py` 与 `r20_backend/execution_router.py` 实测（只跑 `tests/trading tests/venues` ⇒ 数字是**下界**）：前者 94.1%、后者 71.8%（后者未命中多为 live-only 分支）。逐行映射到函数后，发现**撤销/护栏类**分支从未被执行：「该合约仍有活动持仓 ⇒ 整合约跳过（宁可留腿，不可裸奔）」、「腿没有 id ⇒ 跳过（绝不按合约全撤）」、「dry-run 只报不做」、「positions 里的非 dict 行不得让流程炸掉」、「tag 证据的孤儿腿才允许撤」。补 5 条护栏用例后前者升到 **94.7%**（964/973/996 等行转为命中）。另记一条边界：`own_position` 里**有**该基名时带量腿会被判 `size_mismatch` ⇒ 保守不撤，故 tag 孤儿分支在生产里是**防御性**的 | 防 | `scripts/trader/venue_protection.py` | `tests/trading/test_venue_protection.py::CancelOrphanSafetyTest` |
| 「读不到」的分支必须有用例走到（路由侧）| 本刀把覆盖率探针固化成工具 `tests/coverage_probe.py`（stdlib `sys.settrace`，不装 `coverage`；用法 `python -m tests.coverage_probe <目标文件> -- <pytest 选择>`），并补 14 条只读分支用例：`_exposure_venues` 的「凭证未配置 ⇒ 进 skipped 留痕」「凭证读取失败 ⇒ 留痕（读不到 ≠ 没有）」「枚举失败 ⇒ 退回只算本次场所」；`_read_symbol_position` 的非 dict 行/按 pos_side 取值/读失败退化成只有 base+side；`_verify_symbol_flat` 的非 dict 行、「读不出量 ⇒ 当作仍有仓」「别的币不影响本合约」；`_cancel_proven_own_legs` 的「读腿失败 ⇒ 什么都不撤」「台账读失败 ⇒ 只退回 tag 证据，仍撤得动」「缺 id ⇒ 不撤」「适配器无撤单能力 ⇒ 如实记 failed」；`_load_venue_pool_soft` 「读失败按无限制继续，但**必须真的告警**」。探针复测确认这 5 条路由分支与 `venue_protection` 的 964/973/996 全部转为命中。另记两条自查：**桩必须与真实现同型**（`venue_credentials` 返回 `(api_key, secret_key)`，用 `{}` 当桩会让 `all({})` 恒 True ⇒ 把无凭证场所误判为有凭证）；**平仓前必须真有持仓**，否则腿走「未撤」分支、到不了「无撤单能力」| 防 | `tests/coverage_probe.py` | `tests/trading/test_router_read_branches.py::ExposureVenueReadBranchTest` |
| 开仓前的校验与「跨所敞口读不到 ⇒ 拒开」必须有用例 | 覆盖率探针把 `open_protected_position` 里**发单之前**的守卫逐行点出来：缺 `asset`、数值字段非数字、`margin`/`leverage`/`entry` 非有限正数三条 `validate` 拒绝（实测此前**没有任何测试文件**提到过这两句报错），以及跨所敞口**读不到**时的fail-closed（`跨所敞口不可核算 … 按 fail-closed 拒开`）与留痕（`统计范围=`/`counted_venues`/`skipped_venues`）。补 6 例（17 个子用例）后 `execution_router.py` 覆盖 **71.8% → 87.0%**（未命中 91 → 42，其余为下单主流程等 live-only 分支）。两条实测披露：① 套件沙箱把风险键`R20_MAX_TOTAL_EXPOSURE_USDT` 隔离掉 ⇒ 该常量为 0 ⇒ **跨所敞口闸门在测试环境默认是关的**，要用例自己给正值才走得到（代码默认 `None`，生产 `.env` 里该键存在）；② 读失败发生在敞口缓存写入**之前** ⇒ 该路径的 `counted_venues`/`skipped_venues` 会退化成空、detail 显示 `统计范围=—`（宁可显示「没数成」，也不显示假的场所清单）| 防 | `r20_backend/execution_router.py` | `tests/trading/test_router_validate_guards.py::OpenPositionValidateGuardTest` |
| 止盈宽度钳制的边界分支要有用例（唯一改写止盈价的地方）| `clamp_take_profit_width` 是「止盈不许挂天际线」的落点，也是发单前**唯一**会改写止盈价的函数，此前其「参数非法/不该动手」的保守分支无任何用例：非数字入参 ⇒ 原样返回（不猜不炸）、`limit/sl/tp` 非正 ⇒ 原样返回、入场价与止损价相同（风险距离为 0）⇒ 原样返回、`curr_reward<=0`（止盈在错误一侧）⇒ 原样返回、风控常量传垃圾 ⇒ 退化默认值、两个上限都不可用 ⇒ 原样返回（宁可不夹，不可乱夹）。补 9 例后该文件覆盖 **86.0% → 100%**。两条实测记录：① 语义实测纠正了我的期望 —— `allowed_max` 低于 `risk*min_rr` 时**底线会盖过ATR 上限**（我按 min_rr=2 算成 70100，实测 74000，代码对、用例错）；② **范围决定数字**：只用 `tests/trading tests/venues tests/ui` 时 `protection.py` 显示 21%、`risk_gates.py` 63%，把 `tests/extraction` 纳入后两者都是 **100%** —— 报缺口前必须把相关测试目录算全，否则会去修一个**不存在**的缺口 | 防 | `scripts/trader/brackets.py` | `tests/trading/test_brackets_clamp_branches.py::ClampTakeProfitWidthBranchTest` |
| 熔断判定的「不可判定 = 不放松」分支要有用例 | 活路径走的是 `scripts/trader/circuit_guard.py::is_circuit_breaker_active`。**全量套件**下探针点出 6 条从未执行的行，全部是 fail-closed 落点：黑天鹅哨兵已熔断 ⇒ 原样上抛其理由；熔断状态文件处于 active/triggered 且未过期 ⇒ 熔断；状态文件**损坏** ⇒ 熔断（不当作没熔断）；台账同步旁车检查**不可用** ⇒ 熔断；环境不可判定 ⇒ 当日亏损**保守全计**（demo/live 不互抵，宁停不漏）；日亏损数据**读取失败** ⇒ 熔断。补 10 例后该文件 **90.2% → 100%**（探针逐行确认 105/115/117/138/149/155 转命中）。另补 11 例把黑天鹅哨兵的 fail-closed **契约**写成显式断言（行情取不到/样本不足/形态不可判定/断崖/长下影/情绪文件损坏/分数非数字 ⇒ 熔断；正常行情与缺失情绪文件 ⇒ 放行且理由为空）。⚠️ 本刀同时纠正一个自造错觉：我先前凭 `tests/audit`、`tests/extraction` 里的**用例名**推断「哨兵从没被真的跑起来」，而全量探针显示哨兵本体（45-91 行）**早已 100% 覆盖**（真正驱动在 `tests/core/test_black_swan_sentinel_revival.py`）⇒ **看用例名 ≠ 看覆盖事实**，判缺口必须以全量探针为准 | 防 | `scripts/trader/circuit_guard.py` | `tests/trading/test_circuit_breaker_active_fail_closed.py::CircuitBreakerActiveFailClosedTest` |
| 退出链「要做的事没做成时绝不假装做成」| 全量探针在 `scripts/trader/position_exit.py::manage_position_tp_and_trailing` 点出 30 行未执行，全在退出链上：硬止损**平仓失败**必须保留仓位并把失败写进 executed_actions（不许静默继续）、云端保护与安全退出**都失败**要吼出来、时间止损的平仓成功/失败/通知/清理、阶梯锁利（多空对称）的触发/失败/上移锁利线同步云端、动能回撤止盈（峰值利润达标后从高点回撤或从低点反弹）、tracker 缺 takeProfitPx 时补默认、以及首次见到持仓必须建 tracker。以**全注入式 harness** 覆盖（依赖全是关键字注入 ⇒ 无需任何 patch）：19 例后该文件探针实测 **73.3% → 100%**（120/120 行，逐行确认）。另含两条健壮性断言：**行情无效 ⇒ 保留云端保护、跳过本地移动止盈**（盲区里绝不动保护单）、历史 tracker 必须补齐 policy_version/policy_hash/entryTs | 防 | `scripts/trader/position_exit.py` | `tests/trading/test_position_exit_failure_branches.py::ExitFailureBranchTest` |
| 云端保护的每一条「不算数」都必须真的不算数 | 全量探针在 `scripts/trader/cloud_protection.py` 点出 9 行未执行，**全是 `continue` 型保守跳过**，而每一条都能翻转「有没有保护」的结论：覆盖核算里把不该算的单算进去 ⇒ 误判已保护 ⇒ 裸奔；把该算的漏掉 ⇒ 误判没保护 ⇒ 触发安全退出。补 23 例覆盖：状态非 live/effective、方向不是平仓方向、`posSide` 不匹配（空头持多头的腿）、缺 tp/sl 触发价（半条腿不算保护）、非 reduce-only、保护单列表里混入非 dict 行、原生改单后**残余旧 SL 逐笔撤掉**、回退路径里「旧单 id == 刚挂上的 id」绝不撤、修复后**核验读失败 ⇒ 重试而非当作通过**；另补 `sync_cloud_algo_stop`（棘轮上移立刻同步云端止损）：无活单返回 False、价格一致幂等跳过、价格变化用该活单 algoId amend、**净持仓账户 `posSide=net` 必须容忍**（第一百八十六刀修掉的「同文件同一语义两处写法」——只认精确相等会让云端止损收紧静默失效）、读失败只返回 False 不抛。探针实测：我这一份用例单独就让该文件达到 **98.8%**（82 行仅剩 1 行，即已被既有用例覆盖的核验通过路径），全量基线原为 89.0% | 防 | `scripts/trader/cloud_protection.py` | `tests/trading/test_cloud_protection_branches.py::LiveOcoCoverageFilterTest` |
| 披露行里不许出现「名叫 None 的坏所」| `cycle_disclosure_payload` 旧写法是 `{str(v) for v in broken_venues if str(v)}` —— 而 `str(None)` 是**真值** `"None"`，于是列表里混进一个 `None` 就会在运维看到的披露行里多出一所假场所（`凭证坏所=1(None)`）：「UI 不说谎」的反面。已改为 `if v is not None and str(v).strip()` 并 `strip()` 归一，空串/纯空白同样不进披露。同时补 16 条**契约**用例：`_load_watchdog_state` 刻意区分「文件不存在 ⇒ `{}`（首次运行，合法空态）」与「不可读/损坏 ⇒ `None`（调用方必须不写单）」（读不到 ≠ 没有）、单条时间戳坏了只丢那一条但要留痕、防抖状态上限 500 条、写失败只返回 False 绝不抛；`cycle_disclosure_payload` 绝不抛（非 dict 报告/`None` 集合宽容）、坏所去重排序、形状违规只截前三并写明总数、**未开闸的巡检也要写进那一行**（没在跑的保护也是事实）；`write_cycle_disclosure_snapshot` 带毫秒新鲜度戳、失败只返回 False。⚠️ 如实记录：该区域（448-491 与 674-749）在全量探针下**本就 0 行未命中** ⇒ 这批用例是**契约级**而非覆盖率级；本刀真正的行为改动只有上面那处披露谎言。全量基线剩余未覆盖在编排阶段（`cycle_stages.py` 70.0%，74 行）| 防 | `scripts/trader/cycle_stages.py` | `tests/trading/test_cycle_disclosure_contract.py::DisclosurePayloadTest` |
| 周期编排三阶段的「这一步没做成要如实说」| 全量探针显示 `scripts/trader/cycle_stages.py` 70.0%，未命中集中在编排阶段；本刀补 12 例覆盖三处**每轮必走**的路径与其失败语义：`fetch_universe_and_manage_positions` —— **只对真有持仓的标的**做止盈止损管理（没仓的标的不许碰）、陈旧 tracker 清理要留痕、无论如何都要落盘 trackers；`persist_state_and_sync_ledger` —— 状态原子落盘 + 日志追加、台账自动同步**只在开关打开时跑且两个脚本各自存在才跑各自**、同步脚本炸了**只告警**不影响落盘；`preflight_reconcile_and_housekeeping` —— 引擎未就绪 ⇒ **本周期中止**、陈旧挂单清不掉 ⇒ **中止**（不许边挂边下单）、挂单对账失败 ⇒ fail-closed **只禁本轮新增下单**（持仓管理照常）、舆情采集失败只告警、时间戳必须是北京时间算出的绝对时刻。探针逐行确认：原先未命中的 30 行里本文件覆盖了 **20 行**（50/51、97-102、130-152）；剩余 10 行（209-219，`fetch_positions_and_reconcile` 的持仓汇总与「同一合约多空同时存在 ⇒ abort」分支）需要 13 项注入依赖的完整 harness，留下一刀 | 防 | `scripts/trader/cycle_stages.py` | `tests/trading/test_cycle_stages_orchestration.py::PreflightStageTest` |
| 相位 1 的「输入失败语义表」要逐项有用例 | `fetch_positions_and_reconcile` 自带一张逐项实测的输入失败表（第一百二十八刀），本刀造 13 项注入依赖的 harness 把它**逐条钉成断言**：OKX 持仓 / 挂单 / 余额**任一路读不到 ⇒ 整周期 abort**（读不到 ≠ 没有仓，也不许拿 0 余额硬算）；跨所持仓读失败 ⇒ `entries_blocked=True`（fail-closed 禁本轮新开仓）且跨所笔数显式**未知**、绝不装 0；跨所**挂单**枚举失败 ⇒ 预留对账 `venue_snapshot_verified=False`（否则会按「无仓无挂」误释放活单的预留，释放不可逆）；凭证已死场所 ⇒ 每周期明说「未计入」；外所多头/空头进各自同向配额与槽位。持仓汇总本身：只数**有量**的仓（数量 0 不算，否则槽位被虚占）、同合约**多空并存 ⇒ abort**（系统无法表达，硬挑一边记账就是撒谎）、`net` 既不算多也不算空。探针逐行确认：原先未命中的 30 行里本文件再覆盖 **11 行**（209-219 与 336），累计本会话在该文件覆盖 31 行中的 30 行；剩余未命中集中在 `scan_risk_gates_and_ai_brain` 等后续阶段 | 防 | `scripts/trader/cycle_stages.py` | `tests/trading/test_cycle_reconcile_stage.py::InputFailureSemanticsTest` |
| 模型给指令、底座把关的交汇点要逐条钉住 | 相位 4 前段 `scan_risk_gates_and_ai_brain` 是「认知决策归模型，物理风控归底座」的交汇点，补 14 例钉住：熔断中 ⇒ **不叫模型**且不再走池闸（先止损，别再谈开仓）；总开关关掉 ⇒ 一个模型调用都不发；模型**有**产出 ⇒ 必须**重新拉一次真实仓位**再执行持仓管理（拉不到就 append「AI持仓管理跳过」而**不拿旧快照执行**；刷新只保留 size>0 的行）；模型**无**产出 ⇒ 读周期健康度：并发跳过要写明「禁止复用旧持仓指令」、连续失败要吼且 ≥3 轮升级为 🔴 人工核查并带出失败原因；模型那段**抛异常只 warn**（绝不打断周期）；标的池不可信 ⇒ fail-closed **只做持仓风控接管、禁止开新仓**（但持仓管理照常）；外所持仓必须汇入三所平权全景、跨所笔数拿不到时写「未知」绝不装 0。覆盖归因（如实）：宽口径 before-run 里该段的 AI 健康度分支（422-427/429）**确为缺口**，本刀补齐；该段其余行此前已被别处用例走到 ⇒ 这批用例部分是契约级而非覆盖率级 | 防 | `scripts/trader/cycle_stages.py` | `tests/trading/test_cycle_brain_stage.py::ScanRiskGatesAndBrainTest` |
| 跨所保护巡检格：不知道缺口多久了就不动手 | `venue_protection_watchdog_stage`（默认关闭的加固层）补 15 例，逐条钉住它的保守选择：总闸没开 ⇒ **零网络零写单**（连判定都不做）；环境轴不可得 ⇒ 本轮跳过；防抖开着时四条「不冒险」——观察轮失败 / **状态不可读** / 防抖计算异常 / 状态不可写 ⇒ 一律**本周期不写单**（状态失真的防抖等于没有防抖）；`dry_run=True` ⇒ 只判定并把「本来会做」的动作报出来，**且若审计层居然返回 `actions` 就当场喊 🔴**（预演不得写单）；缺口未持续够 ⇒ **只观察**且把「已持续 X 分钟」写进 `executed_actions`（让人看得见它在逼近阈值）；缺口愈合 ⇒ 状态自清（不攒垃圾、也不会突然补写单）；完全没有止损腿仓位只报 CRITICAL 并写明「需人工或用既定策略价位重挂」——**巡检层不臆造价位**（价位是策略决定）；本格异常只告警（加固层不该成为新的单点）。覆盖归因：宽口径 before-run 里 551/579/589/592（环境不可得与三条防抖兜底）**确为缺口**，本刀补齐；探针确认本文件单跑即覆盖该格全部行 | 防 | `scripts/trader/cycle_stages.py` | `tests/trading/test_cycle_watchdog_stage.py::WatchdogStageTest` |
| 并集被改写成整体赋值：OKX 在途挂单被静默丢弃 | 抽取那一刀（`bb6cb57`）把「OKX loop 建**基准** → 外所枚举 `add` 进来」写成了 `pending_inst_ids, pending_long_count, pending_short_count = collect_pending_inst_ids(...)` ——**整体赋值**。而 `collect_pending_inst_ids` 只枚举 `venues=('gate','binance')` ⇒ **OKX（主场所）在途挂单被静默丢弃**，后果两条：① `reserved_slot_count`/同向计数少算 OKX 在途单 ⇒ 执行层开仓闸（`reserved_slot_count < MAX_CONCURRENT_POSITIONS`）可能**超发槽位**；② `reconcile_reservation_ledger` 拿到的集合里没有 OKX 在场活单 ⇒ 对账器据「无仓无挂」把它当陈旧占用**释放**（释放不可逆——正是该函数 docstring 自己点名的第一类错误）。已恢复为**并集**（两处枚举各管一段：OKX 走本地 loop、外所走适配器，谁都不是对方的替代），并在 `tests/extraction` 登记为**文档化差异**（附旧/新锚点与理由）。反证：把并集退回整体赋值 ⇒ 新用例当场红（槽位 2 != 3），恢复即绿。顺带补 3 例覆盖在途挂单过滤（只数 live/partially_filled，已撤/已成不算在场；无 instId 仍算同向）与两处异常兜底（环境轴读不到按不可信、坏所探测异常只 warn）| 防 | `scripts/trader/cycle_stages.py` | `tests/trading/test_cycle_reconcile_stage.py::InputFailureSemanticsTest` |
| 形状预检只给可见性、不给行为（接线也要钉）| `data_shape_preflight_stage`（第 49 刀）的定位是**加载侧管行为、本阶段管可见性**：读不出来由加载侧 fail-closed，读得到但形状不合规由本阶段指名道姓地打印。补 5 例钉住：两个校验器**各自拿到自己的路径**（接线错位会让预检看错文件）、违规行必须带来源前缀（意图/追踪器，否则读者不知道改哪个文件）、一个校验器报多条时不许只留一条、**无违规也要明说「合规」**（沉默会与「预检没跑」混淆）、校验器抛错本阶段**不吞**（照实上抛 ⇒ 周期停在预检阶段）且不产生副作用。覆盖归因（如实）：该区域在全量探针下**本就 0 行未命中** ⇒ 这 5 例是**契约级**而非覆盖率级 | 防 | `scripts/trader/cycle_stages.py` | `tests/trading/test_cycle_shape_preflight.py::ShapePreflightTest` |
| 死写闸：增量被整体赋值抹掉，必须静态就能查出来 | 第二百二十刀那处回归（`pending_inst_ids = set()` → 循环里 `add`/`+=` → 又被 `= collect(...)` **整体赋值**）的特点是**不报错、不抛异常、旧测试全绿** —— 只有「拿尺子量那个数」才露馅，而它发生在主链风控计数上。故新增全仓 AST 闸 `tests/audit/test_no_dead_aggregate_overwrite.py`：同一层块内「先整体赋值建基准 ⇒ 中间只被增量修改（`add`/`update`/`+=`/`x[k]=v`）⇒ 之后又被整体赋值，且**从未被真正读过**」即报。**牙齿**：合成样本 6 例 —— 正例（对象集合、累加器两种）必报；**不误杀**（`rows = sorted(rows)` 这类读了旧值的读改写、普通重绑、互不相干的名字、嵌套函数内同名量）必不报；另设扫描面地板（≥200 个文件）。**真代码反证**：把第二百二十刀的修复临时退回 ⇒ 闸当场点名 231/232/233 行建立、297 行被整体赋值（三个名字全中）；恢复修复 ⇒ 全绿。全仓扫描当前**零命中**（同类缺陷在本仓暂无第二例）| 防 | `scripts/trader/cycle_stages.py` | `tests/audit/test_no_dead_aggregate_overwrite.py::Scanner` |
| 被否决的闸：比较口径漂移（普查 105 组、无一处真漂移）| 修掉第一百八十六刀那处「同文件同一语义两种写法」（`posSide` 一处 `in {pos_side, "net"}` 容错、一处只认精确相等 ⇒ 净持仓账户永远找不到活止损单 ⇒ 云端止损收紧静默失效）之后，先做**普查**再决定要不要做全仓闸：同主语多口径 **105 组**（绝大多数是同处按分支派发 `venue == okx/binance/gate` 与嵌套校验）、只看侧/状态类主语 **25 组**（同上，逐组判读无一处真漂移）、只看字面量 net 容错不一致 **0 组**。⇒ 硬做闸要背 25-105 条白名单，违背「白名单必须少且写明理由」的既有纪律，**故不做**；普查留成工具 `tests/comparison_set_census.py`（可重复跑，`--risky`/`--net-drift`/`--json`），另只钉**尖锐子集** `tests/audit/test_comparison_set_drift.py`：两边都写字面量、一边含 `net` 一边不含 ⇒ 报。**牙齿**：合成正例必报；**不误杀**按场所分支派发、比较对象是变量（后者写明为**本闸盲区**——历史那处比的正是变量 `pos_side`，本闸抓不到）。**真代码反证**：临时在 `cloud_protection.py` 植入一处「一边含 net、一边不含」⇒ 闸当场点名 L193/L195；移除即绿。另设扫描面地板（≥200 文件）与信号地板（侧/状态类 ≥10 组）| 防 | `tests/comparison_set_census.py` | `tests/audit/test_comparison_set_drift.py::TeethTest` |
| 分批止盈的每条分支都是钱：准入守卫、按所路由、旧保护单清理 | `execute_scale_out_if_eligible` 全量探针 78.2%（31 行未命中），补 21 例钉住：四道准入守卫（未启用/行情数据不完整/无持仓/持仓均价无效）一律**返回原因、不猜**；切片低于最小精度 ⇒ **降级全仓追踪**（`scale_out_phase=-1` 防每轮重复提示）并留痕，**绝不提交碎单**；按所路由 —— Binance **hedge**（`positionSide` 非空，含只在 `raw` 里的情形）走 `position_side=`、**one-way 才走 `reduce_only=True`**（hedge 传 reduceOnly 会被拒），Gate 与多所兜底同理；**没有适配器注册表 / 下单抛异常** ⇒ 如实点名是哪一所失败，绝不假装成功；下单成功后**先清旧保护单再重建**（避免超额单量穿仓反向开单与旧止损残留），清理异常只告警、不让已成交的减仓判失败；成功后才锁 `phase=1`/`scale_count=999`（永久互斥金字塔加仓）。**顺手核过一处可疑不对称**（第 217 行传 canonical、219 行传 `native_symbol(...)`）：实测符号转换**幂等**（`native_symbol_pure` 对 `BTC`/`BTCUSDT`/`BTC_USDT` 都收敛到同一值）⇒ **不是缺陷**，不做无谓改动。**教训（已写入测试）**：能力探测要用「**真的没有那个属性**」来测 —— 第一版让桩继承基类并把方法写成「调用即抛 AttributeError」⇒ `hasattr` 仍为真、走错分支、异常被吞、目标行永远覆盖不到 | 防 | `scripts/trader/scale_out.py` | `tests/trading/test_scale_out_paths.py::BinanceRoutingTest` |
| 方向推不出来就跳过；改单没生效就不许动本地跟踪器 | 探针点名的 6 行收尾，两条语义：① 外所**在途挂单没有 `side`** 时，用**有符号数量**推断方向（正=买/开多、负=卖/开空）——少了这一步，这些单会因方向不合规被**静默跳过** ⇒ 槽位/同向占用少算；而数量**不是数字**时**容忍但不猜**（跳过该单，绝不当成买或卖），数量为 0 同样不占槽位。② AI 持仓管理：指令文件**读不动/坏 JSON** ⇒ 留痕并返回（绝不拿半份数据动仓位）；云端止损更新**被拒或抛异常** ⇒ `continue` —— 既不谎报成功，也**不把本地跟踪器 `trailingStopPx` 改成没生效的价位**（否则下一轮本地以为已经保本、与云端不一致）；QQ 通知失败 ⇒ **只吞掉**（止损已经生效，本地状态照常更新）。探针确认 6 行全部命中 | 防 | `scripts/trader/cycle_snapshot.py` | `tests/trading/test_ai_position_mgmt_paths.py::CloudStopUpdateFailureTest` |
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


"""交易员脚本的抽取子包（结构优化阶段 4·B3）。

`scripts/ai_factor_trader.py` 是实盘主脚本（由 worker 每 15 分钟 respawn），
按研究文档 B3 采用**门面保留式抽取**：只把不依赖模块状态的纯逻辑搬进来，
门面保留同名壳与全部被测试钉住的字面量。

## 模块清单

| 模块 | 内容 | 注入面 |
|---|---|---|
| `factors.py` | `fetch_single_instrument_data` 单标的行情/指标装配 | 宽（多函数） |
| `signals.py` | `evaluate_asset_signal` / `clamp` 多因子评分 | `asset_class_profiles` / `is_in_stop_cooldown` / `load_adaptive_config` |
| `protection.py` | 止损信号、棘轮移损、AI 收紧判定、平仓载荷/手续费 | `safe_float` 等，全部调用期注入 |
| `gates.py` | `order_margin_gate` / `equity_margin_cap` / `is_tradfi_market_liquid` | `MAX_SINGLE_ASSET_MARGIN` / `MAX_MARGIN_EQUITY_RATIO` |
| `position_mgmt.py` | `execute_ai_position_management` 主脑持仓指令执行器（95 行） | 文件路径 / `ai_tightens_stop` / `close_position_confirmed` / `okx_rest` / 多所三项，全部调用期 |
| `brackets.py` | `normalize_bracket_prices` 限价单三价顺序钳制（长/空各一份内联合并为一处） | 无（纯函数，数值全部入参） |
| `pyramiding.py` | `pyramiding_gate` 顺势浮盈加仓五条门禁（长/空各一份内联合并为一处） | 4 个风控常量 + 全部中间量，均调用期 |
| `order_intent.py` | `resolve_entry_prices` 三价定价 + `build_order_intent` 下单载荷装配（长/空各一份内联合并为一处） | 全部入参；保证金闸门/权益顶刻意留在门面（计数锚点载体） |
| `notifications.py` | `entry_action_message` / `entry_failure_message` / `trade_open_kwargs` 方向文案与通知参数（12 个方向常量收成单一来源） | 全部入参；`leverage` 与全部状态变更刻意留在门面 |
| `cycle_snapshot.py` | `collect_pending_inst_ids` 外所挂单枚举与去重计数 + `build_state_payload` 面板状态快照 | venue_registry/load_instruments/信号求值函数，均调用期 |
| `signal_snapshot.py` | `build_signal_snapshot` 开仓时刻因果/数理/舆情观测组装（自进化复盘数据源；B3 第八十二刀，零交易动作） | 唯一外部依赖 `DATA_DIR`（因子库快照路径根），门面壳调用期注入；专测 patch 面保真 |
| `cycle_stages.py` | `execute_portfolio` 的四个相位段：`fetch_positions_and_reconcile`（相位 1：取真实持仓+合约对账+跨所汇总+挂单盲区守卫+预留对账，B3 第九十二刀）+ `preflight_reconcile_and_housekeeping`（0/0a 就绪闸+对账+回收+舆情）/ `fetch_universe_and_manage_positions`（2-3 并发取因子+逐仓退出）/ `persist_state_and_sync_ledger`（5-6 面板持久化+台账同步）（B3 第九十一刀） | 全同名 kw-only 入参；段体 **AST 逐字**；段内 `return None` = 本周期中止（调用点判 None 后 `return None`）|
| `entry_execution.py` | `execute_entry_scan` —— `execute_portfolio` **相位 4 入场循环**（300 行：逐标的信号评估→置信度/流动性/加仓闸→定价与载荷→受保护下单→通知与追踪器）（B3 第九十刀） | 41 项同名入参（12 外围局部量 + 29 门面全局）；**AST 逐字**、无返回值（0 return/0 break；3 个计数器循环后不再被读） |
| `position_exit.py` | `manage_position_tp_and_trailing` 持仓**机械退出**主流程（硬止损 / 三档追踪棘轮 / 时间止损 / 云端保护同步 / 平仓确认 / 台账；与 `position_mgmt.py` 的"主脑指令执行"是两个关注点）（B3 第八十九刀） | 同名注入 18 项；`time` 子包自 import |
| `order_submit.py` | `submit_protected_limit_order` 受保护限价单提交（决策面前置闸→合约对账→价格锚定→**入场价穿价幻觉闸**→demo rescale→多所平权分发）（B3 第八十八刀，唯一落单函数） | 同名注入 11 项；审计④ 价格理智锚点的三段文本随实现迁入本模块（锚点已工具化） |
| `routing_policy.py` | 路由与预算政策域：`load_routing_mode`/`load_preferred_venue`（路由档读取）+ `portfolio_risk_budget_usdt`/`estimate_margin_usdt`（预算与保证金估算）+ `_decision_payload`/`_rejection_focus_reason`（决策载荷取值）+ `portfolio_budget_guard`（跨所合算总闸纯函数）+ `route_and_reserve_signal`（路由+预留主流程，146 行）（B3 第八十七刀） | 同名注入 14 项（最宽）；壳签名=基线逐字；模块对象（routing_policy/risk_reservation/venue_router）按对象注入 |
| `venue_query.py` | `query_positions` OKX 查持仓 + `venue_execution_ready` 就绪判定（登记/闸开/坏所摘除） + `fetch_other_venue_positions` 外所持仓全景 + `_venue_health_stamp` 健康观测读取 + `close_position_confirmed` 平仓后交易所侧确认（B3 第八十六刀） | 同名注入 ⇒ body 零例外逐字；`_BROKEN_VENUES` 按引用注入（读写同一集合）；三个跨模块注入项（position_mgmt / reservation_reconcile / venue_evidence）由门面调用期解析 |
| `cloud_protection.py` | `amend_venue_stop_loss` 跨所云端 SL 棘轮（amend 优先/否则先挂新再撤旧）+ `_live_oco_coverage` 覆盖统计 + `ensure_cloud_position_protection` 100% OCO 校验修复 + `sync_cloud_algo_stop` 云端条件单同步（B3 第八十五刀） | 同名注入 ⇒ body 零例外逐字；`_live_oco_coverage` 亦按注入项传给 ensure（跨函数 patch 面） |
| `order_lifecycle.py` | `clean_stale_open_orders` 超时挂单回收+同向重复单收敛（OKX 与外所同尺，fail-closed）+ `reconcile_pending_orders` 重启接管对账（B3 第八十四刀） | 同名注入 ⇒ body 零例外逐字；**嵌套闭包随函数整体迁**（`_intent_covers`/`_cancel_orphan`）；`_BROKEN_VENUES` 按引用注入 |
| `ledger_writer.py` | `record_trade` 成交双写台账（policy 溯源→JSON 原子替换→SQLite）+ `record_open_intent` 意图簿写入时清理（B3 第八十三刀） | 同名注入 ⇒ body 零例外逐字；`record_trade_sqlite` 可为 None 的语义原样 |
| `venue_evidence.py` | `build_venue_candidates` 路由候选装配（健康观测/费率平权/稳定性惩罚）+ `persist_venue_decision` 选所证据回写（flock 包 RMW + 原子替换；B3 第八十二刀） | 注入 kw 与门面全局**同名**（函数体逐字零改动的代价；flock tripwire 文本随实现住此）|
| `circuit_guard.py` | `check_black_swan_sentinel` 黑天鹅哨兵（行情断崖+极端舆情，"不可判定=不放松"）+ `is_circuit_breaker_active` 开仓熔断三查（B3 第八十一刀，trader 瘦身首域） | `fetch_candles_direct` / 三个文件路径常量 / `current_environment` / `effective_daily_loss_limit`，全部由门面壳调用期注入（对拍门 `tests/extraction/test_trader_circuit_guard_extraction.py`） |
| `leverage.py` | `clamp_ai_leverage` AI 杠杆夹取（配置区间 → 池内单标的上限，顺序是关键） | MIN/MAX_LEVERAGE + 池值，均调用期 |
| `sizing.py` | `size_for_decision` 按 AI 决策推导下单张数（四道钳制：0.5x 下限 / 2.0x 上限 / 余额硬顶只砍不放 / 步长量化） | quantize_size + max_size_within_margin 由门面注入 |
| `position_universe.py` | `collect_okx_position_payloads` 从因子快照摘出 OKX 在仓并补追踪器字段 + `merge_cross_venue_positions` 汇入三所持仓（合成 id `VENUE:inst`） | 无（纯装配；不取数 —— 必须吃**已冻结**的周期快照） |
| `data_shape.py` | 生产数据产物**形状校验**（意图/追踪器：类型·键名·单调性，每条违规带下游后果）+ `read_json_safe` 只读读取 | 无（纯函数；不取数、不写盘） |
| `reservation_reconcile.py` | `_utc_age_seconds` SQLite UTC→秒龄（不可解析 = **-inf**，方向是红线） + `reconcile_reservation_ledger` 预留台账账实相符回笼（US-010，74 行） | `reservation_manager` / `fetch_other_venue_positions` / `state_closed` / `default_ttl_s`，**全部调用期注入** |
| `scale_out.py` | `execute_scale_out_if_eligible` 分批平仓止盈执行引擎（首批50%锁定+云端OCO重置+保本移损+互斥加仓锁） | `okx_rest` / `venue_registry` / `record_trade` / `notify_trade_close` / `close_fee` 等全部调用期注入 |
| `venue_protection.py` | 跨所（Gate/Binance）保护单**覆盖核验与临期续期**（roadmap G8）：`scan_protective_orders` 纯判定（缺口/临期/不可判定）+ `ensure_venue_protection` 先挂新后撤旧的安全动作（动作层已具备，周期接线见后续刀）+ `attribute_protective_orders` **逐腿归属**（matched / 旧量腿 / 可归因孤儿 / **归属不可判定**——后者不得自动撤销） | 零门面注入：IO 全部由调用方传 `ad`（子模块 import 期不绑定任何门面名字） |

## ⛔ 已评估、**结论是不该抽**：`execute_portfolio` 的开仓执行段

`execute_portfolio` 主循环里，做多与做空各有一份 **59 行**的开仓执行段，
逐行 diff 确认**除 7 处替换外完全相同**（`is_long` / 追踪器键后缀 / 一个计数器）——
看起来是"抽公共代码"的教科书场景。

**但它不该抽，而且已经被试过一次（2026-09-14，第三十七刀，已回滚）。**
那 59 行里约 **30 行是既有测试锚点的载体**：

| 锚点 | 数量 | 位置要求 |
|---|---|---|
| `resolve_entry_prices(` | 2 | **门面**主执行路径，且所在分支须备齐传入的每个名字 |
| `build_order_intent(` | 2 | 同上（`tests/extraction/test_trader_order_intent_extraction.py`） |
| `sl_px, tp_px = normalize_bracket_prices(` | 2 | **门面**（`tests/extraction/test_trader_brackets_extraction.py`） |
| `_order_margin = order_margin_gate(` | 2 | **门面**（`tests/extraction/test_trader_gates_extraction.py`） |
| `notify_trade_open(..., leverage=int(ai_lever))` | 4 | **门面**（审计缺陷 D 的守卫） |

`tests/extraction/test_trader_order_intent_extraction.py::test_facade_keeps_the_two_anchor_lines`
的 docstring 明确写着这是**刻意**不抽的部分。**实测：整块搬走会让 11 个测试翻红。**

更要紧的是**技术上的死结**：这些锚点行**算出的正是下游要用的值** ——
`limit_px/tp_px/sl_px`（来自 `resolve_entry_prices` + `normalize_bracket_prices`）、
`_order_margin`、`_side/_pos_side/_venue_ctx`（来自 `build_order_intent`）。
留下锚点行，就必须把这些值**再传一遍**给子模块（新增约 6 个参数）；
而"只抽剩下的 29 行"要新增一层 18 参数的间接。

> **收益 4%（`ai_factor_trader.py` 2844 → 约 2738 行），代价是一层 18 参数的间接。**
> 不划算，故保持现状。**留着这份记录，避免后人第三次尝试。**

## 两条铁律

1. **子模块不得在 import 期绑定门面名字**。`pin_baseline_risk_env()` 的原地重载
   名单里**没有**任何子模块，import 期绑定会变成过期快照；`patch.object(门面, 名字)`
   这类测试缝也会被静默关掉。一律**调用期注入**。
2. **门面壳必须是 `def name(...)`，不能是 `name = impl` 别名**。计数锚点
   （如 `ai_factor_trader.py` 里 `order_margin_gate(` 恰 3 次）与
   `inspect.getsource(trader.xxx)` 都依赖"门面里有一个真正的 def"。

## 本包是实盘进程的 import 根之一 —— 改完必须做语法校验

`scripts/ai_factor_trader.py` 第 41 行起就 `from scripts.trader.xxx import ...`。
本包任何文件只要有 **SyntaxError**，交易员整个周期会在 import 阶段直接死掉：
`logs/ai_factor_trader.log` **不会**新增任何行（含 traceback），表现为"周期静默消失"。
2026-09-14 10:00 那次事故就是这么来的（往本文件尾部追加文档时漏了 docstring 闭合）。
所以：**改本包任何文件后，务必 `python -c "import scripts.trader"` 或至少
`ast.parse(...)` 验证一次**，不要只看测试是否绿。
"""

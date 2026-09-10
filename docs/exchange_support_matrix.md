# 交易所支持矩阵（Exchange Support Matrix）

> R20 多交易所适配层（`r20_backend/exchanges/`）能力差异的单一事实文档。
> 代码级能力表见各适配器 `ExchangeCapabilities`，本文档与其保持同步。

## 当前支持状态

| 场所 | 只读行情 | 断流备源 | 决策证据 | 凭证配置 | 实盘执行 |
|---|---|---|---|---|---|
| **OKX V5**（默认主战场） | ✅ | 主源 | ✅ 全因子 | ✅ V5 直签 REST（API Key 唯一） | ✅ 生产链路（`ai_factor_trader`） |
| **Binance USDT-M** | ✅ 免登录 | ✅（OKX 全断时补 ticker/K线/费率） | ✅ 基差/大户多空比 | ✅ 后台预留 | ⏸ 适配器未实装（独立条件单双轨待建） |
| **Gate.io V4 永续** | ✅ 免登录 | ✅ | ✅ 基差/费率 | ✅ 后台已收口 | 🔓 已实装：`R20_GATE_EXECUTION=1`（后台第5节开关+确认短语）+ `data/venue_routing.json` 配池；**平行试验田** `scripts/gate_lab_trader.py`（dry_run 默认演算） |

## Gate 试验田（平行、隔离、四道闸）

主链（OKX 15 分钟循环）与试验田**零共享写状态**：试验田只读 brain 决策缓存，
独立台账 `data/gate_lab_trackers.json`。开闸四道：

1. `venue_routing.json` 的 `gate.assets` 非空（币名池）；
2. 执行开关 `R20_GATE_EXECUTION=1`（后台第 5 节，需确认短语 `OPEN GATE EXECUTION`）；
3. 池内 `dry_run: false`；
4. Gate 凭证就绪。任一缺失自动 fail-safe 降为演算或不动作。

预算护栏：`margin_per_trade_usdt`（每笔上限）+ `max_open`（并发笔数）+ 置信度
门禁（默认 80，与主链同尺）；物理风控（几何/R:R/双腿保护单回读+缺口补挂+
棘轮先挂新再撤旧）全部走与主链同一套尺子。

「Gate 开闸」三重保险：`R20_GATE_EXECUTION=1`（env 显式）+ 后台凭证就绪（缺失即
`ExchangeCapabilityError`）+ 路由内物理校验（几何/R:R 底线/100% 保护单回读，任一
缺口撤单回滚）。沙盒 `fx-api-testnet` 连续实测 502，暂以实盘最小单验证。
「Binance ⏸」：无附属 TP/SL 需双轨 OCO + 触发价默认 MARK_PRICE 反转，独立工程另做。

## 关键差异（照搬会踩的坑）

| 维度 | OKX | Binance | Gate |
|---|---|---|---|
| 标的命名 | `BTC-USDT-SWAP` | `BTCUSDT` | `BTC_USDT` |
| 下单数量单位 | 张（`ctVal` 折算） | **币本位数量**（按 stepSize 截断） | **带符号张数**（正多负空） |
| 止盈止损 | 单单附带 `attachAlgoOrds` 云端 OCO | ❌ 无附属 TP/SL，须独立 `STOP_MARKET`/`TAKE_PROFIT_MARKET` 并自补「一单触发撤另一单」 | 独立 `/price_orders` 条件单资源族 |
| 触发价默认 | 最新价 | ⚠️ **标记价格 MARK_PRICE** | 最新价（可选 index/mark） |
| 签名 | HMAC-SHA256 + Passphrase | HMAC-SHA256/Ed25519 | HMAC-**SHA512** |
| 限频惩罚 | 429 退避 | 429 后不停手 → **418 封 IP 最长 3 天** | 最宽松（~100-200 req/s） |
| 大陆网络 | ✅ 可用 | ❌ 明文封锁（KYC+IP） | ⚠️ 无明文封锁，实测最宽松 |
| 大户数据 | rubik 多空比（免费） | `topLongShortPositionRatio`（免费） | `contract_stats` 四所最全（免费，含清算史） |
| 官方沙盒 | `x-simulated-trading` 模拟盘 | `demo-fapi.binance.com`（旧 testnet.binancefuture.com 已过时） | `fx-api-testnet.gateio.ws`（实测偶发 502） |

## 沙盒档位

后台「交易所与标的池 → 5. 多交易所数据源与凭证」勾选即热切换
（对应环境变量 `R20_BINANCE_TESTNET` / `R20_GATE_TESTNET`）；
开启后该所的行情与未来执行全部指向官方测试网。

## 数据健康度

每 15 分钟决策周期自动落盘三所取数健康（成功币数/失败原因/延迟）：
后台同一卡片实时展示，原始数据在 `data/venue_health.json`。
备源容灾顺序是**健康感知**的（失败多者后置、延迟差 >5x 才翻转防抖；
健康文件缺失/损坏回退静态序）。

## 跨所数据能力矩阵

> 消费点状态为 2026-09-09 代码实测（mission/binance-gate-coordination 合入后）；
> 「未消费」= 端点已接通但无下游，均有排期（维护者本地 plan_local/R20_VENUE_VALUE_ROADMAP.md，
> 不入库；社区用户可视为 backlog）。

| 数据项 | Binance | Gate | R20 消费点 | 健康度接入 |
|---|---|---|---|---|
| ticker/基差 | ✅ 免费（本机出口 bookTicker 被 WAF 拦→depth 自动回退） | ✅ 免费 | 决策 Prompt 跨所证据行 + OKX 全断备源 | ✅ |
| K线 | ✅ 免费 ≤1500 根 | ✅ 免费 ≤2000 根 | 备源容灾（形状对齐 OKX 契约） | ✅ |
| 资金费率（现值） | ✅ premiumIndex | ✅ ticker.funding_rate | 证据行双所费率 + 背离标注 | ✅ |
| 资金费率（历史） | ✅ /fapi/v1/fundingRate | ✅ contract_stats 序列 | 未消费（W1 三所背离统计） | — |
| 大户多空比 | ✅ topLongShortPositionRatio | ✅ contract_stats top_lsr_* | 证据行双所大户比 + 分歧标注 | ✅ |
| Taker 主动比 | ✅ takerlongshortRatio | ✅ lsr_taker | 未消费（W4 候选，OKX 同项已在主链） | — |
| 清算数据 | ❌ 合约端点无免费等价 | ✅ liq_*（contract_stats 聚合） | 未消费（W2 清算放量雷达；逐笔流需签名——评估为不做） | — |
| OI（现值/历史） | ✅ openInterest / openInterestHist | ✅ open_interest(_value) | 未消费（主链 OI 用 OKX rubik；W4 多源互证候选） | — |
| 新币上线公告 | ✅ CMS bapi（实测 2253 篇，免费） | ❌ 网页 403（不做抓取） | 未消费（W3 先做人工读小工具） | — |

三所中位数 oracle（防插针）：所需三所现价端点全部免费可用，门禁改造在 W2 排期。

## 扩展一个新场所

1. 在 `r20_backend/exchanges/` 新建子类：声明 `ExchangeCapabilities`
   （数量语义/触发价默认/限频/网络政策全部显式写进能力表）+ 实现公共行情切面；
2. 未实装的私有切面**保持基类显式抛错**（fail-closed），不要写假实现；
3. `registry._ADAPTERS` 注册；开闸执行前不动 `ADAPTER_EXECUTION_ENABLED`；
4. 单测按 `tests/test_exchanges_adapter.py` 模式全 mock + 真机只读冒烟。

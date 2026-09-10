# US-005 六组合验收清单 + 未验项台账

> 依据：`THREE_VENUE_COORDINATION_DESIGN.md`（§4 顺序 / §5 决策点 / §10 验收门槛）、
> `THREE_VENUE_API_FRESHNESS_AUDIT_20260910.md`（§3 未证实事项）、
> `THREE_VENUE_API_RESEARCH.md`（环境矩阵）。冲突处以时效审计（2026-09-10）为准。
>
> 本文件只做验收记录与台账，**不含任何自动执行命令**；所有命令须由运维者在
> 授权环境内手动执行并回填结果。

---

## 0. 安全红线声明（必读）

- [ ] LIVE 格验收前必须由用户**单独书面授权**：额度上限、标的白名单、单笔损失上限、累计损失熔断线；缺任一项即禁止开验。
- [ ] 本清单中所有 LIVE 格命令仅作**手工核验样板**，不提供可直接回车执行的完整脚本；涉及真实资金的步骤由人工逐步执行。
- [ ] 模拟格（demo/testnet）验收同样必须在**测试沙盒环境**运行，禁止在生产交易进程内执行任何验收命令。
- [ ] LIVE key 三所统一「只勾交易 + 提币关死 + IP 白名单」三原则（RESEARCH §3.7）。
- [ ] 提交超时/503 不等于失败：先按 clientOrderId 回查，未知期间禁止重发到另一家（DESIGN §4）。
- [ ] 任一环境写路径开闸 = 该所该环境 100% mock 测试 + 沙盒全生命周期 + 最小单首验 + 四道闸（RESEARCH §3.7）。

---

## 1. 六格矩阵总览

| 格 | 环境端点 | 当前状态 |
|---|---|---|
| OKX-live | `www.okx.com`（实盘 key） | ❌ 未验 |
| OKX-demo | 同域 + header `x-simulated-trading: 1`（sim 前缀 key） | 🔶 部分（认证读✓ 下单✓ 保护核验待验） |
| Binance-live | `fapi.binance.com` | ❌ 未验 |
| Binance-demo | `demo-fapi.binance.com` + Demo 专属 key | 🔶 部分（认证读✓） |
| Gate-live | `api.gateio.ws` | ❌ 未验 |
| Gate-testnet | `api-testnet.gateapi.io` + Futures TestNet 专页 key | 🔶 部分（认证读✓） |

> 说明：六格九步验收中，凡标注「okx_rest / 适配器」的步骤走 `r20_backend/exchanges/`
> 适配器链路（BaseExchangeAdapter 私有面）；标注「curl 直测」的步骤为绕开适配器的
> 端点级直签验证，用于定位适配器与端点的差异。P2 适配器收编未完成前，直测通过
> 不等于适配器通过，两栏均需回填。

---

## 2. 格 A：OKX-demo（同域 + `x-simulated-trading: 1`）

### 2.1 前置条件
- [ ] 模拟交易→个人中心→API 单独创建的模拟 key（5 开头 `sim` 前缀）
- [ ] key 绑定 IP 白名单（未绑 IP 且带交易权限的 key 14 天不活动自动删除）
- [ ] OKX-demo 执行开闸状态 = 已授权（四道闸通过）
- [ ] posMode 确认 `net_mode`（或按主链约定）

### 2.2 九步验收命令
- [ ] ① 认证读（balance/positions）——okx_rest / curl 直测：`GET /api/v5/account/balance?ccy=USDT`、`GET /api/v5/account/positions`（已验 ✓，历史记录：ALGO/LINK 在仓）
- [ ] ② 合约与精度核验——curl 直测：`GET /api/v5/public/instruments?instType=SWAP&instId=…`（ctVal / lotSz / minSz / tickSz）
- [ ] ③ 小额挂单——curl 直测：`POST /api/v5/trade/order`（限价，最小张数；写接口曾有 51001 故障，见台账 §5）
- [ ] ④ 撤单——curl 直测：`POST /api/v5/trade/cancel-order`
- [ ] ⑤ 市价成交——curl 直测：`POST /api/v5/trade/order`（ordType=market，最小名义）
- [ ] ⑥ 保护单覆盖核验——curl 直测：`POST /api/v5/trade/order`（attachAlgoOrds 附带 TP/SL）→ `GET /api/v5/trade/orders-algo-pending` 回读校验；注意 OKX 为「一单带 OCO」原子模型，**附带保护并非受理即生效**，必须回读确认（时效审计 §2 更正）
- [ ] ⑦ 棘轮改止损——curl 直测：`POST /api/v5/trade/amend-algos`（或撤旧挂新），验证旧单撤销 + 新 SL 生效
- [ ] ⑧ 平仓——curl 直测：`POST /api/v5/trade/close-position` 或反向市价；适配器：`close_position`
- [ ] ⑨ 台账对账——curl 直测：`GET /api/v5/account/bills`（已接）→ 与 `trades(venue='okx')` 逐笔对平；适配器：account_snapshot
- [ ] ⑩ 重启恢复——重启服务进程后回读 positions + orders-algo-pending，验证保护单仍在、状态一致（DESIGN §6）

---

## 3. 格 B：OKX-live（`www.okx.com` 实盘 key）

### 3.1 前置条件
- [ ] **用户单独授权：额度 / 标的 / 损失上限（缺一不开验）**
- [ ] 实盘 key + IP 白名单 + 提币权限关闭
- [ ] OKX-live 执行开闸 = 六格顺序最后一格（现网最后开）

### 3.2 九步验收命令
- [ ] ① 认证读——curl 直测：`GET /api/v5/account/balance?ccy=USDT`、`GET /api/v5/account/positions`（实盘 key 同域）
- [ ] ② 合约与精度核验——curl 直测：`GET /api/v5/public/instruments?instType=SWAP`
- [ ] ③ 小额挂单——人工执行：`POST /api/v5/trade/order`（最小张数限价）
- [ ] ④ 撤单——人工执行：`POST /api/v5/trade/cancel-order`
- [ ] ⑤ 市价成交——人工执行：最小名义市价单
- [ ] ⑥ 保护单覆盖核验——人工执行：attachAlgoOrds + `GET /api/v5/trade/orders-algo-pending` 回读
- [ ] ⑦ 棘轮改止损——人工执行：`POST /api/v5/trade/amend-algos`
- [ ] ⑧ 平仓——人工执行：`POST /api/v5/trade/close-position`
- [ ] ⑨ 台账对账——curl 直测：`GET /api/v5/account/bills` → `trades(venue='okx')`
- [ ] ⑩ 重启恢复——重启后回读仓位 + 条件单

---

## 4. 格 C：Binance-demo（`demo-fapi.binance.com` + Demo 专属 key）

### 4.1 前置条件
- [ ] Demo Trading 页面右上角 Account→API Management 创建的 **Demo 专属 key**（主站 key 不通用）
- [ ] IP 白名单；HMAC-SHA256 query 签名 + `X-MBX-APIKEY` 头
- [ ] Binance-demo 执行开闸状态 = 已授权（Gate-testnet 小额通过之后，见 §8 顺序图）

### 4.2 九步验收命令
- [ ] ① 认证读——curl 直测：`GET /fapi/v2/balance`（已验 ✓，USDT 5077.42）、`GET /fapi/v2/positionRisk`（✓ 无持仓）
- [ ] ② 合约与精度核验——curl 直测：`GET /fapi/v1/exchangeInfo`（stepSize / tickSize / minQty）
- [ ] ③ 小额挂单——curl 直测：`POST /fapi/v1/order`（LIMIT，GTC；positionSide 按 long/short 分仓模式）
- [ ] ④ 撤单——curl 直测：`DELETE /fapi/v1/order`
- [ ] ⑤ 市价成交——curl 直测：`POST /fapi/v1/order`（type=MARKET，reduceOnly=false）
- [ ] ⑥ 保护单覆盖核验——curl 直测：币安为「主单+独立条件单」两段式：`POST /fapi/v1/order`（STOP_MARKET / TAKE_PROFIT_MARKET，closePosition=true）→ `GET /fapi/v1/openOrders` 过滤 STOP/TP 类回读校验；开仓成功→立即挂双腿→回读→失败撤主单回滚
- [ ] ⑦ 棘轮改止损——curl 直测：撤旧 STOP_MARKET（`DELETE /fapi/v1/order`）+ 挂新（`POST /fapi/v1/order`），验证旧单不存在、新 SL 生效
- [ ] ⑧ 平仓——curl 直测：`POST /fapi/v1/order`（MARKET + reduceOnly=true）
- [ ] ⑨ 台账对账——curl 直测：`GET /fapi/v1/income`（REALIZED_PNL/FEE/COMMISSION）→ `trades(venue='binance')`
- [ ] ⑩ 重启恢复——重启后回读 positionRisk + openOrders（含 STOP/TP 条件单）
- [ ] 注：以上路径为 demo-fapi 与 fapi 同构契约；适配器步骤待 BinanceAdapter 私有执行面（P2）完成后回填「适配器」栏

---

## 5. 格 D：Binance-live（`fapi.binance.com`）

### 5.1 前置条件
- [ ] **用户单独授权：额度 / 标的 / 损失上限**；Binance live 执行走样本门槛（模拟先行，RESEARCH §3.7）
- [ ] LIVE key：只勾交易 + 提币关死 + IP 白名单
- [ ] Binance-live 执行开闸 = Gate-live 20U 之后（见 §8）

### 5.2 九步验收命令
- [ ] ① 认证读——curl 直测：`GET /fapi/v2/balance`、`GET /fapi/v2/positionRisk`
- [ ] ② 合约与精度核验——curl 直测：`GET /fapi/v1/exchangeInfo`
- [ ] ③ 小额挂单——人工执行：`POST /fapi/v1/order`（最小名义限价）
- [ ] ④ 撤单——人工执行：`DELETE /fapi/v1/order`
- [ ] ⑤ 市价成交——人工执行：最小名义 MARKET
- [ ] ⑥ 保护单覆盖核验——人工执行：STOP_MARKET + TAKE_PROFIT_MARKET 双腿 + openOrders 回读
- [ ] ⑦ 棘轮改止损——人工执行：撤旧挂新 STOP_MARKET
- [ ] ⑧ 平仓——人工执行：MARKET reduceOnly
- [ ] ⑨ 台账对账——curl 直测：`GET /fapi/v1/income` → `trades(venue='binance')`
- [ ] ⑩ 重启恢复——重启后回读 positionRisk + openOrders

---

## 6. 格 E：Gate-testnet（`api-testnet.gateapi.io` + Futures TestNet key）

### 6.1 前置条件
- [ ] APIv4 Keys 页 → **Futures TestNet APIKeys 专页 tab** 创建的专用 key（主站 key 对 testnet 域无效，历史实测 INVALID_KEY）
- [ ] 老域 `fx-api-testnet.gateio.ws` 已确认废弃，代码不得再引用
- [ ] Gate-testnet 执行开闸状态 = 已授权（Gate-dry 通过之后，见 §8）

### 6.2 九步验收命令
- [ ] ① 认证读——curl 直测：`GET /api/v4/futures/usdt/accounts`（已验 ✓，1360.06 USDT）、`GET /api/v4/futures/usdt/positions`（✓）、`GET /api/v4/futures/usdt/orders`（✓）
- [ ] ② 合约与精度核验——curl 直测：`GET /api/v4/futures/usdt/contracts/{contract}`（quanto_multiplier / order_size_min / order_size_round）
- [ ] ③ 小额挂单——curl 直测：`POST /api/v4/futures/usdt/orders`（最小张数限价）
- [ ] ④ 撤单——curl 直测：`DELETE /api/v4/futures/usdt/orders/{order_id}`
- [ ] ⑤ 市价成交——curl 直测：`POST /api/v4/futures/usdt/orders`（tif=ioc，最小张数）
- [ ] ⑥ 保护单覆盖核验——curl 直测：`POST /api/v4/futures/usdt/price_orders`（STOP/TP 独立条件单）→ `GET /api/v4/futures/usdt/open_price_orders` 回读校验
- [ ] ⑦ 棘轮改止损——curl 直测：`DELETE /api/v4/futures/usdt/price_orders/{id}` + 重挂新价单；dual_plus / decimal amount 支持情况见台账 §5
- [ ] ⑧ 平仓——curl 直测：`POST /api/v4/futures/usdt/orders`（reduce_only=true，tif=ioc）
- [ ] ⑨ 台账对账——curl 直测：`GET /api/v4/futures/usdt/my_trades` + 平仓结算流水 → `trades(venue='gate')`
- [ ] ⑩ 重启恢复——重启后回读 positions + open_price_orders
- [ ] 适配器栏：`exchanges/gate.py` 域名修复后全流程沙盒回归（Gate 两段式回滚流程已实现 e0712ad）

---

## 7. 格 F：Gate-live（`api.gateio.ws`）

### 7.1 前置条件
- [ ] **用户单独授权：额度（首验 20U 级）/ 标的 / 损失上限**
- [ ] LIVE key：只勾交易 + 提币关死 + IP 白名单
- [ ] Gate-live 执行开闸 = Gate-testnet 小额通过之后（六格中第一个开闸的 LIVE，见 §8）

### 7.2 九步验收命令
- [ ] ① 认证读——curl 直测：`GET /api/v4/futures/usdt/accounts`、`GET /api/v4/futures/usdt/positions`
- [ ] ② 合约与精度核验——curl 直测：`GET /api/v4/futures/usdt/contracts/{contract}`
- [ ] ③ 小额挂单——人工执行：`POST /api/v4/futures/usdt/orders`（≤20U 名义限价）
- [ ] ④ 撤单——人工执行：`DELETE /api/v4/futures/usdt/orders/{order_id}`
- [ ] ⑤ 市价成交——人工执行：最小名义 tif=ioc
- [ ] ⑥ 保护单覆盖核验——人工执行：price_orders 双腿 + open_price_orders 回读
- [ ] ⑦ 棘轮改止损——人工执行：撤旧挂新 price_orders
- [ ] ⑧ 平仓——人工执行：reduce_only=true tif=ioc
- [ ] ⑨ 台账对账——curl 直测：my_trades + 结算流水 → `trades(venue='gate')`
- [ ] ⑩ 重启恢复——重启后回读 positions + open_price_orders

---

## 8. 开闸顺序图（文字版）

来源：`THREE_VENUE_API_RESEARCH.md` §3 决策点 C（故事标注「DESIGN §5 决策点 C」，实际出处为 RESEARCH §3，本文件已显式更正，见 §9）。

```
Gate-dry（已就绪）
  └→ Gate-testnet 小额（先行环境垫底）
       └→ 币安 demo 小额（同构契约演练）
            └→ Gate-live 20U（六格中首个 LIVE，最小额度）
                 └→ 币安 live（样本门槛：模拟先行 + 样本垫底）
                      └→ OKX-live（现网，最后开）
```

- [ ] Gate-dry 通过（前置，非六格）
- [ ] Gate-testnet 九步全绿 → 开闸 Gate-live
- [ ] 币安 demo 九步全绿（在 Gate-testnet 之后，与 Gate-live 并行可）
- [ ] Gate-live 20U 九步全绿 → 开闸币安 live
- [ ] 币安 live 样本门槛达成 → 开闸 OKX-live
- [ ] OKX-live 现网九步全绿 → 六格收官

每步都有上一环境的样本垫底；任一格九步未全绿不得进入下一格（DESIGN §10：所有场所同一门槛）。

---

## 9. 与 THREE_VENUE_COORDINATION_DESIGN.md 的一致性与冲突标注

- [ ] P2（三所对称执行适配）→ 本文件各格 ②—⑧ 依赖适配器收编完成，命令栏已区分「curl 直测 / 适配器」，直测通过不冒充适配器通过 —— 与 DESIGN §10 P2 一致
- [ ] P3（组合风险原子预留、可解释路由）→ LIVE 格前置条件中的额度授权对应 P3 预算约束 —— 一致
- [ ] P4（六组合逐格验收）→ 本文件即 P4 的落地清单；LIVE 授权另确认 —— 一致
- [ ] **冲突 1（以本文件为准）**：开闸顺序图故事依据写作「DESIGN §5 决策点 C」，经核对 DESIGN §5 为「风控融合」，开闸六步序实际出自 `THREE_VENUE_API_RESEARCH.md` §3 决策点 C。理由：时效审计（2026-09-10）确认 RESEARCH 环境矩阵章节为唯一记录六格开闸序的文本，DESIGN 未重复给出。
- [ ] **冲突 2（以本文件为准）**：DESIGN §10 九步含「兄弟腿清理」，本清单并入 ⑧平仓+⑩重启恢复 的兄弟腿核查（撤残留条件单），不单列第十步，以保持六格九步同构、逐格可比。理由：验收粒度归一优先于步骤计数，兄弟腿核查动作本身不删减。
- [ ] **冲突 3（以本文件为准）**：OKX 保护核验以「回读 orders-algo-pending 确认生效」为准，不采纳旧调研「受理即生效」表述（时效审计 §2 已更正）。

---

## 10. 未验项台账（合并时效审计 §3 + 设计文档遗留）

| # | 未验项 | 来源 | 验证方法 | 负责阶段 |
|---|---|---|---|---|
| 1 | Binance Demo WS 地址与用户流/密钥域对应（本机 202 空正文问题未解；旧记 `wss://dstream.binance.com` 不可靠） | 审计 §3-1；RESEARCH 环境矩阵 | 用 Demo 专属 key 建 WS user-data 连接，观察订单/持仓推送；202 空正文按端点逐一排查 | P0/P2 |
| 2 | Gate `api-testnet.gateapi.io` 对应的官网账户/测试产品说明（本机 403；SDK 服务器列表与新域实测有差异） | 审计 §3-2 | 官网文档快照 + testnet key 全流程实测交叉印证 | P0 |
| 3 | Gate 新增改单（amend）/ decimal amount / dual_plus 在用户 Demo 与 LIVE 账户上各自的实际支持 | 审计 §3-3 | testnet 与 live 各发一笔 decimal 数量订单 + 改单请求，分别记录结果进能力表 | P2 |
| 4 | 三所端点级限频表、响应头与 Demo 差异（不能发通用数字；旧表「~3 req/s」等仅为估算） | 审计 §3-4；DESIGN §11 | 按具体端点压测收集 4xx/响应头（X-MBX-USED-WEIGHT、OKX X-RateLimit、Gate X-Gate-RateLimit-Remaining） | P1 |
| 5 | OKX 51001 写接口故障根因（当日故障已消失，但根因未定；不能据此证实现存保护有效性） | 审计 §3 末条 + RESEARCH 事件记录 | 复现窗口期内抓取完整响应体/trace id 提交工单；平时按 ⑥⑦ 步持续验证附带保护生效 | P2（持续） |
| 6 | 六格 LIVE 验收（okx-live / binance-live / gate-live 全部 ❌ 未验；只读/mock/dry_run 通过不替代 live 成交） | 审计 §3-5；DESIGN §10 P4 | 按 §3/§5/§7 九步清单逐格执行，LIVE 须用户单独授权 | P4 |
| 7 | BinanceAdapter 私有执行面（当前仅公共行情；下单/双腿/回滚/棘轮未实现） | RESEARCH §3.2/3.3；DESIGN §10 P2 | demo-fapi 九步全流程演练后回填适配器栏 | P2 |
| 8 | OKXAdapter 收编（现散在 okx_trade_service，未统一进 BaseExchangeAdapter） | RESEARCH §3.2；DESIGN §10 P2 | 收编后六格 ①—⑨ 适配器栏回归 | P2 |
| 9 | 故障注入项未跑：主单响应丢失、条件单一腿失败、重复/乱序推送、限频、账户读断、凭证轮换、外部人工仓、demo 重置、同币三所订单 ID 碰撞 | DESIGN §10 | 逐项沙盒注入并记录 | P2/P4 |

- [ ] 台账每项关闭时回填验证日期、证据链接与结论；与时效审计 §3 同步销项

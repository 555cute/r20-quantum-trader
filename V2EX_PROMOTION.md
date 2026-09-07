# [开源] R20 量子交易系统 v7.5.1，新一代量化基底系统

各位 V 友大家好，

在加密货币量化实战中，传统的交易系统架构正面临严重的代际困境：
* **传统硬编码脚本 (网格 / CTA / 均线)**：逻辑全部写死在代码里，缺乏宏观多空感知与形态认知，面对频繁的洗盘、假突破与无序震荡，极其容易在单边行情中抗单爆仓或在震荡市中反复磨损手续费；
* **市面上的通用 Agent / LLM 包装层**：往往只是写了一个简单的 Prompt，把几十上百根 K 线的原始 JSON 无脑丢进上下文，单次推理消耗数万 Token，每天 API 账单比利润还高；更致命的是大模型偶尔发生数学与几何计算幻觉（买多止损反设在现价上方），实盘裸奔极度危险；且缺乏动态跟踪机制，**经常冲高浮盈几十 U 舍不得走，最终一路回撤变成割肉**。

为了解决这一系列工程与实战痛点，我用 Python (FastAPI) + Vue 3 打造并开源了这套**新一代量化基底系统 (Base Quantitative Architecture)** —— **R20 Quantum**。

它不再是一个固定写死的交易程序，而是一套**全策略插槽化、语义变量数据联动、零幻觉物理硬拦截、且具备自进化闭环能力**的量化决策底座。

* 🖥️ **在线实盘操盘大屏预览**：https://www.r20.cn
* 🌐 **GitHub 仓库**：https://github.com/555cute/r20-quantum-trader (欢迎 Star / Fork)
* 🧪 **自动化单测**：**325 项全栈单测 100% 绿灯全过**（覆盖微积分因果动力学、Fail-Closed 风控、事件总线与云端 OCO 联动）
* 📜 **开源协议**：MIT License (100% 完整开源，无任何闭门或付费版本)
* 💬 **官方交流群**：`655973677`

---

## ⚡ 三大量化范式技术架构与实战能力深度横向对比

![三大量化范式技术架构与实战能力深度横向对比](https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/v751_features_summary.png)

---

## 📸 系统全景一览 (v7.5.1 实机 1080P 超高清截图)

### 1. 前台双翼量化操盘大屏 (Bento 资产舱 + 纯本地轻量 K 线 + 真实资金费/手续费明细透传)
![前台操盘大屏](https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/v751_live_dashboard.png)
*放弃被 DNS 污染的外部 TradingView iframe 跨域外链，本地打入轻量级图表引擎，国内宽带/移动网络秒开直连，支持秒级增量蜡烛跳动与多指标同屏叠加（MA + BOLL + VWAP + SAR + BBI）。*

### 2. 机构级量化控制面 (策略编排、自进化心法、多模型投委会与物理风控)
![后台机构级控制面](https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/v751_admin_overview.png)

---

## 🔬 为什么称之为“新一代量化基底系统”？五大核心工程设计

### 1. 乐高式「四大核心策略插槽」架构 (Policy Pluggability)
系统将策略制定权、自进化权、物理风控权与多模型仲裁权彻底模块化、解耦为四大独立插槽：
* **SLOT 01 · 🎨 提示词策略工作室 (Prompt Studio)**：告别硬编码！支持前台可视化拖拽编排交易 System 纪律与 User 行情模板，模块自由启停、增删、排序，自描述 v4 方案跨环境一键导入导出；
* **SLOT 02 · 🧬 启发式自进化认知中枢 (Self-Evolution)**：每 6 小时自动读取真实已平仓订单台账，闭环复盘自省，沉淀白盒启发式心法，经 Evolution Shield 防污染护栏保护并享有 7~14 天动态半衰期淘汰；
* **SLOT 03 · 🛡️ Fail-Closed 物理硬拦截管线 (Interceptors Pipeline)**：4H 顺势铁律、真实 2.0R 盈亏比、ADX 震荡过滤、防跨标的 Beta 踩踏门禁，Python 代码层兜底拦截；
* **SLOT 04 · 🏛️ 对冲基金多模型投委会 (Council Pro)**：资深交易员席位提案制（Claude 3.5 / DeepSeek）、风控官席位反向挑刺质询（GPT-4o / Qwen）、CIO 席位终审裁决发单。

![提示词策略工作室](https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/v751_prompt_studio.png)

---

### 2. 提示词与实盘数据的「语义变量插槽联动引擎」
我们坚决反对把数十根 K 线的原始生硬 JSON 粗暴喂给大模型。我们在后端构建了**因果微积分动力学提炼层**，将市场多维信息抽象为即插即用的标准语义变量插槽：
* `{{macro_4h}}`：4H 宏观多空大浪通道定性与大级别结构；
* `{{calculus_1h}}`：1H 因果微积分动力学矩阵（严密计算速度 $v$、加速度 $a$、加加速度 $j$、物理曲率 $\kappa$ 与做功功率 $\Phi = v \cdot a$）；
* `{{smart_money}}`：顶级交易员大额资金流向与长短持仓深度比；
* `{{news_intelligence}}`：全网 10 分钟实时舆情与快讯情绪雷达；
* `{{trading_memory}}`：动态加载自进化沉淀的当前生效白盒交易心法；
* `{{account_positions}}`：持仓均价、动态止损线、历史峰值浮盈比与当前极值回撤率。

**单次全市场 6 大主流币综合推演仅消耗 2.5K ~ 3.5K Token，推演成本低于半分钱，网络传输与计算耗时压缩至毫秒级！**

![因果微积分动力学矩阵](https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/v751_calculus_factors.png)

---

### 3. 物理层彻底断绝模型幻觉：Fail-Closed 物理硬拦截
在实盘交易中，任何模型输出都必须被预设为“不可信”。
**AI 只有“提案权”，绝对没有“物理发单权”！**
底层构建了独立的 Python 物理硬拦截管线。哪怕大模型发生 100% 幻觉，只要不满足：
1. 几何价格合法性（买多必须 $SL < Entry < TP$）；
2. 真实数学期望盈亏比 $R:R \ge 2.0$；
3. 4H 宏观大势反向绝对否决（多头大浪下 100% 严禁开空摸顶）；
4. 跨标的防系统性 Beta 踩踏（全系统同向持仓严控 3 笔）；
底层的物理代码层将在毫秒内 **Fail-Closed 强制拒单**，杜绝任何穿仓与异常订单隐患。

![Fail-Closed物理硬拦截管线](https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/v751_interceptors_failclosed.png)

---

### 4. 彻底根治“浮盈不走变成割肉”：多空对称三阶利润棘轮 + 云端 OCO 秒级联动
针对量化实战中最痛心的“浮盈大幅回吐坐过山车”，系统落地了严密的多空对称动态机制：
* **Tier 1 (保本垫)**：浮盈达到 $+1.5\text{x ATR}$（约 $1.0R$ 且覆盖双边手续费）时，强制收紧止损至开仓成本位 $+0.20\%$，彻底切断亏损风险；
* **Tier 2 (波段锁利)**：浮盈达到 $+2.2\text{x ATR}$ 时，锁定成本上方至少 $+1.0\text{x ATR}$（锁定 35%~50% 扎实波段利润）；
* **Tier 3 (动能回撤主动止盈)**：峰值利润 $\ge 2.0\text{x ATR}$ 且从极值回撤超 $0.75\text{x ATR}$，或 1H 微积分做功功率出现负功率耗散（$\Phi < -0.12$）时，主动市价落袋，拒绝死等远端极度理想化的挂单；
* **云端 OCO 秒级联动**：本地棘轮保底线跃迁时，立即调用 `okx swap algo amend` 秒级推高交易所云端条件单，即使本地服务器断网重启，交易所层面的利润保护依然严丝合缝。

---

### 5. 启发式自进化认知中枢与策略秒级回滚
* **闭环自省与白盒心法**：每 6 小时自动审查真实平仓台账，逐单归因提炼启发式软约束，下个决策周期自动生效，不重启服务实现策略自主演进；
* **策略大一统版本快照 (Policy Snapshot)**：提示词策略、自进化心法库、拦截插件与投委会参数聚合为全局唯一 Hash，全链路订单 100% 溯源绑死。一旦调优异常，支持在 0.5 秒内原子覆写回滚至已知黄金基线。

![策略大一统快照与原子回滚](https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/v751_policy_snapshot.png)

---

## 🏛️ 对冲基金级多模型投委会机制 (Council Pro)

系统支持开启对冲基金式的 **多模型辩论与仲裁委员会**：
* **资深交易员席位**（如 Claude 3.5 Sonnet / DeepSeek-V3）针对全池标的逐一给出全要素提案；
* **风控质询席位**（如 GPT-4o / Qwen-2.5）专挑同行方案中的流动性陷阱与假突破漏洞反向辩论；
* **首席投资官 CIO 席位** 终审查决，决定采纳哪位交易员的方案发单，或在分歧过大时全盘 WAIT 观望。

![对冲基金多模型投委会](https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/v751_council_board.png)

---

## ⚡ 极速一键本地部署指南

系统提供了开箱即用的一键启动脚本与 Docker Compose 容器化方案：

### 方式一：一键启动脚本 (Ubuntu / Debian / macOS)
```bash
# 1. 克隆开源代码仓库
git clone https://github.com/555cute/r20-quantum-trader.git
cd r20-quantum-trader

# 2. 安装依赖并启动 (自动检测环境、构建前端并拉起守护)
pip install -r requirements.txt
chmod +x start.sh && ./start.sh
```

### 方式二：Docker Compose 容器化启动
```bash
docker-compose up -d --build
```
启动后在浏览器访问 `http://localhost:8080` 即可进入操盘终端与管理后台。

---

## 💬 交流与反馈

代码已 100% 完整开源在 GitHub，欢迎各位 V 友 Star 关注、拍砖指正、提 Issue 和 PR：

* 🖥️ **在线实盘大屏演示**：https://www.r20.cn
* 🌐 **项目 GitHub**：https://github.com/555cute/r20-quantum-trader
* 💬 **官方交流群**：`655973677`

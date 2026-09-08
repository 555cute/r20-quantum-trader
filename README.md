# R20 量子交易系统 (R20 Quantum Trading System)

<div align="center">

```
  ____ ____   ___    ___                  _                    _____             _ _             
 |  _ \___ \ / _ \  / _ \ _   _  __ _ _ __| |_ _   _ _ __ ___  |_   _| __ __ _  __| (_)_ __   __ _ 
 | |_) |__) | | | || | | | | | |/ _` | '__| __| | | | '_ ` _ \   | || '__/ _` |/ _` | | '_ \ / _` |
 |  _ </ __/| |_| || |_| | |_| | (_| | |  | |_| |_| | | | | | |  | || | | (_| | (_| | | | | | (_| |
 |_| \_\_____|\___/  \__\_\\__,_|\__,_|_|   \__|\__,_|_| |_| |_|  |_||_|  \__,_|\__,_|_|_| |_|\__, |
                                                                                               |___/ 
```

[![Release](https://img.shields.io/badge/release-v7.6.0-orange.svg?style=flat-square)](https://github.com/555cute/r20-quantum-trader/releases/tag/v7.6.0)
[![LINUX DO](https://img.shields.io/badge/Community-LINUX%20DO-F97316?style=flat-square&logo=linux&logoColor=white)](https://linux.do/)
[![License](https://img.shields.io/badge/license-MIT-green.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg?style=flat-square)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg?style=flat-square)](https://fastapi.tiangolo.com/)
[![Vue 3](https://img.shields.io/badge/Vue-3.5%2B-4FC08D.svg?style=flat-square)](https://vuejs.org/)
[![Chart](https://img.shields.io/badge/Chart-KLineChart%20v10%20Native-blue.svg?style=flat-square)](https://klinecharts.com/)
[![CI](https://github.com/cnlimiter/r20-quantum-trader/actions/workflows/ci.yml/badge.svg)](https://github.com/cnlimiter/r20-quantum-trader/actions/workflows/ci.yml)

[![GHCR](https://img.shields.io/badge/GHCR-ghcr.io%2Fcnlimiter%2Fr20--quantum--trader-blue.svg?style=flat-square&logo=github)](https://github.com/cnlimiter/r20-quantum-trader/pkgs/container/r20-quantum-trader)

**新一代机构级加密货币波段量化决策与执行系统 · AI 投委会大模型驱动**  
🔥 **核心亮点：全栈策略全要素 100% 深度可自定义 · 17 项执行层硬风控后台可视化 · 三套优质风控预设一键应用 · 0.5s 策略快照原子回滚**  
*插槽化自由编排 · 白盒自进化认知复盘 · Fail-Closed 物理硬拦截 · 多模型决策委员会 · 原生云端 OCO 风控*

[📸 系统全景一览](#-系统全景一览) · [🎯 策略全要素 100% 自定义](#-核心设计哲学策略全要素-100-深度可自定义-full-stack-customizability) · [1. 提示词策略工作室](#1--提示词策略工作室-prompt-studio) · [2. 自进化认知中枢](#2--启发式自进化认知中枢-self-evolution) · [3. 物理拦截插件](#3-️-fail-closed-python-物理硬拦截管线-interceptors) · [4. 多模型投委会](#4--对冲基金多模型决策委员会-council-pro) · [5. 模型网关配置](#5--大模型网关与全局推理配置中心-llm-hub) · [6. 策略大一统版本快照](#6--策略大一统版本快照归档与-05s-原子回滚-policy-snapshot) · [7. 风控管理中心](#7-️-执行层风控管理中心与优质预设套件-risk-control-center) · [☕ 赞赏支持](#-赞助与支持-sponsor--donate) · [🐧 LINUX DO 社区](https://linux.do/) · [🚀 极速部署](#-极速部署指南) · [发行版日志](https://github.com/555cute/r20-quantum-trader/releases)

<br/>

> 💬 **官方交流 QQ 群**：**`655973677`** ｜ 🐧 **开源社区**：**[LINUX DO (linux.do)](https://linux.do/)** ｜ 欢迎量化交易员、提示词工程师、大模型 Agent 开发者共同交流探讨实战心得！

<table align="center">
  <tr>
    <td align="center" style="border: none;">
      <a href="#-赞助与支持-sponsor--donate">
        <img src="docs/images/wechat_reward_qrcode.png" width="160" alt="微信赞赏码 · 梁文绷" style="border-radius: 8px;" />
      </a>
      <br/>
      <span style="font-size: 12px; color: #888;">☕ <b>支持作者</b> · 微信扫码赞赏 (梁文绷)</span>
    </td>
  </tr>
</table>

</div>

---

## 🔒 数据流向与隐私 (Data Flow & Privacy)

**本系统不内置任何默认第三方中继网关。** 所有 LLM 出口必须由使用者显式配置：

| 配置项 | 说明 |
| :--- | :--- |
| `LLM_BASE_URL` / `OPENAI_BASE_URL` | 决策提示词的发送目标（OpenAI 兼容端点、官方直连或自建中继均可） |
| `LLM_API_KEY` | 对应密钥，支持后台加密存储 |
| `R20_OKX_ENV=demo` | 默认模拟盘，实盘需显式切换 |

- **透明审计**：`GET /api/v1/health` 会返回 `data_flow.llm_endpoint_host`，明确显示当前持仓与权益数据正发往哪个主机；未配置时返回 `NOT_CONFIGURED` 并在调用时抛出显式错误，**绝不静默回落到任何私有域名**。
- **提示词护栏**：白盒安全扫描仅拦截「解除硬约束」的意图（如"忽略 P0"、"取消止损"），不会误伤杠杆倍数、保证金金额等正常参数调优表述；命中时报错会附带**具体命中片段**便于改写。
- **权限分离**：LLM 仅有"提案权"，下单前必须通过 Python 层 Fail-Closed 物理硬拦截（几何合法性、R:R ≥ 2.0、4H 顺势否决、同向持仓上限）。

---

## 📸 系统全景一览

R20 量子交易系统由**前台双翼量化操盘终端**与**后台机构级控制面**共同构成：

### 1. 前台双翼量化操盘大屏（Bento 资产舱 + KLineChart v10 原生 K 线工作站 + 资金费/手续费明细全透传）
![前台操盘大屏](docs/images/v760_live_dashboard.png)

### 2. 后台机构级量化控制面（核心策略、数据健康与物理风控全景监控）
![后台机构级控制面](docs/images/v760_admin_overview.png)

### 3. 执行层风控管理中心（17 项硬风控可视化 + 三套优质预设一键应用 · v7.6.0）
![执行层风控管理中心](docs/images/v760_risk_control.png)

---

## 🎯 核心设计哲学：策略全要素 100% 深度可自定义 (Full-Stack Customizability)

> **“策略制定权属于交易员，而非写死的系统硬代码。”**

传统量化软件往往将交易标的、仓位杠杆、止盈止损与研判逻辑死死硬编码在底层脚本中，调整极为繁琐且缺乏回溯能力。  
**R20 量子交易系统彻底摒弃黑盒硬编码**，系统中的**输入特征、标的池、提示词语义插槽、多模型投委会、物理风控门禁、利润棘轮与版本快照**全部解耦为插槽化组件，**100% 允许交易员自由定制与热插拔**：

```
                             ┌───────────────────────────────────────────────────────────┐
                             │       R20 全要素 100% 深度可自定义策略架构 (Modular & Pluggable)    │
                             └─────────────────────────────┬─────────────────────────────┘
                                                           │
     ┌───────────────────────┬─────────────────────────────┼─────────────────────────────┬───────────────────────┐
     ▼                       ▼                             ▼                             ▼                       ▼
┌──────────────────┐  ┌──────────────────┐        ┌──────────────────┐          ┌──────────────────┐  ┌──────────────────┐
│ 1. 标的池全要素   │  │ 2. 提示词与插槽  │        │ 3. 执行层风控中心 │          │ 4. 投委会席位矩阵 │  │ 5. 策略大一统快照 │
│    100% 自定义   │  │    100% 自定义   │        │    100% 自定义   │          │    100% 自定义   │  │ 0.5s 秒级回滚    │
├──────────────────┤  ├──────────────────┤        ├──────────────────┤          ├──────────────────┤  ├──────────────────┤
│• 任意OKX合约增删 │  │• System/User 重写│        │• 17项硬风控管理页│          │• 参谋席位自由增删│  │• 4大策略单元打包 │
│• 面值/精度/最小量│  │• 积木式消息管线  │        │• 三套预设一键应用│          │• 席位模型/温度解耦│ │• 唯一 SHA-256 溯源│
│• 基础仓位/风险额 │  │• 语义数据变量插槽│        │• Python插件+沙箱测│          │• 辩论/仲裁流程可调│ │• 0.5s 原子级热回滚│
│• 杠杆上限/ATR倍数│  │• JSON 方案导入导出│       │• 单一事实源全联动│          │• 思考链超时自由配│  │• 台账 100% 溯源绑定│
└──────────────────┘  └──────────────────┘        └──────────────────┘          └──────────────────┘  └──────────────────┘
```

### 🛠️ 7 大维度全流程深度自定义一览表

| 维度 | 自定义支持范围 | 配置与生效方式 | 业务收益 |
| :--- | :--- | :--- | :--- |
| **1. 标的池与合约规格** | 任意 OKX 永续合约/币种扩展、面值 (`ctVal`)、报价精度 (`tickSz`)、最小张数 (`minSz`)、基础仓位 (`base_sz`)、单笔风险暴露 (`risk_per_trade_usd`) | 前后台动态更新 / 编辑 `data/instrument_pool.json` | 告别固定 6 币种限制，自由交易任意心仪山寨币或主流币 |
| **2. 标的分级与风控参数** | 标的分级（Tier-1 蓝筹 / Tier-2 高弹性动量币）、杠杆上限（2x~5x 自由配置）、宽止损 ATR 乘数、24H 最低流动性门槛 | 标的池分级管理配置，热生效 | 蓝筹大资金防滑点，小币种宽止损防插针，差异化风控 |
| **3. 提示词策略工作室** | 「交易 System」军规与「交易 User」模版完全可改，自由启停稳健/激进/自定义方案，积木式卡片拖拽 | 前台 Prompt Studio 可视化拖拽编排，支持 JSON 导入/导出 | 任意注入专属于交易员自己的交易哲学与研判流派 |
| **4. 语义变量插槽联动** | 内置 `{{macro_4h}}`、`{{calculus_1h}}`、`{{smart_money}}`、`{{account_positions}}`、`{{trading_memory}}` 数据插槽 | 一键点击或键入插槽变量，毫秒级绑定底层实时特征 | 自由组合输入特征，大模型上下文仅传输精炼高信息密度数据 |
| **5. 物理风控拦截插件** | 4H 顺势铁律、75%~85% 动态置信度门禁、1H ADX 趋势杂波过滤、2.0R 真实盈亏比门禁、自定义 Python 拦截插件 | 独立 Python 插件文件，支持前台在线沙箱单步模拟单测 | 策略规则完全透明可见，杜绝模型幻觉，底座代码物理兜底 |
| **6. 多模型投委会与模型矩阵** | 参谋席位自由增删（进攻官/风控官/数理官/宏观官/舆情官/自定义专家）、席位独立绑定不同大模型与温度、10~1800s 思考超时预算 | 前台 Council Pro 席位编辑与模型网关直连，支持双轮辩论与单模型极速直出 | 彻底终结单模型认知偏见，长思考链大模型与极速模型混部调度 |
| **7. 执行层风控管理中心** | 最高持仓数、同向敞口、单笔保证金占比、单标的保证金封顶、杠杆上限、1R 风险额、R:R 硬底线、开仓/加仓置信度门禁、日亏熔断、最长持仓时间、止损冷静期、金字塔加仓门禁等 17 项 | 后台风控管理页集中配置或三套优质预设一键应用，`.env` 持久化，下一巡检周期热生效 | 告别写死在 py 里的黑盒阈值，风控口径提示词/执行层/管理页三位一体 |

---

## 🏛️ 系统核心策略系统能力详析

### 1. 🎨 提示词策略工作室 (Prompt Studio)

告别硬编码！提示词策略工作室支持交易员在前台可视化编排 **「交易 System」** 纪律与 **「交易 User」** 行情组装模版，将市场快讯、自进化心法、微积分动力学矩阵等动态数据抽象为标准语义变量插槽：

![提示词策略工作室](docs/images/v760_prompt_studio.png)

- **模块自由插拔与自定义重写**：支持捕捉确定性波段、核心军规、止损防线等多模块拖拽排序与单项启停；交易员可随时覆写每一段 System 与 User 规则；
- **标准化语义变量插槽**：内置 `{{macro_4h}}`、`{{calculus_1h}}`、`{{smart_money}}`、`{{news_intelligence}}`、`{{trading_memory}}`、`{{account_positions}}` 等丰富数据插槽，点击一键注入；
- **实时模板组装预览**：右侧高保真预览真实注入后的拼接文本与源码，排查格式一目了然；
- **策略方案导入与导出**：一键保存当前自定义方案、复制副本、查看历史版本，支持以标准化 JSON 策略包跨环境无缝迁移与分享。

---

### 2. 🧬 启发式自进化认知中枢 (Self-Evolution)

绝不在回撤期情绪化乱改参数！系统每 6 小时自动读取全量实盘平仓台账进行闭环自省与归因，将真实交易教训沉淀为白盒启发式心法，并由防污染护栏严格捍卫：

![自进化认知实验室](docs/images/v760_self_evolution.png)

- **实盘台账自动穿透**：实时统计复盘样本数、综合胜率、利润因子（PF），精准剖析多空双杀、手续费摩擦损耗与执行偏差；
- **白盒黄金心法沉淀**：以 `data/structured_trading_memory.json` 为权威存储，生成 `AI_TRADING_MEMORY.md` 展示投影；只把启用且未过期的心法注入交易决策；
- **防污染护栏 (Evolution Shield)**：离群噪点剔除、宪法级防偏见红线；支持一键回滚至官方黄金基准；
- **心法敏锐半衰期 (7~14天)**：动态评估历史心法时效，自动衰减并淘汰过期经验，杜绝因旧周期杂波导致过度拟合。

Docker 镜像和源码都包含基准心法预设，但首次启动不会自动写入或启用。尚未初始化时，前台会显示明确空状态，并链接到管理后台「自进化」；由管理员显式初始化/回滚基准，或由成功的复盘生成。已存在但全部禁用/过期、读取失败与网络失败分别展示，不会永久停在「加载中」。

---

### 3. 🛡️ Fail-Closed Python 物理硬拦截管线 (Interceptors)

**“认知决策归模型，物理风控归底座”**。系统绝不把止损与风控寄托于 LLM 提示词本身的自律，而是在交易执行底层构筑了不可跳过的 Python 物理插件流水线：

![物理拦截插件管线](docs/images/v760_interceptors_failclosed.png)

- **Fail-Closed 故障硬切断**：任何拦截插件报错或超时，开仓动作一律无条件强制熔断拦截；
- **纯 Python 插件化解耦（交易员可自由定制与编写插件）**：
  1. `#1 4H 宏观大周期顺势铁律` (`01_macro_trend_filter.py`)：4H 多头通道严禁逆势摸顶开空，空头承压严禁逆势抄底接飞刀；
  2. `#2 高置信度质量门禁` (`02_confidence_gatekeeper.py`)：置信度门槛可自定义（默认低于 75% 强制降级 WAIT）；
  3. `#3 1H ADX 趋势强度门禁` (`03_adx_volatility_filter.py`)：过滤无序震荡垃圾市，1H ADX 阈值可自定义（默认 < 18 严禁开仓）；
  4. `#4 真实 2.0R 盈亏比门禁` (`04_risk_reward_gatekeeper.py`)：执行层真实盈亏比几何校验，严禁亏损概率不对称的劣质交易；
- **在线沙箱回归测试**：支持在界面直接输入模拟报价参数，现场单步测试每道拦截插件的通过/拒绝表现。

---

### 4. 👥 对冲基金多模型决策委员会 (Council Pro)

引入现代顶级对冲基金 Trading Desk 运作机制，彻底终结单一大模型决策的幻觉与认知盲区：

![对冲基金多模型决策委员会](docs/images/v760_council_board.png)

- **参谋席位 100% 自由定义**：
  - 支持任意添加、删除与就地编辑参谋角色（动量进攻官、保守风控官、量化数理官、宏观策略官、舆情侦察官，或自定义 Expert 参谋）；
  - 每个席位可**独立绑定大模型厂商**（如进攻官配 Claude 3.7，风控官配 DeepSeek-R1，数理官配 GPT-4o），独立指定系统提示词与温度；
- **双轮质询互评模式 (Cross-Exam)**：第一轮独立提案 ➔ 第二轮同行交叉漏洞质询辩论 ➔ 第三轮 CIO 统筹终审查阅拍板；支持一键切换为单模型 2 秒极速直出；
- **首席投资官 (CIO) 统筹终审发单**：综合多方辩论成果，统一校验资金占用与跨标的敞口，输出符合 JSON Schema 规范的标准化执行指令。

---

### 5. 🤖 大模型网关与全局推理配置中心 (LLM Hub)

全开放模型供应商直连矩阵与思考链预算管理，为推理型大模型与轻量极速模型提供工业级调度中枢：

![模型网关与全局推理配置](docs/images/v760_llm_hub.png)

- **多供应商自由接入**：原生兼容 OpenAI、Claude、Gemini、DeepSeek、Qwen 等主流模型厂商 API；
- **全局思考推演超时配置 (10~1800s)**：针对 DeepSeek-R1、o1/o3、Claude 3.7 Thinking、Gemini 3 Pro 等长思考链旗舰模型，前台自由调配思考等待预算，杜绝硬编码超时过早截断；
- **推理强度与采样温度解耦**：支持针对不同参谋席位独立绑定模型与参数，实现计算资源效费比最优配比。

---

### 6. 📦 策略大一统版本快照归档与 0.5s 原子回滚 (Policy Snapshot)

针对多组件联动修改可能引发的“策略撕裂”与“回测无法复现”难题，R20 打造了工业级策略快照引擎：

![策略大一统版本快照](docs/images/v760_policy_snapshot.png)

- **四大策略单元聚合指纹**：将「提示词模版」、「物理拦截插件」、「投委会席位配置」与「标的池风控参数」四合一，计算出唯一不可变的 SHA-256 策略版本标识（如 `v7.5.1@8b2c4e1f`）；
- **策略方案具名归档**：支持将当前调试满意的全套自定义策略一键归档为独立文件（如“激进突破版”、“牛市趋势追随版”），随时切换；
- **0.5s 秒级一键原子回滚**：当新策略表现不佳时，可在控制面一键秒级回滚到任意历史归档版本。回滚采用原子操作，前置自动快照备份并校验哈希，杜绝局部状态残留；
- **全链路台账 100% 溯源绑定**：每一笔开仓与平仓订单，后台均强制锁定记录开仓当时的 `policy_version` 与 `policy_hash`，真正实现“哪笔交易用了哪套策略，毫秒级精准溯源”。

---

### 7. 🎛️ 执行层风控管理中心与优质预设套件 (Risk Control Center)

v7.6.0 起，全部执行层硬风控参数从 py 源码中彻底剥离，收敛至后台「风控管理页」集中可视化配置：

![执行层风控管理中心](docs/images/v760_risk_control.png)

- **17 项硬风控全量可配**：最高持仓数、同向敞口上限、单笔保证金占比、单标的累计保证金封顶、杠杆上限、单笔 1R 风险额、最小盈亏比 R:R 硬底线、开仓/加仓 AI 置信度门禁、日亏熔断（比例+绝对双封顶）、最长持仓时间（时间止损）、止损冷静期、金字塔加仓三重门禁等；
- **三套优质预设一键应用**：🛡️ 稳健防守（新账户/恶劣行情：禁加仓、3x 杠杆、85% 高门禁）、⚖️ 均衡波段（推荐基线：攻守兼备）、🚀 进取猎手（单边趋势市：同向 4 仓、2 次浮盈加仓、16h 大波段呼吸空间）；页面实时标记「当前生效」，支持应用后逐项微调与 RESET RISK 回退基线；
- **单一事实源架构**：`scripts/risk_constants.py` 为唯一参数源，执行引擎、核心风控底座（R:R 物理拦截）与后台管理页三方同源、import 时断言零漂移；
- **静态宪法 + 动态预算**：AI 主脑 System 提示词保持静态（策略快照缓存永不失效），全部实时风控阈值由每轮用户消息的【本周期风险预算】小节从单一事实源动态推导注入——**提示词口径永远等于代码口径**，改一处全链路对齐；
- **15 分钟热生效**：保存即写入 `.env`，交易引擎下一巡检周期自动加载，无需重启；越界值服务端物理拒绝，全部变更进入操作审计。

---

## 💰 资金规模适配（小资金账户可直接运行）

风控与仓位**全部按实际可用余额比例推导**，不存在任何写死的绝对金额，20U 也能正常开单。下表比例均为**默认值**，可在后台「风控管理页」按账户规模自由调整或一键套用预设套件：

| 维度 | 规则（默认值，均可配） | 20U 账户 | 4000U 账户 |
| :--- | :--- | ---: | ---: |
| 单笔 1R 风险额 | `min(池内配置, 余额 × 2%)` | 0.40 U | 15.0 U |
| 单笔保证金硬顶 | `余额 × 20%` | 4.0 U | 800 U |
| 单标的累计保证金 | `min(600, 余额 × 30%)` | 6.0 U | 600 U |
| 单日亏损熔断 | `min(150, 余额 × 5%)` | 1.0 U | 150 U |

- **张数按交易所 `minSz` 步长量化**（OKX 多数永续为 0.01 张），不再强制"至少 1 张"。
  旧实现会把 BTC 的 0.01 张（7.92 U 名义）放大成 1 张（792 U 名义 / 5x 需 158 U 保证金），
  导致小资金账户被拒单、中等账户静默开出远超风险预算的仓位。
- 资金规模不足以开出合规最小仓位（无法同时满足最小下单量、止损呼吸空间与 R:R ≥ 2.0）的标的，
  **直接跳过并在日志标注"资金不匹配"**，而不是放大仓位硬开。
- 全部阈值可在后台「风控管理页」可视化配置，或直接改 `.env`（见 [`env.example`](env.example) 的「执行层风控参数」段），二者同源。

> ⚠️ 实话：20U 虽然技术上可跑，但 Taker 0.05% 双边手续费 + 资金费率相对 1.8~2.2x ATR 的止损空间占比过高，
> 统计意义有限。建议用模拟盘或 ≥300U 实盘验证策略。

---

## Binance USD-M 接入

在 `/admin/security` 的“交易所”中选择 **Binance USD-M USDT 永续**，默认使用 DEMO。分别配置 Binance DEMO / LIVE API Key 与 Secret；空白字段不覆盖已有密钥，密钥以加密方式保存，不回显明文。

- 行情、账户、挂单、限价下单、杠杆、保护单、平仓和台账统一走所选交易所适配器；内部数量统一为币数量，只有 OKX 适配层转换为合约张数。
- 切换需输入具体目标短语，例如 `SWITCH BINANCE DEMO`。页面加载不自动测试凭证或刷新私有账户；交易任务另有目标环境确认。
- Binance 保护为两条配对条件单，**不是原子 OCO**。未知提交、第二条保护失败、部分成交及保护更新均有独立处理。
- 账户状态按交易所、环境和凭证指纹隔离；历史范围或费用换算不能证实时标记不完整，不伪造完整台账或历史杠杆。

部署参数、保护机制差异及历史数据边界见 [STANDALONE.md](STANDALONE.md)。

## 🚀 极速部署指南

完整进程所有权与恢复说明见 [STANDALONE.md](STANDALONE.md) 和 [RECOVERY_GUIDE.md](RECOVERY_GUIDE.md)。控制面包含会执行交易的管理接口，不是只读服务。模型输出仍须通过基础风控和执行层检查；可配置插件不等于所有规则都不可关闭。交易所受理订单不等于成交，保护委托不能保证零损失。

### 方式 A：源码直接部署 (Python 3.11/3.12 / Node.js 22.12+)

#### 1. 克隆代码与配置环境变量
```bash
git clone https://github.com/cnlimiter/r20-quantum-trader.git
cd r20-quantum-trader

cp env.example .env
vim .env  # 填写您的 OKX API 与大模型凭据 (例如 OpenAI / Gemini / DeepSeek)
```

#### 2. 安装依赖并启动
```bash
# 1. 安装后端 Python 依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install -g @okx_ai/okx-trade-cli@^1.4.4

# 2. 编译打包现代化 Vue 3 前端操盘终端 (基于原生高性能 KLineChart v10)
cd frontend
npm ci
npm run build
cd ..

# 3. 首次仅启动控制面，不自动交易或轮询账户
R20_GATEWAY_WORKER_ENABLED=0 R20_DASHBOARD_WORKER_ENABLED=0 \
  python -m uvicorn r20_backend.app:app --host 127.0.0.1 --port 8080
```

PowerShell 使用 `$env:R20_GATEWAY_WORKER_ENABLED="0"` 和 `$env:R20_DASHBOARD_WORKER_ENABLED="0"` 设置开关，再启动 Python。默认启用 worker 时，后端会拉起 Gateway 调度交易。仅选一个管理方：后端托管，或配套 systemd 的独立 Gateway；不得同时运行旧 scheduler、QwenPaw cron、独立 dashboard。行情 REST 主路径仍有 CLI 依赖，不能视为完全零子进程。

---

### 方式 B：Docker / Docker-Compose 容器化一键启动 (推荐)

默认 `docker-compose.yml` **只拉取** GitHub Container Registry 镜像，不含本地 `build`，避免 `docker compose up` 误跑源码构建或混用远端旧 `latest`。镜像：[`ghcr.io/cnlimiter/r20-quantum-trader`](https://github.com/cnlimiter/r20-quantum-trader/pkgs/container/r20-quantum-trader)。默认分支推送、`v*` 标签和手动发布先通过 Linux/Windows、Python 3.11/3.12 测试及前端构建，再发布镜像；`latest` 仅跟随 GitHub 默认分支，不硬编码 `main`。首次发布默认为 private，公开仓库可在 Packages 页改为 public 后匿名拉取。私有包需 classic PAT（`read:packages`）：

```bash
echo "$CR_PAT" | docker login ghcr.io -u USERNAME --password-stdin
```

默认启动（宿主机 `.env` 仅作启动注入，`pull_policy: always`）：

```bash
cp env.example .env
docker compose up -d
```

未设置 `R20_SETUP_TOKEN` / `R20_ADMIN_TOKEN`（或仍是示例占位符）时，首次启动会各生成一次并打印到容器日志：`R20_SETUP_TOKEN` 是 `admin` 初始登录密码（不写盘，建号成功后从进程环境清除）；`R20_ADMIN_TOKEN` 是 `X-R20-Admin-Token` 管理头，写入 `./data/config/.env`。该头可在已有账号后调用 overview 等管理 API，不能登录控制台、不能当登录密码、不能做用户管理或 `require_superadmin` 操作。已有值后不会再生成或打印。显式配置的 setup token 不会被改写。




#### 已运行实例：一条命令更新，可回滚

在 **Linux Docker 宿主机** 的现有 Compose 目录执行。仅安装了 Compose 的服务器，先从要部署的已审阅源码版本复制 `deploy/update_docker.py` 到该目录下的同名路径；脚本只依赖 Python 3 标准库与 Docker Compose v2，不需要安装应用依赖。不要覆盖服务器定制的 Compose、`.env`、DNS、端口或网络配置。

```bash
# 更新到默认分支最新通过 CI 的镜像；先拉取并解析 digest，再等待交易周期锁
python3 deploy/update_docker.py update --image ghcr.io/cnlimiter/r20-quantum-trader:latest

# 只读预检 / 查看当前版本、健康状态、回滚目标
python3 deploy/update_docker.py update --image ghcr.io/cnlimiter/r20-quantum-trader:latest --dry-run
python3 deploy/update_docker.py status

# 使用本机保留的上一镜像回滚，不拉取、不回退业务数据
python3 deploy/update_docker.py rollback
```

- 必须是已有的单实例服务，`/app/data` 使用可写 bind mount，镜像保留 `${R20_IMAGE:-...}`。命令不管理首次安装、远程 Docker context 或多副本更新。
- 保留宿主机 Compose 及挂载目录，只修改启动 `.env` 的 `R20_IMAGE` 为解析后的不可变 digest；后台密钥/设置仍在 `data/config/.env`。部署历史记录于 `.r20-deploy-state.json`。
- 更新前按固定顺序等待 `.ai_factor_trader.lock` 和 `.ai_brain_cycle.lock`，同时保护常规因子周期与独立主脑调用；锁超时不替换容器。仅重建 R20 服务，等待健康检查，并校验镜像、挂载、DNS、端口与网络。检查到部署配置漂移会在更新前拒绝执行。
- 普通更新失败自动恢复上一镜像并验证健康；Docker 命令超时则保留 `pending` 恢复记录，不盲目发起第二次重建。先检查 `status` 与 Docker 状态，确认先前操作已经结束后再执行 `rollback`。
- 指定已核验版本可把 `--image` 改为 `仓库@sha256:...`；也支持镜像站 digest。`--no-pull` 仅使用本地镜像。`--project-dir` 指定部署目录，多个 `--file` 可保留原有 Compose overlay。不要清理仍需回滚的旧镜像；本命令不会 prune 镜像、删除卷或覆盖业务数据。
- 后续本地修改流程：提交并推送默认分支 → 等待 **Docker Package** 成功 → 执行更新命令。服务器不自动追踪每次提交，避免未经确认重启交易进程。

模型输出上限可在后台 LLM 设置中保存，默认 `4096`，也支持模型专属上限（留空继承全局）。Responses 使用 `max_output_tokens`；Chat 与 Claude 使用各自协议字段。输出截断、拒绝、空内容、HTTP 外层 JSON 和业务 JSON 错误分别记录；无效 JSON 不补全、不作为交易指令执行。大上下文和高思考强度下仍需按供应商能力设置上限，短探针成功不代表真实交易长提示词一定成功。

自进化模型失败，或响应省略 `asset_multipliers`、返回空映射/错误类型时，不写 `asset_multipliers.json`：已有文件保持原样，缺失文件保持缺失。只有显式返回非空倍率映射时，才按原有边界写入倍率。



从当前源码构建（独立 overlay，镜像名为 `r20-quantum-trader:local`，不拉 GHCR）：

```bash
cp env.example .env
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

宿主机 `.env` 通过 compose `env_file` 注入初始值（含 worker 开关）。后台写入的配置落在已挂载的 `./data/config/.env`（容器内 `/app/data/config/.env`，由 `R20_ENV_FILE` 指定），并覆盖这些初始值。不要把单个 `.env` 文件 bind-mount 进容器。源码部署默认仍是项目根目录 `.env`，不要为原生安装设置 `R20_ENV_FILE`。

- `docker compose stop` / `start`：复用同一容器，数据卷与后台配置都还在。
- `docker compose up -d --force-recreate` 或 `down` 后 `up`：容器层被替换，但 `./data` 卷保留，后台保存的交易所选择等设置仍在。旧镜像把后台配置写在容器内 `/app/.env`，重建会丢失。
- 已在运行的旧容器若改过后台配置，升级或重建前请自行把容器内 `/app/.env` 安全导出到数据卷的 `data/config/.env`。不要把明文凭证写入归档或提交到版本库。
- 打包的 data 备份会排除该配置文件，须单独安全保管。

---

### 🖥️ 访问与管理

服务启动成功后，浏览器直接访问：  
- 🖥️ **前台量化操盘工作台**：`http://localhost:8080/`  
- ⚙️ **后台策略管理控制面**：`http://localhost:8080/admin/login`（默认系统账号 `admin`，首次启动进入系统后可自由设定高强密码）

---

## 🧪 全栈自动化测试保障

系统配备了涵盖物理风控几何拦截、策略版本快照、多模型仲裁、OKX 鉴权与前后端 API 契约的完整自动化测试套件：

```bash
R20_GATEWAY_WORKER_ENABLED=0 R20_DASHBOARD_WORKER_ENABLED=0 R20_TESTING=1 \
  python3 -m unittest discover -s tests -t . -p "test_*.py"
```

> `-t .` 使 `tests` 作为包导入，触发 `tests/__init__.py` 的环境隔离——
> 即使生产 `.env` 已应用自定义风控套件，测试仍以代码默认基线断言。

测试结果以当前命令输出为准，静态数量不代表通过。测试需要隔离数据和外部请求；关闭 worker 不会拦截显式调用的网络方法。回测使用独立 MA 参考策略，并非 LLM/投委会实盘回放；缺失行情返回 incomplete/非零退出，不生成合成行情或覆盖成功报告。图表行情失败也不生成模拟蜡烛，已有真实缓存可明确标记为 stale。

---

## ⚠️ 免责声明 (Disclaimer)

1. 本项目为**开源量化交易系统与研究工具**，仅供学习、研究与模拟盘回测使用；
2. 加密货币市场具有极高风险与剧烈波动，任何历史回测与量化模型均无法保证未来收益；
3. 强烈建议先在 OKX **DEMO 模拟盘**环境下充分验证策略与风控逻辑；
4. 开发者不对任何因使用本项目产生的直接或间接投资损失承担责任。

---

## ☕ 赞助与支持 (Sponsor & Donate)

如果 **R20 量子交易系统** 对您的量化策略构建、实盘博弈或开源研究有所助益，欢迎请作者喝杯咖啡 ☕，您的慷慨赞赏是驱动系统长期维护与算法演进的最大动力！

<div align="center">

<img src="docs/images/wechat_reward_qrcode.png" width="250" alt="微信赞赏码 · 梁文绷" />

<br/>

**微信扫码赞赏支持作者 · 梁文绷**

</div>

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 协议完全开源自由使用。

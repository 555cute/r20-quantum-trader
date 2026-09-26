<div align="center">

[**简体中文**](README.md) · [English](README.en.md)

# AstraQuant

### 机构级多交易所平权量化决策与执行系统

[![Release](https://img.shields.io/badge/Release-v8.3.1-blue.svg?style=flat-square)](https://github.com/555cute/astra-quant-agent/releases/tag/v8.3.1)
[![Website](https://img.shields.io/badge/Site-www.astraquant.tech-6E56CF.svg?style=flat-square)](https://www.astraquant.tech)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat-square)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg?style=flat-square)](https://fastapi.tiangolo.com/)
[![Vue 3](https://img.shields.io/badge/Vue-3.5%2B-4FC08D.svg?style=flat-square)](https://vuejs.org/)
[![Tests](https://img.shields.io/badge/Tests-10k%2B%20Passing-brightgreen.svg?style=flat-square)](tests/)
[![Community](https://img.shields.io/badge/Community-LINUX%20DO-F97316.svg?style=flat-square&logo=linux&logoColor=white)](https://linux.do/)

**命名 AI 席位交叉质询 → CIO 终审采纳 → 执行层物理硬风控 → OKX / Binance / Gate 三所真实下单**

*决策归大模型，风控归物理底座，订单真的下去了 —— 全链路白盒可审计。*

[60 秒跑起来](#-60-秒跑起来) · [它是什么](#-它是什么) · [四个设计原则](#-四个设计原则) · [界面](#-界面) · [策略配置中心](#-策略配置中心) · [资金与风控](#-资金规模与分级风控) · [部署](#-部署) · [架构索引](#architecture-index) · [品牌与代号](#-品牌与内部代号改名时必读)

</div>

> 🌟 **本项目首发并深度链接认可 [LINUX DO (linux.do)](https://linux.do/) 开源技术社区，致敬真诚、开放的技术交流精神！**

---

## 🚀 60 秒跑起来

```bash
git clone https://github.com/555cute/astra-quant-agent.git && cd astra-quant-agent
./deploy/docker-start.sh          # Docker 一键起（推荐）；宿主机部署走 ./deploy/install.sh
```

启动后：

| 入口 | 地址 |
|---|---|
| 前台操盘大屏 | `http://localhost:8080/` |
| 后台控制面 | `http://localhost:8080/admin/login`（默认用户名 `admin`） |

> ⚠️ 需要**两套**凭证才能真跑：一个**大模型 API Key** + 一套**交易所 API Key**。
> 默认以**模拟盘 / Demo 环境**启动，配置齐全并显式切换后才会碰真实资金。

---

## 🧭 它是什么

AstraQuant 是一套面向专业交易团队与量化交易员的**多交易所平权量化决策与自动化执行操作系统**。

每 **15 分钟**一个主脑周期：命名 AI 席位（进攻 / 动量 / 数理 / 宏观等）各自独立研判并提出交易意图，**CIO 席位采纳终审**给出最终裁决；裁决在触达交易所网关之前，必须逐条穿透**执行层 Python 物理风控管线**；通过后才以 Maker 限价单落到 **OKX / Binance / Gate**，止盈止损作为原生条件单原子挂载。

它**不是**一个把交易逻辑写死在脚本里的回测玩具：

- **投委会是真实的多模型协作** —— 每个席位可独立绑定不同厂商的大模型与推理温度，双轮交叉质询后由 CIO 仲裁；
- **风控是可调的物理管线** —— 26 项执行层硬风控旋钮从源码里剥离到单一事实源，在后台集中可视化配置，拦截插件报错或超时一律 **Fail-Closed 强制熔断**；
- **策略是数据，不是代码** —— 提示词、投委会席位、风控阈值、模型路由全部可在界面里改，改完存成带 SHA-256 指纹的版本快照，可 0.5 秒原子回滚；
- **全过程可审计** —— 每一次决策的原始思考链（CoT）、席位交锋记录、选所证据链、当时的 `policy_version` 与 `policy_hash` 都落盘可查。

---

## 🎯 四个设计原则

1. **认知决策归模型，物理风控归底座。**
   大模型与投委会只有交易意图的**提案权**。每一笔订单在触达交易所网关之前，必须 100% 穿透底层 Python 物理硬门禁（订单几何合法性、真实盈亏比下限、4H 顺势否决、同向持仓配额等）。任何一道拦截插件报错或超时 → **开仓动作无条件熔断**。这条设计负责消灭"模型幻觉直接变成亏损"。

2. **全市场体制自适应（Market Regime Auto-Detection）。**
   告别"震荡市跑单边被磨损、单边市跑网格被套牢"。系统按微积分速度 `v`、加速度 `a`、定积分能量 `E` 与波动率分布研判大盘体制，并推荐匹配的策略预设。

3. **三所平权对等撮合（Three-Venue Parity）。**
   OKX、Binance、Gate 三大交易所在风控与执行上地位对等，统一多所资产视图，自适应识别各所的双向持仓模式与原生条件保护单。

4. **全链路白盒可解释 + 台账自进化。**
   完整记录模型原始推演链、席位交锋与选所证据。每 6 小时穿透真实平仓台账做闭环归因，提炼交易心法注入长期记忆（带防偏见护栏与时效半衰期）。

---

## 📸 界面

系统由**前台双翼操盘工作台**与**后台机构级控制面**构成，支持桌面 1080P/2K/4K 大屏盯盘与移动端响应式。

### 1. 🖥️ 前台双翼量化操盘工作台

整合资产总览舱、因果微积分动力学矩阵与 KLineChart v10 原生 K 线引擎：

![前台操盘大屏](docs/images/v800_live_dashboard.png)

### 2. 🌊 深度思考链（CoT）与决策轨迹抽屉

对每一笔决策提供可展开的 **「🧠 深度思考链与数理依据 (CoT)」**，完整折叠展现形态结构、微积分特征、概率期望与模型原始推演草稿：

![前台决策轨迹与深度思考链](docs/images/v800_trajectory_cot.png)

### 3. 🧠 AI 决策雷达与推演博弈全景

![AI 决策雷达与推演博弈大盘](docs/images/v800_radar_view.png)

### 4. ⚙️ 后台机构级量化控制面

全景监控运行健康度、三所连接状态、大模型推理时延、内存消耗与底层硬风控拦截事件：

![后台机构级控制面](docs/images/v800_admin_overview.png)

---

## 🧩 策略配置中心

> **"策略制定权永远属于交易员，而不是写死的系统硬代码。"**

传统量化软件把交易逻辑深埋在底层脚本里，调参困难且无法复盘。AstraQuant 把**提示词、投委会、物理风控、认知自省、版本快照、风控阈值、模型路由与多所执行**解耦为九个可视化配置模块：

| # | 模块 | 管什么 |
|---|---|---|
| 1 | 🎨 **提示词策略工作室** | 可视化编排「System 核心军规」与「User 市场特征组装模版」，9 类实时语义变量插槽一键注入（`{{market_regime}}` / `{{market_matrix}}` / `{{risk_budget}}` / `{{news_intelligence}}` / `{{trading_memory}}` / `{{account_positions}}` 等），支持方案副本与 JSON 导入导出，内置防投毒护栏 |
| 2 | 👥 **对冲基金多模型决策委员会** | 席位 100% 自由增删；每个席位可**独立绑定不同厂商的大模型**（如进攻官配 Claude、风控官配 DeepSeek-R1、数理官配 GPT-4o）与独立系统提示词、推演温度；双轮交叉质询 + CIO 统筹终审 |
| 3 | 🛡️ **Fail-Closed 物理硬拦截管线** | 不可跳过的 Python 插件流水线。出厂四道核心门禁：4H 宏观顺势铁律 / 高置信度质量门禁 / 1H ADX 趋势杂波过滤 / 真实盈亏比门禁。支持在线沙箱单步测试每道插件的通过与否决表现 |
| 4 | 🧬 **启发式自进化认知中枢** | 每 6 小时穿透全量平仓台账做闭环归因，提炼黄金心法沉淀进长期记忆；带离群噪点剔除、防偏见红线与 7~14 天敏锐半衰期，支持一键回滚官方基准 |
| 5 | 📦 **策略大一统版本快照** | 打包「提示词 + 拦截插件 + 投委会席位 + 标的池参数」计算 SHA-256 策略指纹（如 `v8.3.1@a7f29b1c`）；0.5 秒原子回滚；每笔订单强制记录当时的 `policy_version` / `policy_hash` |
| 6 | 🎛️ **执行层风控管理中心** | 26 项执行硬风控全量可调 + 三套预设一键应用（🛡️ 稳健防守 / ⚖️ 均衡波段 / 🚀 进取猎手）；高危操作强制短语二次确认 |
| 7 | 🤖 **大模型网关与推理配置中心** | 多供应商直连矩阵（OpenAI / Claude / Gemini / DeepSeek / Qwen 等）；全局思考预算 10~1800s，适配长思考链旗舰模型；主模型频控或宕机时秒级自动降级切换 |
| 8 | 🌐 **三所平权执行拓扑** | 三所原生直连、独立凭证与持仓模式；跨所因果微积分动力学矩阵实时比对价差、资金费率与动量加速度 |
| 9 | 🧪 **回测与沙箱子系统** | 多资产组合回测、统计显著性验证与单步沙箱演练，与实盘共用同一套风控与执行代码路径 |

![提示词策略工作室](docs/images/v800_prompt_studio.png)
![对冲基金多模型决策委员会](docs/images/v800_council_board.png)
![物理硬拦截插件管线](docs/images/v800_interceptors_failclosed.png)
![策略大一统版本快照](docs/images/v800_policy_snapshot.png)
![执行层风控管理中心](docs/images/v800_risk_control.png)
![大模型网关与全局推理配置](docs/images/v800_llm_hub.png)
![自进化认知中枢](docs/images/v800_self_evolution.png)
![跨所因果微积分动力学矩阵](docs/images/v800_calculus_factors.png)

---

## 📊 多级日志与全链路可观测性

| 日志标识 | 物理路径 | 写入主体 | 记录范围 |
| :--- | :--- | :--- | :--- |
| `trader` | `logs/ai_factor_trader.log` | 量化交易主脑巡检进程 | 标的池行情获取、多席位辩论推演、置信度过滤、选所路由、保护单挂载与止损棘轮收紧 |
| `backend` | `logs/uvicorn.log` | FastAPI / Uvicorn 异步服务 | HTTP 请求响应流水、中间件拦截、CORS、异常堆栈与只读数据面报错 |
| `scheduler` | `logs/r20_gateway.log` | 调度守护进程 | 调度器单例锁抢占、定时任务触发、心跳租约与碎片清理 |
| `audit` | `logs/r20_admin_audit.jsonl` | 安全审计子系统（Append-Only） | 操作时间戳、IP、动作类型与结果（登录鉴权、改密、凭证编辑、风控调参、紧急全平仓） |

Prometheus + Grafana 观测栈见 [`deploy/observability/README.md`](deploy/observability/README.md)：把 `/api/v1/admin/metrics` 变成面板与告警，端口默认只绑 `127.0.0.1`。

---

## 💰 资金规模与分级风控

系统采用**基于账户净值的比例推导机制**，不预设写死的绝对金额门槛 —— 小额资金（如 20U 模拟盘）与机构级资金（如 4000U+）都能自适应安全运行：

| 风控指标 | 计算规则（基线默认值） | 20U 资金池 | 4000U 资金池 |
| :--- | :--- | ---: | ---: |
| **单笔 1R 风险限额** | `min(池内单标上限, 可用余额 × 2%)` | 0.40 U | 15.0 U |
| **单笔保证金硬顶** | `可用余额 × 20%` | 4.0 U | 800.0 U |
| **单标的累计保证金** | `min(600, 可用余额 × 30%)` | 6.0 U | 600.0 U |
| **单日最大亏损熔断** | `min(150, 可用余额 × 5%)` | 1.0 U | 150.0 U |

---

## 🧪 测试与门禁

本仓把"能跑绿"当成交付的一部分，而不是事后补的仪式。仓库里的每个数字都由测试盯着：

```bash
# 1) 后端：审计 / 大模型 / UI / 多所契约子集
.venv/bin/pytest tests/audit tests/llm tests/ui tests/venues -q

# 2) 后端：全量离线套件
.venv/bin/pytest tests/ -q

# 3) 前端：类型检查 + 构建 + 测试
cd frontend
npx vue-tsc --noEmit -p tsconfig.app.json
npm run build
node --test tests/*.test.mjs
```

> ⚠️ **Python 一律用虚拟环境**：仓内不假设有全局解释器，命令统一走 `.venv/bin/python` / `.venv/bin/pytest`。

---

<a id="architecture-index"></a>
<a id="architecture-index"></a>
## 🗂️ 代码结构入口（给开发者 / 接手的 Agent）

> ⚠️ **重要提示**：本仓历史上**从未存在过 `OPENCODE.md`**（部分老文档里的这条指引指向一个不存在的文件，请勿查找此路径）。真实的工程结构入口如下：

| 想了解 | 看这里 | 说明 |
|---|---|---|
| **后端分层体系** | `r20_backend/README.md` | 后端分层（L0 门面 / L1 装配 / L2 路由 / L3 领域 / L4 子包）、新模块抽取约定 |
| **运行时守护脚本** | `scripts/README.md` | 哪个是入口/守护、根层模块用途、调度周期与双拼写 import 规范 |
| **前端组件与状态** | `frontend/src/components/admin/README.md` | 前端组件与 Composable 划分、Vue 3 后台与操盘看板状态机 |
| **独立部署环境** | `STANDALONE.md` | 本地独立部署、环境变量配置与服务拉起 |
| **应急故障恢复** | `RECOVERY_GUIDE.md` | 应急止损、冷备份数据恢复与进程重置预案 |
| **提示词工程攻略** | `docs/PROMPT_GUIDE.md` | 全量实时语义数据字典、波段呼吸编写军规与投委会实战指南 |
| **观测栈接入** | `deploy/observability/README.md` | Prometheus 抓取与 Grafana 面板导入 |

**架构门禁保障机制**（这三道闸会盯着文档本身）：

1. **子包模块全登记** —— `tests/audit/test_directory_docs_current.py` 强制受管子包的新增模块写入各自 `__init__.py` 清单；
2. **根层模块全登记** —— `r20_backend/*.py` 与 `scripts/*.py` 根层模块必须登记在对应 `README.md` 的表格里；
3. **文档数字防腐烂** —— `tests/core/test_readme_baseline_numbers.py` 要求基线测试数字与仓内真实用例数保持同量级对齐，漂到两倍就会被抓住。

> 📌 **提交纪律**：门禁必须跑在**将要提交的那棵树**上（`git status --short` + 对每个新文件 `git ls-files --error-unmatch`）。

---

## 🚀 部署

### 方式 A：🐳 Docker 一键部署（最推荐，零环境依赖）

自动包含 Python 3.11、编译前端静态资源，并编排 Web 引擎与网关 Worker 两个服务：

```bash
git clone https://github.com/555cute/astra-quant-agent.git
cd astra-quant-agent
cp env.example .env && vim .env        # 填大模型 Key 与交易所 Key
./deploy/docker-start.sh               # 等价于 docker compose up -d --build

docker compose ps                      # 查看状态
docker compose logs -f                 # 跟随日志
```

两个服务都声明了 `restart: unless-stopped`，并在**容器内**自带监督与心跳：容器重启、宿主重启或进程被 OOM 掉之后会自行拉起，不需要额外的 `autoheal`。

### 方式 B：传统本地 / 物理机部署

```bash
git clone https://github.com/555cute/astra-quant-agent.git
cd astra-quant-agent
sh deploy/install.sh                   # 创建 .venv 并装依赖
vim .env

source .venv/bin/activate
cd frontend && npm install && npm run build && cd ..

python -m uvicorn r20_backend.app:app --host 0.0.0.0 --port 8080
# 或一键拉起：./start.sh
```

Windows / PowerShell 用户可用 `start.ps1`。systemd 单元模板在 `deploy/`（见 `deploy/r20-quantum.service` 等三份）。

---

## ⚠️ 免责声明 (Disclaimer)

1. 本项目属于**开源量化交易软件与量化算法研究框架**，仅供技术研究、学习交流与模拟盘环境测试；
2. 加密货币市场具有极高风险与不确定性，任何历史回测表现均无法预示未来收益；
3. 使用者应具备量化交易专业常识；在充分理解策略逻辑之前，请务必先在 **DEMO 模拟盘**环境完整验证；
4. 开发者与开源社区不对任何使用本软件所造成的直接或间接投资损失承担法律责任。

---

## 🤝 社区认可与致谢

本项目深度链接并官方认可 **[LINUX DO (linux.do)](https://linux.do/)** 开源技术社区：

- 🐧 **社区支持** —— 特别鸣谢 LINUX DO 社区提供的开放技术土壤与策略灵感，感谢全体热心 L 友的持续反馈与实盘建议；
- 💬 **研讨交流** —— 欢迎在 [LINUX DO 社区](https://linux.do/) 交流多模型委员会调优、提示词编写与实盘风控体验；
- 开放透明、共同演进，致敬所有秉持开源与极客精神的探索者。

---

## 🏷️ 品牌与内部代号（改名时必读）

**对外品牌：AstraQuant**（官网 <https://www.astraquant.tech>，中文/英文文档见 [README.md](README.md) / [README.en.md](README.en.md)）。**内部代号：R20。**

2026-09 做过一次品牌改名，**只改了对外可见的那一层**，内部标识**有意保留**：

| 层 | 内容 | 状态 |
|---|---|---|
| **对外** | 仓库名 · description · topics · README · 界面品牌串 · 通知标题 · 容器镜像名 · robots/sitemap/canonical | **AstraQuant** |
| **内部** | Python 包名 `r20_backend` / `r20_gateway` · **`R20_*` 环境变量键** · 含 r20 的文件名 · DB 文件名 | **保留 R20** |
| **内部（有线契约）** | 会话头 `X-R20-Session` · 高危操作确认短语 `UPDATE R20` / `BACKUP R20` / `RESTORE R20` · 备份归档魔数 `R20GCM2` · Grafana 面板 UID `r20-quantum-trader` · systemd 单元里的 `/opt/r20-quantum-trader` | **保留 R20** |

**为什么内部不一起改**：`R20_*` 是**用户已经写进 `.env` 的配置契约** —— 改前缀会让所有已部署实例**静默失去配置**（回到"未就绪"），而对外一分流量都换不来；包名牵连 2000+ 处 import；确认短语与会话头是前后端**逐字校验**的线上契约，单边改会让高危操作用不了。所以后来者若要"把改名做彻底"，请先读本节：**那不是遗留未完成，是有意为之**。

给用户看的文案里出现 `R20_*` 变量名是正确的（那是**接口名**，不是品牌名）。

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 协议发布，自由开源。

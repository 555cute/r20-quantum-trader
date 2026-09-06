# R20 Quantum Trader (R20 智能对冲对冲基金投委会量化系统)

<div align="center">

[![Version](https://img.shields.io/badge/version-v7.4.2-blue.svg?style=flat-square)](https://github.com/555cute/r20-quantum-trader/releases/tag/v7.4.2)
[![License](https://img.shields.io/badge/license-MIT-green.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg?style=flat-square)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg?style=flat-square)](https://fastapi.tiangolo.com/)
[![Vue 3](https://img.shields.io/badge/Vue-3.x-4FC08D.svg?style=flat-square)](https://vuejs.org/)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind-3.x-38B2AC.svg?style=flat-square)](https://tailwindcss.com/)
[![Tests](https://img.shields.io/badge/tests-116%20passed-brightgreen.svg?style=flat-square)](tests/)

**全新演进的 LLM 原生数字资产对冲量化交易系统**  
*策略大一统版本快照 · 具名归档与一键原子回滚 · 同等身份交易员双轮质询 · 核心风控不可绕过底座 · 交易所原生云端 OCO 风控*

[在线官网与实盘大屏](https://www.r20.cn) · [快速上手](#-快速启动指南) · [策略版本控制台](#-四大单元策略版本快照工作台) · [投委会架构](#-投委会决策架构) · [核心特性](#-系统核心架构与特性) · [版本日志](CHANGELOG.md)

</div>

---

## 🏛️ v7.4.2 重磅升级总览 (Release Highlights)

在 **v7.4.2** 中，系统全面落地了公共行情零进程化重构、自进化防污染认知闭环与全链路服务加固：

1. **高性能零进程公共行情微服务 (Zero-Process Market Feed)**：
   - 彻底切断高频反复唤起 Node CLI / OKX CLI 派生进程的 CPU 资源风暴；
   - 采用持久化 HTTP Keep-Alive 连接池，单次标的行情耗时由 550ms 压降至 **66ms**；
   - 发现并直连官方 MCP 聚合指标端点（`POST /api/v5/aigc/mcp/indicators`），单次 JSON 批处理同时返回 ADX/KDJ/BBWIDTH/CMF，技术指标获取速度提升 **29.7 倍**（2.2s -> **74ms**）；
   - 全市场 Ticker 批量秒拉，全量因子计算时间由 20s 骤降至 **2.7s**，彻底根除低配机器 CPU 打满导致的 LLM 调用 502 超时隐患。

2. **自进化认知防污染护栏与白盒心法库闭环**：
   - 修复自进化引擎中的 `ROOT` 路径未定义及标的资金乘数持久化缺陷；
   - 全面初始化并激活白盒结构化基准心法库（`structured_trading_memory.json`），解决后台心法列表空载与版本锁死；
   - 落地网关任务远程触发中枢（`POST /api/v1/admin/gateway/jobs/{job_id}/run`），支持在管理后台一键触发自进化复盘与全量网关任务。

3. **Web 端全链路与网络协议加固**：
   - 全面支持 `HEAD /` 及各 SPA 路由探测，彻底根除网络监控及探针的 405 Method Not Allowed；
   - 规范各子脚本模块顶层 `sys.path` 隔离加载，消除外部包导入断层。

---

## 🏛️ v7.4.1 历史回顾

在 **v7.4.1** 中，系统修复了自进化生命周期闭环与后台策略版本管理交互：
- 调度器升级为 `["02:00", "08:00", "14:00", "20:00"]`；
- 落实 TTL 半衰期自然淘汰机制与 Top 8 容量上限保护；
- 策略版本控制工作台补齐物理删除与原子回滚。

---

## 📸 实景截图矩阵

### 1. 策略大一统版本快照控制台 (Policy Snapshot Workbench)
![策略版本快照控制台](docs/images/admin_policy_snapshot.png)

### 2. 对冲基金投委会决策中枢 (Trading Desk Council)
![对冲基金投委会中枢](docs/images/admin_council.png)

### 3. 真实量化实盘监控终端全景
![实盘监控终端全景](docs/images/dashboard_trading.png)

---

## ⚡ 系统核心架构与特性

- **大模型核心决策 (LLM-Native 70% 权重)**：告别僵化死板的传统指标策略，由 DeepSeek / Claude / GPT / Gemini 等旗舰大模型担任全权量化决策大脑。
- **微积分行情动力学**：实时解构 15M/1H 价格时间序列的一阶导数（速度 $v$）、二阶导数（加速度 $a$）及定积分动能（做功 $E$），量化趋势爆发力。
- **Top 100 聪明钱雷达**：全天候扫描全网持仓前 100 名主力账户的真实多空持仓、平均建仓成本与资金净流向。
- **确定性硬门禁 (Deterministic Interceptors)**：置信度 $\ge 75\%$、盈亏比 $R:R \ge 2.0$、2.0x ATR 防插针宽止损、保本移损与反向持仓防对冲。
- **交易所原生 OCO 委托**：所有策略发单强制绑定 OKX 云端条件单，即使后端离线断网，交易所撮合引擎仍严格执行防穿仓兜底。
- **双模响应式界面**：Vue 3 + Tailwind CSS 极简响应式架构，完美适配手机移动端与宽屏桌面，支持深浅双模极致对比度。

---

## 🚀 快速启动指南

### 1. 环境克隆与依赖安装
```bash
git clone https://github.com/555cute/r20-quantum-trader.git
cd r20-quantum-trader

# 创建并激活虚拟环境
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# .\venv\Scripts\activate  # Windows

# 安装 Python 后端核心依赖
pip install -r requirements.txt
```

### 2. 配置环境变量
```bash
cp env.example .env
# 编辑 .env 配置你的 OKX API 凭证与默认大模型 API Key
```

### 3. 构建前端静态资源
```bash
cd frontend
npm install
npm run build
cd ..
```

### 4. 启动量化服务与控制面
```bash
# 启动常驻量化核心与控制后台 (监听 0.0.0.0:8080)
python3 -m uvicorn r20_backend.app:app --host 0.0.0.0 --port 8080 --reload
```
打开浏览器访问：
- **实盘监控终端**：`http://localhost:8080/`
- **管理控制面**：`http://localhost:8080/admin/`
- **API 交互文档**：`http://localhost:8080/api/docs`

---

## 🧪 自动化测试验证

系统包含覆盖策略引擎、投委会机制、拦截器插件与安全鉴权的自动化单元测试：
```bash
python3 -m unittest discover -s tests/
# 输出: Ran 116 tests ... OK (100% 通过)
```

---

## 📄 开源协议与免责声明

- 本项目基于 **[MIT License](LICENSE)** 开源。
- **免责声明**：本项目仅供量化交易研究与学术交流使用。加密货币属于高风险高波动资产，策略历史表现不代表未来收益，请务必根据自身风险承受能力理性参与实盘。

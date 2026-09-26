# AstraQuant 纯净工程跨机迁移与 DeepSeek Harness 开发接手指南

> 适用场景：将项目迁移至新电脑（macOS / Linux / Windows WSL），并在新电脑的 **DeepSeek Harness** 环境中无缝继续接手开发与部署运行。

---

## 目录
- [一、项目本体资产与纯净度声明](#一项目本体资产与纯净度声明)
- [二、新电脑获取工程本体的双通道](#二新电脑获取工程本体的双通道)
- [三、新电脑运行环境要求与初始化](#三新电脑运行环境要求与初始化)
- [四、新电脑 DeepSeek Harness 接入与无缝接手](#四新电脑-deepseek-harness-接入与无缝接手)
- [五、自检门禁与服务拉起验证](#五自检门禁与服务拉起验证)
- [六、常见疑难排查与避坑指南 (FAQ)](#六常见疑难排查与避坑指南-faq)

---

## 一、项目本体资产与纯净度声明

本归档专为**纯代码本体跨机开发**设计，已执行深度洁净处理：

### ✅ 完整保留的核心资产
1. **全量代码与版本控制**：
   - 完整 `.git/` 仓库（保留所有本地分支、Commit 历史与远程 origin 跟踪）；
   - 后端全部模块 `r20_backend/`（L0-L4 架构、风控引擎与接口路由）；
   - 前端全部源码 `frontend/`（Vue 3 + TypeScript 纯净工程）；
   - 量化与算子脚本 `scripts/`（全量 33 个策略、复盘、执行与分析脚本）；
   - 自动化测试用例 `tests/`（涵盖审计、量化、UI 与风控门禁）；
   - 部署编排配置 `deploy/`、`Dockerfile`、`docker-compose.yml`；
   - 系统设计与架构文档 `docs/`。
2. **免编译前端静态产物**：
   - `frontend/dist/`：打包前已通过 411 项测试与 `vue-tsc` 严格校验，新电脑即使暂未安装 Node.js/npm，后端也能直接静态挂载并托管 UI 控制台。
3. **初始配置与接手规范**：
   - `AGENTS.md`：新电脑 Harness Agent 的架构规范、测试门禁与执行军规；
   - `env.example`：标准环境变量配置模板。

### ❌ 彻底剔除的运行数据与敏感项
- 数据库与业务状态：已排除全部 SQLite 数据库（`*.db` / `*.sqlite3`）、交易账本、决策历史、监控快照等；
- 个人私密凭证：已排除 `.env` 及 Fernet 加密密钥文件（`r20_secrets.enc` / `.r20_secret_key`）；
- 本地历史日志与备份：已排除 `logs/*.log` 与 `backups/*`；
- 平台相关二进制环境：已排除 Python `.venv/` 与 `frontend/node_modules/`（避免跨机器架构导致的动态链接库不兼容问题）；
- 运行缓存与文件锁：已排除全部 `*.lock`、`*.pid`、`__pycache__`、`.pytest_cache`。

---

## 二、新电脑获取工程本体的双通道

您可以根据新电脑的网络与偏好，选择最适合的方式将项目置入新电脑：

### 通道 A：使用下载的离线归档包（推荐·免编译）
包含完整的 `.git`、源码及已预构建的前端产物：
```bash
# 1. 解压归档包至目标目录（如 /data/dsh/home 或本地工作区）
tar -xzvf r20_clean_project.tar.gz

# 2. 进入项目根目录
cd r20

# 3. 赋予核心脚本执行权限
chmod +x start.sh deploy/install.sh
```

### 通道 B：直接通过 GitHub 在线 Clone（极速·纯源码）
如果新电脑可以直接访问 GitHub：
```bash
# 1. 克隆完整仓库
git clone https://github.com/555cute/astraquant.git astraquant
cd r20
chmod +x start.sh deploy/install.sh

# 2. 编译前端静态资源（需 Node.js 18+）
cd frontend
npm install
npm run build
cd ..
```

---

## 三、新电脑运行环境要求与初始化

### 1. 系统与基础依赖建议
- **操作系统**：Linux (Ubuntu 22.04+ / Debian 12+)、macOS (Apple Silicon / Intel)、Windows WSL2
- **Python 环境**：Python 3.11 或 3.12（推荐使用虚拟环境）
  - *macOS 用户提示*：若初次安装 C 扩展依赖报错，请先在终端运行 `xcode-select --install`
- **Node.js**（仅在需要二次开发前端时需要）：Node.js 18.x 或 20.x，npm 9+

### 2. 初始化环境配置 (.env)
由于项目本体不含任何私密凭据，首次启动前请基于模板创建个人配置文件：
```bash
cp env.example .env
chmod 600 .env
```
用编辑器打开 `.env`：
- 若仅用于本地离线开发或回测：保持 `R20_OKX_ENV=demo`，暂不填入真实 Key 即可；
- 若需要调用大语言模型（LLM）：配置 `LLM_API_KEY` 与对应的 `LLM_BASE_URL`；
- 若接入实盘/模拟盘：通过管理后台 `/admin` 进行 Fernet 加密录入，或直接在 `.env` 中按需配置。

### 3. 一键安装依赖并初始化数据结构
```bash
# 使用官方标准化安装脚本
sh deploy/install.sh

# 激活虚拟环境
source .venv/bin/activate
```
*该脚本会自动创建专属 `.venv` 虚拟环境、安装 `requirements.txt` 全部依赖，并初始化基础交易池数据。*

---

## 四、新电脑 DeepSeek Harness 接入与无缝接手

在新电脑上启动 DeepSeek Harness 后，让 AI Agent 无缝接手开发的流程如下：

### 1. 工作区挂载 (Workspace)
- 将 DeepSeek Harness 的工作区路径直接指向新电脑上的 `r20` 目录（例如容器化部署下默认为 `/data/dsh/home/r20`，或本地路径）；
- 在 Web 控制台确认当前工作目录为 `r20` 根目录。

### 2. 模型服务配置 (LLM Providers)
如果新电脑上的 DeepSeek Harness 为全新安装，请确保在 Harness 中配置好模型 Provider：
- 在 Harness 的 `settings.yaml` 或 UI 模型配置中填入您的模型提供商（如 CPA、DeepSeek、OpenAI 等）及 API 密钥，确保 Agent 具备代码生成与分析能力。

### 3. 唤醒 Agent 接手指令
在新电脑与 Harness Agent 开启首次对话时，可直接发送以下接手提示词：
> *"我已经将 AstraQuant 项目迁移到当前工作区，请阅读根目录下的 AGENTS.md，了解当前工程分层架构、质量门禁与执行红线，然后检查代码状态并准备继续开发。"*

内置的 `AGENTS.md` 包含全套系统规则，新 Agent 会自动对齐架构，不产生任何上下文偏离。

---

## 五、自检门禁与服务拉起验证

### 1. 自动化质量门禁测试
每次启动或进行代码修改后，务必执行门禁验证确保环境正常：
```bash
# 激活环境
source .venv/bin/activate

# 运行后端审计与风控基础测试
pytest tests/audit/test_admin_token_duplication_contract.py tests/llm/test_llm_resilience.py tests/ui/test_dashboard_cache.py

# 前端类型检查与组件测试（如有 node 环境）
cd frontend
npm run build
npx vue-tsc --noEmit
node --test tests/*.test.mjs
cd ..
```

### 2. 启动服务
- **原生极速拉起**：
  ```bash
  ./start.sh
  ```
- **Docker Compose 容器化拉起**：
  ```bash
  docker compose up -d --build
  ```

启动成功后，浏览器打开 `http://127.0.0.1:8080` 即可访问完整 Web 操盘台与管理面板。

---

## 六、常见疑难排查与避坑指南 (FAQ)

### Q1: 运行脚本报 `ModuleNotFoundError: No module named 'r20_backend'`？
- **原因**：未将项目根目录加入 Python 搜索路径，或未激活 `.venv`。
- **解决办法**：
  ```bash
  source .venv/bin/activate
  export PYTHONPATH=".:scripts:$PYTHONPATH"
  ```

### Q2: 浏览器打开 8080 端口显示 404 或白屏？
- **原因**：未检测到 `frontend/dist` 静态资源目录。
- **解决办法**：若使用的是通道 B (git clone)，请在 `frontend` 目录下执行 `npm install && npm run build` 生成构建产物；若使用的是通道 A，请检查解压路径是否完整包含 `frontend/dist`。

### Q3: 后端启动提示 `NOT READY` 状态？
- **原因**：这是正常且符合架构安全红线的现象。系统在未配置完整 OKX API Key 三元组（Key / Secret / Passphrase）时会实行“失败关闭”保护，拒绝一切交易写入；公共行情 REST 接口不受影响。

### Q4: 跨机 Git 提交报错或分支脱节？
- **解决办法**：项目本体完整保留了 `.git/`，解压后通过 `git status` 确认处于 `main` 分支，执行 `git fetch origin` 即可随时拉取最新远端改动。

---
*文档版本：v8.3.0 · 适用框架：DeepSeek Harness & Cordis Engine*

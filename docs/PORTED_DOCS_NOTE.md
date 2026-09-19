# Ported reference docs: QuantX PRO (Rust + Next.js) — chart rewrite & visual evidence

> **语境说明（先读这段）**：本目录下这几份文件来自**另一个仓库** `BusHelr/quantx-pro`
> （Rust 交易内核 + Next.js 16 前端）。它们**不是**本 Python 栈（`r20_backend` / `r20_gateway` /
> `frontend`）的变更记录，也不是本仓库的运行依赖；仅作**对照参考**（图表实现与验收方式）。
> 文中出现的路径（`apps/web/...`、`services/quantx-core/...`、`crates/...`）都指向那个仓库。

## 本次带入的文件

| 文件 | 内容 |
|---|---|
| `docs/AGENT_TEAM_FIXES_2026-09-19.md` | 修复记录：交易所数据同步、本金硬闸、标的池、通知、自检、新闻多源、K 线从 0 重写、智能决策、视觉证据（§22） |
| `docs/assets/chart-*.png`（7 张） | K 线图表真实渲染截图（由运行实例自动采集；尺寸与实测读数见 §22） |
| `scripts/chart_screenshots.mjs` | 截图采集脚本（图表库 `takeScreenshot()` 直出 canvas + 面板截图，含 PNG 头校验） |
| `scripts/chart-verify.mjs` | 图表验收 22 项（交互 + 库层状态：`series.options()`、`priceScale().options()`、图元 `markers()`、尺寸守恒） |
| `scripts/chart-features.mjs` | 功能矩阵 15 项（7 档周期 / 快捷键 / 平移与回实时 / 场所切换 / 轨迹与标记 / 绘图面板 / 指标参数 / 中英切换） |
| `scripts/chart-occlusion.mjs` | 遮挡专项：5 视口 × 逐根蜡烛判定"浮层是否压住 K 线"（空过按失败计） |

## 可复用的经验（与本栈的技术栈无关的部分）

1. **浮层与画布的坐标口径必须唯一**：K 线用图表库原生图元（marks/primitive）绘制，
   不要自绘第二层 canvas；两套坐标系必然产生偏移。
2. **`scaleMargins` 是相对 pane 高度**，不是容器高度——按容器高度算留白会让浮层压住 K 线
   （实测：容器 412px 而主图 pane 191px，留白只做到一半，26 根蜡烛被压）。
3. **浮层出现/换行要用 MutationObserver 捕捉**（数据到位后才渲染的浮层，靠数据依赖会漏触发）。
4. **验收断言要"逐根/逐项"**：只比整图最高点会漏掉"浮层横向范围内某几根蜡烛顶到浮层下沿"。
5. **验收脚本的空过是最大的风险**：未就绪时"检查 0 项 → 0 违规 → PASS"，必须把
   "取不到被检对象"判为失败，并打印状态链。
6. **CSP 不含 `'unsafe-eval'` 时不要用 `page.waitForFunction`**（Playwright 会在页内 eval 谓词，
   被 CSP 拒且不报错），改用 `page.evaluate(函数)` + 手动轮询。

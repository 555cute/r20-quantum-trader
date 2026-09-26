# `components/admin/` 该怎么读

这个目录原先叫"共享管理组件"，但研究文档 F7 指出它**名不副实**：5 个组件里 4 个只有
一个消费者。于是接手的人会误以为它们都被多处依赖，既不敢改、也不敢删。

结构优化阶段 3 处理后的现状（引用者数量为**实测**，2026-09-14）：

## 顶层 = 真正被多页共享

| 组件 | 引用者数 | 说明 |
| --- | --- | --- |
| `DataTable.vue` | **1** | F1 之前只有 `AuditPage` 1 个消费者（"假共享"）。已升级为真原语。**批 7–8 计数下降**：`PluginsPage` / `AgentsPage` / `AboutPage` 的登记表先后改为行式清单，故 8 → 1（`SecurityPage`/`AuditPage`/`AdminSysPage`/`BackupPage` 的登记表先后改为行式清单，现仅剩 `GatewayPage`） |
| `PageHeader.vue` | **18** | 一直是共享的 |

## `page-parts/` = 单页专用

| 组件 | 唯一消费者 |
| --- | --- |
| `SettingsSection.vue` | `views/admin/SecurityPage.vue` |
| `VenueCredentialCard.vue` | `views/admin/SecurityPage.vue` |
| `DangerZone.vue` | `views/admin/RiskPage.vue` |

放进子目录是**刻意的**：让"单页专用"这件事写在路径上，而不是靠读者去数引用。
F7 的另一种解法（推广）对这三个组件目前**不成立**，理由见下。

## `SettingsSection` 为什么没有"推广"

实测有 **9 个管理页**在手工重写"带标题的卡片"：`AboutPage` `AdminSysPage` `AgentsPage`
`BackupPage` `DecisionsPage` `EvolutionPage` `GatewayPage` `NotifyPage` `PluginsPage`。

看起来很该推广，但逐页比对后**它们不是同一个形状**：

- `SettingsSection`：`<section>` + `<header px-4 py-3>`（标题 + description + actions 槽）+ `<div p-4>` 两段式
- 手写版：整卡 `p-4 sm:p-5` + 内联 header（`flex items-center justify-between pb-3 mb-3 border-b`），
  且**标题里带图标、header 里塞内联徽标与页面专属副文本**（例：`AboutPage` 的
  `INFO/OPEN SOURCE`、`AdminSysPage` 的 `KeyRound` + 当前账号名）

要吃掉这些变体，得给组件加图标槽、徽标槽、header 变体 —— 那是**设计改动**，
会让 9 个上线页面的外观发生变化，必须目视评审。故本轮**不做**，保持手写原样。

## ⚠️ 一处已更正的错误结论（结构优化阶段 4·B3 第五十五刀）

本节原先写着下面这句，**它是错的**：

> ~~以下 10 个组件在 `src/` 里**实测 0 个引用者**（已排除路径别名导致的误判）：~~
> ~~`base/` BaseSwitch · BaseSparkline · BaseDrawer · BaseDialog · BaseTabs · CopyButton~~
> ~~`dashboard/` VenueAccountCard · DataStatus · SettingsPopover · FactorDrawer~~

**实测（第五十五刀重核，逐个查 import 与模板标签）—— 这 10 个全部有消费者**：

| 组件 | 消费者数 | 消费者 |
| --- | --- | --- |
| `base/BaseDrawer` | 4 | `dashboard/` 的 FactorDrawer · LedgerDrawer · PeekDrawer · RadarDrawer |
| `base/CopyButton` | 7 | `base/BaseCodeBlock` · `dashboard/AboutModal` · `dashboard/FirstRunGuide` · `dashboard/PeekDrawer` · `views/admin/AboutPage` · `views/admin/DecisionsPage` · `views/admin/PromptStudioPage` |
| `base/BaseDialog` | 15 | `base/ConfirmHost` · `dashboard/AboutModal` · `views/admin/GatewayPage` · `views/admin/CouncilPage` · `views/admin/PromptStudioPage` · `views/admin/PolicySnapshotPage` · `views/admin/EvolutionPage` · `views/admin/InterceptorsPage` · `views/admin/NotifyPage` · `views/admin/AboutPage` · `views/admin/llm/ModelEditDialog` · `views/admin/llm/RemoteFetchDialog` · `views/admin/SecurityPage` · `views/admin/BackupPage` |
| `base/BaseSwitch` | 11 | `dashboard/SettingsPopover` · `views/admin/CouncilPage` · `views/admin/PromptStudioPage` · `views/admin/EvolutionPage` · `views/admin/InterceptorsPage` · `views/admin/NotifyPage` · `views/admin/llm/ProviderListView` · `views/admin/llm/ProviderDetailView` · `views/admin/SecurityPage` · `views/admin/RiskPage` |
| `base/BaseSparkline` | 1 | `dashboard/KpiRibbon` |
| `base/BaseTabs` | 1 | `dashboard/RadarDrawer` |
| `dashboard/VenueAccountCard` | 1 | `dashboard/VenueAccountsPanel` |
| `dashboard/DataStatus` | 1 | `dashboard/KpiRibbon` |
| `dashboard/SettingsPopover` | 1 | `dashboard/TopBar` |
| `dashboard/FactorDrawer` | 1 | `dashboard/FactorMatrix` |

**错在哪（值得记下来）**：本条是**误读台账 F7** 的产物。
台账 §3 F7 讲的是 **`components/admin/` 自己那 5 个组件里有 4 个单用**
（`DataTable`/`SettingsSection`/`DangerZone`/`VenueCredentialCard`），
而不是 `base/` 与 `dashboard/` 里的组件。把"单用（1 个消费者）"
误当成"0 个引用者"，再顺手列成了另外两个目录的清单。

若照原结论执行，会**删掉 10 个正在使用的组件**。这也是本 README
此前没有被任何测试检查的原因 —— 文档错误可以静默存活。

> 教训：写"实测 X"之前先跑一遍取证，且**别把 1 个消费者读成 0 个**。
> 本仓同类问题已多次出现（第五十一 / 五十四 / 五十五刀均为
> "凭印象写下了本可查证的东西"）。

## 相邻目录的现状（供接手人导航）

这两个目录**没有** README，结构靠文件名自明；此处给一份实测导航：

- `components/base/` —— 原语组件。`BaseDrawer`/`CopyButton`/`BaseDialog` 是多消费者，
  其余（`BaseSwitch`/`BaseSparkline`/`BaseTabs`）目前单消费者。
- `components/dashboard/` —— 仪表盘。其中 **7 个 `.ts` 是纯逻辑模块**，
  与本目录的 `.vue` 组件分开：`chartCandles.ts`（蜡烛取数与归一）、
  `chartCountdown.ts`（周期倒计时）、`chartIndicators.ts`（指标目录）、
  `chartLiveLevels.ts`（活动持仓/挂单 → 入场价、方向、止损、止盈）、
  `chartMath.ts`（风险收益与精度）、`chartOverlays.ts`（价格线规划）、
  `chartStyles.ts`（图表主题）。除 `chartMath`/`chartStyles`
  各有 2 个消费者外，其余各 1 个（均为 `ChartWorkstation.vue`）。
  **纯逻辑外提的收益是"可被 node 直接测试"**，见
  `frontend/tests/chartCandles.test.mjs` 与 `frontend/tests/chartLiveLevels.test.mjs`。

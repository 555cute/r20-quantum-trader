<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useTheme } from '../composables/useTheme'
import {ShieldCheck, Cpu, FileText, ArrowLeft,
  ExternalLink, Copy, Terminal, Users, Brain, TrendingUp,
  Layers, Lock, ShieldAlert, ChevronRight, Menu, X, Sun, Moon, Server} from 'lucide-vue-next'

const router = useRouter()
const { theme, toggleTheme } = useTheme()
import { APP_VERSION, APP_NAME } from '../config/version'

const activeSection = ref('overview')
const mobileMenuOpen = ref(false)
const copiedTag = ref('')
const zoomImage = ref<string | null>(null)

const sections = [
  { id: 'overview', title: '1. 系统架构与量化哲学', icon: TrendingUp },
  { id: 'dashboard', title: '2. 双翼工作台与资产控制舱', icon: Terminal },
  { id: 'council', title: '3. 对冲基金投委会 (Trading Desk)', icon: Users },
  { id: 'policy_snapshot', title: '4. 策略版本快照控制台 (Policy Snapshot)', icon: Layers },
  { id: 'prompt_studio', title: '5. 提示词策略与语义变量插槽', icon: FileText },
  { id: 'interceptors', title: '6. Python 物理拦截插件 (Fail-Closed)', icon: ShieldCheck },
  { id: 'risk_control', title: '7. 执行层风控管理中心', icon: Lock },
  { id: 'llm_hub', title: '8. 模型连接与 API 协议支持', icon: Cpu },
  { id: 'self_evolution', title: '9. 自进化认知与长期记忆闭环', icon: Brain },
  { id: 'deployment', title: '10. 生产部署与多通道通知', icon: Server },
  { id: 'faq', title: '11. 常见问题解答与风控底线 (FAQ)', icon: ShieldAlert },
]

function copyText(text: string, tag: string) {
  navigator.clipboard.writeText(text)
  copiedTag.value = tag
  setTimeout(() => {
    copiedTag.value = ''
  }, 2000)
}

function scrollToSection(id: string) {
  activeSection.value = id
  mobileMenuOpen.value = false
  const el = document.getElementById(id)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

// Scroll spy
function onScroll() {
  const scrollPos = window.scrollY + 120
  for (let i = sections.length - 1; i >= 0; i--) {
    const el = document.getElementById(sections[i].id)
    if (el && el.offsetTop <= scrollPos) {
      activeSection.value = sections[i].id
      break
    }
  }
}

onMounted(() => {
  window.addEventListener('scroll', onScroll, { passive: true })
})

onUnmounted(() => {
  window.removeEventListener('scroll', onScroll)
})
</script>

<template>
  <div class="min-h-screen font-sans transition-colors selection:bg-blue-500/30" style="background-color: var(--surface-0); color: var(--ink-1);">
    <!-- Top Header Navigation (Slim & Clean) -->
    <header class="sticky top-0 z-40 backdrop-blur-md border-b px-3 sm:px-6 h-[48px] flex items-center justify-between transition-colors" style="background-color: var(--surface-header); border-color: var(--line-1);">
      <div class="flex items-center space-x-2 sm:space-x-3 min-w-0">
        <button
          @click="router.push('/')"
          class="flex items-center space-x-1 px-2 py-1 rounded-lg border text-xs transition-colors cursor-pointer shadow-xs shrink-0"
          style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-2);"
          title="返回实盘终端"
        >
          <ArrowLeft class="w-3.5 h-3.5" />
          <span class="hidden sm:inline">返回终端</span>
        </button>
        <div class="h-4 w-px hidden sm:block shrink-0" style="background-color: var(--line-1);"></div>
        <div class="flex items-center space-x-1.5 sm:space-x-2 min-w-0">
          <span class="font-semibold text-xs text-[#F7931A] px-1 py-0.2 rounded bg-amber-500/10 border border-amber-500/30 shrink-0">₿</span>
          <span class="font-semibold text-xs sm:text-sm tracking-wide shrink-0 whitespace-nowrap" style="color: var(--ink-1);">
            {{ APP_NAME }}
          </span>
          <span
            class="px-1.5 sm:px-2 py-0.2 rounded text-[11px] border font-bold shrink-0 whitespace-nowrap"
            style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);"
          >
            <span class="hidden md:inline">{{ APP_VERSION }} 官方开发与使用指南</span>
            <span class="hidden sm:inline md:hidden">{{ APP_VERSION }} 指南</span>
            <span class="sm:hidden">DOCS</span>
          </span>
        </div>
      </div>

      <div class="flex items-center space-x-1.5 sm:space-x-2 shrink-0">
        <!-- Mobile TOC Drawer Button -->
        <button
          @click="mobileMenuOpen = !mobileMenuOpen"
          class="sm:hidden flex items-center justify-center w-7.5 h-7.5 rounded-lg border transition-all cursor-pointer shadow-xs"
          style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-1);"
          title="目录索引 (TOC)"
        >
          <Menu v-if="!mobileMenuOpen" class="w-3.5 h-3.5" />
          <X v-else class="w-3.5 h-3.5" />
        </button>

        <!-- Theme Toggle -->
        <button
          @click="toggleTheme"
          class="flex items-center justify-center w-7.5 h-7.5 rounded-lg border transition-all cursor-pointer shadow-xs"
          style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-1);"
          :title="theme === 'dark' ? '切换为亮色模式' : '切换为暗色模式'"
        >
          <Sun v-if="theme === 'dark'" class="w-3.5 h-3.5 text-amber-400 hover:rotate-45 transition-transform" />
          <Moon v-else class="w-3.5 h-3.5 text-slate-700 hover:-rotate-12 transition-transform" />
        </button>

        <!-- Admin Portal (Desktop only) -->
        <button
          @click="router.push('/admin')"
          class="hidden sm:flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-xs cursor-pointer transition-colors shadow-xs"
          style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-2);"
        >
          <Lock class="w-3.5 h-3.5" />
          <span>控制台</span>
        </button>

        <!-- GitHub link -->
        <a
          href="https://github.com/555cute/r20-quantum-trader"
          target="_blank"
          class="flex items-center space-x-1 px-2 sm:px-3 py-1 rounded-lg text-xs font-bold transition-all shadow-xs"
          style="background-color: var(--ink-1); color: var(--surface-2);"
        >
          <ExternalLink class="w-3.5 h-3.5" />
          <span class="hidden sm:inline">GitHub</span>
        </a>
      </div>
    </header>

    <!-- Mobile TOC Backdrop Overlay -->
    <div
      v-if="mobileMenuOpen"
      class="fixed inset-0 bg-black/60 backdrop-blur-xs z-40 sm:hidden transition-opacity"
      @click="mobileMenuOpen = false"
    ></div>

    <!-- Main Container -->
    <div class="max-w-7xl mx-auto px-4 sm:px-6 py-6 sm:py-8 flex gap-8">
      <!-- Left Sticky Sidebar (TOC) -->
      <aside
        class="w-64 shrink-0 fixed inset-y-12 left-0 z-50 sm:z-30 sm:bg-transparent p-4 sm:p-0 border-r sm:border-r-0 transition-transform duration-200 sm:translate-x-0 sm:sticky sm:top-16 sm:h-[calc(100vh-5rem)] overflow-y-auto"
        :class="mobileMenuOpen ? 'translate-x-0 bg-[var(--surface-2)] shadow-2xl' : '-translate-x-full sm:translate-x-0'"
        style="border-color: var(--line-1);"
      >
        <div class="flex items-center justify-between mb-3 px-2">
          <div class="text-[11px] font-bold uppercase tracking-wider" style="color: var(--ink-3);">
            目录索引 (TOC)
          </div>
          <button
            @click="mobileMenuOpen = false"
            class="sm:hidden p-1 rounded-lg border text-xs cursor-pointer transition-colors"
            style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-2);"
            title="关闭目录"
          >
            <X class="w-3.5 h-3.5" />
          </button>
        </div>
        <nav class="space-y-1">
          <button
            v-for="s in sections"
            :key="s.id"
            @click="scrollToSection(s.id)"
            class="w-full text-left px-3 py-2 rounded-xl text-xs font-medium transition-all flex items-center justify-between group cursor-pointer border"
            :style="activeSection === s.id
              ? { backgroundColor: 'var(--accent-bg)', color: 'var(--accent)', borderColor: 'var(--accent-line)', fontWeight: 'bold' }
              : { backgroundColor: 'transparent', borderColor: 'transparent', color: 'var(--ink-2)' }"
          >
            <div class="flex items-center space-x-2.5 truncate">
              <component :is="s.icon" class="w-3.5 h-3.5 shrink-0" />
              <span class="truncate">{{ s.title }}</span>
            </div>
            <ChevronRight class="w-3 h-3 opacity-0 group-hover:opacity-100 transition-opacity" :class="activeSection === s.id ? 'opacity-100' : ''" />
          </button>
        </nav>

        <div
          class="mt-6 p-3.5 rounded-xl border text-xs space-y-2 shadow-xs"
          style="background-color: var(--surface-2); border-color: var(--line-1);"
        >
          <div class="text-[11px] uppercase font-bold" style="color: var(--ink-3);">社区交流与极客讨论</div>
          <div class="font-bold flex items-center justify-between" style="color: var(--ink-1);">
            <span>QQ 官方群</span>
            <span class="text-xs px-1.5 py-0.2 rounded border" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">655973677</span>
          </div>
          <p class="text-[11px] leading-relaxed" style="color: var(--ink-2);">
            欢迎量化极客、提示词工程师、深度求索 R1 用户共同交流探索。
          </p>
        </div>
      </aside>

      <!-- Right Content Area -->
      <main class="min-w-0 flex-1 space-y-14 pb-24">
        <!-- 1. 系统概览与量化哲学 -->
        <section id="overview" class="space-y-4 pt-2">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-bold border" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">CHAPTER 01</span>
            <h2 class="text-xl sm:text-2xl font-semibold tracking-wide" style="color: var(--ink-1);">系统架构与量化哲学</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--ink-2);">
            <strong>R20量子交易系统 (R20 Quantum Trading System)</strong> 是一套专为高波动加密货币（Crypto）打造的<strong>机构级全自动波段量化决策与执行系统</strong>。系统依托 OKX 交易所官方 REST/WebSocket V5 生产 API 与 @okx_ai 官方交易底座，运行在严格的北京时间（UTC+8）自然日财务基准之上，聚焦 1H~4H 大级别顺势波段，以<strong>“胜率第一、宁缺毋滥、三位一体 Fail-Closed 物理硬防线”</strong>为最高风控宗旨。在 v7.5.0 全面集成本地化 TradingView 官方轻量引擎与视觉 LLM 友好型量价形态工作站；v7.6.0 里程碑将全部执行层硬风控阈值从源码剥离，升级为后台可视化「风控管理中心」与三套优质预设套件。
          </p>

          <!-- 4 Core Pillars Grid -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3.5 pt-2">
            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="flex items-center space-x-2 text-xs font-bold" style="color: var(--up);">
                <ShieldCheck class="w-4 h-4" />
                <span>Fail-Closed 物理硬拦截</span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--ink-2);">
                绝不将风控寄托于 LLM 提示词本身。在交易执行底层设立不可覆盖的 Python 物理拦截插件管线，4H 顺势门禁、80% 置信度、1H ADX 震荡过滤及真实 2.0R 盈亏比门禁物理硬切断。
              </p>
            </div>

            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="flex items-center space-x-2 text-xs font-bold" style="color: var(--accent);">
                <Users class="w-4 h-4" />
                <span>多模型决策委员会 (Council Pro)</span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--ink-2);">
                支持并发调度宏观分析师、盘口微结构官、舆情侦察官等多参谋席位展开深度思考辩论，落地一票否决、加权共识与动能突破三种裁决机制，由首席终审仲裁官收口输出严格契约。
              </p>
            </div>

            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="flex items-center space-x-2 text-xs font-bold" style="color: var(--ink-1);">
                <Layers class="w-4 h-4" />
                <span>语义数据插槽提示词系统</span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--ink-2);">
                全网快讯、自进化心法、多标的数理矩阵等动态数据抽象为标准语义变量插槽（如 <code>&#123;&#123;news_intelligence&#125;&#125;</code>），支持模块自由解耦与策略方案一键导入导出。
              </p>
            </div>

            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="flex items-center space-x-2 text-xs font-bold" style="color: var(--warn);">
                <Brain class="w-4 h-4" />
                <span>自进化认知复盘闭环</span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--ink-2);">
                每日 20:00 自动读取真实平仓台账流水进行自我反思与痛点归因，自动更新 <code>AI_TRADING_MEMORY.md</code> 长效实战心法，具备时效覆盖与动态经验淘汰机制。
              </p>
            </div>
          </div>
        </section>

        <!-- 2. 双翼工作台与资产控制舱 -->
        <section id="dashboard" class="space-y-4 pt-6 border-t" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-bold border" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">CHAPTER 02</span>
            <h2 class="text-xl sm:text-2xl font-semibold tracking-wide" style="color: var(--ink-1);">双翼量化工作台与资产控制舱</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--ink-2);">
            前台终端采用机构级**「双翼量化工作台（Dual-Wing Workstation）」**架构，支持在宽屏下的 62% : 38% 双翼并行视角与全景纵向视角间一键切换：
          </p>

          <div class="grid grid-cols-1 md:grid-cols-2 gap-3.5 pt-1 text-xs">
            <div class="p-3.5 rounded-xl border space-y-1.5" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="font-bold text-sm" style="color: var(--ink-1);">左翼：主控与操盘中心 (62%)</div>
              <p style="color: var(--ink-2);">
                • <strong>4 单元独立 Bento 资产控制舱</strong>：移动端采用 2x2 对称紧凑网格（两行，一行两个），PC 宽屏呈现黄金比例立体空间（总权益、基准净盈亏、今日已结、持仓浮盈分离解耦）。<br>
                • <strong>今日已结资金费与手续费全透传 (v7.5.0)</strong>：实时汇总统计跨周期永续合约资金费（Funding Fee）结算明细与平仓交易手续费（Trading Fee），对账透明清晰，消除浮盈与已结盈亏认知差。<br>
                • <strong>TradingView 官方原生 K 线操盘工作站 (v7.5.0)</strong>：本地打包集成，0 外部依赖免 VPN 秒开；支持 150 根 K 线全屏铺满、MA/BOLL/VOL 多指标独立共存，为视觉 LLM 读取 K 线形态量身打造；叠加四维交易线（入场、止损、止盈）与科学真实盈亏比测算控制台。
              </p>
            </div>
            <div class="p-3.5 rounded-xl border space-y-1.5" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="font-bold text-sm" style="color: var(--ink-1);">右翼：六币因果动力学与微结构雷达 (38%)</div>
              <p style="color: var(--ink-2);">
                • <strong>微积分物理动能指标</strong>：实时计算一阶速度 $v$、二阶加速度 $a$、三阶冲击 $j$ 与 ADX 趋势动量。<br>
                • <strong>聪明钱微结构</strong>：追踪大户多空比与净流入流出，点击卡片即刻呼出深度数学推演与当轮实发 Prompt 抽屉。
              </p>
            </div>
          </div>

          <!-- Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <div class="text-[11px] px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--line-1); color: var(--ink-2);">
              <span>实机截图 · R20 量子交易系统暗色极客操盘台 (v7.5.0 · Bento 资产舱 + 资金费/手续费明细透传 + TradingView 原生 K 线)</span>
              <span class="font-bold" style="color: var(--accent);">点击图片放大</span>
            </div>
            <img
              src="/images/v750_dashboard_dark.png"
              alt="R20 量子交易系统暗色极客操盘台"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/images/v750_dashboard_dark.png'"
            />
          </div>

          <!-- Screenshot Card 2: K-line TradingView & Multi-indicators -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <div class="text-[11px] px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--line-1); color: var(--ink-2);">
              <span>实机截图 · 视觉 LLM 友好型多指标共存工作站 (MA + BOLL + VOL 高对比度无遮挡 · 150根铺满视口)</span>
              <span class="font-bold" style="color: var(--accent);">点击图片放大</span>
            </div>
            <img
              src="/images/v750_kline_tradingview.png"
              alt="视觉 LLM 友好型多指标共存工作站"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/images/v750_kline_tradingview.png'"
            />
          </div>
        </section>

        <!-- 3. 多模型决策委员会 -->
        <section id="council" class="space-y-4 pt-6 border-t" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-bold border" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">CHAPTER 03</span>
            <h2 class="text-xl sm:text-2xl font-semibold tracking-wide" style="color: var(--ink-1);">多模型决策委员会 (Council Pro)</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--ink-2);">
            为了彻底消除单一模型的幻觉与盲区，系统落地了<strong>多参谋并发辩论与博弈仲裁机制</strong>。在每一轮决策前，行情数理包将分发给各独立席位进行并发思考：
          </p>

          <div class="rounded-xl border p-4 text-xs space-y-2.5 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <div class="font-bold text-xs" style="color: var(--ink-1);">三种委员会共识机制：</div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-2.5 pt-1">
              <div class="p-2.5 rounded-lg border" style="background-color: var(--surface-1); border-color: var(--line-1);">
                <div class="font-bold" style="color: var(--down);">1. 一票否决制 (Paranoid Veto)</div>
                <div class="text-[11px] mt-1" style="color: var(--ink-2);">只要任一参谋提出重大风险预警，仲裁官无条件强制降级为 WAIT。</div>
              </div>
              <div class="p-2.5 rounded-lg border" style="background-color: var(--surface-1); border-color: var(--line-1);">
                <div class="font-bold" style="color: var(--accent);">2. 加权共识制 (Weighted Majority)</div>
                <div class="text-[11px] mt-1" style="color: var(--ink-2);">按各席位置信度加权投票，仅在同向权重绝对占优时准许发单。</div>
              </div>
              <div class="p-2.5 rounded-lg border" style="background-color: var(--surface-1); border-color: var(--line-1);">
                <div class="font-bold" style="color: var(--up);">3. 动能突破优先 (Alpha Hunter)</div>
                <div class="text-[11px] mt-1" style="color: var(--ink-2);">当微积分加速度与冲击超阈值共振时，赋予技术突破参谋优先表决权。</div>
              </div>
            </div>
          </div>

          <!-- Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <div class="text-[11px] px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--line-1); color: var(--ink-2);">
              <span>实机截图 · 对冲基金投委会决策中枢 (交易员提案制与CIO终审发单)</span>
              <span class="font-bold" style="color: var(--accent);">点击图片放大</span>
            </div>
            <img
              :src="'/docs/images/v760_council_board.png'"
              alt="对冲基金投委会决策中枢"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/docs/images/v760_council_board.png'"
            />
          </div>
        </section>

        <!-- 3.5 策略版本快照工作台 (Policy Snapshot Workbench) -->
        <section id="policy_snapshot" class="space-y-4 pt-6 border-t" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-bold border" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">CHAPTER 03.5</span>
            <h2 class="text-xl sm:text-2xl font-semibold tracking-wide" style="color: var(--ink-1);">策略版本快照控制台 (Policy Snapshot)</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--ink-2);">
            v7.4.0 独创的<strong>策略大一统版本快照控制台</strong>，实时聚合提示词、自进化心法、物理拦截器与投委会四大单元的不可变指纹，解决量化策略碎片化与复盘失真难题：
          </p>

          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
            <div class="p-3 rounded-xl border" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="font-bold text-purple-400">1. 不可变哈希指纹</div>
              <p class="text-[11px] mt-1" style="color: var(--ink-2);">四大单元配置任何变动即时生成唯一版本哈希（如 #4aa048db），不可伪造。</p>
            </div>
            <div class="p-3 rounded-xl border" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="font-bold text-cyan-400">2. 具名版本归档与删除</div>
              <p class="text-[11px] mt-1" style="color: var(--ink-2);">一键将跑得好的全盘配置持久化入库，可随时管理、备注并支持物理清除。</p>
            </div>
            <div class="p-3 rounded-xl border" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="font-bold text-emerald-400">3. 一键秒级原子回滚</div>
              <p class="text-[11px] mt-1" style="color: var(--ink-2);">调乱参数时，0.5秒内全盘原子恢复四大单元真实配置，下一交易周期立即可用。</p>
            </div>
          </div>

          <!-- Policy Snapshot Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <div class="text-[11px] px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--line-1); color: var(--ink-2);">
              <span>实机截图 · 策略版本快照控制台 (四大单元指纹透视、具名归档库与一键秒级回滚)</span>
              <span class="font-bold" style="color: var(--accent);">点击图片放大</span>
            </div>
            <img
              :src="'/docs/images/v760_policy_snapshot.png'"
              alt="策略大一统版本快照控制台"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/docs/images/v760_policy_snapshot.png'"
            />
          </div>
        </section>

        <!-- 4. 提示词策略与变量插槽 -->
        <section id="prompt_studio" class="space-y-4 pt-6 border-t" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-bold border" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">CHAPTER 04</span>
            <h2 class="text-xl sm:text-2xl font-semibold tracking-wide" style="color: var(--ink-1);">提示词策略与语义变量插槽</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--ink-2);">
            提示词策略工作室彻底解除了所有预设锁定，支持对四大核心管线（交易 System、交易 User、自进化 System、自进化 User）进行可视化定制。引入<strong>语义变量插槽（Semantic Slots）</strong>引擎：
          </p>

          <!-- Variable Table -->
          <div class="rounded-xl border overflow-x-auto shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <table class="w-full text-left text-xs whitespace-nowrap">
              <thead>
                <tr class="border-b" style="border-color: var(--line-1); background-color: var(--surface-1); color: var(--ink-2);">
                  <th class="p-3 font-bold">变量占位符</th>
                  <th class="p-3 font-bold">数据分类</th>
                  <th class="p-3 font-bold">注入内容与实战用途</th>
                </tr>
              </thead>
              <tbody class="divide-y" style="border-color: var(--line-1);">
                <tr class="hover:bg-[var(--surface-3)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--accent);">&#123;&#123;news_intelligence&#125;&#125;</td>
                  <td class="p-3" style="color: var(--ink-1);">全网快讯</td>
                  <td class="p-3" style="color: var(--ink-2);">注入全网最新重大突发要闻、黑天鹅熔断状态与宏观情绪标签</td>
                </tr>
                <tr class="hover:bg-[var(--surface-3)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--up);">&#123;&#123;trading_memory&#125;&#125;</td>
                  <td class="p-3" style="color: var(--ink-1);">自进化心法</td>
                  <td class="p-3" style="color: var(--ink-2);">注入昨日真实复盘提炼的核心心法、避坑铁律与长效实战教训</td>
                </tr>
                <tr class="hover:bg-[var(--surface-3)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--ink-1);">&#123;&#123;market_matrix&#125;&#125;</td>
                  <td class="p-3" style="color: var(--ink-1);">微积分数理</td>
                  <td class="p-3" style="color: var(--ink-2);">注入标的池全部币种最新价、微积分动力学 (v/a/j/κ/Φ)、1H ADX 与聪明钱净流</td>
                </tr>
                <tr class="hover:bg-[var(--surface-3)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--warn);">&#123;&#123;account_positions&#125;&#125;</td>
                  <td class="p-3" style="color: var(--ink-1);">账户敞口</td>
                  <td class="p-3" style="color: var(--ink-2);">注入在途持仓方向、均价、标记价、未结浮盈 UPL 及云端止损防线</td>
                </tr>
                <tr class="hover:bg-[var(--surface-3)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--accent);">&#123;&#123;pending_orders&#125;&#125;</td>
                  <td class="p-3" style="color: var(--ink-1);">挂单池</td>
                  <td class="p-3" style="color: var(--ink-2);">注入在途 Maker 限价挂单价格、张数及被动成交状态</td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <div class="text-[11px] px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--line-1); color: var(--ink-2);">
              <span>实机截图 · 提示词策略工作室 (语义插槽工具条、多方案打包导入导出与实发效果实时预览)</span>
              <span class="font-bold" style="color: var(--accent);">点击图片放大</span>
            </div>
            <img
              :src="'/docs/images/v760_prompt_studio.png'"
              alt="提示词策略工作室"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/docs/images/v760_prompt_studio.png'"
            />
          </div>
        </section>

        <!-- 5. Python 物理拦截插件 -->
        <section id="interceptors" class="space-y-4 pt-6 border-t" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-bold border" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">CHAPTER 05</span>
            <h2 class="text-xl sm:text-2xl font-semibold tracking-wide" style="color: var(--ink-1);">Python 物理拦截插件中心 (Fail-Closed)</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--ink-2);">
            发单执行层遵循 **Fail-Closed 哲学**：任何异常、参数错误或插件拦截，一律直接将订单强制重写为安全等待（WAIT）。5 大标准内置插件提供工业级防护：
          </p>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
            <div class="p-3.5 rounded-xl border space-y-1.5 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="flex items-center justify-between">
                <span class="font-bold text-xs" style="color: var(--ink-1);">01_macro_trend_filter.py</span>
                <span class="px-2 py-0.2 rounded text-[11px] font-bold border" style="background-color: var(--up-bg); color: var(--up); border-color: var(--up-line);">顺势铁律</span>
              </div>
              <p class="text-[11px]" style="color: var(--ink-2);">4H 宏观多头通道严禁摸顶开空；4H 空头通道严禁接飞刀做多。</p>
            </div>

            <div class="p-3.5 rounded-xl border space-y-1.5 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="flex items-center justify-between">
                <span class="font-bold text-xs" style="color: var(--ink-1);">02_confidence_gatekeeper.py</span>
                <span class="px-2 py-0.2 rounded text-[11px] font-bold border" style="background-color: var(--up-bg); color: var(--up); border-color: var(--up-line);">胜率第一</span>
              </div>
              <p class="text-[11px]" style="color: var(--ink-2);">模型综合置信度低于 80% 一律强制拦截为 WAIT；Meme 币种提至 85%。</p>
            </div>

            <div class="p-3.5 rounded-xl border space-y-1.5 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="flex items-center justify-between">
                <span class="font-bold text-xs" style="color: var(--ink-1);">03_adx_volatility_filter.py</span>
                <span class="px-2 py-0.2 rounded text-[11px] font-bold border" style="background-color: var(--up-bg); color: var(--up); border-color: var(--up-line);">猴市过滤</span>
              </div>
              <p class="text-[11px]" style="color: var(--ink-2);">1H ADX 趋势强度 &lt; 18 判定为低流动性无序猴市，严禁开仓频繁磨损费率。</p>
            </div>

            <div class="p-3.5 rounded-xl border space-y-1.5 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="flex items-center justify-between">
                <span class="font-bold text-xs" style="color: var(--ink-1);">04_risk_reward_gatekeeper.py</span>
                <span class="px-2 py-0.2 rounded text-[11px] font-bold border" style="background-color: var(--up-bg); color: var(--up); border-color: var(--up-line);">真实 2.0R</span>
              </div>
              <p class="text-[11px]" style="color: var(--ink-2);">根据入场价、止盈目标与云端止损线严密验算盈亏比，拒绝低于 2.0R 的劣质赔率。</p>
            </div>
          </div>

          <!-- Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <div class="text-[11px] px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--line-1); color: var(--ink-2);">
              <span>实机截图 · 物理拦截插件中心 (4H顺势铁律、置信度门禁、ADX震荡过滤与现场沙箱回归测试)</span>
              <span class="font-bold" style="color: var(--accent);">点击图片放大</span>
            </div>
            <img
              :src="'/docs/images/v760_interceptors_failclosed.png'"
              alt="物理拦截插件中心"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/docs/images/v760_interceptors_failclosed.png'"
            />
          </div>
        </section>

<!-- 7. 执行层风控管理中心 -->
        <section id="risk_control" class="space-y-4 pt-6 border-t" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-bold border" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">CHAPTER 06</span>
            <h2 class="text-xl sm:text-2xl font-semibold tracking-wide" style="color: var(--ink-1);">执行层风控管理中心 (Risk Control Center)</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--ink-2);">
            v7.6.0 里程碑：全部执行层硬风控阈值从 py 源码中彻底剥离，收敛至后台「策略配置 → 风控管理」集中可视化配置。系统遵循 <strong>单一事实源 (Single Source of Truth)</strong> 架构——<code>scripts/risk_constants.py</code> 一处定义，交易执行引擎、核心风控底座、AI 主脑提示词与后台管理页四方同源联动，<strong>提示词口径永远等于代码口径</strong>；保存即写入 <code>.env</code>，交易引擎下一巡检周期（≤15 分钟）自动生效，无需重启。
          </p>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
            <div class="p-3.5 rounded-xl border space-y-1.5 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="flex items-center justify-between">
                <span class="font-bold text-xs" style="color: var(--ink-1);">仓位与敞口</span>
                <span class="px-2 py-0.2 rounded text-[11px] font-bold border" style="background-color: var(--up-bg); color: var(--up); border-color: var(--up-line);">6 项</span>
              </div>
              <p class="text-[11px]" style="color: var(--ink-2);">最高持仓数（0=自动跟随标的池）、同向持仓上限、单笔保证金占比硬顶、单标的累计保证金占比与绝对封顶、单笔杠杆上限。</p>
            </div>
            <div class="p-3.5 rounded-xl border space-y-1.5 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="flex items-center justify-between">
                <span class="font-bold text-xs" style="color: var(--ink-1);">单笔风险门禁</span>
                <span class="px-2 py-0.2 rounded text-[11px] font-bold border" style="background-color: var(--up-bg); color: var(--up); border-color: var(--up-line);">3 项</span>
              </div>
              <p class="text-[11px]" style="color: var(--ink-2);">单笔 1R 风险额占比、最小盈亏比 R:R 硬底线（Fail-Closed 物理拦截）、新开仓最低 AI 置信度门禁。</p>
            </div>
            <div class="p-3.5 rounded-xl border space-y-1.5 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="flex items-center justify-between">
                <span class="font-bold text-xs" style="color: var(--ink-1);">止损与熔断</span>
                <span class="px-2 py-0.2 rounded text-[11px] font-bold border" style="background-color: var(--up-bg); color: var(--up); border-color: var(--up-line);">5 项</span>
              </div>
              <p class="text-[11px]" style="color: var(--ink-2);">日亏熔断（余额比例+绝对金额双封顶取小）、最长持仓时间（时间止损）、横盘判定带宽（×ATR）、止损后同标的冷静期。</p>
            </div>
            <div class="p-3.5 rounded-xl border space-y-1.5 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <div class="flex items-center justify-between">
                <span class="font-bold text-xs" style="color: var(--ink-1);">金字塔加仓门禁</span>
                <span class="px-2 py-0.2 rounded text-[11px] font-bold border" style="background-color: var(--up-bg); color: var(--up); border-color: var(--up-line);">3 项</span>
              </div>
              <p class="text-[11px]" style="color: var(--ink-2);">单标的最大加仓次数（0=彻底禁止）、加仓最小底仓浮盈率、加仓最低 AI 置信度。三重门禁缺一不可。</p>
            </div>
          </div>

          <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <h3 class="text-sm font-bold" style="color: var(--ink-1);">🎯 三套优质预设套件 · 一键应用</h3>
            <p class="text-xs leading-relaxed" style="color: var(--ink-2);">
              <strong>🛡️ 稳健防守</strong>（新账户/恶劣行情：4 仓、同向 2、保证金 10%、3x 杠杆、R:R≥2.5、置信度 85%、日亏 3% 熔断、禁止加仓）；<strong>⚖️ 均衡波段</strong>（推荐出厂基线：同向 3 仓、单笔保证金 20% 硬顶、8 小时时间止损、允许 1 次严格浮盈加仓）；<strong>🚀 进取猎手</strong>（单边趋势市：同向 4 仓、保证金 25%、7x 杠杆、置信度 72%、16 小时大波段、2 次金字塔加仓）。页面实时标记「当前生效」套件，应用后仍可逐项微调，单项一键还原，底部 RESET RISK 可整体回退代码基线。
            </p>
          </div>

          <!-- Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <div class="text-[11px] px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--line-1); color: var(--ink-2);">
              <span>实机截图 · 执行层风控管理中心 (17 项硬风控可视化与三套优质预设一键应用)</span>
              <span class="font-bold" style="color: var(--accent);">点击图片放大</span>
            </div>
            <img
              :src="'/docs/images/v760_risk_control.png'"
              alt="执行层风控管理中心"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/docs/images/v760_risk_control.png'"
            />
          </div>
        </section>

                <!-- 8. 模型连接与协议格式 -->
        <section id="llm_hub" class="space-y-4 pt-6 border-t" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-bold border" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">CHAPTER 07</span>
            <h2 class="text-xl sm:text-2xl font-semibold tracking-wide" style="color: var(--ink-1);">模型连接与 API 协议支持</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--ink-2);">
            后台「模型连接」模块实现了跨厂商无缝兼容，支持纳管市场上所有主流大模型及其代理网关：
          </p>

          <div class="rounded-xl border p-4 text-xs space-y-2 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <div class="font-bold text-xs" style="color: var(--ink-1);">支持的 API 协议标准：</div>
            <ul class="space-y-1.5 list-disc list-inside" style="color: var(--ink-2);">
              <li><strong>OpenAI Chat</strong> (<code>/chat/completions</code>)：兼容 DeepSeek R1/V3、OpenAI GPT-4o、Qwen 2.5、GLM-4 等绝大部分供应商与 OneAPI/NewAPI 聚合网关。</li>
              <li><strong>OpenAI Responses</strong> (<code>/responses</code>)：支持 OpenAI o3-mini、o1 等新一代推理协议标准。</li>
              <li><strong>Claude Messages</strong> (<code>/messages</code>)：原生适配 Anthropic Claude 3.7 Sonnet / Haiku 及其 Thinking 协议。</li>
              <li><strong>思考强度自适应 (Reasoning Effort)</strong>：支持在 HIGH（长链推理）、MEDIUM、LOW 及 AUTO 间自由微调，并由执行器自动转换或剥离非兼容参数。</li>
            </ul>
          </div>

          <!-- Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <div class="text-[11px] px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--line-1); color: var(--ink-2);">
              <span>实机截图 · 模型连接控制台 (全局生效模型卡片、预设一键填入、连通性与思考链时延诊断)</span>
              <span class="font-bold" style="color: var(--accent);">点击图片放大</span>
            </div>
            <img
              :src="'/docs/images/v760_llm_hub.png'"
              alt="模型连接控制台"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/docs/images/v760_llm_hub.png'"
            />
          </div>
        </section>

        <!-- 7. 自进化认知与长期记忆闭环 -->
        <section id="self_evolution" class="space-y-4 pt-6 border-t" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-bold border" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">CHAPTER 08</span>
            <h2 class="text-xl sm:text-2xl font-semibold tracking-wide" style="color: var(--ink-1);">自进化认知与长期记忆闭环 (每6小时复盘)</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--ink-2);">
            传统的量化策略往往因静态参数而在牛熊轮动中失效。R20 将自进化体系升级为<strong>独立的认知中枢模块</strong>，每 6 小时（每日 4 次：02:00、08:00、14:00、20:00）对全天真实成交流水穿透复盘：
          </p>

          <div class="space-y-2 text-xs font-sans" style="color: var(--ink-2);">
            <div>• <strong style="color: var(--ink-1);">高频弹性调度</strong>：每 6 小时自动触发穿透，快速感知盘口微观结构变化；</div>
            <div>• <strong style="color: var(--ink-1);">实战心法 CRUD 面板</strong>：管理员可在线一键添加新心法，或剔除失效规则，即时同步注入 System Prompt；</div>
            <div>• <strong style="color: var(--ink-1);">双层持久化存储</strong>：机器可读 JSON 与人类可读 Markdown（<code>data/AI_TRADING_MEMORY.md</code>）同步生成并即刻注入下一轮决策。</div>
          </div>

          <!-- Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <div class="text-[11px] px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--line-1); color: var(--ink-2);">
              <span>实机截图 · 自进化认知配置与长期心法记忆库 (每6小时复盘看板、实战心法CRUD管理与强制立即复盘)</span>
              <span class="font-bold" style="color: var(--accent);">点击图片放大</span>
            </div>
            <img
              :src="'/docs/images/v760_self_evolution.png'"
              alt="自进化配置与实战心法面板"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/docs/images/v760_self_evolution.png'"
            />
          </div>
        </section>

        <!-- 8. 生产部署与多通道通知 -->
        <section id="deployment" class="space-y-4 pt-6 border-t" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-bold border" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">CHAPTER 09</span>
            <h2 class="text-xl sm:text-2xl font-semibold tracking-wide" style="color: var(--ink-1);">生产部署与多通道通知告警</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--ink-2);">
            系统支持在标准 Linux (Ubuntu/Debian) 云服务器上秒级开箱即用：
          </p>

          <div class="rounded-xl border p-4 text-xs space-y-2 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
            <div class="flex items-center justify-between" style="color: var(--ink-2);">
              <span>极速启动命令序列:</span>
              <button
                @click="copyText('git clone https://github.com/555cute/r20-quantum-trader.git\ncd r20-quantum-trader\npip install -r requirements.txt\n./scripts/start_standalone.sh', 'deploy_cmd')"
                class="flex items-center space-x-1 cursor-pointer hover:underline"
                style="color: var(--accent);"
              >
                <Copy class="w-3 h-3" />
                <span>{{ copiedTag === 'deploy_cmd' ? '已复制命令' : '复制命令' }}</span>
              </button>
            </div>
            <pre class="p-3 rounded-lg overflow-x-auto leading-relaxed border" style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--up);">git clone https://github.com/555cute/r20-quantum-trader.git
cd r20-quantum-trader
pip install -r requirements.txt
./scripts/start_standalone.sh</pre>
          </div>

          <div class="text-xs space-y-1.5 font-sans" style="color: var(--ink-2);">
            <p><strong style="color: var(--ink-1);">支持的告警通知渠道：</strong></p>
            <p>1. <strong>企业微信 Webhook</strong>：开仓、平仓、止盈止损移动、自进化报告实时推送到群；</p>
            <p>2. <strong>Telegram Bot</strong>：支持自定义 API Base 反向代理，国内海外无障碍推送；</p>
            <p>3. <strong>QQ 机器人频道</strong>：支持腾讯官方开放平台 AppID 与 ClientSecret 私聊推送；</p>
            <p>4. <strong>通用 Webhook</strong>：支持飞书、钉钉、Discord 及私有运维告警服务无缝对接。</p>
          </div>
        </section>

        <!-- 9. 常见问题解答与风控底线 (FAQ) -->
        <section id="faq" class="space-y-4 pt-6 border-t" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-bold border" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">CHAPTER 10</span>
            <h2 class="text-xl sm:text-2xl font-semibold tracking-wide" style="color: var(--ink-1);">常见问题解答与风控底线 (FAQ)</h2>
          </div>

          <div class="space-y-3">
            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <h3 class="text-sm font-bold" style="color: var(--ink-1);">Q1: 为什么策略推演经常输出 WAIT？是系统出故障了吗？</h3>
              <p class="text-xs leading-relaxed" style="color: var(--ink-2);">
                不是故障。在 R20 的量化哲学中，<strong>WAIT 是最核心、最高价值的风险防御决策</strong>。当 4H 趋势不明朗、1H ADX &lt; 18 处于横盘猴市、或置信度未达到风控管理页配置的最低开仓门禁（默认 80%）时，系统坚决选择空仓等待，杜绝因频繁无效交易损耗昂贵的手续费与资金费。
              </p>
            </div>

            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <h3 class="text-sm font-bold" style="color: var(--ink-1);">Q2: 我的 OKX API Key 和大模型密钥会泄露吗？</h3>
              <p class="text-xs leading-relaxed" style="color: var(--ink-2);">
                绝不会。系统采用<strong>全本地无害化存储</strong>（本地加密 SQLite 库与环境变量隔离），开源仓库的 <code>.gitignore</code> 已严密阻断任何凭证提交；公开接口及前台响应对所有 Key、Token 与 Secret 均进行强力掩码脱敏（如 <code>sk-***abcd</code>）。
              </p>
            </div>

            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <h3 class="text-sm font-bold" style="color: var(--ink-1);">Q3: 如何将策略分享给他人，或从策略广场导入？</h3>
              <p class="text-xs leading-relaxed" style="color: var(--ink-2);">
                在后台「提示词策略」页面点击「导出策略方案」即可下载标准 <code>.json</code> 策略包；在另一台服务器上点击「导入策略」，系统将自动校验 JSON 结构与变量合法性并即刻装载生效。
              </p>
            </div>

            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
              <h3 class="text-sm font-bold" style="color: var(--ink-1);">Q4: 风控参数（持仓数、保证金占比、最长持仓时间等）在哪里修改？改完要重启吗？</h3>
              <p class="text-xs leading-relaxed" style="color: var(--ink-2);">
                在后台「策略配置 → 风控管理」页集中配置全部 17 项执行层硬风控，或直接一键应用稳健防守/均衡波段/进取猎手三套预设。保存即写入 <code>.env</code> 单一事实源，交易引擎在下一巡检周期（≤15 分钟）自动加载生效，<strong>无需重启</strong>；AI 主脑每轮收到的【本周期风险预算】小节会同步展示最新口径，越界数值由服务端物理拒绝并留痕审计。
              </p>
            </div>
          </div>
        </section>
      </main>
    </div>

    <!-- Image Zoom Modal -->
    <div
      v-if="zoomImage"
      class="fixed inset-0 z-50 bg-black/85 backdrop-blur-md flex items-center justify-center p-4 cursor-zoom-out"
      @click="zoomImage = null"
    >
      <div class="relative max-w-6xl max-h-[92dvh]">
        <img :src="zoomImage" alt="Zoomed Screenshot" class="rounded-xl shadow-2xl max-h-[90dvh] object-contain border border-white/10" />
        <button
          @click="zoomImage = null"
          class="absolute top-3 right-3 p-2 rounded-full bg-black/60 hover:bg-black/90 text-white cursor-pointer"
        >
          <X class="w-5 h-5" />
        </button>
      </div>
    </div>
  </div>
</template>

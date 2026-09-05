<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useTheme } from '../composables/useTheme'
import {
  BookOpen, ShieldCheck, Cpu, FileText, Sparkles, ArrowLeft,
  ExternalLink, Copy, Check, Terminal, Users, Brain, TrendingUp,
  Layers, Lock, ShieldAlert, ChevronRight, Menu, X, Play, RefreshCw,
  Sun, Moon, Zap, Activity, Database, Server
} from 'lucide-vue-next'

const router = useRouter()
const { theme, toggleTheme } = useTheme()

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
  { id: 'llm_hub', title: '7. 模型连接与 API 协议支持', icon: Cpu },
  { id: 'self_evolution', title: '8. 自进化认知与长期记忆闭环', icon: Brain },
  { id: 'deployment', title: '9. 生产部署与多通道通知', icon: Server },
  { id: 'faq', title: '10. 常见问题解答与风控底线 (FAQ)', icon: ShieldAlert },
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
  <div class="min-h-screen font-sans transition-colors selection:bg-blue-500/30" style="background-color: var(--bg-app); color: var(--text-main);">
    <!-- Top Header Navigation (Slim & Clean) -->
    <header class="sticky top-0 z-40 backdrop-blur-md border-b px-3 sm:px-6 h-[48px] flex items-center justify-between transition-colors" style="background-color: var(--bg-header); border-color: var(--border-subtle);">
      <div class="flex items-center space-x-2 sm:space-x-3 min-w-0">
        <button
          @click="router.push('/')"
          class="flex items-center space-x-1 px-2 py-1 rounded-lg border text-xs font-mono transition-colors cursor-pointer shadow-xs shrink-0"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          title="返回实盘终端"
        >
          <ArrowLeft class="w-3.5 h-3.5" />
          <span class="hidden sm:inline">返回终端</span>
        </button>
        <div class="h-4 w-px hidden sm:block shrink-0" style="background-color: var(--border-subtle);"></div>
        <div class="flex items-center space-x-1.5 sm:space-x-2 min-w-0">
          <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse shrink-0"></span>
          <span class="font-mono font-black text-xs sm:text-sm tracking-wide shrink-0 whitespace-nowrap" style="color: var(--text-main);">
            R20 QUANTUM
          </span>
          <span
            class="px-1.5 sm:px-2 py-0.2 rounded text-[10px] font-mono border font-bold shrink-0 whitespace-nowrap"
            style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);"
          >
            <span class="hidden md:inline">v7.4.1 官方开发与使用指南</span>
            <span class="hidden sm:inline md:hidden">v7.4.1 指南</span>
            <span class="sm:hidden">DOCS</span>
          </span>
        </div>
      </div>

      <div class="flex items-center space-x-1.5 sm:space-x-2 shrink-0">
        <!-- Mobile TOC Drawer Button -->
        <button
          @click="mobileMenuOpen = !mobileMenuOpen"
          class="sm:hidden flex items-center justify-center w-7.5 h-7.5 rounded-lg border transition-all cursor-pointer shadow-xs"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
          title="目录索引 (TOC)"
        >
          <Menu v-if="!mobileMenuOpen" class="w-3.5 h-3.5" />
          <X v-else class="w-3.5 h-3.5" />
        </button>

        <!-- Theme Toggle -->
        <button
          @click="toggleTheme"
          class="flex items-center justify-center w-7.5 h-7.5 rounded-lg border transition-all cursor-pointer shadow-xs"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
          :title="theme === 'dark' ? '切换为亮色模式' : '切换为暗色模式'"
        >
          <Sun v-if="theme === 'dark'" class="w-3.5 h-3.5 text-amber-400 hover:rotate-45 transition-transform" />
          <Moon v-else class="w-3.5 h-3.5 text-slate-700 hover:-rotate-12 transition-transform" />
        </button>

        <!-- Admin Portal (Desktop only) -->
        <button
          @click="router.push('/admin')"
          class="hidden sm:flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-xs font-mono cursor-pointer transition-colors shadow-xs"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <Lock class="w-3.5 h-3.5" />
          <span>控制台</span>
        </button>

        <!-- GitHub link -->
        <a
          href="https://github.com/555cute/r20-quantum-trader"
          target="_blank"
          class="flex items-center space-x-1 px-2 sm:px-3 py-1 rounded-lg text-xs font-mono font-bold transition-all shadow-xs"
          style="background-color: var(--text-main); color: var(--bg-card);"
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
        :class="mobileMenuOpen ? 'translate-x-0 bg-[var(--bg-card)] shadow-2xl' : '-translate-x-full sm:translate-x-0'"
        style="border-color: var(--border-subtle);"
      >
        <div class="flex items-center justify-between mb-3 px-2">
          <div class="text-[11px] font-mono font-bold uppercase tracking-wider" style="color: var(--text-faint);">
            目录索引 (TOC)
          </div>
          <button
            @click="mobileMenuOpen = false"
            class="sm:hidden p-1 rounded-lg border text-xs cursor-pointer transition-colors"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
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
              ? { backgroundColor: 'var(--color-brand-bg)', color: 'var(--color-brand)', borderColor: 'var(--color-brand-border)', fontWeight: 'bold' }
              : { backgroundColor: 'transparent', borderColor: 'transparent', color: 'var(--text-muted)' }"
          >
            <div class="flex items-center space-x-2.5 truncate">
              <component :is="s.icon" class="w-3.5 h-3.5 shrink-0" />
              <span class="truncate">{{ s.title }}</span>
            </div>
            <ChevronRight class="w-3 h-3 opacity-0 group-hover:opacity-100 transition-opacity" :class="activeSection === s.id ? 'opacity-100' : ''" />
          </button>
        </nav>

        <div
          class="mt-6 p-3.5 rounded-xl border text-xs font-mono space-y-2 shadow-xs"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <div class="text-[10px] uppercase font-bold" style="color: var(--text-faint);">社区交流与极客讨论</div>
          <div class="font-bold flex items-center justify-between" style="color: var(--text-main);">
            <span>QQ 官方群</span>
            <span class="font-mono text-xs px-1.5 py-0.2 rounded border" style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);">655973677</span>
          </div>
          <p class="text-[10px] leading-relaxed" style="color: var(--text-muted);">
            欢迎量化极客、提示词工程师、深度求索 R1 用户共同交流探索。
          </p>
        </div>
      </aside>

      <!-- Right Content Area -->
      <main class="min-w-0 flex-1 space-y-14 pb-24">
        <!-- 1. 系统概览与量化哲学 -->
        <section id="overview" class="space-y-4 pt-2">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border" style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);">CHAPTER 01</span>
            <h2 class="text-xl sm:text-2xl font-black tracking-wide" style="color: var(--text-main);">系统架构与量化哲学</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted);">
            <strong>R20 Quantum Trader</strong> 是一套专为高波动加密货币（Crypto）打造的<strong>机构级全自动波段量化决策与执行系统</strong>。系统依托 OKX 交易所官方 REST/WebSocket V5 生产 API 与 @okx_ai 官方交易底座，运行在严格的北京时间（UTC+8）自然日财务基准之上，聚焦 1H~4H 大级别顺势波段，以<strong>“胜率第一、宁缺毋滥、三位一体 Fail-Closed 物理硬防线”</strong>为最高风控宗旨。
          </p>

          <!-- 4 Core Pillars Grid -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3.5 pt-2">
            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <div class="flex items-center space-x-2 text-xs font-mono font-bold" style="color: var(--color-up);">
                <ShieldCheck class="w-4 h-4" />
                <span>Fail-Closed 物理硬拦截</span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted);">
                绝不将风控寄托于 LLM 提示词本身。在交易执行底层设立不可覆盖的 Python 物理拦截插件管线，4H 顺势门禁、80% 置信度、1H ADX 震荡过滤及真实 2.0R 盈亏比门禁物理硬切断。
              </p>
            </div>

            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <div class="flex items-center space-x-2 text-xs font-mono font-bold" style="color: var(--color-brand);">
                <Users class="w-4 h-4" />
                <span>多模型决策委员会 (Council Pro)</span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted);">
                支持并发调度宏观分析师、盘口微结构官、舆情侦察官等多参谋席位展开深度思考辩论，落地一票否决、加权共识与动能突破三种裁决机制，由首席终审仲裁官收口输出严格契约。
              </p>
            </div>

            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <div class="flex items-center space-x-2 text-xs font-mono font-bold" style="color: var(--text-main);">
                <Layers class="w-4 h-4" />
                <span>语义数据插槽提示词系统</span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted);">
                全网快讯、自进化心法、多标的数理矩阵等动态数据抽象为标准语义变量插槽（如 <code>&#123;&#123;news_intelligence&#125;&#125;</code>），支持模块自由解耦与策略方案一键导入导出。
              </p>
            </div>

            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <div class="flex items-center space-x-2 text-xs font-mono font-bold" style="color: var(--color-warn);">
                <Brain class="w-4 h-4" />
                <span>自进化认知复盘闭环</span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted);">
                每日 20:00 自动读取真实平仓台账流水进行自我反思与痛点归因，自动更新 <code>AI_TRADING_MEMORY.md</code> 长效实战心法，具备时效覆盖与动态经验淘汰机制。
              </p>
            </div>
          </div>
        </section>

        <!-- 2. 双翼工作台与资产控制舱 -->
        <section id="dashboard" class="space-y-4 pt-6 border-t" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border" style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);">CHAPTER 02</span>
            <h2 class="text-xl sm:text-2xl font-black tracking-wide" style="color: var(--text-main);">双翼量化工作台与资产控制舱</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted);">
            前台终端采用机构级**「双翼量化工作台（Dual-Wing Workstation）」**架构，支持在宽屏下的 62% : 38% 双翼并行视角与全景纵向视角间一键切换：
          </p>

          <div class="grid grid-cols-1 md:grid-cols-2 gap-3.5 pt-1 text-xs font-mono">
            <div class="p-3.5 rounded-xl border space-y-1.5" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <div class="font-bold text-sm" style="color: var(--text-main);">左翼：主控与操盘中心 (62%)</div>
              <p style="color: var(--text-muted);">
                • <strong>4 单元独立 Bento 资产控制舱</strong>：官方总权益、基准净盈亏水线、今日已结、持仓净盈亏分离解耦。<br>
                • <strong>高密度交互式操盘台 (Tactical Desk)</strong>：分段切换在途实盘持仓与限价挂单池，支持币种快速筛选与云端 100% OCO 止损状态验证。
              </p>
            </div>
            <div class="p-3.5 rounded-xl border space-y-1.5" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <div class="font-bold text-sm" style="color: var(--text-main);">右翼：六币因果动力学与微结构雷达 (38%)</div>
              <p style="color: var(--text-muted);">
                • <strong>微积分物理动能指标</strong>：实时计算一阶速度 $v$、二阶加速度 $a$、三阶冲击 $j$ 与 ADX 趋势动量。<br>
                • <strong>聪明钱微结构</strong>：追踪大户多空比与净流入流出，点击卡片即刻呼出深度数学推演与当轮实发 Prompt 抽屉。
              </p>
            </div>
          </div>

          <!-- Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <div class="text-[11px] font-mono px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--border-subtle); color: var(--text-muted);">
              <span>实机截图 · 双翼量化工作台 (左翼操盘台 + 右翼六币微积分雷达 + 亮暗双模切换)</span>
              <span class="font-bold" style="color: var(--color-brand);">点击图片放大</span>
            </div>
            <img
              src="https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/dashboard_trading.png"
              alt="双翼量化工作台全景"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = 'https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/dashboard_trading.png'"
            />
          </div>
        </section>

        <!-- 3. 多模型决策委员会 -->
        <section id="council" class="space-y-4 pt-6 border-t" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border" style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);">CHAPTER 03</span>
            <h2 class="text-xl sm:text-2xl font-black tracking-wide" style="color: var(--text-main);">多模型决策委员会 (Council Pro)</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted);">
            为了彻底消除单一模型的幻觉与盲区，系统落地了<strong>多参谋并发辩论与博弈仲裁机制</strong>。在每一轮决策前，行情数理包将分发给各独立席位进行并发思考：
          </p>

          <div class="rounded-xl border p-4 text-xs font-mono space-y-2.5 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <div class="font-bold text-xs" style="color: var(--text-main);">三种委员会共识机制：</div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-2.5 pt-1">
              <div class="p-2.5 rounded-lg border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
                <div class="font-bold" style="color: var(--color-down);">1. 一票否决制 (Paranoid Veto)</div>
                <div class="text-[11px] mt-1" style="color: var(--text-muted);">只要任一参谋提出重大风险预警，仲裁官无条件强制降级为 WAIT。</div>
              </div>
              <div class="p-2.5 rounded-lg border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
                <div class="font-bold" style="color: var(--color-brand);">2. 加权共识制 (Weighted Majority)</div>
                <div class="text-[11px] mt-1" style="color: var(--text-muted);">按各席位置信度加权投票，仅在同向权重绝对占优时准许发单。</div>
              </div>
              <div class="p-2.5 rounded-lg border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
                <div class="font-bold" style="color: var(--color-up);">3. 动能突破优先 (Alpha Hunter)</div>
                <div class="text-[11px] mt-1" style="color: var(--text-muted);">当微积分加速度与冲击超阈值共振时，赋予技术突破参谋优先表决权。</div>
              </div>
            </div>
          </div>

          <!-- Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <div class="text-[11px] font-mono px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--border-subtle); color: var(--text-muted);">
              <span>实机截图 · 对冲基金投委会决策中枢 (交易员提案制与CIO终审发单)</span>
              <span class="font-bold" style="color: var(--color-brand);">点击图片放大</span>
            </div>
            <img
              src="https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/admin_council.png"
              alt="对冲基金投委会决策中枢"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = 'https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/admin_council.png'"
            />
          </div>
        </section>

        <!-- 3.5 策略版本快照工作台 (Policy Snapshot Workbench) -->
        <section id="policy_snapshot" class="space-y-4 pt-6 border-t" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border" style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);">CHAPTER 03.5</span>
            <h2 class="text-xl sm:text-2xl font-black tracking-wide" style="color: var(--text-main);">策略版本快照控制台 (Policy Snapshot)</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted);">
            v7.4.0 独创的<strong>策略大一统版本快照控制台</strong>，实时聚合提示词、自进化心法、物理拦截器与投委会四大单元的不可变指纹，解决量化策略碎片化与复盘失真难题：
          </p>

          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 font-mono text-xs">
            <div class="p-3 rounded-xl border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <div class="font-bold text-purple-400">1. 不可变哈希指纹</div>
              <p class="text-[11px] mt-1" style="color: var(--text-muted);">四大单元配置任何变动即时生成唯一版本哈希（如 #4aa048db），不可伪造。</p>
            </div>
            <div class="p-3 rounded-xl border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <div class="font-bold text-cyan-400">2. 具名版本归档与删除</div>
              <p class="text-[11px] mt-1" style="color: var(--text-muted);">一键将跑得好的全盘配置持久化入库，可随时管理、备注并支持物理清除。</p>
            </div>
            <div class="p-3 rounded-xl border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <div class="font-bold text-emerald-400">3. 一键秒级原子回滚</div>
              <p class="text-[11px] mt-1" style="color: var(--text-muted);">调乱参数时，0.5秒内全盘原子恢复四大单元真实配置，下一交易周期立即可用。</p>
            </div>
          </div>

          <!-- Policy Snapshot Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <div class="text-[11px] font-mono px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--border-subtle); color: var(--text-muted);">
              <span>实机截图 · 策略版本快照控制台 (四大单元指纹透视、具名归档库与一键秒级回滚)</span>
              <span class="font-bold" style="color: var(--color-brand);">点击图片放大</span>
            </div>
            <img
              src="https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/admin_policy_snapshot.png"
              alt="策略大一统版本快照控制台"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = 'https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/admin_policy_snapshot.png'"
            />
          </div>
        </section>

        <!-- 4. 提示词策略与变量插槽 -->
        <section id="prompt_studio" class="space-y-4 pt-6 border-t" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border" style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);">CHAPTER 04</span>
            <h2 class="text-xl sm:text-2xl font-black tracking-wide" style="color: var(--text-main);">提示词策略与语义变量插槽</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted);">
            提示词策略工作室彻底解除了所有预设锁定，支持对四大核心管线（交易 System、交易 User、自进化 System、自进化 User）进行可视化定制。引入<strong>语义变量插槽（Semantic Slots）</strong>引擎：
          </p>

          <!-- Variable Table -->
          <div class="rounded-xl border overflow-x-auto shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <table class="w-full text-left text-xs font-mono whitespace-nowrap">
              <thead>
                <tr class="border-b" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle); color: var(--text-muted);">
                  <th class="p-3 font-bold">变量占位符</th>
                  <th class="p-3 font-bold">数据分类</th>
                  <th class="p-3 font-bold">注入内容与实战用途</th>
                </tr>
              </thead>
              <tbody class="divide-y" style="border-color: var(--border-subtle);">
                <tr class="hover:bg-[var(--bg-card-hover)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--color-brand);">&#123;&#123;news_intelligence&#125;&#125;</td>
                  <td class="p-3" style="color: var(--text-main);">全网快讯</td>
                  <td class="p-3" style="color: var(--text-muted);">注入全网最新重大突发要闻、黑天鹅熔断状态与宏观情绪标签</td>
                </tr>
                <tr class="hover:bg-[var(--bg-card-hover)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--color-up);">&#123;&#123;trading_memory&#125;&#125;</td>
                  <td class="p-3" style="color: var(--text-main);">自进化心法</td>
                  <td class="p-3" style="color: var(--text-muted);">注入昨日真实复盘提炼的核心心法、避坑铁律与长效实战教训</td>
                </tr>
                <tr class="hover:bg-[var(--bg-card-hover)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--text-main);">&#123;&#123;market_matrix&#125;&#125;</td>
                  <td class="p-3" style="color: var(--text-main);">微积分数理</td>
                  <td class="p-3" style="color: var(--text-muted);">注入 6 币种最新价、微积分动力学 (v/a/j)、1H ADX 与聪明钱净流</td>
                </tr>
                <tr class="hover:bg-[var(--bg-card-hover)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--color-warn);">&#123;&#123;account_positions&#125;&#125;</td>
                  <td class="p-3" style="color: var(--text-main);">账户敞口</td>
                  <td class="p-3" style="color: var(--text-muted);">注入在途持仓方向、均价、标记价、未结浮盈 UPL 及云端止损防线</td>
                </tr>
                <tr class="hover:bg-[var(--bg-card-hover)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--color-brand);">&#123;&#123;pending_orders&#125;&#125;</td>
                  <td class="p-3" style="color: var(--text-main);">挂单池</td>
                  <td class="p-3" style="color: var(--text-muted);">注入在途 Maker 限价挂单价格、张数及被动成交状态</td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <div class="text-[11px] font-mono px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--border-subtle); color: var(--text-muted);">
              <span>实机截图 · 提示词策略工作室 (语义插槽工具条、多方案打包导入导出与实发效果实时预览)</span>
              <span class="font-bold" style="color: var(--color-brand);">点击图片放大</span>
            </div>
            <img
              src="https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/admin_prompt_studio.png"
              alt="提示词策略工作室"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = 'https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/admin_prompt_studio.png'"
            />
          </div>
        </section>

        <!-- 5. Python 物理拦截插件 -->
        <section id="interceptors" class="space-y-4 pt-6 border-t" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border" style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);">CHAPTER 05</span>
            <h2 class="text-xl sm:text-2xl font-black tracking-wide" style="color: var(--text-main);">Python 物理拦截插件中心 (Fail-Closed)</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted);">
            发单执行层遵循 **Fail-Closed 哲学**：任何异常、参数错误或插件拦截，一律直接将订单强制重写为安全等待（WAIT）。5 大标准内置插件提供工业级防护：
          </p>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
            <div class="p-3.5 rounded-xl border space-y-1.5 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <div class="flex items-center justify-between">
                <span class="font-bold text-xs font-mono" style="color: var(--text-main);">01_macro_trend_filter.py</span>
                <span class="px-2 py-0.2 rounded text-[9px] font-bold border" style="background-color: var(--color-up-bg); color: var(--color-up); border-color: var(--color-up-border);">顺势铁律</span>
              </div>
              <p class="text-[11px]" style="color: var(--text-muted);">4H 宏观多头通道严禁摸顶开空；4H 空头通道严禁接飞刀做多。</p>
            </div>

            <div class="p-3.5 rounded-xl border space-y-1.5 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <div class="flex items-center justify-between">
                <span class="font-bold text-xs font-mono" style="color: var(--text-main);">02_confidence_gatekeeper.py</span>
                <span class="px-2 py-0.2 rounded text-[9px] font-bold border" style="background-color: var(--color-up-bg); color: var(--color-up); border-color: var(--color-up-border);">胜率第一</span>
              </div>
              <p class="text-[11px]" style="color: var(--text-muted);">模型综合置信度低于 80% 一律强制拦截为 WAIT；Meme 币种提至 85%。</p>
            </div>

            <div class="p-3.5 rounded-xl border space-y-1.5 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <div class="flex items-center justify-between">
                <span class="font-bold text-xs font-mono" style="color: var(--text-main);">03_adx_volatility_filter.py</span>
                <span class="px-2 py-0.2 rounded text-[9px] font-bold border" style="background-color: var(--color-up-bg); color: var(--color-up); border-color: var(--color-up-border);">猴市过滤</span>
              </div>
              <p class="text-[11px]" style="color: var(--text-muted);">1H ADX 趋势强度 &lt; 18 判定为低流动性无序猴市，严禁开仓频繁磨损费率。</p>
            </div>

            <div class="p-3.5 rounded-xl border space-y-1.5 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <div class="flex items-center justify-between">
                <span class="font-bold text-xs font-mono" style="color: var(--text-main);">04_risk_reward_gatekeeper.py</span>
                <span class="px-2 py-0.2 rounded text-[9px] font-bold border" style="background-color: var(--color-up-bg); color: var(--color-up); border-color: var(--color-up-border);">真实 2.0R</span>
              </div>
              <p class="text-[11px]" style="color: var(--text-muted);">根据入场价、止盈目标与云端止损线严密验算盈亏比，拒绝低于 2.0R 的劣质赔率。</p>
            </div>
          </div>

          <!-- Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <div class="text-[11px] font-mono px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--border-subtle); color: var(--text-muted);">
              <span>实机截图 · 物理拦截插件中心 (4H顺势铁律、置信度门禁、ADX震荡过滤与现场沙箱回归测试)</span>
              <span class="font-bold" style="color: var(--color-brand);">点击图片放大</span>
            </div>
            <img
              src="https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/admin_interceptors.png"
              alt="物理拦截插件中心"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = 'https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/admin_interceptors.png'"
            />
          </div>
        </section>

        <!-- 6. 模型连接与协议格式 -->
        <section id="llm_hub" class="space-y-4 pt-6 border-t" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border" style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);">CHAPTER 06</span>
            <h2 class="text-xl sm:text-2xl font-black tracking-wide" style="color: var(--text-main);">模型连接与 API 协议支持</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted);">
            后台「模型连接」模块实现了跨厂商无缝兼容，支持纳管市场上所有主流大模型及其代理网关：
          </p>

          <div class="rounded-xl border p-4 text-xs font-mono space-y-2 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <div class="font-bold text-xs" style="color: var(--text-main);">支持的 API 协议标准：</div>
            <ul class="space-y-1.5 list-disc list-inside" style="color: var(--text-muted);">
              <li><strong>OpenAI Chat</strong> (<code>/chat/completions</code>)：兼容 DeepSeek R1/V3、OpenAI GPT-4o、Qwen 2.5、GLM-4 等绝大部分供应商与 OneAPI/NewAPI 聚合网关。</li>
              <li><strong>OpenAI Responses</strong> (<code>/responses</code>)：支持 OpenAI o3-mini、o1 等新一代推理协议标准。</li>
              <li><strong>Claude Messages</strong> (<code>/messages</code>)：原生适配 Anthropic Claude 3.7 Sonnet / Haiku 及其 Thinking 协议。</li>
              <li><strong>思考强度自适应 (Reasoning Effort)</strong>：支持在 HIGH（长链推理）、MEDIUM、LOW 及 AUTO 间自由微调，并由执行器自动转换或剥离非兼容参数。</li>
            </ul>
          </div>

          <!-- Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <div class="text-[11px] font-mono px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--border-subtle); color: var(--text-muted);">
              <span>实机截图 · 模型连接控制台 (全局生效模型卡片、预设一键填入、连通性与思考链时延诊断)</span>
              <span class="font-bold" style="color: var(--color-brand);">点击图片放大</span>
            </div>
            <img
              src="https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/admin_llm.png"
              alt="模型连接控制台"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = 'https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/admin_llm.png'"
            />
          </div>
        </section>

        <!-- 7. 自进化认知与长期记忆闭环 -->
        <section id="self_evolution" class="space-y-4 pt-6 border-t" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border" style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);">CHAPTER 07</span>
            <h2 class="text-xl sm:text-2xl font-black tracking-wide" style="color: var(--text-main);">自进化认知与长期记忆闭环 (每6小时复盘)</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted);">
            传统的量化策略往往因静态参数而在牛熊轮动中失效。R20 将自进化体系升级为<strong>独立的认知中枢模块</strong>，每 6 小时（每日 4 次：02:00、08:00、14:00、20:00）对全天真实成交流水穿透复盘：
          </p>

          <div class="space-y-2 text-xs font-sans" style="color: var(--text-muted);">
            <div>• <strong style="color: var(--text-main);">高频弹性调度</strong>：每 6 小时自动触发穿透，快速感知盘口微观结构变化；</div>
            <div>• <strong style="color: var(--text-main);">实战心法 CRUD 面板</strong>：管理员可在线一键添加新心法，或剔除失效规则，即时同步注入 System Prompt；</div>
            <div>• <strong style="color: var(--text-main);">双层持久化存储</strong>：机器可读 JSON 与人类可读 Markdown（<code>data/AI_TRADING_MEMORY.md</code>）同步生成并即刻注入下一轮决策。</div>
          </div>

          <!-- Screenshot Card -->
          <div class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <div class="text-[11px] font-mono px-2 py-1 flex items-center justify-between border-b mb-2" style="border-color: var(--border-subtle); color: var(--text-muted);">
              <span>实机截图 · 自进化认知配置与长期心法记忆库 (每6小时复盘看板、实战心法CRUD管理与强制立即复盘)</span>
              <span class="font-bold" style="color: var(--color-brand);">点击图片放大</span>
            </div>
            <img
              src="https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/admin_evolution.png"
              alt="自进化配置与实战心法面板"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = 'https://raw.githubusercontent.com/555cute/r20-quantum-trader/main/docs/images/admin_evolution.png'"
            />
          </div>
        </section>

        <!-- 8. 生产部署与多通道通知 -->
        <section id="deployment" class="space-y-4 pt-6 border-t" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border" style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);">CHAPTER 08</span>
            <h2 class="text-xl sm:text-2xl font-black tracking-wide" style="color: var(--text-main);">生产部署与多通道通知告警</h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted);">
            系统支持在标准 Linux (Ubuntu/Debian) 云服务器上秒级开箱即用：
          </p>

          <div class="rounded-xl border p-4 text-xs font-mono space-y-2 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <div class="flex items-center justify-between" style="color: var(--text-muted);">
              <span>极速启动命令序列:</span>
              <button
                @click="copyText('git clone https://github.com/555cute/r20-quantum-trader.git\ncd r20-quantum-trader\npip install -r requirements.txt\n./scripts/start_standalone.sh', 'deploy_cmd')"
                class="flex items-center space-x-1 cursor-pointer hover:underline"
                style="color: var(--color-brand);"
              >
                <Copy class="w-3 h-3" />
                <span>{{ copiedTag === 'deploy_cmd' ? '已复制命令' : '复制命令' }}</span>
              </button>
            </div>
            <pre class="p-3 rounded-lg overflow-x-auto leading-relaxed border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--color-up);">git clone https://github.com/555cute/r20-quantum-trader.git
cd r20-quantum-trader
pip install -r requirements.txt
./scripts/start_standalone.sh</pre>
          </div>

          <div class="text-xs space-y-1.5 font-sans" style="color: var(--text-muted);">
            <p><strong style="color: var(--text-main);">支持的告警通知渠道：</strong></p>
            <p>1. <strong>企业微信 Webhook</strong>：开仓、平仓、止盈止损移动、自进化报告实时推送到群；</p>
            <p>2. <strong>Telegram Bot</strong>：支持自定义 API Base 反向代理，国内海外无障碍推送；</p>
            <p>3. <strong>QQ 机器人频道</strong>：支持腾讯官方开放平台 AppID 与 ClientSecret 私聊推送；</p>
            <p>4. <strong>通用 Webhook</strong>：支持飞书、钉钉、Discord 及私有运维告警服务无缝对接。</p>
          </div>
        </section>

        <!-- 9. 常见问题解答与风控底线 (FAQ) -->
        <section id="faq" class="space-y-4 pt-6 border-t" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <span class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border" style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);">CHAPTER 09</span>
            <h2 class="text-xl sm:text-2xl font-black tracking-wide" style="color: var(--text-main);">常见问题解答与风控底线 (FAQ)</h2>
          </div>

          <div class="space-y-3">
            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <h3 class="text-sm font-bold" style="color: var(--text-main);">Q1: 为什么策略推演经常输出 WAIT？是系统出故障了吗？</h3>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted);">
                不是故障。在 R20 的量化哲学中，<strong>WAIT 是最核心、最高价值的风险防御决策</strong>。当 4H 趋势不明朗、1H ADX &lt; 18 处于横盘猴市、或置信度未达到 80% 及格线时，系统坚决选择空仓等待，杜绝因频繁无效交易损耗昂贵的手续费与资金费。
              </p>
            </div>

            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <h3 class="text-sm font-bold" style="color: var(--text-main);">Q2: 我的 OKX API Key 和大模型密钥会泄露吗？</h3>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted);">
                绝不会。系统采用<strong>全本地无害化存储</strong>（本地加密 SQLite 库与环境变量隔离），开源仓库的 <code>.gitignore</code> 已严密阻断任何凭证提交；公开接口及前台响应对所有 Key、Token 与 Secret 均进行强力掩码脱敏（如 <code>sk-***abcd</code>）。
              </p>
            </div>

            <div class="p-4 rounded-xl border space-y-2 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
              <h3 class="text-sm font-bold" style="color: var(--text-main);">Q3: 如何将策略分享给他人，或从策略广场导入？</h3>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted);">
                在后台「提示词策略」页面点击「导出策略方案」即可下载标准 <code>.json</code> 策略包；在另一台服务器上点击「导入策略」，系统将自动校验 JSON 结构与变量合法性并即刻装载生效。
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

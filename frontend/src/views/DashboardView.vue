<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDashboardStore } from '../stores/dashboard'
import HeaderBar from '../components/HeaderBar.vue'
import TopHudRibbon from '../components/TopHudRibbon.vue'
import TacticalDesk from '../components/TacticalDesk.vue'
import InstrumentMatrix from '../components/InstrumentMatrix.vue'
import LedgerLogs from '../components/LedgerLogs.vue'
import NewsIntelligence from '../components/NewsIntelligence.vue'
import SelfEvolutionLab from '../components/SelfEvolutionLab.vue'
import TradesLedger from '../components/TradesLedger.vue'
import AiBrainHistory from '../components/AiBrainHistory.vue'
import FloatingActions from '../components/FloatingActions.vue'
import {
  LayoutGrid,
  Cpu,
  Newspaper,
  Sparkles,
  Receipt,
  Columns,
  Rows,
} from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const store = useDashboardStore()
const layoutMode = ref<'dual' | 'stacked'>('stacked')

// Sync initial tab from route path
function syncTabFromRoute() {
  const metaTab = route.meta?.tab as any
  if (metaTab && ['trading', 'factors', 'news', 'lab', 'history'].includes(metaTab)) {
    store.activeTab = metaTab
  } else if (route.path === '/') {
    store.activeTab = 'trading'
  }
}

// Watch route changes to update activeTab
watch(() => route.path, () => {
  syncTabFromRoute()
})

// Watch store.activeTab to push URL route for perfect SEO & Cloudflare caching
watch(() => store.activeTab, (newTab) => {
  const targetPath = newTab === 'trading' ? '/' : `/${newTab}`
  if (route.path !== targetPath && !route.path.startsWith('/admin') && !route.path.startsWith('/docs')) {
    router.replace(targetPath).catch(() => {})
  }
})

onMounted(() => {
  syncTabFromRoute()
  store.startPolling(3000)
  try {
    const saved = localStorage.getItem('r20_dashboard_layout_v2')
    if (saved === 'dual' || saved === 'stacked') {
      layoutMode.value = saved
    } else {
      layoutMode.value = 'stacked'
    }
  } catch {
    layoutMode.value = 'stacked'
  }
})

onUnmounted(() => {
  store.stopPolling()
})

function setLayout(mode: 'dual' | 'stacked') {
  layoutMode.value = mode
  try {
    localStorage.setItem('r20_dashboard_layout_v2', mode)
  } catch {
    // fallback
  }
}
</script>

<template>
  <div
    class="min-h-screen flex flex-col transition-colors selection:bg-blue-500/30"
    style="background-color: var(--bg-app); color: var(--text-main);"
  >
    <!-- Top Nav Ribbon with 5 Tabs (fixed: never scrolls away) -->
    <HeaderBar />

    <!-- Spacer for fixed header -->
    <div class="h-[46px] sm:h-[50px] shrink-0"></div>

    <!-- Dynamic Main Content Based on Active Tab -->
    <main class="flex-1 max-w-[2160px] w-full mx-auto px-3 sm:px-6 2xl:px-8 pt-3 pb-24 sm:pb-6 space-y-3.5">
      <!-- TAB 1: 实盘矩阵 (TRADING) -->
      <div v-show="store.activeTab === 'trading'" class="space-y-3.5">
        <!-- Sub-Header Controls: Layout Switcher & Status Line -->
        <div class="flex items-center justify-between px-1">
          <div class="flex items-center space-x-2 text-xs font-mono">
            <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
            <span class="font-bold tracking-wider" style="color: var(--text-main);">量子量化实盘监控</span>
            <span style="color: var(--text-faint);">·</span>
            <span style="color: var(--text-muted);">自动决策周期：15m</span>
          </div>

          <!-- Layout Mode Switcher (Desktop) -->
          <div
            class="hidden md:flex items-center p-0.5 rounded-lg border text-xs font-mono"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <button
              @click="setLayout('stacked')"
              class="flex items-center space-x-1.5 px-2.5 py-1 rounded-md transition-all cursor-pointer"
              :style="layoutMode === 'stacked'
                ? { backgroundColor: 'var(--color-brand-bg)', color: 'var(--color-brand)', fontWeight: 'bold' }
                : { color: 'var(--text-muted)' }"
              title="标准全景布局：自上而下沉浸式展开资产、操盘台与六币动力学雷达"
            >
              <Rows class="w-3.5 h-3.5" />
              <span>全景视图</span>
            </button>
            <button
              @click="setLayout('dual')"
              class="flex items-center space-x-1.5 px-2.5 py-1 rounded-md transition-all cursor-pointer"
              :style="layoutMode === 'dual'
                ? { backgroundColor: 'var(--color-brand-bg)', color: 'var(--color-brand)', fontWeight: 'bold' }
                : { color: 'var(--text-muted)' }"
              title="双翼工作台：左翼操盘中心，右翼微结构雷达"
            >
              <Columns class="w-3.5 h-3.5" />
              <span>双翼分栏</span>
            </button>
          </div>
        </div>

        <!-- Layout Mode 1: Dual-Wing Institutional Workstation (Only when user explicitly chooses dual) -->
        <div v-if="layoutMode === 'dual'" class="flex flex-col lg:flex-row gap-3.5 items-start">
          <!-- Left Wing: Master Asset Cockpit + Tactical Desk (62% width on wide displays) -->
          <div class="w-full lg:w-[62%] 2xl:w-[64%] space-y-3.5">
            <!-- 1. Master Bento HUD Cockpit -->
            <TopHudRibbon />
            <!-- 2. Integrated Interactive Tactical Desk (Positions + Orders) -->
            <TacticalDesk />
          </div>

          <!-- Right Wing: 6-Asset Live Dynamics Radar (38% width on wide displays) -->
          <div class="w-full lg:w-[38%] 2xl:w-[36%] space-y-3.5">
            <InstrumentMatrix />
          </div>
        </div>

        <!-- Layout Mode 2: Stacked Full View (Default: Natural Top-Down Flow) -->
        <div v-else class="space-y-3.5">
          <TopHudRibbon />
          <TacticalDesk />
          <InstrumentMatrix />
        </div>
      </div>

      <!-- TAB 2: AI全景推演 (FACTORS) -->
      <div v-show="store.activeTab === 'factors'" class="space-y-3.5">
        <AiBrainHistory />
      </div>

      <!-- TAB 3: 全网舆情 (NEWS) -->
      <div v-show="store.activeTab === 'news'" class="space-y-3.5">
        <NewsIntelligence />
      </div>

      <!-- TAB 4: 自进化实验室 (LAB) -->
      <div v-show="store.activeTab === 'lab'" class="space-y-3.5">
        <SelfEvolutionLab />
      </div>

      <!-- TAB 5: 交易台账与生命周期 (HISTORY) -->
      <div v-show="store.activeTab === 'history'" class="space-y-3.5">
        <TradesLedger />
        <LedgerLogs />
      </div>
    </main>

    <!-- Global Footer -->
    <footer
      class="border-t py-3 text-center text-xs font-mono transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
    >
      <div class="flex items-center justify-center space-x-2">
        <button
          @click="store.showAboutModal = true"
          class="hover:text-[var(--color-brand)] transition-colors cursor-pointer"
          title="点击查看开源仓库与项目信息"
        >
          R20 QUANTUM TRADER v7.4.1
        </button>
        <span>•</span>
        <span>VUE 3 + VITE + TAILWIND CSS</span>
        <span>•</span>
        <a
          href="https://github.com/555cute/r20-quantum-trader"
          target="_blank"
          rel="noopener noreferrer"
          class="hover:text-[var(--color-brand)] transition-colors"
        >
          GitHub
        </a>
      </div>
    </footer>

    <!-- Mobile Bottom Navigation Bar (md:hidden) -->
    <nav
      class="md:hidden fixed inset-x-0 bottom-0 z-50 px-2 py-1.5 border-t backdrop-blur-xl"
      style="background-color: var(--bg-header); border-color: var(--border-subtle);"
    >
      <div class="max-w-md mx-auto flex items-center justify-around font-mono">
        <button
          @click="store.activeTab = 'trading'"
          class="flex flex-col items-center justify-center flex-1 py-1 transition cursor-pointer"
          :style="{ color: store.activeTab === 'trading' ? 'var(--color-brand)' : 'var(--text-muted)' }"
        >
          <LayoutGrid class="w-4 h-4 mb-0.5" />
          <span class="text-[10px] font-bold">实盘</span>
        </button>
        <button
          @click="store.activeTab = 'factors'"
          class="flex flex-col items-center justify-center flex-1 py-1 transition cursor-pointer"
          :style="{ color: store.activeTab === 'factors' ? 'var(--color-brand)' : 'var(--text-muted)' }"
        >
          <Cpu class="w-4 h-4 mb-0.5" />
          <span class="text-[10px] font-bold">AI推演</span>
        </button>
        <button
          @click="store.activeTab = 'news'"
          class="flex flex-col items-center justify-center flex-1 py-1 transition cursor-pointer"
          :style="{ color: store.activeTab === 'news' ? 'var(--color-brand)' : 'var(--text-muted)' }"
        >
          <Newspaper class="w-4 h-4 mb-0.5" />
          <span class="text-[10px] font-bold">舆情</span>
        </button>
        <button
          @click="store.activeTab = 'lab'"
          class="flex flex-col items-center justify-center flex-1 py-1 transition cursor-pointer"
          :style="{ color: store.activeTab === 'lab' ? 'var(--color-brand)' : 'var(--text-muted)' }"
        >
          <Sparkles class="w-4 h-4 mb-0.5" />
          <span class="text-[10px] font-bold">自进化</span>
        </button>
        <button
          @click="store.activeTab = 'history'"
          class="flex flex-col items-center justify-center flex-1 py-1 transition cursor-pointer"
          :style="{ color: store.activeTab === 'history' ? 'var(--color-brand)' : 'var(--text-muted)' }"
        >
          <Receipt class="w-4 h-4 mb-0.5" />
          <span class="text-[10px] font-bold">台账</span>
        </button>
      </div>
    </nav>

    <!-- Global Floating Actions -->
    <FloatingActions />
  </div>
</template>

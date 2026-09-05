<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDashboardStore } from '../stores/dashboard'
import { useI18n } from '../composables/useI18n'
import HeaderBar from '../components/HeaderBar.vue'
import TopHudRibbon from '../components/TopHudRibbon.vue'
import LiveTradingViewChart from '../components/LiveTradingViewChart.vue'
import TacticalDesk from '../components/TacticalDesk.vue'
import InstrumentMatrix from '../components/InstrumentMatrix.vue'
import LedgerLogs from '../components/LedgerLogs.vue'
import NewsIntelligence from '../components/NewsIntelligence.vue'
import SelfEvolutionLab from '../components/SelfEvolutionLab.vue'
import TradesLedger from '../components/TradesLedger.vue'
import AiBrainHistory from '../components/AiBrainHistory.vue'
import FloatingActions from '../components/FloatingActions.vue'
import {
  Terminal,
  Cpu,
  Newspaper,
  Sparkles,
  Receipt,
  Settings,
} from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const store = useDashboardStore()
const { t } = useI18n()

function syncTabFromRoute() {
  const metaTab = route.meta?.tab as any
  if (metaTab && ['trading', 'factors', 'news', 'lab', 'history'].includes(metaTab)) {
    store.activeTab = metaTab
  } else if (route.path === '/' || route.path === '') {
    store.activeTab = 'trading'
  }
}

onMounted(() => {
  syncTabFromRoute()
  store.startPolling(3000)
})

onUnmounted(() => {
  store.stopPolling()
})

const mobileTabs = computed(() => [
  { id: 'trading', label: '实盘', enLabel: 'Trade', icon: Terminal },
  { id: 'factors', label: 'AI推演', enLabel: 'Radar', icon: Cpu },
  { id: 'news', label: '舆情', enLabel: 'News', icon: Newspaper },
  { id: 'lab', label: '实验室', enLabel: 'Labs', icon: Sparkles },
  { id: 'history', label: '台账', enLabel: 'Ledger', icon: Receipt },
])
</script>

<template>
  <div
    class="min-h-screen flex flex-col transition-colors select-none font-sans"
    style="background-color: var(--bg-app); color: var(--text-main);"
  >
    <!-- Bloomberg/Terminal Style Unified Header Bar -->
    <HeaderBar />

    <!-- Fixed Header Spacer -->
    <div class="h-[48px] shrink-0"></div>

    <!-- Main Professional Workstation Container -->
    <main class="flex-1 w-full px-2 lg:px-4 pt-2 pb-20 md:pb-6 space-y-2.5">
      <!-- ================================================================= -->
      <!-- TAB 1: MASTER TERMINAL (机构级全景交易终端) -->
      <!-- ================================================================= -->
      <div v-show="store.activeTab === 'trading'" class="space-y-2.5">
        <!-- 1. Top HUD Ribbon (4 Metric Bento Blocks in 1 uniform row) -->
        <TopHudRibbon />

        <!-- 2. Dual-Wing Integrated Workstation Grid -->
        <div class="grid grid-cols-1 xl:grid-cols-12 gap-2.5 items-start">
          <!-- Left Wing: Live Professional Chart + Tactical Order Desk (8 Cols on Wide Screens) -->
          <div class="xl:col-span-8 space-y-2.5">
            <!-- Professional TradingView Chart -->
            <LiveTradingViewChart />

            <!-- Tactical Positions & Orders Workstation -->
            <TacticalDesk />
          </div>

          <!-- Right Wing: 6-Asset Dynamics Radar & Micro-Calculus (4 Cols on Wide Screens) -->
          <div class="xl:col-span-4 space-y-2.5">
            <InstrumentMatrix />
          </div>
        </div>

        <!-- 3. Real-Time Telemetry & Log Console Stream -->
        <LedgerLogs />
      </div>

      <!-- ================================================================= -->
      <!-- TAB 2: RADAR & DYNAMICS (AI 全景推演与微积分) -->
      <!-- ================================================================= -->
      <div v-show="store.activeTab === 'factors'" class="space-y-2.5">
        <AiBrainHistory />
      </div>

      <!-- ================================================================= -->
      <!-- TAB 3: NEWS & FLOWS (全网多所舆情与流动性情报) -->
      <!-- ================================================================= -->
      <div v-show="store.activeTab === 'news'" class="space-y-2.5">
        <NewsIntelligence />
      </div>

      <!-- ================================================================= -->
      <!-- TAB 4: QUANTUM LABS (v8.0 OmniMatrix 前沿试验田) -->
      <!-- ================================================================= -->
      <div v-show="store.activeTab === 'lab'" class="space-y-2.5">
        <SelfEvolutionLab />
      </div>

      <!-- ================================================================= -->
      <!-- TAB 5: TRADING LEDGER (实盘审计台账) -->
      <!-- ================================================================= -->
      <div v-show="store.activeTab === 'history'" class="space-y-2.5">
        <TradesLedger />
      </div>
    </main>

    <!-- Mobile High-Precision Bottom Docking Navigation Bar (md:hidden) -->
    <nav
      class="md:hidden fixed inset-x-0 bottom-0 z-40 px-2 py-1.5 border-t backdrop-blur-xl transition-colors font-mono"
      style="background-color: var(--bg-header); border-color: var(--border-subtle);"
    >
      <div class="max-w-md mx-auto flex items-center justify-around">
        <button
          v-for="item in mobileTabs"
          :key="item.id"
          @click="store.activeTab = item.id as any"
          class="flex flex-col items-center justify-center flex-1 py-1 transition-all cursor-pointer rounded-lg"
          :style="{
            color: store.activeTab === item.id ? 'var(--text-main)' : 'var(--text-muted)',
            backgroundColor: store.activeTab === item.id ? 'var(--bg-card-hover)' : 'transparent'
          }"
        >
          <component :is="item.icon" class="w-4 h-4 mb-0.5" />
          <span class="text-[10px] font-bold">{{ item.label }}</span>
        </button>

        <a
          href="/admin/"
          class="flex flex-col items-center justify-center flex-1 py-1 transition-all cursor-pointer text-slate-400 hover:text-slate-100"
        >
          <Settings class="w-4 h-4 mb-0.5 text-indigo-400" />
          <span class="text-[10px] font-bold">后台</span>
        </a>
      </div>
    </nav>

    <!-- Global Floating Action Buttons (Audio Alert, Quick Actions) -->
    <FloatingActions />
  </div>
</template>

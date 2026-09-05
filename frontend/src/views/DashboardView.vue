<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDashboardStore } from '../stores/dashboard'
import HeaderBar from '../components/HeaderBar.vue'
import TopHudRibbon from '../components/TopHudRibbon.vue'
import TacticalDesk from '../components/TacticalDesk.vue'
import InstrumentMatrix from '../components/InstrumentMatrix.vue'
import LiveTradingViewChart from '../components/LiveTradingViewChart.vue'
import LedgerLogs from '../components/LedgerLogs.vue'
import NewsIntelligence from '../components/NewsIntelligence.vue'
import SelfEvolutionLab from '../components/SelfEvolutionLab.vue'
import TradesLedger from '../components/TradesLedger.vue'
import AiBrainHistory from '../components/AiBrainHistory.vue'
import FloatingActions from '../components/FloatingActions.vue'
import {
  Activity,
  Maximize2,
  RefreshCw,
  Terminal,
  Cpu,
  Layers,
} from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const store = useDashboardStore()

// High-density Institutional Workstation View
function syncTabFromRoute() {
  const metaTab = route.meta?.tab as any
  if (metaTab && ['trading', 'factors', 'news', 'lab', 'history'].includes(metaTab)) {
    store.activeTab = metaTab
  } else if (route.path === '/') {
    store.activeTab = 'trading'
  }
}

watch(() => route.path, () => {
  syncTabFromRoute()
})

watch(() => store.activeTab, (newTab) => {
  const targetPath = newTab === 'trading' ? '/' : `/${newTab}`
  if (route.path !== targetPath && !route.path.startsWith('/admin') && !route.path.startsWith('/docs')) {
    router.replace(targetPath).catch(() => {})
  }
})

onMounted(() => {
  syncTabFromRoute()
  store.startPolling(3000)
})

onUnmounted(() => {
  store.stopPolling()
})
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
    <main class="flex-1 w-full px-2.5 lg:px-4 pt-2.5 pb-12 space-y-2.5">
      <!-- ================================================================= -->
      <!-- TAB 1: MASTER TERMINAL (机构级全景交易终端) -->
      <!-- ================================================================= -->
      <div v-show="store.activeTab === 'trading'" class="space-y-2.5">
        <!-- 1. Top HUD Ribbon (4 Metric Bento Blocks in 1 uniform row) -->
        <TopHudRibbon />

        <!-- 2. Dual-Wing Integrated Workstation Grid -->
        <div class="grid grid-cols-1 xl:grid-cols-12 gap-2.5 items-start">
          <!-- Left Wing: Live Professional Chart + Tactical Order Desk (7 Cols on Wide Screens) -->
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

    <!-- Global Floating Action Buttons (Audio Alert, Quick Actions) -->
    <FloatingActions />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDashboardStore } from '../stores/dashboard'
import { useI18nStore } from '../stores/i18n'
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
const i18n = useI18nStore()

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
  { id: 'trading', label: i18n.locale === 'zh' ? '操盘' : 'Desk', icon: Terminal },
  { id: 'factors', label: i18n.locale === 'zh' ? '决策' : 'Brain', icon: Cpu },
  { id: 'news', label: i18n.locale === 'zh' ? '全息' : 'Intel', icon: Newspaper },
  { id: 'lab', label: i18n.locale === 'zh' ? '实验' : 'Labs', icon: Sparkles },
  { id: 'history', label: i18n.locale === 'zh' ? '台账' : 'Ledger', icon: Receipt },
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
      <!-- 统一顶部四大指标卡 -->
      <TopHudRibbon />

      <!-- ================================================================= -->
      <!-- TAB 1: 综合操盘 (Master Desk) - 70/30 黄金分割专注实盘执行 -->
      <!-- ================================================================= -->
      <div v-show="store.activeTab === 'trading'" class="space-y-2.5">
        <div class="grid grid-cols-1 xl:grid-cols-12 gap-2.5 items-start">
          <!-- Left Wing: Live Professional Chart (8 Cols) -->
          <div class="xl:col-span-8 space-y-2.5">
            <LiveTradingViewChart />
          </div>

          <!-- Right Wing: Tactical Order Desk (4 Cols) -->
          <div class="xl:col-span-4 space-y-2.5">
            <TacticalDesk />
          </div>
        </div>

        <!-- Real-Time Telemetry & Log Console Stream -->
        <LedgerLogs />
      </div>

      <!-- ================================================================= -->
      <!-- TAB 2: 决策中枢 (Decision Brain) - 投委会深度辩论 + 6 币微积分动能雷达 -->
      <!-- ================================================================= -->
      <div v-show="store.activeTab === 'factors'" class="space-y-2.5">
        <div class="grid grid-cols-1 xl:grid-cols-12 gap-2.5 items-start">
          <div class="xl:col-span-8 space-y-2.5">
            <AiBrainHistory />
          </div>
          <div class="xl:col-span-4 space-y-2.5">
            <InstrumentMatrix />
          </div>
        </div>
      </div>

      <!-- ================================================================= -->
      <!-- TAB 3: 市场全息 (Market Intel) - 全网多所舆情与宏观异动流 -->
      <!-- ================================================================= -->
      <div v-show="store.activeTab === 'news'" class="space-y-2.5">
        <NewsIntelligence />
      </div>

      <!-- ================================================================= -->
      <!-- TAB 4: 量子实验室 (Quantum Labs) - 合并台账 / 跨所套利 / 本地视觉 -->
      <!-- ================================================================= -->
      <div v-show="store.activeTab === 'lab'" class="space-y-2.5">
        <SelfEvolutionLab />
      </div>

      <!-- ================================================================= -->
      <!-- TAB 5: 审计台账 (Audit Ledger) - 历史已结成交与策略快照闭环 -->
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
          <span class="text-[10px] font-bold">{{ i18n.t.adminConsole }}</span>
        </a>
      </div>
    </nav>

    <!-- Global Floating Action Buttons (Audio Alert, Quick Actions) -->
    <FloatingActions />
  </div>
</template>

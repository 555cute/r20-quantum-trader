<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useTheme } from '../composables/useTheme'
import AboutModal from './AboutModal.vue'
import {
  Activity,
  Terminal,
  Cpu,
  Newspaper,
  Sparkles,
  Receipt,
  Settings,
  BookOpen,
  Sun,
  Moon,
  Clock,
  Wifi,
  ShieldAlert,
} from 'lucide-vue-next'

const store = useDashboardStore()
const { theme, toggleTheme } = useTheme()

const currentTimeUtc = ref('')
const currentTimeLocal = ref('')
let timer: any = null

function updateClock() {
  const d = new Date()
  currentTimeUtc.value = d.toLocaleTimeString('en-GB', { timeZone: 'UTC', hour12: false })
  currentTimeLocal.value = d.toLocaleTimeString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })
}

onMounted(() => {
  updateClock()
  timer = setInterval(updateClock, 1000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

const tabs = [
  { id: 'trading', label: 'MASTER TERMINAL', sub: '主交易台', icon: Terminal },
  { id: 'factors', label: 'RADAR & DYNAMICS', sub: '微积分动能', icon: Cpu },
  { id: 'news', label: 'NEWS & FLOWS', sub: '全网舆情', icon: Newspaper },
  { id: 'lab', label: 'QUANTUM LABS', sub: '前沿试验田', icon: Sparkles },
  { id: 'history', label: 'TRADING LEDGER', sub: '交易台账', icon: Receipt },
] as const
</script>

<template>
  <header
    class="fixed top-0 left-0 right-0 z-40 h-[48px] px-3 lg:px-4 flex items-center border-b select-none transition-colors"
    style="background-color: var(--bg-header); border-color: var(--border-subtle); backdrop-filter: blur(16px);"
  >
    <div class="w-full flex items-center justify-between gap-3">
      <!-- 1. Left: Institutional Terminal Branding & Global Venue Heartbeats -->
      <div class="flex items-center space-x-3 shrink-0">
        <!-- Logo & Identity -->
        <div class="flex items-center space-x-2 cursor-pointer" @click="store.activeTab = 'trading'">
          <div
            class="w-6 h-6 rounded flex items-center justify-center font-mono font-black text-xs border tracking-tighter"
            style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); color: #f8fafc; border-color: #334155;"
          >
            Ω
          </div>
          <div class="flex flex-col">
            <div class="flex items-center space-x-1.5">
              <span class="font-mono font-black text-xs tracking-wider uppercase text-slate-100">
                R20 QUANTUM
              </span>
              <span class="text-[9px] font-mono px-1 py-0.2 rounded border border-slate-700 bg-slate-800 text-slate-300 font-semibold">
                DESKTOP
              </span>
            </div>
          </div>
        </div>

        <!-- Venue Status Chips (OKX / BINANCE / GATE) -->
        <div class="hidden xl:flex items-center space-x-1.5 pl-2 border-l" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-1 px-2 py-0.5 rounded border text-[10px] font-mono" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
            <span class="text-slate-300 font-bold">OKX</span>
            <span class="text-[9px] text-emerald-400">PROD</span>
          </div>
          <div class="flex items-center space-x-1 px-2 py-0.5 rounded border text-[10px] font-mono" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span>
            <span class="text-slate-300 font-bold">BINANCE</span>
            <span class="text-[9px] text-amber-400">FEED</span>
          </div>
          <div class="flex items-center space-x-1 px-2 py-0.5 rounded border text-[10px] font-mono" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <span class="w-1.5 h-1.5 rounded-full bg-blue-400"></span>
            <span class="text-slate-300 font-bold">GATE</span>
            <span class="text-[9px] text-blue-400">FEED</span>
          </div>
        </div>
      </div>

      <!-- 2. Center: Modular Tab Docking Station -->
      <nav
        class="hidden md:flex items-center p-1 rounded-lg border shrink-0 transition-colors"
        style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
      >
        <button
          v-for="tab in tabs"
          :key="tab.id"
          @click="store.activeTab = tab.id as any"
          class="h-7.5 flex items-center space-x-2 px-3 rounded-md text-xs font-mono font-bold transition-all cursor-pointer whitespace-nowrap"
          :style="store.activeTab === tab.id
            ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-app)' }
            : { color: 'var(--text-muted)' }"
          :class="store.activeTab === tab.id ? 'shadow-xs' : 'hover:text-[var(--text-main)]'"
        >
          <component :is="tab.icon" class="w-3.5 h-3.5" />
          <span>{{ tab.label }}</span>
        </button>
      </nav>

      <!-- 3. Right: Telemetry, Global Dual Clocks & Quick System Controls -->
      <div class="flex items-center space-x-2 text-xs font-mono">
        <!-- Dual Clocks: UTC & SGT (Professional Bloomberg style) -->
        <div
          class="hidden 2xl:flex items-center h-7 space-x-2 px-2.5 rounded border text-[11px]"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <span>UTC <strong class="text-slate-200 num-tabular">{{ currentTimeUtc }}</strong></span>
          <span class="text-slate-600">|</span>
          <span>BJT <strong class="text-emerald-400 num-tabular">{{ currentTimeLocal }}</strong></span>
        </div>

        <!-- Risk Sentinel Badge -->
        <div
          class="hidden sm:flex items-center h-7 px-2 rounded border text-[10px] font-mono font-bold text-emerald-400 border-emerald-500/30 bg-emerald-500/10"
          title="三位一体物理拦截管线：FAIL-CLOSED 模式已生效"
        >
          <ShieldAlert class="w-3 h-3 mr-1 text-emerald-400" />
          FAIL-CLOSED
        </div>

        <!-- Admin Console Link -->
        <a
          href="/admin/"
          class="h-7 px-2.5 rounded border flex items-center space-x-1.5 cursor-pointer hover:bg-[var(--bg-card-hover)] transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
          title="进入管理员风控治理控制台"
        >
          <Settings class="w-3.5 h-3.5 text-indigo-400" />
          <span class="hidden sm:inline font-bold">ADMIN</span>
        </a>

        <!-- Theme Toggle -->
        <button
          @click="toggleTheme"
          class="w-7 h-7 rounded border flex items-center justify-center cursor-pointer hover:bg-[var(--bg-card-hover)] transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          title="切换亮色/暗色主题"
        >
          <Sun v-if="theme === 'dark'" class="w-3.5 h-3.5" />
          <Moon v-else class="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  </header>
</template>

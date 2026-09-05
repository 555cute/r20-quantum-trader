<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useTheme } from '../composables/useTheme'
import { useI18n } from '../composables/useI18n'
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
  Globe,
  Menu,
  X,
  Layers,
  ChevronRight,
} from 'lucide-vue-next'

const store = useDashboardStore()
const { theme, toggleTheme } = useTheme()
const { locale, t, toggleLocale } = useI18n()

const currentTimeUtc = ref('')
const currentTimeLocal = ref('')
const mobileDrawerOpen = ref(false)
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

const tabs = computed(() => [
  { id: 'trading', label: t.terminal, sub: t.terminalSub, icon: Terminal },
  { id: 'factors', label: t.radar, sub: t.radarSub, icon: Cpu },
  { id: 'news', label: t.news, sub: t.newsSub, icon: Newspaper },
  { id: 'lab', label: t.labs, sub: t.labsSub, icon: Sparkles },
  { id: 'history', label: t.ledger, sub: t.ledgerSub, icon: Receipt },
])

function switchTabMobile(tabId: any) {
  store.activeTab = tabId
  mobileDrawerOpen.value = false
}
</script>

<template>
  <header
    class="fixed top-0 left-0 right-0 z-40 h-[48px] px-2.5 sm:px-4 flex items-center border-b select-none transition-colors"
    style="background-color: var(--bg-header); border-color: var(--border-subtle); backdrop-filter: blur(16px);"
  >
    <div class="w-full flex items-center justify-between gap-2 sm:gap-3">
      <!-- 1. Left: Branding & Multi-Venue Badges -->
      <div class="flex items-center space-x-2 sm:space-x-3 shrink-0">
        <!-- Mobile Drawer Trigger Button -->
        <button
          @click="mobileDrawerOpen = !mobileDrawerOpen"
          class="md:hidden w-7.5 h-7.5 rounded-md border flex items-center justify-center cursor-pointer transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
          aria-label="Toggle Navigation Drawer"
        >
          <Menu class="w-4 h-4 text-indigo-400" />
        </button>

        <!-- Brand Identity -->
        <div class="flex items-center space-x-1.5 sm:space-x-2 cursor-pointer" @click="store.activeTab = 'trading'">
          <div
            class="w-6 h-6 rounded flex items-center justify-center font-mono font-black text-xs border tracking-tighter"
            style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); color: #f8fafc; border-color: #334155;"
          >
            Ω
          </div>
          <div class="flex items-center space-x-1.5">
            <span class="font-mono font-black text-xs tracking-wider uppercase text-slate-100">
              R20 QUANTUM
            </span>
            <span class="hidden sm:inline-block text-[9px] font-mono px-1 py-0.2 rounded border border-slate-700 bg-slate-800 text-slate-300 font-semibold">
              {{ t.modeDesktop }}
            </span>
          </div>
        </div>

        <!-- Venue Status Indicators (OKX / BINANCE / GATE) -->
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

      <!-- 2. Center: Modular Tab Docking Station (Desktop) -->
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

      <!-- 3. Right: Language Switcher, Controls & Clocks -->
      <div class="flex items-center space-x-1.5 sm:space-x-2 text-xs font-mono">
        <!-- Dual Clocks: UTC & SGT (Bloomberg style, Ultra-wide only) -->
        <div
          class="hidden 2xl:flex items-center h-7 space-x-2 px-2.5 rounded border text-[11px]"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <span>UTC <strong class="text-slate-200 num-tabular">{{ currentTimeUtc }}</strong></span>
          <span class="text-slate-600">|</span>
          <span>BJT <strong class="text-emerald-400 num-tabular">{{ currentTimeLocal }}</strong></span>
        </div>

        <!-- 🌐 Global Language Switch Capsule (ZH / EN) -->
        <button
          @click="toggleLocale"
          class="h-7 px-2 sm:px-2.5 rounded border flex items-center space-x-1 sm:space-x-1.5 cursor-pointer transition-all hover:bg-[var(--bg-card-hover)]"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
          :title="locale === 'zh' ? 'Switch to English' : '切换至中文'"
        >
          <Globe class="w-3.5 h-3.5 text-indigo-400 shrink-0" />
          <span class="font-bold text-[11px]">{{ locale === 'zh' ? '中 / EN' : 'EN / 中' }}</span>
        </button>

        <!-- Fail-Closed Sentinel Shield -->
        <div
          class="hidden lg:flex items-center h-7 px-2 rounded border text-[10px] font-mono font-bold text-emerald-400 border-emerald-500/30 bg-emerald-500/10"
          title="三位一体物理拦截管线：FAIL-CLOSED 模式已生效"
        >
          <ShieldAlert class="w-3 h-3 mr-1 text-emerald-400" />
          FAIL-CLOSED
        </div>

        <!-- Admin Console Link -->
        <a
          href="/admin/"
          class="h-7 px-2 sm:px-2.5 rounded border flex items-center space-x-1.5 cursor-pointer hover:bg-[var(--bg-card-hover)] transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
          title="进入管理员风控治理控制台"
        >
          <Settings class="w-3.5 h-3.5 text-indigo-400" />
          <span class="hidden sm:inline font-bold">{{ t.admin }}</span>
        </a>

        <!-- Theme Toggle Button -->
        <button
          @click="toggleTheme"
          class="w-7 h-7 rounded border flex items-center justify-center cursor-pointer hover:bg-[var(--bg-card-hover)] transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          :title="theme === 'dark' ? t.themeLight : t.themeDark"
        >
          <Sun v-if="theme === 'dark'" class="w-3.5 h-3.5" />
          <Moon v-else class="w-3.5 h-3.5" />
        </button>
      </div>
    </div>

    <!-- Mobile Drawer Overlay & Sliding Side Panel -->
    <div
      v-if="mobileDrawerOpen"
      class="md:hidden fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex"
      @click.self="mobileDrawerOpen = false"
    >
      <div
        class="w-[280px] max-w-[85vw] h-full flex flex-col justify-between border-r shadow-2xl p-4 transition-transform font-mono"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="space-y-4">
          <!-- Drawer Header -->
          <div class="flex items-center justify-between pb-3 border-b" style="border-color: var(--border-subtle);">
            <div class="flex items-center space-x-2">
              <div
                class="w-6 h-6 rounded flex items-center justify-center font-bold text-xs border border-slate-700 bg-slate-800 text-slate-100"
              >
                Ω
              </div>
              <span class="font-bold text-xs text-slate-100">R20 全功能工作台</span>
            </div>
            <button @click="mobileDrawerOpen = false" class="p-1 rounded text-slate-400 hover:text-slate-100">
              <X class="w-4 h-4" />
            </button>
          </div>

          <!-- All Core Workstation Views -->
          <div class="space-y-1">
            <div class="text-[10px] text-slate-400 uppercase tracking-wider font-bold px-2 py-1">
              核心操盘视窗
            </div>
            <button
              v-for="tab in tabs"
              :key="tab.id"
              @click="switchTabMobile(tab.id)"
              class="w-full flex items-center justify-between px-3 py-2.5 rounded-lg text-xs font-bold transition-all"
              :style="store.activeTab === tab.id
                ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-app)' }
                : { color: 'var(--text-muted)' }"
            >
              <div class="flex items-center space-x-2.5">
                <component :is="tab.icon" class="w-4 h-4" />
                <span>{{ tab.label }}</span>
              </div>
              <ChevronRight class="w-3.5 h-3.5 opacity-50" />
            </button>
          </div>

          <!-- Quick Actions & Governance -->
          <div class="space-y-1 pt-2 border-t" style="border-color: var(--border-subtle);">
            <div class="text-[10px] text-slate-400 uppercase tracking-wider font-bold px-2 py-1">
              管理与设置
            </div>
            <a
              href="/admin/"
              class="w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs text-slate-300 hover:bg-[#1f222d] transition-colors"
            >
              <div class="flex items-center space-x-2.5">
                <Settings class="w-4 h-4 text-indigo-400" />
                <span>{{ t.admin }}</span>
              </div>
              <ChevronRight class="w-3.5 h-3.5 opacity-50" />
            </a>
            <a
              href="/docs"
              class="w-full flex items-center justify-between px-3 py-2 rounded-lg text-xs text-slate-300 hover:bg-[#1f222d] transition-colors"
            >
              <div class="flex items-center space-x-2.5">
                <BookOpen class="w-4 h-4 text-emerald-400" />
                <span>{{ t.docs }}</span>
              </div>
              <ChevronRight class="w-3.5 h-3.5 opacity-50" />
            </a>
          </div>
        </div>

        <!-- Drawer Footer: System Status -->
        <div class="pt-3 border-t text-[11px] space-y-2" style="border-color: var(--border-subtle); color: var(--text-faint);">
          <div class="flex items-center justify-between">
            <span>语言 / Language:</span>
            <button
              @click="toggleLocale"
              class="px-2 py-0.5 rounded border border-slate-700 bg-slate-800 text-slate-200 font-bold"
            >
              {{ locale === 'zh' ? '中文 (ZH)' : 'English (EN)' }}
            </button>
          </div>
          <div class="flex items-center justify-between">
            <span>风控防线:</span>
            <span class="text-emerald-400 font-bold">FAIL-CLOSED</span>
          </div>
        </div>
      </div>
    </div>
  </header>
</template>

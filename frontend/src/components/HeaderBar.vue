<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useTheme } from '../composables/useTheme'
import { useI18n } from '../composables/useI18n'
import AboutModal from './AboutModal.vue'
import {
  LayoutGrid,
  Cpu,
  Newspaper,
  Sparkles,
  Receipt,
  ShieldCheck,
  ExternalLink,
  BookOpen,
  Sun,
  Moon,
  Clock,
  Globe,
} from 'lucide-vue-next'

const store = useDashboardStore()
const { theme, toggleTheme } = useTheme()
const { t, isEn, toggleLocale } = useI18n()

const currentTime = ref('')
let timer: any = null

function updateClock() {
  const d = new Date()
  currentTime.value = d.toLocaleTimeString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' })
}

onMounted(() => {
  updateClock()
  timer = setInterval(updateClock, 1000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

const tabs = computed(() => [
  { id: 'trading', label: t('nav.tabMatrix'), icon: LayoutGrid },
  { id: 'factors', label: t('nav.tabRadar'), icon: Cpu },
  { id: 'news', label: t('nav.tabNews'), icon: Newspaper },
  { id: 'lab', label: t('nav.tabLab'), icon: Sparkles },
  { id: 'history', label: t('nav.tabLedger'), icon: Receipt },
])
</script>

<template>
  <header
    class="fixed top-0 left-0 right-0 z-40 h-[46px] sm:h-[50px] flex items-center border-b transition-colors"
    style="background-color: var(--bg-header); border-color: var(--border-subtle); backdrop-filter: blur(12px);"
  >
    <div class="max-w-[2048px] w-full mx-auto px-2.5 sm:px-6 2xl:px-8 flex items-center justify-between gap-1.5 sm:gap-4 overflow-hidden">
      <!-- Left: Minimal Institutional Identity with subtle crypto currency icon -->
      <div class="flex items-center space-x-2 shrink-0">
        <div class="flex items-center space-x-1.5 cursor-pointer select-none" @click="store.activeTab = 'trading'">
          <div
            class="w-5 h-5 rounded flex items-center justify-center font-mono font-black text-xs border"
            style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);"
            title="Crypto Native Quant"
          >
            ₿
          </div>
          <span class="font-mono font-black text-xs sm:text-sm tracking-wide whitespace-nowrap" style="color: var(--text-main);">
            {{ t('nav.title') }}
          </span>
        </div>
        <button
          @click="store.showAboutModal = true"
          class="px-1.5 py-0.2 rounded text-[10px] font-mono font-bold border transition-colors cursor-pointer"
          style="background-color: var(--bg-card-subtle); color: var(--text-muted); border-color: var(--border-subtle);"
          :title="t('nav.aboutTitle')"
        >
          v7.4.2
        </button>
        <span
          class="w-1.5 h-1.5 rounded-full shrink-0"
          :class="store.isConnected ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'"
          title="OKX V5 PROD"
        ></span>
        <span
          v-if="store.isStale"
          class="px-1.5 py-0.2 rounded text-[9px] font-mono font-bold border animate-pulse"
          style="background-color: var(--color-warn-bg); color: var(--color-warn); border-color: var(--color-warn-border);"
        >
          {{ t('nav.stale') }}
        </span>
      </div>

      <!-- Center: High-Precision Tab Switcher (Visible on large screens xl:flex, to avoid bursting 1080p/laptop screens) -->
      <nav
        class="hidden xl:flex items-center p-0.5 rounded-xl border shrink-0 transition-colors"
        style="background-color: var(--bg-badge); border-color: var(--border-subtle);"
      >
        <button
          v-for="tab in tabs"
          :key="tab.id"
          @click="store.activeTab = tab.id as any"
          class="h-7 flex items-center space-x-1.5 px-2.5 2xl:px-3 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer whitespace-nowrap"
          :style="store.activeTab === tab.id
            ? { backgroundColor: 'var(--bg-card)', color: 'var(--text-main)', borderColor: 'var(--border-medium)', boxShadow: 'var(--shadow-card)' }
            : { color: 'var(--text-muted)' }"
          :class="store.activeTab === tab.id ? 'border' : 'hover:text-[var(--text-main)]'"
        >
          <component :is="tab.icon" class="w-3.5 h-3.5" />
          <span>{{ tab.label }}</span>
        </button>
      </nav>

      <!-- Right: Mobile-Compact Essential Tools -->
      <div class="flex items-center space-x-1 sm:space-x-1.5 2xl:space-x-2 shrink-0 text-xs font-mono">
        <!-- Realtime Clock (Large screens only) -->
        <div
          class="hidden 2xl:flex items-center h-7 space-x-1.5 px-2 rounded-lg border text-[11px]"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          :title="t('nav.utcClock')"
        >
          <Clock class="w-3 h-3" style="color: var(--text-faint);" />
          <span class="num-tabular font-medium">{{ currentTime }}</span>
          <span class="text-[9px] font-bold opacity-60">UTC+8</span>
        </div>

        <!-- 🌐 Global Language Switcher Capsule -->
        <button
          @click="toggleLocale"
          class="flex items-center h-7 sm:h-7.5 space-x-1 px-1.5 sm:px-2 rounded-lg border transition-all cursor-pointer shadow-xs shrink-0 font-bold select-none"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
          :title="t('nav.switchLang')"
        >
          <Globe class="w-3 h-3 sm:w-3.5 sm:h-3.5 text-indigo-400" />
          <span class="text-[10px] sm:text-[11px] uppercase tracking-wider">{{ isEn ? 'EN' : '中' }}</span>
        </button>

        <!-- ☀️ / 🌙 Theme Toggle Button -->
        <button
          @click="toggleTheme"
          class="flex items-center justify-center w-7 h-7 sm:w-7.5 sm:h-7.5 rounded-lg border transition-all cursor-pointer shadow-xs shrink-0"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
          :title="t('nav.switchTheme')"
        >
          <Sun v-if="theme === 'dark'" class="w-3.5 h-3.5 text-amber-400 hover:rotate-45 transition-transform" />
          <Moon v-else class="w-3.5 h-3.5 text-slate-700 hover:-rotate-12 transition-transform" />
        </button>

        <!-- Documentation Link -->
        <a
          href="/docs"
          class="hidden sm:flex items-center h-7 sm:h-7.5 space-x-1 px-2 rounded-lg border transition-colors cursor-pointer shadow-xs"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          :title="t('nav.docs')"
        >
          <BookOpen class="w-3.5 h-3.5" />
          <span class="hidden md:inline font-medium">{{ t('nav.docs') }}</span>
        </a>

        <!-- Control Plane Button -->
        <a
          href="/admin"
          target="_blank"
          class="flex items-center h-7 sm:h-7.5 space-x-1 px-2 sm:px-2.5 rounded-lg border transition-all font-medium cursor-pointer shadow-xs"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);"
        >
          <ShieldCheck class="w-3.5 h-3.5" />
          <span class="text-[11px] sm:text-xs">{{ t('nav.controlPlane') }}</span>
          <ExternalLink class="w-3 h-3 opacity-60 hidden lg:inline" />
        </a>
      </div>
    </div>

    <!-- About Modal -->
    <AboutModal
      :visible="store.showAboutModal"
      @close="store.showAboutModal = false"
    />
  </header>
</template>

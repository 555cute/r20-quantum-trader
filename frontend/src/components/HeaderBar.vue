<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useTheme } from '../composables/useTheme'
import { useI18n } from '../composables/useI18n'
import { APP_VERSION, APP_NAME, OFFICIAL_NOTICE } from '../config/version'
import AboutModal from './AboutModal.vue'
import CryptoLogo from './CryptoLogo.vue'
import {
  LayoutGrid,
  Cpu,
  Newspaper,
  Sparkles,
  Receipt,
  Settings,
  ExternalLink,
  BookOpen,
  Sun,
  Moon,
  Clock,
} from 'lucide-vue-next'

const store = useDashboardStore()
const { theme, toggleTheme, setTheme, cvd, toggleCvd } = useTheme()
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

const settingsOpen = ref(false)
const settingsBtn = ref<HTMLElement | null>(null)
const settingsPanel = ref<HTMLElement | null>(null)

function onDocClick(e: MouseEvent) {
  if (!settingsOpen.value) return
  const t = e.target as Node
  if (settingsBtn.value?.contains(t) || settingsPanel.value?.contains(t)) return
  settingsOpen.value = false
}
onMounted(() => document.addEventListener('click', onDocClick, true))
onUnmounted(() => document.removeEventListener('click', onDocClick, true))


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
      <!-- Left: Minimal Institutional Identity with Bitcoin Logo -->
      <div class="flex items-center space-x-2 shrink-0">
        <div class="flex items-center space-x-2 cursor-pointer select-none" @click="store.activeTab = 'trading'">
          <div class="w-6 h-6 rounded-md flex items-center justify-center border font-mono font-black text-sm text-[#F7931A] shrink-0 shadow-xs" style="background-color: rgba(247, 147, 26, 0.12); border-color: rgba(247, 147, 26, 0.35);">
            ₿
          </div>
          <span class="font-mono font-black text-xs sm:text-sm tracking-wide whitespace-nowrap" style="color: var(--text-main);">
            {{ APP_NAME }}
          </span>
        </div>
        <button
          @click="store.showAboutModal = true"
          class="px-1.5 py-0.2 rounded text-[11px] font-mono font-bold border transition-colors cursor-pointer whitespace-nowrap"
          style="background-color: var(--bg-card-subtle); color: var(--text-muted); border-color: var(--border-subtle);"
          :title="OFFICIAL_NOTICE"
        >
          {{ APP_VERSION }} · Official
        </button>
        <span
          class="w-1.5 h-1.5 rounded-full shrink-0"
          :class="store.isConnected ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'"
          title="OKX V5 PROD"
        ></span>
        <span
          v-if="store.isStale"
          class="px-1.5 py-0.2 rounded text-[11px] font-mono font-bold border animate-pulse"
          style="background-color: var(--color-warn-bg); color: var(--color-warn); border-color: var(--color-warn-border);"
        >
          {{ t('nav.stale') }}
        </span>
      </div>

      <!-- Center: High-Precision Tab Switcher (Visible on desktop/laptop md:flex) -->
      <nav
        class="hidden md:flex items-center p-0.5 rounded-xl border shrink-0 transition-colors"
        style="background-color: var(--bg-badge); border-color: var(--border-subtle);"
      >
        <button
          v-for="tab in tabs"
          :key="tab.id"
          @click="store.activeTab = tab.id as any"
          class="h-7 flex items-center space-x-1 sm:space-x-1.5 px-2 lg:px-2.5 2xl:px-3 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer whitespace-nowrap"
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
          <span class="text-[11px] font-bold opacity-60">UTC+8</span>
        </div>

        <!-- P1: single settings popover (lang / theme / CVD / console / docs) -->
        <div class="relative shrink-0">
          <button
            ref="settingsBtn"
            @click="settingsOpen = !settingsOpen"
            class="flex items-center justify-center w-7 h-7 sm:w-7.5 sm:h-7.5 rounded-lg border transition-all cursor-pointer shadow-xs group"
            :style="settingsOpen
              ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--color-brand)' }
              : { backgroundColor: 'var(--bg-card)', borderColor: 'var(--border-subtle)', color: 'var(--text-main)' }"
            :title="t('nav.settings')"
          >
            <Settings class="w-3.5 h-3.5 transition-transform duration-300" :class="settingsOpen ? 'rotate-90' : ''" />
          </button>

          <Teleport to="body">
          <div
            v-if="settingsOpen"
            ref="settingsPanel"
            class="fixed top-[54px] right-3 z-50 w-52 rounded-xl border p-1.5 shadow-lg"
            style="background-color: var(--bg-elevated); border-color: var(--border-medium);"
          >
            <!-- Language -->
            <div class="px-2 pt-1.5 pb-1 text-[11px] font-mono font-bold uppercase tracking-wider" style="color: var(--text-faint);">{{ t('nav.language') }}</div>
            <div class="flex px-1 pb-1.5 space-x-1">
              <button @click="isEn && toggleLocale()" class="flex-1 h-7 rounded-md text-[11px] font-mono font-bold border cursor-pointer transition-all"
                :style="!isEn ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--color-brand)' } : { borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }">中文</button>
              <button @click="!isEn && toggleLocale()" class="flex-1 h-7 rounded-md text-[11px] font-mono font-bold border cursor-pointer transition-all"
                :style="isEn ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--color-brand)' } : { borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }">EN</button>
            </div>
            <div class="h-px my-1" style="background-color: var(--border-subtle);"></div>
            <!-- Theme -->
            <div class="px-2 pt-1 pb-1 text-[11px] font-mono font-bold uppercase tracking-wider" style="color: var(--text-faint);">{{ t('nav.theme') }}</div>
            <div class="flex px-1 pb-1.5 space-x-1">
              <button @click="setTheme('dark')" class="flex-1 h-7 rounded-md text-[11px] font-mono font-bold border cursor-pointer transition-all flex items-center justify-center space-x-1"
                :style="theme === 'dark' ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--color-brand)' } : { borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"><Moon class="w-3 h-3" /><span>{{ t('nav.themeDark') }}</span></button>
              <button @click="setTheme('light')" class="flex-1 h-7 rounded-md text-[11px] font-mono font-bold border cursor-pointer transition-all flex items-center justify-center space-x-1"
                :style="theme === 'light' ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--color-brand)' } : { borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"><Sun class="w-3 h-3" /><span>{{ t('nav.themeLight') }}</span></button>
            </div>
            <div class="h-px my-1" style="background-color: var(--border-subtle);"></div>
            <!-- CVD -->
            <button @click="toggleCvd()" class="w-full flex items-center justify-between px-2 py-1.5 rounded-md text-[11px] font-mono cursor-pointer hover:bg-[var(--bg-card-hover)] transition-colors" style="color: var(--text-main);" :title="t('nav.cvdHint')">
              <span>{{ t('nav.cvd') }}</span>
              <span class="w-8 h-4 rounded-full border relative transition-colors shrink-0" :style="cvd ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)' } : { backgroundColor: 'var(--bg-badge)', borderColor: 'var(--border-subtle)' }">
                <span class="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full transition-all" :style="cvd ? { left: '17px', backgroundColor: 'var(--color-brand)' } : { left: '2px', backgroundColor: 'var(--text-faint)' }"></span>
              </span>
            </button>
            <div class="h-px my-1" style="background-color: var(--border-subtle);"></div>
            <!-- Links -->
            <a href="/admin" target="_blank" class="flex items-center space-x-2 px-2 py-1.5 rounded-md text-xs font-mono cursor-pointer hover:bg-[var(--bg-card-hover)] transition-colors" style="color: var(--text-main);">
              <Settings class="w-3.5 h-3.5" style="color: var(--text-muted);" /><span>{{ t('nav.controlPlane') }}</span><ExternalLink class="w-3 h-3 ml-auto" style="color: var(--text-faint);" />
            </a>
            <a href="/docs" class="flex items-center space-x-2 px-2 py-1.5 rounded-md text-xs font-mono cursor-pointer hover:bg-[var(--bg-card-hover)] transition-colors" style="color: var(--text-main);">
              <BookOpen class="w-3.5 h-3.5" style="color: var(--text-muted);" /><span>{{ t('nav.docs') }}</span>
            </a>
          </div>
          </Teleport>
        </div>
      </div>
    </div>

    <!-- About Modal -->
    <AboutModal
      :visible="store.showAboutModal"
      @close="store.showAboutModal = false"
    />
  </header>
</template>

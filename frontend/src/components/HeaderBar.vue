<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useTheme } from '../composables/useTheme'
import { useI18nStore } from '../stores/i18n'
import {
  Settings,
  Sun,
  Moon,
  ShieldAlert,
  Globe,
} from 'lucide-vue-next'

const store = useDashboardStore()
const { theme, toggleTheme } = useTheme()
const i18n = useI18nStore()

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
</script>

<template>
  <header
    class="fixed top-0 left-0 right-0 z-40 h-[46px] px-3 sm:px-4 flex items-center border-b select-none transition-colors"
    style="background-color: var(--bg-header); border-color: var(--border-subtle); backdrop-filter: blur(16px);"
  >
    <div class="w-full flex items-center justify-between gap-3">
      <!-- Left: 纯粹品牌标识与交易所状态指示 -->
      <div class="flex items-center space-x-2.5 shrink-0">
        <div class="flex items-center space-x-2 cursor-pointer" @click="store.activeTab = 'trading'">
          <div
            class="w-6 h-6 rounded flex items-center justify-center font-mono font-black text-xs border tracking-tighter shadow-xs"
            style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); color: #f8fafc; border-color: #334155;"
          >
            Ω
          </div>
          <div class="flex items-center space-x-1.5 font-mono">
            <span class="font-black text-xs tracking-wider uppercase text-slate-100">
              {{ i18n.t.appName }}
            </span>
            <span class="hidden sm:inline-block text-[9px] px-1 py-0.2 rounded border border-slate-700 bg-slate-800 text-slate-400 font-semibold">
              {{ i18n.t.appMode }}
            </span>
          </div>
        </div>

        <!-- Venue Pulse Status (Desktop Only) -->
        <div class="hidden xl:flex items-center space-x-1.5 pl-2 border-l" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-1 px-1.5 py-0.5 rounded border text-[10px] font-mono" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
            <span class="text-slate-300 font-bold">OKX</span>
          </div>
          <div class="flex items-center space-x-1 px-1.5 py-0.5 rounded border text-[10px] font-mono" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span>
            <span class="text-slate-300 font-bold">BINANCE</span>
          </div>
          <div class="flex items-center space-x-1 px-1.5 py-0.5 rounded border text-[10px] font-mono" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <span class="w-1.5 h-1.5 rounded-full bg-blue-400"></span>
            <span class="text-slate-300 font-bold">GATE</span>
          </div>
        </div>
      </div>

      <!-- Right: 全局功能控制区（双时钟、语言、硬防线、唯一后台入口、亮暗主题） -->
      <div class="flex items-center space-x-1.5 sm:space-x-2 text-xs font-mono">
        <!-- Dual Clocks: UTC & SGT (Bloomberg style, Ultra-wide only) -->
        <div
          class="hidden 2xl:flex items-center h-7 space-x-1.5 px-2 rounded border text-[10px]"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <span>UTC <strong class="text-slate-200 num-tabular">{{ currentTimeUtc }}</strong></span>
          <span class="text-slate-600">|</span>
          <span>BJT <strong class="text-emerald-400 num-tabular">{{ currentTimeLocal }}</strong></span>
        </div>

        <!-- 🌐 语言切换 (纯正双语) -->
        <button
          @click="i18n.toggleLocale"
          class="h-7 px-2.5 rounded border flex items-center space-x-1.5 cursor-pointer transition-all hover:bg-[var(--bg-card-hover)]"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
          :title="i18n.t.switchLang"
        >
          <Globe class="w-3.5 h-3.5 text-indigo-400 shrink-0" />
          <span class="font-bold text-[11px]">{{ i18n.locale === 'zh' ? '中 / EN' : 'EN / 中' }}</span>
        </button>

        <!-- 🛡️ Fail-Closed 熔断硬防线状态徽标 -->
        <div
          class="hidden lg:flex items-center h-7 px-2.5 rounded border text-[10px] font-mono font-bold text-emerald-400 border-emerald-500/30 bg-emerald-500/10"
          :title="i18n.t.hardDefense"
        >
          <ShieldAlert class="w-3 h-3 mr-1 text-emerald-400" />
          <span>{{ i18n.t.hardDefense }}</span>
        </div>

        <!-- ⚙️ 全站唯一管理控制台入口 (桌面端显示文字，移动端为小巧图标) -->
        <a
          href="/admin/"
          class="h-7 px-2 sm:px-2.5 rounded border flex items-center space-x-1.5 cursor-pointer hover:bg-[var(--bg-card-hover)] transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
          :title="i18n.t.adminConsole"
        >
          <Settings class="w-3.5 h-3.5 text-indigo-400" />
          <span class="hidden sm:inline font-bold">{{ i18n.t.adminConsole }}</span>
        </a>

        <!-- ☀️/🌙 亮暗主题切换 -->
        <button
          @click="toggleTheme"
          class="w-7 h-7 rounded border flex items-center justify-center cursor-pointer hover:bg-[var(--bg-card-hover)] transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          :title="theme === 'dark' ? i18n.t.themeLight : i18n.t.themeDark"
        >
          <Sun v-if="theme === 'dark'" class="w-3.5 h-3.5 text-amber-400 hover:rotate-45 transition-transform" />
          <Moon v-else class="w-3.5 h-3.5 text-slate-700 hover:-rotate-12 transition-transform" />
        </button>
      </div>
    </div>
  </header>
</template>

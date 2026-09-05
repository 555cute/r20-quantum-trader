<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useTheme } from '../composables/useTheme'
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
} from 'lucide-vue-next'

const store = useDashboardStore()
const { theme, toggleTheme } = useTheme()

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

const tabs = [
  { id: 'trading', label: '实盘矩阵', icon: LayoutGrid },
  { id: 'factors', label: 'AI全景推演', icon: Cpu },
  { id: 'news', label: '全网舆情', icon: Newspaper },
  { id: 'lab', label: 'AI自进化', icon: Sparkles },
  { id: 'history', label: '交易台账', icon: Receipt },
] as const
</script>

<template>
  <header
    class="fixed top-0 left-0 right-0 z-40 h-[46px] sm:h-[50px] px-3 sm:px-5 flex items-center border-b transition-colors"
    style="background-color: var(--bg-header); border-color: var(--border-subtle); backdrop-filter: blur(12px);"
  >
    <div class="max-w-[2160px] w-full mx-auto flex items-center justify-between gap-2.5 sm:gap-4">
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
            R20 QUANTUM
          </span>
        </div>
        <button
          @click="store.showAboutModal = true"
          class="px-1.5 py-0.2 rounded text-[10px] font-mono font-bold border transition-colors cursor-pointer"
          style="background-color: var(--bg-card-subtle); color: var(--text-muted); border-color: var(--border-subtle);"
          title="查看系统版本与开源主仓信息"
        >
          v7.4.1
        </button>
        <span
          class="w-1.5 h-1.5 rounded-full shrink-0"
          :class="store.isConnected ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'"
          title="OKX V5 PROD 运行状态"
        ></span>
        <span
          v-if="store.isStale"
          class="px-1.5 py-0.2 rounded text-[9px] font-mono font-bold border animate-pulse"
          style="background-color: var(--color-warn-bg); color: var(--color-warn); border-color: var(--color-warn-border);"
        >
          STALE
        </span>
      </div>

      <!-- Center: High-Precision Tab Switcher (Desktop only) -->
      <nav
        class="hidden md:flex items-center p-0.5 rounded-xl border shrink-0 transition-colors"
        style="background-color: var(--bg-badge); border-color: var(--border-subtle);"
      >
        <button
          v-for="tab in tabs"
          :key="tab.id"
          @click="store.activeTab = tab.id as any"
          class="h-7 flex items-center space-x-1.5 px-3 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer whitespace-nowrap"
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
      <div class="flex items-center space-x-1.5 sm:space-x-2 shrink-0 text-xs font-mono">
        <!-- Realtime Clock (Desktop only) -->
        <div
          class="hidden lg:flex items-center h-7.5 space-x-1.5 px-2.5 rounded-lg border text-[11px]"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          title="北京时间 (UTC+8)"
        >
          <Clock class="w-3.5 h-3.5" style="color: var(--text-faint);" />
          <span class="num-tabular font-medium">{{ currentTime }}</span>
          <span class="text-[9px] font-bold opacity-60">UTC+8</span>
        </div>

        <!-- ☀️ / 🌙 Theme Toggle Button -->
        <button
          @click="toggleTheme"
          class="flex items-center justify-center w-7.5 h-7.5 rounded-lg border transition-all cursor-pointer shadow-xs shrink-0"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
          :title="theme === 'dark' ? '切换为亮色模式' : '切换为暗色模式'"
        >
          <Sun v-if="theme === 'dark'" class="w-3.5 h-3.5 text-amber-400 hover:rotate-45 transition-transform" />
          <Moon v-else class="w-3.5 h-3.5 text-slate-700 hover:-rotate-12 transition-transform" />
        </button>

        <!-- Documentation Link -->
        <a
          href="/docs"
          class="flex items-center h-7.5 space-x-1 px-2.5 rounded-lg border transition-colors cursor-pointer shadow-xs"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          title="系统架构与使用文档"
        >
          <BookOpen class="w-3.5 h-3.5" />
          <span class="hidden sm:inline font-medium">文档</span>
        </a>

        <!-- Control Plane Button -->
        <a
          href="/admin"
          target="_blank"
          class="flex items-center h-7.5 space-x-1 px-2.5 sm:px-3 rounded-lg border transition-all font-medium cursor-pointer shadow-xs"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);"
        >
          <ShieldCheck class="w-3.5 h-3.5" />
          <span>控制面</span>
          <ExternalLink class="w-3 h-3 opacity-60 hidden sm:inline" />
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

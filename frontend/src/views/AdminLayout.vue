<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useTheme } from '../composables/useTheme'
import { useI18nStore } from '../stores/i18n'
import AboutModal from '../components/AboutModal.vue'
import {
  LayoutDashboard,
  Radio,
  FileText,
  Sparkles,
  ShieldCheck,
  Users,
  Layers,
  Cpu,
  Package,
  FileCode,
  Wallet,
  RefreshCw,
  Scroll,
  UserCog,
  Info,
  LogOut,
  ExternalLink,
  BookOpen,
  Sun,
  Moon,
  ChevronRight,
  Menu,
  X,
  Globe,
} from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const { theme, toggleTheme } = useTheme()
const i18n = useI18nStore()

// 精简收敛的四大管理板块
const navGroups = computed(() => [
  {
    label: i18n.t.adminNavGroup1,
    items: [
      { id: 'overview', label: i18n.t.adminOverview, icon: LayoutDashboard },
      { id: 'decisions', label: i18n.t.adminDecisions, icon: Radio },
    ],
  },
  {
    label: i18n.t.adminNavGroup2,
    items: [
      { id: 'promptlib', label: i18n.t.adminPrompt, icon: FileText },
      { id: 'llm', label: i18n.t.adminLlm, icon: Cpu },
      { id: 'council', label: i18n.t.adminCouncil, icon: Users },
      { id: 'evolution', label: i18n.t.adminEvolution, icon: Sparkles },
    ],
  },
  {
    label: i18n.t.adminNavGroup3,
    items: [
      { id: 'interceptors', label: i18n.t.adminInterceptors, icon: ShieldCheck },
      { id: 'security', label: i18n.t.adminVenues, icon: Wallet },
      { id: 'gateway', label: i18n.t.adminGateway, icon: RefreshCw },
      { id: 'policy', label: i18n.t.adminPolicy, icon: Layers },
    ],
  },
  {
    label: i18n.t.adminNavGroup4,
    items: [
      { id: 'notify', label: i18n.t.adminNotify, icon: Radio },
      { id: 'backup', label: i18n.t.adminBackup, icon: FileCode },
      { id: 'audit', label: i18n.t.adminAudit, icon: Scroll },
      { id: 'adminsys', label: i18n.t.adminAuth, icon: UserCog },
      { id: 'about', label: i18n.t.adminAbout, icon: Info },
    ],
  },
])

const mobileDrawerOpen = ref(false)
const isNavigating = ref(false)
const pendingView = ref<string | null>(null)
const showAboutModal = ref(false)

const currentView = computed<string>(() => {
  const seg = route.path.split('/').filter(Boolean).pop() || 'overview'
  return seg
})

const activeView = computed<string>(() => {
  return pendingView.value || currentView.value
})

const currentGroupName = computed<string>(() => {
  for (const group of navGroups.value) {
    const hit = group.items.find((i) => i.id === activeView.value)
    if (hit) return group.label
  }
  return i18n.locale === 'zh' ? '管理控制' : 'Control'
})

const currentLabel = computed<string>(() => {
  for (const group of navGroups.value) {
    const hit = group.items.find((i) => i.id === activeView.value)
    if (hit) return hit.label
  }
  return activeView.value
})

function navigateTo(id: string) {
  if (currentView.value === id) {
    mobileDrawerOpen.value = false
    return
  }
  pendingView.value = id
  isNavigating.value = true
  router.push(`/admin/${id}`).catch(() => {}).finally(() => {
    pendingView.value = null
    isNavigating.value = false
    mobileDrawerOpen.value = false
  })
}

function handleLogout() {
  auth.logout()
  router.push('/admin/login')
}
</script>

<template>
  <div
    class="min-h-screen flex transition-colors selection:bg-indigo-500/30 font-mono select-none"
    style="background-color: var(--bg-app); color: var(--text-main);"
  >
    <!-- Mobile Drawer Backdrop -->
    <div
      v-if="mobileDrawerOpen"
      class="fixed inset-0 z-40 bg-black/60 backdrop-blur-xs md:hidden transition-opacity"
      @click="mobileDrawerOpen = false"
    ></div>

    <!-- Mobile Slide Drawer -->
    <aside
      class="fixed inset-y-0 left-0 w-[280px] z-50 md:hidden flex flex-col transition-transform duration-250 ease-out shadow-2xl border-r"
      :class="mobileDrawerOpen ? 'translate-x-0' : '-translate-x-full'"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="px-4 py-3.5 border-b flex items-center justify-between" style="border-color: var(--border-subtle);">
        <div class="flex items-center space-x-2.5">
          <div
            class="w-7 h-7 rounded flex items-center justify-center font-mono font-black text-xs border"
            style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); color: #f8fafc; border-color: #334155;"
          >
            Ω
          </div>
          <div>
            <div class="text-xs font-black tracking-wide font-mono text-slate-100">{{ i18n.t.adminConsole }}</div>
            <span class="text-[10px] font-mono text-indigo-400 font-bold">v7.4.1 PROD</span>
          </div>
        </div>
        <button
          @click="mobileDrawerOpen = false"
          class="w-8 h-8 rounded-lg flex items-center justify-center border cursor-pointer text-slate-400"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
        >
          <X class="w-4 h-4" />
        </button>
      </div>

      <!-- Drawer Nav Items -->
      <nav class="flex-1 overflow-y-auto p-3 space-y-3">
        <div v-for="group in navGroups" :key="group.label">
          <div class="text-[10px] font-mono font-bold uppercase tracking-wider px-2 py-1 mb-1 text-slate-500">
            {{ group.label }}
          </div>
          <div class="space-y-1">
            <button
              v-for="item in group.items"
              :key="item.id"
              @click="navigateTo(item.id)"
              class="w-full text-left px-3 py-2 rounded-lg text-xs font-mono font-medium transition-all flex items-center justify-between cursor-pointer"
              :style="activeView === item.id
                ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-app)' }
                : { color: 'var(--text-muted)' }"
              :class="activeView === item.id ? 'font-bold shadow-xs' : 'hover:text-[var(--text-main)] hover:bg-[var(--bg-card-hover)]'"
            >
              <div class="flex items-center space-x-2.5">
                <component :is="item.icon" class="w-4 h-4 shrink-0" />
                <span>{{ item.label }}</span>
              </div>
              <ChevronRight v-if="activeView === item.id" class="w-3.5 h-3.5 opacity-80" />
            </button>
          </div>
        </div>
      </nav>

      <!-- Drawer Footer User Info & Language -->
      <div class="p-3 border-t flex flex-col space-y-2" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);">
        <div class="flex items-center justify-between">
          <span class="text-xs text-slate-400">{{ i18n.t.switchLang }}:</span>
          <button
            @click="i18n.toggleLocale"
            class="px-2 py-0.5 rounded border border-slate-700 bg-slate-800 text-slate-200 text-xs font-bold cursor-pointer"
          >
            {{ i18n.locale === 'zh' ? 'English' : '中文' }}
          </button>
        </div>
        <div class="flex items-center justify-between pt-1 border-t border-slate-800">
          <span class="text-xs text-slate-300 font-bold">{{ auth.user?.username || 'admin' }}</span>
          <button @click="handleLogout" class="text-xs text-rose-400 flex items-center gap-1 font-bold cursor-pointer">
            <LogOut class="w-3.5 h-3.5" />
            <span>{{ i18n.t.logout }}</span>
          </button>
        </div>
      </div>
    </aside>

    <!-- Desktop Sidebar -->
    <aside
      class="hidden md:flex flex-col w-[210px] lg:w-[230px] shrink-0 border-r transition-colors z-30 select-none shadow-sm"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="h-13 sm:h-14 px-4 flex items-center justify-between border-b" style="border-color: var(--border-subtle);">
        <div class="flex items-center space-x-2.5">
          <div
            class="w-7 h-7 rounded flex items-center justify-center font-mono font-black text-xs border"
            style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); color: #f8fafc; border-color: #334155;"
          >
            Ω
          </div>
          <div>
            <div class="text-xs font-black tracking-wide font-mono text-slate-100">
              {{ i18n.t.adminConsole }}
            </div>
            <span class="text-[10px] font-mono text-indigo-400 font-bold">v7.4.1 PROD</span>
          </div>
        </div>
        <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
      </div>

      <!-- Nav Groups (Desktop) -->
      <nav class="overflow-y-auto overflow-x-hidden flex-1 py-2 px-2 space-y-1">
        <div v-for="group in navGroups" :key="group.label" class="mb-2">
          <div class="text-[9px] font-mono font-bold uppercase tracking-wider px-2 py-1 text-slate-500">
            {{ group.label }}
          </div>
          <div class="space-y-0.5">
            <button
              v-for="item in group.items"
              :key="item.id"
              @click="navigateTo(item.id)"
              class="w-full text-left px-2.5 py-1.5 rounded-lg text-xs font-mono font-medium transition-all flex items-center space-x-2 cursor-pointer"
              :style="activeView === item.id
                ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-app)' }
                : { color: 'var(--text-muted)' }"
              :class="activeView === item.id ? 'font-bold shadow-xs' : 'hover:text-[var(--text-main)] hover:bg-[var(--bg-card-hover)]'"
            >
              <component :is="item.icon" class="w-3.5 h-3.5 shrink-0" />
              <span class="truncate">{{ item.label }}</span>
            </button>
          </div>
        </div>
      </nav>

      <!-- Sidebar Footer User Profile -->
      <div
        class="px-3 py-2.5 border-t flex items-center justify-between text-xs font-mono"
        style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);"
      >
        <div class="truncate">
          <div class="font-bold truncate text-[11px] text-slate-200">{{ auth.user?.username || 'admin' }}</div>
          <div class="text-[9px] capitalize text-slate-500">{{ auth.user?.role || 'superadmin' }}</div>
        </div>
        <button
          @click="handleLogout"
          class="p-1.5 rounded hover:bg-rose-500/10 text-rose-400 transition-colors cursor-pointer"
          :title="i18n.t.logout"
        >
          <LogOut class="w-3.5 h-3.5" />
        </button>
      </div>
    </aside>

    <!-- Main Content Shell -->
    <div class="flex-1 flex flex-col min-w-0">
      <header
        class="h-13 sm:h-14 border-b px-3 sm:px-6 flex items-center justify-between z-20 transition-colors shrink-0"
        style="background-color: var(--bg-header); border-color: var(--border-subtle); backdrop-filter: blur(12px);"
      >
        <div class="flex items-center space-x-2.5 text-xs font-mono">
          <button
            @click="mobileDrawerOpen = !mobileDrawerOpen"
            class="md:hidden flex items-center space-x-1.5 px-2 py-1 rounded-lg border transition-all cursor-pointer shadow-xs"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);"
          >
            <Menu class="w-3.5 h-3.5 text-indigo-400" />
            <span class="text-[11px] font-bold">{{ i18n.t.menu }}</span>
          </button>

          <div class="flex items-center space-x-1.5 sm:space-x-2">
            <span style="color: var(--text-faint);" class="hidden sm:inline">{{ i18n.t.adminConsole }}</span>
            <ChevronRight class="w-3 h-3 hidden sm:inline" style="color: var(--text-faint);" />
            <span style="color: var(--text-muted);" class="hidden sm:inline">{{ currentGroupName }}</span>
            <ChevronRight class="w-3 h-3 hidden sm:inline" style="color: var(--text-faint);" />
            <h2 class="text-xs sm:text-sm font-black font-mono uppercase tracking-wide flex items-center space-x-1.5" style="color: var(--text-main);">
              <span>{{ currentLabel }}</span>
              <span v-if="isNavigating" class="inline-block w-1.5 h-1.5 rounded-full bg-indigo-500 animate-ping"></span>
            </h2>
          </div>
        </div>

        <div class="flex items-center space-x-1.5 sm:space-x-2 text-xs font-mono">
          <!-- 🌐 Global Language Switch Capsule in Admin -->
          <button
            @click="i18n.toggleLocale"
            class="h-7 px-2.5 rounded border flex items-center space-x-1 cursor-pointer transition-all hover:bg-[var(--bg-card-hover)]"
            style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
            :title="i18n.t.switchLang"
          >
            <Globe class="w-3.5 h-3.5 text-indigo-400 shrink-0" />
            <span class="font-bold text-[11px]">{{ i18n.locale === 'zh' ? '中 / EN' : 'EN / 中' }}</span>
          </button>

          <!-- ☀️ / 🌙 Theme Toggle Button -->
          <button
            @click="toggleTheme"
            class="flex items-center justify-center w-7 h-7 rounded-lg border transition-all cursor-pointer shadow-xs hover:bg-[var(--bg-card-hover)]"
            style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
            :title="theme === 'dark' ? i18n.t.themeLight : i18n.t.themeDark"
          >
            <Sun v-if="theme === 'dark'" class="w-3.5 h-3.5 text-amber-400 hover:rotate-45 transition-transform" />
            <Moon v-else class="w-3.5 h-3.5 text-slate-700 hover:-rotate-12 transition-transform" />
          </button>

          <!-- Back to Terminal -->
          <a
            href="/"
            class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border transition-all cursor-pointer shadow-xs text-xs hover:bg-[var(--bg-card-hover)]"
            style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          >
            <span>{{ i18n.t.tabTrading }}</span>
            <ExternalLink class="w-3 h-3 opacity-60" />
          </a>

          <!-- Docs -->
          <a
            href="/docs"
            class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border transition-all cursor-pointer shadow-xs text-xs hover:bg-[var(--bg-card-hover)]"
            style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          >
            <BookOpen class="w-3 h-3" />
            <span class="hidden sm:inline">{{ i18n.t.docs }}</span>
          </a>

          <!-- Mobile Logout -->
          <button
            @click="handleLogout"
            class="md:hidden flex items-center space-x-1 px-2 py-1 rounded-lg border cursor-pointer"
            style="background-color: var(--color-down-bg); color: var(--color-down); border-color: var(--color-down-border);"
            :title="i18n.t.logout"
          >
            <LogOut class="w-3.5 h-3.5" />
          </button>
        </div>
      </header>

      <!-- Router View Workspace -->
      <main class="flex-1 p-3 sm:p-5 overflow-y-auto max-w-[2160px] w-full mx-auto">
        <router-view />
      </main>
    </div>

    <!-- About Modal -->
    <AboutModal
      :visible="showAboutModal"
      @close="showAboutModal = false"
    />
  </div>
</template>

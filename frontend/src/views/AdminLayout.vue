<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useTheme } from '../composables/useTheme'
import { useI18n } from '../composables/useI18n'
import AboutModal from '../components/AboutModal.vue'
import {
  LayoutDashboard,
  Cpu,
  Layers,
  Radio,
  FileCode,
  Scroll,
  UserCog,
  Info,
  Package,
  FileText,
  Users,
  ShieldCheck,
  RefreshCw,
  LogOut,
  ExternalLink,
  BookOpen,
  Sun,
  Moon,
  ChevronRight,
  Wallet,
  Menu,
  X,
  Sparkles,
  Globe,
} from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const { theme, toggleTheme } = useTheme()
const { locale, t, toggleLocale } = useI18n()

const navGroups = computed(() => [
  {
    label: locale.value === 'zh' ? '系统总览' : 'OVERVIEW',
    items: [
      { id: 'overview', label: locale.value === 'zh' ? '运行总览' : 'System Overview', icon: LayoutDashboard },
      { id: 'decisions', label: locale.value === 'zh' ? '决策日志' : 'Decision Logs', icon: Radio },
    ],
  },
  {
    label: locale.value === 'zh' ? '策略配置' : 'STRATEGY',
    items: [
      { id: 'promptlib', label: locale.value === 'zh' ? '提示词策略' : 'Prompt Studio', icon: FileText },
      { id: 'evolution', label: locale.value === 'zh' ? '自进化认知' : 'Self-Evolution', icon: Sparkles },
      { id: 'interceptors', label: locale.value === 'zh' ? '物理拦截插件' : 'Risk Interceptors', icon: ShieldCheck },
      { id: 'council', label: locale.value === 'zh' ? '多模型委员会' : 'Council Desk', icon: Users },
      { id: 'policy', label: locale.value === 'zh' ? '策略版本快照' : 'Policy Workbench', icon: Layers },
      { id: 'llm', label: locale.value === 'zh' ? '模型连接' : 'LLM Providers', icon: Cpu },
      { id: 'agents', label: locale.value === 'zh' ? '运行单元' : 'Sub-Agents', icon: Package },
      { id: 'plugins', label: locale.value === 'zh' ? '系统插件' : 'Custom Plugins', icon: FileCode },
    ],
  },
  {
    label: locale.value === 'zh' ? '交易与网关' : 'TRADING & GATEWAY',
    items: [
      { id: 'security', label: locale.value === 'zh' ? '交易所与标的池' : 'Venues & Pairs', icon: Wallet },
      { id: 'gateway', label: locale.value === 'zh' ? '任务网关' : 'Task Gateway', icon: RefreshCw },
      { id: 'notify', label: locale.value === 'zh' ? '消息通知' : 'Alert Dispatcher', icon: Radio },
      { id: 'backup', label: locale.value === 'zh' ? '备份与还原' : 'Disaster Recovery', icon: FileCode },
    ],
  },
  {
    label: locale.value === 'zh' ? '系统管理' : 'GOVERNANCE',
    items: [
      { id: 'audit', label: locale.value === 'zh' ? '操作审计' : 'Audit Logs', icon: Scroll },
      { id: 'adminsys', label: locale.value === 'zh' ? '管理员凭证' : 'Credentials', icon: UserCog },
      { id: 'about', label: locale.value === 'zh' ? '版本与信息' : 'About System', icon: Info },
    ],
  },
])

const mobileDrawerOpen = ref(false)
const isNavigating = ref(false)
const pendingView = ref<string | null>(null)
const showAboutModal = ref(false)

// Current view derived from URL
const currentView = computed<string>(() => {
  const seg = route.path.split('/').filter(Boolean).pop() || 'overview'
  return seg
})

// Optimistic view for 0ms visual feedback
const activeView = computed<string>(() => {
  return pendingView.value || currentView.value
})

const currentGroupName = computed<string>(() => {
  for (const group of navGroups.value) {
    const hit = group.items.find((i) => i.id === activeView.value)
    if (hit) return group.label
  }
  return locale.value === 'zh' ? '管理控制' : 'Control'
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

// Pre-fetch all admin chunks in background during idle time to guarantee 0ms transitions
const prefetchViews = () => {
  const loaders = [
    () => import('../views/admin/OverviewPage.vue'),
    () => import('../views/admin/SecurityPage.vue'),
    () => import('../views/admin/CouncilPage.vue'),
    () => import('../views/admin/LlmPage.vue'),
    () => import('../views/admin/NotifyPage.vue'),
    () => import('../views/admin/PromptStudioPage.vue'),
    () => import('../views/admin/EvolutionPage.vue'),
    () => import('../views/admin/InterceptorsPage.vue'),
    () => import('../views/admin/DecisionsPage.vue'),
    () => import('../views/admin/GatewayPage.vue'),
    () => import('../views/admin/PolicySnapshotPage.vue'),
    () => import('../views/admin/PluginsPage.vue'),
    () => import('../views/admin/AgentsPage.vue'),
    () => import('../views/admin/BackupPage.vue'),
    () => import('../views/admin/AuditPage.vue'),
    () => import('../views/admin/AdminSysPage.vue'),
    () => import('../views/admin/AboutPage.vue'),
  ]
  if ('requestIdleCallback' in window) {
    loaders.forEach((loader) => (window as any).requestIdleCallback(() => loader().catch(() => {})))
  } else {
    setTimeout(() => loaders.forEach((loader) => loader().catch(() => {})), 800)
  }
}

onMounted(() => {
  prefetchViews()
})
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

    <!-- Mobile Slide Drawer (Only for Mobile) -->
    <aside
      class="fixed inset-y-0 left-0 w-[280px] z-50 md:hidden flex flex-col transition-transform duration-250 ease-out shadow-2xl border-r"
      :class="mobileDrawerOpen ? 'translate-x-0' : '-translate-x-full'"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <!-- Drawer Header -->
      <div class="px-4 py-3.5 border-b flex items-center justify-between" style="border-color: var(--border-subtle);">
        <div class="flex items-center space-x-2.5">
          <div
            class="w-7 h-7 rounded-md flex items-center justify-center font-mono font-black text-xs border shadow-xs"
            style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); color: #f8fafc; border-color: #334155;"
          >
            Ω
          </div>
          <div>
            <div class="text-xs font-black tracking-wide font-mono text-slate-100">R20 GOVERNANCE</div>
            <button
              @click="showAboutModal = true; mobileDrawerOpen = false"
              class="text-[10px] font-mono transition-colors cursor-pointer text-left block text-indigo-400 font-bold"
            >
              v7.4.1 PROD
            </button>
          </div>
        </div>
        <button
          @click="mobileDrawerOpen = false"
          class="w-8 h-8 rounded-lg flex items-center justify-center border cursor-pointer"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
          title="关闭抽屉"
        >
          <X class="w-4 h-4" />
        </button>
      </div>

      <!-- Drawer Nav Items -->
      <nav class="flex-1 overflow-y-auto p-3 space-y-4">
        <div v-for="group in navGroups" :key="group.label">
          <div
            class="text-[10px] font-mono font-bold uppercase tracking-wider px-2.5 py-1 mb-1 text-slate-500"
          >
            {{ group.label }}
          </div>
          <div class="space-y-1">
            <button
              v-for="item in group.items"
              :key="item.id"
              @click="navigateTo(item.id)"
              class="w-full text-left px-3 py-2.5 rounded-lg text-xs font-mono font-medium transition-all flex items-center justify-between cursor-pointer min-h-[40px] touch-manipulation"
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
      <div class="p-3 border-t flex flex-col space-y-2.5" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);">
        <div class="flex items-center justify-between">
          <span class="text-xs text-slate-400">语言 / Language:</span>
          <button
            @click="toggleLocale"
            class="px-2 py-0.5 rounded border border-slate-700 bg-slate-800 text-slate-200 text-xs font-bold"
          >
            {{ locale === 'zh' ? '中 / EN' : 'EN / 中' }}
          </button>
        </div>
        <div class="flex items-center justify-between pt-1 border-t border-slate-800">
          <div class="flex items-center space-x-2 min-w-0">
            <div class="w-6 h-6 rounded border flex items-center justify-center font-bold text-[10px] bg-slate-800 border-slate-700 text-indigo-400">
              {{ auth.user?.username?.charAt(0).toUpperCase() || 'A' }}
            </div>
            <div class="truncate text-xs text-slate-200 font-bold">
              {{ auth.user?.username || 'admin' }}
            </div>
          </div>
          <button
            @click="handleLogout"
            class="text-xs text-rose-400 flex items-center gap-1 font-bold"
          >
            <LogOut class="w-3.5 h-3.5" />
            <span>{{ t.logout }}</span>
          </button>
        </div>
      </div>
    </aside>

    <!-- Desktop Persistent Sidebar -->
    <aside
      class="hidden md:flex flex-col w-[220px] lg:w-[240px] shrink-0 border-r transition-colors z-30 select-none shadow-sm"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <!-- Sidebar Header -->
      <div
        class="h-13 sm:h-14 px-4 flex items-center justify-between border-b"
        style="border-color: var(--border-subtle);"
      >
        <div class="flex items-center space-x-2.5">
          <div
            class="w-7 h-7 rounded flex items-center justify-center font-mono font-black text-xs border shadow-xs"
            style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); color: #f8fafc; border-color: #334155;"
          >
            Ω
          </div>
          <div>
            <div class="text-xs font-black tracking-wide font-mono text-slate-100">
              R20 GOVERNANCE
            </div>
            <button
              @click="showAboutModal = true"
              class="text-[10px] font-mono transition-colors cursor-pointer text-left block text-indigo-400 font-bold"
              title="点击查看开源主仓信息"
            >
              v7.4.1 PROD
            </button>
          </div>
        </div>

        <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" title="控制面正常在线"></span>
      </div>

      <!-- Nav Groups (Desktop Vertical) -->
      <nav class="overflow-y-auto overflow-x-hidden flex-1 py-2 px-2.5 space-y-1">
        <div v-for="group in navGroups" :key="group.label" class="mb-2.5">
          <div
            class="text-[9px] font-mono font-bold uppercase tracking-wider px-2 py-1 flex items-center justify-between text-slate-500"
          >
            <span>{{ group.label }}</span>
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

      <!-- Sidebar Footer User Profile (Desktop) -->
      <div
        class="px-3 py-2.5 border-t flex items-center justify-between text-xs font-mono"
        style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);"
      >
        <div class="flex items-center space-x-2 min-w-0">
          <div
            class="w-6 h-6 rounded-md border flex items-center justify-center font-bold text-[10px] bg-slate-800 border-slate-700 text-indigo-400"
          >
            {{ auth.user?.username?.charAt(0).toUpperCase() || 'A' }}
          </div>
          <div class="truncate">
            <div class="font-bold truncate text-[11px] text-slate-200">{{ auth.user?.username || 'admin' }}</div>
            <div class="text-[9px] capitalize text-slate-500">{{ auth.user?.role || 'superadmin' }}</div>
          </div>
        </div>
        <button
          @click="handleLogout"
          class="p-1.5 rounded hover:bg-rose-500/10 text-rose-400 transition-colors cursor-pointer"
          :title="t.logout"
        >
          <LogOut class="w-3.5 h-3.5" />
        </button>
      </div>
    </aside>

    <!-- Main Content Shell -->
    <div class="flex-1 flex flex-col min-w-0">
      <!-- Top Title Header Bar (Clean, Unified) -->
      <header
        class="h-13 sm:h-14 border-b px-3 sm:px-6 flex items-center justify-between z-20 transition-colors shrink-0"
        style="background-color: var(--bg-header); border-color: var(--border-subtle); backdrop-filter: blur(12px);"
      >
        <!-- Mobile Drawer Hamburger + Breadcrumbs -->
        <div class="flex items-center space-x-2.5 text-xs font-mono">
          <!-- Hamburger Button for Mobile -->
          <button
            @click="mobileDrawerOpen = !mobileDrawerOpen"
            class="md:hidden flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg border transition-all cursor-pointer shadow-xs active:scale-95 touch-manipulation"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);"
            title="打开导航菜单"
          >
            <Menu class="w-3.5 h-3.5 text-indigo-400" />
            <span class="text-[11px] font-bold">菜单</span>
          </button>

          <div class="flex items-center space-x-1.5 sm:space-x-2">
            <span style="color: var(--text-faint);" class="hidden sm:inline">控制面</span>
            <ChevronRight class="w-3 h-3 hidden sm:inline" style="color: var(--text-faint);" />
            <span style="color: var(--text-muted);" class="hidden sm:inline">{{ currentGroupName }}</span>
            <ChevronRight class="w-3 h-3 hidden sm:inline" style="color: var(--text-faint);" />
            <h2 class="text-xs sm:text-sm font-black font-mono uppercase tracking-wide flex items-center space-x-1.5" style="color: var(--text-main);">
              <span>{{ currentLabel }}</span>
              <span
                v-if="isNavigating"
                class="inline-block w-1.5 h-1.5 rounded-full bg-indigo-500 animate-ping"
              ></span>
            </h2>
          </div>
        </div>

        <div class="flex items-center space-x-1.5 sm:space-x-2.5 text-xs font-mono">
          <!-- 🌐 Global Language Switch Capsule in Admin -->
          <button
            @click="toggleLocale"
            class="h-7 px-2 sm:px-2.5 rounded border flex items-center space-x-1 cursor-pointer transition-all hover:bg-[var(--bg-card-hover)]"
            style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
            :title="locale === 'zh' ? 'Switch to English' : '切换至中文'"
          >
            <Globe class="w-3.5 h-3.5 text-indigo-400 shrink-0" />
            <span class="font-bold text-[11px]">{{ locale === 'zh' ? '中 / EN' : 'EN / 中' }}</span>
          </button>

          <!-- ☀️ / 🌙 Theme Toggle Button -->
          <button
            @click="toggleTheme"
            class="flex items-center justify-center w-7 h-7 rounded-lg border transition-all cursor-pointer shadow-xs hover:bg-[var(--bg-card-hover)]"
            style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
            :title="theme === 'dark' ? t.themeLight : t.themeDark"
          >
            <Sun v-if="theme === 'dark'" class="w-3.5 h-3.5 text-amber-400 hover:rotate-45 transition-transform" />
            <Moon v-else class="w-3.5 h-3.5 text-slate-700 hover:-rotate-12 transition-transform" />
          </button>

          <!-- Back to Terminal -->
          <a
            href="/"
            class="flex items-center space-x-1 px-2 sm:px-2.5 py-1 rounded-lg border transition-all cursor-pointer shadow-xs text-[11px] sm:text-xs hover:bg-[var(--bg-card-hover)]"
            style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          >
            <span>实盘大屏</span>
            <ExternalLink class="w-3 h-3 opacity-60" />
          </a>

          <!-- Docs -->
          <a
            href="/docs"
            class="flex items-center space-x-1 px-2 sm:px-2.5 py-1 rounded-lg border transition-all cursor-pointer shadow-xs text-[11px] sm:text-xs hover:bg-[var(--bg-card-hover)]"
            style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          >
            <BookOpen class="w-3 h-3" />
            <span class="hidden sm:inline">文档</span>
          </a>

          <!-- Mobile Logout -->
          <button
            @click="handleLogout"
            class="md:hidden flex items-center space-x-1 px-2 py-1 rounded-lg border cursor-pointer active:scale-95"
            style="background-color: var(--color-down-bg); color: var(--color-down); border-color: var(--color-down-border);"
            :title="t.logout"
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

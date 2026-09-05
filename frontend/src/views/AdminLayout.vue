<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useTheme } from '../composables/useTheme'
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
} from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const { theme, toggleTheme } = useTheme()

const navGroups = [
  {
    label: '系统总览',
    items: [
      { id: 'overview', label: '运行总览', icon: LayoutDashboard },
      { id: 'decisions', label: '决策日志', icon: Radio },
    ],
  },
  {
    label: '策略配置',
    items: [
      { id: 'promptlib', label: '提示词策略', icon: FileText },
      { id: 'evolution', label: '自进化配置', icon: Sparkles },
      { id: 'interceptors', label: '物理拦截插件', icon: ShieldCheck },
      { id: 'council', label: '模型委员会', icon: Users },
      { id: 'policy', label: '策略版本快照', icon: Layers },
      { id: 'llm', label: '模型连接', icon: Cpu },
      { id: 'agents', label: '运行单元', icon: Package },
      { id: 'plugins', label: '系统插件', icon: FileCode },
    ],
  },
  {
    label: '交易与网关',
    items: [
      { id: 'security', label: '交易所与标的池', icon: Wallet },
      { id: 'gateway', label: '任务网关', icon: RefreshCw },
      { id: 'notify', label: '消息通知', icon: Radio },
      { id: 'backup', label: '备份与还原', icon: FileCode },
    ],
  },
  {
    label: '系统管理',
    items: [
      { id: 'audit', label: '操作审计', icon: Scroll },
      { id: 'adminsys', label: '管理员与密码', icon: UserCog },
      { id: 'about', label: '版本与更新', icon: Info },
    ],
  },
] as const

const mobileDrawerOpen = ref(false)
const isNavigating = ref(false)
const pendingView = ref<string | null>(null)

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
  for (const group of navGroups) {
    const hit = (group.items as readonly { id: string; label: string }[]).find((i) => i.id === activeView.value)
    if (hit) return group.label
  }
  return '管理控制'
})

const currentLabel = computed<string>(() => {
  for (const group of navGroups) {
    const hit = (group.items as readonly { id: string; label: string }[]).find((i) => i.id === activeView.value)
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
    () => import('../views/admin/AuditPage.vue'),
    () => import('../views/admin/AdminSysPage.vue'),
    () => import('../views/admin/BackupPage.vue'),
    () => import('../views/admin/AgentsPage.vue'),
    () => import('../views/admin/PluginsPage.vue'),
    () => import('../views/admin/AboutPage.vue'),
  ]
  loaders.forEach((load) => {
    try { load() } catch (_) {}
  })
}

onMounted(() => {
  if ('requestIdleCallback' in window) {
    (window as any).requestIdleCallback(prefetchViews)
  } else {
    setTimeout(prefetchViews, 300)
  }
})

const showAboutModal = ref(false)
</script>

<template>
  <div
    class="min-h-screen flex flex-col md:flex-row font-sans transition-colors selection:bg-blue-500/30"
    style="background-color: var(--bg-app); color: var(--text-main);"
  >
    <!-- Top 2.5px Brand Loading Progress Line -->
    <div
      v-if="isNavigating"
      class="fixed top-0 left-0 right-0 h-[2.5px] z-50 animate-pulse transition-opacity"
      style="background: linear-gradient(90deg, #3B82F6, #10B981, #6366F1);"
    ></div>

    <!-- Mobile Drawer Overlay -->
    <div
      v-if="mobileDrawerOpen"
      class="fixed inset-0 bg-black/60 backdrop-blur-xs z-40 md:hidden transition-opacity"
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
            style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);"
          >
            ₿
          </div>
          <div>
            <div class="text-xs font-black tracking-wide font-mono" style="color: var(--text-main);">R20 CONTROL</div>
            <button
              @click="showAboutModal = true; mobileDrawerOpen = false"
              class="text-[10px] font-mono transition-colors cursor-pointer text-left block"
              style="color: var(--color-brand);"
            >
              v7.4.1
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

      <!-- Drawer Nav Items (Full Height, Ergonomic 44px Touch Targets) -->
      <nav class="flex-1 overflow-y-auto p-3 space-y-4">
        <div v-for="group in navGroups" :key="group.label">
          <div
            class="text-[10px] font-mono font-bold uppercase tracking-wider px-2.5 py-1 mb-1"
            style="color: var(--text-faint);"
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
                ? { backgroundColor: 'var(--color-brand-bg)', color: 'var(--color-brand)', borderColor: 'var(--color-brand-border)' }
                : { color: 'var(--text-muted)' }"
              :class="activeView === item.id ? 'border font-bold shadow-xs' : 'border border-transparent hover:text-[var(--text-main)] hover:bg-[var(--bg-card-hover)]'"
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

      <!-- Drawer Footer User Info -->
      <div class="p-3 border-t flex items-center justify-between" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);">
        <div class="flex items-center space-x-2 min-w-0">
          <div
            class="w-6 h-6 rounded-md border flex items-center justify-center font-bold text-[10px]"
            style="background-color: var(--bg-badge); border-color: var(--border-medium); color: var(--color-brand);"
          >
            {{ auth.user?.username?.charAt(0).toUpperCase() || 'A' }}
          </div>
          <div class="truncate text-xs font-mono">
            <span class="font-bold" style="color: var(--text-main);">{{ auth.user?.username || 'admin' }}</span>
          </div>
        </div>
        <button
          @click="handleLogout"
          class="px-2.5 py-1 rounded text-xs font-mono border hover:bg-rose-500/10 transition-colors cursor-pointer"
          style="color: var(--color-down); border-color: var(--color-down-border); background-color: var(--color-down-bg);"
        >
          退出
        </button>
      </div>
    </aside>

    <!-- Desktop Sidebar (Hidden completely on mobile, only visible on md:) -->
    <aside
      class="hidden md:flex md:w-[220px] md:shrink-0 md:border-r md:flex-col md:h-screen md:sticky md:top-0 transition-colors z-30"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <!-- Brand Header (Desktop) -->
      <div
        class="px-4 py-3.5 border-b flex items-center justify-between"
        style="border-color: var(--border-subtle);"
      >
        <div class="flex items-center space-x-2.5">
          <div
            class="w-7 h-7 rounded-md flex items-center justify-center font-mono font-black text-xs border shadow-xs"
            style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);"
          >
            ₿
          </div>
          <div>
            <div class="text-xs font-black tracking-wide font-mono" style="color: var(--text-main);">
              R20 CONTROL
            </div>
            <button
              @click="showAboutModal = true"
              class="text-[10px] font-mono transition-colors cursor-pointer text-left block"
              style="color: var(--color-brand);"
              title="点击查看开源主仓信息"
            >
              v7.4.1
            </button>
          </div>
        </div>

        <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" title="控制面正常"></span>
      </div>

      <!-- Nav Groups (Desktop Vertical) -->
      <nav class="overflow-y-auto overflow-x-hidden flex-1 py-2 px-2.5 space-y-1">
        <div v-for="group in navGroups" :key="group.label" class="mb-2.5">
          <div
            class="text-[9px] font-mono font-bold uppercase tracking-wider px-2 py-1 flex items-center justify-between"
            style="color: var(--text-faint);"
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
                ? { backgroundColor: 'var(--color-brand-bg)', color: 'var(--color-brand)', borderColor: 'var(--color-brand-border)' }
                : { color: 'var(--text-muted)' }"
              :class="activeView === item.id ? 'border font-bold shadow-xs' : 'border border-transparent hover:text-[var(--text-main)] hover:bg-[var(--bg-card-hover)]'"
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
            class="w-6 h-6 rounded-md border flex items-center justify-center font-bold text-[10px]"
            style="background-color: var(--bg-badge); border-color: var(--border-medium); color: var(--color-brand);"
          >
            {{ auth.user?.username?.charAt(0).toUpperCase() || 'A' }}
          </div>
          <div class="truncate">
            <div class="font-bold truncate text-[11px]" style="color: var(--text-main);">{{ auth.user?.username || 'admin' }}</div>
            <div class="text-[9px] capitalize" style="color: var(--text-faint);">{{ auth.user?.role || 'superadmin' }}</div>
          </div>
        </div>
        <button
          @click="handleLogout"
          class="p-1.5 rounded hover:bg-rose-500/10 hover:text-rose-500 transition-colors cursor-pointer"
          style="color: var(--text-faint);"
          title="退出登录"
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
            <Menu class="w-3.5 h-3.5 text-blue-400" />
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
                class="inline-block w-1.5 h-1.5 rounded-full bg-blue-500 animate-ping"
              ></span>
            </h2>
          </div>
        </div>

        <div class="flex items-center space-x-1.5 sm:space-x-2.5 text-xs font-mono">
          <!-- ☀️ / 🌙 Theme Toggle Button -->
          <button
            @click="toggleTheme"
            class="flex items-center justify-center w-7 h-7 rounded-lg border transition-all cursor-pointer shadow-xs"
            style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
            :title="theme === 'dark' ? '切换为亮色模式' : '切换为暗色模式'"
          >
            <Sun v-if="theme === 'dark'" class="w-3.5 h-3.5 text-amber-400 hover:rotate-45 transition-transform" />
            <Moon v-else class="w-3.5 h-3.5 text-slate-700 hover:-rotate-12 transition-transform" />
          </button>

          <!-- Back to Terminal -->
          <a
            href="/"
            target="_blank"
            class="flex items-center space-x-1 px-2 sm:px-2.5 py-1 rounded-lg border transition-all cursor-pointer shadow-xs text-[11px] sm:text-xs"
            style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          >
            <span>实盘</span>
            <ExternalLink class="w-3 h-3 opacity-60" />
          </a>

          <!-- Docs -->
          <a
            href="/docs"
            target="_blank"
            class="flex items-center space-x-1 px-2 sm:px-2.5 py-1 rounded-lg border transition-all cursor-pointer shadow-xs text-[11px] sm:text-xs"
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
            title="退出登录"
          >
            <LogOut class="w-3.5 h-3.5" />
          </button>
        </div>
      </header>

      <!-- Router View Workspace -->
      <main class="flex-1 p-3.5 sm:p-5 overflow-y-auto max-w-[2160px] w-full mx-auto">
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

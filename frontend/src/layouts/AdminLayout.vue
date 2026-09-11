<script setup lang="ts">
/**
 * 控制台壳层（Admin Terminal Shell）：
 * 1. 可折叠机构级侧栏（5 大功能组 / nav.ts 单一来源 / 激活项辉光高亮）
 * 2. 顶栏中枢（实时面包屑 / 北京时间合约时钟 / 返回实盘 / 主题切换 / 安全注销）
 * 3. 移动端平滑抽屉与安全登出处理
 */
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  PanelLeftClose,
  PanelLeftOpen,
  Menu,
  X,
  LogOut,
  MonitorPlay,
  Sun,
  Moon,
  ChevronRight,
  ShieldCheck,
} from 'lucide-vue-next';
import { useAuthStore } from '../stores/auth';
import { useTheme } from '../composables/useTheme';
import { useI18n } from '../composables/useI18n';
import { useLocalStorage } from '../composables/useLocalStorage';
import { adminGroups } from '../config/nav';
import { APP_VERSION, APP_NAME } from '../config/version';
import BeijingClock from '../components/base/BeijingClock.vue';

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const { theme, toggleTheme } = useTheme();
const { t } = useI18n();

const collapsed = useLocalStorage('r20_admin_sidebar', false);
const drawerOpen = ref(false);

const currentKey = computed(() => (route.name as string) || 'admin-overview');
const currentMeta = computed(() => {
  for (const g of adminGroups) {
    const hit = g.items.find((i) => i.key === currentKey.value);
    if (hit) return { group: g, item: hit };
  }
  return null;
});

function go(path: string) {
  drawerOpen.value = false;
  if (route.path !== path) router.push(path);
}

function logout() {
  auth.logout();
  router.push('/admin/login');
}

/* 后台 chunk 空闲预取：切页零等待 */
onMounted(() => {
  const prefetch = () =>
    adminGroups
      .flatMap((g) => g.items)
      .forEach((i) => import(`../views/admin/${pageFile(i.key)}.vue`).catch(() => {}));
  if ('requestIdleCallback' in window) (window as any).requestIdleCallback(prefetch);
  else setTimeout(prefetch, 400);
});

function pageFile(key: string): string {
  const map: Record<string, string> = {
    'admin-overview': 'OverviewPage',
    'admin-decisions': 'DecisionsPage',
    'admin-gateway': 'GatewayPage',
    'admin-council': 'CouncilPage',
    'admin-promptlib': 'PromptStudioPage',
    'admin-evolution': 'EvolutionPage',
    'admin-policy': 'PolicySnapshotPage',
    'admin-risk': 'RiskPage',
    'admin-interceptors': 'InterceptorsPage',
    'admin-plugins': 'PluginsPage',
    'admin-security': 'SecurityPage',
    'admin-llm': 'LlmPage',
    'admin-notify': 'NotifyPage',
    'admin-agents': 'AgentsPage',
    'admin-backup': 'BackupPage',
    'admin-audit': 'AuditPage',
    'admin-adminsys': 'AdminSysPage',
    'admin-about': 'AboutPage',
  };
  return map[key] || 'OverviewPage';
}

/* 路由变化时收起移动端抽屉 */
watch(() => route.path, () => (drawerOpen.value = false));
</script>

<template>
  <div class="flex min-h-screen" style="background-color: var(--surface-0); color: var(--ink-1)">
    <!-- 1. 侧栏（桌面端） -->
    <aside
      class="sticky top-0 hidden h-screen shrink-0 flex-col border-e transition-[width] duration-200 md:flex z-30"
      :class="collapsed ? 'w-[56px]' : 'w-[236px]'"
      style="background-color: var(--surface-1); border-color: var(--line-1)"
    >
      <!-- 品牌区 -->
      <div class="flex h-12 items-center gap-2 border-b px-3 shrink-0" style="border-color: var(--line-1)">
        <img src="/favicon.svg" class="h-6 w-6 shrink-0 rounded-md shadow-sm" alt="R20" />
        <div v-if="!collapsed" class="min-w-0">
          <p class="truncate text-xs font-bold leading-tight" style="color: var(--ink-strong)">{{ APP_NAME }}</p>
          <p class="num text-2xs leading-tight font-mono text-[var(--ink-3)]">{{ APP_VERSION }} · 控制台</p>
        </div>
      </div>

      <!-- 导航组列表 -->
      <nav class="scroll-y flex-1 py-2">
        <div v-for="g in adminGroups" :key="g.key" class="mb-1.5">
          <p v-if="!collapsed" class="t-label px-3 pb-1 pt-2 text-[10px] uppercase font-bold text-[var(--ink-3)]">
            {{ t(g.labelKey) }}
          </p>
          <div v-else class="mx-3 mt-2 border-t" style="border-color: var(--line-1)" />

          <button
            v-for="item in g.items"
            :key="item.key"
            type="button"
            class="relative flex h-8 w-full cursor-pointer items-center gap-2.5 px-3 text-xs transition-colors group"
            :class="currentKey === item.key ? 'font-semibold' : 'font-medium'"
            :style="currentKey === item.key
              ? {
                  backgroundColor: 'var(--surface-3)',
                  color: 'var(--ink-strong)',
                  boxShadow: 'inset 0 1px 0 rgba(255, 255, 255, 0.05)',
                }
              : { color: 'var(--ink-2)' }"
            :title="collapsed ? t(item.labelKey) : undefined"
            @click="go(item.path)"
          >
            <!-- 激活左侧高光线 -->
            <span
              v-if="currentKey === item.key"
              class="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-[var(--accent)] shadow-[0_0_6px_var(--accent)]"
            />
            <component
              :is="item.icon"
              class="h-4 w-4 shrink-0 transition-transform group-hover:scale-105"
              :class="currentKey === item.key ? 'text-[var(--accent)]' : 'text-[var(--ink-2)]'"
            />
            <span v-if="!collapsed" class="truncate tracking-tight">{{ t(item.labelKey) }}</span>
          </button>
        </div>
      </nav>

      <!-- 底部侧栏折叠开关 -->
      <button
        type="button"
        class="flex h-9 cursor-pointer items-center justify-center gap-2 border-t text-xs transition-colors hover:bg-[var(--surface-3)] shrink-0"
        style="border-color: var(--line-1); color: var(--ink-2)"
        :title="collapsed ? t('admin.shell.expand') : t('admin.shell.collapse')"
        @click="collapsed = !collapsed"
      >
        <PanelLeftOpen v-if="collapsed" class="h-4 w-4" />
        <PanelLeftClose v-else class="h-4 w-4" />
        <span v-if="!collapsed" class="text-2xs">{{ t('admin.shell.collapse') }}</span>
      </button>
    </aside>

    <!-- 2. 主体区 (顶栏 + 内容) -->
    <div class="flex min-w-0 flex-1 flex-col">
      <!-- 控制台顶栏 -->
      <header
        class="sticky top-0 z-20 flex h-12 shrink-0 items-center justify-between border-b px-3 sm:px-5 backdrop-blur-xl"
        style="background-color: var(--surface-header); border-color: var(--line-1)"
      >
        <div class="flex items-center gap-2 min-w-0">
          <!-- 移动端汉堡抽屉触发器 -->
          <button
            type="button"
            class="btn btn-quiet btn-icon md:hidden cursor-pointer"
            @click="drawerOpen = !drawerOpen"
          >
            <Menu v-if="!drawerOpen" class="h-4 w-4" />
            <X v-else class="h-4 w-4" />
          </button>

          <!-- 面包屑导航 -->
          <div class="flex items-center gap-1.5 text-xs truncate">
            <span class="t-faint hidden sm:inline">控制台</span>
            <ChevronRight class="h-3 w-3 t-faint hidden sm:inline" />
            <span v-if="currentMeta" class="t-faint hidden sm:inline">{{ t(currentMeta.group.labelKey) }}</span>
            <ChevronRight v-if="currentMeta" class="h-3 w-3 t-faint hidden sm:inline" />
            <span class="font-bold text-[var(--ink-strong)] truncate">
              {{ currentMeta ? t(currentMeta.item.labelKey) : '管理控制台' }}
            </span>
          </div>
        </div>

        <!-- 顶栏右侧工具区 -->
        <div class="flex items-center gap-1.5 sm:gap-2">
          <!-- 北京时间时钟 -->
          <BeijingClock class="hidden lg:block mx-1" />

          <!-- 返回实盘大屏 -->
          <button
            type="button"
            class="btn btn-ghost btn-sm flex items-center gap-1.5 text-xs cursor-pointer font-semibold"
            :title="t('admin.shell.backToDashboard')"
            @click="router.push('/')"
          >
            <MonitorPlay class="h-3.5 w-3.5 text-[var(--accent)]" />
            <span class="hidden sm:inline">{{ t('admin.shell.backToDashboard') }}</span>
          </button>

          <!-- 主题切换 -->
          <button
            type="button"
            class="btn btn-quiet btn-icon cursor-pointer"
            :title="t('dash.shell.settings.theme')"
            @click="toggleTheme"
          >
            <Sun v-if="theme === 'dark'" class="h-4 w-4 text-amber-400" />
            <Moon v-else class="h-4 w-4 text-[var(--ink-2)]" />
          </button>

          <!-- 安全登出 -->
          <button
            type="button"
            class="btn btn-quiet btn-icon text-[var(--down)] hover:bg-[var(--down-bg)] cursor-pointer"
            :title="t('admin.shell.logout')"
            @click="logout"
          >
            <LogOut class="h-4 w-4" />
          </button>
        </div>
      </header>

      <!-- 页面内容注入点 -->
      <main class="flex-1 p-3 sm:p-5 overflow-x-hidden">
        <router-view />
      </main>
    </div>

    <!-- 3. 移动端抽屉导航 -->
    <Teleport to="body">
      <Transition name="fade">
        <div
          v-if="drawerOpen"
          class="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm md:hidden"
          @click="drawerOpen = false"
        />
      </Transition>

      <Transition name="slide">
        <div
          v-if="drawerOpen"
          class="fixed inset-y-0 left-0 z-50 w-64 border-e shadow-2xl md:hidden flex flex-col"
          style="background-color: var(--surface-1); border-color: var(--line-2)"
        >
          <!-- 抽屉头部 -->
          <div class="flex h-12 items-center justify-between border-b px-3.5" style="border-color: var(--line-1)">
            <div class="flex items-center gap-2">
              <img src="/favicon.svg" class="h-6 w-6 rounded-md" alt="" />
              <span class="text-xs font-bold" style="color: var(--ink-strong)">{{ APP_NAME }}</span>
            </div>
            <button type="button" class="btn btn-quiet btn-icon" @click="drawerOpen = false">
              <X class="h-4 w-4" />
            </button>
          </div>

          <!-- 抽屉导航项 -->
          <nav class="scroll-y flex-1 py-2">
            <div v-for="g in adminGroups" :key="g.key" class="mb-2">
              <p class="t-label px-3.5 pb-1 pt-2 text-[10px] uppercase font-bold text-[var(--ink-3)]">
                {{ t(g.labelKey) }}
              </p>
              <button
                v-for="item in g.items"
                :key="item.key"
                type="button"
                class="flex h-8.5 w-full items-center gap-2.5 px-3.5 text-xs transition-colors"
                :style="currentKey === item.key
                  ? { backgroundColor: 'var(--surface-3)', color: 'var(--ink-strong)', fontWeight: 'bold' }
                  : { color: 'var(--ink-2)' }"
                @click="go(item.path)"
              >
                <component :is="item.icon" class="h-4 w-4 shrink-0 text-[var(--accent)]" />
                <span class="truncate">{{ t(item.labelKey) }}</span>
              </button>
            </div>
          </nav>

          <!-- 抽屉底部安全退出 -->
          <div class="border-t p-3" style="border-color: var(--line-1)">
            <button
              type="button"
              class="btn btn-ghost w-full justify-center text-xs text-[var(--down)]"
              @click="logout"
            >
              <LogOut class="h-3.5 w-3.5 mr-1" />
              <span>{{ t('admin.shell.logout') }}</span>
            </button>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

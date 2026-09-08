<script setup lang="ts">
/**
 * 控制台壳层：可折叠侧栏（5 组 / nav.ts 单一来源）+ 顶栏（面包屑/⌘K/主题/CVD/返回大屏/退出）。
 * 登录守卫在 router；此处只管呈现。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import {
  PanelLeftClose, PanelLeftOpen, Menu, X, LogOut, MonitorPlay,
  Sun, Moon, Command, Eye,
} from 'lucide-vue-next';
import { useAuthStore } from '../stores/auth';
import { useTheme } from '../composables/useTheme';
import { useI18n } from '../composables/useI18n';
import { useUi } from '../composables/useUi';
import { useLocalStorage } from '../composables/useLocalStorage';
import { useHotkeys } from '../composables/useHotkeys';
import { adminGroups } from '../config/nav';
import { APP_VERSION, APP_NAME } from '../config/version';

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const { theme, toggleTheme, cvd, toggleCvd } = useTheme();
const { t } = useI18n();
const { cmdkOpen, peekOpen } = useUi();

useHotkeys({ 'mod+k': { handler: () => (cmdkOpen.value = true), allowInInput: true } });

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
  const prefetch = () => adminGroups.flatMap((g) => g.items).forEach((i) => import(`../views/admin/${pageFile(i.key)}.vue`).catch(() => {}));
  if ('requestIdleCallback' in window) (window as any).requestIdleCallback(prefetch);
  else setTimeout(prefetch, 400);
});
function pageFile(key: string): string {
  const map: Record<string, string> = {
    'admin-overview': 'OverviewPage', 'admin-decisions': 'DecisionsPage', 'admin-gateway': 'GatewayPage',
    'admin-council': 'CouncilPage', 'admin-promptlib': 'PromptStudioPage', 'admin-evolution': 'EvolutionPage',
    'admin-policy': 'PolicySnapshotPage', 'admin-risk': 'RiskPage', 'admin-interceptors': 'InterceptorsPage',
    'admin-plugins': 'PluginsPage', 'admin-security': 'SecurityPage', 'admin-llm': 'LlmPage',
    'admin-notify': 'NotifyPage', 'admin-agents': 'AgentsPage', 'admin-backup': 'BackupPage',
    'admin-audit': 'AuditPage', 'admin-adminsys': 'AdminSysPage', 'admin-about': 'AboutPage',
  };
  return map[key] || 'OverviewPage';
}

/* 路由变化时收起移动端抽屉 */
watch(() => route.path, () => (drawerOpen.value = false));
</script>

<template>
  <div class="flex min-h-screen" style="background-color: var(--surface-0); color: var(--ink-1)">
    <!-- 侧栏（桌面） -->
    <aside
      class="sticky top-0 hidden h-screen shrink-0 flex-col border-e transition-[width] duration-200 md:flex"
      :class="collapsed ? 'w-[56px]' : 'w-[232px]'"
      style="background-color: var(--surface-1); border-color: var(--line-1)"
    >
      <!-- 品牌 -->
      <div class="flex h-12 items-center gap-2 border-b px-3" style="border-color: var(--line-1)">
        <img src="/favicon.svg" class="h-6 w-6 shrink-0 rounded-md" alt="" />
        <div v-if="!collapsed" class="min-w-0">
          <p class="truncate text-xs font-bold leading-tight" style="color: var(--ink-strong)">{{ APP_NAME }}</p>
          <p class="num text-2xs leading-tight" style="color: var(--ink-3)">{{ APP_VERSION }}</p>
        </div>
      </div>

      <nav class="scroll-y flex-1 py-2">
        <div v-for="g in adminGroups" :key="g.key" class="mb-1">
          <p v-if="!collapsed" class="t-label px-3 pb-1 pt-2.5">{{ t(g.labelKey) }}</p>
          <div v-else class="mx-3 mt-2 border-t" style="border-color: var(--line-1)" />
          <button
            v-for="item in g.items"
            :key="item.key"
            class="relative flex h-8 w-full cursor-pointer items-center gap-2.5 px-3 text-sm transition-colors"
            :class="currentKey === item.key ? 'font-semibold' : 'font-medium'"
            :style="currentKey === item.key
              ? { backgroundColor: 'var(--surface-3)', color: 'var(--ink-strong)' }
              : { color: 'var(--ink-2)' }"
            :title="collapsed ? t(item.labelKey) : undefined"
            @click="go(item.path)"
          >
            <span v-if="currentKey === item.key" class="absolute inset-y-1 left-0 w-0.5 rounded-full" style="background-color: var(--accent)" />
            <component :is="item.icon" class="h-4 w-4 shrink-0" />
            <span v-if="!collapsed" class="truncate">{{ t(item.labelKey) }}</span>
          </button>
        </div>
      </nav>

      <button
        class="flex h-9 cursor-pointer items-center justify-center gap-2 border-t text-xs transition-colors hover:bg-[var(--surface-3)]"
        style="border-color: var(--line-1); color: var(--ink-2)"
        :title="collapsed ? t('admin.shell.expand') : t('admin.shell.collapse')"
        @click="collapsed = !collapsed"
      >
        <PanelLeftOpen v-if="collapsed" class="h-4 w-4" />
        <PanelLeftClose v-else class="h-4 w-4" />
        <span v-if="!collapsed">{{ t('admin.shell.collapse') }}</span>
      </button>
    </aside>

    <!-- 移动端抽屉 -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="drawerOpen" class="fixed inset-0 z-[var(--z-drawer)] md:hidden">
          <div class="absolute inset-0" style="background-color: var(--overlay-scrim)" @click="drawerOpen = false" />
          <aside class="absolute inset-y-0 left-0 flex w-[260px] flex-col" style="background-color: var(--surface-1); border-right: 1px solid var(--line-2)">
            <div class="flex h-12 items-center justify-between border-b px-3" style="border-color: var(--line-1)">
              <span class="text-sm font-bold" style="color: var(--ink-strong)">{{ t('admin.shell.breadcrumbRoot') }}</span>
              <button class="btn btn-quiet btn-icon" @click="drawerOpen = false"><X class="h-4 w-4" /></button>
            </div>
            <nav class="scroll-y flex-1 py-2">
              <div v-for="g in adminGroups" :key="g.key">
                <p class="t-label px-3 pb-1 pt-2.5">{{ t(g.labelKey) }}</p>
                <button
                  v-for="item in g.items"
                  :key="item.key"
                  class="flex h-10 w-full cursor-pointer items-center gap-2.5 px-3 text-sm"
                  :style="currentKey === item.key ? { backgroundColor: 'var(--surface-3)', color: 'var(--ink-strong)' } : { color: 'var(--ink-1)' }"
                  @click="go(item.path)"
                >
                  <component :is="item.icon" class="h-4 w-4 shrink-0" />
                  {{ t(item.labelKey) }}
                </button>
              </div>
            </nav>
          </aside>
        </div>
      </Transition>
    </Teleport>

    <!-- 主区 -->
    <div class="flex min-w-0 flex-1 flex-col">
      <header
        class="sticky top-0 z-[var(--z-header)] flex h-12 items-center gap-2 border-b px-3 backdrop-blur-xl sm:px-5"
        style="background-color: var(--surface-header); border-color: var(--line-1)"
      >
        <button class="btn btn-quiet btn-icon md:hidden" @click="drawerOpen = true"><Menu class="h-4 w-4" /></button>
        <div class="min-w-0">
          <p class="t-faint truncate text-2xs leading-tight">
            {{ t('admin.shell.breadcrumbRoot') }}<span v-if="currentMeta"> / {{ t(currentMeta.group.labelKey) }}</span>
          </p>
        </div>
        <div class="ms-auto flex items-center gap-1">
          <button class="btn btn-quiet btn-icon hidden sm:inline-flex" :title="t('nav.actions.search')" @click="cmdkOpen = true">
            <Command class="h-4 w-4" />
          </button>
          <button class="btn btn-quiet btn-icon" :title="t('nav.actions.promptPeek')" @click="peekOpen = true">
            <Eye class="h-4 w-4" />
          </button>
          <button
            class="btn h-8 px-2 text-2xs"
            :class="cvd ? 'btn-primary' : 'btn-ghost'"
            :title="t('dash.shell.settings.cvd')"
            @click="toggleCvd"
          >
            CVD
          </button>
          <button class="btn btn-quiet btn-icon" :title="t('dash.shell.settings.theme')" @click="toggleTheme">
            <Sun v-if="theme === 'dark'" class="h-4 w-4" />
            <Moon v-else class="h-4 w-4" />
          </button>
          <span class="mx-1 hidden h-5 w-px sm:block" style="background-color: var(--line-2)" />
          <button class="btn btn-ghost btn-sm hidden sm:inline-flex" @click="router.push('/')">
            <MonitorPlay class="h-3.5 w-3.5" />{{ t('nav.actions.backToScreen') }}
          </button>
          <button class="btn btn-quiet btn-icon" :title="t('nav.actions.logout')" @click="logout"><LogOut class="h-4 w-4" /></button>
        </div>
      </header>

      <main class="admin-workspace mx-auto w-full max-w-[1400px] flex-1 p-3.5 sm:p-5">
        <router-view />
      </main>
    </div>
  </div>
</template>

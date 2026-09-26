<script setup lang="ts">
/**
 * AdminLayout.vue · R20 开发者工作台外壳
 * ---------------------------------------------------------------------------
 * 视觉语言：DeepSeek Harness 侧边导航工作台
 *   · 画布与侧栏同底（#0a0a0a），靠 6% 发丝描边分区，不用独立侧栏底色
 *   · 导航项 30px 基线；选中态 = 白色 alpha 分层 + 品牌蓝图标，不做加粗染色
 *   · 顶栏 48px 与画布同底 + 底描边；面包屑弱化，动作区右对齐
 *   · 内容区留白走 --ds-space-5，全站无渐变、无 glow、无点阵背景
 *
 * 行为契约：与重构前完全一致 —— 折叠持久化、鉴权退出、面包屑、大屏跳转、
 *          主题切换、移动端抽屉、后台 chunk 空闲预取，逻辑未做任何改动。
 */
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useRouteFocus } from '../composables/useRouteFocus';
import {
  PanelLeftClose,
  PanelLeftOpen,
  Menu,
  X,
  LogOut,
  MonitorPlay,
  Sun,
  Moon,
} from 'lucide-vue-next';
import { useAuthStore } from '../stores/auth';
import { useI18n } from '../composables/useI18n';
import { useTheme } from '../composables/useTheme';
import { useLocalStorage } from '../composables/useLocalStorage';
import { adminGroups } from '../config/nav';
import { APP_VERSION } from '../config/version';
import BeijingClock from '../components/base/BeijingClock.vue';
import SkipLink from '../components/base/SkipLink.vue';

const route = useRoute();

// 批 112：切页后把焦点交给主内容（首次进入不抢，见组合式头注）
useRouteFocus();
const router = useRouter();
const auth = useAuthStore();
const { t } = useI18n();
const { theme, toggleTheme } = useTheme();

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

/* 后台 chunk 空闲预取 */
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

watch(() => route.path, () => (drawerOpen.value = false));
</script>

<template>
  <div class="wb">
    <!-- 键盘用户的第一个 Tab 落点：跳过 18 项侧边导航直达正文（批 45） -->
    <SkipLink />

    <!-- ═══ 侧边导航 ═══ -->
    <aside id="admin-desktop-sidebar" class="wb-rail" :class="{ 'is-collapsed': collapsed }">
      <!-- 品牌 -->
      <div class="wb-brand">
        <img src="/favicon.svg" class="wb-logo" alt="" />
        <div v-if="!collapsed" class="wb-brand-text">
          <span class="wb-brand-name">{{ t('brand.name') }}</span>
          <span class="wb-version">{{ APP_VERSION }}</span>
        </div>
        <button type="button"
          v-if="!collapsed"
          class="wb-icon-btn wb-collapse"
          :title="t('admin.shell.collapse')"
          :aria-expanded="!collapsed"
          :aria-controls="'admin-desktop-sidebar'"
          @click="collapsed = true"
        >
          <PanelLeftClose :size="15" />
        </button>
      </div>

      <!-- 导航分组 -->
      <nav class="wb-nav scroll-area">
        <div v-for="g in adminGroups" :key="g.key" class="wb-group">
          <div v-if="!collapsed" class="wb-group-label">{{ t(g.labelKey) }}</div>
          <div v-else class="wb-group-sep" />
          <button type="button"
            v-for="item in g.items"
            :key="item.key"
            class="wb-item"
            :class="{ 'is-active': currentKey === item.key }"
            :title="collapsed ? t(item.labelKey) : undefined"
            :aria-current="currentKey === item.key ? 'page' : undefined"
            @click="go(item.path)"
          >
            <component :is="item.icon" :size="15" class="wb-item-icon" />
            <span v-if="!collapsed" class="wb-item-text">{{ t(item.labelKey) }}</span>
          </button>
        </div>
      </nav>

      <!-- 底部动作 -->
      <div class="wb-rail-foot">
        <button type="button"
          v-if="collapsed"
          class="wb-icon-btn wb-expand"
          :title="t('admin.shell.expand')"
          :aria-expanded="!collapsed"
          :aria-controls="'admin-desktop-sidebar'"
          @click="collapsed = false"
        >
          <PanelLeftOpen :size="15" />
        </button>
        <button type="button" class="wb-item wb-item-danger" :title="t('nav.actions.logout')" @click="logout">
          <LogOut :size="15" class="wb-item-icon" />
          <span v-if="!collapsed" class="wb-item-text">{{ t('nav.actions.logout') }}</span>
        </button>
      </div>
    </aside>

    <!-- ═══ 主工位 ═══ -->
    <div class="wb-stage">
      <!-- 顶栏 -->
      <header class="wb-topbar">
        <button type="button"
          class="wb-icon-btn wb-burger"
          :title="drawerOpen ? t('admin.shell.collapse') : t('admin.shell.expand')"
          :aria-label="drawerOpen ? t('admin.shell.collapse') : t('admin.shell.expand')"
          :aria-expanded="drawerOpen"
          :aria-controls="drawerOpen ? 'admin-mobile-drawer' : undefined"
          @click="drawerOpen = !drawerOpen"
        >
          <Menu :size="16" />
        </button>

        <nav class="wb-crumbs" aria-label="Breadcrumb">
          <span class="wb-crumb-root">{{ t('admin.shell.breadcrumbRoot') }}</span>
          <template v-if="currentMeta">
            <span class="wb-crumb-sep sm:inline hidden">/</span>
            <span class="wb-crumb-dim">{{ t(currentMeta.group.labelKey) }}</span>
            <span class="wb-crumb-sep">/</span>
            <span class="wb-crumb-cur">
              <component :is="currentMeta.item.icon" :size="14" />
              {{ t(currentMeta.item.labelKey) }}
            </span>
          </template>
        </nav>

        <div class="wb-topbar-right">
          <BeijingClock class="wb-clock" />

          <button type="button" class="wb-icon-btn" :title="t('nav.actions.theme')" :aria-label="t('nav.actions.theme')" @click="toggleTheme">
            <Sun v-if="theme === 'dark'" :size="15" />
            <Moon v-else :size="15" />
          </button>

          <button type="button" class="wb-icon-btn" :title="t('nav.actions.backToScreen')" @click="router.push('/')">
            <MonitorPlay :size="15" />
          </button>
        </div>
      </header>

      <!-- 内容区 -->
      <main id="main-content" tabindex="-1" class="wb-main scroll-area outline-none">
        <div class="wb-content">
          <router-view />
        </div>
      </main>
    </div>

    <!-- ═══ 移动端抽屉 ═══ -->
    <div v-if="drawerOpen" class="wb-drawer-root">
      <div class="wb-scrim" @click="drawerOpen = false" />
      <aside id="admin-mobile-drawer" class="wb-drawer" :aria-label="t('admin.shell.brand')">
        <div class="wb-drawer-head">
          <span class="wb-brand-name">{{ t('brand.name') }} {{ t('admin.shell.brand') }}</span>
          <button type="button"
            class="wb-icon-btn"
            :title="t('common.close')"
            :aria-label="t('common.close')"
            @click="drawerOpen = false"
          >
            <X :size="15" />
          </button>
        </div>
        <nav class="wb-nav scroll-area">
          <div v-for="g in adminGroups" :key="g.key" class="wb-group">
            <div class="wb-group-label">{{ t(g.labelKey) }}</div>
            <button type="button"
              v-for="item in g.items"
              :key="item.key"
              class="wb-item"
              :class="{ 'is-active': currentKey === item.key }"
              :aria-current="currentKey === item.key ? 'page' : undefined"
              @click="go(item.path)"
            >
              <component :is="item.icon" :size="15" class="wb-item-icon" />
              <span class="wb-item-text">{{ t(item.labelKey) }}</span>
            </button>
          </div>
        </nav>
        <div class="wb-rail-foot">
          <button type="button" class="wb-item wb-item-danger" @click="logout">
            <LogOut :size="15" class="wb-item-icon" />
            <span class="wb-item-text">{{ t('nav.actions.logout') }}</span>
          </button>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
/* ── 外壳：画布满高，无独立底色，靠描边分区 ── */
.wb {
  display: flex;
  height: 100svh;
  width: 100%;
  overflow: hidden;
  background-color: var(--ds-color-bg-page);
  color: var(--ds-color-text-secondary);
}

/* ══ 侧边导航 ══ */
.wb-rail {
  display: none;
  flex-direction: column;
  flex-shrink: 0;
  width: var(--w-sidebar);
  border-right: 1px solid var(--ds-color-border-default);
  background-color: var(--surface-sidebar);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  transition: width var(--dur-base) var(--ease-out);
}
.wb-rail.is-collapsed {
  width: var(--w-sidebar-collapsed);
}
@media (min-width: 768px) {
  .wb-rail {
    display: flex;
  }
}

.wb-brand {
  display: flex;
  align-items: center;
  gap: var(--ds-space-2);
  height: var(--h-topbar);
  padding: 0 var(--ds-space-3);
  border-bottom: 1px solid var(--ds-color-border-default);
  flex-shrink: 0;
}
.wb-logo {
  width: 20px;
  height: 20px;
  border-radius: var(--r-xs);
  flex-shrink: 0;
}
.wb-brand-text {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1;
}
.wb-brand-name {
  font-size: var(--text-xs);
  font-weight: 600;
  letter-spacing: var(--track-title);
  color: var(--ds-color-text-primary);
  white-space: nowrap;
}
.wb-version {
  font-family: var(--ds-font-mono);
  font-size: var(--text-4xs);
  color: var(--ds-color-text-placeholder);
  border: 1px solid var(--ds-color-border-default);
  border-radius: var(--r-xs);
  padding: 0 4px;
  line-height: 15px;
}
.wb-collapse {
  margin-left: auto;
}

/* 导航列表 */
.wb-nav {
  flex: 1;
  padding: var(--ds-space-3) var(--ds-space-2);
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-4);
}
.wb-group {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.wb-group-label {
  padding: 0 var(--ds-space-2) 6px;
  font-size: var(--text-2xs);
  font-weight: 500;
  letter-spacing: var(--track-label);
  text-transform: uppercase;
  color: var(--ds-color-text-placeholder);
}
.wb-group-sep {
  height: 1px;
  margin: var(--ds-space-2) var(--ds-space-2);
  background-color: var(--ds-color-border-default);
}

.wb-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 32px;
  padding: 0 var(--ds-space-2);
  border: 1px solid transparent;
  border-radius: var(--r-ctl);
  background-color: transparent;
  color: var(--ds-color-text-description);
  font-family: inherit;
  font-size: var(--text-xs);
  font-weight: 400;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out);
}
.wb-item:hover {
  background-color: var(--ds-color-bg-hover);
  color: var(--ds-color-text-primary);
}
.wb-item.is-active {
  background: linear-gradient(90deg, rgba(59, 130, 246, 0.12) 0%, rgba(59, 130, 246, 0.02) 100%);
  color: var(--ds-color-text-primary);
  font-weight: 600;
  border: 1px solid var(--accent-line);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
}
.wb-item-icon {
  flex-shrink: 0;
  opacity: 0.6;
}
.wb-item.is-active .wb-item-icon {
  opacity: 1;
  color: var(--ds-color-brand);
}
.wb-item-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wb-item-danger:hover {
  background-color: var(--down-bg);
  color: var(--down);
}

/* 底部 */
.wb-rail-foot {
  flex-shrink: 0;
  padding: var(--ds-space-2);
  border-top: 1px solid var(--ds-color-border-default);
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.wb-expand {
  align-self: center;
  margin-bottom: var(--ds-space-1);
}

/* 图标按钮 */
.wb-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  /* 批 35：写死 28px。全站控件高度梯队是 --h-sm/md/lg = 24/30/36，
     28 不在梯队上，且同一行里的 .btn（30）比它高 2px。改用 --h-md。 */
  width: var(--h-md);
  height: var(--h-md);
  border: 1px solid transparent;
  border-radius: var(--r-ctl);
  background: transparent;
  color: var(--ds-color-text-description);
  cursor: pointer;
  flex-shrink: 0;
  transition: background-color var(--dur-fast), color var(--dur-fast),
    border-color var(--dur-fast);
}
.wb-icon-btn:hover {
  background-color: var(--ds-btn-ghost-hover-bg);
  border-color: var(--ds-btn-ghost-hover-border);
  color: var(--ds-color-text-primary);
}

/* ══ 主工位 ══ */
.wb-stage {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  overflow: hidden;
}

.wb-topbar {
  display: flex;
  align-items: center;
  gap: var(--ds-space-3);
  height: var(--h-topbar);
  padding: 0 var(--ds-space-3);
  border-bottom: 1px solid var(--ds-color-border-default);
  background-color: var(--surface-header);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  flex-shrink: 0;
}
@media (min-width: 768px) {
  .wb-topbar {
    padding: 0 var(--ds-space-4);
  }
}
.wb-burger {
  display: inline-flex;
}
@media (min-width: 768px) {
  .wb-burger {
    display: none;
  }
}

/* 面包屑 */
.wb-crumbs {
  display: flex;
  align-items: center;
  gap: var(--ds-space-2);
  min-width: 0;
  font-size: var(--text-xs);
}
.wb-crumb-root {
  font-weight: 600;
  color: var(--ds-color-text-primary);
  letter-spacing: var(--track-title);
}
.wb-crumb-sep {
  color: var(--ds-color-text-placeholder);
}
.wb-crumb-dim {
  color: var(--ds-color-text-placeholder);
  display: none;
}
@media (min-width: 640px) {
  .wb-crumb-dim {
    display: inline;
  }
}
.wb-crumb-cur {
  display: flex;
  align-items: center;
  gap:6px;
  color: var(--ds-color-text-secondary);
  font-weight: 500;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wb-topbar-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--ds-space-1);
}
.wb-clock {
  display: none;
}
@media (min-width: 1024px) {
  .wb-clock {
    display: block;
    margin-right: var(--ds-space-2);
  }
}

/* 内容区 */
.wb-main {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  /* 批 33：此前移动端 16px、≥768px 24px，而前台壳层（DashboardLayout）
     在 ≥640px 就是 16px + 底部 24px —— 同一套外壳两个内缩值，前后台来回切
     页面整体会横移 8px。统一到前台的 16/16/24/16。 */
  padding: var(--ds-space-4);
  padding-bottom: var(--ds-space-5);
}
.wb-content {
  flex: 1;
  min-height: 0;
  max-width: 1600px;
  width: 100%;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-4);
}

/* ══ 移动端抽屉 ══ */
.wb-drawer-root {
  position: fixed;
  inset: 0;
  z-index: var(--z-drawer);
  display: flex;
}
.wb-scrim {
  position: absolute;
  inset: 0;
  background-color: var(--overlay-scrim);
  backdrop-filter: blur(2px);
}
.wb-drawer {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 268px;
  max-width: 82vw;
  background-color: var(--ds-color-bg-page);
  border-right: 1px solid var(--ds-color-border-default);
}
.wb-drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ds-space-2);
  height: var(--h-topbar);
  padding: 0 var(--ds-space-3);
  border-bottom: 1px solid var(--ds-color-border-default);
  flex-shrink: 0;
}
</style>

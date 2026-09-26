<script setup lang="ts">
/**
 * DashboardLayout.vue · DeepSeek Harness 开发者工作台骨架布局
 * 采用侧边导航工作台架构、分层工作区设计、顶部控制条与全局决策轨迹/日志面板
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useRouteFocus } from '../composables/useRouteFocus';
import { useDashboardStore } from '../stores/dashboard';
import { useI18n } from '../composables/useI18n';
import { APP_VERSION } from '../config/version';
import { useUi } from '../composables/useUi';
import { useHotkeys } from '../composables/useHotkeys';
import { useLocalStorage } from '../composables/useLocalStorage';
import { publicTabs } from '../config/nav';
import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  X,
} from 'lucide-vue-next';

import TopBar from '../components/dashboard/TopBar.vue';
import MobileTabBar from '../components/dashboard/MobileTabBar.vue';
import MatrixView from '../views/dashboard/MatrixView.vue';
import RadarView from '../views/dashboard/RadarView.vue';
import NewsView from '../views/dashboard/NewsView.vue';
import EvolutionView from '../views/dashboard/EvolutionView.vue';
import LedgerView from '../views/dashboard/LedgerView.vue';
import AboutModal from '../components/dashboard/AboutModal.vue';
import SkipLink from '../components/base/SkipLink.vue';
import TrajectoryPanel from '../components/dashboard/TrajectoryPanel.vue';

const route = useRoute();

// 批 112：切页后把焦点交给主内容（首次进入不抢，见组合式头注）
useRouteFocus();
const router = useRouter();
const store = useDashboardStore();
const { t } = useI18n();
const { aboutOpen, trajectoryOpen } = useUi();

const sidebarCollapsed = useLocalStorage('r20_sidebar_collapsed', false);

/* ── 窄屏导航（2026-09-16 用户反馈修复）────────────────────────────────────
 * 症状：窗口宽度 < 768px（Tailwind md 断点）时左侧栏被 `hidden md:flex` 整体隐藏，
 * 顶栏那个「展开导航栏」按钮因此点了毫无反应 —— 侧栏在窄屏根本不存在，也没有抽屉
 * 兜底。修复：窄屏下把同一套导航变成 off-canvas 抽屉，由**同一个按钮**开合。
 * 桌面端行为完全不变（仍是折叠成 w-16 的窄侧栏，偏好持久化）。 */
const NARROW_QUERY = '(max-width: 767px)';
const isNarrow = ref(false);
const mobileNavOpen = ref(false);
let _mq: MediaQueryList | null = null;

function _syncNarrow(e: MediaQueryList | MediaQueryListEvent) {
  isNarrow.value = e.matches;
  if (!e.matches) mobileNavOpen.value = false;  // 回到桌面端：抽屉态必须清掉
}

/** 顶栏按钮的唯一语义：切换「导航可见性」——桌面=折叠/展开，窄屏=开合抽屉。 */
function toggleNav() {
  if (isNarrow.value) mobileNavOpen.value = !mobileNavOpen.value;
  else sidebarCollapsed.value = !sidebarCollapsed.value;
}
/** 传给顶栏的「导航是否可见」，两套形态共用一个图标语义。 */
const navExpanded = computed(() =>
  isNarrow.value ? mobileNavOpen.value : !sidebarCollapsed.value);
/** 桌面端折叠态（窄屏抽屉永远是完整宽度，标签/分组必须照常显示）。 */
const navCompact = computed(() => !isNarrow.value && sidebarCollapsed.value);

/* 全局 ⌘J 快捷键呼出轨迹面板 */
useHotkeys({
  'mod+j': { handler: () => (trajectoryOpen.value = !trajectoryOpen.value), allowInInput: true },
});

const activeTab = computed(() => (route.meta?.tab as string) || 'trading');

watch(activeTab, (v) => {
  store.activeTab = v as any;
  mobileNavOpen.value = false;   // 切页即收起窄屏抽屉
}, { immediate: true });

onMounted(() => {
  _mq = window.matchMedia(NARROW_QUERY);
  _syncNarrow(_mq);
  _mq.addEventListener('change', _syncNarrow);
  store.startPolling(3000);
});
onUnmounted(() => {
  _mq?.removeEventListener('change', _syncNarrow);
  store.stopPolling();
});

function go(path: string) {
  mobileNavOpen.value = false;
  if (route.path !== path) router.push(path);
}
</script>

<template>
  <div
    class="flex h-screen w-screen overflow-hidden text-xs"
    style="color: var(--ink-1)"
  >
    <!-- 键盘用户的第一个 Tab 落点：跳过侧边导航与顶栏动作组直达正文（批 45） -->
    <SkipLink />
    <!-- 左侧：R20 终端侧边导航栏
         桌面（md+）= 常驻侧栏（可折叠 236/56）；窄屏 = off-canvas 抽屉（同一元素）
         层级：遮罩 z-30 < 抽屉 z-50 < 顶栏 z-[60] —— 顶栏必须压在抽屉之上，
         否则抽屉展开后会把顶栏那颗「展开/收起导航」按钮自己盖住，点不回去。 -->
    <aside
      id="dashboard-sidebar"
      class="flex flex-col shrink-0 border-e transition-all duration-200 z-50 select-none backdrop-blur-xl fixed inset-y-0 left-0 w-[var(--w-sidebar)] md:static"
      :class="[
        sidebarCollapsed ? 'md:w-[var(--w-sidebar-collapsed)]' : 'md:w-[var(--w-sidebar)]',
        mobileNavOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0',
      ]"
      style="background-color: var(--surface-sidebar); border-color: var(--line-1)"
    >
      <!-- 品牌头部 -->
      <div
        class="flex h-12 items-center justify-between border-b px-3.5"
        style="border-color: var(--line-1)"
      >
        <!-- 批 43：品牌区此前是 `<div @click>` —— 键盘用户回不到首页，
             屏幕阅读器也不知道它是链接。改成真 `<RouterLink>`（外观靠 a 的类保留）。

             批 98：**折叠态下这个链接没有可访问名** —— 品牌字由
             `v-if="!navCompact"` 控制，折叠时整个字标不渲染，链接里只剩下
             `<img alt="">`（空 alt = 明确声明装饰性）。批 43 的初衷
             （「屏幕阅读器也知道它是链接」）在折叠态其实落空了。
             补 `aria-label` 取品牌名：折叠态有名字；展开态可访问名
             **恰好等于可见字标**，满足 WCAG 2.5.3（Label in Name）。
             用 `brand.name` 而不是新造键 —— 它已是品牌名的单一事实源。 -->
        <RouterLink
          to="/"
          class="flex items-center gap-2.5 min-w-0 cursor-pointer no-underline transition-opacity hover:opacity-85"
          style="color: inherit"
          :aria-label="t('brand.name')"
        >
          <img src="/favicon.svg" alt="" class="h-6 w-6 shrink-0 rounded" />
          <div v-if="!navCompact" class="min-w-0 truncate">
            <div class="flex items-center gap-1.5">
              <span class="font-bold tracking-tight text-sm text-[var(--ink-strong)]">
                {{ t('brand.name') }}
              </span>
              <span class="dsh-status-dot active" :title="t('dash.shell.nav.liveDot')" />
            </div>
          </div>
        </RouterLink>

        <span
          v-if="!navCompact"
          class="rounded px-1.5 py-0.5 text-3xs font-mono font-medium border"
          style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-3)"
        >
          {{ APP_VERSION }}
        </span>
      </div>

      <!-- 工作台频道列表 -->
      <div class="flex-1 overflow-y-auto px-2 py-3 space-y-1">
        <div
          v-if="!navCompact"
          class="px-2 py-1 text-3xs font-semibold uppercase tracking-wider text-[var(--ink-3)]"
        >
          {{ t('dash.shell.nav.groupCore') }}
        </div>

        <!-- 批 100：这一组频道按钮**没有任何 hover 反馈**，而同一列表下面的「文档」
             按钮有 `hover:bg-[var(--surface-2)] hover:text-[var(--ink-1)]`
             —— 相邻两项 hover 行为不同。
             不能直接加 `hover:` 类：原来活动/非活动态写在内联 `:style` 上，
             **内联样式优先级高于工具类**，hover 类不会生效。故改成 `:class`，
             并把 hover 只加在**非活动**项上 —— 与全站既有语汇
             `.seg button:hover:not(.seg-on)` 一致（选中项不因悬停变样）。 -->
        <button
          v-for="tab in publicTabs"
          :key="tab.key"
          type="button"
          class="w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs font-medium cursor-pointer transition-all relative overflow-hidden group"
          :class="
            activeTab === tab.key
              ? 'bg-gradient-to-r from-[var(--brand-bg)] to-transparent text-[var(--ink-strong)] font-semibold border border-[var(--line-2)] shadow-xs'
              : 'text-[var(--ink-2)] hover:bg-[var(--surface-2)] hover:text-[var(--ink-1)] border border-transparent'
          "
          :title="navCompact ? t(tab.labelKey) : undefined"
          :aria-current="activeTab === tab.key ? 'page' : undefined"
          @click="go(tab.path)"
        >
          <span
            v-if="activeTab === tab.key"
            class="absolute left-0 top-1.5 bottom-1.5 w-1 rounded-r-full bg-[var(--brand)] shadow-[0_0_8px_var(--brand)]"
          />
          <component
            :is="tab.icon"
            class="h-4 w-4 shrink-0 transition-all duration-200"
            :class="activeTab === tab.key ? 'text-[var(--brand)] scale-105' : 'opacity-70 group-hover:opacity-100'"
          />
          <span v-if="!navCompact" class="truncate">{{ t(tab.labelKey) }}</span>
          <span
            v-if="!navCompact && activeTab === tab.key"
            class="ms-auto h-1.5 w-1.5 rounded-full bg-[var(--brand)] shadow-[0_0_6px_var(--brand)]"
          />
        </button>

        <div class="my-3 border-t" style="border-color: var(--line-1)" />

        <div
          v-if="!navCompact"
          class="px-2 py-1 text-3xs font-semibold uppercase tracking-wider text-[var(--ink-3)]"
        >
          {{ t('dash.shell.nav.groupRef') }}
        </div>

        <button type="button"
          class="w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs font-medium text-[var(--ink-2)] hover:bg-[var(--surface-2)] hover:text-[var(--ink-1)] border border-transparent cursor-pointer transition-all"
          :title="navCompact ? t('dash.shell.nav.docsTitle') : undefined"
          :aria-current="route.path === '/docs' ? 'page' : undefined"
          @click="go('/docs')"
        >
          <BookOpen class="h-4 w-4 shrink-0" />
          <span v-if="!navCompact">{{ t('dash.shell.nav.docs') }}</span>
        </button>
      </div>

      <!-- 侧边栏底栏：折叠控制器与关于入口 -->
      <div
        class="border-t p-2 flex items-center justify-between"
        style="border-color: var(--line-1); background-color: var(--surface-sidebar)"
      >
        <button type="button"
          class="btn btn-quiet btn-icon h-7 w-7 cursor-pointer"
          :title="isNarrow ? t('dash.shell.nav.closeMobile') : (navCompact ? t('dash.shell.nav.expand') : t('dash.shell.nav.collapse'))"
          :aria-label="isNarrow ? t('dash.shell.nav.closeMobile') : (navCompact ? t('dash.shell.nav.expand') : t('dash.shell.nav.collapse'))"
          :aria-expanded="navExpanded"
          :aria-controls="'dashboard-sidebar'"
          @click="toggleNav"
        >
          <X v-if="isNarrow" class="h-3.5 w-3.5" />
          <ChevronRight v-else-if="navCompact" class="h-3.5 w-3.5" />
          <ChevronLeft v-else class="h-3.5 w-3.5" />
        </button>

        <button type="button"
          v-if="!navCompact"
          class="btn btn-quiet h-7 px-2 text-3xs font-medium cursor-pointer"
          @click="aboutOpen = true"
        >
          {{ t('dash.shell.nav.about') }}
        </button>
      </div>
    </aside>

    <!-- 窄屏抽屉遮罩：点空白处收起（低于抽屉 z-50、低于顶栏 z-[60]） -->
    <div
      v-if="isNarrow && mobileNavOpen"
      class="fixed inset-0 z-30 md:hidden"
      style="background-color: var(--overlay-scrim)"
      @click="mobileNavOpen = false"
    />

    <!-- 右侧：主舞台区（顶部控制条 + 各视图内容画布） -->
    <div class="flex-1 flex flex-col min-w-0 h-screen overflow-hidden">
      <!-- 顶部工作台状态条 -->
      <TopBar
        :nav-expanded="navExpanded"
        @toggle-sidebar="toggleNav"
      />

      <!-- 主工作区滚动容器 -->
      <main
        id="main-content"
        tabindex="-1"
        class="flex-1 overflow-y-auto overflow-x-hidden min-w-0 p-3 sm:p-4 pb-20 md:pb-6 outline-none"
      >
        <div class="mx-auto w-full max-w-[2048px]">
          <KeepAlive :max="5">
            <MatrixView v-if="activeTab === 'trading'" key="trading" />
            <RadarView v-else-if="activeTab === 'factors'" key="factors" />
            <NewsView v-else-if="activeTab === 'news'" key="news" />
            <EvolutionView v-else-if="activeTab === 'lab'" key="lab" />
            <LedgerView v-else-if="activeTab === 'history'" key="history" />
          </KeepAlive>
        </div>
      </main>

      <!-- 移动端底部导航栏 -->
      <MobileTabBar class="md:hidden" />
    </div>

    <!-- 全局浮层组件 -->
    <TrajectoryPanel :open="trajectoryOpen" @close="trajectoryOpen = false" />
    <AboutModal />
  </div>
</template>

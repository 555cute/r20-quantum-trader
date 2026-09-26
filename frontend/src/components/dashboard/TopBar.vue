<script setup lang="ts">
/**
 * TopBar.vue · 交易工作台顶部控制条
 * ---------------------------------------------------------------------------
 * 极简精炼重构：
 * 1. 移除冗余图标按钮（文档与控制台已完整位于左侧导航栏，无需在顶栏二次堆砌）
 * 2. 移除居中长串秒级北京时间时钟，释放呼吸空间
 * 3. 紧凑集成：导航展开/收起 + 页面面包屑 + 聚合运行状态 + 决策轨迹 + 偏好设置
 * 4. 2026-09-16 用户反馈：删除「搜索 / 命令 ⌘K」——实测无人使用，纯占位；
 *    后台入口统一收敛到最右 ⚙ 偏好设置弹层里的「管理控制台」。
 */
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import {
  Activity,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-vue-next';
import { publicTabs } from '../../config/nav';
import { useI18n } from '../../composables/useI18n';
import { useUi } from '../../composables/useUi';
import DataStatus from './DataStatus.vue';
import SettingsPopover from './SettingsPopover.vue';

defineProps<{
  /** 导航当前是否可见（桌面=侧栏未折叠；窄屏=抽屉已打开）。 */
  navExpanded?: boolean;
}>();

const emit = defineEmits<{
  (e: 'toggleSidebar'): void;
}>();

const route = useRoute();
const { t } = useI18n();
const { trajectoryOpen } = useUi();
const activeTab = computed(() => {
  const tabKey = (route.meta?.tab as string) || 'trading';
  return publicTabs.find((t) => t.key === tabKey) || publicTabs[0];
});
</script>

<template>
  <header
    class="relative z-[60] flex h-12 w-full shrink-0 items-center justify-between border-b px-3 sm:px-4 backdrop-blur-xl transition-colors"
    style="background-color: var(--surface-header); border-color: var(--line-1); color: var(--ink-1)"
  >
    <!-- 左侧：侧栏切换 + 面包屑 + 核心聚合状态 -->
    <div class="flex items-center gap-2.5 sm:gap-3.5">
      <button type="button"
        class="btn btn-quiet btn-icon h-[var(--h-md)] w-[var(--h-md)] cursor-pointer text-[var(--ink-2)] hover:text-[var(--ink-strong)]"
        :title="navExpanded ? t('dash.shell.nav.closeNav') : t('dash.shell.nav.openNav')"
        :aria-label="navExpanded ? t('dash.shell.nav.closeNav') : t('dash.shell.nav.openNav')"
        :aria-expanded="!!navExpanded"
        :aria-controls="'dashboard-sidebar'"
        @click="emit('toggleSidebar')"
      >
        <PanelLeftClose v-if="navExpanded" class="h-4 w-4" />
        <PanelLeftOpen v-else class="h-4 w-4" />
      </button>

      <!-- 工作台面包屑与当前频道 -->
      <div class="flex items-center gap-2">
        <span class="font-bold tracking-tight text-sm" style="color: var(--ink-strong)">
          <span class="sm:hidden">AstraQuant</span>
          <span class="hidden sm:inline">{{ t('brand.name') }}</span>
        </span>
        <span class="hidden sm:inline text-xs" style="color: var(--ink-3)">/</span>
        <span class="hidden sm:flex items-center gap-1.5 text-xs font-medium" style="color: var(--ink-1)">
          <component :is="activeTab.icon" class="h-3.5 w-3.5 text-blue-400" />
          {{ t(activeTab.labelKey) }}
        </span>
      </div>

      <span class="hidden h-3.5 w-px sm:block" style="background-color: var(--line-1)" />

      <!-- 数据与引擎在线状态（一体化微胶囊） -->
      <DataStatus class="hidden sm:flex" />
    </div>

    <!-- 右侧：决策轨迹 + 偏好设置（后台入口统一在 ⚙ 内） -->
    <div class="flex items-center gap-2">
      <!-- 决策轨迹流入口 -->
      <button type="button"
        class="inline-flex h-[var(--h-md)] items-center gap-1.5 rounded-full border px-3 text-xs font-medium cursor-pointer transition-all hover:border-[var(--line-2)] hover:bg-[var(--surface-3)]"
        style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-1)"
        :title="`${t('dash.shell.trajectoryBtn')} (⌘J)`"
        @click="trajectoryOpen = true"
      >
        <span class="dsh-status-dot active" aria-hidden="true" />
        <Activity class="h-3.5 w-3.5 text-blue-400" />
        <span>{{ t('dash.shell.trajectoryBtn') }}</span>
        <kbd class="hidden sm:inline-flex ml-0.5">⌘J</kbd>
      </button>

      <!-- 偏好设置 -->
      <SettingsPopover />
    </div>
  </header>
</template>

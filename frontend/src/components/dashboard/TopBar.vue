<script setup lang="ts">
import BeijingClock from '../base/BeijingClock.vue';
/** 前台顶栏：品牌 / 5 tab / 决策透视 / ⌘K / 主题 / 偏好弹层 */
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Moon, Sun, Eye, BookOpen, LayoutDashboard } from 'lucide-vue-next';
import { publicTabs } from '../../config/nav';
import { useI18n } from '../../composables/useI18n';
import { useTheme } from '../../composables/useTheme';
import { useUi } from '../../composables/useUi';
import { APP_VERSION, APP_NAME } from '../../config/version';
import SettingsPopover from './SettingsPopover.vue';

const route = useRoute();
const router = useRouter();
const { t } = useI18n();
const { theme, toggleTheme } = useTheme();
const { peekOpen, aboutOpen } = useUi();

const isDark = computed(() => theme.value === 'dark');
const activeKey = computed(() => (route.meta?.tab as string) || 'trading');

function go(path: string) {
  if (route.path !== path) router.push(path);
}
</script>

<template>
  <header
    class="fixed inset-x-0 top-0 z-[var(--z-header)] h-12 border-b backdrop-blur-xl"
    style="background-color: var(--surface-header); border-color: var(--line-1)"
  >
    <div class="mx-auto flex h-full max-w-[2048px] items-center gap-3 px-3 sm:px-5">
      <!-- 品牌区 -->
      <div class="flex min-w-0 items-center gap-2.5">
        <img src="/favicon.svg" alt="" class="h-7 w-7 shrink-0 rounded-md" />
        <button class="truncate text-md font-bold tracking-tight cursor-pointer" style="color: var(--ink-strong)" @click="go('/')">
          {{ APP_NAME }}
        </button>
        <button
          class="badge badge-accent badge-mono hidden shrink-0 cursor-pointer sm:inline-flex"
          :title="t('dash.about.title')"
          @click="aboutOpen = true"
        >
          {{ APP_VERSION }}
        </button>
      </div>

      <!-- 中央导航（桌面） -->
      <nav class="absolute left-1/2 hidden -translate-x-1/2 items-center gap-0.5 md:flex" aria-label="primary">
        <button
          v-for="tab in publicTabs"
          :key="tab.key"
          class="flex h-8 cursor-pointer items-center gap-1.5 rounded-lg px-3 text-sm font-medium transition-colors"
          :style="
            activeKey === tab.key
              ? { backgroundColor: 'var(--surface-3)', color: 'var(--ink-strong)' }
              : { color: 'var(--ink-2)' }
          "
          :aria-current="activeKey === tab.key ? 'page' : undefined"
          @click="go(tab.path)"
        >
          <component :is="tab.icon" class="h-4 w-4" />
          {{ t(tab.labelKey) }}
        </button>
      </nav>

      <!-- 右侧动作区 -->
      <div class="ms-auto flex items-center gap-1">
        <BeijingClock class="mx-1 md:hidden min-[1200px]:block" />
        <button class="btn btn-quiet btn-icon hidden md:inline-flex" :title="t('nav.actions.docs')" @click="go('/docs')">
          <BookOpen class="h-4 w-4" />
        </button>
        <button class="btn btn-quiet btn-icon hidden md:inline-flex" :title="t('nav.actions.console')" @click="go('/admin')">
          <LayoutDashboard class="h-4 w-4" />
        </button>
        <span class="mx-1 hidden h-5 w-px md:block" style="background-color: var(--line-2)" />
        <button class="btn btn-quiet btn-icon hidden md:inline-flex" :title="t('nav.actions.promptPeek')" @click="peekOpen = true">
          <Eye class="h-4 w-4" />
        </button>
        <button class="btn btn-quiet btn-icon" :title="t('dash.shell.settings.theme')" @click="toggleTheme">
          <Sun v-if="isDark" class="h-4 w-4" />
          <Moon v-else class="h-4 w-4" />
        </button>
        <SettingsPopover />
      </div>
    </div>
  </header>
</template>

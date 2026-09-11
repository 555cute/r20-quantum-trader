<script setup lang="ts">
/** 前台顶栏：品牌 / 三所通信中枢 / 5 tab / 决策透视 / ⌘K / 主题 / 偏好弹层 */
import { computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Moon, Sun, Eye, BookOpen, LayoutDashboard } from 'lucide-vue-next';
import { publicTabs } from '../../config/nav';
import { useI18n } from '../../composables/useI18n';
import { useTheme } from '../../composables/useTheme';
import { useUi } from '../../composables/useUi';
import { APP_VERSION, APP_NAME } from '../../config/version';
import BeijingClock from '../base/BeijingClock.vue';
import SettingsPopover from './SettingsPopover.vue';
import VenueCapsule from './VenueCapsule.vue';

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
    class="fixed inset-x-0 top-0 z-[var(--z-header)] h-12 border-b backdrop-blur-2xl transition-colors duration-200"
    style="
      background-color: var(--surface-header);
      border-color: var(--line-1);
      box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.4);
    "
  >
    <div class="mx-auto flex h-full max-w-[2048px] items-center gap-2.5 px-3 sm:gap-3.5 sm:px-5">
      <!-- 品牌区 -->
      <div class="flex min-w-0 items-center gap-1.5 sm:gap-2">
        <button
          type="button"
          class="flex cursor-pointer items-center gap-1.5 sm:gap-2 transition-opacity hover:opacity-90"
          @click="go('/')"
        >
          <img src="/favicon.svg" alt="R20" class="h-6 w-6 shrink-0 rounded-md shadow-sm sm:h-7 sm:w-7" />
          <span class="truncate text-sm font-bold tracking-tight hidden sm:inline" style="color: var(--ink-strong)">
            {{ APP_NAME }}
          </span>
          <span class="text-xs font-bold tracking-tight sm:hidden" style="color: var(--ink-strong)">
            R20
          </span>
        </button>

        <button
          type="button"
          class="badge badge-accent badge-mono hidden shrink-0 cursor-pointer transition-all hover:brightness-110 sm:inline-flex"
          :title="t('dash.about.title')"
          @click="aboutOpen = true"
        >
          {{ APP_VERSION }}
        </button>
      </div>

      <!-- 中央导航（桌面端）：活动项带精致微光底衬与高光 -->
      <nav class="absolute left-1/2 hidden -translate-x-1/2 items-center gap-1 lg:flex" aria-label="primary">
        <button
          v-for="tab in publicTabs"
          :key="tab.key"
          type="button"
          class="relative flex h-8 cursor-pointer items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-all duration-200"
          :style="
            activeKey === tab.key
              ? {
                  backgroundColor: 'var(--surface-3)',
                  color: 'var(--ink-strong)',
                  boxShadow: '0 0 16px -3px var(--glow-accent, rgba(247, 147, 26, 0.25)), inset 0 1px 0 rgba(255, 255, 255, 0.08)',
                  borderColor: 'var(--line-2)',
                }
              : { color: 'var(--ink-2)' }
          "
          :aria-current="activeKey === tab.key ? 'page' : undefined"
          @click="go(tab.path)"
        >
          <component :is="tab.icon" class="h-3.5 w-3.5 transition-transform duration-200" :class="{ 'scale-110 text-[var(--accent)]': activeKey === tab.key }" />
          <span>{{ t(tab.labelKey) }}</span>
          <!-- 底部微光条 -->
          <span
            v-if="activeKey === tab.key"
            class="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-[var(--accent)]"
            style="box-shadow: 0 0 8px var(--accent)"
          />
        </button>
      </nav>

      <!-- 右侧动作区 -->
      <div class="ms-auto flex items-center gap-1 sm:gap-1.5">
        <!-- 三所对等连接胶囊 -->
        <VenueCapsule />

        <!-- 时钟（宽屏常驻） -->
        <BeijingClock class="mx-1 hidden xl:block" />

        <span class="mx-1 hidden h-4 w-px xl:block" style="background-color: var(--line-1)" />

        <!-- 常用导航入口 -->
        <button
          type="button"
          class="btn btn-quiet btn-icon hidden md:inline-flex"
          :title="t('nav.actions.docs')"
          @click="go('/docs')"
        >
          <BookOpen class="h-4 w-4" />
        </button>
        <button
          type="button"
          class="btn btn-quiet btn-icon hidden md:inline-flex"
          :title="t('nav.actions.console')"
          @click="go('/admin')"
        >
          <LayoutDashboard class="h-4 w-4" />
        </button>

        <span class="mx-0.5 hidden h-4 w-px md:block" style="background-color: var(--line-1)" />

        <!-- 决策透视 -->
        <button
          type="button"
          class="btn btn-quiet btn-icon hidden md:inline-flex"
          :title="t('nav.actions.promptPeek')"
          @click="peekOpen = true"
        >
          <Eye class="h-4 w-4" />
        </button>

        <!-- 主题切换 -->
        <button
          type="button"
          class="btn btn-quiet btn-icon"
          :title="t('dash.shell.settings.theme')"
          @click="toggleTheme"
        >
          <Sun v-if="isDark" class="h-4 w-4 text-[var(--warn)]" />
          <Moon v-else class="h-4 w-4 text-[var(--ink-2)]" />
        </button>

        <!-- 偏好设置 -->
        <SettingsPopover />
      </div>
    </div>
  </header>
</template>

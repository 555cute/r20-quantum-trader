<script setup lang="ts">
/** 移动端底部标签栏：磨砂玻璃 + 触控微光 + 活动项高亮指示 */
import { useRoute, useRouter } from 'vue-router';
import { computed } from 'vue';
import { publicTabs } from '../../config/nav';
import { useI18n } from '../../composables/useI18n';

const route = useRoute();
const router = useRouter();
const { t } = useI18n();
const activeKey = computed(() => (route.meta?.tab as string) || 'trading');
</script>

<template>
  <nav
    class="fixed inset-x-0 bottom-0 z-[var(--z-header)] border-t backdrop-blur-2xl transition-all duration-200 lg:hidden"
    style="
      background-color: var(--surface-header);
      border-color: var(--line-1);
      padding-bottom: env(safe-area-inset-bottom);
      box-shadow: 0 -4px 20px -2px rgba(0, 0, 0, 0.35);
    "
    aria-label="mobile primary"
  >
    <div class="mx-auto flex max-w-md items-stretch justify-around px-1">
      <button
        v-for="tab in publicTabs"
        :key="tab.key"
        type="button"
        class="relative flex flex-1 cursor-pointer flex-col items-center gap-0.5 py-2 text-2xs font-medium transition-all duration-200"
        :style="{
          color: activeKey === tab.key ? 'var(--accent)' : 'var(--ink-2)',
        }"
        @click="router.push(tab.path)"
      >
        <!-- 活动项顶部高光微线条 -->
        <span
          v-if="activeKey === tab.key"
          class="absolute inset-x-3 top-0 h-0.5 rounded-full bg-[var(--accent)]"
          style="box-shadow: 0 0 8px var(--accent)"
        />

        <div
          class="flex h-6 w-6 items-center justify-center rounded-lg transition-transform duration-200"
          :class="{ 'scale-110 bg-[var(--surface-3)] text-[var(--accent)]': activeKey === tab.key }"
        >
          <component :is="tab.icon" class="h-4 w-4" />
        </div>
        <span class="tracking-tight">{{ t(tab.labelKey) }}</span>
      </button>
    </div>
  </nav>
</template>

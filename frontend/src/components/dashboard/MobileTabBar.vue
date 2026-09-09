<script setup lang="ts">
/** 移动端底部标签栏 */
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
    class="fixed inset-x-0 bottom-0 z-[var(--z-header)] border-t backdrop-blur-xl md:hidden"
    style="background-color: var(--surface-header); border-color: var(--line-1); padding-bottom: env(safe-area-inset-bottom)"
    aria-label="mobile primary"
  >
    <div class="mx-auto flex max-w-md items-stretch justify-around">
      <button
        v-for="tab in publicTabs"
        :key="tab.key"
        class="flex flex-1 cursor-pointer flex-col items-center gap-0.5 py-1.5 text-2xs font-medium transition-colors"
        :style="{ color: activeKey === tab.key ? 'var(--accent)' : 'var(--ink-2)' }"
        @click="router.push(tab.path)"
      >
        <component :is="tab.icon" class="h-[18px] w-[18px]" />
        <span>{{ t(tab.labelKey) }}</span>
      </button>
    </div>
  </nav>
</template>

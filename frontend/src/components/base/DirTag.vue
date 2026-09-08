<script setup lang="ts">
/** 方向标签：多/空/观望 —— 永远带 ▲▼ 符号，不依赖颜色语义 */
import { computed } from 'vue';
import { useI18n } from '../../composables/useI18n';

const props = defineProps<{ dir: 'long' | 'short' | 'flat' | string; size?: 'sm' | 'md' }>();
const { t } = useI18n();

const norm = computed<'long' | 'short' | 'flat'>(() => {
  const d = String(props.dir || '').toUpperCase();
  if (d.includes('LONG') || d === 'BUY' || d === 'B') return 'long';
  if (d.includes('SHORT') || d === 'SELL' || d === 'S') return 'short';
  return 'flat';
});
const glyph = computed(() => ({ long: '▲', short: '▼', flat: '—' })[norm.value]);
const label = computed(() => t(`common.dir.${norm.value}`));
</script>

<template>
  <span class="dir" :class="`dir-${norm}`">
    <span aria-hidden="true">{{ glyph }}</span>{{ label }}
  </span>
</template>

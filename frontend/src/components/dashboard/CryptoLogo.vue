<script setup lang="ts">
/** 币种头像：字母徽标 + 按 symbol 哈希的稳定色相（无外部图片依赖） */
import { computed } from 'vue';

const props = withDefaults(defineProps<{ symbol?: string; size?: number }>(), { size: 20 });

const HUES = [212, 265, 32, 160, 340, 190, 285, 100, 12, 230];

const hue = computed(() => {
  const s = String(props.symbol || 'R');
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 997;
  return HUES[h % HUES.length];
});
</script>

<template>
  <span
    class="inline-flex shrink-0 select-none items-center justify-center rounded-md font-bold"
    :style="{
      width: size + 'px',
      height: size + 'px',
      fontSize: Math.max(9, size * 0.52) + 'px',
      lineHeight: 1,
      backgroundColor: `hsl(${hue} 60% 50% / 0.14)`,
      color: `hsl(${hue} 70% 62%)`,
      border: `1px solid hsl(${hue} 60% 55% / 0.3)`,
    }"
    aria-hidden="true"
  >
    {{ (symbol || '?').slice(0, 1) }}
  </span>
</template>

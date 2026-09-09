<script setup lang="ts">
/** SVG 迷你走势线：无依赖、tabular 场景专用；自动按数据正负取涨跌色 */
import { computed } from 'vue';

const props = withDefaults(
  defineProps<{
    values: (number | null)[];
    width?: number;
    height?: number;
    /** 'auto' 末值>=首值取涨色，否则跌色 */
    color?: 'auto' | 'up' | 'down' | 'accent' | 'muted';
    area?: boolean;
  }>(),
  { width: 72, height: 26, color: 'auto', area: true },
);

const clean = computed(() => props.values.filter((v): v is number => v !== null && Number.isFinite(v)));

const path = computed(() => {
  const vs = clean.value;
  if (vs.length < 2) return { line: '', fill: '' };
  const min = Math.min(...vs);
  const max = Math.max(...vs);
  const span = max - min || 1;
  const step = props.width / (vs.length - 1);
  const pts = vs.map((v, i) => [i * step, props.height - 2 - ((v - min) / span) * (props.height - 4)] as const);
  const line = pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const fill = `${line} L${props.width},${props.height} L0,${props.height} Z`;
  return { line, fill };
});

const stroke = computed(() => {
  const vs = clean.value;
  if (props.color === 'up') return 'var(--up)';
  if (props.color === 'down') return 'var(--down)';
  if (props.color === 'accent') return 'var(--accent)';
  if (props.color === 'muted') return 'var(--ink-2)';
  return vs.length >= 2 && vs[vs.length - 1] >= vs[0] ? 'var(--up)' : 'var(--down)';
});
</script>

<template>
  <svg
    v-if="path.line"
    :width="width"
    :height="height"
    :viewBox="`0 0 ${width} ${height}`"
    class="shrink-0"
    aria-hidden="true"
  >
    <path v-if="area" :d="path.fill" :fill="stroke" opacity="0.08" />
    <path :d="path.line" fill="none" :stroke="stroke" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" />
  </svg>
</template>

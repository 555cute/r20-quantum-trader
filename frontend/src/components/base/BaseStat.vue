<script setup lang="ts">
/** KPI 单元：标签 + 大数字 + 副值(涨跌) + 可选 sparkline。KPI 必带 Δ 或走势，禁裸数字。 */
withDefaults(
  defineProps<{
    label: string;
    value: string;
    /** 副值文本（已含符号），配合 deltaTone 上色 */
    delta?: string;
    deltaTone?: 'up' | 'down' | 'muted' | 'warn';
    hint?: string;
  }>(),
  { deltaTone: 'muted' },
);

const toneVar = {
  up: 'var(--up)',
  down: 'var(--down)',
  warn: 'var(--warn)',
  muted: 'var(--ink-2)',
} as const;
</script>

<template>
  <div class="kpi-cell flex min-w-0 flex-col justify-center gap-0.5 overflow-hidden px-4 py-2.5" :title="hint">
    <span class="t-label truncate">{{ label }}</span>
    <div class="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0">
      <span class="num truncate text-lg font-bold leading-tight xl:text-xl" style="color: var(--ink-strong)">{{ value }}</span>
      <span v-if="delta" class="num shrink-0 text-xs font-semibold" :style="{ color: toneVar[deltaTone] }">{{ delta }}</span>
      <div class="ms-auto flex shrink-0 items-center"><slot name="extra" /></div>
    </div>
  </div>
</template>

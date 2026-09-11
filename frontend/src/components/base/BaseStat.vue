<script setup lang="ts">
/**
 * KPI 单元：标签 + 大数字 + 副值(涨跌) + 可选走势。KPI 必带 Δ 或走势，禁裸数字。
 * 视觉基座：--surface-1 内凹井 + 1px 微描边 + 顶部内高光；数值永远等宽。
 */
import BaseSparkline from './BaseSparkline.vue';

withDefaults(
  defineProps<{
    label: string;
    value: string;
    /** 副值文本（已含符号），配合 deltaTone 上色 */
    delta?: string;
    deltaTone?: 'up' | 'down' | 'muted' | 'warn';
    hint?: string;
    /** 单位后缀（如 U / % / 笔），弱化字色，不与主数值抢注意力 */
    unit?: string;
    /** 迷你走势数据：给了就在右侧画，KPI「必带走势」的默认承载位 */
    spark?: (number | null)[];
    sparkColor?: 'auto' | 'up' | 'down' | 'accent' | 'muted';
  }>(),
  { deltaTone: 'muted', sparkColor: 'auto' },
);

const toneVar = {
  up: 'var(--up)',
  down: 'var(--down)',
  warn: 'var(--warn)',
  muted: 'var(--ink-2)',
} as const;
</script>

<template>
  <div class="kpi-cell flex min-w-0 flex-col justify-center gap-1 overflow-hidden px-4 py-2.5" :title="hint">
    <span class="t-label truncate">{{ label }}</span>
    <div class="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0">
      <span
        class="num min-w-0 truncate text-lg font-bold leading-tight xl:text-xl"
        style="color: var(--ink-strong)"
        >{{ value }}</span
      >
      <span v-if="unit" class="num shrink-0 text-2xs font-medium" style="color: var(--ink-3)">{{ unit }}</span>
      <span
        v-if="delta"
        class="num shrink-0 text-2xs font-semibold"
        :style="{ color: toneVar[deltaTone] }"
        >{{ delta }}</span
      >
      <div class="ms-auto flex shrink-0 items-center gap-1.5">
        <BaseSparkline v-if="spark && spark.length > 1" :values="spark" :color="sparkColor" :width="60" :height="20" />
        <slot name="extra" />
      </div>
    </div>
  </div>
</template>

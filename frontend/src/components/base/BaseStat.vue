<script setup lang="ts">
/**
 * BaseStat.vue · DeepSeek Harness 风格 KPI 指标单元
 * 包含：微标头、大号等宽数理数值、动态变动副值、走势图插槽与可展开释义
 *
 * ⚠️ 左侧内边距必须 = 卡片头 `.dsh-card-header`（16px）与 `.fact` 单元（16px）：
 *    全站卡片正文只有**一条 16px 左轨**。此前这里是 `px-3.5`（14px，且 14 不在
 *    密集尺度刻度 2/4/6/8/10/12/16 上）⇒ 卡片头标题起点 16px、KPI 数值起点 14px，
 *    每个统计条都带 2px 左轨错位，整列扫视时读数发虚。见 `px-4`。
 */
import { ref, useId } from 'vue';

withDefaults(
  defineProps<{
    label: string;
    value: string;
    delta?: string;
    deltaTone?: 'up' | 'down' | 'muted' | 'warn';
    hint?: string;
  }>(),
  { deltaTone: 'muted' },
);

const showHint = ref(false);
const hintId = useId();

const toneVar = {
  up: 'var(--up)',
  down: 'var(--down)',
  warn: 'var(--warn)',
  muted: 'var(--ink-2)',
} as const;
</script>

<template>
  <div class="group flex min-w-0 flex-col justify-center gap-1 overflow-hidden px-4 py-2.5 select-none" :title="hint">
    <div class="flex min-w-0 items-center justify-between gap-1">
      <span class="truncate text-3xs font-semibold uppercase tracking-wider text-[var(--ink-3)]">{{ label }}</span>
      <!-- 批 85：`aria-controls` 的目标 `<p :id="hintId">` 是 `v-if="hint && showHint"`
           —— 收起时它不在 DOM 里，此时仍输出 aria-controls 就是**悬空引用**
           （ARIA 要求被引用元素存在）。与 BaseTabs 既有约定一致：
           目标不在就不输出该属性，展开态由 aria-expanded 表达。 -->
      <button
        v-if="hint"
        type="button"
        class="kpi-hint shrink-0 cursor-pointer opacity-0 group-hover:opacity-60 hover:!opacity-100 focus-visible:opacity-100 group-focus-within:opacity-60"
        :aria-label="hint"
        :aria-expanded="showHint"
        :aria-controls="showHint ? hintId : undefined"
        @click.stop="showHint = !showHint"
      >
        i
      </button>
    </div>

    <div class="flex min-w-0 flex-wrap items-baseline justify-between gap-1">
      <span class="num font-mono truncate text-lg font-bold leading-tight text-[var(--ink-strong)]">{{ value }}</span>
      <div class="flex items-center gap-1.5 shrink-0">
        <span v-if="delta" class="num font-mono text-3xs font-semibold" :style="{ color: toneVar[deltaTone] }">{{ delta }}</span>
        <slot name="extra" />
      </div>
    </div>

    <p v-if="hint && showHint" :id="hintId" class="text-4xs leading-snug font-sans text-[var(--ink-2)] mt-0.5">{{ hint }}</p>
  </div>
</template>

<style scoped>
.kpi-hint {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 13px;
  height: 13px;
  padding: 0;
  border-radius: var(--r-pill);
  border: 1px solid var(--line-1);
  background: var(--surface-2);
  color: var(--ink-3);
  font-family: var(--font-mono);
  /* 批 28：原为写死的 9px（全站唯一 9px 文本），改回档位；
     与工具栏其它小字同档，也是「不再有 11px 以下文本」的一部分。 */
  font-size: var(--text-4xs);
  font-style: italic;
  font-weight: 700;
  line-height: 1;
  transition: all var(--dur-fast);
}
/* 批 18：可见圆点保持 13px（不破坏指标行节奏），命中区用透明伪元素撑到 25px */
.kpi-hint::after {
  content: '';
  position: absolute;
  inset: -6px;
}
.kpi-hint:hover {
  background: var(--surface-3);
  color: var(--ink-1);
  border-color: var(--line-2);
}
</style>

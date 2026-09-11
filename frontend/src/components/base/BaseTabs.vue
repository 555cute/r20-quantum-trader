<script setup lang="ts">
/**
 * 下划线选项卡：抽屉/详情页内分区切换。
 * 视觉：激活项 ink-strong + 品牌下划线（带滑入动画），计数永远等宽且不与标签抢位。
 * 可达：role=tablist + ←/→ 键盘切换（tab 键仍可逐项聚焦）。
 */
import type { Component } from 'vue';

const props = defineProps<{
  modelValue: string;
  items: { key: string; label: string; count?: number; icon?: Component; title?: string }[];
  /** 撑满容器宽度（窄面板内均分用） */
  fill?: boolean;
}>();
const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>();

function move(delta: number) {
  const items = props.items || [];
  if (!items.length) return;
  const idx = items.findIndex((i) => i.key === props.modelValue);
  const next = items[Math.min(items.length - 1, Math.max(0, (idx < 0 ? 0 : idx) + delta))];
  if (next && next.key !== props.modelValue) emit('update:modelValue', next.key);
}
</script>

<template>
  <div
    class="flex items-center gap-0.5 overflow-x-auto"
    :class="fill && 'w-full'"
    style="border-bottom: 1px solid var(--line-1)"
    role="tablist"
    @keydown.left.prevent="move(-1)"
    @keydown.right.prevent="move(1)"
  >
    <button
      v-for="it in items"
      :key="it.key"
      role="tab"
      :title="it.title"
      class="base-tab relative shrink-0 cursor-pointer px-3 py-2 text-sm font-medium"
      :class="[modelValue === it.key ? 'is-on' : '', fill && 'flex-1']"
      :aria-selected="modelValue === it.key"
      :tabindex="modelValue === it.key ? 0 : -1"
      @click="emit('update:modelValue', it.key)"
    >
      <span class="inline-flex items-center justify-center gap-1.5">
        <component v-if="it.icon" :is="it.icon" class="h-3.5 w-3.5 shrink-0 opacity-80" />
        <span class="truncate">{{ it.label }}</span>
        <span v-if="it.count !== undefined" class="num rounded px-1 text-2xs" style="color: var(--ink-3)">{{ it.count }}</span>
      </span>
      <span class="base-tab-ink" aria-hidden="true" />
    </button>
  </div>
</template>

<style scoped>
.base-tab {
  color: var(--ink-2);
  transition: color var(--dur-fast) var(--ease-out), background-color var(--dur-fast) var(--ease-out);
}
.base-tab:hover { color: var(--ink-1); background-color: color-mix(in srgb, var(--surface-3) 55%, transparent); }
.base-tab.is-on { color: var(--ink-strong); font-weight: 600; }
.base-tab-ink {
  position: absolute;
  inset-inline: 8px;
  bottom: -1px;
  height: 2px;
  border-radius: 2px;
  background-color: var(--accent);
  box-shadow: var(--glow-accent);
  transform: scaleX(0.4);
  opacity: 0;
  transition: transform var(--dur-base) var(--ease-out), opacity var(--dur-base) var(--ease-out);
}
.base-tab.is-on .base-tab-ink { transform: scaleX(1); opacity: 1; }
</style>

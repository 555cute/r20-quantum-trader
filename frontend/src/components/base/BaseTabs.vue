<script setup lang="ts">
/** 下划线选项卡：抽屉/详情页内分区切换 */
defineProps<{
  modelValue: string;
  items: { key: string; label: string; count?: number }[];
}>();
const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>();
</script>

<template>
  <div class="flex items-center gap-1 overflow-x-auto" style="border-bottom: 1px solid var(--line-1)" role="tablist">
    <button
      v-for="it in items"
      :key="it.key"
      role="tab"
      class="relative shrink-0 px-3 py-2 text-sm font-medium transition-colors cursor-pointer"
      :style="{
        color: modelValue === it.key ? 'var(--ink-strong)' : 'var(--ink-2)',
      }"
      :aria-selected="modelValue === it.key"
      @click="emit('update:modelValue', it.key)"
    >
      {{ it.label }}
      <span v-if="it.count !== undefined" class="num ml-1 text-xs" style="color: var(--ink-3)">{{ it.count }}</span>
      <span
        v-if="modelValue === it.key"
        class="absolute inset-x-2 -bottom-px h-0.5 rounded-full"
        style="background-color: var(--accent)"
      />
    </button>
  </div>
</template>

<script setup lang="ts">
/** 空态：图标环 + 主文案 + 辅助说明 + 可选操作 */
import { Inbox } from 'lucide-vue-next';
import type { Component } from 'vue';

withDefaults(defineProps<{ text: string; desc?: string; icon?: Component; compact?: boolean }>(), {});
</script>

<template>
  <div class="empty" :class="compact && 'py-6'">
    <span class="empty-icon">
      <component :is="icon || Inbox" class="h-5 w-5" />
    </span>
    <p style="color: var(--ink-2)">{{ text }}</p>
    <p v-if="desc" class="max-w-[42ch] text-xs leading-relaxed" style="color: var(--ink-3)">{{ desc }}</p>
    <div v-if="$slots.action" class="mt-2"><slot name="action" /></div>
  </div>
</template>

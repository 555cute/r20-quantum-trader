<script setup lang="ts">
/** 空态：图标 + 主文案 + 辅助说明 + 可选操作 */
import { Inbox } from 'lucide-vue-next';
import type { Component } from 'vue';

withDefaults(defineProps<{ text: string; desc?: string; icon?: Component }>(), {});
</script>

<template>
  <div class="empty">
    <component :is="icon || Inbox" class="h-6 w-6" style="color: var(--line-3)" />
    <p>{{ text }}</p>
    <p v-if="desc" class="text-xs" style="color: var(--ink-3)">{{ desc }}</p>
    <div v-if="$slots.action" class="mt-2"><slot name="action" /></div>
  </div>
</template>

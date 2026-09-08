<script setup lang="ts">
/** 代码块：滚动 + 复制角标 */
import CopyButton from './CopyButton.vue';
withDefaults(defineProps<{ code: string; lang?: string; maxHeight?: string }>(), { maxHeight: '320px' });
</script>

<template>
  <div class="relative group">
    <pre class="code-block" :style="{ maxHeight }">{{ code }}</pre>
    <div class="absolute right-1.5 top-1.5 opacity-0 transition-opacity group-hover:opacity-100">
      <CopyButton :text="code" />
    </div>
  </div>
</template>

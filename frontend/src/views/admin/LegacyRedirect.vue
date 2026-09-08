<script setup lang="ts">
import { onMounted, ref } from 'vue'

const currentPath = ref(window.location.pathname)

onMounted(() => {
  // Redirect to legacy admin.html for pages not yet migrated
  window.location.href = '/admin/legacy.html#' + currentPath.value
})
</script>

<template>
  <div class="py-12 text-center">
    <p class="text-xs font-mono text-[var(--text-faint)]">该页面正在迁移中，正在跳转到旧版管理面板...</p>
  </div>
</template>

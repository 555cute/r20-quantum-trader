<script setup lang="ts">
defineProps<{
  size?: number
}>()
</script>

<template>
  <div
    class="inline-flex items-center justify-center rounded-lg font-mono font-black select-none shrink-0 shadow-xs border transition-all"
    :style="{
      width: `${size || 24}px`,
      height: `${size || 24}px`,
      backgroundColor: 'rgba(247, 147, 26, 0.12)',
      borderColor: 'rgba(247, 147, 26, 0.35)',
      color: '#F7931A',
      fontSize: `${Math.max(12, (size || 24) * 0.58)}px`,
      lineHeight: '1',
    }"
    title="R20量子交易系统"
  >
    ₿
  </div>
</template>

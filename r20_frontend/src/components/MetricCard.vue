<script setup lang="ts">
defineProps<{ label:string; value:string|number; suffix?:string; detail?:string; tone?:'profit'|'loss'|'brand'|'warning'|'default' }>()
</script>
<template>
  <div class="metric-card min-w-0" :data-tone="tone || 'brand'">
    <div class="flex items-center justify-between gap-2"><span class="metric-label">{{ label }}</span><slot name="badge" /></div>
    <div class="my-2.5 flex items-baseline gap-1.5"><strong class="metric-value key-number text-2xl sm:text-[26px]">{{ value }}</strong><span v-if="suffix" class="muted text-[10px] font-mono font-bold">{{ suffix }}</span></div>
    <div class="divider flex min-h-8 flex-wrap items-center gap-1.5 border-t pt-2"><slot name="footer"><span class="muted text-[10px] sm:text-[11px]">{{ detail || '—' }}</span></slot></div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ title: string; subtitle?: string; count?: string | number; tone?: 'brand'|'profit'|'loss'|'warning' }>()
</script>
<template>
  <div class="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b divider pb-3 mb-3">
    <div class="min-w-0 flex items-center gap-2.5">
      <span class="h-2 w-2 shrink-0 rounded-full" :style="{background: tone === 'loss' ? 'var(--loss)' : tone === 'warning' ? 'var(--warning)' : tone === 'profit' ? 'var(--profit)' : 'var(--brand)'}"></span>
      <div class="min-w-0"><h2 class="text-xs font-extrabold font-mono sm:text-sm sm:truncate">{{ title }}</h2><p v-if="subtitle" class="muted mt-0.5 text-[10px] leading-4 sm:text-[11px] sm:truncate">{{ subtitle }}</p></div>
    </div>
    <span v-if="count !== undefined" class="badge">{{ count }}</span>
    <slot name="actions" />
  </div>
</template>

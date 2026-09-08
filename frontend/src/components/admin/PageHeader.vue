<script setup lang="ts">
/** P2 shared: unified page title/description/actions header (Primer pattern).
 *  At most ONE primary action — convention enforced by review, not code. */
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-2 sm:gap-4 pb-3 mb-3.5 border-b" style="border-color: var(--border-subtle);">
    <div class="min-w-0">
      <h1 class="text-sm sm:text-base font-black font-mono tracking-wide" style="color: var(--text-main);">
        {{ title }}
      </h1>
      <p v-if="description" class="text-[11px] font-mono mt-1 leading-relaxed max-w-3xl" style="color: var(--text-muted);">
        {{ description }}
      </p>
    </div>
    <div class="flex items-center gap-2 shrink-0 flex-wrap">
      <slot name="actions" />
    </div>
  </div>
</template>

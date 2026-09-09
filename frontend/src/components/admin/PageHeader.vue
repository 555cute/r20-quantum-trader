<script setup lang="ts">
/** P2 shared: unified page title/description/actions header (Primer pattern).
 *  At most ONE primary action — convention enforced by review, not code. */
defineProps<{
  title: string
  description?: string
  stacked?: boolean
}>()
</script>

<template>
  <div :class="stacked ? 'flex flex-col gap-2 pb-3 mb-3.5 border-b' : 'flex flex-col sm:flex-row sm:items-start justify-between gap-2 sm:gap-4 pb-3 mb-3.5 border-b'" style="border-color: var(--line-1);">
    <div class="min-w-0">
      <h1 class="text-md font-semibold tracking-tight" style="color: var(--ink-1);">
        {{ title }}
      </h1>
      <p v-if="description" class="text-[11px] mt-1 leading-relaxed max-w-3xl" style="color: var(--ink-2);">
        {{ description }}
      </p>
    </div>
    <div class="flex max-w-full items-center gap-2 shrink-0 overflow-x-auto pt-0.5">
      <slot name="actions" />
    </div>
  </div>
</template>

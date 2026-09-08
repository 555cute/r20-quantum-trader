<script setup lang="ts">
/** P2 shared: settings section card. Header = title + muted description +
 *  right-aligned actions slot (usually the section's own save button). */
defineProps<{
  title: string
  description?: string
  /** tone: 'default' surface card | 'subtle' recessed */
  tone?: 'default' | 'subtle'
}>()
</script>

<template>
  <section
    class="rounded-xl border shadow-xs transition-colors"
    :style="tone === 'subtle'
      ? { backgroundColor: 'var(--surface-1)', borderColor: 'var(--line-1)' }
      : { backgroundColor: 'var(--surface-2)', borderColor: 'var(--line-1)' }"
  >
    <header class="flex items-start justify-between gap-3 px-4 py-3 border-b" style="border-color: var(--line-1);">
      <div class="min-w-0">
        <h3 class="text-sm font-semibold" style="color: var(--ink-1);">
          {{ title }}
        </h3>
        <p v-if="description" class="text-[11px] mt-0.5 leading-relaxed" style="color: var(--ink-2);">
          {{ description }}
        </p>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <slot name="actions" />
      </div>
    </header>
    <div class="p-4">
      <slot />
    </div>
  </section>
</template>

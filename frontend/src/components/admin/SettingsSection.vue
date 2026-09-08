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
      ? { backgroundColor: 'var(--bg-card-subtle)', borderColor: 'var(--border-subtle)' }
      : { backgroundColor: 'var(--bg-card)', borderColor: 'var(--border-subtle)' }"
  >
    <header class="flex items-start justify-between gap-3 px-4 py-3 border-b" style="border-color: var(--border-subtle);">
      <div class="min-w-0">
        <h3 class="text-xs sm:text-[13px] font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
          {{ title }}
        </h3>
        <p v-if="description" class="text-[11px] font-mono mt-0.5 leading-relaxed" style="color: var(--text-muted);">
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

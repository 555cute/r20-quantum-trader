<script setup lang="ts">
/** P2 shared: the ONE site-wide save/notice bar (replaces 11 duplicated bannerMsg impls).
 *  ok/warn auto-dismiss after 4s; errors persist until dismissed. */
import { watch, onUnmounted } from 'vue'
import { CheckCircle2, AlertTriangle, XCircle, X } from 'lucide-vue-next'

const props = defineProps<{
  type: 'ok' | 'err' | 'warn'
  text: string
  /** bump nonce to re-trigger auto-dismiss even when text repeats */
  nonce?: number
}>()
const emit = defineEmits<{ (e: 'dismiss'): void }>()

let timer: any = null
watch(() => [props.text, props.nonce], () => {
  if (timer) { clearTimeout(timer); timer = null }
  if (props.text && props.type !== 'err') {
    timer = setTimeout(() => emit('dismiss'), 4000)
  }
})
onUnmounted(() => { if (timer) clearTimeout(timer) })

const style = () =>
  props.type === 'ok'
    ? { bg: 'var(--color-up-bg)', border: 'var(--color-up-border)', fg: 'var(--color-up)' }
    : props.type === 'warn'
      ? { bg: 'var(--color-warn-bg)', border: 'var(--color-warn-border)', fg: 'var(--color-warn)' }
      : { bg: 'var(--color-down-bg)', border: 'var(--color-down-border)', fg: 'var(--color-down)' }
</script>

<template>
  <div
    v-if="text"
    class="sticky top-1 z-30 mb-3 flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs font-mono shadow-xs animate-[savebar-in_.18s_ease-out]"
    :style="{ backgroundColor: style().bg, borderColor: style().border, color: style().fg }"
    role="status"
  >
    <div class="flex items-center gap-2 min-w-0">
      <CheckCircle2 v-if="type === 'ok'" class="w-3.5 h-3.5 shrink-0" />
      <AlertTriangle v-else-if="type === 'warn'" class="w-3.5 h-3.5 shrink-0" />
      <XCircle v-else class="w-3.5 h-3.5 shrink-0" />
      <span class="break-words">{{ text }}</span>
    </div>
    <button @click="emit('dismiss')" class="shrink-0 opacity-60 hover:opacity-100 cursor-pointer" title="关闭">
      <X class="w-3.5 h-3.5" />
    </button>
  </div>
</template>

<style scoped>
@keyframes savebar-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}
@media (prefers-reduced-motion: reduce) {
  .animate-\[savebar-in_\.18s_ease-out\] { animation: none; }
}
</style>

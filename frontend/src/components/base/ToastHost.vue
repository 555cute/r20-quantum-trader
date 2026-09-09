<script setup lang="ts">
/** Toast 渲染宿主：右上角堆叠，App.vue 挂一次。 */
import { CheckCircle2, AlertTriangle, XCircle, Info, X } from 'lucide-vue-next';
import { useToast } from '../../composables/useToast';

const { items, dismiss } = useToast();

const icons = { ok: CheckCircle2, err: XCircle, warn: AlertTriangle, info: Info } as const;
const colors = {
  ok: 'var(--up)',
  err: 'var(--down)',
  warn: 'var(--warn)',
  info: 'var(--info)',
} as const;
</script>

<template>
  <Teleport to="body">
    <div
      class="fixed right-3 top-14 flex w-[340px] max-w-[calc(100vw-24px)] flex-col gap-2"
      style="z-index: var(--z-toast)"
      aria-live="polite"
    >
      <TransitionGroup name="toast">
        <div
          v-for="item in items"
          :key="item.id"
          class="float-panel flex items-start gap-2.5 px-3.5 py-3"
          style="border-radius: var(--r-card)"
          role="status"
        >
          <component :is="icons[item.kind]" class="mt-px h-4 w-4 shrink-0" :style="{ color: colors[item.kind] }" />
          <div class="min-w-0 flex-1">
            <p class="text-sm font-semibold leading-snug" style="color: var(--ink-1)">{{ item.title }}</p>
            <p v-if="item.desc" class="mt-0.5 text-xs leading-relaxed" style="color: var(--ink-2)">{{ item.desc }}</p>
          </div>
          <button class="-me-1 mt-px rounded p-1 opacity-60 transition-opacity hover:opacity-100" @click="dismiss(item.id)">
            <X class="h-3.5 w-3.5" />
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<style scoped>
.toast-enter-active { transition: transform 0.22s var(--ease-out), opacity 0.22s var(--ease-out); }
.toast-leave-active { transition: all 0.18s ease; position: absolute; width: 100%; }
.toast-enter-from { transform: translateX(24px); opacity: 0; }
.toast-leave-to { transform: translateX(16px); opacity: 0; }
.toast-move { transition: transform 0.2s ease; }
</style>

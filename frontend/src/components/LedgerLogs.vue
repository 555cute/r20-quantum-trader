<script setup lang="ts">
import { useDashboardStore } from '../stores/dashboard'
import { Terminal } from 'lucide-vue-next'

const store = useDashboardStore()
</script>

<template>
  <div
    class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <div class="flex items-center justify-between mb-3">
      <div class="flex items-center space-x-2">
        <Terminal class="w-4 h-4" style="color: var(--color-brand);" />
        <h2 class="text-xs sm:text-sm font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
          系统巡检日志流 (15分钟周期)
        </h2>
      </div>
      <span class="text-xs font-mono" style="color: var(--text-faint);">最新在前</span>
    </div>

    <div
      class="rounded-lg border p-3 max-h-56 overflow-y-auto font-mono text-[11px] space-y-1.5"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
    >
      <div
        v-for="(log, idx) in store.logs"
        :key="idx"
        class="border-l-2 pl-2 py-0.5 leading-relaxed transition-colors hover:bg-[var(--bg-card-hover)]"
        style="border-color: var(--border-medium); color: var(--text-muted);"
      >
        {{ log }}
      </div>
      <div v-if="store.logs.length === 0" class="text-center py-4" style="color: var(--text-faint);">
        暂无巡检日志
      </div>
    </div>
  </div>
</template>

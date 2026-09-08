<script setup lang="ts">
import { useI18n } from '../composables/useI18n'
const { t } = useI18n()
import { ref } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { RefreshCw, Terminal, X, Copy } from 'lucide-vue-next'

const store = useDashboardStore()
const isRotating = ref(false)
const promptModalOpen = ref(false)
const promptCopied = ref(false)

function manualRefresh() {
  if (isRotating.value) return
  isRotating.value = true
  store.fetchDashboard(false).finally(() => {
    setTimeout(() => { isRotating.value = false }, 600)
  })
}

function copyPrompt() {
  const text = store.data?.ai_last_prompt || ''
  if (!text) return
  navigator.clipboard.writeText(text)
  promptCopied.value = true
  setTimeout(() => { promptCopied.value = false }, 1500)
}
</script>

<template>
  <!-- P4 deep: icon-only floating stack, bottom-left, no chart obstruction -->
  <div class="fixed bottom-24 right-3 sm:bottom-6 sm:right-5 z-40 flex flex-col space-y-2">
    <button
      @click="manualRefresh"
      :title="t('cmd.refresh')"
      class="w-9 h-9 rounded-full shadow-lg border transition hover:-translate-y-0.5 active:scale-95 backdrop-blur-md cursor-pointer flex items-center justify-center"
      style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-muted);"
    >
      <RefreshCw class="w-4 h-4" :class="{ 'animate-spin': isRotating || store.isRefreshing }" />
    </button>

    <div class="relative">
      <button
        @click="promptModalOpen = true"
        :title="t('desk.livePrompt')"
        class="w-9 h-9 rounded-full shadow-lg border transition hover:-translate-y-0.5 active:scale-95 backdrop-blur-md cursor-pointer flex items-center justify-center"
        style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-muted);"
      >
        <Terminal class="w-4 h-4" />
      </button>
      <span class="absolute top-0 right-0 w-2 h-2 rounded-full bg-emerald-500 animate-pulse pointer-events-none"></span>
    </div>
  </div>

  <!-- Realtime Prompt Audit Modal -->
  <div
    v-if="promptModalOpen"
    class="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6 bg-black/60 backdrop-blur-md"
    @click.self="promptModalOpen = false"
  >
    <div
      class="border rounded-2xl w-full max-w-5xl max-h-[90vh] flex flex-col shadow-2xl overflow-hidden font-mono"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <!-- Modal Header -->
      <div
        class="px-5 py-3.5 border-b flex items-center justify-between shrink-0"
        style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
      >
        <div class="flex items-center space-x-2.5">
          <div
            class="w-7 h-7 rounded-lg border flex items-center justify-center"
            style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-main);"
          >
            <Terminal class="w-4 h-4" />
          </div>
          <div>
            <h3 class="text-sm font-bold" style="color: var(--text-main);">实时 AI 大脑提示词审计</h3>
            <p class="text-[11px]" style="color: var(--text-faint);">当前轮次真实发往大模型网关的完整 System + User Prompt 原文</p>
          </div>
        </div>
        <div class="flex items-center space-x-2">
          <button
            @click="copyPrompt"
            class="flex items-center space-x-1 px-3 py-1.5 rounded-lg border text-xs cursor-pointer transition-colors"
            style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          >
            <Copy class="w-3.5 h-3.5" />
            <span>{{ promptCopied ? '已复制 ✓' : '复制全文' }}</span>
          </button>
          <button
            @click="promptModalOpen = false"
            class="p-1.5 rounded-lg border transition-colors cursor-pointer"
            style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-faint);"
          >
            <X class="w-4 h-4" />
          </button>
        </div>
      </div>

      <!-- Modal Body -->
      <div class="flex-1 overflow-y-auto p-5" style="background-color: var(--bg-card);">
        <pre
          class="text-xs font-mono whitespace-pre-wrap leading-relaxed select-text p-4 rounded-xl border"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
        >{{ store.data?.ai_last_prompt || '等待下一次 15 分钟交易周期写入实发提示词...' }}</pre>
      </div>
    </div>
  </div>
</template>

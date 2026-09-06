<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { Terminal, RefreshCw } from 'lucide-vue-next'

const { api } = useApi()
const loading = ref(true)
const logs = ref<string[]>([])
const activeLogTab = ref<'trader' | 'backend' | 'scheduler'>('trader')
const logContent = ref<string>('')
const logLoading = ref(false)

async function loadDecisions() {
  loading.value = true
  try {
    const res = await api('/api/v1/admin/runtime')
    logs.value = res.recent_logs || []
    await fetchLogStream('trader')
  } catch (e: any) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

async function fetchLogStream(type: 'trader' | 'backend' | 'scheduler') {
  activeLogTab.value = type
  logLoading.value = true
  try {
    const res = await api(`/api/v1/admin/logs?source=${type}&lines=100`)
    logContent.value = res.content || res.lines?.join('\n') || '无实时日志'
  } catch (e: any) {
    logContent.value = `获取日志失败: ${e.message}`
  } finally {
    logLoading.value = false
  }
}

onMounted(() => {
  loadDecisions()
})
</script>

<template>
  <div class="space-y-4 max-w-[2048px] mx-auto">
    <div class="flex items-center justify-between">
      <p class="text-xs font-mono" style="color: var(--text-muted);">核对 AI 宏观基调与逐币动作，并审查交易、后台与任务调度三路实时日志流。</p>
      <span
        class="text-[10px] font-mono px-2 py-1 rounded border font-bold"
        style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);"
      >
        日常运行 · 决策与审计
      </span>
    </div>

    <!-- 3-Way Log Streams -->
    <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 mb-3 border-b" style="border-color: var(--border-subtle);">
        <div class="flex items-center space-x-2">
          <Terminal class="w-4 h-4 text-purple-400" />
          <h2 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">系统实时日志流</h2>
        </div>
        <!-- Log Selector Tabs -->
        <div class="flex flex-wrap gap-1 p-1 rounded-lg border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
          <button
            @click="fetchLogStream('trader')"
            class="px-2.5 py-1 rounded text-xs font-mono font-bold cursor-pointer transition-colors"
            :style="activeLogTab === 'trader' ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' } : { color: 'var(--text-muted)' }"
          >
            交易巡检 (Trader)
          </button>
          <button
            @click="fetchLogStream('backend')"
            class="px-2.5 py-1 rounded text-xs font-mono font-bold cursor-pointer transition-colors"
            :style="activeLogTab === 'backend' ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' } : { color: 'var(--text-muted)' }"
          >
            控制面服务 (Backend)
          </button>
          <button
            @click="fetchLogStream('scheduler')"
            class="px-2.5 py-1 rounded text-xs font-mono font-bold cursor-pointer transition-colors"
            :style="activeLogTab === 'scheduler' ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' } : { color: 'var(--text-muted)' }"
          >
            任务调度器 (Scheduler)
          </button>
        </div>
      </div>

      <div class="relative">
        <div v-if="logLoading" class="absolute inset-0 bg-black/40 backdrop-blur-xs flex items-center justify-center text-xs font-mono" style="color: var(--color-brand);">
          <RefreshCw class="w-4 h-4 animate-spin mr-1.5" />
          <span>正在拉取最新日志流...</span>
        </div>
        <pre class="border rounded-lg p-3 text-xs font-mono max-h-[520px] overflow-y-auto whitespace-pre-wrap leading-relaxed select-text" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);">{{ logContent }}</pre>
      </div>
    </div>
  </div>
</template>

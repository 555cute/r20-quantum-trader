<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
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

// 条目起点：[YYYY-MM-DD HH:MM:SS] 或 uvicorn 的 INFO:/ERROR: 等；其余行视为上一条目的延续，
// 反转时以「条目」为单位，多行条目（如 traceback、npm 提示续行）内部顺序保持不乱。
const ENTRY_START = /^(\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]|(INFO|WARNING|ERROR|CRITICAL|DEBUG)[:\s])/

function reverseLogEntries(raw: string): string {
  const lines = raw.split('\n')
  const entries: string[][] = []
  for (const line of lines) {
    if (entries.length === 0 || ENTRY_START.test(line)) {
      entries.push([line])
    } else {
      entries[entries.length - 1].push(line)
    }
  }
  return entries.reverse().map(e => e.join('\n')).join('\n')
}

async function fetchLogStream(type: 'trader' | 'backend' | 'scheduler') {
  activeLogTab.value = type
  logLoading.value = true
  try {
    const res = await api(`/api/v1/admin/logs?source=${type}&lines=100`)
    const raw: string = res.content || res.lines?.join('\n') || ''
    logContent.value = raw ? reverseLogEntries(raw) : '无实时日志'
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
      <p class="text-xs" style="color: var(--ink-2);"> 系统实时日志流 —— 核对 AI 宏观基调与逐币动作，并审查交易、后台与任务调度三路实时日志流。 </p>
      <span
        class="text-[11px] px-2 py-1 rounded border font-bold"
        style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);"
      >
        日常运行 · 决策与审计
      </span>
    </div>

    <!-- 3-Way Log Streams -->
    <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 mb-3 border-b" style="border-color: var(--line-1);">
        <div class="flex items-center space-x-2">
          <Terminal class="w-4 h-4 text-purple-400" />
          <h2 class="text-xs font-semibold uppercase tracking-wide" style="color: var(--ink-1);">{{ t('admin.nDecisions') }}</h2>
          <span class="text-[11px] px-1.5 py-0.5 rounded border font-bold" style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-3);">最新在前</span>
        </div>
        <!-- Log Selector Tabs -->
        <div class="flex flex-wrap gap-1 p-1 rounded-lg border" style="background-color: var(--surface-1); border-color: var(--line-1);">
          <button
            @click="fetchLogStream('trader')"
            class="px-2.5 py-1 rounded text-xs font-bold cursor-pointer transition-colors"
            :style="activeLogTab === 'trader' ? { backgroundColor: 'var(--ink-1)', color: 'var(--surface-2)' } : { color: 'var(--ink-2)' }"
          >
            交易巡检 (Trader)
          </button>
          <button
            @click="fetchLogStream('backend')"
            class="px-2.5 py-1 rounded text-xs font-bold cursor-pointer transition-colors"
            :style="activeLogTab === 'backend' ? { backgroundColor: 'var(--ink-1)', color: 'var(--surface-2)' } : { color: 'var(--ink-2)' }"
          >
            控制面服务 (Backend)
          </button>
          <button
            @click="fetchLogStream('scheduler')"
            class="px-2.5 py-1 rounded text-xs font-bold cursor-pointer transition-colors"
            :style="activeLogTab === 'scheduler' ? { backgroundColor: 'var(--ink-1)', color: 'var(--surface-2)' } : { color: 'var(--ink-2)' }"
          >
            任务调度器 (Scheduler)
          </button>
        </div>
      </div>

      <div class="relative">
        <div v-if="logLoading" class="absolute inset-0 bg-black/40 backdrop-blur-xs flex items-center justify-center text-xs" style="color: var(--accent);">
          <RefreshCw class="w-4 h-4 animate-spin mr-1.5" />
          <span>正在拉取最新日志流...</span>
        </div>
        <pre class="border rounded-lg p-3 text-xs max-h-[520px] overflow-y-auto whitespace-pre-wrap leading-relaxed select-text" style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-1);">{{ logContent }}</pre>
      </div>
    </div>
  </div>
</template>

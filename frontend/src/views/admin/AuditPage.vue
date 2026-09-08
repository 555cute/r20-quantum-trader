<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from '../../composables/useI18n'
import DataTable from '../../components/admin/DataTable.vue'
const { t } = useI18n()
import { useApi } from '../../composables/useApi'
import { Scroll, RefreshCw, Search } from 'lucide-vue-next'

const { api } = useApi()
const records = ref<any[]>([])
const loading = ref(true)
const search = ref('')
const detailRec = ref<any | null>(null)

async function load() {
  loading.value = true
  try {
    const res = await api('/api/v1/admin/audit?limit=200')
    records.value = res.records || []
  } finally {
    loading.value = false
  }
}

function filtered() {
  const q = search.value.trim().toLowerCase()
  if (!q) return records.value
  return records.value.filter((r) =>
    [r.action, r.status, JSON.stringify(r.detail || '')].join(' ').toLowerCase().includes(q)
  )
}

function statusColor(s: string) {
  if (s === 'success' || s === 'completed' || s === 'accepted') return 'text-emerald-400'
  if (s === 'failed' || s === 'denied') return 'text-rose-400'
  return 'text-amber-400'
}

onMounted(load)
</script>

<template>
  <div class="space-y-4 max-w-[2048px] mx-auto">
    <div class="flex items-center justify-between">
      <p class="text-xs font-mono" style="color: var(--text-muted);">只追加的操作审计流水；登录、配置变更、交易动作全部留痕。</p>
      <span
        class="text-[11px] font-mono px-2 py-1 rounded border font-bold"
        style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);"
      >
        治理 · 1/3
      </span>
    </div>

    <!-- Toolbar -->
    <div class="rounded-xl border p-3 flex items-center gap-3 shadow-xs transition-colors" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
      <div class="flex items-center space-x-2 flex-1 rounded-lg px-3 py-2 border transition-colors" style="background-color: var(--bg-input); border-color: var(--border-subtle);">
        <Search class="w-3.5 h-3.5" style="color: var(--text-faint);" />
        <input v-model="search" placeholder="搜索动作 / 状态 / 账号 / 详情..." class="flex-1 bg-transparent text-xs font-mono outline-none" style="color: var(--text-main);" />
      </div>
      <button @click="load" class="flex items-center space-x-1 px-3 py-2 rounded-lg border text-xs font-mono font-bold cursor-pointer transition-all shadow-xs" style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);">
        <RefreshCw class="w-3.5 h-3.5" :class="{ 'animate-spin': loading }" /><span>刷新</span>
      </button>
    </div>

    <!-- Audit Rows -->
    <div class="rounded-xl border overflow-hidden shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
      <div class="px-4 py-3 border-b flex items-center justify-between" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);">
        <div class="flex items-center space-x-2">
          <Scroll class="w-4 h-4 text-purple-400" />
          <h2 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
            {{ t('admin.nAudit') }} ({{ filtered().length }} {{ t('admin.auditEntries') }})
          </h2>
        </div>
        <span class="text-[11px] font-mono" style="color: var(--text-faint);">点击任意行穿透查看原始参数 JSON</span>
      </div>

      <div class="max-h-[580px] overflow-y-auto">
        <DataTable
          flat
          clickable
          :columns="[
            { key: 'timestamp', label: '时间戳' },
            { key: 'action', label: '动作类型' },
            { key: 'status', label: '执行结果' },
            { key: 'detail', label: '操作者与审计详情' },
          ]"
          :rows="filtered()"
          :row-key="(r: any, i: number) => i"
          empty-text="暂无符合条件的审计记录"
          @row-click="detailRec = $event"
        >
          <template #cell-timestamp="{ row }">
            <span class="num-tabular" style="color: var(--text-faint);">{{ row.timestamp }}</span>
          </template>
          <template #cell-action="{ row }">
            <span class="font-bold" style="color: var(--color-brand);">{{ row.action }}</span>
          </template>
          <template #cell-status="{ row }">
            <span class="font-bold" :class="statusColor(row.status)">{{ row.status }}</span>
          </template>
          <template #cell-detail="{ row }">
            <span class="block max-w-[480px] truncate">
              <strong style="color: var(--text-main);">{{ row.detail?.actor || row.detail?.username || 'system' }}</strong>
              <span class="ml-1 opacity-70" style="color: var(--text-muted);">· {{ JSON.stringify(row.detail || {}) }}</span>
            </span>
          </template>
        </DataTable>
      </div>
    </div>

    <!-- Detail Modal -->
    <div v-if="detailRec" class="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4" @click.self="detailRec = null">
      <div class="rounded-xl border p-5 sm:p-6 w-full max-w-[640px] max-h-[88dvh] overflow-y-auto shadow-2xl transition-colors" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <h3 class="text-sm font-bold mb-3 font-mono" style="color: var(--text-main);">审计详情 · {{ detailRec.action }}</h3>
        <pre class="border rounded-lg p-3 text-xs font-mono whitespace-pre-wrap max-h-[400px] overflow-y-auto select-text" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);">{{ JSON.stringify(detailRec, null, 2) }}</pre>
        <div class="flex justify-end mt-4">
          <button @click="detailRec = null" class="px-4 py-2 rounded-lg border text-xs font-mono font-bold cursor-pointer transition-all shadow-xs" style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

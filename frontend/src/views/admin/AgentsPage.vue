<script setup lang="ts">
import { fmtDateTime } from '../../utils/format';
import { ref, onMounted } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
import { useApi } from '../../composables/useApi'
import { Package, Cpu, KeyRound, RefreshCw } from 'lucide-vue-next'

const { api } = useApi()
const data = ref<any>(null)
const loading = ref(true)
const errText = ref('')

async function load() {
  loading.value = true
  try {
    data.value = await api('/api/v1/admin/agents')
    errText.value = ''
  } catch (e: any) {
    errText.value = e.message
  } finally {
    loading.value = false
  }
}

function statusColor(s: string) {
  if (['success', 'running', 'online', 'idle'].includes(s)) return 'text-emerald-400'
  if (['failed', 'error', 'offline'].includes(s)) return 'text-rose-400'
  return 'text-amber-400'
}

onMounted(load)
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <p class="text-xs text-[var(--ink-3)]">受管 Worker 存活、密文库状态与大模型调用遥测。</p>
      <span class="text-[11px] text-blue-400 bg-blue-500/10 px-2 py-1 rounded border border-blue-500/20">策略配置 · 3/3</span>
    </div>

    <div v-if="errText" class="p-3 rounded-lg text-xs bg-rose-500/10 border border-rose-500/20 text-rose-400">{{ errText }}</div>
    <div v-if="loading" class="py-12 text-center text-xs text-[var(--ink-3)]"><RefreshCw class="w-5 h-5 animate-spin inline mr-1.5 text-blue-400" />正在加载运行单元...</div>

    <template v-else-if="data">
      <!-- Agents -->
      <div class="rounded-xl border overflow-hidden shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="px-4 py-3 border-b flex items-center justify-between" style="border-color: var(--line-1); background-color: var(--surface-1);">
          <div class="flex items-center space-x-2">
            <Package class="w-4 h-4 text-blue-400" />
            <h2 class="text-xs font-semibold" style="color: var(--ink-1);">{{ t('nav.admin.agents') }}</h2>
        <p class="text-[11px] mt-0.5" style="color: var(--ink-2);"> 受管 Worker 单元清单 </p>
          </div>
          <button @click="load" class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-[11px] cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-2); color: var(--ink-1);">
            <RefreshCw class="w-3 h-3" />
            <span>刷新</span>
          </button>
        </div>
        <div class="table-scroll-container">
          <table class="w-full text-left text-xs whitespace-nowrap">
            <thead>
              <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--line-1); background-color: var(--surface-1); color: var(--ink-2);">
                <th class="py-2.5 px-4">Worker 单元</th>
                <th class="py-2.5 px-3">核心职责</th>
                <th class="py-2.5 px-3">健康状态</th>
                <th class="py-2.5 px-3">最近执行时间</th>
                <th class="py-2.5 px-3">运行结果</th>
                <th class="py-2.5 px-4 text-right">产物时效</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="a in data.agents" :key="a.id" class="border-b last:border-b-0 hover:bg-[var(--surface-3)] transition-colors" style="border-color: var(--line-1);">
                <td class="py-2.5 px-4 font-bold" style="color: var(--ink-1);">{{ a.name }}</td>
                <td class="py-2.5 px-3" style="color: var(--ink-2);">{{ a.role }}</td>
                <td class="py-2.5 px-3 font-bold" :class="statusColor(a.health)">{{ a.health }}</td>
                <td class="py-2.5 px-3 num" style="color: var(--ink-3);">{{ fmtDateTime(a.last_run_at || '尚未调度') }}</td>
                <td class="py-2.5 px-3 font-bold" :class="statusColor(a.last_run_status)">{{ a.last_run_status }}</td>
                <td class="py-2.5 px-4 text-right" style="color: var(--ink-2);">{{ a.output_age_seconds != null ? Math.round(a.output_age_seconds / 60) + ' 分钟前' : (a.output ? '冷启动' : '无产物') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <!-- Model Telemetry -->
        <div class="rounded-xl border overflow-hidden shadow-xs p-4" style="background-color: var(--surface-2); border-color: var(--line-1);">
          <div class="flex items-center space-x-2 mb-3"><Cpu class="w-4 h-4 text-purple-400" /><h2 class="text-xs font-semibold" style="color: var(--ink-1);">模型调用遥测 (最近 50 次)</h2></div>
          <div class="text-[11px] mb-3 p-2.5 rounded-lg border leading-relaxed" style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-2);">{{ data.prompt_policy }}</div>
          <div class="grid grid-cols-3 gap-2.5 mb-3 text-center">
            <div class="rounded-lg border p-2" style="background-color: var(--surface-1); border-color: var(--line-1);"><div class="text-[11px]" style="color: var(--ink-3);">总调用量</div><div class="text-sm font-bold num mt-0.5" style="color: var(--ink-1);">{{ data.model_stats?.total_calls ?? '--' }}</div></div>
            <div class="rounded-lg border p-2" style="background-color: var(--surface-1); border-color: var(--line-1);"><div class="text-[11px]" style="color: var(--ink-3);">调用成功率</div><div class="text-sm font-bold num mt-0.5" :class="(data.model_stats?.total_calls ?? 0) > 0 && (data.model_stats?.successful_calls ?? 0) < (data.model_stats?.total_calls ?? 0) ? 'text-amber-500' : 'text-emerald-500'">{{ (data.model_stats?.total_calls ?? 0) > 0 ? Math.round(100 * (data.model_stats?.successful_calls ?? 0) / data.model_stats.total_calls) + '%' : '--' }}</div></div>
            <div class="rounded-lg border p-2" style="background-color: var(--surface-1); border-color: var(--line-1);"><div class="text-[11px]" style="color: var(--ink-3);">平均时延</div><div class="text-sm font-bold num mt-0.5" style="color: var(--ink-1);">{{ data.model_stats?.avg_duration_ms ? Math.round(data.model_stats.avg_duration_ms) + 'ms' : '--' }}</div></div>
          </div>
          <div class="table-scroll-container max-h-60 overflow-y-auto rounded-lg border" style="border-color: var(--line-1);">
            <table class="w-full text-left text-xs whitespace-nowrap">
              <thead class="sticky top-0 z-10">
                <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--line-1); background-color: var(--surface-1); color: var(--ink-2);">
                  <th class="py-2 px-3">调用方</th>
                  <th class="py-2 px-2">模型</th>
                  <th class="py-2 px-2">状态</th>
                  <th class="py-2 px-2">Tokens</th>
                  <th class="py-2 px-3 text-right">耗时</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="c in (data.model_calls || []).slice(0, 30)" :key="c.id" class="border-b last:border-b-0 hover:bg-[var(--surface-3)] transition-colors" style="border-color: var(--line-1);">
                  <td class="py-1.5 px-3" style="color: var(--ink-2);">{{ c.caller || '--' }}</td>
                  <td class="py-1.5 px-2 num" style="color: var(--ink-3);">{{ c.model || '--' }}</td>
                  <td class="py-1.5 px-2 font-bold" :class="statusColor(c.status)">{{ c.status }}</td>
                  <td class="py-1.5 px-2 num" style="color: var(--ink-2);">{{ c.total_tokens ?? '--' }}</td>
                  <td class="py-1.5 px-3 text-right num" style="color: var(--ink-2);">{{ c.duration_ms ? Math.round(c.duration_ms) + 'ms' : '--' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Secret Store -->
        <div class="rounded-xl border p-4 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
          <div class="flex items-center space-x-2 mb-3"><KeyRound class="w-4 h-4 text-amber-500" /><h2 class="text-xs font-semibold" style="color: var(--ink-1);">本机加密密文库</h2></div>
          <div class="space-y-1.5 text-xs">
            <div class="flex items-center justify-between border rounded-lg px-3 py-2" style="background-color: var(--surface-1); border-color: var(--line-1);">
              <span style="color: var(--ink-2);">加密库状态</span>
              <span :class="data.secret_store?.initialized ? 'text-emerald-500 font-bold' : 'text-rose-500 font-bold'">{{ data.secret_store?.initialized ? '已初始化 ✓' : '未初始化' }} · {{ data.secret_store?.count ?? 0 }} 项密文 · 文件权限 {{ data.secret_store?.store_mode || '--' }}</span>
            </div>
            <div class="flex items-center justify-between border rounded-lg px-3 py-2" style="background-color: var(--surface-1); border-color: var(--line-1);">
              <span style="color: var(--ink-2);">读取优先级</span><span style="color: var(--ink-1);">{{ data.secret_store?.source_priority || 'encrypted-store-over-env' }}</span>
            </div>
            <div v-for="k in (data.secret_store?.keys || [])" :key="k" class="flex items-center justify-between border rounded-lg px-3 py-2" style="background-color: var(--surface-1); border-color: var(--line-1);">
              <span style="color: var(--ink-2);">{{ k }}</span>
              <span class="text-emerald-500 font-bold">已配置 ✓</span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

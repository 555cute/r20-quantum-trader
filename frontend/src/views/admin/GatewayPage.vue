<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { Zap, RefreshCw, RotateCcw, Server, Clock, AlertTriangle } from 'lucide-vue-next'

const { api } = useApi()
const gw = ref<any>(null)
const loading = ref(true)
const bannerMsg = ref<{ text: string; type: 'ok' | 'err' } | null>(null)

const deliveredCount = computed(() => (gw.value?.stats?.delivered ?? 0) + (gw.value?.stats?.accepted ?? 0))
const deliveryTotal = computed(() => Object.values(gw.value?.stats || {}).reduce((a: number, b: any) => a + Number(b || 0), 0))
const overdueCount = computed(() => (gw.value?.scheduler?.jobs || []).filter((j: any) => j.overdue).length)

async function load() {
  loading.value = true
  try {
    gw.value = await api('/api/v1/admin/gateway?limit=50')
  } catch (e: any) {
    bannerMsg.value = { text: `加载失败：${e.message}`, type: 'err' }
  } finally {
    loading.value = false
  }
}

async function replayDelivery(id: number) {
  const phrase = prompt(`重放投递 #${id} 需精确输入确认短语：REPLAY ${id}`)
  if (!phrase) return
  try {
    await api(`/api/v1/admin/gateway/deliveries/${id}/replay`, {
      method: 'POST',
      body: JSON.stringify({ confirmation: phrase.trim().toUpperCase() }),
    })
    bannerMsg.value = { text: `投递 #${id} 已重新入队`, type: 'ok' }
    await load()
  } catch (e: any) {
    bannerMsg.value = { text: `重放失败：${e.message}`, type: 'err' }
  }
}

function statusColor(s: string) {
  if (s === 'success' || s === 'delivered' || s === 'ok') return 'text-emerald-400'
  if (s === 'dead' || s === 'failed' || s === 'error') return 'text-rose-400'
  if (s === 'pending' || s === 'retrying') return 'text-amber-400'
  return 'text-zinc-300'
}

onMounted(load)
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <p class="text-xs text-[var(--text-faint)] font-mono">调度任务、事件投递队列与死信重放；Gateway 仅记录无内容遥测。</p>
      <span class="text-[11px] font-mono text-blue-400 bg-blue-500/10 px-2 py-1 rounded border border-blue-500/20">日常运行 · 4/4</span>
    </div>

    <div v-if="bannerMsg" class="p-3 rounded-lg text-xs font-mono border" :class="bannerMsg.type === 'ok' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-rose-500/10 border-rose-500/20 text-rose-400'">{{ bannerMsg.text }}</div>

    <div v-if="loading" class="py-12 text-center text-xs font-mono text-[var(--text-faint)]"><RefreshCw class="w-5 h-5 animate-spin inline mr-1.5 text-blue-400" />正在加载网关状态...</div>

    <template v-else-if="gw">
      <!-- Worker & Stats Cards -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div class="rounded-xl border p-4 shadow-xs transition-colors" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2 text-[11px] font-mono mb-2" style="color: var(--text-muted);"><Server class="w-4 h-4 text-emerald-500" /><span>Gateway 进程</span></div>
          <div class="text-lg font-black font-mono" :class="gw.running ? 'text-emerald-500' : 'text-rose-500'">{{ gw.running ? 'ONLINE' : 'OFFLINE' }}</div>
          <div class="text-[11px] font-mono mt-1" style="color: var(--text-faint);">PID {{ gw.pid || '--' }} · v{{ gw.version }}</div>
        </div>
        <div class="rounded-xl border p-4 shadow-xs transition-colors" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2 text-[11px] font-mono mb-2" style="color: var(--text-muted);"><Zap class="w-4 h-4 text-blue-500" /><span>投递队列</span></div>
          <div class="text-lg font-black font-mono num-tabular" style="color: var(--text-main);">{{ deliveredCount }}<span class="text-xs" style="color: var(--text-muted);"> / {{ deliveryTotal }}</span></div>
          <div class="text-[11px] font-mono mt-1" style="color: var(--text-faint);">待处理 {{ gw.stats?.pending ?? 0 }} · 重试 {{ gw.stats?.retry ?? 0 }}</div>
        </div>
        <div class="rounded-xl border p-4 shadow-xs transition-colors" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2 text-[11px] font-mono mb-2" style="color: var(--text-muted);"><AlertTriangle class="w-4 h-4 text-amber-500" /><span>死信 / 关键事件</span></div>
          <div class="text-lg font-black font-mono num-tabular" :class="(gw.stats?.dead ?? 0) > 0 ? 'text-rose-500' : 'text-emerald-500'">{{ gw.stats?.dead ?? 0 }}<span class="text-xs" style="color: var(--text-muted);"> / {{ gw.event_health?.critical_total ?? 0 }}</span></div>
          <div class="text-[11px] font-mono mt-1" style="color: var(--text-faint);">关键未达 {{ gw.event_health?.critical_unmet ?? 0 }} · 失败 {{ gw.event_health?.critical_failed ?? 0 }}</div>
        </div>
        <div class="rounded-xl border p-4 shadow-xs transition-colors" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2 text-[11px] font-mono mb-2" style="color: var(--text-muted);"><Clock class="w-4 h-4 text-purple-500" /><span>调度任务</span></div>
          <div class="text-lg font-black font-mono num-tabular" style="color: var(--text-main);">{{ gw.scheduler?.jobs?.length ?? 0 }}</div>
          <div class="text-[11px] font-mono mt-1" :class="overdueCount > 0 ? 'text-rose-500' : 'text-emerald-500'">{{ overdueCount > 0 ? overdueCount + ' 个任务逾期!' : '无逾期任务' }}</div>
        </div>
      </div>

      <!-- Scheduler Jobs -->
      <div v-if="gw.scheduler?.jobs?.length" class="rounded-xl border overflow-hidden shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="px-4 py-3 border-b flex items-center justify-between" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);">
          <h2 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">本地调度计划（北京时间）</h2>
          <span class="text-[11px] font-mono" style="color: var(--text-faint);">{{ gw.scheduler.jobs.length }} 个受管定时作业</span>
        </div>
        <div class="table-scroll-container">
          <table class="w-full text-left text-xs font-mono whitespace-nowrap">
            <thead>
              <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle); color: var(--text-muted);">
                <th class="py-2.5 px-4">任务</th>
                <th class="py-2.5 px-3">脚本</th>
                <th class="py-2.5 px-3">触发</th>
                <th class="py-2.5 px-3">最近调度</th>
                <th class="py-2.5 px-4 text-right">状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="j in gw.scheduler.jobs" :key="j.name" class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors" style="border-color: var(--border-subtle);">
                <td class="py-2.5 px-4 font-bold" style="color: var(--text-main);">{{ j.name }}</td>
                <td class="py-2.5 px-3 text-[11px]" style="color: var(--text-muted);">{{ j.script }}</td>
                <td class="py-2.5 px-3 font-medium" style="color: var(--text-main);">{{ j.schedule }}</td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-faint);">{{ j.last_scheduled_at || '尚未调度' }}</td>
                <td class="py-2.5 px-4 text-right font-bold" :class="j.overdue ? 'text-rose-400' : 'text-emerald-400'">
                  {{ j.overdue ? '⚠ 逾期' : '正常' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Deliveries -->
      <div class="rounded-xl border overflow-hidden shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="px-4 py-3 border-b flex items-center justify-between" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);">
          <div class="flex items-center space-x-2">
            <h2 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">事件投递队列 (最近 50 条)</h2>
            <span class="text-[11px] font-mono px-2 py-0.2 rounded border font-bold" style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);">
              {{ gw.deliveries?.length || 0 }} 记录
            </span>
          </div>
          <button @click="load" class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-[11px] font-mono cursor-pointer transition-all shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-main);">
            <RefreshCw class="w-3 h-3" />
            <span>刷新队列</span>
          </button>
        </div>
        <div class="table-scroll-container max-h-[420px] overflow-y-auto">
          <table class="w-full text-left text-xs font-mono whitespace-nowrap">
            <thead class="sticky top-0 z-10">
              <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle); color: var(--text-muted);">
                <th class="py-2.5 px-4">#</th>
                <th class="py-2.5 px-3">事件类型</th>
                <th class="py-2.5 px-3">投递通道</th>
                <th class="py-2.5 px-3">状态</th>
                <th class="py-2.5 px-3">尝试</th>
                <th class="py-2.5 px-3">时间</th>
                <th class="py-2.5 px-4 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in gw.deliveries" :key="d.id" class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors" style="border-color: var(--border-subtle);">
                <td class="py-2.5 px-4 num-tabular" style="color: var(--text-faint);">{{ d.id }}</td>
                <td class="py-2.5 px-3 font-bold" style="color: var(--text-main);">{{ d.event_type || d.topic || '--' }}</td>
                <td class="py-2.5 px-3" style="color: var(--text-muted);">{{ d.channel || '--' }}</td>
                <td class="py-2.5 px-3 font-bold" :class="statusColor(d.status)">{{ d.status }}</td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-muted);">{{ d.attempts ?? d.attempt_count ?? 1 }}</td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-faint);">{{ d.created_at || d.time || '--' }}</td>
                <td class="py-2.5 px-4 text-right">
                  <button v-if="d.status === 'dead'" @click="replayDelivery(d.id)" class="flex items-center space-x-1 ml-auto px-2 py-1 rounded-md border text-[11px] font-mono cursor-pointer transition-colors" style="background-color: var(--color-warn-bg); border-color: var(--color-warn-border); color: var(--color-warn);">
                    <RotateCcw class="w-3 h-3" /><span>重放</span>
                  </button>
                  <span v-else class="text-[11px]" style="color: var(--text-faint);">--</span>
                </td>
              </tr>
              <tr v-if="!gw.deliveries || gw.deliveries.length === 0">
                <td colspan="7" class="py-8 text-center" style="color: var(--text-faint);">暂无投递记录</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

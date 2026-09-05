<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useApi } from '../../composables/useApi'
import {
  Cpu,
  Database,
  Activity,
  Server,
  ShieldCheck,
  RefreshCw,
  ArrowRight,
  FileText,
  Users,
  Layers,
  Clock,
  CheckCircle2,
  AlertCircle,
} from 'lucide-vue-next'

const router = useRouter()
const { api } = useApi()
const runtime = ref<any>(null)
const loading = ref(true)

function duration(s: number | null): string {
  if (s == null) return '--'
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

const formattedDecisions = computed(() => {
  const d = runtime.value?.full_decisions
  if (!d) return []
  if (Array.isArray(d)) return d
  if (typeof d === 'object') {
    return Object.entries(d).map(([k, v]: [string, any]) => ({
      instId: v.instId || k,
      action: v.decision?.action || v.action || 'WAIT',
      confidence: (v.decision?.confidence ?? v.confidence ?? 0) > 1 ? (v.decision?.confidence ?? v.confidence ?? 0) / 100 : (v.decision?.confidence ?? v.confidence ?? 0),
      timestamp: v.time_str || (v.timestamp ? String(v.timestamp) : '--'),
      reason: v.decision?.summary_reason || v.thought_process?.market_structure || v.reason || '',
    }))
  }
  return []
})

const dataHealthFiles = computed(() => {
  const dh = runtime.value?.data_health
  if (!dh) return []
  if (Array.isArray(dh)) return dh
  if (Array.isArray(dh.files)) return dh.files
  return []
})

const dataHealthOverall = computed(() => {
  const dh = runtime.value?.data_health
  if (!dh) return 'UNKNOWN'
  if (typeof dh === 'object' && dh.overall) return dh.overall
  if (Array.isArray(dh)) {
    return dh.every((f: any) => f.fresh) ? 'LIVE' : 'STALE'
  }
  return 'UNKNOWN'
})

async function loadRuntime() {
  loading.value = true
  try {
    const [rt, cfg] = await Promise.all([
      api('/api/v1/admin/runtime').catch(() => null),
      api('/api/v1/admin/config').catch(() => null),
    ])
    if (rt) {
      if (cfg?.configuration) {
        rt.configuration = { ...cfg.configuration, ...(rt?.configuration || {}) }
      }
      runtime.value = rt
    }
  } catch (e: any) {
    console.error('Failed to load runtime:', e)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadRuntime()
})

const quickNav = [
  { label: '提示词策略工作室', desc: '语义变量与预设方案', route: '/admin/promptlib', icon: FileText },
  { label: '物理拦截插件', desc: 'Fail-Closed 风险拦截器', route: '/admin/interceptors', icon: ShieldCheck },
  { label: '多模型决策委员会', desc: '博弈仲裁与思考链透视', route: '/admin/council', icon: Users },
  { label: '模型连接配置', desc: '供应商与思考强度', route: '/admin/llm', icon: Cpu },
]
</script>

<template>
  <div class="space-y-4 max-w-[2160px] mx-auto">
    <!-- Top Executive Header Strip -->
    <div
      class="rounded-xl border p-4 sm:p-5 flex flex-col md:flex-row md:items-center justify-between gap-3 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div>
        <div class="flex items-center space-x-2">
          <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
          <h1 class="text-sm sm:text-base font-black font-mono tracking-wide" style="color: var(--text-main);">
            R20 QUANTUM CONTROL CENTER
          </h1>
          <span
            class="px-2 py-0.2 rounded text-[10px] font-mono font-bold border"
            style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);"
          >
            v7.4.1
          </span>
        </div>
        <p class="text-xs font-mono mt-1" style="color: var(--text-muted);">
          交易引擎、微积分决策链路、数据健康与物理拦截插件全景监控。
        </p>
      </div>

      <div class="flex items-center space-x-2">
        <button
          @click="loadRuntime"
          :disabled="loading"
          class="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg border text-xs font-mono font-bold transition-all cursor-pointer shadow-xs disabled:opacity-50"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
        >
          <RefreshCw class="w-3.5 h-3.5" :class="loading ? 'animate-spin' : ''" />
          <span>刷新状态</span>
        </button>
      </div>
    </div>

    <!-- Loading State -->
    <div v-if="loading" class="py-16 text-center text-xs font-mono" style="color: var(--text-muted);">
      <RefreshCw class="w-6 h-6 animate-spin mx-auto mb-2" style="color: var(--color-brand);" />
      <span>正在拉取最新控制面运行态...</span>
    </div>

    <!-- Runtime Data -->
    <template v-else-if="runtime">
      <!-- 4 High-Density Metric Bento Cards -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <!-- 1. 服务状态 -->
        <div
          class="rounded-xl border p-4 shadow-xs transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-[11px] font-mono" style="color: var(--text-muted);">后台服务进程</span>
            <div
              class="w-6 h-6 rounded-md flex items-center justify-center border"
              style="background-color: var(--color-up-bg); border-color: var(--color-up-border); color: var(--color-up);"
            >
              <Server class="w-3.5 h-3.5" />
            </div>
          </div>
          <div class="text-xl sm:text-2xl font-black font-mono tracking-tight" style="color: var(--color-up);">
            ONLINE
          </div>
          <div class="text-[10px] font-mono mt-1" style="color: var(--text-faint);">
            PID {{ runtime.service?.pid || '--' }} · FastAPI V5
          </div>
        </div>

        <!-- 2. 运行时间 -->
        <div
          class="rounded-xl border p-4 shadow-xs transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-[11px] font-mono" style="color: var(--text-muted);">引擎持续运行</span>
            <div
              class="w-6 h-6 rounded-md flex items-center justify-center border"
              style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);"
            >
              <Activity class="w-3.5 h-3.5" />
            </div>
          </div>
          <div class="text-xl sm:text-2xl font-black font-mono tracking-tight num-tabular" style="color: var(--text-main);">
            {{ duration(runtime.service?.uptime_seconds) }}
          </div>
          <div class="text-[10px] font-mono mt-1" style="color: var(--text-faint);">
            已运行秒数 {{ runtime.service?.uptime_seconds || 0 }}s
          </div>
        </div>

        <!-- 3. LLM 核心主脑 -->
        <div
          class="rounded-xl border p-4 shadow-xs transition-colors cursor-pointer group"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          @click="router.push('/admin/llm')"
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-[11px] font-mono" style="color: var(--text-muted);">决策主脑模型</span>
            <div
              class="w-6 h-6 rounded-md flex items-center justify-center border"
              style="background-color: var(--bg-badge); border-color: var(--border-subtle); color: var(--text-main);"
            >
              <Cpu class="w-3.5 h-3.5" />
            </div>
          </div>
          <div class="text-sm sm:text-base font-black font-mono truncate" style="color: var(--text-main);">
            {{ runtime.llm_runtime?.active_model || 'gemini-3.8-flash-high' }}
          </div>
          <div class="text-[10px] font-mono mt-1 flex items-center space-x-1.5" style="color: var(--text-faint);">
            <span>推理思考: {{ runtime.llm_runtime?.active_reasoning_effort || 'HIGH' }}</span>
            <span>·</span>
            <span class="text-indigo-400 group-hover:underline">配置通道 →</span>
          </div>
        </div>

        <!-- 4. 交易所环境与授权 -->
        <div
          class="rounded-xl border p-4 shadow-xs transition-colors cursor-pointer group"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          @click="router.push('/admin/security')"
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-[11px] font-mono" style="color: var(--text-muted);">多交易所网络与环境</span>
            <div
              class="w-6 h-6 rounded-md flex items-center justify-center border"
              style="background-color: var(--bg-badge); border-color: var(--border-subtle); color: var(--text-main);"
            >
              <Database class="w-3.5 h-3.5" />
            </div>
          </div>
          <div class="text-xl sm:text-2xl font-black font-mono tracking-tight" style="color: var(--color-brand);">
            {{ runtime.credentials?.simulated_trading ? 'DEMO' : 'LIVE' }}
          </div>
          <div class="text-[10px] font-mono mt-1 flex items-center space-x-1" style="color: var(--text-faint);">
            <span :class="runtime.credentials?.okx_configured ? 'text-emerald-400' : 'text-amber-400'">
              ● OKX / 币安 / Gate 聚合
            </span>
            <span>·</span>
            <span class="text-indigo-400 group-hover:underline">凭证中心 →</span>
          </div>
        </div>
      </div>

      <!-- Quick Nav Action Deck -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <button
          v-for="nav in quickNav"
          :key="nav.route"
          @click="router.push(nav.route)"
          class="p-3.5 rounded-xl border flex items-center justify-between transition-all cursor-pointer text-left group"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <div class="flex items-center space-x-3">
            <div
              class="w-8 h-8 rounded-lg flex items-center justify-center border shrink-0 transition-transform group-hover:scale-105"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);"
            >
              <component :is="nav.icon" class="w-4 h-4" />
            </div>
            <div>
              <div class="text-xs font-black font-mono group-hover:text-blue-500 transition-colors" style="color: var(--text-main);">
                {{ nav.label }}
              </div>
              <div class="text-[10px] font-mono truncate" style="color: var(--text-faint);">
                {{ nav.desc }}
              </div>
            </div>
          </div>
          <ArrowRight class="w-3.5 h-3.5 shrink-0 transition-transform group-hover:translate-x-0.5" style="color: var(--text-faint);" />
        </button>
      </div>

      <!-- Main Dual Panel: LLM Decision Audit & Data Freshness -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <!-- Left: Full Decisions Audit (2 Columns) -->
        <div
          class="lg:col-span-2 rounded-xl border p-4 sm:p-5 shadow-xs transition-colors flex flex-col justify-between"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <div>
            <div class="flex items-center justify-between pb-3 mb-3 border-b" style="border-color: var(--border-subtle);">
              <div class="flex items-center space-x-2">
                <Layers class="w-4 h-4" style="color: var(--color-brand);" />
                <h2 class="text-xs font-black font-mono uppercase tracking-wider" style="color: var(--text-main);">
                  大模型决策态势全景 (Realtime Brain Status)
                </h2>
              </div>
              <button
                @click="router.push('/admin/decisions')"
                class="text-xs font-mono flex items-center space-x-1 cursor-pointer transition-colors"
                style="color: var(--color-brand);"
              >
                <span>完整推演日志</span>
                <ArrowRight class="w-3 h-3" />
              </button>
            </div>

            <!-- Decisions List -->
            <div v-if="formattedDecisions.length" class="space-y-2">
              <div
                v-for="d in formattedDecisions"
                :key="d.instId"
                class="p-3 rounded-lg border font-mono text-xs transition-colors"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
              >
                <div class="flex items-center justify-between">
                  <div class="flex items-center space-x-2">
                    <span class="font-black text-sm" style="color: var(--text-main);">{{ d.instId }}</span>
                    <span
                      class="px-2 py-0.5 rounded text-[10px] font-bold border"
                      :style="{
                        backgroundColor: d.action?.includes('BUY') ? 'var(--color-up-bg)' : d.action?.includes('SELL') ? 'var(--color-down-bg)' : 'var(--bg-badge)',
                        borderColor: d.action?.includes('BUY') ? 'var(--color-up-border)' : d.action?.includes('SELL') ? 'var(--color-down-border)' : 'var(--border-subtle)',
                        color: d.action?.includes('BUY') ? 'var(--color-up)' : d.action?.includes('SELL') ? 'var(--color-down)' : 'var(--text-muted)'
                      }"
                    >
                      {{ d.action || '观望 HOLD' }}
                    </span>
                    <span v-if="d.confidence" class="text-[10px]" style="color: var(--text-faint);">
                      置信度 {{ Math.round(d.confidence * 100) }}%
                    </span>
                  </div>
                  <div class="text-[10px]" style="color: var(--text-faint);">
                    {{ d.timestamp ? d.timestamp.substring(11, 19) : '--' }}
                  </div>
                </div>
                <div class="mt-2 text-xs font-sans line-clamp-2 leading-relaxed" style="color: var(--text-muted);">
                  {{ d.reason || '大模型评估当前动能结构未达到击穿阈值，顺势风控保持被动防御。' }}
                </div>
              </div>
            </div>
            <div v-else class="py-12 text-center text-xs font-mono" style="color: var(--text-muted);">
              暂无巡检决策记录，等待下轮 15M 定时任务...
            </div>
          </div>
        </div>

        <!-- Right: Data Health Monitor (1 Column) -->
        <div
          class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors flex flex-col justify-between"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <div>
            <div class="flex items-center justify-between pb-3 mb-3 border-b" style="border-color: var(--border-subtle);">
              <div class="flex items-center space-x-2">
                <Clock class="w-4 h-4" style="color: var(--color-brand);" />
                <h2 class="text-xs font-black font-mono uppercase tracking-wider" style="color: var(--text-main);">
                  关键数据管道时效 (Data Health)
                </h2>
              </div>
              <span
                class="px-2 py-0.5 rounded text-[10px] font-mono font-bold border"
                :style="{
                  backgroundColor: dataHealthOverall === 'LIVE' ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
                  borderColor: dataHealthOverall === 'LIVE' ? 'var(--color-up-border)' : 'var(--color-down-border)',
                  color: dataHealthOverall === 'LIVE' ? 'var(--color-up)' : 'var(--color-down)'
                }"
              >
                {{ dataHealthOverall }}
              </span>
            </div>

            <table class="w-full text-left font-mono text-xs border-collapse">
              <thead>
                <tr class="border-b text-[10px] uppercase" style="border-color: var(--border-subtle); color: var(--text-faint);">
                  <th class="pb-2 font-medium">通道来源</th>
                  <th class="pb-2 font-medium">状态</th>
                  <th class="pb-2 font-medium">更新延时</th>
                  <th class="pb-2 text-right font-medium">字节数</th>
                </tr>
              </thead>
              <tbody class="divide-y" style="border-color: var(--border-subtle);">
                <tr
                  v-for="(x, idx) in dataHealthFiles"
                  :key="idx"
                  class="hover:bg-[var(--bg-card-subtle)] transition-colors"
                >
                  <td class="py-2.5 font-bold" style="color: var(--text-main);">
                    {{ x.file || x.name }}
                  </td>
                  <td class="py-2.5">
                    <span
                      class="px-2 py-0.5 rounded text-[10px] font-bold border inline-flex items-center space-x-1"
                      :style="{
                        backgroundColor: x.fresh ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
                        borderColor: x.fresh ? 'var(--color-up-border)' : 'var(--color-down-border)',
                        color: x.fresh ? 'var(--color-up)' : 'var(--color-down)'
                      }"
                    >
                      <CheckCircle2 v-if="x.fresh" class="w-2.5 h-2.5" />
                      <AlertCircle v-else class="w-2.5 h-2.5" />
                      <span>{{ x.fresh ? '正常新鲜' : '延迟过期' }}</span>
                    </span>
                  </td>
                  <td class="py-2.5 num-tabular" style="color: var(--text-muted);">
                    {{ duration(x.age_seconds) }}
                  </td>
                  <td class="py-2.5 text-right font-mono num-tabular" style="color: var(--text-muted);">
                    {{ x.bytes ? Math.round(x.bytes / 1024) + ' KB' : '--' }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- Security & Config Cards -->
      <div
        class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="flex items-center justify-between pb-3 mb-3 border-b" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <ShieldCheck class="w-4 h-4 text-emerald-500" />
            <h2 class="text-xs font-black font-mono uppercase tracking-wider" style="color: var(--text-main);">
              生产环境核心安全配置
            </h2>
          </div>
          <span class="text-[10px] font-mono" style="color: var(--text-faint);">敏感 Key 已脱敏防泄露保护</span>
        </div>

        <div v-if="runtime.configuration && Object.keys(runtime.configuration).length" class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
          <div
            v-for="(v, k) in runtime.configuration"
            :key="k"
            class="rounded-lg border p-3 font-mono transition-colors"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
          >
            <div class="text-[10px] uppercase truncate font-medium" style="color: var(--text-faint);">{{ k }}</div>
            <div class="text-xs font-bold truncate mt-1.5" style="color: var(--text-main);" :title="String(v)">{{ v || '未配置' }}</div>
          </div>
        </div>
        <div v-else class="py-6 text-center text-xs font-mono" style="color: var(--text-muted);">
          正在拉取核心安全配置...
        </div>
      </div>
    </template>
  </div>
</template>

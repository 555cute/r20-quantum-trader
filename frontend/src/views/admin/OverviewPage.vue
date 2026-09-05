<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useApi } from '../../composables/useApi'
import { useI18nStore } from '../../stores/i18n'
import {
  Cpu,
  ShieldCheck,
  RefreshCw,
  FileText,
  Users,
} from 'lucide-vue-next'

const router = useRouter()
const { api } = useApi()
const i18n = useI18nStore()
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

const quickNav = computed(() => [
  { label: i18n.locale === 'zh' ? '提示词策略工作室' : 'Prompt Studio', desc: i18n.locale === 'zh' ? '语义变量与预设方案' : 'Variables & Presets', route: '/admin/promptlib', icon: FileText },
  { label: i18n.locale === 'zh' ? '物理拦截插件' : 'Risk Interceptors', desc: i18n.locale === 'zh' ? 'Fail-Closed 风险拦截器' : 'Fail-Closed Hard Defense', route: '/admin/interceptors', icon: ShieldCheck },
  { label: i18n.locale === 'zh' ? '多模型决策委员会' : 'Council Architecture', desc: i18n.locale === 'zh' ? '博弈仲裁与思考链透视' : 'Consensus & Thought Chains', route: '/admin/council', icon: Users },
  { label: i18n.locale === 'zh' ? '模型连接配置' : 'LLM Providers', desc: i18n.locale === 'zh' ? '供应商与思考强度' : 'Vendors & Reasoning Effort', route: '/admin/llm', icon: Cpu },
])
</script>

<template>
  <div class="space-y-4 max-w-[2160px] mx-auto font-mono text-xs select-none">
    <!-- Top Executive Header Strip -->
    <div
      class="rounded-xl border p-4 flex flex-col md:flex-row md:items-center justify-between gap-3 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div>
        <div class="flex items-center space-x-2">
          <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
          <h1 class="text-sm sm:text-base font-black tracking-wide" style="color: var(--text-main);">
            {{ i18n.t.appName }} · {{ i18n.t.adminConsole }}
          </h1>
          <span
            class="px-2 py-0.2 rounded text-[10px] font-bold border"
            style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);"
          >
            v7.4.1
          </span>
        </div>
        <p class="text-xs mt-1" style="color: var(--text-muted);">
          {{ i18n.locale === 'zh' ? '交易引擎、微积分决策链路、数据健康与物理拦截插件全景监控。' : 'Unified telemetry for trading engine, calculus chain, health & interceptors.' }}
        </p>
      </div>

      <button
        @click="loadRuntime"
        :disabled="loading"
        class="flex items-center justify-center space-x-1.5 px-3 py-1.5 rounded-lg border transition-all cursor-pointer font-bold shrink-0 self-start md:self-auto hover:bg-[var(--bg-card-hover)]"
        style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);"
      >
        <RefreshCw class="w-3.5 h-3.5" :class="loading ? 'animate-spin' : ''" />
        <span>{{ i18n.t.refresh }}</span>
      </button>
    </div>

    <!-- 4 Core Runtime Metric Bento Grid -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
      <!-- 1. Service Process -->
      <div
        class="rounded-xl border p-3.5 flex flex-col justify-between shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="flex items-center justify-between">
          <span style="color: var(--text-muted);">{{ i18n.locale === 'zh' ? '后台服务进程' : 'Backend Service' }}</span>
          <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
        </div>
        <div class="my-2">
          <div class="text-xl font-black text-emerald-400">ONLINE</div>
          <div class="text-[10px] mt-0.5" style="color: var(--text-faint);">
            PID {{ runtime?.process?.pid || '--' }} · FastAPI V5
          </div>
        </div>
      </div>

      <!-- 2. Uptime -->
      <div
        class="rounded-xl border p-3.5 flex flex-col justify-between shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="flex items-center justify-between">
          <span style="color: var(--text-muted);">{{ i18n.locale === 'zh' ? '持续运行时间' : 'Engine Uptime' }}</span>
        </div>
        <div class="my-2">
          <div class="text-xl font-black" style="color: var(--text-main);">
            {{ duration(runtime?.process?.uptime_seconds) }}
          </div>
          <div class="text-[10px] mt-0.5" style="color: var(--text-faint);">
            {{ i18n.locale === 'zh' ? '运行秒数' : 'Seconds' }}: {{ runtime?.process?.uptime_seconds || 0 }}s
          </div>
        </div>
      </div>

      <!-- 3. Primary LLM -->
      <div
        class="rounded-xl border p-3.5 flex flex-col justify-between shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="flex items-center justify-between">
          <span style="color: var(--text-muted);">{{ i18n.locale === 'zh' ? '主裁决模型' : 'Primary CIO Model' }}</span>
        </div>
        <div class="my-2">
          <div class="text-base font-black truncate" style="color: var(--text-main);">
            {{ runtime?.ai_brain?.active_model || 'gemini-3.8-flash-high' }}
          </div>
          <div class="text-[10px] mt-0.5" style="color: var(--text-faint);">
            {{ i18n.locale === 'zh' ? '思考强度' : 'Effort' }}: {{ runtime?.ai_brain?.reasoning_effort || 'HIGH' }}
          </div>
        </div>
      </div>

      <!-- 4. Multi-Exchange Execution -->
      <div
        class="rounded-xl border p-3.5 flex flex-col justify-between shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="flex items-center justify-between">
          <span style="color: var(--text-muted);">{{ i18n.locale === 'zh' ? '多交易所网关' : 'Exchange Matrix' }}</span>
        </div>
        <div class="my-2">
          <div class="text-xl font-black text-slate-100">LIVE</div>
          <div class="text-[10px] mt-0.5" style="color: var(--text-faint);">
            OKX / Binance / Gate
          </div>
        </div>
      </div>
    </div>

    <!-- Quick Navigation Matrix (4 Tiles) -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
      <div
        v-for="item in quickNav"
        :key="item.route"
        @click="router.push(item.route)"
        class="rounded-xl border p-3 flex items-center justify-between cursor-pointer transition-all shadow-xs hover:border-[var(--border-medium)] hover:bg-[var(--bg-card-hover)]"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="flex items-center space-x-2.5">
          <div
            class="w-7 h-7 rounded-lg border flex items-center justify-center shrink-0"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
          >
            <component :is="item.icon" class="w-3.5 h-3.5" />
          </div>
          <div>
            <div class="font-bold text-xs" style="color: var(--text-main);">{{ item.label }}</div>
            <div class="text-[10px]" style="color: var(--text-muted);">{{ item.desc }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Main 2-Column Split: Real-time Decisions & Data Health -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-3 items-start">
      <!-- Left: Recent Decisions (8 cols) -->
      <div
        class="lg:col-span-8 rounded-xl border p-4 shadow-xs transition-colors space-y-3"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="flex items-center justify-between border-b pb-2.5" style="border-color: var(--border-subtle);">
          <span class="font-bold text-xs uppercase" style="color: var(--text-main);">
            {{ i18n.locale === 'zh' ? '各标的最新决策指令与思考态势' : 'Latest Decision Commands & Rationales' }}
          </span>
          <button @click="router.push('/admin/decisions')" class="text-[11px] text-indigo-400 hover:underline cursor-pointer">
            {{ i18n.locale === 'zh' ? '完整决策流 →' : 'Full Logs →' }}
          </button>
        </div>

        <div v-if="formattedDecisions.length === 0" class="py-8 text-center" style="color: var(--text-muted);">
          {{ i18n.locale === 'zh' ? '暂无决策记录' : 'No decisions recorded' }}
        </div>

        <div v-else class="space-y-2">
          <div
            v-for="item in formattedDecisions"
            :key="item.instId"
            class="p-3 rounded-lg border space-y-1"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
          >
            <div class="flex items-center justify-between">
              <span class="font-bold text-xs text-slate-200">{{ item.instId }}</span>
              <div class="flex items-center space-x-1.5">
                <span
                  class="px-1.5 py-0.2 rounded text-[10px] font-bold border"
                  :class="item.action === 'BUY_LONG' ? 'border-emerald-500/30 text-emerald-400 bg-emerald-500/10' : item.action === 'SELL_SHORT' ? 'border-rose-500/30 text-rose-400 bg-rose-500/10' : 'border-slate-700 text-slate-400'"
                >
                  {{ item.action }}
                </span>
                <span class="text-[10px] text-slate-400">{{ (item.confidence * 100).toFixed(0) }}%</span>
                <span class="text-[10px] text-slate-500">{{ item.timestamp }}</span>
              </div>
            </div>
            <p v-if="item.reason" class="text-[11px] line-clamp-2 leading-relaxed" style="color: var(--text-muted);">
              {{ item.reason }}
            </p>
          </div>
        </div>
      </div>

      <!-- Right: Data Pipeline Health (4 cols) -->
      <div
        class="lg:col-span-4 rounded-xl border p-4 shadow-xs transition-colors space-y-3"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="flex items-center justify-between border-b pb-2.5" style="border-color: var(--border-subtle);">
          <span class="font-bold text-xs uppercase" style="color: var(--text-main);">
            {{ i18n.locale === 'zh' ? '关键数据管道健康度' : 'Data Pipeline Freshness' }}
          </span>
          <span class="px-1.5 py-0.2 rounded text-[10px] font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/30">
            HEALTHY
          </span>
        </div>

        <div class="space-y-1.5">
          <div
            v-for="f in dataHealthFiles"
            :key="f.file || f.name"
            class="flex items-center justify-between p-2 rounded-lg border text-[11px]"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
          >
            <span class="font-mono truncate max-w-[160px] text-slate-300">{{ f.file || f.name }}</span>
            <div class="flex items-center space-x-2">
              <span class="text-[10px] text-slate-400">{{ f.age_seconds }}s</span>
              <span class="w-1.5 h-1.5 rounded-full" :class="f.fresh ? 'bg-emerald-500' : 'bg-rose-500'"></span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

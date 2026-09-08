<script setup lang="ts">
import { computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useI18n } from '../composables/useI18n'
import { Sparkles, Brain, Cpu, AlertTriangle, CheckCircle2, Clock, Activity, ShieldCheck, FileText } from 'lucide-vue-next'

const store = useDashboardStore()
const { t, isEn } = useI18n()
const review = computed(() => store.data?.review || {})
const memoryMd = computed(() => store.data?.ai_trading_memory_md || '')

const evolutionTime = computed(() => review.value?.timestamp || '--')
const totalTrades = computed(() => review.value?.total_trades ?? 0)
const winRate = computed(() => review.value?.win_rate ?? 0)
const profitFactor = computed(() => review.value?.profit_factor ?? 0)
const changeStatus = computed(() => review.value?.change_status || 'NO_CHANGE')
const insights = computed<string[]>(() => review.value?.insights || review.value?.diagnosis_insights || [])
const actionsTaken = computed<string[]>(() => review.value?.actions_taken || [])
const overwriteReason = computed(() => review.value?.memory_overwrites_reason || review.value?.summary || '')
</script>

<template>
  <div class="space-y-3.5 2xl:space-y-5">
    <!-- Lab Header Banner with Realtime Execution Status -->
    <div class="p-3 sm:p-4 rounded-xl border flex flex-col md:flex-row md:items-center justify-between gap-3 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
      <div class="flex items-center space-x-2.5 2xl:space-x-3">
        <div class="w-8 h-8 rounded-lg flex items-center justify-center border shrink-0" style="background-color: var(--bg-badge); border-color: var(--border-subtle);">
          <Sparkles class="w-4 h-4 text-amber-400 shrink-0" />
        </div>
        <div>
          <div class="flex items-center space-x-2 flex-wrap gap-y-1">
            <h2 class="text-xs sm:text-sm font-black font-mono tracking-wide" style="color: var(--text-main);">
              {{ t('lab.labTitle') }}
            </h2>
            <span
              class="text-[11px] font-mono px-2 py-0.5 rounded border font-bold"
              :class="changeStatus === 'EVOLVED' ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30' : 'text-amber-400 bg-amber-500/10 border-amber-500/30'"
            >
              {{ t('lab.status') }}: {{ changeStatus }}
            </span>
          </div>
          <p class="text-[11px] font-mono mt-0.5" style="color: var(--text-muted);">
            {{ t('lab.labSubtitle') }}
          </p>
        </div>
      </div>

      <!-- Live Evolution Metrics HUD -->
      <div class="flex items-center space-x-2 sm:space-x-3 text-xs font-mono flex-wrap gap-y-1.5">
        <div class="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg border" style="background-color: var(--bg-badge); border-color: var(--border-subtle);">
          <Clock class="w-3.5 h-3.5 text-indigo-400" />
          <span style="color: var(--text-muted);">{{ t('lab.latestReview') }}:</span>
          <strong class="text-emerald-400 font-bold num-tabular">{{ evolutionTime }}</strong>
        </div>
        <div class="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg border" style="background-color: var(--bg-badge); border-color: var(--border-subtle);">
          <Activity class="w-3.5 h-3.5 text-blue-400" />
          <span style="color: var(--text-muted);">{{ t('lab.sampleBaseline') }}:</span>
          <strong style="color: var(--text-main);">{{ totalTrades }} {{ t('lab.sampleTrades') }} ({{ winRate }}%)</strong>
        </div>
        <div class="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg border" style="background-color: var(--bg-badge); border-color: var(--border-subtle);">
          <Cpu class="w-3.5 h-3.5 text-purple-400" />
          <span style="color: var(--text-muted);">{{ t('lab.engineModel') }}:</span>
          <strong class="text-indigo-300">{{ store.llmRuntime.model || '未配置模型' }}</strong>
        </div>
      </div>
    </div>

    <!-- Upstream LLM failure notice -->
    <div
      v-if="review.llm_error"
      class="rounded-xl border p-3 sm:p-3.5 flex items-start space-x-2 font-mono text-[11px] 2xl:text-xs"
      style="background-color: var(--color-warn-bg); border-color: var(--color-warn-border); color: var(--color-warn);"
    >
      <AlertTriangle class="w-3.5 h-3.5 shrink-0 mt-0.5" />
      <div class="space-y-0.5">
        <div class="font-bold">{{ isEn ? `Review failed at ${review.timestamp || '--'}: upstream gateway error. Existing heuristics preserved as NO_CHANGE.` : `最近一轮 ${review.timestamp || '--'} 复盘未能完成：大模型网关返回错误，本轮按 NO_CHANGE 保留原有心法。` }}</div>
        <div style="color: var(--text-muted);">{{ review.llm_error }}</div>
      </div>
    </div>

    <!-- Dual Layout: Realtime Memory MD & Insights Diagnosis -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-3.5 2xl:gap-5 items-stretch">
      
      <!-- 1. Realtime Trading Memory (Markdown) -->
      <div
        class="h-full rounded-xl border p-4 sm:p-5 flex flex-col justify-between shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div>
          <div class="flex items-center justify-between pb-3 mb-3 border-b" style="border-color: var(--border-subtle);">
            <div class="flex items-center space-x-2">
              <Brain class="w-4 h-4 text-emerald-400" />
              <h3 class="text-xs sm:text-sm font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
                {{ t('lab.memoryTitle') }}
              </h3>
            </div>
            <span
              class="text-[11px] font-mono px-2 py-0.5 rounded border font-bold text-emerald-400"
              style="background-color: var(--bg-badge); border-color: var(--border-subtle);"
            >
              {{ t('lab.memoryBadge') }}
            </span>
          </div>
          <div
            class="p-3.5 rounded-lg border text-xs font-mono leading-relaxed max-h-[440px] overflow-y-auto whitespace-pre-wrap select-text"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
          >
            {{ memoryMd || (isEn ? 'Loading immutable heuristics...' : '正在读取长期心法知识库...') }}
          </div>
        </div>
        <div class="pt-3 mt-3 border-t text-[11px] font-mono flex items-center justify-between" style="border-color: var(--border-subtle); color: var(--text-faint);">
          <span>{{ t('lab.storageFile') }}: <code>data/AI_TRADING_MEMORY.md</code></span>
          <span class="text-emerald-400 font-bold">{{ t('lab.promptStatus') }}</span>
        </div>
      </div>

      <!-- 2. AI Diagnosis & Actions Taken -->
      <div
        class="h-full rounded-xl border p-4 sm:p-5 flex flex-col justify-between shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="space-y-3">
          <div class="flex items-center justify-between pb-3 border-b" style="border-color: var(--border-subtle);">
            <div class="flex items-center space-x-2">
              <FileText class="w-4 h-4 text-indigo-400" />
              <h3 class="text-xs sm:text-sm font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
                {{ t('lab.insightsTitle') }}
              </h3>
            </div>
            <span
              class="text-[11px] font-mono px-2 py-0.5 rounded border font-bold"
              style="background-color: var(--bg-badge); border-color: var(--border-subtle); color: var(--text-muted);"
            >
              PF: {{ profitFactor }}
            </span>
          </div>

          <!-- 决策理由 -->
          <div v-if="overwriteReason" class="p-3 rounded-lg border text-xs font-mono leading-relaxed" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
            <div class="text-[11px] uppercase mb-1 font-bold text-amber-400">{{ t('lab.verdictReason') }}</div>
            <p class="text-xs font-sans leading-relaxed" style="color: var(--text-main);">
              {{ overwriteReason }}
            </p>
          </div>

          <!-- 诊断洞察列表 -->
          <div class="space-y-2">
            <div class="text-[11px] font-mono uppercase font-bold" style="color: var(--text-faint);">
              {{ t('lab.diagnosisInsights') }} ({{ insights.length }})
            </div>
            <div class="space-y-1.5 max-h-[220px] overflow-y-auto pr-1">
              <div
                v-for="(item, idx) in insights"
                :key="idx"
                class="p-2 rounded-lg border text-[11px] font-mono leading-relaxed"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
              >
                <div class="flex items-start space-x-1.5">
                  <span class="text-indigo-400 font-bold shrink-0">#{{ idx + 1 }}</span>
                  <span style="color: var(--text-main);">{{ item }}</span>
                </div>
              </div>
              <div v-if="insights.length === 0" class="text-xs font-mono py-2 text-center" style="color: var(--text-faint);">
                {{ t('lab.noDiagnosis') }}
              </div>
            </div>
          </div>

          <!-- 执行行动清单 -->
          <div v-if="actionsTaken.length > 0" class="space-y-1.5 pt-1">
            <div class="text-[11px] font-mono uppercase font-bold" style="color: var(--text-faint);">
              {{ t('lab.actionsTaken') }} ({{ actionsTaken.length }})
            </div>
            <div class="space-y-1">
              <div
                v-for="(act, idx) in actionsTaken"
                :key="idx"
                class="flex items-start space-x-1.5 text-[11px] font-mono"
                style="color: var(--text-main);"
              >
                <CheckCircle2 class="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                <span>{{ act }}</span>
              </div>
            </div>
          </div>
        </div>

        <div class="pt-3 mt-3 border-t text-[11px] font-mono flex items-center justify-between" style="border-color: var(--border-subtle); color: var(--text-faint);">
          <span>{{ isEn ? `Baseline: recent ${totalTrades} closed trades` : `复盘基线: 最近 ${totalTrades} 笔平仓` }}</span>
          <span class="text-indigo-400 font-bold">{{ t('lab.evolutionLoop') }}</span>
        </div>
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { Newspaper, Flame, ExternalLink, ShieldAlert } from 'lucide-vue-next'

const store = useDashboardStore()
const intel = computed<any>(() => store.data?.news_intelligence || {})
const newsItems = computed<any[]>(() => intel.value.latest_news || [])
const coinsSentiment = computed<[string, any][]>(() => Object.entries(intel.value.coins_sentiment || {}))
const macro = computed<string>(() => intel.value.macro_sentiment || '--')
const breakerActive = computed<boolean>(() => !!intel.value.circuit_breaker?.active)

function labelClass(label: string) {
  if (label === 'bullish') return 'color: var(--color-up); background-color: var(--color-up-bg); border-color: var(--color-up-border);'
  if (label === 'bearish') return 'color: var(--color-down); background-color: var(--color-down-bg); border-color: var(--color-down-border);'
  if (label === 'mixed') return 'color: var(--color-warn); background-color: var(--color-warn-bg); border-color: var(--color-warn-border);'
  return 'color: var(--text-muted); background-color: var(--bg-badge); border-color: var(--border-subtle);'
}

function labelCn(label: string) {
  return { bullish: '偏多', bearish: '偏空', mixed: '多空交织', neutral: '中性' }[label] || label || '中性'
}

function importanceClass(imp: string) {
  if (imp === 'high' || imp === 'critical') return 'color: var(--color-down);'
  if (imp === 'medium') return 'color: var(--color-warn);'
  return 'color: var(--text-faint);'
}

function importanceCn(imp: string) {
  return { critical: '重大', high: '高', medium: '中', low: '低' }[imp] || (imp || '低')
}
</script>

<template>
  <div class="space-y-3.5 2xl:space-y-5">
    <!-- Header Banner -->
    <div class="panel-banner-compact">
      <div class="flex items-center space-x-2.5 2xl:space-x-3">
        <div class="panel-banner-icon">
          <Newspaper class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
        </div>
        <div>
          <h2 class="text-xs sm:text-[13px] 2xl:text-sm font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
            全网加密重大舆情与流动性情报
          </h2>
          <p class="text-[11px] 2xl:text-xs font-mono mt-0.5" style="color: var(--text-muted);">
            主流财经与链上异动 · 更新于 {{ intel.updated_at || '--' }} (UTC+8)
          </p>
        </div>
      </div>

      <div class="flex items-center space-x-2 2xl:space-x-3">
        <span
          class="h-7 2xl:h-8 px-2.5 2xl:px-3 rounded-[4px] border text-[11px] 2xl:text-xs font-mono font-bold inline-flex items-center space-x-1"
          :style="{
            backgroundColor: breakerActive ? 'var(--color-down-bg)' : 'var(--color-up-bg)',
            borderColor: breakerActive ? 'var(--color-down-border)' : 'var(--color-up-border)',
            color: breakerActive ? 'var(--color-down)' : 'var(--color-up)'
          }"
        >
          <ShieldAlert class="w-3 h-3 2xl:w-3.5 2xl:h-3.5" />
          <span>{{ breakerActive ? '黑天鹅熔断激活' : '常态监控中' }}</span>
        </span>

        <span
          class="h-7 2xl:h-8 px-2.5 2xl:px-3 rounded-[4px] border text-[11px] 2xl:text-xs font-mono inline-flex items-center space-x-1"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <span>宏观情绪:</span>
          <strong style="color: var(--text-main);">{{ macro }}</strong>
        </span>
      </div>
    </div>

    <!-- Coin Sentiment Chips -->
    <div v-if="coinsSentiment.length" class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2.5 2xl:gap-3.5">
      <div
        v-for="[ccy, s] in coinsSentiment"
        :key="ccy"
        class="rounded-xl border p-3 2xl:p-3.5 shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="flex items-center justify-between mb-1.5">
          <span class="text-xs 2xl:text-sm font-black font-mono" style="color: var(--text-main);">{{ ccy }}</span>
          <span class="px-1.5 py-0.2 rounded text-[10px] 2xl:text-xs font-mono font-bold border" :style="labelClass(s.label)">
            {{ labelCn(s.label) }}
          </span>
        </div>
        <div class="flex items-center justify-between text-[11px] 2xl:text-xs font-mono">
          <span style="color: var(--color-up);">多 {{ s.bullish_ratio || s.bullish_pct || '--' }}</span>
          <span style="color: var(--color-down);">空 {{ s.bearish_ratio || s.bearish_pct || '--' }}</span>
        </div>
        <div class="flex items-center justify-between text-[10px] 2xl:text-[11px] font-mono mt-1 pt-1 border-t" style="border-color: var(--border-subtle);">
          <span style="color: var(--text-faint);">提及 {{ (s.mentions ?? 0).toLocaleString() }}</span>
          <span v-if="s.long_short_ratio" class="font-bold text-blue-400">比率 {{ s.long_short_ratio }}</span>
        </div>
      </div>
    </div>

    <!-- News List -->
    <div
      v-if="newsItems.length === 0"
      class="py-16 2xl:py-24 text-center border border-dashed rounded-xl"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
    >
      <p class="text-xs 2xl:text-sm font-mono font-medium">当前市场无破坏性突发黑天鹅或高热度异动，舆情环境平稳。</p>
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-3 2xl:gap-4">
      <div
        v-for="item in newsItems"
        :key="item.id"
        class="rounded-xl border p-4 2xl:p-5 transition-all shadow-xs flex flex-col justify-between"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div>
          <div class="flex items-start justify-between gap-2 mb-2">
            <div class="flex items-start space-x-1.5 min-w-0">
              <Flame class="w-4 h-4 2xl:w-4.5 2xl:h-4.5 shrink-0 mt-0.5" :style="importanceClass(item.importance)" />
              <span class="font-bold text-xs sm:text-sm 2xl:text-base leading-snug font-sans" style="color: var(--text-main);">
                {{ item.title }}
              </span>
            </div>
            <span class="text-[10px] 2xl:text-xs font-mono shrink-0" style="color: var(--text-faint);">
              {{ item.time }}
            </span>
          </div>

          <p class="text-xs 2xl:text-sm leading-relaxed font-sans line-clamp-3" style="color: var(--text-muted);">
            {{ item.summary }}
          </p>
        </div>

        <div class="mt-3 2xl:mt-4 pt-2.5 2xl:pt-3 border-t flex items-center justify-between text-[11px] 2xl:text-xs font-mono" style="border-color: var(--border-subtle); color: var(--text-muted);">
          <span>热度: <strong :style="importanceClass(item.importance)">{{ importanceCn(item.importance) }}</strong></span>
          <span class="flex items-center space-x-2">
            <span>标的: <strong style="color: var(--text-main);">{{ (item.coins || []).join(', ') || 'ALL' }}</strong></span>
            <a
              v-if="item.url"
              :href="item.url"
              target="_blank"
              rel="noopener noreferrer"
              class="flex items-center hover:underline"
              style="color: var(--color-brand);"
            >
              原文<ExternalLink class="w-3 h-3 ml-0.5" />
            </a>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

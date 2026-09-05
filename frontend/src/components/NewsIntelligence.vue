<script setup lang="ts">
import { ref, computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { Newspaper, Flame, ExternalLink, ShieldAlert, Layers } from 'lucide-vue-next'

const store = useDashboardStore()
const intel = computed<any>(() => store.data?.news_intelligence || {})
const allNewsItems = computed<any[]>(() => intel.value.latest_news || [])
const coinsSentiment = computed<[string, any][]>(() => Object.entries(intel.value.coins_sentiment || {}))
const macro = computed<string>(() => intel.value.macro_sentiment || '--')
const breakerActive = computed<boolean>(() => !!intel.value.circuit_breaker?.active)

// Platform Tab Filter: ALL | OKX | Binance | Gate.io
const selectedPlatform = ref<'ALL' | 'OKX' | 'Binance' | 'Gate.io'>('ALL')

const platformCounts = computed(() => {
  const counts: Record<string, number> = { ALL: allNewsItems.value.length, OKX: 0, Binance: 0, 'Gate.io': 0 }
  for (const item of allNewsItems.value) {
    const p = item.platform || (item.platforms && item.platforms[0]) || 'OKX'
    if (counts[p] !== undefined) {
      counts[p]++
    }
  }
  return counts
})

const filteredNews = computed(() => {
  if (selectedPlatform.value === 'ALL') {
    return allNewsItems.value
  }
  return allNewsItems.value.filter(item => {
    const p = item.platform || (item.platforms && item.platforms[0]) || 'OKX'
    return p.toLowerCase() === selectedPlatform.value.toLowerCase()
  })
})

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

function platformBadgeStyle(platform: string) {
  const p = (platform || '').toLowerCase()
  if (p.includes('binance')) return 'background-color: rgba(243, 186, 47, 0.15); color: #f3ba2f; border-color: rgba(243, 186, 47, 0.3);'
  if (p.includes('gate')) return 'background-color: rgba(35, 84, 255, 0.15); color: #2354ff; border-color: rgba(35, 84, 255, 0.3);'
  return 'background-color: rgba(16, 185, 129, 0.15); color: #10b981; border-color: rgba(16, 185, 129, 0.3);'
}
</script>

<template>
  <div class="space-y-3.5">
    <!-- Header Banner -->
    <div
      class="rounded-xl border p-4 sm:p-5 flex flex-wrap items-center justify-between gap-3 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center space-x-3">
        <div
          class="w-9 h-9 rounded-lg flex items-center justify-center border shrink-0"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);"
        >
          <Newspaper class="w-4 h-4" />
        </div>
        <div>
          <h2 class="text-xs sm:text-sm font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
            全网多源重大舆情与流动性情报 (OKX / Binance / Gate)
          </h2>
          <p class="text-xs font-mono mt-0.5" style="color: var(--text-muted);">
            全天候扫描 OKX 突发快讯、币安官方公告与 Gate.io 上线异动 · 更新于 {{ intel.updated_at || '--' }} (UTC+8)
          </p>
        </div>
      </div>

      <div class="flex items-center space-x-2">
        <span
          class="px-2.5 py-1 rounded-lg border text-xs font-mono font-bold"
          :style="{
            backgroundColor: breakerActive ? 'var(--color-down-bg)' : 'var(--color-up-bg)',
            borderColor: breakerActive ? 'var(--color-down-border)' : 'var(--color-up-border)',
            color: breakerActive ? 'var(--color-down)' : 'var(--color-up)'
          }"
        >
          <ShieldAlert class="w-3 h-3 inline mr-1" />
          {{ breakerActive ? '黑天鹅熔断激活' : '常态监控中' }}
        </span>

        <span
          class="px-2.5 py-1 rounded-lg border text-xs font-mono"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          宏观情绪: <strong style="color: var(--text-main);">{{ macro }}</strong>
        </span>
      </div>
    </div>

    <!-- Platform Filter Tabs -->
    <div
      class="rounded-xl border p-2 flex flex-wrap items-center justify-between gap-2 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center gap-1.5 overflow-x-auto">
        <button
          v-for="tab in (['ALL', 'OKX', 'Binance', 'Gate.io'] as const)"
          :key="tab"
          @click="selectedPlatform = tab"
          class="px-3 py-1.5 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer flex items-center gap-1.5"
          :style="selectedPlatform === tab
            ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
            : { backgroundColor: 'var(--bg-card-subtle)', color: 'var(--text-muted)' }"
        >
          <span>{{ tab === 'ALL' ? '全平台全息' : tab }}</span>
          <span class="text-[10px] px-1.5 py-0.2 rounded-full border opacity-80" :style="{ borderColor: 'currentColor' }">
            {{ platformCounts[tab] || 0 }}
          </span>
        </button>
      </div>

      <div class="text-[11px] font-mono px-2 text-right hidden sm:block" style="color: var(--text-faint);">
        展示 {{ filteredNews.length }} 条 · 支持多源独立过滤
      </div>
    </div>

    <!-- Coin Sentiment Chips -->
    <div v-if="coinsSentiment.length" class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2.5">
      <div
        v-for="[ccy, s] in coinsSentiment"
        :key="ccy"
        class="rounded-xl border p-3 shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="flex items-center justify-between mb-1.5">
          <span class="text-xs font-black font-mono" style="color: var(--text-main);">{{ ccy }}</span>
          <span class="px-1.5 py-0.2 rounded text-[10px] font-mono font-bold border" :style="labelClass(s.label)">
            {{ labelCn(s.label) }}
          </span>
        </div>
        <div class="flex items-center justify-between text-[11px] font-mono">
          <span style="color: var(--color-up);">多 {{ s.bullish_ratio || s.bullish_pct || '--' }}</span>
          <span style="color: var(--color-down);">空 {{ s.bearish_ratio || s.bearish_pct || '--' }}</span>
        </div>
        <div class="flex items-center justify-between text-[10px] font-mono mt-1 pt-1 border-t" style="border-color: var(--border-subtle);">
          <span style="color: var(--text-faint);">提及 {{ (s.mentions ?? 0).toLocaleString() }}</span>
          <span v-if="s.long_short_ratio" class="font-bold text-blue-400">比率 {{ s.long_short_ratio }}</span>
        </div>
      </div>
    </div>

    <!-- News List -->
    <div
      v-if="filteredNews.length === 0"
      class="py-16 text-center border border-dashed rounded-xl"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
    >
      <p class="text-xs font-mono font-medium">当前平台筛选下无突发快讯或公告。</p>
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-3">
      <div
        v-for="item in filteredNews"
        :key="item.id"
        class="rounded-xl border p-4 transition-all shadow-xs flex flex-col justify-between"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div>
          <div class="flex items-start justify-between gap-2 mb-2">
            <div class="flex items-start space-x-1.5 min-w-0">
              <Flame class="w-4 h-4 shrink-0 mt-0.5" :style="importanceClass(item.importance)" />
              <span class="font-bold text-xs sm:text-sm leading-snug font-sans" style="color: var(--text-main);">
                {{ item.title }}
              </span>
            </div>
            <span class="text-[10px] font-mono shrink-0" style="color: var(--text-faint);">
              {{ item.time }}
            </span>
          </div>

          <p class="text-xs leading-relaxed font-sans line-clamp-3 mb-3" style="color: var(--text-muted);">
            {{ item.summary }}
          </p>
        </div>

        <div class="mt-2 pt-2.5 border-t flex items-center justify-between text-[11px] font-mono" style="border-color: var(--border-subtle); color: var(--text-muted);">
          <div class="flex items-center gap-1.5">
            <span class="px-2 py-0.5 rounded text-[10px] font-bold border" :style="platformBadgeStyle(item.platform)">
              {{ item.platform || 'OKX' }}
            </span>
            <span>热度: <strong :style="importanceClass(item.importance)">{{ importanceCn(item.importance) }}</strong></span>
          </div>
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

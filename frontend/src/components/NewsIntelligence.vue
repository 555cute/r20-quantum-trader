<script setup lang="ts">
import { computed, ref } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useI18nStore } from '../stores/i18n'
import {
  Newspaper,
  Flame,
  ExternalLink,
  ShieldAlert,
  Layers,
} from 'lucide-vue-next'

const store = useDashboardStore()
const i18n = useI18nStore()
const selectedPlatform = ref<'ALL' | 'OKX' | 'Binance' | 'Gate.io'>('ALL')

const intel = computed(() => store.data?.news_intelligence || {})
const allNews = computed(() => intel.value.latest_news || [])
const coinsSentiment = computed<[string, any][]>(() => Object.entries(intel.value.coins_sentiment || {}))
const macro = computed(() => intel.value.macro_sentiment || '--')
const breakerActive = computed(() => !!intel.value.circuit_breaker?.active)

const platformCounts = computed(() => {
  const counts: Record<string, number> = { ALL: allNews.value.length, OKX: 0, Binance: 0, 'Gate.io': 0 }
  allNews.value.forEach((item: any) => {
    const p = item.platform || (item.platforms && item.platforms[0]) || 'OKX'
    if (p.toLowerCase().includes('okx')) counts.OKX++
    else if (p.toLowerCase().includes('binance')) counts.Binance++
    else if (p.toLowerCase().includes('gate')) counts['Gate.io']++
    else counts.OKX++
  })
  return counts
})

const filteredNews = computed(() => {
  if (selectedPlatform.value === 'ALL') return allNews.value
  return allNews.value.filter((item: any) => {
    const p = item.platform || (item.platforms && item.platforms[0]) || 'OKX'
    return p.toLowerCase() === selectedPlatform.value.toLowerCase()
  })
})

function labelClass(label: string) {
  if (label === 'bullish') return 'background-color: var(--color-up-bg); color: var(--color-up); border-color: var(--color-up-border);'
  if (label === 'bearish') return 'background-color: var(--color-down-bg); color: var(--color-down); border-color: var(--color-down-border);'
  return 'background-color: var(--bg-card-subtle); color: var(--text-muted); border-color: var(--border-subtle);'
}

function labelCn(label: string) {
  if (label === 'bullish') return i18n.locale === 'zh' ? '看多' : 'Bullish'
  if (label === 'bearish') return i18n.locale === 'zh' ? '看空' : 'Bearish'
  return i18n.locale === 'zh' ? '中性' : 'Neutral'
}

function platformBadgeStyle(platform: string) {
  const p = (platform || '').toLowerCase()
  if (p.includes('binance')) return 'background-color: rgba(243, 186, 47, 0.15); color: #f3ba2f; border-color: rgba(243, 186, 47, 0.3);'
  if (p.includes('gate')) return 'background-color: rgba(35, 84, 255, 0.15); color: #2354ff; border-color: rgba(35, 84, 255, 0.3);'
  return 'background-color: rgba(16, 185, 129, 0.15); color: #10b981; border-color: rgba(16, 185, 129, 0.3);'
}
</script>

<template>
  <div class="space-y-3.5 font-mono">
    <!-- Header Banner -->
    <div
      class="rounded-xl border p-3.5 sm:p-4 flex flex-wrap items-center justify-between gap-3 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center space-x-3">
        <div
          class="w-8 h-8 sm:w-9 sm:h-9 rounded-lg flex items-center justify-center border shrink-0"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);"
        >
          <Newspaper class="w-4 h-4" />
        </div>
        <div>
          <h2 class="text-xs sm:text-sm font-black uppercase tracking-wide" style="color: var(--text-main);">
            {{ i18n.t.newsTitle }}
          </h2>
          <p class="text-[11px] sm:text-xs mt-0.5" style="color: var(--text-muted);">
            {{ i18n.t.newsSubtitle }}
          </p>
        </div>
      </div>

      <div class="flex items-center space-x-2">
        <span
          class="px-2.5 py-1 rounded-lg border text-[11px] sm:text-xs font-bold"
          :style="{
            backgroundColor: breakerActive ? 'var(--color-down-bg)' : 'var(--color-up-bg)',
            borderColor: breakerActive ? 'var(--color-down-border)' : 'var(--color-up-border)',
            color: breakerActive ? 'var(--color-down)' : 'var(--color-up)'
          }"
        >
          <ShieldAlert class="w-3 h-3 inline mr-1" />
          {{ breakerActive ? i18n.t.circuitBreakerActive : i18n.t.circuitBreakerNormal }}
        </span>

        <span
          class="px-2.5 py-1 rounded-lg border text-[11px] sm:text-xs"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          宏观: <strong style="color: var(--text-main);">{{ macro }}</strong>
        </span>
      </div>
    </div>

    <!-- Platform Filter Tabs (完美适配移动端与桌面端自适应比例) -->
    <div
      class="rounded-xl border p-1.5 sm:p-2 flex flex-wrap items-center justify-between gap-2 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="grid grid-cols-2 sm:flex items-center gap-1.5 w-full sm:w-auto">
        <button
          v-for="tab in (['ALL', 'OKX', 'Binance', 'Gate.io'] as const)"
          :key="tab"
          @click="selectedPlatform = tab"
          class="px-2.5 sm:px-3 py-1.5 rounded-lg text-xs font-bold transition-all cursor-pointer flex items-center justify-center sm:justify-start gap-1.5"
          :style="selectedPlatform === tab
            ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-app)' }
            : { backgroundColor: 'var(--bg-card-subtle)', color: 'var(--text-muted)' }"
        >
          <span>{{ tab === 'ALL' ? i18n.t.filterAll : tab }}</span>
          <span class="text-[10px] px-1.5 py-0.2 rounded-full border opacity-80" :style="{ borderColor: 'currentColor' }">
            {{ platformCounts[tab] || 0 }}
          </span>
        </button>
      </div>

      <div class="text-[11px] px-2 text-right hidden lg:block" style="color: var(--text-faint);">
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
          <span class="text-xs font-black" style="color: var(--text-main);">{{ ccy }}</span>
          <span class="px-1.5 py-0.2 rounded text-[10px] font-bold border" :style="labelClass(s.label)">
            {{ labelCn(s.label) }}
          </span>
        </div>
        <div class="flex items-center justify-between text-[11px]">
          <span style="color: var(--color-up);">多 {{ s.bullish_ratio || s.bullish_pct || '--' }}</span>
          <span style="color: var(--color-down);">空 {{ s.bearish_ratio || s.bearish_pct || '--' }}</span>
        </div>
      </div>
    </div>

    <!-- News List / Cards -->
    <div v-if="filteredNews.length === 0" class="py-16 text-center rounded-xl border border-dashed" style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);">
      <div class="w-10 h-10 mx-auto mb-2 rounded-xl flex items-center justify-center border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
        <Layers class="w-4 h-4" />
      </div>
      <p class="text-xs">{{ i18n.t.noNews }}</p>
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-3">
      <div
        v-for="item in filteredNews"
        :key="item.id"
        class="rounded-xl border p-3.5 sm:p-4 flex flex-col justify-between space-y-3 transition-colors hover:border-[var(--border-medium)]"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div>
          <div class="flex items-start justify-between gap-2 mb-1.5">
            <div class="flex items-start space-x-2 min-w-0">
              <Flame class="w-3.5 h-3.5 shrink-0 mt-0.5" style="color: var(--color-down);" />
              <h3 class="font-bold text-xs sm:text-sm line-clamp-2" style="color: var(--text-main);">
                {{ item.title }}
              </h3>
            </div>
            <span class="text-[10px] shrink-0" style="color: var(--text-faint);">
              {{ item.time }}
            </span>
          </div>

          <p class="text-xs line-clamp-3 leading-relaxed" style="color: var(--text-muted);">
            {{ item.summary }}
          </p>
        </div>

        <div class="pt-2 border-t flex items-center justify-between text-[11px]" style="border-color: var(--border-subtle); color: var(--text-faint);">
          <div class="flex items-center space-x-2">
            <span class="px-1.5 py-0.2 rounded border font-bold text-[10px]" :style="platformBadgeStyle(item.platform)">
              {{ item.platform || 'OKX' }}
            </span>
            <span v-if="item.coins && item.coins.length">
              标的: <strong style="color: var(--text-muted);">{{ item.coins.join(', ') }}</strong>
            </span>
          </div>

          <a
            v-if="item.url"
            :href="item.url"
            target="_blank"
            rel="noopener noreferrer"
            class="flex items-center space-x-1 hover:underline text-indigo-400"
          >
            <span>{{ i18n.t.originalLink }}</span>
            <ExternalLink class="w-3 h-3" />
          </a>
        </div>
      </div>
    </div>
  </div>
</template>

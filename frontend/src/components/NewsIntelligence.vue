<script setup lang="ts">
import { computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useI18n } from '../composables/useI18n'
import { Newspaper, Flame, ExternalLink, ShieldAlert, RefreshCw, Radio } from 'lucide-vue-next'

const store = useDashboardStore()
const { t } = useI18n()
const intel = computed<any>(() => store.data?.news_intelligence || {})
const newsItems = computed<any[]>(() => intel.value.latest_news || [])
const coinsSentiment = computed<[string, any][]>(() => Object.entries(intel.value.coins_sentiment || {}))
const macro = computed<string>(() => intel.value.macro_sentiment || '--')
const breakerActive = computed<boolean>(() => !!intel.value.circuit_breaker?.active)
// Upstream harvest failed this cycle -> the list below is the last good cache.
const isStaleFeed = computed<boolean>(() => !!intel.value.stale_sections)

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
  if (imp === 'high' || imp === 'critical') return 'color: var(--color-warn); font-weight: 900;'
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
          <div class="flex items-center space-x-2">
            <h2 class="text-xs sm:text-[13px] 2xl:text-sm font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
              {{ t('news.title') }}
            </h2>
            <span
              v-if="isStaleFeed"
              class="hidden md:inline-flex items-center space-x-1 text-[11px] 2xl:text-[11px] font-mono px-2 py-0.5 rounded-[4px] border"
              style="background-color: var(--color-warn-bg); border-color: var(--color-warn-border); color: var(--color-warn);"
              title="上游资讯源本轮抓取失败，页面展示的是最近一次成功的缓存内容"
            >
              <span class="w-1.5 h-1.5 rounded-full" style="background-color: var(--color-warn);"></span>
              <span>{{ t('news.staleTag') }}</span>
            </span>
            <span v-else class="hidden md:inline-flex items-center space-x-1 text-[11px] 2xl:text-[11px] font-mono px-2 py-0.5 rounded-[4px] border" style="background-color: var(--color-up-bg); border-color: var(--color-up-border); color: var(--color-up);">
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
              <span>{{ t('news.autoUpdate') }}</span>
            </span>
          </div>
          <p class="text-[11px] 2xl:text-xs font-mono mt-0.5" style="color: var(--text-muted);">
            主流财经与链上异动 · 抓取于 {{ intel.updated_at || '--' }}
            <span v-if="intel.news_fresh_at" style="color: var(--text-faint);">· 最新快讯 {{ intel.news_fresh_at }}</span>
            (UTC+8)
          </p>
        </div>
      </div>

      <div class="flex items-center space-x-2 2xl:space-x-3">
        <button
          @click="store.fetchDashboard(false)"
          :disabled="store.isRefreshing"
          class="h-7 2xl:h-8 px-2 2xl:px-2.5 rounded-[4px] border text-[11px] 2xl:text-xs font-mono inline-flex items-center space-x-1 hover:bg-[var(--bg-card-hover)] transition-colors cursor-pointer"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
          title="立即手动刷新最新快讯与舆情"
        >
          <RefreshCw class="w-3 h-3" :class="store.isRefreshing ? 'animate-spin text-blue-400' : ''" />
          <span class="hidden sm:inline">{{ store.isRefreshing ? t('news.syncing') : t('news.refresh') }}</span>
        </button>

        <span
          class="h-7 2xl:h-8 px-2.5 2xl:px-3 rounded-[4px] border text-[11px] 2xl:text-xs font-mono font-bold inline-flex items-center space-x-1"
          :style="{
            backgroundColor: breakerActive ? 'var(--color-down-bg)' : 'var(--color-up-bg)',
            borderColor: breakerActive ? 'var(--color-down-border)' : 'var(--color-up-border)',
            color: breakerActive ? 'var(--color-down)' : 'var(--color-up)'
          }"
        >
          <ShieldAlert class="w-3 h-3 2xl:w-3.5 2xl:h-3.5" />
          <span>{{ breakerActive ? t('news.breakerOn') : t('news.breakerOff') }}</span>
        </span>

        <span
          class="h-7 2xl:h-8 px-2.5 2xl:px-3 rounded-[4px] border text-[11px] 2xl:text-xs font-mono inline-flex items-center space-x-1"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <span>{{ t('news.macroLabel') }}:</span>
          <strong style="color: var(--text-main);">{{ macro }}</strong>
        </span>
      </div>
    </div>

    <!-- P1: Long/Short temperature band (L4: aggregate distribution belongs in one strip, not a card grid) -->
    <div
      v-if="coinsSentiment.length"
      class="rounded-xl border px-3 sm:px-4 py-2.5 flex flex-wrap items-center gap-x-6 gap-y-2 shadow-xs"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <span class="text-[11px] font-mono font-bold uppercase tracking-wider shrink-0" style="color: var(--text-muted);">{{ t('news.tempBand') }}</span>
      <div
        v-for="[ccy, s] in coinsSentiment"
        :key="ccy"
        class="flex items-center space-x-2 min-w-0"
        :title="`${t('news.mentions')} ${((s.mentions ?? 0)).toLocaleString()}${s.long_short_ratio ? ' · ' + t('news.lsRatio') + ' ' + s.long_short_ratio : ''}`"
      >
        <span class="text-xs font-mono font-black" style="color: var(--text-main);">{{ ccy }}</span>
        <!-- bull/bear ratio micro-bar -->
        <span class="w-14 h-1 rounded-full overflow-hidden flex shrink-0" style="background-color: var(--bg-badge);">
          <span
            class="h-full"
            :style="{ width: Math.min(100, Number(s.bullish_ratio || s.bullish_pct || 50)) + '%', backgroundColor: 'var(--color-up)' }"
          ></span>
        </span>
        <span class="text-[11px] font-mono num-tabular shrink-0" :style="{ color: s.label === 'bullish' ? 'var(--color-up)' : s.label === 'bearish' ? 'var(--color-down)' : 'var(--text-muted)' }">
          {{ labelCn(s.label) }}
        </span>
      </div>
      <span class="text-[11px] font-mono ml-auto shrink-0" style="color: var(--text-faint);">
        {{ t('news.macroLabel') }} <strong style="color: var(--text-main);">{{ macro }}</strong>
      </span>
    </div>

    <!-- News List -->
    <div
      v-if="newsItems.length === 0"
      class="py-16 2xl:py-24 text-center border border-dashed rounded-xl"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
    >
      <p class="text-xs 2xl:text-sm font-mono font-medium">{{ t('news.calmEmpty') }}</p>
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
            <span class="text-[11px] 2xl:text-xs font-mono shrink-0" style="color: var(--text-faint);">
              {{ item.time }}
            </span>
          </div>

          <p class="text-xs 2xl:text-sm leading-relaxed font-sans line-clamp-3" style="color: var(--text-muted);">
            {{ item.summary }}
          </p>
        </div>

        <div class="mt-3 2xl:mt-4 pt-2.5 2xl:pt-3 border-t flex items-center justify-between text-[11px] 2xl:text-xs font-mono" style="border-color: var(--border-subtle); color: var(--text-muted);">
          <span>{{ t('news.hot') }}: <strong :style="importanceClass(item.importance)">{{ importanceCn(item.importance) }}</strong></span>
          <span class="flex items-center space-x-2">
            <span>{{ t('news.target') }}: <strong style="color: var(--text-main);">{{ (item.coins || []).join(', ') || 'ALL' }}</strong></span>
            <a
              v-if="item.url"
              :href="item.url"
              target="_blank"
              rel="noopener noreferrer"
              class="flex items-center hover:underline"
              style="color: var(--color-brand);"
            >
              {{ t('news.source') }}<ExternalLink class="w-3 h-3 ml-0.5" />
            </a>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

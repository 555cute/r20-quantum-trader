<script setup lang="ts">
import { computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { Newspaper, Flame, ExternalLink, ShieldAlert, RefreshCw } from 'lucide-vue-next'

const store = useDashboardStore()
const intel = computed<Record<string, any>>(() => store.data?.news_intelligence || {})
const newsItems = computed<any[]>(() => Array.isArray(intel.value.latest_news) ? intel.value.latest_news : [])
const coinsSentiment = computed<[string, any][]>(() => {
  const raw = intel.value.coins_sentiment
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  return Object.entries(raw).filter(([, s]) => s && typeof s === 'object')
})
const macro = computed<string>(() => {
  const raw = intel.value.macro_sentiment
  if (typeof raw !== 'string') return ''
  const trimmed = raw.trim()
  if (!trimmed || trimmed === '--') return ''
  return trimmed
})
const breakerActive = computed<boolean>(() => !!intel.value.circuit_breaker?.active)
const isStaleFeed = computed<boolean>(() => !!intel.value.stale_sections)
const feedStatus = computed<string>(() => String(intel.value.status || ''))

type BadgeTone = 'up' | 'warn' | 'down' | 'muted'

const statusBadge = computed<{ text: string; tone: BadgeTone }>(() => {
  const status = feedStatus.value
  if (status === 'disabled') return { text: '新闻源已关闭', tone: 'muted' }
  if (status === 'pending') return { text: '待采集', tone: 'warn' }
  if (status === 'partial') return { text: '部分来源失败', tone: 'warn' }
  if (status === 'error') return { text: '采集失败', tone: 'down' }
  if (status === 'ok' && isStaleFeed.value) return { text: '数据源延迟 · 展示缓存', tone: 'warn' }
  if (status === 'ok') return { text: '情报可用', tone: 'up' }
  if (isStaleFeed.value) return { text: '数据源延迟 · 展示缓存', tone: 'warn' }
  return { text: '状态未知', tone: 'muted' }
})

const emptyCopy = computed(() => {
  const status = feedStatus.value
  if (status === 'disabled') return '新闻来源已全部关闭，当前不展示采集内容。'
  if (status === 'pending') return '尚未完成采集，请稍后刷新。'
  if (status === 'partial') return '部分来源采集失败，当前没有可展示的快讯。'
  if (status === 'error') return '采集失败，暂无可用快讯。'
  if (isStaleFeed.value) return '缓存已过期，当前没有可展示的快讯。'
  return '当前没有可展示的快讯。'
})

function badgeStyle(tone: BadgeTone): Record<string, string> {
  if (tone === 'up') {
    return { backgroundColor: 'var(--color-up-bg)', borderColor: 'var(--color-up-border)', color: 'var(--color-up)' }
  }
  if (tone === 'down') {
    return { backgroundColor: 'var(--color-down-bg)', borderColor: 'var(--color-down-border)', color: 'var(--color-down)' }
  }
  if (tone === 'warn') {
    return { backgroundColor: 'var(--color-warn-bg)', borderColor: 'var(--color-warn-border)', color: 'var(--color-warn)' }
  }
  return { backgroundColor: 'var(--bg-badge)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }
}

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

function itemCoins(item: { coins?: unknown }): string[] {
  return Array.isArray(item.coins) ? item.coins.filter((c): c is string => typeof c === 'string' && c.trim().length > 0) : []
}

function ratioText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '--'
  return String(value)
}

function safeHttpUrl(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  const trimmed = raw.trim()
  if (!trimmed) return null
  try {
    const parsed = new URL(trimmed)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null
    return parsed.href
  } catch {
    return null
  }
}

function itemHref(item: { url?: unknown }): string | null {
  return safeHttpUrl(item.url)
}
</script>

<template>
  <div class="space-y-3.5 2xl:space-y-5 min-w-0">
    <div class="panel-banner-compact">
      <div class="flex items-center space-x-2.5 2xl:space-x-3 min-w-0">
        <div class="panel-banner-icon">
          <Newspaper class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
        </div>
        <div class="min-w-0">
          <div class="flex items-center flex-wrap gap-2">
            <h2 class="text-xs sm:text-[13px] 2xl:text-sm font-black font-mono uppercase tracking-wide break-words" style="color: var(--text-main);">
              全网加密重大舆情与流动性情报
            </h2>
            <span
              class="inline-flex items-center space-x-1 text-[10px] 2xl:text-[11px] font-mono px-2 py-0.5 rounded-[4px] border"
              :style="badgeStyle(statusBadge.tone)"
            >
              <span
                class="w-1.5 h-1.5 rounded-full"
                :class="statusBadge.tone === 'up' ? 'bg-emerald-400 animate-pulse' : ''"
                :style="statusBadge.tone === 'up' ? {} : { backgroundColor: 'currentColor' }"
              ></span>
              <span>{{ statusBadge.text }}</span>
            </span>
          </div>
          <p class="text-[11px] 2xl:text-xs font-mono mt-0.5 break-words" style="color: var(--text-muted);">
            主流财经与链上异动 · 抓取于 {{ intel.updated_at || '--' }}
            <span v-if="intel.news_fresh_at" style="color: var(--text-faint);">· 最新快讯 {{ intel.news_fresh_at }}</span>
            (UTC+8)
          </p>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2 2xl:gap-3">
        <button
          type="button"
          @click="store.fetchDashboard(false)"
          :disabled="store.isRefreshing"
          class="h-7 2xl:h-8 px-2 2xl:px-2.5 rounded-[4px] border text-[11px] 2xl:text-xs font-mono inline-flex items-center space-x-1 hover:bg-[var(--bg-card-hover)] transition-colors cursor-pointer disabled:opacity-40"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
          title="立即手动刷新最新快讯与舆情"
        >
          <RefreshCw class="w-3 h-3" :class="store.isRefreshing ? 'animate-spin text-blue-400' : ''" />
          <span class="hidden sm:inline">{{ store.isRefreshing ? '同步中' : '刷新' }}</span>
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
          <span>{{ breakerActive ? '黑天鹅熔断激活' : '未激活新闻熔断' }}</span>
        </span>

        <span
          v-if="macro"
          class="h-7 2xl:h-8 px-2.5 2xl:px-3 rounded-[4px] border text-[11px] 2xl:text-xs font-mono inline-flex items-center space-x-1"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <span>宏观情绪:</span>
          <strong style="color: var(--text-main);">{{ macro }}</strong>
        </span>
      </div>
    </div>

    <div v-if="coinsSentiment.length" class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2.5 2xl:gap-3.5">
      <div
        v-for="[ccy, s] in coinsSentiment"
        :key="ccy"
        class="rounded-xl border p-3 2xl:p-3.5 shadow-xs transition-colors min-w-0"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="flex items-center justify-between mb-1.5 gap-1 min-w-0">
          <span class="text-xs 2xl:text-sm font-black font-mono truncate" style="color: var(--text-main);">{{ ccy }}</span>
          <span class="px-1.5 py-0.2 rounded text-[10px] 2xl:text-xs font-mono font-bold border shrink-0" :style="labelClass(s.label)">
            {{ labelCn(s.label) }}
          </span>
        </div>
        <div class="flex items-center justify-between text-[11px] 2xl:text-xs font-mono gap-1">
          <span style="color: var(--color-up);">多 {{ ratioText(s.bullish_ratio ?? s.bullish_pct) }}</span>
          <span style="color: var(--color-down);">空 {{ ratioText(s.bearish_ratio ?? s.bearish_pct) }}</span>
        </div>
        <div class="flex items-center justify-between text-[10px] 2xl:text-[11px] font-mono mt-1 pt-1 border-t gap-1" style="border-color: var(--border-subtle);">
          <span style="color: var(--text-faint);">提及 {{ (s.mentions ?? 0).toLocaleString() }}</span>
          <span v-if="s.long_short_ratio" class="font-bold text-blue-400 truncate">比率 {{ s.long_short_ratio }}</span>
        </div>
      </div>
    </div>

    <div
      v-if="newsItems.length === 0"
      class="py-16 2xl:py-24 text-center border border-dashed rounded-xl px-4"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
    >
      <p class="text-xs 2xl:text-sm font-mono font-medium break-words">{{ emptyCopy }}</p>
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-3 2xl:gap-4">
      <div
        v-for="item in newsItems"
        :key="item.id || item.url || item.title"
        class="rounded-xl border p-4 2xl:p-5 transition-all shadow-xs flex flex-col justify-between min-w-0"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="min-w-0">
          <div class="flex items-start justify-between gap-2 mb-2">
            <div class="flex items-start space-x-1.5 min-w-0">
              <Flame class="w-4 h-4 2xl:w-4.5 2xl:h-4.5 shrink-0 mt-0.5" :style="importanceClass(item.importance)" />
              <span class="font-bold text-xs sm:text-sm 2xl:text-base leading-snug font-sans break-words" style="color: var(--text-main);">
                {{ item.title }}
              </span>
            </div>
            <span class="text-[10px] 2xl:text-xs font-mono shrink-0" style="color: var(--text-faint);">
              {{ item.time }}
            </span>
          </div>

          <div class="flex flex-wrap items-center gap-1.5 mb-2">
            <span
              v-if="item.source_name"
              class="text-[10px] font-mono px-1.5 py-0.5 rounded border"
              style="border-color: var(--border-subtle); color: var(--text-muted);"
            >
              {{ item.source_name }}
            </span>
            <span
              v-if="item.category"
              class="text-[10px] font-mono px-1.5 py-0.5 rounded border"
              style="border-color: var(--border-subtle); color: var(--text-muted);"
            >
              {{ item.category }}
            </span>
            <span
              v-if="item.stale"
              class="text-[10px] font-mono px-1.5 py-0.5 rounded border"
              style="background-color: var(--color-warn-bg); border-color: var(--color-warn-border); color: var(--color-warn);"
            >
              过期缓存
            </span>
          </div>

          <p v-if="item.summary" class="text-xs 2xl:text-sm leading-relaxed font-sans line-clamp-3 break-words" style="color: var(--text-muted);">
            {{ item.summary }}
          </p>
        </div>

        <div class="mt-3 2xl:mt-4 pt-2.5 2xl:pt-3 border-t flex flex-wrap items-center justify-between gap-2 text-[11px] 2xl:text-xs font-mono" style="border-color: var(--border-subtle); color: var(--text-muted);">
          <span>热度: <strong :style="importanceClass(item.importance)">{{ importanceCn(item.importance) }}</strong></span>
          <span class="flex items-center flex-wrap gap-2 min-w-0">
            <span v-if="itemCoins(item).length">标的: <strong style="color: var(--text-main);">{{ itemCoins(item).join(', ') }}</strong></span>
            <a
              v-if="itemHref(item)"
              :href="itemHref(item) || undefined"
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

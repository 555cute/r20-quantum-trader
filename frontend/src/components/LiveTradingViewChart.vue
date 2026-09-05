<script setup lang="ts">
import { ref, watch, onMounted, nextTick } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useI18nStore } from '../stores/i18n'
import {
  LineChart,
  Maximize2,
  Minimize2,
  RefreshCw,
  ChevronDown,
  Activity,
  Layers,
} from 'lucide-vue-next'

const store = useDashboardStore()
const i18n = useI18nStore()
const containerRef = ref<HTMLDivElement | null>(null)
const isExpanded = ref(true)

const symbolList = [
  { id: 'BTC', name: 'BTC', tvSymbol: 'BINANCE:BTCUSDT' },
  { id: 'ETH', name: 'ETH', tvSymbol: 'BINANCE:ETHUSDT' },
  { id: 'SOL', name: 'SOL', tvSymbol: 'BINANCE:SOLUSDT' },
  { id: 'DOGE', name: 'DOGE', tvSymbol: 'BINANCE:DOGEUSDT' },
  { id: 'XRP', name: 'XRP', tvSymbol: 'BINANCE:XRPUSDT' },
  { id: 'PEPE', name: 'PEPE', tvSymbol: 'BINANCE:PEPEUSDT' },
]

const intervalList = [
  { id: '15', label: '15m' },
  { id: '60', label: '1H' },
  { id: '240', label: '4H' },
  { id: 'D', label: '1D' },
]

const selectedSymbol = ref(symbolList[0])
const selectedInterval = ref(intervalList[1])

function initTradingViewWidget() {
  if (!containerRef.value) return
  containerRef.value.innerHTML = ''

  const widgetContainer = document.createElement('div')
  widgetContainer.className = 'tradingview-widget-container__widget'
  widgetContainer.style.height = '100%'
  widgetContainer.style.width = '100%'
  containerRef.value.appendChild(widgetContainer)

  const script = document.createElement('script')
  script.src = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js'
  script.type = 'text/javascript'
  script.async = true
  script.innerHTML = JSON.stringify({
    autosize: true,
    symbol: selectedSymbol.value.tvSymbol,
    interval: selectedInterval.value.id,
    timezone: 'Asia/Shanghai',
    theme: 'dark',
    style: '1',
    locale: i18n.locale === 'zh' ? 'zh_CN' : 'en',
    enable_publishing: false,
    hide_top_toolbar: false,
    hide_legend: false,
    save_image: false,
    backgroundColor: 'rgba(11, 15, 25, 1)',
    gridColor: 'rgba(255, 255, 255, 0.05)',
    allow_symbol_change: true,
    calendar: false,
    support_host: 'https://www.tradingview.com',
  })
  containerRef.value.appendChild(script)
}

onMounted(() => {
  initTradingViewWidget()
})

watch([selectedSymbol, selectedInterval, () => i18n.locale], () => {
  nextTick(() => {
    initTradingViewWidget()
  })
})
</script>

<template>
  <div
    class="rounded-xl border shadow-xs transition-colors overflow-hidden font-mono"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <!-- Header Controls Ribbon -->
    <div
      class="px-3 sm:px-4 py-2 border-b flex flex-wrap items-center justify-between gap-2.5"
      style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);"
    >
      <div class="flex items-center space-x-2">
        <div
          class="w-6 h-6 rounded flex items-center justify-center border shadow-xs"
          style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);"
        >
          <LineChart class="w-3.5 h-3.5" />
        </div>
        <div>
          <span class="text-xs font-black tracking-wide" style="color: var(--text-main);">
            {{ i18n.t.chartTitle }}
          </span>
          <span class="ml-2 text-[10px] px-1.5 py-0.2 rounded border text-emerald-400 border-emerald-500/30 bg-emerald-500/10">
            {{ i18n.t.tradingViewEngine }}
          </span>
        </div>
      </div>

      <!-- Controls: Symbol, Timeframe, Toggle -->
      <div class="flex items-center space-x-1.5 sm:space-x-2 text-xs">
        <!-- Symbol Selector Pills -->
        <div class="flex items-center space-x-1 p-0.5 rounded border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <button
            v-for="sym in symbolList"
            :key="sym.id"
            @click="selectedSymbol = sym"
            class="px-2 py-0.5 rounded text-[10px] font-bold transition-all cursor-pointer"
            :style="selectedSymbol.id === sym.id
              ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-app)' }
              : { color: 'var(--text-muted)' }"
          >
            {{ sym.name }}
          </button>
        </div>

        <!-- Timeframe Selector Pills -->
        <div class="flex items-center space-x-1 p-0.5 rounded border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <button
            v-for="iv in intervalList"
            :key="iv.id"
            @click="selectedInterval = iv"
            class="px-2 py-0.5 rounded text-[10px] font-bold transition-all cursor-pointer"
            :style="selectedInterval.id === iv.id
              ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-app)' }
              : { color: 'var(--text-muted)' }"
          >
            {{ iv.label }}
          </button>
        </div>

        <!-- Reload Widget Button -->
        <button
          @click="initTradingViewWidget"
          class="w-7 h-7 rounded border flex items-center justify-center cursor-pointer transition-colors hover:bg-[var(--bg-card-hover)]"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          :title="i18n.t.refresh"
        >
          <RefreshCw class="w-3.5 h-3.5" />
        </button>

        <!-- Toggle Collapse/Expand -->
        <button
          @click="isExpanded = !isExpanded"
          class="h-7 px-2 rounded border flex items-center space-x-1 cursor-pointer transition-colors hover:bg-[var(--bg-card-hover)] text-[11px]"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <span>{{ isExpanded ? i18n.t.collapse : i18n.t.expand }}</span>
        </button>
      </div>
    </div>

    <!-- Chart Body Container -->
    <div
      v-show="isExpanded"
      ref="containerRef"
      class="w-full transition-all duration-300 relative"
      style="height: 520px; background-color: #0b0f19;"
    ></div>
  </div>
</template>

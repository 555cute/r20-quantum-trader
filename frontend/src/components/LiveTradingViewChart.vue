<script setup lang="ts">
import { ref, onMounted, watch, nextTick } from 'vue'
import { LineChart, BarChart2, Maximize2, RefreshCw } from 'lucide-vue-next'

// Trading symbols supported across OKX / Binance / Gate
const symbols = [
  { id: 'BTC', label: 'BTC/USDT', tvSymbol: 'BINANCE:BTCUSDT' },
  { id: 'ETH', label: 'ETH/USDT', tvSymbol: 'BINANCE:ETHUSDT' },
  { id: 'SOL', label: 'SOL/USDT', tvSymbol: 'BINANCE:SOLUSDT' },
  { id: 'DOGE', label: 'DOGE/USDT', tvSymbol: 'BINANCE:DOGEUSDT' },
  { id: 'XRP', label: 'XRP/USDT', tvSymbol: 'BINANCE:XRPUSDT' },
  { id: 'PEPE', label: 'PEPE/USDT', tvSymbol: 'BINANCE:PEPEUSDT' },
]

const intervals = [
  { id: '15', label: '15m' },
  { id: '60', label: '1H' },
  { id: '240', label: '4H' },
  { id: 'D', label: '1D' },
]

const selectedSymbol = ref(symbols[0])
const selectedInterval = ref(intervals[1]) // Default 1H
const isCollapsed = ref(false)
const containerRef = ref<HTMLElement | null>(null)

function initTradingViewWidget() {
  if (!containerRef.value) return
  containerRef.value.innerHTML = '' // Clear existing

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
    style: '1', // Candlestick
    locale: 'zh_CN',
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

watch([selectedSymbol, selectedInterval], () => {
  nextTick(() => {
    initTradingViewWidget()
  })
})
</script>

<template>
  <div
    class="rounded-xl border shadow-xs transition-colors overflow-hidden"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <!-- Header Controls Ribbon -->
    <div
      class="px-4 py-2.5 border-b flex flex-wrap items-center justify-between gap-3"
      style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);"
    >
      <div class="flex items-center space-x-2.5">
        <div
          class="w-7 h-7 rounded-lg flex items-center justify-center border shadow-xs"
          style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);"
        >
          <LineChart class="w-4 h-4" />
        </div>
        <div>
          <span class="text-xs font-black font-mono tracking-wide" style="color: var(--text-main);">
            实盘专业 K 线视窗 · 全要素微积分行情联动
          </span>
          <span class="ml-2 text-[10px] font-mono px-1.5 py-0.2 rounded border text-emerald-400 border-emerald-500/30 bg-emerald-500/10">
            TradingView 原生引擎
          </span>
        </div>
      </div>

      <!-- Controls: Symbol, Timeframe, Toggle -->
      <div class="flex items-center gap-2">
        <!-- Symbol Selector -->
        <div class="flex items-center rounded-lg border p-0.5 text-xs font-mono" style="background-color: var(--bg-input); border-color: var(--border-subtle);">
          <button
            v-for="sym in symbols"
            :key="sym.id"
            @click="selectedSymbol = sym"
            class="px-2 py-1 rounded transition-all cursor-pointer text-[11px] font-bold"
            :style="selectedSymbol.id === sym.id
              ? { backgroundColor: 'var(--color-brand-bg)', color: 'var(--color-brand)' }
              : { color: 'var(--text-muted)' }"
          >
            {{ sym.id }}
          </button>
        </div>

        <!-- Interval Selector -->
        <div class="hidden sm:flex items-center rounded-lg border p-0.5 text-xs font-mono" style="background-color: var(--bg-input); border-color: var(--border-subtle);">
          <button
            v-for="int in intervals"
            :key="int.id"
            @click="selectedInterval = int"
            class="px-2 py-1 rounded transition-all cursor-pointer text-[11px] font-bold"
            :style="selectedInterval.id === int.id
              ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
              : { color: 'var(--text-muted)' }"
          >
            {{ int.label }}
          </button>
        </div>

        <!-- Refresh / Reload -->
        <button
          @click="initTradingViewWidget"
          class="p-1.5 rounded-lg border cursor-pointer hover:opacity-80 transition-opacity"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          title="刷新图表"
        >
          <RefreshCw class="w-3.5 h-3.5" />
        </button>

        <!-- Toggle Collapse -->
        <button
          @click="isCollapsed = !isCollapsed"
          class="px-2 py-1 rounded-lg border text-[11px] font-mono cursor-pointer hover:opacity-80 transition-opacity"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          {{ isCollapsed ? '展开图表' : '折叠' }}
        </button>
      </div>
    </div>

    <!-- Chart Container Area -->
    <div
      v-show="!isCollapsed"
      class="w-full relative"
      style="height: 480px; min-height: 400px; background-color: #0b0f19;"
    >
      <div ref="containerRef" class="w-full h-full tradingview-widget-container"></div>
    </div>
  </div>
</template>

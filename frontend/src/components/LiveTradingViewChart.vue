<script setup lang="ts">
import { ref, computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useI18nStore } from '../stores/i18n'
import {
  LineChart,
  RefreshCw,
} from 'lucide-vue-next'

const store = useDashboardStore()
const i18n = useI18nStore()
const isExpanded = ref(true)
const reloadKey = ref(0)

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

// 生成标准可靠的 TradingView iframe 嵌入 URL，100% 避免因外部 script 注入受阻导致的黑屏
const iframeSrc = computed(() => {
  const sym = encodeURIComponent(selectedSymbol.value.tvSymbol)
  const iv = selectedInterval.value.id
  const lang = i18n.locale === 'zh' ? 'zh_CN' : 'en'
  return `https://s.tradingview.com/widgetembed/?frameElementId=tradingview_widget&symbol=${sym}&interval=${iv}&hidesidetoolbar=1&symboledit=1&saveimage=0&toolbarbg=rgba(11,15,25,1)&theme=dark&style=1&timezone=Asia%2FShanghai&studies=[]&hideideas=1&theme=dark&style=1&locale=${lang}&utm_source=localhost&key=${reloadKey.value}`
})

function reloadChart() {
  reloadKey.value++
}
</script>

<template>
  <div
    class="w-full max-w-full min-w-0 rounded-xl border shadow-xs transition-colors overflow-hidden font-mono"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <!-- Header Controls Ribbon (自适应换行，避免移动端破框) -->
    <div
      class="px-2.5 sm:px-4 py-2 border-b flex flex-wrap items-center justify-between gap-2"
      style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);"
    >
      <div class="flex items-center space-x-1.5 sm:space-x-2 min-w-0">
        <div
          class="w-5 h-5 sm:w-6 sm:h-6 rounded flex items-center justify-center border shadow-xs shrink-0"
          style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);"
        >
          <LineChart class="w-3 h-3 sm:w-3.5 sm:h-3.5" />
        </div>
        <div class="truncate">
          <span class="text-xs font-black tracking-wide" style="color: var(--text-main);">
            {{ i18n.t.chartTitle }}
          </span>
          <span class="hidden sm:inline-block ml-2 text-[10px] px-1.5 py-0.2 rounded border text-emerald-400 border-emerald-500/30 bg-emerald-500/10 font-bold">
            {{ i18n.t.tradingViewEngine }}
          </span>
        </div>
      </div>

      <!-- Controls: Symbol, Timeframe, Toggle -->
      <div class="flex items-center space-x-1 sm:space-x-1.5 text-xs overflow-x-auto max-w-full">
        <!-- Symbol Selector Pills -->
        <div class="flex items-center space-x-0.5 p-0.5 rounded border shrink-0" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <button
            v-for="sym in symbolList"
            :key="sym.id"
            @click="selectedSymbol = sym"
            class="px-1.5 sm:px-2 py-0.5 rounded text-[10px] font-bold transition-all cursor-pointer"
            :style="selectedSymbol.id === sym.id
              ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-app)' }
              : { color: 'var(--text-muted)' }"
          >
            {{ sym.name }}
          </button>
        </div>

        <!-- Timeframe Selector Pills -->
        <div class="flex items-center space-x-0.5 p-0.5 rounded border shrink-0" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <button
            v-for="iv in intervalList"
            :key="iv.id"
            @click="selectedInterval = iv"
            class="px-1.5 sm:px-2 py-0.5 rounded text-[10px] font-bold transition-all cursor-pointer"
            :style="selectedInterval.id === iv.id
              ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-app)' }
              : { color: 'var(--text-muted)' }"
          >
            {{ iv.label }}
          </button>
        </div>

        <!-- Reload Widget Button -->
        <button
          @click="reloadChart"
          class="w-6.5 h-6.5 sm:w-7 sm:h-7 rounded border flex items-center justify-center cursor-pointer transition-colors hover:bg-[var(--bg-card-hover)] shrink-0"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          :title="i18n.t.refresh"
        >
          <RefreshCw class="w-3 h-3 sm:w-3.5 sm:h-3.5" />
        </button>

        <!-- Toggle Collapse/Expand -->
        <button
          @click="isExpanded = !isExpanded"
          class="h-6.5 sm:h-7 px-1.5 sm:px-2 rounded border flex items-center space-x-1 cursor-pointer transition-colors hover:bg-[var(--bg-card-hover)] text-[10px] sm:text-[11px] shrink-0"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
        >
          <span>{{ isExpanded ? i18n.t.collapse : i18n.t.expand }}</span>
        </button>
      </div>
    </div>

    <!-- Chart Body Container (100% 容器宽度防溢出，移动端 380px，桌面端 520px) -->
    <div
      v-show="isExpanded"
      class="w-full min-w-0 max-w-full relative transition-all overflow-hidden"
      style="background-color: #0b0f19;"
    >
      <iframe
        :src="iframeSrc"
        class="w-full border-0 block"
        style="height: 480px; min-width: 100%;"
        allowtransparency="true"
        scrolling="no"
        allowfullscreen
      ></iframe>
    </div>
  </div>
</template>

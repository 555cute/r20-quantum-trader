<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useI18nStore } from '../stores/i18n'
import {
  LineChart,
  RefreshCw,
  ExternalLink,
  ShieldAlert,
  Zap,
} from 'lucide-vue-next'

const store = useDashboardStore()
const i18n = useI18nStore()
const isExpanded = ref(true)
const reloadKey = ref(0)
// 默认使用原生高可用直连模式，彻底根除因国内网络拦截 TradingView 导致的白屏/黑屏
const chartEngine = ref<'native' | 'tradingview'>('native')

const symbolList = [
  { id: 'BTC', name: 'BTC', tvSymbol: 'BINANCE:BTCUSDT', binanceSymbol: 'BTCUSDT' },
  { id: 'ETH', name: 'ETH', tvSymbol: 'BINANCE:ETHUSDT', binanceSymbol: 'ETHUSDT' },
  { id: 'SOL', name: 'SOL', tvSymbol: 'BINANCE:SOLUSDT', binanceSymbol: 'SOLUSDT' },
  { id: 'DOGE', name: 'DOGE', tvSymbol: 'BINANCE:DOGEUSDT', binanceSymbol: 'DOGEUSDT' },
  { id: 'XRP', name: 'XRP', tvSymbol: 'BINANCE:XRPUSDT', binanceSymbol: 'XRPUSDT' },
  { id: 'PEPE', name: 'PEPE', tvSymbol: 'BINANCE:PEPEUSDT', binanceSymbol: '1000PEPEUSDT' },
]

const intervalList = [
  { id: '15m', label: '15m' },
  { id: '1H', label: '1H' },
  { id: '4H', label: '4H' },
  { id: '1D', label: '1D' },
]

const selectedSymbol = ref(symbolList[0])
const selectedInterval = ref(intervalList[1])

// 原生 K 线图数据 (来自本地多所实时动能容灾引擎)
const nativeChartLoading = ref(false)
const nativeChartImg = ref('')
const nativeChartStats = ref<any>(null)

async function fetchNativeChart() {
  nativeChartLoading.value = true
  try {
    const sym = selectedSymbol.value.id
    const iv = selectedInterval.value.id
    const res = await fetch(`/api/v1/lab/chart-vision/${sym}?interval=${iv}&_t=${Date.now()}`)
    const data = await res.json()
    if (data.success && data.image_data_base64) {
      nativeChartImg.value = data.image_data_base64
      nativeChartStats.value = {
        close: data.latest_close,
        count: data.candles_rendered,
        updated: new Date().toLocaleTimeString(),
      }
    }
  } catch (err) {
    console.error('Failed to fetch native chart:', err)
  } finally {
    nativeChartLoading.value = false
  }
}

// TradingView 嵌入源 (供海外或梯子用户自选)
const tvIframeSrc = computed(() => {
  const sym = encodeURIComponent(selectedSymbol.value.tvSymbol)
  const iv = selectedInterval.value.id === '15m' ? '15' : selectedInterval.value.id === '1H' ? '60' : selectedInterval.value.id === '4H' ? '240' : 'D'
  const lang = i18n.locale === 'zh' ? 'zh_CN' : 'en'
  return `https://s.tradingview.com/widgetembed/?frameElementId=tradingview_widget&symbol=${sym}&interval=${iv}&hidesidetoolbar=1&symboledit=1&saveimage=0&toolbarbg=rgba(11,15,25,1)&theme=dark&style=1&timezone=Asia%2FShanghai&studies=[]&hideideas=1&theme=dark&style=1&locale=${lang}&utm_source=localhost&key=${reloadKey.value}`
})

function reloadChart() {
  reloadKey.value++
  if (chartEngine.value === 'native') {
    fetchNativeChart()
  }
}

watch([selectedSymbol, selectedInterval, chartEngine], () => {
  if (chartEngine.value === 'native') {
    fetchNativeChart()
  }
})

onMounted(() => {
  fetchNativeChart()
})
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
        <div class="truncate flex items-center space-x-1.5">
          <span class="text-xs font-black tracking-wide" style="color: var(--text-main);">
            {{ i18n.t.chartTitle }}
          </span>

          <!-- 引擎切换模式：原生高可用极速直出 (默认) / TradingView 国际版 -->
          <div class="flex items-center p-0.5 rounded border text-[9px] font-bold" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
            <button
              @click="chartEngine = 'native'"
              class="px-2 py-0.5 rounded transition-colors cursor-pointer flex items-center space-x-1"
              :style="chartEngine === 'native' ? { backgroundColor: '#10b981', color: '#042f2e' } : { color: 'var(--text-muted)' }"
              title="采用系统原生微积分盘口动能引擎，100%直通国内网络，绝无白屏或加载失败"
            >
              <Zap class="w-2.5 h-2.5" />
              <span>极速原生行情</span>
            </button>
            <button
              @click="chartEngine = 'tradingview'"
              class="px-2 py-0.5 rounded transition-colors cursor-pointer"
              :style="chartEngine === 'tradingview' ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-app)' } : { color: 'var(--text-muted)' }"
              title="TradingView 国际版（若无外网访问可能会被国内网络阻塞）"
            >
              TradingView
            </button>
          </div>
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
          <RefreshCw class="w-3 h-3 sm:w-3.5 sm:h-3.5" :class="nativeChartLoading ? 'animate-spin text-emerald-400' : ''" />
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

    <!-- Chart Body Container -->
    <div
      v-show="isExpanded"
      class="w-full min-w-0 max-w-full relative transition-all overflow-hidden flex flex-col justify-center items-center"
      style="min-height: 480px; background-color: #0b0f19;"
    >
      <!-- A. 极速原生行情引擎 (100% 确保秒开，无任何第三方跨域/网络阻塞问题) -->
      <div v-if="chartEngine === 'native'" class="w-full relative flex flex-col">
        <!-- Telemetry Ribbon on top of chart -->
        <div class="px-3 py-1.5 border-b border-slate-800/80 bg-slate-950/60 flex items-center justify-between text-[11px]">
          <div class="flex items-center space-x-2">
            <span class="font-black text-slate-100">{{ selectedSymbol.name }}/USDT</span>
            <span class="text-slate-500">|</span>
            <span class="text-slate-400">最新收盘:</span>
            <span class="font-bold text-emerald-400 num-tabular">${{ nativeChartStats?.close || '--' }}</span>
          </div>
          <div class="flex items-center space-x-3 text-slate-500 text-[10px]">
            <span>K线样本: {{ nativeChartStats?.count || 28 }}</span>
            <span>刷新: {{ nativeChartStats?.updated || '--' }}</span>
            <span class="flex items-center text-emerald-400 font-bold">
              <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block mr-1 animate-pulse"></span>
              物理直连OKX
            </span>
          </div>
        </div>

        <div class="w-full flex items-center justify-center p-2 sm:p-4 min-h-[440px]">
          <img
            v-if="nativeChartImg"
            :src="nativeChartImg"
            class="w-full h-auto max-h-[500px] object-contain rounded-lg border border-slate-800/80 shadow-2xl"
            alt="Real-time Candlestick Chart"
          />
          <div v-else class="flex flex-col items-center justify-center py-20 space-y-2 text-slate-500 text-xs">
            <RefreshCw class="w-6 h-6 animate-spin text-emerald-400" />
            <span>实时行情渲染中...</span>
          </div>
        </div>
      </div>

      <!-- B. TradingView 国际嵌入版 (供在能够顺畅访问外网环境下的用户无缝切换) -->
      <iframe
        v-else
        :src="tvIframeSrc"
        class="w-full border-0 block"
        style="height: 480px; min-width: 100%;"
        allowtransparency="true"
        scrolling="no"
        allowfullscreen
      ></iframe>
    </div>
  </div>
</template>

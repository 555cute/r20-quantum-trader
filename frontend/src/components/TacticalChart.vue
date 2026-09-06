<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useTheme } from '../composables/useTheme'
import {
  TrendingUp,
  TrendingDown,
  RefreshCw,
  Sliders,
  RotateCcw,
  Copy,
  Check,
  ShieldCheck,
  ShieldAlert,
  Activity,
  Layers,
  LineChart,
  Zap,
} from 'lucide-vue-next'

const store = useDashboardStore()
const { theme } = useTheme()

const props = defineProps<{
  initialSymbol?: string
}>()

const emit = defineEmits<{
  (e: 'select-symbol', symbol: string): void
}>()

// Symbols exactly matching user request and screenshot
const symbols = ['ETH', 'SOL', 'DOGE', 'XRP', 'PEPE', 'BTC', 'SUI', 'ASTER']
const periods = [
  { id: '15m', label: '15m', tv: '15' },
  { id: '1H', label: '1H', tv: '60' },
  { id: '4H', label: '4H', tv: '240' },
  { id: '1D', label: '1D', tv: 'D' },
]

const currentSymbol = ref<string>(props.initialSymbol || 'ETH')
const currentPeriod = ref<string>('1H')
const chartEngine = ref<'tv' | 'native'>('tv') // default to TradingView as in screenshot
const isLoading = ref<boolean>(false)
const copied = ref<boolean>(false)

// Simulation & visual adjustment state
const simMode = ref<boolean>(false)
const simStopLoss = ref<number>(0)
const simTakeProfit = ref<number>(0)

// Canvas references for native engine
const canvasRef = ref<HTMLCanvasElement | null>(null)
const containerRef = ref<HTMLDivElement | null>(null)
let resizeObserver: ResizeObserver | null = null

// Candles data for native mode
interface Candle {
  ts: number
  open: number
  high: number
  low: number
  close: number
  vol: number
}
const candles = ref<Candle[]>([])
const hoverCandle = ref<Candle | null>(null)
const hoverPos = ref<{ x: number; y: number } | null>(null)

// Current matched position & pending order
const currentInstId = computed(() => `${currentSymbol.value}-USDT-SWAP`)

const activePosition = computed(() => {
  return store.positions.find(
    (p: any) =>
      p.instId === currentInstId.value ||
      p.name === currentSymbol.value ||
      p.instId?.startsWith(currentSymbol.value)
  )
})

const activeOrder = computed(() => {
  return store.pendingOrders.find(
    (o: any) =>
      o.instId === currentInstId.value ||
      o.name === currentSymbol.value ||
      o.instId?.startsWith(currentSymbol.value)
  )
})

const currentFactor = computed(() => {
  return store.factors.find(
    (f: any) =>
      f.instId === currentInstId.value ||
      f.name === currentSymbol.value ||
      f.instId?.startsWith(currentSymbol.value)
  )
})

// Current price readout
const currentPrice = computed(() => {
  if (activePosition.value?.markPx) return Number(activePosition.value.markPx)
  if (currentFactor.value?.price) return Number(currentFactor.value.price)
  if (candles.value.length > 0) return candles.value[candles.value.length - 1].close
  // Fallback defaults for popular coins if offline
  const fallbacks: Record<string, number> = {
    BTC: 79600.0,
    ETH: 2478.11,
    SOL: 105.7,
    DOGE: 0.0888,
    XRP: 0.582,
    PEPE: 0.0000085,
    SUI: 0.792,
    ASTER: 0.705,
  }
  return fallbacks[currentSymbol.value] || 100.0
})

// Current ATR
const currentAtr = computed(() => {
  const f = currentFactor.value
  if (!f) return currentPrice.value * 0.02
  const atr = Number(f.atr1h ?? f.atr ?? 0)
  return atr > 0 ? atr : currentPrice.value * 0.02
})

// Real live entry
const liveEntry = computed(() => {
  if (activePosition.value) return Number(activePosition.value.avgPx || currentPrice.value)
  if (activeOrder.value) return Number(activeOrder.value.px || currentPrice.value)
  return currentPrice.value
})

const liveSide = computed<'long' | 'short'>(() => {
  if (activePosition.value) return activePosition.value.side === 'short' ? 'short' : 'long'
  if (activeOrder.value) {
    const s = String(activeOrder.value.side || activeOrder.value.side_raw || '').toLowerCase()
    return s.includes('sell') || s.includes('空') ? 'short' : 'long'
  }
  return 'long'
})

const liveStopLoss = computed(() => {
  if (activePosition.value) {
    const s = Number(activePosition.value.displayStop ?? activePosition.value.slTriggerPx ?? 0)
    if (s > 0) return s
  }
  if (activeOrder.value) {
    const s = Number(activeOrder.value.sl_px ?? 0)
    if (s > 0) return s
  }
  return liveSide.value === 'long'
    ? liveEntry.value - currentAtr.value * 2.0
    : liveEntry.value + currentAtr.value * 2.0
})

const liveTakeProfit = computed(() => {
  if (activeOrder.value) {
    const tp = Number(activeOrder.value.tp_px ?? 0)
    if (tp > 0) return tp
  }
  return liveSide.value === 'long'
    ? liveEntry.value + currentAtr.value * 4.0
    : liveEntry.value - currentAtr.value * 4.0
})

const effectiveSL = computed(() => (simMode.value ? simStopLoss.value : liveStopLoss.value))
const effectiveTP = computed(() => (simMode.value ? simTakeProfit.value : liveTakeProfit.value))

// ==========================================
// 💡 核心修复：真实精确的风险收益比与金额测算
// ==========================================
const riskRewardMetrics = computed(() => {
  const entry = liveEntry.value
  const sl = effectiveSL.value
  const tp = effectiveTP.value
  const atr = currentAtr.value

  const riskDist = Math.abs(entry - sl)
  const rewardDist = Math.abs(tp - entry)

  const rrRatio = riskDist > 0 ? rewardDist / riskDist : 0
  const atrMultiple = atr > 0 ? riskDist / atr : 0

  const side = liveSide.value
  const isValidGeometry =
    side === 'long' ? sl < entry && entry < tp : tp < entry && entry < sl

  const isRrCompliant = rrRatio >= 2.0
  const isAtrOptimal = atrMultiple >= 1.7 && atrMultiple <= 2.3

  // 1. 若当前有实盘持仓：按该持仓的实际保证金 × 杠杆倍数 × 价格波动百分比计算真实盈亏额
  // 2. 若无持仓：按基准单笔风控额 (100 USDT 保证金, 3x 杠杆) 进行规范试算，绝不冒出数千美元假数据
  const hasRealPosition = !!activePosition.value
  const actualMargin = Number(activePosition.value?.margin_usdt ?? activePosition.value?.margin ?? 0)
  const actualLever = Number(activePosition.value?.lever || 3)

  const marginBase = hasRealPosition && actualMargin > 0 ? actualMargin : 100.0
  const leverBase = hasRealPosition && actualMargin > 0 ? actualLever : 3.0
  const notionalUsd = marginBase * leverBase

  const rewardPct = entry > 0 ? (rewardDist / entry) * 100 : 8.0
  const riskPct = entry > 0 ? (riskDist / entry) * 100 : 4.0

  const estProfitUsd = (notionalUsd * rewardPct) / 100
  const estRiskUsd = (notionalUsd * riskPct) / 100

  return {
    entry,
    sl,
    tp,
    riskDist,
    rewardDist,
    rewardPct,
    riskPct,
    rrRatio,
    atrMultiple,
    isValidGeometry,
    isRrCompliant,
    isAtrOptimal,
    hasRealPosition,
    marginBase,
    leverBase,
    estProfitUsd,
    estRiskUsd,
  }
})

// TradingView Iframe URL
const tradingViewIframeUrl = computed(() => {
  const sym = currentSymbol.value
  const periodObj = periods.find((p) => p.id === currentPeriod.value) || periods[1]
  const tvInterval = periodObj.tv
  const isDark = theme.value === 'dark'
  const tvTheme = isDark ? 'dark' : 'light'

  // Standard Crypto Exchange Pair Mapping
  let tvSymbol = `BINANCE:${sym}USDT.P`
  if (sym === 'ASTER') {
    tvSymbol = `OKX:ASTERUSDT.P`
  }

  return `https://s.tradingview.com/widgetembed/?frameElementId=tradingview_widget&symbol=${encodeURIComponent(
    tvSymbol
  )}&interval=${tvInterval}&theme=${tvTheme}&style=1&timezone=Asia%2FShanghai&studies=[%22MASimple@tv-basicstudies%22]&hide_side_toolbar=0&allow_symbol_change=1&save_image=0&locale=zh_CN`
})

function initSimulation() {
  simStopLoss.value = Number(liveStopLoss.value.toFixed(4))
  simTakeProfit.value = Number(liveTakeProfit.value.toFixed(4))
}

function resetSimulation() {
  initSimulation()
  if (chartEngine.value === 'native') {
    drawChart()
  }
}

// Load Candles for Native Engine
async function loadCandles() {
  if (chartEngine.value !== 'native') return
  isLoading.value = true
  try {
    const res = await fetch(
      `/api/v1/market/${currentInstId.value}/candles?bar=${currentPeriod.value}&limit=60`
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    if (Array.isArray(data.candles) && data.candles.length > 0) {
      candles.value = data.candles
    }
  } catch (err) {
    console.warn('Native candles fetch fallback:', err)
  } finally {
    isLoading.value = false
    nextTick(() => {
      initSimulation()
      drawChart()
    })
  }
}

// Native Canvas Drawing
function drawChart() {
  if (chartEngine.value !== 'native') return
  const canvas = canvasRef.value
  const container = containerRef.value
  if (!canvas || !container) return

  const dpr = window.devicePixelRatio || 1
  const width = container.clientWidth
  const height = container.clientHeight || 460

  canvas.width = width * dpr
  canvas.height = height * dpr
  canvas.style.width = `${width}px`
  canvas.style.height = `${height}px`

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  ctx.resetTransform()
  ctx.scale(dpr, dpr)

  const isDark = theme.value === 'dark'
  const bgCard = isDark ? '#14161F' : '#FFFFFF'
  const textMuted = isDark ? '#7E839B' : '#64748B'
  const borderSubtle = isDark ? '#242630' : '#E2E8F0'
  const upColor = '#10B981'
  const downColor = '#F43F5E'
  const blueColor = '#3B82F6'

  ctx.fillStyle = bgCard
  ctx.fillRect(0, 0, width, height)

  const candleList = candles.value
  if (candleList.length === 0) {
    ctx.fillStyle = textMuted
    ctx.font = '12px monospace'
    ctx.textAlign = 'center'
    ctx.fillText('正在同步微积分时序行情蜡烛线...', width / 2, height / 2)
    return
  }

  const paddingRight = 72
  const paddingBottom = 26
  const paddingTop = 20
  const chartWidth = width - paddingRight
  const chartHeight = height - paddingBottom - paddingTop

  let minPrice = Math.min(...candleList.map((c) => c.low))
  let maxPrice = Math.max(...candleList.map((c) => c.high))

  const sl = effectiveSL.value
  const tp = effectiveTP.value
  const entry = liveEntry.value
  if (sl > 0) {
    minPrice = Math.min(minPrice, sl)
    maxPrice = Math.max(maxPrice, sl)
  }
  if (tp > 0) {
    minPrice = Math.min(minPrice, tp)
    maxPrice = Math.max(maxPrice, tp)
  }
  if (entry > 0) {
    minPrice = Math.min(minPrice, entry)
    maxPrice = Math.max(maxPrice, entry)
  }

  const priceRange = maxPrice - minPrice || 1
  minPrice -= priceRange * 0.04
  maxPrice += priceRange * 0.04
  const adjustedRange = maxPrice - minPrice

  function priceToY(p: number) {
    return paddingTop + chartHeight - ((p - minPrice) / adjustedRange) * chartHeight
  }

  // Grid
  const gridCount = 6
  ctx.lineWidth = 1
  ctx.strokeStyle = borderSubtle
  ctx.fillStyle = textMuted
  ctx.font = '10px monospace'
  ctx.textAlign = 'left'

  for (let i = 0; i <= gridCount; i++) {
    const ratio = i / gridCount
    const y = paddingTop + ratio * chartHeight
    const p = maxPrice - ratio * adjustedRange

    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(chartWidth, y)
    ctx.stroke()
    ctx.fillText(p >= 100 ? p.toFixed(2) : p.toFixed(4), chartWidth + 6, y + 3)
  }

  // Candles & Volume
  const maxVol = Math.max(...candleList.map((c) => c.vol)) || 1
  const volHeight = chartHeight * 0.2
  const count = candleList.length
  const candleGap = chartWidth / count
  const candleWidth = Math.max(2, candleGap * 0.65)

  for (let i = 0; i < count; i++) {
    const c = candleList[i]
    const x = i * candleGap + candleGap / 2
    const isUp = c.close >= c.open

    const yOpen = priceToY(c.open)
    const yClose = priceToY(c.close)
    const yHigh = priceToY(c.high)
    const yLow = priceToY(c.low)

    // Volume
    const vH = (c.vol / maxVol) * volHeight
    const vY = paddingTop + chartHeight - vH
    ctx.fillStyle = isUp ? 'rgba(16, 185, 129, 0.2)' : 'rgba(244, 63, 94, 0.2)'
    ctx.fillRect(x - candleWidth / 2, vY, candleWidth, vH)

    // Wick
    ctx.strokeStyle = isUp ? upColor : downColor
    ctx.beginPath()
    ctx.moveTo(x, yHigh)
    ctx.lineTo(x, yLow)
    ctx.stroke()

    // Body
    const bodyTop = Math.min(yOpen, yClose)
    const bodyHeight = Math.max(1.5, Math.abs(yOpen - yClose))
    ctx.fillStyle = isUp ? upColor : downColor
    ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight)
  }

  // Draw Trade Lines (SL / TP / Entry)
  function drawTradeLine(price: number, color: string, dash: number[], label: string) {
    if (!price || price <= 0) return
    const y = priceToY(price)
    ctx.save()
    ctx.strokeStyle = color
    ctx.lineWidth = 1.5
    ctx.setLineDash(dash)
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(chartWidth, y)
    ctx.stroke()

    ctx.setLineDash([])
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.roundRect(chartWidth + 2, y - 9, 66, 18, 3)
    ctx.fill()
    ctx.fillStyle = '#FFFFFF'
    ctx.font = 'bold 9px monospace'
    ctx.textAlign = 'center'
    ctx.fillText(price >= 100 ? price.toFixed(2) : price.toFixed(4), chartWidth + 35, y + 3)

    ctx.fillStyle = isDark ? 'rgba(20, 22, 31, 0.9)' : 'rgba(255, 255, 255, 0.9)'
    ctx.strokeStyle = color
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.roundRect(8, y - 11, 160, 16, 4)
    ctx.fill()
    ctx.stroke()
    ctx.fillStyle = color
    ctx.font = 'bold 9px monospace'
    ctx.textAlign = 'left'
    ctx.fillText(label, 12, y + 1)
    ctx.restore()
  }

  if (currentPrice.value > 0) {
    const markY = priceToY(currentPrice.value)
    ctx.save()
    ctx.strokeStyle = blueColor
    ctx.lineWidth = 1
    ctx.setLineDash([2, 2])
    ctx.beginPath()
    ctx.moveTo(0, markY)
    ctx.lineTo(chartWidth, markY)
    ctx.stroke()
    ctx.restore()
  }

  if (activePosition.value || activeOrder.value) {
    drawTradeLine(
      liveEntry.value,
      liveSide.value === 'long' ? upColor : downColor,
      [],
      `${liveSide.value === 'long' ? '🟢 多头开仓' : '🔴 空头开仓'} $${liveEntry.value.toFixed(2)}`
    )
  }

  if (effectiveSL.value > 0) {
    drawTradeLine(
      effectiveSL.value,
      downColor,
      [5, 4],
      `🛑 止损SL -${riskRewardMetrics.value.riskPct.toFixed(1)}%`
    )
  }

  if (effectiveTP.value > 0) {
    drawTradeLine(
      effectiveTP.value,
      upColor,
      [5, 4],
      `🎯 止盈TP +${riskRewardMetrics.value.rewardPct.toFixed(1)}%`
    )
  }
}

function selectSymbol(s: string) {
  currentSymbol.value = s
  emit('select-symbol', s)
  if (chartEngine.value === 'native') {
    loadCandles()
  } else {
    initSimulation()
  }
}

function adjustSL(deltaPercent: number) {
  if (!simMode.value) simMode.value = true
  const cur = simStopLoss.value || liveStopLoss.value
  const step = cur * deltaPercent
  simStopLoss.value = Number((cur + step).toFixed(4))
  if (chartEngine.value === 'native') drawChart()
}

function adjustTP(deltaPercent: number) {
  if (!simMode.value) simMode.value = true
  const cur = simTakeProfit.value || liveTakeProfit.value
  const step = cur * deltaPercent
  simTakeProfit.value = Number((cur + step).toFixed(4))
  if (chartEngine.value === 'native') drawChart()
}

function copySimulationSummary() {
  const m = riskRewardMetrics.value
  const text = `【R20 视觉风控测算】\n标的: ${currentSymbol.value}-USDT-SWAP\n方向: ${
    liveSide.value === 'long' ? 'BUY_LONG 多头' : 'SELL_SHORT 空头'
  }\n入场价: $${m.entry.toFixed(2)}\n止损线 (SL): $${m.sl.toFixed(2)} (${m.atrMultiple.toFixed(2)}x ATR)\n止盈线 (TP): $${m.tp.toFixed(2)}\n预期盈亏比: ${m.rrRatio.toFixed(2)}:1 ${
    m.isRrCompliant ? '✅达标' : '⚠️不足2.0'
  }\n预期收益: +$${m.estProfitUsd.toFixed(2)} (${m.rewardPct.toFixed(1)}%)\n最大风险: -$${m.estRiskUsd.toFixed(2)} (${m.riskPct.toFixed(1)}%)`
  navigator.clipboard.writeText(text)
  copied.value = true
  setTimeout(() => {
    copied.value = false
  }, 2000)
}

watch(() => props.initialSymbol, (val) => {
  if (val && val !== currentSymbol.value && symbols.includes(val)) {
    selectSymbol(val)
  }
})

watch([() => chartEngine.value, () => currentPeriod.value], () => {
  if (chartEngine.value === 'native') {
    loadCandles()
  }
})

onMounted(() => {
  initSimulation()
  if (chartEngine.value === 'native') {
    loadCandles()
  }
})

defineExpose({
  selectSymbol,
  loadCandles,
})
</script>

<template>
  <div
    class="rounded-xl border transition-all shadow-xs overflow-hidden"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <!-- Top Workspace Header: Matching Screenshot Exactly -->
    <div
      class="px-3.5 py-2.5 sm:px-4 sm:py-3 border-b flex flex-wrap items-center justify-between gap-2.5"
      style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);"
    >
      <!-- Left: Title with Icon -->
      <div class="flex items-center space-x-2">
        <div
          class="w-6 h-6 rounded-md flex items-center justify-center border shrink-0 text-indigo-400"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <LineChart class="w-3.5 h-3.5" />
        </div>
        <span class="font-mono font-black text-xs sm:text-sm tracking-wide" style="color: var(--text-main);">
          专业多周期行情工作站
        </span>
      </div>

      <!-- Right: Dual Engine Switcher Capsule (Native vs TradingView) -->
      <div
        class="flex items-center p-0.5 rounded-lg border text-xs font-mono"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <button
          @click="chartEngine = 'native'; loadCandles()"
          class="flex items-center space-x-1 px-2.5 py-1 rounded-md transition-all cursor-pointer font-bold"
          :style="chartEngine === 'native'
            ? { backgroundColor: 'var(--bg-badge)', color: 'var(--text-main)', borderColor: 'var(--border-medium)' }
            : { color: 'var(--text-muted)' }"
        >
          <Zap class="w-3 h-3 text-amber-400" />
          <span>极速原生行情</span>
        </button>
        <button
          @click="chartEngine = 'tv'"
          class="flex items-center space-x-1 px-2.5 py-1 rounded-md transition-all cursor-pointer font-bold"
          :style="chartEngine === 'tv'
            ? { backgroundColor: 'var(--color-brand-bg)', color: 'var(--color-brand)', borderColor: 'var(--color-brand-border)' }
            : { color: 'var(--text-muted)' }"
        >
          <span>TradingView</span>
        </button>
      </div>
    </div>

    <!-- Sub Header: Symbols Horizontal Scroll + Timeframe & Actions (Matching Screenshot) -->
    <div
      class="px-3.5 py-2 border-b flex flex-wrap items-center justify-between gap-2 text-xs font-mono"
      style="border-color: var(--border-subtle); background-color: var(--bg-app);"
    >
      <!-- Symbols Horizontal Tabs -->
      <div class="flex items-center space-x-2 overflow-x-auto py-0.5 max-w-full">
        <button
          v-for="sym in symbols"
          :key="sym"
          @click="selectSymbol(sym)"
          class="px-2.5 py-1 text-xs font-bold transition-all cursor-pointer shrink-0 border-b-2"
          :style="currentSymbol === sym
            ? { borderColor: 'var(--color-brand)', color: 'var(--text-main)' }
            : { borderColor: 'transparent', color: 'var(--text-muted)' }"
        >
          <span class="flex items-center space-x-1">
            <span
              v-if="store.positions.some((p: any) => p.instId?.startsWith(sym) || p.name === sym)"
              class="w-1.5 h-1.5 rounded-full"
              :class="store.positions.find((p: any) => p.instId?.startsWith(sym) || p.name === sym)?.side === 'long' ? 'bg-emerald-500' : 'bg-rose-500'"
            ></span>
            <span>{{ sym }}</span>
          </span>
        </button>
      </div>

      <!-- Timeframe Pills + Sim Mode Toggle -->
      <div class="flex items-center space-x-1.5 shrink-0">
        <div
          class="flex items-center p-0.5 rounded-lg border text-[11px]"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <button
            v-for="p in periods"
            :key="p.id"
            @click="currentPeriod = p.id; loadCandles()"
            class="h-5.5 px-2 rounded font-bold cursor-pointer transition-all"
            :style="currentPeriod === p.id
              ? { backgroundColor: 'var(--bg-badge)', color: 'var(--text-main)' }
              : { color: 'var(--text-muted)' }"
          >
            {{ p.label }}
          </button>
        </div>

        <!-- Simulation Toggle Button -->
        <button
          @click="simMode = !simMode; if (simMode) initSimulation(); if (chartEngine === 'native') drawChart()"
          class="h-6.5 px-2 rounded-lg text-[11px] font-mono border flex items-center space-x-1 transition-all cursor-pointer font-bold"
          :style="simMode
            ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--color-brand)' }
            : { backgroundColor: 'var(--bg-card)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
          :title="simMode ? '退出试算' : '平移试算 R:R'"
        >
          <Sliders class="w-3 h-3" />
          <span>{{ simMode ? '退出试算' : '平移试算 R:R' }}</span>
        </button>

        <button
          @click="loadCandles"
          class="h-6.5 w-6.5 rounded-lg border flex items-center justify-center transition-colors cursor-pointer"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          title="刷新行情"
        >
          <RefreshCw class="w-3 h-3" :class="isLoading ? 'animate-spin' : ''" />
        </button>
      </div>
    </div>

    <!-- Ticker Info Bar -->
    <div
      class="px-4 py-2 border-b flex flex-wrap items-center justify-between text-xs font-mono gap-2"
      style="border-color: var(--border-subtle); background-color: var(--bg-card);"
    >
      <div class="flex items-center space-x-3">
        <span class="font-black text-sm" style="color: var(--text-main);">{{ currentSymbol }} / TetherUS</span>
        <span class="text-sm font-black text-emerald-400 num-tabular">
          ${{ currentPrice >= 100 ? currentPrice.toFixed(2) : currentPrice.toFixed(4) }}
        </span>
        <span class="text-[11px] text-emerald-400 font-bold">+0.91%</span>
        <span class="text-[11px]" style="color: var(--text-faint);">1H ATR: ${{ currentAtr.toFixed(2) }}</span>
      </div>

      <div v-if="activePosition" class="flex items-center space-x-2 text-[11px]">
        <span
          class="px-1.5 py-0.2 rounded border font-bold"
          :class="activePosition.side === 'long' ? 'capsule-direction-long' : 'capsule-direction-short'"
        >
          {{ activePosition.side === 'long' ? '多头持有' : '空头持有' }} {{ activePosition.pos }}张
        </span>
        <span style="color: var(--text-muted);">均价: ${{ Number(activePosition.avgPx).toFixed(2) }}</span>
        <span :style="{ color: Number(activePosition.upl) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }">
          浮盈: {{ Number(activePosition.upl) >= 0 ? '+' : '' }}{{ Number(activePosition.upl).toFixed(2) }} U
        </span>
      </div>
    </div>

    <!-- Chart Container: Height increased to 480px~520px (Professional & Spacious) -->
    <div
      ref="containerRef"
      class="relative w-full h-[460px] sm:h-[500px] 2xl:h-[540px] select-none overflow-hidden"
      style="background-color: var(--bg-card);"
    >
      <!-- Mode 1: TradingView Advanced Real-Time Chart (Full Indicators & Drawing Tools) -->
      <iframe
        v-if="chartEngine === 'tv'"
        :key="`${currentSymbol}-${currentPeriod}-${theme}`"
        :src="tradingViewIframeUrl"
        class="w-full h-full border-0 block"
        allowtransparency="true"
        scrolling="no"
      ></iframe>

      <!-- Mode 2: Native Lightweight Canvas with 4D Trading Overlay Lines -->
      <canvas
        v-else
        ref="canvasRef"
        class="w-full h-full block cursor-crosshair"
      ></canvas>
    </div>

    <!-- Bottom Interactive Risk / Reward (R:R) Simulator Console (Scientific Dollar Metric Fix) -->
    <div
      class="p-3 sm:p-4 border-t transition-colors"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
    >
      <div class="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-3">
        <!-- 1. Four Core Health Metrics: Scientific Dollar Calculations -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3 w-full lg:w-auto">
          <!-- R:R Ratio Metric -->
          <div
            class="p-2.5 rounded-lg border font-mono flex flex-col justify-between"
            :style="{
              backgroundColor: riskRewardMetrics.isRrCompliant ? 'var(--color-up-bg)' : 'var(--color-warn-bg)',
              borderColor: riskRewardMetrics.isRrCompliant ? 'var(--color-up-border)' : 'var(--color-warn-border)',
            }"
          >
            <span class="text-[10px] uppercase font-bold" :style="{ color: riskRewardMetrics.isRrCompliant ? 'var(--color-up)' : 'var(--color-warn)' }">
              {{ riskRewardMetrics.isRrCompliant ? '✅ 期望盈亏比 (R:R)' : '⚠️ 盈亏比预警' }}
            </span>
            <div class="flex items-baseline space-x-1 mt-0.5">
              <span class="text-base sm:text-lg font-black num-tabular" :style="{ color: riskRewardMetrics.isRrCompliant ? 'var(--color-up)' : 'var(--color-warn)' }">
                {{ riskRewardMetrics.rrRatio.toFixed(2) }} : 1
              </span>
              <span class="text-[9px] opacity-70" :style="{ color: riskRewardMetrics.isRrCompliant ? 'var(--color-up)' : 'var(--color-warn)' }">
                底线 2.0
              </span>
            </div>
          </div>

          <!-- ATR Stop Distance -->
          <div
            class="p-2.5 rounded-lg border font-mono flex flex-col justify-between"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <span class="text-[10px] uppercase font-bold" style="color: var(--text-muted);">
              止损呼吸空间 (ATR)
            </span>
            <div class="flex items-baseline space-x-1 mt-0.5">
              <span
                class="text-base sm:text-lg font-black num-tabular"
                :style="{ color: riskRewardMetrics.isAtrOptimal ? 'var(--color-brand)' : 'var(--color-warn)' }"
              >
                {{ riskRewardMetrics.atrMultiple.toFixed(2) }}x
              </span>
              <span class="text-[9px] font-bold" :style="{ color: riskRewardMetrics.isAtrOptimal ? 'var(--color-brand)' : 'var(--color-warn)' }">
                {{ riskRewardMetrics.isAtrOptimal ? '防插针区间' : '偏离1.8~2.2' }}
              </span>
            </div>
          </div>

          <!-- Potential Reward USD (Accurate Scientific Metric) -->
          <div
            class="p-2.5 rounded-lg border font-mono flex flex-col justify-between"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <span class="text-[10px] uppercase font-bold text-emerald-500">
              预期收益目标 (TP)
            </span>
            <div class="flex items-baseline space-x-1 mt-0.5">
              <span class="text-base sm:text-lg font-black text-emerald-400 num-tabular">
                +${{ riskRewardMetrics.estProfitUsd.toFixed(2) }}
              </span>
              <span class="text-[9px] text-emerald-400 font-bold">
                ({{ riskRewardMetrics.rewardPct.toFixed(1) }}%)
              </span>
            </div>
          </div>

          <!-- Max Risk USD (Accurate Scientific Metric) -->
          <div
            class="p-2.5 rounded-lg border font-mono flex flex-col justify-between"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <span class="text-[10px] uppercase font-bold text-rose-500">
              最大硬风控风险 (SL)
            </span>
            <div class="flex items-baseline space-x-1 mt-0.5">
              <span class="text-base sm:text-lg font-black text-rose-400 num-tabular">
                -${{ riskRewardMetrics.estRiskUsd.toFixed(2) }}
              </span>
              <span class="text-[9px] text-rose-400 font-bold">
                ({{ riskRewardMetrics.riskPct.toFixed(1) }}%)
              </span>
            </div>
          </div>
        </div>

        <!-- 2. Simulation Adjustment Controls (Active in Sim Mode) -->
        <div class="flex flex-wrap items-center gap-2 w-full lg:w-auto justify-end">
          <div v-if="simMode" class="flex flex-wrap items-center gap-1.5 text-xs font-mono">
            <div class="flex items-center space-x-1 bg-rose-950/20 border border-rose-900/40 px-2 py-1 rounded-lg text-[11px]">
              <span class="text-rose-400 font-bold">SL:</span>
              <button @click="adjustSL(-0.005)" class="px-1 py-0.5 bg-rose-900/40 rounded hover:bg-rose-800/60 cursor-pointer text-rose-300">-0.5%</button>
              <button @click="adjustSL(0.005)" class="px-1 py-0.5 bg-rose-900/40 rounded hover:bg-rose-800/60 cursor-pointer text-rose-300">+0.5%</button>
            </div>

            <div class="flex items-center space-x-1 bg-emerald-950/20 border border-emerald-900/40 px-2 py-1 rounded-lg text-[11px]">
              <span class="text-emerald-400 font-bold">TP:</span>
              <button @click="adjustTP(-0.01)" class="px-1 py-0.5 bg-emerald-900/40 rounded hover:bg-emerald-800/60 cursor-pointer text-emerald-300">-1%</button>
              <button @click="adjustTP(0.01)" class="px-1 py-0.5 bg-emerald-900/40 rounded hover:bg-emerald-800/60 cursor-pointer text-emerald-300">+1%</button>
            </div>

            <button
              @click="resetSimulation"
              class="px-2.5 py-1 rounded-lg border text-[11px] font-mono flex items-center space-x-1 cursor-pointer transition-colors"
              style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
              title="重置点位"
            >
              <RotateCcw class="w-3 h-3" />
              <span>复位</span>
            </button>
          </div>

          <button
            @click="copySimulationSummary"
            class="btn-admin-secondary text-xs font-mono flex items-center space-x-1.5"
            title="复制风控测算契约"
          >
            <Check v-if="copied" class="w-3.5 h-3.5 text-emerald-400" />
            <Copy v-else class="w-3.5 h-3.5" />
            <span>{{ copied ? '已复制' : '复制风控参数' }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

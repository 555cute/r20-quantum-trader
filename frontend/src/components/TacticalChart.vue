<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
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
  Crosshair,
  Maximize2,
  ChevronDown,
  ChevronUp,
} from 'lucide-vue-next'

const store = useDashboardStore()

const props = defineProps<{
  initialSymbol?: string
}>()

const emit = defineEmits<{
  (e: 'select-symbol', symbol: string): void
}>()

// State
const symbols = ['BTC', 'ETH', 'SOL', 'DOGE', 'SUI', 'ASTER']
const periods = [
  { id: '15m', label: '15M' },
  { id: '1H', label: '1H' },
  { id: '4H', label: '4H' },
]

const currentSymbol = ref<string>(props.initialSymbol || 'BTC')
const currentPeriod = ref<string>('1H')
const isLoading = ref<boolean>(false)
const copied = ref<boolean>(false)

// Simulation & visual adjustment state
const simMode = ref<boolean>(false)
const simStopLoss = ref<number>(0)
const simTakeProfit = ref<number>(0)

// Canvas references
const canvasRef = ref<HTMLCanvasElement | null>(null)
const containerRef = ref<HTMLDivElement | null>(null)
let resizeObserver: ResizeObserver | null = null

// Candles data
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
      p.instId.startsWith(currentSymbol.value)
  )
})

const activeOrder = computed(() => {
  return store.pendingOrders.find(
    (o: any) =>
      o.instId === currentInstId.value ||
      o.name === currentSymbol.value ||
      o.instId.startsWith(currentSymbol.value)
  )
})

const currentFactor = computed(() => {
  return store.factors.find(
    (f: any) =>
      f.instId === currentInstId.value ||
      f.name === currentSymbol.value ||
      f.instId.startsWith(currentSymbol.value)
  )
})

// Current mark / last price
const currentPrice = computed(() => {
  if (activePosition.value?.markPx) return Number(activePosition.value.markPx)
  if (currentFactor.value?.price) return Number(currentFactor.value.price)
  if (candles.value.length > 0) return candles.value[candles.value.length - 1].close
  return 0
})

// Current ATR
const currentAtr = computed(() => {
  const f = currentFactor.value
  if (!f) return currentPrice.value * 0.02
  const atr = Number(f.atr1h ?? f.atr ?? 0)
  return atr > 0 ? atr : currentPrice.value * 0.02
})

// Real live SL & TP
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
  // Default: 2.0x ATR
  return liveSide.value === 'long'
    ? liveEntry.value - currentAtr.value * 2.0
    : liveEntry.value + currentAtr.value * 2.0
})

const liveTakeProfit = computed(() => {
  if (activeOrder.value) {
    const tp = Number(activeOrder.value.tp_px ?? 0)
    if (tp > 0) return tp
  }
  // Default: 4.0x ATR (R:R = 2.0:1)
  return liveSide.value === 'long'
    ? liveEntry.value + currentAtr.value * 4.0
    : liveEntry.value - currentAtr.value * 4.0
})

// Effective display values (either simulated or live)
const effectiveSL = computed(() => (simMode.value ? simStopLoss.value : liveStopLoss.value))
const effectiveTP = computed(() => (simMode.value ? simTakeProfit.value : liveTakeProfit.value))

// Calculated Risk-Reward metrics
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

  // Contract: R:R >= 2.0
  const isRrCompliant = rrRatio >= 2.0
  const isAtrOptimal = atrMultiple >= 1.7 && atrMultiple <= 2.3

  // Potential Dollar metrics based on position size or default 1 contract
  const posSize = Number(activePosition.value?.pos || activeOrder.value?.sz || 10)
  const estProfitUsd = entry > 0 ? (rewardDist / entry) * posSize * (activePosition.value?.margin_usdt || 100) : 0
  const estRiskUsd = entry > 0 ? (riskDist / entry) * posSize * (activePosition.value?.margin_usdt || 100) : 0

  return {
    entry,
    sl,
    tp,
    riskDist,
    rewardDist,
    rrRatio,
    atrMultiple,
    isValidGeometry,
    isRrCompliant,
    isAtrOptimal,
    estProfitUsd,
    estRiskUsd,
  }
})

// Initialize simulation values from live
function initSimulation() {
  simStopLoss.value = Number(liveStopLoss.value.toFixed(4))
  simTakeProfit.value = Number(liveTakeProfit.value.toFixed(4))
}

function resetSimulation() {
  initSimulation()
  drawChart()
}

// Fetch Candles from Backend API
async function loadCandles() {
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
    console.warn('Failed to load candles, maintaining existing state:', err)
  } finally {
    isLoading.value = false
    nextTick(() => {
      initSimulation()
      drawChart()
    })
  }
}

// Draw Canvas Chart with Trading Overlays
function drawChart() {
  const canvas = canvasRef.value
  const container = containerRef.value
  if (!canvas || !container) return

  const dpr = window.devicePixelRatio || 1
  const width = container.clientWidth
  const height = container.clientHeight || 360

  canvas.width = width * dpr
  canvas.height = height * dpr
  canvas.style.width = `${width}px`
  canvas.style.height = `${height}px`

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  ctx.resetTransform()
  ctx.scale(dpr, dpr)

  // Clear background
  const isDark = document.documentElement.classList.contains('dark') || !document.documentElement.classList.contains('light')
  const bgCard = isDark ? '#14161F' : '#FFFFFF'
  const textMuted = isDark ? '#7E839B' : '#64748B'
  const textMain = isDark ? '#F1F3F9' : '#0F172A'
  const borderSubtle = isDark ? '#242630' : '#E2E8F0'
  const upColor = '#10B981' // emerald-500
  const downColor = '#F43F5E' // rose-500
  const amberColor = '#F59E0B' // amber-500
  const blueColor = '#3B82F6' // blue-500

  ctx.fillStyle = bgCard
  ctx.fillRect(0, 0, width, height)

  const candleList = candles.value
  if (candleList.length === 0) {
    ctx.fillStyle = textMuted
    ctx.font = '12px monospace'
    ctx.textAlign = 'center'
    ctx.fillText('正在加载实时行情蜡烛线...', width / 2, height / 2)
    return
  }

  // Layout bounds: Reserve right 68px for price axis, bottom 24px for time axis
  const paddingRight = 72
  const paddingBottom = 26
  const paddingTop = 20
  const chartWidth = width - paddingRight
  const chartHeight = height - paddingBottom - paddingTop

  // Calculate High / Low price ranges (including active SL, TP, Entry lines so overlays don't clip)
  let minPrice = Math.min(...candleList.map((c) => c.low))
  let maxPrice = Math.max(...candleList.map((c) => c.high))

  // Incorporate trading lines in price scale
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

  // Add 4% head/tail buffer
  const priceRange = maxPrice - minPrice || 1
  minPrice -= priceRange * 0.04
  maxPrice += priceRange * 0.04
  const adjustedRange = maxPrice - minPrice

  function priceToY(p: number) {
    return paddingTop + chartHeight - ((p - minPrice) / adjustedRange) * chartHeight
  }

  // Draw Horizontal Grid & Price Ticks
  const gridCount = 5
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

    // Price label on right axis
    ctx.fillText(p >= 100 ? p.toFixed(2) : p.toFixed(4), chartWidth + 6, y + 3)
  }

  // Volume scale
  const maxVol = Math.max(...candleList.map((c) => c.vol)) || 1
  const volHeight = chartHeight * 0.22

  // Candle width calculations
  const count = candleList.length
  const candleGap = chartWidth / count
  const candleWidth = Math.max(2, candleGap * 0.65)

  // Draw Candles & Volume
  for (let i = 0; i < count; i++) {
    const c = candleList[i]
    const x = i * candleGap + candleGap / 2
    const isUp = c.close >= c.open

    const yOpen = priceToY(c.open)
    const yClose = priceToY(c.close)
    const yHigh = priceToY(c.high)
    const yLow = priceToY(c.low)

    // 1. Volume Bar
    const vH = (c.vol / maxVol) * volHeight
    const vY = paddingTop + chartHeight - vH
    ctx.fillStyle = isUp ? 'rgba(16, 185, 129, 0.2)' : 'rgba(244, 63, 94, 0.2)'
    ctx.fillRect(x - candleWidth / 2, vY, candleWidth, vH)

    // 2. High/Low Wick
    ctx.strokeStyle = isUp ? upColor : downColor
    ctx.beginPath()
    ctx.moveTo(x, yHigh)
    ctx.lineTo(x, yLow)
    ctx.stroke()

    // 3. Candle Body
    const bodyTop = Math.min(yOpen, yClose)
    const bodyHeight = Math.max(1.5, Math.abs(yOpen - yClose))
    ctx.fillStyle = isUp ? upColor : downColor
    ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight)
  }

  // Draw Moving Averages: MA5 & MA20
  function drawMA(period: number, color: string) {
    if (candleList.length < period) return
    ctx.strokeStyle = color
    ctx.lineWidth = 1.2
    ctx.beginPath()
    let started = false

    for (let i = period - 1; i < candleList.length; i++) {
      let sum = 0
      for (let k = 0; k < period; k++) {
        sum += candleList[i - k].close
      }
      const ma = sum / period
      const x = i * candleGap + candleGap / 2
      const y = priceToY(ma)
      if (!started) {
        ctx.moveTo(x, y)
        started = true
      } else {
        ctx.lineTo(x, y)
      }
    }
    ctx.stroke()
  }

  drawMA(5, '#EAB308') // MA5 Gold
  drawMA(20, '#8B5CF6') // MA20 Purple

  // ==========================================
  // 🎯 Visual Trading Overlays (Entry, SL, TP, Orders)
  // ==========================================

  // Helper to draw horizontal tagged lines
  function drawTradeLine(
    price: number,
    color: string,
    dash: number[],
    label: string,
    subLabel: string = ''
  ) {
    if (!price || price <= 0) return
    const y = priceToY(price)
    if (y < paddingTop - 10 || y > paddingTop + chartHeight + 10) return

    ctx.save()
    ctx.strokeStyle = color
    ctx.lineWidth = 1.5
    ctx.setLineDash(dash)

    // Main line
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(chartWidth, y)
    ctx.stroke()

    // Right Tag Capsule
    ctx.setLineDash([])
    const tagWidth = 64
    const tagHeight = 18
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.roundRect(chartWidth + 2, y - tagHeight / 2, tagWidth, tagHeight, 3)
    ctx.fill()

    ctx.fillStyle = '#FFFFFF'
    ctx.font = 'bold 9px monospace'
    ctx.textAlign = 'center'
    ctx.fillText(price >= 100 ? price.toFixed(2) : price.toFixed(4), chartWidth + 2 + tagWidth / 2, y + 3)

    // Left On-Chart Ribbon
    const ribbonWidth = Math.min(180, chartWidth * 0.4)
    ctx.fillStyle = isDark ? 'rgba(20, 22, 31, 0.85)' : 'rgba(255, 255, 255, 0.9)'
    ctx.strokeStyle = color
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.roundRect(8, y - 11, ribbonWidth, 16, 4)
    ctx.fill()
    ctx.stroke()

    ctx.fillStyle = color
    ctx.font = 'bold 9px monospace'
    ctx.textAlign = 'left'
    ctx.fillText(`${label} ${subLabel}`, 14, y + 1)

    ctx.restore()
  }

  // 1. Current Mark Price Line (Pulsing live beacon)
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

    // Right mark badge
    ctx.setLineDash([])
    ctx.fillStyle = blueColor
    ctx.beginPath()
    ctx.roundRect(chartWidth + 2, markY - 8, 64, 16, 3)
    ctx.fill()
    ctx.fillStyle = '#FFFFFF'
    ctx.font = 'bold 9px monospace'
    ctx.textAlign = 'center'
    ctx.fillText(
      currentPrice.value >= 100 ? currentPrice.value.toFixed(2) : currentPrice.value.toFixed(4),
      chartWidth + 34,
      markY + 3
    )
    ctx.restore()
  }

  // 2. Entry Price Line (if holding position or pending order)
  if (activePosition.value || activeOrder.value) {
    const sideText = liveSide.value === 'long' ? '🟢 多头开仓成本' : '🔴 空头开仓成本'
    const sideColor = liveSide.value === 'long' ? upColor : downColor
    drawTradeLine(liveEntry.value, sideColor, [], sideText, `$${liveEntry.value.toFixed(2)}`)
  }

  // 3. Stop Loss Line (Cloud OCO SL)
  if (effectiveSL.value > 0) {
    const slDistPct =
      liveEntry.value > 0
        ? (((effectiveSL.value - liveEntry.value) / liveEntry.value) * 100).toFixed(2)
        : '0.00'
    const slLabel = simMode.value ? '🛠️ 模拟止损 (SL)' : '🛑 交易所云端止损 (SL)'
    drawTradeLine(effectiveSL.value, downColor, [5, 4], slLabel, `${slDistPct}%`)
  }

  // 4. Take Profit Line (Cloud TP)
  if (effectiveTP.value > 0) {
    const tpDistPct =
      liveEntry.value > 0
        ? (((effectiveTP.value - liveEntry.value) / liveEntry.value) * 100).toFixed(2)
        : '0.00'
    const tpLabel = simMode.value ? '🛠️ 模拟止盈 (TP)' : '🎯 目标云端止盈 (TP)'
    drawTradeLine(effectiveTP.value, upColor, [5, 4], tpLabel, `+${tpDistPct}%`)
  }

  // 5. Pending Maker Order Line (if exists)
  if (activeOrder.value && Number(activeOrder.value.px) > 0) {
    const ordPx = Number(activeOrder.value.px)
    drawTradeLine(
      ordPx,
      amberColor,
      [3, 3],
      '⏳ 挂单委托',
      `$${ordPx} (${activeOrder.value.sz}张)`
    )
  }

  // Draw Crosshair on mouse hover
  if (hoverPos.value) {
    const { x, y } = hoverPos.value
    if (x >= 0 && x <= chartWidth && y >= paddingTop && y <= paddingTop + chartHeight) {
      ctx.save()
      ctx.strokeStyle = isDark ? 'rgba(255, 255, 255, 0.3)' : 'rgba(0, 0, 0, 0.3)'
      ctx.lineWidth = 1
      ctx.setLineDash([3, 3])

      ctx.beginPath()
      ctx.moveTo(x, paddingTop)
      ctx.lineTo(x, paddingTop + chartHeight)
      ctx.moveTo(0, y)
      ctx.lineTo(chartWidth, y)
      ctx.stroke()
      ctx.restore()
    }
  }
}

// Mouse events on canvas
function handleMouseMove(e: MouseEvent) {
  const canvas = canvasRef.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top

  const paddingRight = 72
  const chartWidth = canvas.clientWidth - paddingRight
  if (x >= 0 && x <= chartWidth) {
    hoverPos.value = { x, y }
    const idx = Math.floor((x / chartWidth) * candles.value.length)
    if (idx >= 0 && idx < candles.value.length) {
      hoverCandle.value = candles.value[idx]
    }
  } else {
    hoverPos.value = null
    hoverCandle.value = null
  }
  drawChart()
}

function handleMouseLeave() {
  hoverPos.value = null
  hoverCandle.value = null
  drawChart()
}

// Step adjust SL & TP in simulation mode
function adjustSL(deltaPercent: number) {
  if (!simMode.value) simMode.value = true
  const cur = simStopLoss.value || liveStopLoss.value
  const step = cur * deltaPercent
  simStopLoss.value = Number((cur + step).toFixed(4))
  drawChart()
}

function adjustTP(deltaPercent: number) {
  if (!simMode.value) simMode.value = true
  const cur = simTakeProfit.value || liveTakeProfit.value
  const step = cur * deltaPercent
  simTakeProfit.value = Number((cur + step).toFixed(4))
  drawChart()
}

// Copy simulated parameters
function copySimulationSummary() {
  const m = riskRewardMetrics.value
  const text = `【R20 视觉风控测算契约】\n标的: ${currentSymbol.value}-USDT-SWAP\n方向: ${liveSide.value === 'long' ? 'BUY_LONG 多头' : 'SELL_SHORT 空头'}\n入场价: $${m.entry.toFixed(2)}\n止损价 (SL): $${m.sl.toFixed(2)} (${m.atrMultiple.toFixed(2)}x ATR)\n止盈价 (TP): $${m.tp.toFixed(2)}\n预期盈亏比 (R:R): ${m.rrRatio.toFixed(2)}:1 ${m.isRrCompliant ? '✅契约达标' : '⚠️低于2.0'}`
  navigator.clipboard.writeText(text)
  copied.value = true
  setTimeout(() => {
    copied.value = false
  }, 2000)
}

function selectSymbol(s: string) {
  currentSymbol.value = s
  emit('select-symbol', s)
  loadCandles()
}

// Watchers
watch(() => props.initialSymbol, (val) => {
  if (val && val !== currentSymbol.value && symbols.includes(val)) {
    selectSymbol(val)
  }
})

watch([() => currentPeriod.value, () => store.positions, () => store.pendingOrders], () => {
  loadCandles()
})

onMounted(() => {
  if (containerRef.value) {
    resizeObserver = new ResizeObserver(() => {
      drawChart()
    })
    resizeObserver.observe(containerRef.value)
  }
  loadCandles()
})

onUnmounted(() => {
  if (resizeObserver) resizeObserver.disconnect()
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
    <!-- Top Desk Bar: Symbol Pills + Period Switcher + Live Ticker Status -->
    <div
      class="px-3.5 py-2.5 sm:px-4 sm:py-3 border-b flex flex-wrap items-center justify-between gap-2.5"
      style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);"
    >
      <!-- Left: Symbol Selector Pills -->
      <div class="flex items-center space-x-1 sm:space-x-1.5 overflow-x-auto py-0.5">
        <button
          v-for="sym in symbols"
          :key="sym"
          @click="selectSymbol(sym)"
          class="h-7 sm:h-7.5 px-2.5 sm:px-3 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer border flex items-center space-x-1.5 shrink-0"
          :style="currentSymbol === sym
            ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--color-brand)' }
            : { backgroundColor: 'var(--bg-card)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
        >
          <!-- Active Position Beacon -->
          <span
            v-if="store.positions.some((p: any) => p.instId.startsWith(sym))"
            class="w-1.5 h-1.5 rounded-full"
            :class="store.positions.find((p: any) => p.instId.startsWith(sym))?.side === 'long' ? 'bg-emerald-500' : 'bg-rose-500'"
            title="该标的持有活动持仓"
          ></span>
          <span>{{ sym }}</span>
        </button>
      </div>

      <!-- Center / Right: Timeframe & Actions -->
      <div class="flex items-center space-x-2 shrink-0">
        <!-- Period Switcher -->
        <div
          class="flex items-center p-0.5 rounded-lg border text-[11px] font-mono"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <button
            v-for="p in periods"
            :key="p.id"
            @click="currentPeriod = p.id; loadCandles()"
            class="h-6 px-2 rounded-md font-bold transition-all cursor-pointer"
            :style="currentPeriod === p.id
              ? { backgroundColor: 'var(--bg-badge)', color: 'var(--text-main)', borderColor: 'var(--border-medium)' }
              : { color: 'var(--text-muted)' }"
          >
            {{ p.label }}
          </button>
        </div>

        <!-- Simulation Toggle Button -->
        <button
          @click="simMode = !simMode; if (simMode) initSimulation(); drawChart()"
          class="h-7 px-2.5 rounded-lg text-xs font-mono border flex items-center space-x-1.5 transition-all cursor-pointer font-bold"
          :style="simMode
            ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--color-brand)' }
            : { backgroundColor: 'var(--bg-card)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
          :title="simMode ? '退出模拟平移' : '开启止盈止损可视化试算平移'"
        >
          <Sliders class="w-3.5 h-3.5" />
          <span class="hidden sm:inline">{{ simMode ? '退出试算' : '平移试算 R:R' }}</span>
          <span class="sm:hidden">试算</span>
        </button>

        <!-- Refresh Button -->
        <button
          @click="loadCandles"
          :disabled="isLoading"
          class="h-7 w-7 rounded-lg border flex items-center justify-center transition-colors cursor-pointer"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          title="刷新最新行情蜡烛线"
        >
          <RefreshCw class="w-3.5 h-3.5" :class="isLoading ? 'animate-spin' : ''" />
        </button>
      </div>
    </div>

    <!-- Telemetry Ticker Info Bar (Crosshair / Current Price Readout) -->
    <div
      class="px-4 py-2 border-b flex flex-wrap items-center justify-between text-xs font-mono gap-2"
      style="border-color: var(--border-subtle); background-color: var(--bg-app);"
    >
      <div class="flex items-center space-x-3">
        <!-- Symbol & Price -->
        <div class="flex items-center space-x-1.5">
          <span class="font-black text-sm" style="color: var(--text-main);">{{ currentSymbol }}/USDT</span>
          <span class="text-[10px] px-1.5 py-0.2 rounded border font-bold" style="background-color: var(--bg-badge); border-color: var(--border-subtle); color: var(--text-muted);">
            PERP
          </span>
        </div>

        <div class="flex items-center space-x-2 font-bold num-tabular">
          <span class="text-sm font-black" style="color: var(--text-main);">${{ currentPrice >= 100 ? currentPrice.toFixed(2) : currentPrice.toFixed(4) }}</span>
          <span class="text-[11px]" style="color: var(--text-faint);">1H ATR: ${{ currentAtr.toFixed(2) }}</span>
        </div>
      </div>

      <!-- Hover OHLC Data or Position Info -->
      <div v-if="hoverCandle" class="flex items-center space-x-2 text-[11px] num-tabular" style="color: var(--text-muted);">
        <span>O: <strong style="color: var(--text-main);">${{ hoverCandle.open }}</strong></span>
        <span>H: <strong class="text-emerald-500">${{ hoverCandle.high }}</strong></span>
        <span>L: <strong class="text-rose-500">${{ hoverCandle.low }}</strong></span>
        <span>C: <strong style="color: var(--text-main);">${{ hoverCandle.close }}</strong></span>
        <span>V: {{ hoverCandle.vol.toFixed(0) }}</span>
      </div>
      <div v-else-if="activePosition" class="flex items-center space-x-2 text-[11px] font-mono">
        <span
          class="px-1.5 py-0.2 rounded border font-bold"
          :class="activePosition.side === 'long' ? 'capsule-direction-long' : 'capsule-direction-short'"
        >
          {{ activePosition.side === 'long' ? '多头持有' : '空头持有' }} {{ activePosition.pos }}张
        </span>
        <span style="color: var(--text-muted);">开仓: ${{ Number(activePosition.avgPx).toFixed(2) }}</span>
        <span :style="{ color: Number(activePosition.upl) >= 0 ? 'var(--color-up)' : 'var(--color-down)' }">
          浮盈: {{ Number(activePosition.upl) >= 0 ? '+' : '' }}{{ Number(activePosition.upl).toFixed(2) }} U
        </span>
      </div>
      <div v-else class="text-[11px] font-mono" style="color: var(--text-faint);">
        移动光标可在图表上查看逐根 K 线高低与成交量
      </div>
    </div>

    <!-- Chart Canvas Container -->
    <div
      ref="containerRef"
      class="relative w-full h-[280px] sm:h-[340px] 2xl:h-[380px] cursor-crosshair select-none"
      style="background-color: var(--bg-card);"
    >
      <canvas
        ref="canvasRef"
        class="w-full h-full block"
        @mousemove="handleMouseMove"
        @mouseleave="handleMouseLeave"
      ></canvas>

      <!-- Legend Overlay (Top Left) -->
      <div class="absolute top-2.5 left-3 flex items-center space-x-3 text-[10px] font-mono pointer-events-none opacity-80">
        <span class="flex items-center space-x-1">
          <span class="w-2 h-0.5 bg-yellow-500"></span>
          <span style="color: var(--text-muted);">MA5</span>
        </span>
        <span class="flex items-center space-x-1">
          <span class="w-2 h-0.5 bg-purple-500"></span>
          <span style="color: var(--text-muted);">MA20</span>
        </span>
        <span v-if="effectiveSL > 0" class="flex items-center space-x-1">
          <span class="w-2 h-0.5 border-b border-dashed border-rose-500"></span>
          <span class="text-rose-400 font-bold">SL 止损</span>
        </span>
        <span v-if="effectiveTP > 0" class="flex items-center space-x-1">
          <span class="w-2 h-0.5 border-b border-dashed border-emerald-500"></span>
          <span class="text-emerald-400 font-bold">TP 止盈</span>
        </span>
      </div>
    </div>

    <!-- Bottom Interactive Risk / Reward (R:R) Simulator Console -->
    <div
      class="p-3 sm:p-4 border-t transition-colors"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
    >
      <div class="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-3">
        <!-- 1. Four Core Health Metrics -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3 w-full lg:w-auto">
          <!-- R:R Ratio Metric -->
          <div
            class="p-2 sm:p-2.5 rounded-lg border font-mono flex flex-col justify-between"
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
            class="p-2 sm:p-2.5 rounded-lg border font-mono flex flex-col justify-between"
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

          <!-- Potential Reward USD -->
          <div
            class="p-2 sm:p-2.5 rounded-lg border font-mono flex flex-col justify-between"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <span class="text-[10px] uppercase font-bold text-emerald-500">
              预期收益目标 (TP)
            </span>
            <div class="flex items-baseline space-x-1 mt-0.5">
              <span class="text-base sm:text-lg font-black text-emerald-500 num-tabular">
                +${{ riskRewardMetrics.rewardDist.toFixed(2) }}
              </span>
              <span class="text-[9px] text-emerald-400 font-bold">
                ({{ liveEntry > 0 ? (((riskRewardMetrics.rewardDist) / liveEntry) * 100).toFixed(1) : 0 }}%)
              </span>
            </div>
          </div>

          <!-- Max Risk USD -->
          <div
            class="p-2 sm:p-2.5 rounded-lg border font-mono flex flex-col justify-between"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <span class="text-[10px] uppercase font-bold text-rose-500">
              最大硬风控风险 (SL)
            </span>
            <div class="flex items-baseline space-x-1 mt-0.5">
              <span class="text-base sm:text-lg font-black text-rose-500 num-tabular">
                -${{ riskRewardMetrics.riskDist.toFixed(2) }}
              </span>
              <span class="text-[9px] text-rose-400 font-bold">
                ({{ liveEntry > 0 ? (((riskRewardMetrics.riskDist) / liveEntry) * 100).toFixed(1) : 0 }}%)
              </span>
            </div>
          </div>
        </div>

        <!-- 2. Simulation Adjustment Controls (Active in Sim Mode) -->
        <div class="flex flex-wrap items-center gap-2 w-full lg:w-auto justify-end">
          <div v-if="simMode" class="flex flex-wrap items-center gap-1.5 text-xs font-mono">
            <!-- SL Steppers -->
            <div class="flex items-center space-x-1 bg-rose-950/20 border border-rose-900/40 px-2 py-1 rounded-lg text-[11px]">
              <span class="text-rose-400 font-bold">SL:</span>
              <button @click="adjustSL(-0.005)" class="px-1 py-0.5 bg-rose-900/40 rounded hover:bg-rose-800/60 cursor-pointer text-rose-300">-0.5%</button>
              <button @click="adjustSL(0.005)" class="px-1 py-0.5 bg-rose-900/40 rounded hover:bg-rose-800/60 cursor-pointer text-rose-300">+0.5%</button>
            </div>

            <!-- TP Steppers -->
            <div class="flex items-center space-x-1 bg-emerald-950/20 border border-emerald-900/40 px-2 py-1 rounded-lg text-[11px]">
              <span class="text-emerald-400 font-bold">TP:</span>
              <button @click="adjustTP(-0.01)" class="px-1 py-0.5 bg-emerald-900/40 rounded hover:bg-emerald-800/60 cursor-pointer text-emerald-300">-1%</button>
              <button @click="adjustTP(0.01)" class="px-1 py-0.5 bg-emerald-900/40 rounded hover:bg-emerald-800/60 cursor-pointer text-emerald-300">+1%</button>
            </div>

            <button
              @click="resetSimulation"
              class="px-2.5 py-1 rounded-lg border text-[11px] font-mono flex items-center space-x-1 cursor-pointer transition-colors"
              style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
              title="重置为实盘云端止损止盈值"
            >
              <RotateCcw class="w-3 h-3" />
              <span>复位</span>
            </button>
          </div>

          <!-- Copy Button -->
          <button
            @click="copySimulationSummary"
            class="btn-admin-secondary text-xs font-mono flex items-center space-x-1.5"
            title="复制当前风控测算与点位摘要"
          >
            <Check v-if="copied" class="w-3.5 h-3.5 text-emerald-400" />
            <Copy v-else class="w-3.5 h-3.5" />
            <span>{{ copied ? '已复制摘要' : '复制风控参数' }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useTheme } from '../composables/useTheme'
import {
  TrendingUp,
  RefreshCw,
  Sliders,
  RotateCcw,
  Copy,
  Check,
  ShieldCheck,
  ShieldAlert,
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

// ==========================================
// 1. 动态自动读取系统设置的监控/交易标的
// ==========================================
const availableSymbols = computed<string[]>(() => {
  const set = new Set<string>()
  // 从系统因子监控矩阵读取
  for (const f of store.factors) {
    const sym = f.name || f.instId?.split('-')[0]
    if (sym) set.add(sym.toUpperCase())
  }
  // 从活动持仓读取
  for (const p of store.positions) {
    const sym = p.name || p.instId?.split('-')[0]
    if (sym) set.add(sym.toUpperCase())
  }
  // 从在途挂单读取
  for (const o of store.pendingOrders) {
    const sym = o.name || (o as any).inst || o.instId?.split('-')[0]
    if (sym) set.add(sym.toUpperCase())
  }
  if (set.size === 0) {
    return ['BTC', 'ETH', 'SOL', 'DOGE', 'SUI', 'ASTER']
  }
  return Array.from(set)
})

const periods = [
  { id: '15m', label: '15分', tv: '15' },
  { id: '1H', label: '1时', tv: '60' },
  { id: '4H', label: '4时', tv: '240' },
  { id: '1D', label: '1日', tv: 'D' },
]

const currentSymbol = ref<string>('BTC')
const currentPeriod = ref<string>('1H')
const chartEngine = ref<'native' | 'tv'>('native') // 默认原生极速高饱满K线，支持切换
const isLoading = ref<boolean>(false)
const copied = ref<boolean>(false)
const candleCountdown = ref<string>('00:00')
let pollTimer: any = null
let tickTimer: any = null

// 指标切换选项 (参考 OKX App)
type MainIndicatorType = 'BOLL' | 'MA' | 'EMA' | 'NONE'
type SubIndicatorType = 'MACD' | 'RSI' | 'KDJ' | 'VOL' | 'NONE'
const mainIndicator = ref<MainIndicatorType>('BOLL')
const subIndicator = ref<SubIndicatorType>('MACD')

// 模拟调价平移
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

// 当前标的映射
const currentInstId = computed(() => `${currentSymbol.value}-USDT-SWAP`)

const activePosition = computed(() => {
  return store.positions.find(
    (p: any) =>
      p.instId === currentInstId.value ||
      p.name?.toUpperCase() === currentSymbol.value ||
      p.instId?.toUpperCase().startsWith(currentSymbol.value)
  )
})

const activeOrder = computed(() => {
  return store.pendingOrders.find(
    (o: any) =>
      o.instId === currentInstId.value ||
      o.name?.toUpperCase() === currentSymbol.value ||
      o.instId?.toUpperCase().startsWith(currentSymbol.value)
  )
})

const currentFactor = computed(() => {
  return store.factors.find(
    (f: any) =>
      f.instId === currentInstId.value ||
      f.name?.toUpperCase() === currentSymbol.value ||
      f.instId?.toUpperCase().startsWith(currentSymbol.value)
  )
})

// 当前价格与 ATR
const currentPrice = computed(() => {
  if (activePosition.value?.markPx) return Number(activePosition.value.markPx)
  if (currentFactor.value?.price) return Number(currentFactor.value.price)
  if (candles.value.length > 0) return candles.value[candles.value.length - 1].close
  return 100.0
})

const liveChangePct = computed(() => {
  if (candles.value.length < 2) return 0.0
  const first = candles.value[0]
  const last = candles.value[candles.value.length - 1]
  if (!first || !last || first.open <= 0) return 0.0
  return ((last.close - first.open) / first.open) * 100
})

const currentAtr = computed(() => {
  const f = currentFactor.value
  if (!f) return currentPrice.value * 0.02
  const atr = Number(f.atr1h ?? f.atr ?? 0)
  return atr > 0 ? atr : currentPrice.value * 0.02
})

// 真实入场成本与方向
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
// 2. 真实科学的盈亏与风险金额计算
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

  const isRrCompliant = rrRatio >= 2.0
  const isAtrOptimal = atrMultiple >= 1.7 && atrMultiple <= 2.3

  // 科学计算美元金额
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
    isRrCompliant,
    isAtrOptimal,
    hasRealPosition,
    estProfitUsd,
    estRiskUsd,
  }
})

// ==========================================
// 3. 高精度数学指标计算 (BOLL, MA, EMA, MACD, RSI, KDJ)
// ==========================================
interface IndicatorsResult {
  boll?: { mb: number[]; ub: number[]; lb: number[] }
  ma?: { ma5: number[]; ma10: number[]; ma20: number[] }
  ema?: { ema7: number[]; ema25: number[]; ema99: number[] }
  macd?: { dif: number[]; dea: number[]; bar: number[] }
  rsi?: number[]
  kdj?: { k: number[]; d: number[]; j: number[] }
}

const computedIndicators = computed<IndicatorsResult>(() => {
  const list = candles.value
  const len = list.length
  if (len === 0) return {}

  const closes = list.map((c) => c.close)

  // 1. BOLL (20, 2)
  const mb: number[] = new Array(len).fill(NaN)
  const ub: number[] = new Array(len).fill(NaN)
  const lb: number[] = new Array(len).fill(NaN)
  for (let i = 19; i < len; i++) {
    let sum = 0
    for (let k = 0; k < 20; k++) sum += closes[i - k]
    const mean = sum / 20
    let variance = 0
    for (let k = 0; k < 20; k++) variance += Math.pow(closes[i - k] - mean, 2)
    const std = Math.sqrt(variance / 20)
    mb[i] = mean
    ub[i] = mean + 2 * std
    lb[i] = mean - 2 * std
  }

  // 2. MA (5, 10, 20)
  function calcMA(p: number) {
    const arr: number[] = new Array(len).fill(NaN)
    for (let i = p - 1; i < len; i++) {
      let sum = 0
      for (let k = 0; k < p; k++) sum += closes[i - k]
      arr[i] = sum / p
    }
    return arr
  }

  // 3. EMA
  function calcEMA(p: number) {
    const arr: number[] = new Array(len).fill(NaN)
    if (len < p) return arr
    let prev = closes[0]
    const k = 2 / (p + 1)
    arr[0] = prev
    for (let i = 1; i < len; i++) {
      prev = closes[i] * k + prev * (1 - k)
      if (i >= p - 1) arr[i] = prev
    }
    return arr
  }

  // 4. MACD (12, 26, 9)
  const ema12 = calcEMA(12)
  const ema26 = calcEMA(26)
  const dif: number[] = new Array(len).fill(NaN)
  for (let i = 0; i < len; i++) {
    if (!isNaN(ema12[i]) && !isNaN(ema26[i])) {
      dif[i] = ema12[i] - ema26[i]
    }
  }
  const dea: number[] = new Array(len).fill(NaN)
  const bar: number[] = new Array(len).fill(NaN)
  let prevDea = 0
  let deaInit = false
  const k9 = 2 / (9 + 1)
  for (let i = 0; i < len; i++) {
    if (!isNaN(dif[i])) {
      if (!deaInit) {
        prevDea = dif[i]
        dea[i] = prevDea
        deaInit = true
      } else {
        prevDea = dif[i] * k9 + prevDea * (1 - k9)
        dea[i] = prevDea
      }
      bar[i] = (dif[i] - dea[i]) * 2
    }
  }

  // 5. RSI (14)
  const rsi: number[] = new Array(len).fill(NaN)
  if (len > 14) {
    let gains = 0
    let losses = 0
    for (let i = 1; i <= 14; i++) {
      const diff = closes[i] - closes[i - 1]
      if (diff >= 0) gains += diff
      else losses -= diff
    }
    let avgGain = gains / 14
    let avgLoss = losses / 14
    rsi[14] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)
    for (let i = 15; i < len; i++) {
      const diff = closes[i] - closes[i - 1]
      const g = diff >= 0 ? diff : 0
      const l = diff < 0 ? -diff : 0
      avgGain = (avgGain * 13 + g) / 14
      avgLoss = (avgLoss * 13 + l) / 14
      rsi[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss)
    }
  }

  // 6. KDJ (9, 3, 3)
  const kArr: number[] = new Array(len).fill(NaN)
  const dArr: number[] = new Array(len).fill(NaN)
  const jArr: number[] = new Array(len).fill(NaN)
  let prevK = 50
  let prevD = 50
  for (let i = 0; i < len; i++) {
    if (i < 8) {
      kArr[i] = 50
      dArr[i] = 50
      jArr[i] = 50
      continue
    }
    let l9 = Infinity
    let h9 = -Infinity
    for (let k = 0; k < 9; k++) {
      l9 = Math.min(l9, list[i - k].low)
      h9 = Math.max(h9, list[i - k].high)
    }
    const rsv = h9 === l9 ? 50 : ((closes[i] - l9) / (h9 - l9)) * 100
    prevK = (2 / 3) * prevK + (1 / 3) * rsv
    prevD = (2 / 3) * prevD + (1 / 3) * prevK
    kArr[i] = prevK
    dArr[i] = prevD
    jArr[i] = 3 * prevK - 2 * prevD
  }

  return {
    boll: { mb, ub, lb },
    ma: { ma5: calcMA(5), ma10: calcMA(10), ma20: calcMA(20) },
    ema: { ema7: calcEMA(7), ema25: calcEMA(25), ema99: calcEMA(99) },
    macd: { dif, dea, bar },
    rsi,
    kdj: { k: kArr, d: dArr, j: jArr },
  }
})

// 最新指标当前读数
const currentIndicatorHeader = computed(() => {
  const len = candles.value.length
  if (len === 0) return { mainText: '', subText: '' }
  const idx = hoverCandle.value ? candles.value.indexOf(hoverCandle.value) : len - 1
  const ind = computedIndicators.value

  let mainText = ''
  if (mainIndicator.value === 'BOLL' && ind.boll) {
    const mb = ind.boll.mb[idx]
    const ub = ind.boll.ub[idx]
    const lb = ind.boll.lb[idx]
    if (!isNaN(mb)) {
      mainText = `BOLL20: ${mb.toFixed(1)}  UB: ${ub.toFixed(1)}  LB: ${lb.toFixed(1)}`
    }
  } else if (mainIndicator.value === 'MA' && ind.ma) {
    const m5 = ind.ma.ma5[idx]
    const m10 = ind.ma.ma10[idx]
    const m20 = ind.ma.ma20[idx]
    mainText = `MA5: ${isNaN(m5) ? '--' : m5.toFixed(1)}  MA10: ${isNaN(m10) ? '--' : m10.toFixed(1)}  MA20: ${isNaN(m20) ? '--' : m20.toFixed(1)}`
  } else if (mainIndicator.value === 'EMA' && ind.ema) {
    const e7 = ind.ema.ema7[idx]
    const e25 = ind.ema.ema25[idx]
    mainText = `EMA7: ${isNaN(e7) ? '--' : e7.toFixed(1)}  EMA25: ${isNaN(e25) ? '--' : e25.toFixed(1)}`
  }

  let subText = ''
  if (subIndicator.value === 'MACD' && ind.macd) {
    const dif = ind.macd.dif[idx]
    const dea = ind.macd.dea[idx]
    const bar = ind.macd.bar[idx]
    subText = `DIF: ${isNaN(dif) ? '--' : dif.toFixed(1)}  DEA: ${isNaN(dea) ? '--' : dea.toFixed(1)}  MACD: ${isNaN(bar) ? '--' : bar.toFixed(1)}`
  } else if (subIndicator.value === 'RSI' && ind.rsi) {
    const r = ind.rsi[idx]
    subText = `RSI14: ${isNaN(r) ? '--' : r.toFixed(2)}`
  } else if (subIndicator.value === 'KDJ' && ind.kdj) {
    const k = ind.kdj.k[idx]
    const d = ind.kdj.d[idx]
    subText = `K: ${isNaN(k) ? '--' : k.toFixed(1)}  D: ${isNaN(d) ? '--' : d.toFixed(1)}  J: ${isNaN(ind.kdj.j[idx]) ? '--' : ind.kdj.j[idx].toFixed(1)}`
  } else if (subIndicator.value === 'VOL') {
    const c = candles.value[idx]
    subText = `VOL: ${c ? c.vol.toFixed(1) : '--'}`
  }

  return { mainText, subText }
})

// ==========================================
// 4. 彻底根治“K线太扁”——高精度 Local Scale 渲染
// ==========================================
function drawChart() {
  if (chartEngine.value !== 'native') return
  const canvas = canvasRef.value
  const container = containerRef.value
  if (!canvas || !container) return

  const dpr = window.devicePixelRatio || 1
  const width = container.clientWidth
  const height = container.clientHeight || 480

  canvas.width = width * dpr
  canvas.height = height * dpr
  canvas.style.width = `${width}px`
  canvas.style.height = `${height}px`

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  ctx.resetTransform()
  ctx.scale(dpr, dpr)

  const isDark = theme.value === 'dark'
  const bgCard = isDark ? '#111319' : '#FFFFFF'
  const textMuted = isDark ? '#6B7280' : '#64748B'
  const textMain = isDark ? '#F3F4F6' : '#0F172A'
  const borderSubtle = isDark ? '#1F242F' : '#E5E7EB'
  const upColor = '#10B981' // emerald-500
  const downColor = '#F43F5E' // rose-500
  const orangeBoll = '#F59E0B' // amber-500 for BOLL bands

  ctx.fillStyle = bgCard
  ctx.fillRect(0, 0, width, height)

  const list = candles.value
  if (list.length === 0) {
    ctx.fillStyle = textMuted
    ctx.font = '12px monospace'
    ctx.textAlign = 'center'
    ctx.fillText('正在加载专业 K 线时序数据...', width / 2, height / 2)
    return
  }

  // 布局尺寸切分
  const paddingRight = 72
  const paddingTop = 24
  const paddingBottom = 22
  const hasSubChart = subIndicator.value !== 'NONE'

  // 主图占 70%，副图占 30%
  const chartWidth = width - paddingRight
  const mainHeight = hasSubChart ? (height - paddingTop - paddingBottom) * 0.7 : height - paddingTop - paddingBottom
  const subHeight = hasSubChart ? (height - paddingTop - paddingBottom) * 0.3 : 0
  const subTop = paddingTop + mainHeight

  // ★ 彻底根治“K线太扁”的关键算法：
  // 纵轴价格范围严格只计算当前可见 K 线的 [minLow, maxHigh]（再加入主图指标如 BOLL），
  // 绝不强行纳入远处的止损止盈价！
  let minPrice = Math.min(...list.map((c) => c.low))
  let maxPrice = Math.max(...list.map((c) => c.high))

  // 若开启 BOLL / MA / EMA，将有效指标值适度纳入
  const ind = computedIndicators.value
  if (mainIndicator.value === 'BOLL' && ind.boll) {
    const validUb = ind.boll.ub.filter((v) => !isNaN(v))
    const validLb = ind.boll.lb.filter((v) => !isNaN(v))
    if (validUb.length > 0) maxPrice = Math.max(maxPrice, Math.max(...validUb))
    if (validLb.length > 0) minPrice = Math.min(minPrice, Math.min(...validLb))
  }

  // 上下仅各留白 7%（极佳纵向饱满度）
  const pDelta = maxPrice - minPrice || 1
  minPrice -= pDelta * 0.07
  maxPrice += pDelta * 0.07
  const priceRange = maxPrice - minPrice

  function priceToY(p: number) {
    return paddingTop + mainHeight - ((p - minPrice) / priceRange) * mainHeight
  }

  // 1. 主图水平刻度与标尺网格
  ctx.lineWidth = 1
  ctx.strokeStyle = borderSubtle
  ctx.fillStyle = textMuted
  ctx.font = '10px monospace'
  ctx.textAlign = 'left'

  const gridSteps = 5
  for (let i = 0; i <= gridSteps; i++) {
    const ratio = i / gridSteps
    const y = paddingTop + ratio * mainHeight
    const p = maxPrice - ratio * priceRange

    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(chartWidth, y)
    ctx.stroke()
    ctx.fillText(p >= 100 ? p.toFixed(2) : p.toFixed(4), chartWidth + 6, y + 3)
  }

  // 2. 绘制蜡烛线 (起伏分明、饱满大方)
  const count = list.length
  const candleGap = chartWidth / count
  const candleWidth = Math.max(3, candleGap * 0.72)

  let highestCandle = list[0]
  let lowestCandle = list[0]
  let highestIdx = 0
  let lowestIdx = 0

  for (let i = 0; i < count; i++) {
    const c = list[i]
    if (c.high > highestCandle.high) {
      highestCandle = c
      highestIdx = i
    }
    if (c.low < lowestCandle.low) {
      lowestCandle = c
      lowestIdx = i
    }

    const x = i * candleGap + candleGap / 2
    const isUp = c.close >= c.open
    const yOpen = priceToY(c.open)
    const yClose = priceToY(c.close)
    const yHigh = priceToY(c.high)
    const yLow = priceToY(c.low)

    // 影线
    ctx.strokeStyle = isUp ? upColor : downColor
    ctx.lineWidth = 1.2
    ctx.beginPath()
    ctx.moveTo(x, yHigh)
    ctx.lineTo(x, yLow)
    ctx.stroke()

    // 实体蜡烛 (饱满不干瘪)
    const bodyTop = Math.min(yOpen, yClose)
    const bodyH = Math.max(2, Math.abs(yOpen - yClose))
    ctx.fillStyle = isUp ? upColor : downColor
    ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyH)
  }

  // 3. 高点与低点标注 (参考 OKX App: "82,279.9 —" 与 "— 62,508.3")
  function drawExtremeTag(c: Candle, idx: number, isHigh: boolean) {
    const x = idx * candleGap + candleGap / 2
    const y = priceToY(isHigh ? c.high : c.low)
    const pStr = c.high >= 100 ? (isHigh ? c.high.toFixed(1) : c.low.toFixed(1)) : (isHigh ? c.high.toFixed(4) : c.low.toFixed(4))
    ctx.save()
    ctx.fillStyle = textMuted
    ctx.font = 'bold 9px monospace'
    const isLeft = x > chartWidth * 0.5
    ctx.textAlign = isLeft ? 'right' : 'left'
    const textX = isLeft ? x - 12 : x + 12
    ctx.fillText(isLeft ? `${pStr} ─` : `─ ${pStr}`, textX, isHigh ? y - 4 : y + 10)
    ctx.restore()
  }
  drawExtremeTag(highestCandle, highestIdx, true)
  drawExtremeTag(lowestCandle, lowestIdx, false)

  // 4. 主图指标绘制 (BOLL / MA / EMA)
  function drawLineSeries(data: number[], color: string, width = 1.2) {
    ctx.save()
    ctx.strokeStyle = color
    ctx.lineWidth = width
    ctx.beginPath()
    let started = false
    for (let i = 0; i < count; i++) {
      const v = data[i]
      if (!isNaN(v)) {
        const x = i * candleGap + candleGap / 2
        const y = priceToY(v)
        if (!started) {
          ctx.moveTo(x, y)
          started = true
        } else {
          ctx.lineTo(x, y)
        }
      }
    }
    ctx.stroke()
    ctx.restore()
  }

  if (mainIndicator.value === 'BOLL' && ind.boll) {
    drawLineSeries(ind.boll.ub, orangeBoll, 1.2)
    drawLineSeries(ind.boll.mb, '#10B981', 1.2)
    drawLineSeries(ind.boll.lb, orangeBoll, 1.2)
  } else if (mainIndicator.value === 'MA' && ind.ma) {
    drawLineSeries(ind.ma.ma5, '#EAB308', 1.2)
    drawLineSeries(ind.ma.ma10, '#3B82F6', 1.2)
    drawLineSeries(ind.ma.ma20, '#8B5CF6', 1.2)
  } else if (mainIndicator.value === 'EMA' && ind.ema) {
    drawLineSeries(ind.ema.ema7, '#EAB308', 1.2)
    drawLineSeries(ind.ema.ema25, '#F97316', 1.2)
  }

  // 5. 最新市价水平白光点状线 (参考 OKX App)
  const lastC = list[count - 1]
  if (lastC) {
    const lastY = priceToY(lastC.close)
    ctx.save()
    ctx.strokeStyle = isDark ? 'rgba(255, 255, 255, 0.7)' : 'rgba(0, 0, 0, 0.7)'
    ctx.lineWidth = 1
    ctx.setLineDash([2, 2])
    ctx.beginPath()
    ctx.moveTo(0, lastY)
    ctx.lineTo(chartWidth, lastY)
    ctx.stroke()

    // 右轴光标胶囊 (对标 OKX 官方 App: 现价 + 倒计时)
    ctx.setLineDash([])
    ctx.fillStyle = isDark ? '#262936' : '#E2E8F0'
    ctx.beginPath()
    ctx.roundRect(chartWidth + 2, lastY - 13, 66, 26, 3)
    ctx.fill()
    ctx.fillStyle = textMain
    ctx.font = 'bold 9px monospace'
    ctx.textAlign = 'center'
    ctx.fillText(lastC.close >= 100 ? lastC.close.toFixed(1) : lastC.close.toFixed(4), chartWidth + 35, lastY - 2)
    ctx.fillStyle = isDark ? '#9CA3AF' : '#6B7280'
    ctx.font = '8px monospace'
    ctx.fillText(candleCountdown.value || '00:00', chartWidth + 35, lastY + 9)
    ctx.restore()
  }

  // 6. 四维交易线叠加 (只在处于可视范围内时绘制水平虚线，超出时边缘指示)
  const entry = liveEntry.value
  const sl = effectiveSL.value
  const tp = effectiveTP.value

  function drawTradingLevel(p: number, color: string, label: string) {
    if (p <= 0) return
    if (p >= minPrice && p <= maxPrice) {
      const y = priceToY(p)
      ctx.save()
      ctx.strokeStyle = color
      ctx.lineWidth = 1.4
      ctx.setLineDash([4, 4])
      ctx.beginPath()
      ctx.moveTo(0, y)
      ctx.lineTo(chartWidth, y)
      ctx.stroke()

      ctx.setLineDash([])
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.roundRect(chartWidth + 2, y - 8, 66, 16, 2)
      ctx.fill()
      ctx.fillStyle = '#FFFFFF'
      ctx.font = 'bold 9px monospace'
      ctx.textAlign = 'center'
      ctx.fillText(p >= 100 ? p.toFixed(2) : p.toFixed(4), chartWidth + 35, y + 3)
      ctx.restore()
    }
  }

  if (activePosition.value) {
    drawTradingLevel(entry, liveSide.value === 'long' ? upColor : downColor, '入场成本')
  }
  if (sl > 0) drawTradingLevel(sl, downColor, '止损SL')
  if (tp > 0) drawTradingLevel(tp, upColor, '止盈TP')

  // 7. 副图指标绘制 (MACD / RSI / KDJ / VOL)
  if (hasSubChart) {
    ctx.save()
    // 副图分隔线
    ctx.strokeStyle = borderSubtle
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(0, subTop)
    ctx.lineTo(chartWidth, subTop)
    ctx.stroke()

    if (subIndicator.value === 'MACD' && ind.macd) {
      const bars = ind.macd.bar.filter((v) => !isNaN(v))
      const maxMacd = Math.max(1, Math.max(...bars.map(Math.abs))) * 1.15
      const zeroY = subTop + subHeight / 2

      // MACD 柱状图
      for (let i = 0; i < count; i++) {
        const b = ind.macd.bar[i]
        if (!isNaN(b)) {
          const x = i * candleGap + candleGap / 2
          const bH = (b / maxMacd) * (subHeight / 2)
          ctx.fillStyle = b >= 0 ? upColor : downColor
          ctx.fillRect(x - candleWidth / 2, zeroY, candleWidth, -bH)
        }
      }

      // DIF & DEA 曲线
      function drawSubLine(arr: number[], color: string) {
        ctx.strokeStyle = color
        ctx.lineWidth = 1.2
        ctx.beginPath()
        let st = false
        for (let i = 0; i < count; i++) {
          const v = arr[i]
          if (!isNaN(v)) {
            const x = i * candleGap + candleGap / 2
            const y = zeroY - (v / maxMacd) * (subHeight / 2)
            if (!st) {
              ctx.moveTo(x, y)
              st = true
            } else {
              ctx.lineTo(x, y)
            }
          }
        }
        ctx.stroke()
      }
      drawSubLine(ind.macd.dif, '#EAB308') // 橙黄 DIF
      drawSubLine(ind.macd.dea, '#EC4899') // 粉红 DEA
    } else if (subIndicator.value === 'RSI' && ind.rsi) {
      // 30 / 70 虚线
      const y30 = subTop + subHeight * 0.7
      const y70 = subTop + subHeight * 0.3
      ctx.strokeStyle = isDark ? 'rgba(255,255,255,0.15)' : 'rgba(0,0,0,0.15)'
      ctx.setLineDash([3, 3])
      ctx.beginPath()
      ctx.moveTo(0, y30)
      ctx.lineTo(chartWidth, y30)
      ctx.moveTo(0, y70)
      ctx.lineTo(chartWidth, y70)
      ctx.stroke()
      ctx.setLineDash([])

      // RSI 线
      ctx.strokeStyle = '#EAB308'
      ctx.lineWidth = 1.2
      ctx.beginPath()
      let st = false
      for (let i = 0; i < count; i++) {
        const r = ind.rsi[i]
        if (!isNaN(r)) {
          const x = i * candleGap + candleGap / 2
          const y = subTop + subHeight - (r / 100) * subHeight
          if (!st) {
            ctx.moveTo(x, y)
            st = true
          } else {
            ctx.lineTo(x, y)
          }
        }
      }
      ctx.stroke()
    } else if (subIndicator.value === 'KDJ' && ind.kdj) {
      function drawKDJLine(arr: number[], color: string) {
        ctx.strokeStyle = color
        ctx.lineWidth = 1.2
        ctx.beginPath()
        let st = false
        for (let i = 0; i < count; i++) {
          const v = arr[i]
          if (!isNaN(v)) {
            const x = i * candleGap + candleGap / 2
            const y = subTop + subHeight - (v / 100) * subHeight
            if (!st) {
              ctx.moveTo(x, y)
              st = true
            } else {
              ctx.lineTo(x, y)
            }
          }
        }
        ctx.stroke()
      }
      drawKDJLine(ind.kdj.k, '#EAB308')
      drawKDJLine(ind.kdj.d, '#EC4899')
      drawKDJLine(ind.kdj.j, '#8B5CF6')
    } else if (subIndicator.value === 'VOL') {
      const maxV = Math.max(...list.map((c) => c.vol)) || 1
      for (let i = 0; i < count; i++) {
        const c = list[i]
        const x = i * candleGap + candleGap / 2
        const vH = (c.vol / maxV) * subHeight * 0.9
        ctx.fillStyle = c.close >= c.open ? upColor : downColor
        ctx.fillRect(x - candleWidth / 2, subTop + subHeight - vH, candleWidth, vH)
      }
    }
    ctx.restore()
  }

  // 8. 鼠标十字光标
  if (hoverPos.value) {
    const { x, y } = hoverPos.value
    if (x >= 0 && x <= chartWidth && y >= paddingTop && y <= height - paddingBottom) {
      ctx.save()
      ctx.strokeStyle = isDark ? 'rgba(255, 255, 255, 0.4)' : 'rgba(0, 0, 0, 0.4)'
      ctx.lineWidth = 1
      ctx.setLineDash([3, 3])
      ctx.beginPath()
      ctx.moveTo(x, paddingTop)
      ctx.lineTo(x, height - paddingBottom)
      ctx.moveTo(0, y)
      ctx.lineTo(chartWidth, y)
      ctx.stroke()
      ctx.restore()
    }
  }
}

// 鼠标光标交互
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

// 快速调价
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

function initSimulation() {
  simStopLoss.value = Number(liveStopLoss.value.toFixed(4))
  simTakeProfit.value = Number(liveTakeProfit.value.toFixed(4))
}

function resetSimulation() {
  initSimulation()
  drawChart()
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

function selectSymbol(s: string) {
  currentSymbol.value = s
  emit('select-symbol', s)
  loadCandles()
}

function updateCountdown() {
  const now = new Date()
  const sec = now.getSeconds()
  const min = now.getMinutes()
  const hr = now.getHours()
  let remainSec = 0
  if (currentPeriod.value === '15m') {
    remainSec = (15 - (min % 15)) * 60 - sec
  } else if (currentPeriod.value === '1H') {
    remainSec = (60 - min) * 60 - sec
  } else if (currentPeriod.value === '4H') {
    remainSec = (4 - (hr % 4)) * 3600 - min * 60 - sec
  } else {
    remainSec = 86400 - (hr * 3600 + min * 60 + sec)
  }
  remainSec = Math.max(0, remainSec)
  const h = Math.floor(remainSec / 3600)
  const m = Math.floor((remainSec % 3600) / 60)
  const s = remainSec % 60
  if (h > 0) {
    candleCountdown.value = `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  } else {
    candleCountdown.value = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  }
}

// 实时秒级跳动：让最后一根 K 线收盘价与高低价随盘口跳动
function updateLiveTick() {
  updateCountdown()
  if (candles.value.length === 0) return
  const last = candles.value[candles.value.length - 1]
  const px = currentPrice.value
  if (px > 0 && last) {
    const prevClose = last.close
    last.close = px
    last.high = Math.max(last.high, px)
    last.low = Math.min(last.low, px)
    if (Math.abs(prevClose - px) > 0.0001) {
      drawChart()
    }
  }
}

// 拉取行情 (支持静默轮询)
async function loadCandles(silent = false) {
  if (!silent) isLoading.value = true
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
    console.warn('Candles fetch fallback:', err)
  } finally {
    if (!silent) isLoading.value = false
    nextTick(() => {
      initSimulation()
      drawChart()
    })
  }
}

// 自动匹配首个有持仓的标的
function autoSelectFirstActiveSymbol() {
  if (props.initialSymbol && availableSymbols.value.includes(props.initialSymbol)) {
    currentSymbol.value = props.initialSymbol
    return
  }
  const pos = store.positions[0]
  if (pos) {
    const sym = pos.name || pos.instId?.split('-')[0]
    if (sym && availableSymbols.value.includes(sym.toUpperCase())) {
      currentSymbol.value = sym.toUpperCase()
      return
    }
  }
  if (availableSymbols.value.length > 0) {
    currentSymbol.value = availableSymbols.value[0]
  }
}

watch(availableSymbols, (symbols) => {
  if (symbols.length > 0 && !symbols.includes(currentSymbol.value)) {
    currentSymbol.value = symbols[0]
    loadCandles()
  }
})

watch([() => currentPeriod.value, () => mainIndicator.value, () => subIndicator.value], () => {
  drawChart()
})

onMounted(() => {
  autoSelectFirstActiveSymbol()
  if (containerRef.value) {
    resizeObserver = new ResizeObserver(() => {
      drawChart()
    })
    resizeObserver.observe(containerRef.value)
  }
  loadCandles()
  pollTimer = setInterval(() => {
    if (typeof document !== 'undefined' && !document.hidden) {
      loadCandles(true)
    }
  }, 3000)
  tickTimer = setInterval(updateLiveTick, 1000)
})

onUnmounted(() => {
  if (resizeObserver) resizeObserver.disconnect()
  if (pollTimer) clearInterval(pollTimer)
  if (tickTimer) clearInterval(tickTimer)
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
    <!-- Top Bar: 动态标的横滑 + 周期切换 (参考 OKX App) -->
    <div
      class="px-3 py-2 sm:px-4 sm:py-2.5 border-b flex flex-wrap items-center justify-between gap-2 text-xs font-mono"
      style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);"
    >
      <!-- 动态自动读取系统配置的标的列表 (不再死板照抄) -->
      <div class="flex items-center space-x-1 sm:space-x-1.5 overflow-x-auto py-0.5 max-w-full">
        <button
          v-for="sym in availableSymbols"
          :key="sym"
          @click="selectSymbol(sym)"
          class="px-2.5 py-1 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer border flex items-center space-x-1 shrink-0"
          :style="currentSymbol === sym
            ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--color-brand)' }
            : { backgroundColor: 'var(--bg-card)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
        >
          <!-- 活动持仓指示点 -->
          <span
            v-if="store.positions.some((p: any) => p.name?.toUpperCase() === sym || p.instId?.toUpperCase().startsWith(sym))"
            class="w-1.5 h-1.5 rounded-full"
            :class="store.positions.find((p: any) => p.name?.toUpperCase() === sym || p.instId?.toUpperCase().startsWith(sym))?.side === 'long' ? 'bg-emerald-500' : 'bg-rose-500'"
            title="该标的持有活动持仓"
          ></span>
          <span>{{ sym }}</span>
        </button>
      </div>

      <!-- 周期按钮与刷新 -->
      <div class="flex items-center space-x-1.5 shrink-0">
        <div
          class="flex items-center p-0.5 rounded-lg border text-[11px]"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <button
            v-for="p in periods"
            :key="p.id"
            @click="currentPeriod = p.id; loadCandles()"
            class="h-6 px-2 rounded-md font-bold transition-all cursor-pointer"
            :style="currentPeriod === p.id
              ? { backgroundColor: 'var(--bg-badge)', color: 'var(--text-main)' }
              : { color: 'var(--text-muted)' }"
          >
            {{ p.label }}
          </button>
        </div>

        <button
          @click="simMode = !simMode; if (simMode) initSimulation(); drawChart()"
          class="h-7 px-2 rounded-lg text-xs font-mono border flex items-center space-x-1 transition-all cursor-pointer font-bold"
          :style="simMode
            ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--color-brand)' }
            : { backgroundColor: 'var(--bg-card)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
          :title="simMode ? '退出试算' : '平移试算 R:R'"
        >
          <Sliders class="w-3.5 h-3.5" />
          <span class="hidden sm:inline">{{ simMode ? '退出试算' : '平移试算' }}</span>
        </button>

        <button
          @click="loadCandles"
          class="h-7 w-7 rounded-lg border flex items-center justify-center transition-colors cursor-pointer"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          title="刷新行情"
        >
          <RefreshCw class="w-3.5 h-3.5" :class="isLoading ? 'animate-spin' : ''" />
        </button>
      </div>
    </div>

    <!-- Ticker Info Bar & OKX 指标实时读数 (对标截图) -->
    <div
      class="px-3 py-1.5 sm:px-4 sm:py-2 border-b flex flex-wrap items-center justify-between text-[11px] font-mono gap-2"
      style="border-color: var(--border-subtle); background-color: var(--bg-app);"
    >
      <div class="flex items-center space-x-2">
        <span class="font-black text-xs sm:text-sm" style="color: var(--text-main);">{{ currentSymbol }}USDT 永续</span>
        <span class="font-black text-xs sm:text-sm num-tabular" style="color: var(--text-main);">
          ${{ currentPrice >= 100 ? currentPrice.toFixed(1) : currentPrice.toFixed(4) }}
        </span>
        <span :class="liveChangePct >= 0 ? 'text-emerald-400' : 'text-rose-400'" class="text-[10px] font-bold">
          {{ liveChangePct >= 0 ? '+' : '' }}{{ liveChangePct.toFixed(2) }}%
        </span>
        <span class="flex items-center space-x-1 pl-1">
          <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
          <span class="text-[9px] text-emerald-400 font-bold">实时 3s</span>
        </span>
      </div>

      <!-- 主图指标当前读数 (如 BOLL20: 77873.4 UB: 83427.7 LB: 72319.0) -->
      <div class="text-[10px] font-mono text-amber-400 truncate max-w-full">
        {{ currentIndicatorHeader.mainText }}
      </div>
    </div>

    <!-- K线 Canvas 主视口 (起伏饱满、彻底解决扁平) -->
    <div
      ref="containerRef"
      class="relative w-full h-[400px] sm:h-[460px] 2xl:h-[500px] select-none cursor-crosshair"
      style="background-color: var(--bg-card);"
    >
      <canvas
        ref="canvasRef"
        class="w-full h-full block"
        @mousemove="handleMouseMove"
        @mouseleave="handleMouseLeave"
      ></canvas>

      <!-- 悬浮指示：当云端止盈/止损在可视区外时的提示 (防拉扁) -->
      <div class="absolute top-2 right-20 flex flex-col items-end space-y-1 text-[10px] font-mono pointer-events-none">
        <span v-if="effectiveTP > 0" class="px-1.5 py-0.5 rounded bg-emerald-950/80 border border-emerald-500/40 text-emerald-400 font-bold">
          🎯 止盈TP: ${{ effectiveTP >= 100 ? effectiveTP.toFixed(1) : effectiveTP.toFixed(4) }} (+{{ riskRewardMetrics.rewardPct.toFixed(1) }}%)
        </span>
        <span v-if="effectiveSL > 0" class="px-1.5 py-0.5 rounded bg-rose-950/80 border border-rose-500/40 text-rose-400 font-bold">
          🛑 止损SL: ${{ effectiveSL >= 100 ? effectiveSL.toFixed(1) : effectiveSL.toFixed(4) }} (-{{ riskRewardMetrics.riskPct.toFixed(1) }}%)
        </span>
      </div>

      <!-- 副图指标读数浮层 -->
      <div v-if="currentIndicatorHeader.subText" class="absolute bottom-6 left-3 text-[10px] font-mono text-indigo-400 pointer-events-none">
        {{ currentIndicatorHeader.subText }}
      </div>
    </div>

    <!-- 底部专业指标选择器工具栏 (完全对齐 OKX 官方 App 截图底部) -->
    <div
      class="px-3 py-1.5 border-t flex flex-wrap items-center justify-between gap-2 text-[11px] font-mono"
      style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);"
    >
      <!-- 主图指标切换 -->
      <div class="flex items-center space-x-1 overflow-x-auto">
        <span class="text-[10px] font-bold opacity-60 pr-1">主图:</span>
        <button
          v-for="mi in (['BOLL', 'MA', 'EMA', 'NONE'] as MainIndicatorType[])"
          :key="mi"
          @click="mainIndicator = mi"
          class="px-2 py-0.5 rounded font-bold cursor-pointer transition-all"
          :style="mainIndicator === mi
            ? { backgroundColor: 'var(--bg-badge)', color: 'var(--text-main)', border: '1px solid var(--border-medium)' }
            : { color: 'var(--text-muted)' }"
        >
          {{ mi }}
        </button>
      </div>

      <!-- 副图指标切换 (MACD, RSI, KDJ, VOL) -->
      <div class="flex items-center space-x-1 overflow-x-auto">
        <span class="text-[10px] font-bold opacity-60 pr-1">副图:</span>
        <button
          v-for="si in (['MACD', 'RSI', 'KDJ', 'VOL', 'NONE'] as SubIndicatorType[])"
          :key="si"
          @click="subIndicator = si"
          class="px-2 py-0.5 rounded font-bold cursor-pointer transition-all"
          :style="subIndicator === si
            ? { backgroundColor: 'var(--color-brand-bg)', color: 'var(--color-brand)', border: '1px solid var(--color-brand-border)' }
            : { color: 'var(--text-muted)' }"
        >
          {{ si }}
        </button>
      </div>
    </div>

    <!-- Bottom Interactive Risk / Reward (R:R) Simulator Console (真实金额) -->
    <div
      class="p-3 sm:p-4 border-t transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-3">
        <!-- 四项真实核心风控数据 -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3 w-full lg:w-auto">
          <!-- 期望盈亏比 -->
          <div
            class="p-2.5 rounded-lg border font-mono flex flex-col justify-between"
            :style="{
              backgroundColor: riskRewardMetrics.isRrCompliant ? 'var(--color-up-bg)' : 'var(--color-warn-bg)',
              borderColor: riskRewardMetrics.isRrCompliant ? 'var(--color-up-border)' : 'var(--color-warn-border)',
            }"
          >
            <span class="text-[10px] uppercase font-bold" :style="{ color: riskRewardMetrics.isRrCompliant ? 'var(--color-up)' : 'var(--color-warn)' }">
              {{ riskRewardMetrics.isRrCompliant ? '✅ 期望盈亏比 (R:R)' : '⚠️ 盈亏比不足 2.0' }}
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

          <!-- ATR 呼吸空间 -->
          <div
            class="p-2.5 rounded-lg border font-mono flex flex-col justify-between"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
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

          <!-- 真实预期收益金额 -->
          <div
            class="p-2.5 rounded-lg border font-mono flex flex-col justify-between"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
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

          <!-- 真实最大风险金额 -->
          <div
            class="p-2.5 rounded-lg border font-mono flex flex-col justify-between"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
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

        <!-- 调价与复制操作区 -->
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

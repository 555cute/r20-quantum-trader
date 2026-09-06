<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useTheme } from '../composables/useTheme'
import { useI18n } from '../composables/useI18n'
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  ColorType,
  CrosshairMode,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type UTCTimestamp,
} from 'lightweight-charts'
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
  Layers,
  Activity,
  Maximize2,
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
  for (const f of store.factors) {
    const sym = f.name || f.instId?.split('-')[0]
    if (sym) set.add(sym.toUpperCase())
  }
  for (const p of store.positions) {
    const sym = p.name || p.instId?.split('-')[0]
    if (sym) set.add(sym.toUpperCase())
  }
  for (const o of store.pendingOrders) {
    const sym = o.name || (o as any).inst || o.instId?.split('-')[0]
    if (sym) set.add(sym.toUpperCase())
  }
  if (set.size === 0) {
    return ['BTC', 'ETH', 'SOL', 'DOGE', 'SUI', 'ASTER']
  }
  return Array.from(set)
})

const { t } = useI18n()

const periods = computed(() => [
  { id: '15m', label: t('chart.timeframe15m', '15分'), sec: 900 },
  { id: '1H', label: t('chart.timeframe1h', '1时'), sec: 3600 },
  { id: '4H', label: t('chart.timeframe4h', '4时'), sec: 14400 },
  { id: '1D', label: t('chart.timeframe1d', '1日'), sec: 86400 },
])

const currentSymbol = ref<string>('BTC')
const currentPeriod = ref<string>('1H')

// ==========================================
// 2. 指标多选共存系统 (可同时开启多个，专为视觉LLM与专业看盘优化)
// ==========================================
const indMA = ref<boolean>(true) // MA5 / MA10 / MA20
const indBOLL = ref<boolean>(false) // BOLL 上中下轨
const indVOL = ref<boolean>(true) // VOL 成交量柱状图 (修复比例与量价对比)
const indMACD = ref<boolean>(false) // MACD 柱与双线
const indRSI = ref<boolean>(false) // RSI 14 强弱指标

const isLoading = ref<boolean>(false)
const copied = ref<boolean>(false)
const candleCountdown = ref<string>('00:00')

let pollTimer: any = null
let tickTimer: any = null

// 调价试算平移
const simMode = ref<boolean>(false)
const simStopLoss = ref<number>(0)
const simTakeProfit = ref<number>(0)

// TradingView Lightweight Charts References
const chartContainerRef = ref<HTMLDivElement | null>(null)
let chart: IChartApi | null = null
let candleSeries: ISeriesApi<'Candlestick'> | null = null

// 指标系列实例
let volumeSeries: ISeriesApi<'Histogram'> | null = null
let volMaSeries: ISeriesApi<'Line'> | null = null
let ma5Series: ISeriesApi<'Line'> | null = null
let ma10Series: ISeriesApi<'Line'> | null = null
let ma20Series: ISeriesApi<'Line'> | null = null
let bollUbSeries: ISeriesApi<'Line'> | null = null
let bollMbSeries: ISeriesApi<'Line'> | null = null
let bollLbSeries: ISeriesApi<'Line'> | null = null

// 价格线
let entryPriceLine: IPriceLine | null = null
let slPriceLine: IPriceLine | null = null
let tpPriceLine: IPriceLine | null = null

let resizeObserver: ResizeObserver | null = null

// Raw Candle Data (150 根以上，确保 PC 端铺满视口)
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

const currentAtr = computed(() => {
  const f = currentFactor.value
  if (!f) return currentPrice.value * 0.02
  const atr = Number(f.atr1h ?? f.atr ?? 0)
  return atr > 0 ? atr : currentPrice.value * 0.02
})

const liveChangePct = computed(() => {
  if (candles.value.length < 2) return 0.0
  const first = candles.value[0]
  const last = candles.value[candles.value.length - 1]
  if (!first || !last || first.open <= 0) return 0.0
  return ((last.close - first.open) / first.open) * 100
})

// 真实开仓成本与方向
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
  if (activePosition.value || activeOrder.value) {
    return liveSide.value === 'long'
      ? liveEntry.value - currentAtr.value * 2.0
      : liveEntry.value + currentAtr.value * 2.0
  }
  return 0
})

const liveTakeProfit = computed(() => {
  if (activePosition.value) {
    const tp = Number(activePosition.value.displayTakeProfit ?? activePosition.value.tpTriggerPx ?? 0)
    if (tp > 0) return tp
  }
  if (activeOrder.value) {
    const tp = Number(activeOrder.value.tp_px ?? 0)
    if (tp > 0) return tp
  }
  if (activePosition.value || activeOrder.value) {
    return liveSide.value === 'long'
      ? liveEntry.value + currentAtr.value * 4.0
      : liveEntry.value - currentAtr.value * 4.0
  }
  return 0
})

const effectiveSL = computed(() => (simMode.value ? simStopLoss.value : liveStopLoss.value))
const effectiveTP = computed(() => (simMode.value ? simTakeProfit.value : liveTakeProfit.value))

// 科学计算真实美元金额
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

// 动态根据标的价格计算自适应价格刻度精度 (彻底解决 SUI/ASTER/DOGE 价格标签全是 0.80 的重叠Bug)
function getSymbolPrecision(sym: string, price: number): { precision: number; minMove: number } {
  const upper = sym.toUpperCase()
  if (upper.includes('BTC')) return { precision: 1, minMove: 0.1 }
  if (upper.includes('ETH') || upper.includes('SOL')) return { precision: 2, minMove: 0.01 }
  if (price >= 100) return { precision: 2, minMove: 0.01 }
  if (price >= 10) return { precision: 3, minMove: 0.005 }
  if (price >= 1) return { precision: 3, minMove: 0.001 }
  // SUI, ASTER, DOGE, ADA 等低价币种强制 4 位小数！
  return { precision: 4, minMove: 0.0001 }
}

function applyPricePrecision(price: number) {
  if (!candleSeries) return
  const { precision, minMove } = getSymbolPrecision(currentSymbol.value, price)
  const formatOptions = {
    priceFormat: {
      type: 'price' as const,
      precision,
      minMove,
    },
  }
  candleSeries.applyOptions(formatOptions)
  ma5Series?.applyOptions(formatOptions)
  ma10Series?.applyOptions(formatOptions)
  ma20Series?.applyOptions(formatOptions)
  bollUbSeries?.applyOptions(formatOptions)
  bollMbSeries?.applyOptions(formatOptions)
  bollLbSeries?.applyOptions(formatOptions)
}

// ==========================================
// 3. TradingView Lightweight Charts 极简干净初始化 (对标 OKX 官方)
// ==========================================
function initTradingViewChart() {
  if (!chartContainerRef.value) return
  if (chart) {
    chart.remove()
    chart = null
  }

  const isDark = theme.value === 'dark'
  // OKX 官方同款深邃纯粹黑灰色背景，极致干净专业
  const bgColor = isDark ? '#0C0D12' : '#FFFFFF'
  const textColor = isDark ? '#848E9C' : '#4B5563'
  // 极淡水平分割线，完全隐藏垂直网格线 (与 OKX 一样干净)
  const vertGridColor = 'transparent'
  const horzGridColor = isDark ? 'rgba(255, 255, 255, 0.03)' : 'rgba(0, 0, 0, 0.04)'
  const borderColor = isDark ? '#1E2329' : '#E5E7EB'

  const width = chartContainerRef.value.clientWidth || 800
  const height = chartContainerRef.value.clientHeight || 460

  chart = createChart(chartContainerRef.value, {
    width,
    height,
    layout: {
      background: { type: ColorType.Solid, color: bgColor },
      textColor,
      fontFamily: 'SF Pro Display, -apple-system, monospace, sans-serif',
      fontSize: 11,
    },
    grid: {
      vertLines: { color: vertGridColor },
      horzLines: { color: horzGridColor },
    },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: {
        color: isDark ? 'rgba(255, 255, 255, 0.35)' : 'rgba(0, 0, 0, 0.35)',
        width: 1,
        style: LineStyle.Dotted,
        labelBackgroundColor: isDark ? '#2B313A' : '#E2E8F0',
      },
      horzLine: {
        color: isDark ? 'rgba(255, 255, 255, 0.35)' : 'rgba(0, 0, 0, 0.35)',
        width: 1,
        style: LineStyle.Dotted,
        labelBackgroundColor: isDark ? '#2B313A' : '#E2E8F0',
      },
    },
    rightPriceScale: {
      borderColor,
      autoScale: true,
      scaleMargins: {
        top: 0.10,
        bottom: 0.20,
      },
    },
    timeScale: {
      borderColor,
      timeVisible: true,
      secondsVisible: false,
      barSpacing: 8,
      minBarSpacing: 3,
      rightOffset: 3,
    },
  })

  // 1. 宽大饱满的蜡烛图 (实体大、对比鲜明，易于视觉 LLM 提取形态)
  candleSeries = chart.addSeries(CandlestickSeries, {
    upColor: '#10B981', // 翡翠绿
    downColor: '#F43F5E', // 玫瑰红
    borderVisible: false,
    wickUpColor: '#10B981',
    wickDownColor: '#F43F5E',
  })

  // 2. 彻底修复的 VOL 成交量系列 (高对比度柱状图，视觉 LLM 极佳读取)
  volumeSeries = chart.addSeries(HistogramSeries, {
    priceFormat: {
      type: 'volume',
    },
    priceScaleId: '',
  })
  volumeSeries.priceScale().applyOptions({
    scaleMargins: {
      top: 0.80, // 占据最底部 20% 高度
      bottom: 0,
    },
  })

  // 成交量均线 VOL-MA5
  volMaSeries = chart.addSeries(LineSeries, {
    color: '#F59E0B',
    lineWidth: 1,
    priceScaleId: '',
    crosshairMarkerVisible: false,
    priceLineVisible: false,
  })

  // 3. MA 均线系列 (MA5, MA10, MA20)
  ma5Series = chart.addSeries(LineSeries, {
    color: '#F59E0B', // 金黄
    lineWidth: 1.2,
    crosshairMarkerVisible: false,
    priceLineVisible: false,
  })
  ma10Series = chart.addSeries(LineSeries, {
    color: '#38BDF8', // 天蓝
    lineWidth: 1.2,
    crosshairMarkerVisible: false,
    priceLineVisible: false,
  })
  ma20Series = chart.addSeries(LineSeries, {
    color: '#A855F7', // 紫罗兰
    lineWidth: 1.2,
    crosshairMarkerVisible: false,
    priceLineVisible: false,
  })

  // 4. BOLL 布林带系列 (UB, MB, LB)
  bollUbSeries = chart.addSeries(LineSeries, {
    color: '#FB923C', // 橙红上轨
    lineWidth: 1.2,
    crosshairMarkerVisible: false,
    priceLineVisible: false,
  })
  bollMbSeries = chart.addSeries(LineSeries, {
    color: '#34D399', // 翠绿中轨
    lineWidth: 1.2,
    crosshairMarkerVisible: false,
    priceLineVisible: false,
  })
  bollLbSeries = chart.addSeries(LineSeries, {
    color: '#FB923C', // 橙红下轨
    lineWidth: 1.2,
    crosshairMarkerVisible: false,
    priceLineVisible: false,
  })

  // 十字光标监听
  chart.subscribeCrosshairMove((param) => {
    if (!param || !param.time || !param.seriesData) {
      hoverCandle.value = null
      return
    }
    const data = param.seriesData.get(candleSeries!) as any
    if (data) {
      hoverCandle.value = {
        ts: Number(data.time) * 1000,
        open: data.open,
        high: data.high,
        low: data.low,
        close: data.close,
        vol: 0,
      }
    } else {
      hoverCandle.value = null
    }
  })

  renderChartData()
}

// 格式化并灌入数据
function renderChartData() {
  if (!candleSeries || candles.value.length === 0) return

  // 保证时间戳严格递增去重
  const map = new Map<number, Candle>()
  for (const c of candles.value) {
    const sec = Math.floor(c.ts / 1000)
    map.set(sec, c)
  }
  const sorted = Array.from(map.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([, c]) => c)

  const count = sorted.length

  // 1. 蜡烛数据
  const candleData = sorted.map((c) => ({
    time: Math.floor(c.ts / 1000) as UTCTimestamp,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }))

  // 2. VOL 成交量数据 (高对比度红绿柱)
  const volumeData = sorted.map((c) => ({
    time: Math.floor(c.ts / 1000) as UTCTimestamp,
    value: c.vol,
    color: c.close >= c.open ? 'rgba(16, 185, 129, 0.65)' : 'rgba(244, 63, 94, 0.65)',
  }))

  // 3. MA5, MA10, MA20
  const ma5Data: any[] = []
  const ma10Data: any[] = []
  const ma20Data: any[] = []
  for (let i = 0; i < count; i++) {
    const time = Math.floor(sorted[i].ts / 1000) as UTCTimestamp
    if (i >= 4) {
      let s = 0
      for (let k = 0; k < 5; k++) s += sorted[i - k].close
      ma5Data.push({ time, value: s / 5 })
    }
    if (i >= 9) {
      let s = 0
      for (let k = 0; k < 10; k++) s += sorted[i - k].close
      ma10Data.push({ time, value: s / 10 })
    }
    if (i >= 19) {
      let s = 0
      for (let k = 0; k < 20; k++) s += sorted[i - k].close
      ma20Data.push({ time, value: s / 20 })
    }
  }

  // 4. VOL-MA5
  const volMaData: any[] = []
  for (let i = 4; i < count; i++) {
    let sumV = 0
    for (let k = 0; k < 5; k++) sumV += sorted[i - k].vol
    volMaData.push({
      time: Math.floor(sorted[i].ts / 1000) as UTCTimestamp,
      value: sumV / 5,
    })
  }

  // 5. BOLL (20, 2)
  const bollUbData: any[] = []
  const bollMbData: any[] = []
  const bollLbData: any[] = []
  for (let i = 19; i < count; i++) {
    const time = Math.floor(sorted[i].ts / 1000) as UTCTimestamp
    let sum = 0
    for (let k = 0; k < 20; k++) sum += sorted[i - k].close
    const mean = sum / 20
    let variance = 0
    for (let k = 0; k < 20; k++) variance += Math.pow(sorted[i - k].close - mean, 2)
    const std = Math.sqrt(variance / 20)
    bollMbData.push({ time, value: mean })
    bollUbData.push({ time, value: mean + 2 * std })
    bollLbData.push({ time, value: mean - 2 * std })
  }

  // 动态应用标的价格精度，防止低价币所有指标与刻度四舍五入重叠
  applyPricePrecision(currentPrice.value)

  // 批量应用与可见性控制 (支持多指标独立共存)
  candleSeries.setData(candleData)

  if (volumeSeries) {
    volumeSeries.applyOptions({ visible: indVOL.value })
    if (indVOL.value) volumeSeries.setData(volumeData)
  }
  if (volMaSeries) {
    volMaSeries.applyOptions({ visible: indVOL.value })
    if (indVOL.value) volMaSeries.setData(volMaData)
  }

  if (ma5Series) {
    ma5Series.applyOptions({ visible: indMA.value })
    if (indMA.value) ma5Series.setData(ma5Data)
  }
  if (ma10Series) {
    ma10Series.applyOptions({ visible: indMA.value })
    if (indMA.value) ma10Series.setData(ma10Data)
  }
  if (ma20Series) {
    ma20Series.applyOptions({ visible: indMA.value })
    if (indMA.value) ma20Series.setData(ma20Data)
  }

  if (bollUbSeries && bollMbSeries && bollLbSeries) {
    bollUbSeries.applyOptions({ visible: indBOLL.value })
    bollMbSeries.applyOptions({ visible: indBOLL.value })
    bollLbSeries.applyOptions({ visible: indBOLL.value })
    if (indBOLL.value) {
      bollUbSeries.setData(bollUbData)
      bollMbSeries.setData(bollMbData)
      bollLbSeries.setData(bollLbData)
    }
  }

  updateTradingPriceLines()
  // 紧贴右侧
  chart?.timeScale().scrollToRealtime()
}

function clearTradingPriceLines() {
  if (!candleSeries) return
  if (entryPriceLine) {
    try { candleSeries.removePriceLine(entryPriceLine) } catch {}
    entryPriceLine = null
  }
  if (slPriceLine) {
    try { candleSeries.removePriceLine(slPriceLine) } catch {}
    slPriceLine = null
  }
  if (tpPriceLine) {
    try { candleSeries.removePriceLine(tpPriceLine) } catch {}
    tpPriceLine = null
  }
}

// 刷新原生价格线
function updateTradingPriceLines() {
  if (!candleSeries) return
  clearTradingPriceLines()

  if (activePosition.value || activeOrder.value) {
    const entryPx = liveEntry.value
    if (entryPx > 0) {
      entryPriceLine = candleSeries.createPriceLine({
        price: entryPx,
        color: liveSide.value === 'long' ? '#10B981' : '#F43F5E',
        lineWidth: 1,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: liveSide.value === 'long' ? (isEn ? 'Entry Long' : '多头入场') : (isEn ? 'Entry Short' : '空头入场'),
      })
    }
  }

  const slPx = effectiveSL.value
  if (slPx > 0) {
    slPriceLine = candleSeries.createPriceLine({
      price: slPx,
      color: '#F43F5E',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: `🛑 ${isEn ? 'SL' : '止损SL'} -${riskRewardMetrics.value.riskPct.toFixed(1)}%`,
    })
  }

  const tpPx = effectiveTP.value
  if (tpPx > 0) {
    tpPriceLine = candleSeries.createPriceLine({
      price: tpPx,
      color: '#10B981',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: `🎯 ${isEn ? 'TP' : '止盈TP'} +${riskRewardMetrics.value.rewardPct.toFixed(1)}%`,
    })
  }
}

// 倒计时
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

// 毫秒级 Tick 更新最后一根 K 线
function updateLiveTick() {
  updateCountdown()
  if (!candleSeries || candles.value.length === 0) return
  const last = candles.value[candles.value.length - 1]
  const px = currentPrice.value
  if (px > 0 && last) {
    last.close = px
    last.high = Math.max(last.high, px)
    last.low = Math.min(last.low, px)
    candleSeries.update({
      time: Math.floor(last.ts / 1000) as UTCTimestamp,
      open: last.open,
      high: last.high,
      low: last.low,
      close: last.close,
    })
  }
}

// 拉取 150 根行情数据 (彻底填满 PC 端视口，支持 3s 静默增量对齐)
async function loadCandles(silent = false, resetScale = false) {
  if (!silent) isLoading.value = true
  try {
    const res = await fetch(
      `/api/v1/market/${currentInstId.value}/candles?bar=${currentPeriod.value}&limit=150`
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    if (Array.isArray(data.candles) && data.candles.length > 0) {
      candles.value = data.candles
      renderChartData()
      if (resetScale && chart) {
        chart.timeScale().scrollToRealtime()
      }
    }
  } catch (err) {
    console.warn('Candles fetch fallback:', err)
  } finally {
    if (!silent) isLoading.value = false
  }
}

function selectSymbol(s: string) {
  const sym = s.toUpperCase()
  if (sym === currentSymbol.value) return
  currentSymbol.value = sym
  emit('select-symbol', sym)

  // 1. 立即清除旧币种残留的价格线，防止旧币种的万刀价格污染新标的坐标轴！
  clearTradingPriceLines()
  // 2. 清空当前 Series 数据，杜绝残存跨度
  candleSeries?.setData([])
  volumeSeries?.setData([])
  volMaSeries?.setData([])
  ma5Series?.setData([])
  ma10Series?.setData([])
  ma20Series?.setData([])
  bollUbSeries?.setData([])
  bollMbSeries?.setData([])
  bollLbSeries?.setData([])

  // 3. 重置时间轴
  chart?.timeScale().resetTimeScale()

  // 4. 加载新标的蜡烛并重塑缩放
  loadCandles(false, true)
}

// 调价
function adjustSL(deltaPercent: number) {
  if (!simMode.value) simMode.value = true
  const cur = simStopLoss.value || liveStopLoss.value
  const step = cur * deltaPercent
  simStopLoss.value = Number((cur + step).toFixed(4))
  updateTradingPriceLines()
}

function adjustTP(deltaPercent: number) {
  if (!simMode.value) simMode.value = true
  const cur = simTakeProfit.value || liveTakeProfit.value
  const step = cur * deltaPercent
  simTakeProfit.value = Number((cur + step).toFixed(4))
  updateTradingPriceLines()
}

function initSimulation() {
  simStopLoss.value = Number(liveStopLoss.value.toFixed(4))
  simTakeProfit.value = Number(liveTakeProfit.value.toFixed(4))
  updateTradingPriceLines()
}

function resetSimulation() {
  initSimulation()
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

function autoSelectFirstActiveSymbol() {
  if (props.initialSymbol && availableSymbols.value.includes(props.initialSymbol.toUpperCase())) {
    currentSymbol.value = props.initialSymbol.toUpperCase()
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

watch(() => props.initialSymbol, (val) => {
  if (val && val.toUpperCase() !== currentSymbol.value && availableSymbols.value.includes(val.toUpperCase())) {
    selectSymbol(val.toUpperCase())
  }
})

watch(availableSymbols, (symbols) => {
  if (symbols.length > 0 && !symbols.includes(currentSymbol.value)) {
    currentSymbol.value = symbols[0]
    loadCandles()
  }
})

watch(() => theme.value, () => {
  nextTick(() => {
    initTradingViewChart()
  })
})

// 多指标共存监听
watch([indMA, indBOLL, indVOL], () => {
  renderChartData()
})

onMounted(() => {
  autoSelectFirstActiveSymbol()
  nextTick(() => {
    initTradingViewChart()
    loadCandles()

    if (chartContainerRef.value) {
      resizeObserver = new ResizeObserver((entries) => {
        if (!entries || entries.length === 0) return
        const { width, height } = entries[0].contentRect
        if (chart && width > 0 && height > 0) {
          chart.applyOptions({ width, height })
        }
      })
      resizeObserver.observe(chartContainerRef.value)
    }
  })

  // 3秒静默轮询 + 1秒盘口跳动
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
  if (chart) {
    chart.remove()
    chart = null
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
    <!-- Top Bar: 动态自动读取系统标的 + 周期切换 (OKX 极简干净风) -->
    <div
      class="px-3 py-2 sm:px-4 sm:py-2.5 border-b flex flex-wrap items-center justify-between gap-2 text-xs font-mono"
      style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);"
    >
      <!-- 动态标的横滑栏 (100% 自动读取系统真实设置标的) -->
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
          <span
            v-if="store.positions.some((p: any) => p.name?.toUpperCase() === sym || p.instId?.toUpperCase().startsWith(sym))"
            class="w-1.5 h-1.5 rounded-full"
            :class="store.positions.find((p: any) => p.name?.toUpperCase() === sym || p.instId?.toUpperCase().startsWith(sym))?.side === 'long' ? 'bg-emerald-500' : 'bg-rose-500'"
            title="持有活动持仓"
          ></span>
          <span>{{ sym }}</span>
        </button>
      </div>

      <!-- 周期与多指标独立开关 (支持同时开启，为未来视觉LLM形态推演打造) -->
      <div class="flex items-center space-x-1.5 shrink-0">
        <!-- 周期切换 -->
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

        <!-- 多指标独立开关胶囊 (可同时开启共存) -->
        <div class="flex items-center space-x-1 p-0.5 rounded-lg border text-[11px] font-mono font-bold" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <button
            @click="indMA = !indMA"
            class="h-6 px-2 rounded transition-colors cursor-pointer"
            :style="indMA
              ? { backgroundColor: 'var(--bg-badge)', color: '#F59E0B' }
              : { color: 'var(--text-faint)' }"
            title="主图移动平均线 MA5/MA10/MA20"
          >
            MA
          </button>
          <button
            @click="indBOLL = !indBOLL"
            class="h-6 px-2 rounded transition-colors cursor-pointer"
            :style="indBOLL
              ? { backgroundColor: 'var(--bg-badge)', color: '#FB923C' }
              : { color: 'var(--text-faint)' }"
            title="主图布林带 BOLL(20,2)"
          >
            BOLL
          </button>
          <button
            @click="indVOL = !indVOL"
            class="h-6 px-2 rounded transition-colors cursor-pointer"
            :style="indVOL
              ? { backgroundColor: 'var(--bg-badge)', color: '#10B981' }
              : { color: 'var(--text-faint)' }"
            title="副图成交量 VOL (放量/缩量高对比度柱)"
          >
            VOL
          </button>
        </div>

        <!-- 调价平移试算 -->
        <button
          @click="simMode = !simMode; if (simMode) initSimulation(); updateTradingPriceLines()"
          class="h-6.5 px-2 rounded-lg text-xs font-mono border flex items-center space-x-1 transition-all cursor-pointer font-bold"
          :style="simMode
            ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--color-brand)' }
            : { backgroundColor: 'var(--bg-card)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
          :title="simMode ? '退出试算' : '平移试算 R:R'"
        >
          <Sliders class="w-3 h-3" />
          <span class="hidden sm:inline">{{ simMode ? '退出' : '试算' }}</span>
        </button>

        <button
          @click="loadCandles()"
          class="h-6.5 w-6.5 rounded-lg border flex items-center justify-center transition-colors cursor-pointer"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          title="刷新行情"
        >
          <RefreshCw class="w-3 h-3" :class="isLoading ? 'animate-spin' : ''" />
        </button>
      </div>
    </div>

    <!-- Ticker Info Bar: 实时价格、涨跌、ATR 与倒计时 -->
    <div
      class="px-3 py-1.5 sm:px-4 sm:py-2 border-b flex flex-wrap items-center justify-between text-[11px] font-mono gap-2"
      style="border-color: var(--border-subtle); background-color: var(--bg-app);"
    >
      <div class="flex items-center space-x-2.5">
        <span class="font-black text-xs sm:text-sm" style="color: var(--text-main);">{{ currentSymbol }}USDT {{ t('chart.swapPerp', '永续') }}</span>
        <span class="font-black text-xs sm:text-sm num-tabular" style="color: var(--text-main);">
          ${{ currentPrice >= 100 ? currentPrice.toFixed(1) : currentPrice.toFixed(4) }}
        </span>
        <span :class="liveChangePct >= 0 ? 'text-emerald-400' : 'text-rose-400'" class="text-[10px] font-bold">
          {{ liveChangePct >= 0 ? '+' : '' }}{{ liveChangePct.toFixed(2) }}%
        </span>
        <span class="flex items-center space-x-1 pl-1">
          <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
          <span class="text-[9px] text-emerald-400 font-bold">{{ t('chart.liveStatus', '实时') }} 3s</span>
        </span>
        <span class="text-[10px]" style="color: var(--text-faint);">1H ATR: ${{ currentAtr.toFixed(1) }}</span>
      </div>

      <!-- Hover OHLC 读数 或 周期倒计时 -->
      <div v-if="hoverCandle" class="flex items-center space-x-2 text-[10px] num-tabular" style="color: var(--text-muted);">
        <span>O: <strong style="color: var(--text-main);">${{ hoverCandle.open.toFixed(1) }}</strong></span>
        <span>H: <strong class="text-emerald-400">${{ hoverCandle.high.toFixed(1) }}</strong></span>
        <span>L: <strong class="text-rose-400">${{ hoverCandle.low.toFixed(1) }}</strong></span>
        <span>C: <strong style="color: var(--text-main);">${{ hoverCandle.close.toFixed(1) }}</strong></span>
      </div>
      <div v-else class="flex items-center space-x-2 text-[10px] font-mono" style="color: var(--text-muted);">
        <span>{{ t('chart.countdownLabel', 'K线结线倒计时') }}:</span>
        <span class="font-bold text-amber-400 num-tabular">{{ candleCountdown }}</span>
      </div>
    </div>

    <!-- TradingView Lightweight Chart 容器 (OKX 极简干净风格，蜡烛实体大，PC填满) -->
    <div
      ref="chartContainerRef"
      class="relative w-full h-[420px] sm:h-[480px] 2xl:h-[540px] select-none"
      style="background-color: var(--bg-card);"
    >
      <!-- Top Left Active Indicators Legend (彻底去除阴影遮罩与深色背景，亮暗双模纯净无遮挡) -->
      <div class="absolute top-2 left-3 flex flex-wrap items-center gap-3 text-[10px] font-mono pointer-events-none z-10 select-none">
        <span v-if="indMA" class="flex items-center space-x-1.5">
          <span class="text-amber-500 font-bold">MA5</span>
          <span class="text-sky-500 font-bold">MA10</span>
          <span class="text-purple-500 font-bold">MA20</span>
        </span>
        <span v-if="indBOLL" class="flex items-center space-x-1.5">
          <span class="text-emerald-500 font-bold">BOLL(20,2)</span>
        </span>
        <span v-if="indVOL" class="flex items-center space-x-1.5">
          <span class="text-emerald-500 font-bold">VOL</span>
          <span class="text-amber-500 font-bold">MA5</span>
        </span>
      </div>
    </div>

    <!-- Bottom Interactive Risk / Reward (R:R) Simulator Console (真实科学金额) -->
    <div
      class="p-3 sm:p-4 border-t transition-colors"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
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
              {{ riskRewardMetrics.isRrCompliant ? (isEn ? '✅ Expected R:R' : '✅ 期望盈亏比 (R:R)') : (isEn ? '⚠️ Low R:R (<2.0)' : '⚠️ 盈亏比不足 2.0') }}
            </span>
            <div class="flex items-baseline space-x-1 mt-0.5">
              <span class="text-base sm:text-lg font-black num-tabular" :style="{ color: riskRewardMetrics.isRrCompliant ? 'var(--color-up)' : 'var(--color-warn)' }">
                {{ riskRewardMetrics.rrRatio.toFixed(2) }} : 1
              </span>
              <span class="text-[9px] opacity-70" :style="{ color: riskRewardMetrics.isRrCompliant ? 'var(--color-up)' : 'var(--color-warn)' }">
                {{ isEn ? 'Min 2.0' : '底线 2.0' }}
              </span>
            </div>
          </div>

          <!-- ATR 呼吸空间 -->
          <div
            class="p-2.5 rounded-lg border font-mono flex flex-col justify-between"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <span class="text-[10px] uppercase font-bold" style="color: var(--text-muted);">
              {{ isEn ? 'SL Buffer (ATR)' : '止损呼吸空间 (ATR)' }}
            </span>
            <div class="flex items-baseline space-x-1 mt-0.5">
              <span
                class="text-base sm:text-lg font-black num-tabular"
                :style="{ color: riskRewardMetrics.isAtrOptimal ? 'var(--color-brand)' : 'var(--color-warn)' }"
              >
                {{ riskRewardMetrics.atrMultiple.toFixed(2) }}x
              </span>
              <span class="text-[9px] font-bold" :style="{ color: riskRewardMetrics.isAtrOptimal ? 'var(--color-brand)' : 'var(--color-warn)' }">
                {{ riskRewardMetrics.isAtrOptimal ? (isEn ? 'Noise Buffer' : '防插针区间') : (isEn ? 'Deviates 1.8~2.2' : '偏离1.8~2.2') }}
              </span>
            </div>
          </div>

          <!-- 真实预期收益金额 -->
          <div
            class="p-2.5 rounded-lg border font-mono flex flex-col justify-between"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <span class="text-[10px] uppercase font-bold text-emerald-500">
              {{ isEn ? 'Target Profit (TP)' : '预期收益目标 (TP)' }}
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
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <span class="text-[10px] uppercase font-bold text-rose-500">
              {{ isEn ? 'Max Risk (SL)' : '最大硬风控风险 (SL)' }}
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
              :title="isEn ? 'Reset' : '重置点位'"
            >
              <RotateCcw class="w-3 h-3" />
              <span>{{ isEn ? 'Reset' : '复位' }}</span>
            </button>
          </div>

          <button
            @click="copySimulationSummary"
            class="btn-admin-secondary text-xs font-mono flex items-center space-x-1.5"
            :title="isEn ? 'Copy risk metrics' : '复制风控测算契约'"
          >
            <Check v-if="copied" class="w-3.5 h-3.5 text-emerald-400" />
            <Copy v-else class="w-3.5 h-3.5" />
            <span>{{ copied ? (isEn ? 'Copied' : '已复制') : (isEn ? 'Copy Params' : '复制风控参数') }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

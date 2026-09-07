<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { useTheme } from '../composables/useTheme'
import { useI18n } from '../composables/useI18n'
import {
  init as initKLineChart,
  dispose as disposeKLineChart,
  registerIndicator,
  type Chart as KLineChartType,
  type KLineData,
  type DeepPartial,
  type Styles,
} from 'klinecharts'
import {
  Sliders,
  RefreshCw,
  Copy,
  Check,
  RotateCcw,
  Zap,
  TrendingUp,
  SlidersHorizontal,
  ChevronDown,
} from 'lucide-vue-next'

// ==========================================
// 0. 注册原生 VWAP 指标 (基于成交量加权平均价)
// ==========================================
registerIndicator({
  name: 'VWAP',
  shortName: 'VWAP',
  series: 'price',
  precision: 2,
  figures: [{ key: 'vwap', title: 'VWAP: ', type: 'line' }],
  styles: {
    lines: [{ style: 'solid', smooth: false, size: 1.5, color: '#06B6D4' }], // 青蓝色
  },
  calc: (dataList: KLineData[]) => {
    let cumTypicalVol = 0
    let cumVol = 0
    let lastDay = -1

    return dataList.map((kLine) => {
      const d = new Date(kLine.timestamp)
      const day = d.getUTCDate()
      // 每天重置或者连续累计
      if (lastDay !== -1 && day !== lastDay) {
        cumTypicalVol = 0
        cumVol = 0
      }
      lastDay = day

      const typicalPrice = (kLine.high + kLine.low + kLine.close) / 3
      const vol = Number(kLine.volume || 0)
      cumTypicalVol += typicalPrice * vol
      cumVol += vol

      return {
        vwap: cumVol > 0 ? cumTypicalVol / cumVol : typicalPrice,
      }
    })
  },
})

const props = defineProps<{
  symbol?: string
  initialSymbol?: string
  fullscreen?: boolean
}>()

const emit = defineEmits<{
  (e: 'select-symbol', symbol: string): void
  (e: 'toggle-fullscreen'): void
}>()

const store = useDashboardStore()
const { theme } = useTheme()
const isDark = computed(() => theme.value === 'dark')
const { t, isEn } = useI18n()

// ==========================================
// 1. 标的池与周期切换 (动态读取系统监控池)
// ==========================================
const availableSymbols = computed(() => {
  const holdingSet = new Set<string>()
  const otherSet = new Set<string>()

  // 1. 优先提取当前持仓标的 (去重)
  if (Array.isArray(store.positions)) {
    store.positions.forEach((p) => {
      const sym = (p.name || p.instId?.replace('-USDT-SWAP', '').replace('-USDT', '') || '').toUpperCase()
      if (sym) holdingSet.add(sym)
    })
  }

  // 2. 优先提取挂单标的 (去重)
  if (Array.isArray(store.pendingOrders)) {
    store.pendingOrders.forEach((o) => {
      const sym = (o.name || o.instId?.replace('-USDT-SWAP', '').replace('-USDT', '') || '').toUpperCase()
      if (sym) holdingSet.add(sym)
    })
  }

  // 3. 提取全部监控池标的
  if (Array.isArray(store.factorLibrary)) {
    store.factorLibrary.forEach((f) => {
      const sym = (f.instId?.replace('-USDT-SWAP', '').replace('-USDT', '') || '').toUpperCase()
      if (sym && !holdingSet.has(sym)) {
        otherSet.add(sym)
      }
    })
  }

  // 兜底标的
  const defaults = ['BTC', 'ETH', 'SOL', 'DOGE', 'SUI', 'ADA']
  defaults.forEach((d) => {
    if (!holdingSet.has(d) && !otherSet.has(d)) {
      otherSet.add(d)
    }
  })

  // 持仓/挂单标的绝对排在最前面，其余监控标的紧随其后全部保留！
  return [...Array.from(holdingSet), ...Array.from(otherSet)]
})

const periods = computed(() => [
  { id: '15m', label: t('chart.timeframe15m', '15分'), span: 15, type: 'minute' as const },
  { id: '1H', label: t('chart.timeframe1h', '1时'), span: 1, type: 'hour' as const },
  { id: '4H', label: t('chart.timeframe4h', '4时'), span: 4, type: 'hour' as const },
  { id: '1D', label: t('chart.timeframe1d', '1日'), span: 1, type: 'day' as const },
])

const currentSymbol = ref<string>('BTC')
const currentPeriod = ref<string>('1H')
const isLoading = ref<boolean>(false)
const chartContainer = ref<HTMLElement | null>(null)

// ==========================================
// 2. 指标配置中心 (主图与副图严密区分)
// ==========================================
const showIndicatorMenu = ref<boolean>(false)

// 主图叠加指标 (Overlay on Main Candle Pane)
interface IndicatorOption {
  key: string
  name: string
  label: string
  desc: string
  color: string
  defaultParams?: any[]
  isSub: boolean
}

const mainIndicators: IndicatorOption[] = [
  { key: 'VWAP', name: 'VWAP', label: 'VWAP', desc: '成交量加权均价线', color: '#06B6D4', isSub: false },
  { key: 'MA', name: 'MA', label: 'MA', desc: '均线 (5, 10, 20)', color: '#F59E0B', defaultParams: [5, 10, 20], isSub: false },
  { key: 'EMA', name: 'EMA', label: 'EMA', desc: '指数均线 (12, 26, 50)', color: '#38BDF8', defaultParams: [12, 26, 50], isSub: false },
  { key: 'BOLL', name: 'BOLL', label: 'BOLL', desc: '布林带轨道 (20, 2)', color: '#818CF8', defaultParams: [20, 2], isSub: false },
  { key: 'SAR', name: 'SAR', label: 'SAR', desc: '抛物线转向', color: '#EC4899', isSub: false },
  { key: 'BBI', name: 'BBI', label: 'BBI', desc: '多空多均线综合', color: '#10B981', isSub: false },
]

// 副图独立窗格指标 (Sub Panes)
const subIndicators: IndicatorOption[] = [
  { key: 'VOL', name: 'VOL', label: 'VOL', desc: '成交量与柱形量能', color: '#10B981', isSub: true },
  { key: 'MACD', name: 'MACD', label: 'MACD', desc: '异同移动平均线', color: '#3B82F6', defaultParams: [12, 26, 9], isSub: true },
  { key: 'RSI', name: 'RSI', label: 'RSI', desc: '相对强弱动量 (6, 12, 24)', color: '#F97316', defaultParams: [6, 12, 24], isSub: true },
  { key: 'KDJ', name: 'KDJ', label: 'KDJ', desc: '随机摆动指标 (9, 3, 3)', color: '#A855F7', defaultParams: [9, 3, 3], isSub: true },
  { key: 'OBV', name: 'OBV', label: 'OBV', desc: '能量潮累积线', color: '#EAB308', isSub: true },
  { key: 'WR', name: 'WR', label: 'WR', desc: '威廉超买超卖 (14)', color: '#6366F1', defaultParams: [14], isSub: true },
]

// 默认激活指标：默认开启 VOL 与 VWAP
const activeIndicators = ref<Record<string, boolean>>({
  VWAP: true,
  VOL: true,
  MA: false,
  EMA: false,
  BOLL: false,
  SAR: false,
  BBI: false,
  MACD: false,
  RSI: false,
  KDJ: false,
  OBV: false,
  WR: false,
})

// 记录已挂载的指标 Pane ID，以便精准开关
const mountedPanes = new Map<string, string>()

// 当前标的计算
const currentInstId = computed(() => `${currentSymbol.value}-USDT-SWAP`)
const factorItem = computed(() => {
  if (!Array.isArray(store.factorLibrary)) return undefined
  return store.factorLibrary.find(
    (f) =>
      f.instId === currentInstId.value ||
      f.instId === `${currentSymbol.value}-USDT` ||
      f.instId?.startsWith(currentSymbol.value)
  )
})

const currentAtr = computed(() => Number(factorItem.value?.atr1h || 0))
// 涨跌幅：当前蜡烛价格相比其开盘价的实时变化百分比
const liveChangePct = computed(() => {
  if (candles.value.length > 0) {
    const last = candles.value[candles.value.length - 1]
    if (last && last.open > 0) {
      return ((currentPrice.value - last.open) / last.open) * 100
    }
  }
  return Number(factorItem.value?.c_1h_ret || 0) * 100
})

// 实盘在手持仓与在途委托
const activePosition = computed(() => {
  if (!Array.isArray(store.positions)) return undefined
  const target = currentSymbol.value.toUpperCase()
  return store.positions.find((p) => {
    const sym = (p.name || p.instId?.replace('-USDT-SWAP', '').replace('-USDT', '') || '').toUpperCase()
    return sym === target || p.instId === currentInstId.value
  })
})
const activeOrder = computed(() => {
  if (!Array.isArray(store.pendingOrders)) return undefined
  const target = currentSymbol.value.toUpperCase()
  return store.pendingOrders.find((o) => {
    const sym = (o.name || o.inst || o.instId?.replace('-USDT-SWAP', '').replace('-USDT', '') || '').toUpperCase()
    return sym === target || o.instId === currentInstId.value
  })
})

// 真实最新价格 (纯从当前已加载的实时蜡烛最后一根获取，与K线和最新Tick 100% 同源)
const currentPrice = ref<number>(0)

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
    const s = Number(
      activePosition.value.displayStop ??
      activePosition.value.exchangeSl ??
      activePosition.value.slTriggerPx ??
      activePosition.value.trailingSl ??
      0
    )
    if (s > 0) return s
  }
  if (activeOrder.value) {
    const s = Number(activeOrder.value.sl_px ?? 0)
    if (s > 0) return s
  }
  return 0
})

const liveTakeProfit = computed(() => {
  if (activePosition.value) {
    const tp = Number(
      activePosition.value.displayTakeProfit ??
      activePosition.value.exchangeTp ??
      activePosition.value.tpTriggerPx ??
      0
    )
    if (tp > 0) return tp
  }
  if (activeOrder.value) {
    const tp = Number(activeOrder.value.tp_px ?? 0)
    if (tp > 0) return tp
  }
  return 0
})

// ==========================================
// 3. 调价试算控制器 (Sim Mode)
// ==========================================
const simMode = ref<boolean>(false)
const simEntryPrice = ref<number>(0)
const simSL = ref<number>(0)
const simTP = ref<number>(0)
const copied = ref<boolean>(false)

const effectiveEntry = computed(() => simMode.value ? simEntryPrice.value : liveEntry.value)
const effectiveSL = computed(() => simMode.value ? simSL.value : liveStopLoss.value)
const effectiveTP = computed(() => simMode.value ? simTP.value : liveTakeProfit.value)

function initSimulation() {
  const px = currentPrice.value
  const atr = currentAtr.value > 0 ? currentAtr.value : px * 0.015
  simEntryPrice.value = px
  if (liveSide.value === 'long') {
    simSL.value = Math.max(0, px - atr * 2.0)
    simTP.value = px + atr * 4.4
  } else {
    simSL.value = px + atr * 2.0
    simTP.value = Math.max(0, px - atr * 4.4)
  }
}

function resetSimulation() {
  initSimulation()
  updatePriceLines()
}

// 真实数学风控测算模型
const riskRewardMetrics = computed(() => {
  const entry = effectiveEntry.value
  const sl = effectiveSL.value
  const tp = effectiveTP.value
  const side = liveSide.value
  const atr = currentAtr.value

  let riskDist = 0
  let rewardDist = 0

  if (side === 'long') {
    riskDist = Math.max(0, entry - sl)
    rewardDist = Math.max(0, tp - entry)
  } else {
    riskDist = Math.max(0, sl - entry)
    rewardDist = Math.max(0, entry - tp)
  }

  const riskPct = entry > 0 ? (riskDist / entry) * 100 : 0
  const rewardPct = entry > 0 ? (rewardDist / entry) * 100 : 0
  const rrRatio = riskDist > 0 ? rewardDist / riskDist : 0
  const atrMultiple = atr > 0 ? riskDist / atr : 0

  const isRrCompliant = rrRatio >= 2.0
  const isAtrOptimal = atrMultiple >= 1.8 && atrMultiple <= 2.2

  const hasRealPosition = !!activePosition.value
  let activeMargin = 100.0
  let activeLeverage = 3.0

  if (hasRealPosition && activePosition.value) {
    const rawMargin = Number(activePosition.value.margin_usdt ?? activePosition.value.margin ?? 0)
    if (rawMargin > 0) activeMargin = rawMargin
    const rawLever = Number(activePosition.value.lever ?? 3)
    if (rawLever > 0) activeLeverage = rawLever
  }

  const estProfitUsd = activeMargin * activeLeverage * (rewardPct / 100)
  const estRiskUsd = activeMargin * activeLeverage * (riskPct / 100)

  return {
    riskDist,
    rewardDist,
    riskPct,
    rewardPct,
    rrRatio,
    atrMultiple,
    isRrCompliant,
    isAtrOptimal,
    hasRealPosition,
    estProfitUsd,
    estRiskUsd,
  }
})

// 价格精度自适应
function getSymbolPrecision(sym: string, price: number): number {
  const upper = sym.toUpperCase()
  if (upper.includes('BTC')) return 1
  if (upper.includes('ETH') || upper.includes('SOL')) return 2
  if (price >= 100) return 2
  if (price >= 10) return 3
  if (price >= 1) return 3
  return 4
}

// ==========================================
// 4. KLineChart 引擎核心初始化与生命周期
// ==========================================
let klineChart: KLineChartType | null = null
const candles = ref<Array<any>>([])
const candleCountdown = ref<string>('00:00')

// 绘制的价格线 ID 记录
let entryOverlayId: string | null = null
let slOverlayId: string | null = null
let tpOverlayId: string | null = null

function getChartStyles(): DeepPartial<Styles> {
  const dark = isDark.value
  return {
    grid: {
      show: true,
      horizontal: {
        show: true,
        size: 1,
        color: dark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)',
        style: 'solid',
      },
      vertical: {
        show: false, // 隐藏垂直杂乱网格
      },
    },
    candle: {
      type: 'candle_solid',
      bar: {
        upColor: '#10B981',
        downColor: '#F43F5E',
        noChangeColor: '#888888',
        upBorderColor: '#10B981',
        downBorderColor: '#F43F5E',
        noChangeBorderColor: '#888888',
        upWickColor: '#10B981',
        downWickColor: '#F43F5E',
        noChangeWickColor: '#888888',
      },
      priceMark: {
        show: true,
        high: {
          show: true,
          color: dark ? '#CBD5E1' : '#475569',
          textOffset: 4,
          textSize: 10,
        },
        low: {
          show: true,
          color: dark ? '#CBD5E1' : '#475569',
          textOffset: 4,
          textSize: 10,
        },
        last: {
          show: true,
          upColor: '#10B981',
          downColor: '#F43F5E',
          noChangeColor: '#888888',
          line: {
            show: true,
            style: 'dashed',
            dashedValue: [4, 4],
            size: 1,
          },
          text: {
            show: true,
            size: 11,
            paddingLeft: 4,
            paddingTop: 2,
            paddingRight: 4,
            paddingBottom: 2,
            color: '#FFFFFF',
          },
        },
      },
      tooltip: {
        showRule: 'always',
        showType: 'standard',
        text: {
          size: 11,
          family: 'JetBrains Mono, monospace',
          color: dark ? '#94A3B8' : '#64748B',
        },
      },
    },
    indicator: {
      ohlc: {
        upColor: '#10B981',
        downColor: '#F43F5E',
        noChangeColor: '#888888',
      },
      lines: [
        { style: 'solid', smooth: false, size: 1.5, color: '#F59E0B' }, // MA5 / 黄
        { style: 'solid', smooth: false, size: 1.5, color: '#38BDF8' }, // MA10 / 蓝
        { style: 'solid', smooth: false, size: 1.5, color: '#A855F7' }, // MA20 / 紫
        { style: 'solid', smooth: false, size: 1.5, color: '#F43F5E' },
        { style: 'solid', smooth: false, size: 1.5, color: '#10B981' },
      ],
      lastValueMark: {
        show: true,
        text: {
          show: true,
          size: 10,
          paddingLeft: 3,
          paddingTop: 1,
          paddingRight: 3,
          paddingBottom: 1,
          color: '#FFFFFF',
        },
      },
    },
    xAxis: {
      show: true,
      size: 'auto',
      axisLine: {
        show: true,
        color: dark ? '#1E293B' : '#E2E8F0',
        size: 1,
      },
      tickText: {
        show: true,
        color: dark ? '#64748B' : '#94A3B8',
        family: 'JetBrains Mono, monospace',
        size: 10,
      },
      tickLine: {
        show: true,
        size: 1,
        length: 3,
        color: dark ? '#1E293B' : '#E2E8F0',
      },
    },
    yAxis: {
      show: true,
      size: 'auto',
      position: 'right',
      type: 'normal',
      inside: false,
      axisLine: {
        show: true,
        color: dark ? '#1E293B' : '#E2E8F0',
        size: 1,
      },
      tickText: {
        show: true,
        color: dark ? '#94A3B8' : '#64748B',
        family: 'JetBrains Mono, monospace',
        size: 11,
      },
      tickLine: {
        show: false,
      },
    },
    separator: {
      size: 1,
      color: dark ? '#1E293B' : '#E2E8F0',
      fill: true,
      activeBackgroundColor: dark ? '#334155' : '#CBD5E1',
    },
    crosshair: {
      show: true,
      horizontal: {
        show: true,
        line: {
          style: 'dashed',
          dashedValue: [4, 4],
          size: 1,
          color: dark ? '#64748B' : '#94A3B8',
        },
        text: {
          show: true,
          color: '#FFFFFF',
          size: 11,
          family: 'JetBrains Mono, monospace',
          backgroundColor: '#3B82F6',
        },
      },
      vertical: {
        show: true,
        line: {
          style: 'dashed',
          dashedValue: [4, 4],
          size: 1,
          color: dark ? '#64748B' : '#94A3B8',
        },
        text: {
          show: true,
          color: '#FFFFFF',
          size: 10,
          family: 'JetBrains Mono, monospace',
          backgroundColor: '#475569',
        },
      },
    },
  }
}

// 统一根据 activeIndicators 渲染与挂载指标
function syncIndicators() {
  if (!klineChart) return

  // 1. 同步主图指标 (全部挂在 candle_pane 上，isStack = true 保证多指标自由叠加共存！)
  mainIndicators.forEach((ind) => {
    const isActive = !!activeIndicators.value[ind.key]
    const currentOnChart = klineChart?.getIndicators({ id: `main_${ind.key}` }) || []
    if (isActive) {
      if (currentOnChart.length === 0) {
        klineChart?.createIndicator(
          {
            id: `main_${ind.key}`,
            name: ind.name,
            paneId: 'candle_pane',
            calcParams: ind.defaultParams || [],
          },
          true // 必须为 true，允许多个主图指标同时叠加同屏渲染！
        )
      }
    } else {
      if (currentOnChart.length > 0) {
        klineChart?.removeIndicator({ id: `main_${ind.key}`, paneId: 'candle_pane' })
      }
    }
  })

  // 2. 同步副图指标 (创建独立 Pane)
  subIndicators.forEach((ind) => {
    const isActive = !!activeIndicators.value[ind.key]
    const currentOnChart = klineChart?.getIndicators({ id: `sub_${ind.key}` }) || []
    if (isActive) {
      if (currentOnChart.length === 0) {
        klineChart?.createIndicator(
          {
            id: `sub_${ind.key}`,
            name: ind.name,
            calcParams: ind.defaultParams || [],
          },
          false // 独立副图 Pane
        )
      }
    } else {
      if (currentOnChart.length > 0) {
        // 直接按唯一 id 移除，KLineChart 内部会自动销毁空置的 Pane！
        klineChart?.removeIndicator({ id: `sub_${ind.key}` })
      }
    }
  })
}

function toggleIndicatorKey(key: string) {
  activeIndicators.value = {
    ...activeIndicators.value,
    [key]: !activeIndicators.value[key],
  }
  nextTick(() => {
    syncIndicators()
  })
}

const activeIndicatorCount = computed(() => {
  return Object.values(activeIndicators.value).filter(Boolean).length
})

function initChart() {
  if (!chartContainer.value) return
  disposeKLineChart(chartContainer.value)

  klineChart = initKLineChart(chartContainer.value, {
    locale: isEn.value ? 'en-US' : 'zh-CN',
    timezone: 'Asia/Shanghai',
    styles: getChartStyles(),
    formatter: {
      // 纯纯正正的纯数字时间刻度！彻底去除“几日几日”中文，符合用户习惯！
      formatDate: ({ timestamp }) => {
        const date = new Date(timestamp)
        const m = String(date.getMonth() + 1).padStart(2, '0')
        const d = String(date.getDate()).padStart(2, '0')
        const hh = String(date.getHours()).padStart(2, '0')
        const mm = String(date.getMinutes()).padStart(2, '0')
        if (currentPeriod.value === '1D') {
          return `${m}-${d}`
        }
        if (currentPeriod.value === '4H') {
          return `${m}-${d} ${hh}:${mm}`
        }
        return `${hh}:${mm}`
      },
    },
  })

  if (!klineChart) return
  ;(window as any).__klineChart = klineChart
  klineChart.setOffsetRightDistance(25)

  // 必须显式设置默认 symbol 与 period，KLineChart 内部的 _dataLoader 才会触发加载！
  klineChart.setSymbol({
    ticker: `${currentSymbol.value}/USDT`,
    pricePrecision: 2,
    volumePrecision: 2,
  })
  klineChart.setPeriod({ type: 'hour', span: 1 })

  // 配置 DataLoader 驱动
  klineChart.setDataLoader({
    getBars: async ({ callback }) => {
      try {
        const res = await fetch(
          `/api/v1/market/${currentInstId.value}/candles?bar=${currentPeriod.value}&limit=150`
        )
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json()
        if (Array.isArray(data.candles) && data.candles.length > 0) {
          candles.value = data.candles
          const klineList: KLineData[] = data.candles.map((c: any) => ({
            timestamp: c.ts,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: c.vol,
            turnover: c.vol * c.close,
          }))
          callback(klineList, false)
          nextTick(() => {
            klineChart?.scrollToRealTime()
            updatePriceLines()
          })
          return
        }
      } catch (err) {
        console.warn('DataLoader getBars error:', err)
      }
      callback([], false)
    },
  })

  // 挂载默认指标 (VOL + VWAP)
  syncIndicators()
}

// 清除并更新价格线
function updatePriceLines() {
  if (!klineChart) return

  // 1. 先移除旧的价格线 Overlay
  if (entryOverlayId) {
    klineChart.removeOverlay({ id: entryOverlayId })
    entryOverlayId = null
  }
  if (slOverlayId) {
    klineChart.removeOverlay({ id: slOverlayId })
    slOverlayId = null
  }
  if (tpOverlayId) {
    klineChart.removeOverlay({ id: tpOverlayId })
    tpOverlayId = null
  }

  // 2. 只有在有实盘持仓、或者有挂单、或者在试算模式下，才绘制价格线！
  const entryPx = effectiveEntry.value
  const slPx = effectiveSL.value
  const tpPx = effectiveTP.value
  const hasPosOrOrder = activePosition.value || activeOrder.value || simMode.value

  if (hasPosOrOrder && entryPx > 0) {
    const isLong = liveSide.value === 'long'
    const entryRes = klineChart.createOverlay({
      name: 'priceLine',
      paneId: 'candle_pane',
      points: [{ value: entryPx }],
      styles: {
        line: {
          style: 'solid',
          size: 1.5,
          color: isLong ? '#10B981' : '#F43F5E',
        },
        text: {
          size: 11,
          color: '#FFFFFF',
          backgroundColor: isLong ? '#10B981' : '#F43F5E',
        },
      },
      extendData: isLong ? (isEn.value ? 'Entry Long' : '多头入场') : (isEn.value ? 'Entry Short' : '空头入场'),
    })
    entryOverlayId = typeof entryRes === 'string' ? entryRes : null
  }

  if (slPx > 0) {
    const slRes = klineChart.createOverlay({
      name: 'priceLine',
      paneId: 'candle_pane',
      points: [{ value: slPx }],
      styles: {
        line: {
          style: 'dashed',
          dashedValue: [6, 4],
          size: 1.5,
          color: '#F43F5E',
        },
        text: {
          size: 11,
          color: '#FFFFFF',
          backgroundColor: '#F43F5E',
        },
      },
      extendData: `🛑 ${isEn.value ? 'SL' : '止损SL'} -${riskRewardMetrics.value.riskPct.toFixed(1)}%`,
    })
    slOverlayId = typeof slRes === 'string' ? slRes : null
  }

  if (tpPx > 0) {
    const tpRes = klineChart.createOverlay({
      name: 'priceLine',
      paneId: 'candle_pane',
      points: [{ value: tpPx }],
      styles: {
        line: {
          style: 'dashed',
          dashedValue: [6, 4],
          size: 1.5,
          color: '#10B981',
        },
        text: {
          size: 11,
          color: '#FFFFFF',
          backgroundColor: '#10B981',
        },
      },
      extendData: `🎯 ${isEn.value ? 'TP' : '止盈TP'} +${riskRewardMetrics.value.rewardPct.toFixed(1)}%`,
    })
    tpOverlayId = typeof tpRes === 'string' ? tpRes : null
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
  const m = Math.floor((remainSec % 3600) / 60)
  const s = remainSec % 60
  candleCountdown.value = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

// 拉取行情蜡烛数据 (供定时静默刷新使用)
async function loadCandles(silent = false, resetTime = false) {
  if (!klineChart) return
  if (!silent) isLoading.value = true

  try {
    const res = await fetch(
      `/api/v1/market/${currentInstId.value}/candles?bar=${currentPeriod.value}&limit=150`
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    if (Array.isArray(data.candles) && data.candles.length > 0) {
      candles.value = data.candles

      // 转换为 KLineChart 标准数据结构
      const klineList: KLineData[] = data.candles.map((c: any) => ({
        timestamp: c.ts,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.vol,
        turnover: c.vol * c.close,
      }))

      // 配置标的价格精度
      const prec = getSymbolPrecision(currentSymbol.value, currentPrice.value)
      klineChart.setSymbol({
        ticker: `${currentSymbol.value}/USDT`,
        pricePrecision: prec,
        volumePrecision: 2,
      })

      // 增量精准更新 vs 全量初始化
      const lastCandle = klineList[klineList.length - 1]
      if (lastCandle) {
        currentPrice.value = Number(lastCandle.close)
      }
      if (resetTime || klineChart.getDataList().length === 0) {
        // 全量加载
        klineChart.setDataLoader({
          getBars: ({ callback }) => {
            callback(klineList, false)
          },
        })
        klineChart.scrollToRealTime()
      } else {
        // 定时轮询更新：直接精准喂入最新最后一根/多根未结蜡烛，驱动 K 线毫秒级实时跳动！
        const lastCandle = klineList[klineList.length - 1]
        if (lastCandle) {
          const storeImp = (klineChart as any)._chartStore
          if (storeImp && typeof storeImp._addData === 'function') {
            storeImp._addData(lastCandle, 'update')
          } else {
            // fallback
            klineChart.setDataLoader({
              getBars: ({ callback }) => {
                callback(klineList, false)
              },
            })
          }
        }
      }

      updatePriceLines()
    }
  } catch (err) {
    console.warn('Candles fetch fallback:', err)
  } finally {
    if (!silent) isLoading.value = false
  }
}

// 切换币种
function selectSymbol(s: string) {
  const sym = s.toUpperCase()
  if (sym === currentSymbol.value) return
  currentSymbol.value = sym
  emit('select-symbol', sym)

  // 1. 立即清除旧币种的价格线
  if (entryOverlayId) klineChart?.removeOverlay({ id: entryOverlayId })
  if (slOverlayId) klineChart?.removeOverlay({ id: slOverlayId })
  if (tpOverlayId) klineChart?.removeOverlay({ id: tpOverlayId })
  entryOverlayId = null
  slOverlayId = null
  tpOverlayId = null

  // 2. 清空旧数据防止坐标轴跨度被拉扯
  klineChart?.resetData()

  // 3. 加载新标的蜡烛并滚动到最右侧
  loadCandles(false, true)
}

// 切换周期
function selectPeriod(p: any) {
  if (p.id === currentPeriod.value) return
  currentPeriod.value = p.id
  klineChart?.setPeriod({ type: p.type, span: p.span })
  loadCandles(false, true)
}

function copySimulationSummary() {
  const text = `【R20 风控测算】${currentSymbol.value} 入场:${effectiveEntry.value} SL:${effectiveSL.value} TP:${effectiveTP.value} R:R=${riskRewardMetrics.value.rrRatio.toFixed(2)}:1`
  navigator.clipboard.writeText(text).then(() => {
    copied.value = true
    setTimeout(() => {
      copied.value = false
    }, 2000)
  })
}

// 监听主题与外部持仓变化
watch(isDark, () => {
  klineChart?.setStyles(getChartStyles())
})

watch(() => props.symbol, (newSym) => {
  if (newSym && newSym.toUpperCase() !== currentSymbol.value) {
    selectSymbol(newSym)
  }
})

watch([() => activePosition.value, () => activeOrder.value], () => {
  updatePriceLines()
})

defineExpose({
  selectSymbol,
})

let timer: any = null
let countdownTimer: any = null

function handleClickOutside(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (!target.closest('.indicator-dropdown-container')) {
    showIndicatorMenu.value = false
  }
}

onMounted(() => {
  const initSym = props.initialSymbol || props.symbol
  if (initSym) currentSymbol.value = initSym.toUpperCase()
  document.addEventListener('click', handleClickOutside)
  nextTick(() => {
    initChart()
    // 2.5s 静默增量拉取最新Tick/未结蜡烛，保证移动端与PC端实时跳动！
    timer = setInterval(() => {
      loadCandles(true, false)
    }, 2500)
    countdownTimer = setInterval(updateCountdown, 1000)
  })
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
  if (timer) clearInterval(timer)
  if (countdownTimer) clearInterval(countdownTimer)
  if (chartContainer.value) {
    disposeKLineChart(chartContainer.value)
    klineChart = null
  }
})
</script>

<template>
  <div
    class="flex flex-col border rounded-xl overflow-hidden shadow-xs transition-all select-none"
    style="background-color: var(--bg-card); border-color: var(--border-subtle);"
  >
    <!-- Top Bar: 标的切换、周期选择与指标下拉工作台 -->
    <div
      class="p-2 sm:p-3 border-b flex flex-wrap items-center justify-between gap-2"
      style="border-color: var(--border-subtle);"
    >
      <!-- 标的按钮组 -->
      <div class="flex items-center space-x-1.5 overflow-x-auto max-w-full pb-0.5 sm:pb-0 scrollbar-none">
        <button
          v-for="sym in availableSymbols"
          :key="sym"
          @click="selectSymbol(sym)"
          class="h-7 px-2.5 rounded-lg text-xs font-mono font-bold transition-all shrink-0 cursor-pointer flex items-center space-x-1"
          :style="currentSymbol === sym
            ? { backgroundColor: 'var(--color-brand-bg)', color: 'var(--color-brand)', border: '1px solid var(--color-brand-border)' }
            : { backgroundColor: 'var(--bg-badge)', color: 'var(--text-muted)', border: '1px solid transparent' }"
        >
          <span
            v-if="store.positions.some(p => p.instId?.startsWith(sym) || p.name === sym)"
            class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse mr-0.5"
          ></span>
          <span>{{ sym }}</span>
        </button>
      </div>

      <!-- 周期、指标下拉与试算工具 -->
      <div class="flex items-center space-x-1.5 sm:space-x-2 shrink-0 font-mono">
        <!-- 周期切换 -->
        <div class="flex items-center rounded-lg p-0.5 border text-xs" style="background-color: var(--bg-app); border-color: var(--border-subtle);">
          <button
            v-for="p in periods"
            :key="p.id"
            @click="selectPeriod(p)"
            class="h-6 px-2 rounded-md font-bold transition-all cursor-pointer"
            :style="currentPeriod === p.id
              ? { backgroundColor: 'var(--bg-badge)', color: 'var(--text-main)' }
              : { color: 'var(--text-muted)' }"
          >
            {{ p.label }}
          </button>
        </div>

        <!-- 专业级指标分类下拉菜单 (Indicators Dropdown) -->
        <div class="relative indicator-dropdown-container">
          <button
            @click="showIndicatorMenu = !showIndicatorMenu"
            class="h-6.5 px-2.5 rounded-lg text-xs font-mono border flex items-center space-x-1.5 transition-all cursor-pointer font-bold"
            :style="showIndicatorMenu || activeIndicatorCount > 0
              ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--color-brand)' }
              : { backgroundColor: 'var(--bg-card)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
          >
            <SlidersHorizontal class="w-3 h-3" />
            <span>{{ isEn ? 'Indicators' : '指标' }}</span>
            <span
              v-if="activeIndicatorCount > 0"
              class="w-4 h-4 rounded-full bg-emerald-500/20 text-emerald-400 text-[10px] flex items-center justify-center font-bold"
            >
              {{ activeIndicatorCount }}
            </span>
            <ChevronDown class="w-3 h-3 transition-transform" :class="showIndicatorMenu ? 'rotate-180' : ''" />
          </button>

          <!-- 下拉菜单浮层 -->
          <div
            v-if="showIndicatorMenu"
            class="absolute right-0 top-8 z-50 w-72 p-3 rounded-xl border shadow-xl flex flex-col space-y-3 font-mono text-xs backdrop-blur-md"
            style="background-color: var(--bg-card); border-color: var(--border-medium);"
          >
            <!-- 板块 1: 主图指标 (叠在蜡烛图上) -->
            <div>
              <div class="flex items-center justify-between pb-1.5 border-b mb-1.5" style="border-color: var(--border-subtle);">
                <span class="text-[11px] font-bold uppercase tracking-wider text-amber-400 flex items-center space-x-1">
                  <span>{{ isEn ? 'Main Chart Overlays' : '主图叠加指标' }}</span>
                </span>
                <span class="text-[9px]" style="color: var(--text-faint);">{{ isEn ? 'On Candlestick' : '主蜡烛同屏' }}</span>
              </div>
              <div class="grid grid-cols-2 gap-1.5">
                <button
                  v-for="ind in mainIndicators"
                  :key="ind.key"
                  @click="toggleIndicatorKey(ind.key)"
                  class="p-1.5 rounded-lg border text-left flex items-center justify-between transition-all cursor-pointer"
                  :style="activeIndicators[ind.key]
                    ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--text-main)' }
                    : { backgroundColor: 'var(--bg-card-subtle)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
                >
                  <div class="flex items-center space-x-1.5 truncate">
                    <span class="w-2 h-2 rounded-full shrink-0" :style="{ backgroundColor: ind.color }"></span>
                    <span class="font-bold truncate">{{ ind.label }}</span>
                  </div>
                  <Check v-if="activeIndicators[ind.key]" class="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                </button>
              </div>
            </div>

            <!-- 板块 2: 副图指标 (独立视口窗格) -->
            <div>
              <div class="flex items-center justify-between pb-1.5 border-b mb-1.5" style="border-color: var(--border-subtle);">
                <span class="text-[11px] font-bold uppercase tracking-wider text-emerald-400 flex items-center space-x-1">
                  <span>{{ isEn ? 'Sub-Window Panes' : '副图震荡指标' }}</span>
                </span>
                <span class="text-[9px]" style="color: var(--text-faint);">{{ isEn ? 'Independent Panes' : '独立高宽窗口' }}</span>
              </div>
              <div class="grid grid-cols-2 gap-1.5">
                <button
                  v-for="ind in subIndicators"
                  :key="ind.key"
                  @click="toggleIndicatorKey(ind.key)"
                  class="p-1.5 rounded-lg border text-left flex items-center justify-between transition-all cursor-pointer"
                  :style="activeIndicators[ind.key]
                    ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--text-main)' }
                    : { backgroundColor: 'var(--bg-card-subtle)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
                >
                  <div class="flex items-center space-x-1.5 truncate">
                    <span class="w-2 h-2 rounded-full shrink-0" :style="{ backgroundColor: ind.color }"></span>
                    <span class="font-bold truncate">{{ ind.label }}</span>
                  </div>
                  <Check v-if="activeIndicators[ind.key]" class="w-3.5 h-3.5 text-emerald-400 shrink-0" />
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- 调价平移试算 -->
        <button
          @click="simMode = !simMode; if (simMode) initSimulation(); updatePriceLines()"
          class="h-6.5 px-2 rounded-lg text-xs font-mono border flex items-center space-x-1 transition-all cursor-pointer font-bold"
          :style="simMode
            ? { backgroundColor: 'var(--color-brand-bg)', borderColor: 'var(--color-brand-border)', color: 'var(--color-brand)' }
            : { backgroundColor: 'var(--bg-card)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
          :title="simMode ? (isEn ? 'Exit' : '退出试算') : (isEn ? 'Simulate' : '平移试算 R:R')"
        >
          <Sliders class="w-3 h-3" />
          <span class="hidden sm:inline">{{ simMode ? (isEn ? 'Exit' : '退出') : t('chart.simulateBtn', '试算') }}</span>
        </button>

        <button
          @click="loadCandles(false, true)"
          class="h-6.5 w-6.5 rounded-lg border flex items-center justify-center transition-colors cursor-pointer"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);"
          :title="isEn ? 'Refresh' : '刷新行情'"
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

      <div class="flex items-center space-x-2 text-[10px] font-mono" style="color: var(--text-muted);">
        <span>{{ t('chart.countdownLabel', 'K线结线倒计时') }}:</span>
        <span class="font-bold text-amber-400 num-tabular">{{ candleCountdown }}</span>
      </div>
    </div>

    <!-- 图表挂载核心容器 (纯 Canvas 渲染，自适应铺满) -->
    <div
      ref="chartContainer"
      class="w-full relative"
      :style="{ height: fullscreen ? 'calc(100vh - 260px)' : '480px' }"
    ></div>

    <!-- 底部风控测算控制台 (平移试算展开时展现) -->
    <div
      v-if="simMode"
      class="border-t p-3 sm:p-4 space-y-3 transition-all font-mono"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
    >
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div class="flex items-center space-x-2">
          <Zap class="w-4 h-4 text-amber-400 shrink-0" />
          <span class="text-xs font-bold" style="color: var(--text-main);">
            {{ t('chart.simulateTitle', '科学预期盈亏比测算控制台') }}
          </span>
          <span
            class="text-[10px] px-2 py-0.5 rounded border font-bold"
            :class="riskRewardMetrics.hasRealPosition ? 'text-indigo-400 bg-indigo-500/10 border-indigo-500/30' : 'text-slate-400 bg-slate-500/10 border-slate-500/30'"
          >
            {{ riskRewardMetrics.hasRealPosition ? t('chart.holdingModeNotice', '实盘持仓联动模式') : t('chart.speculativeNotice', '标准观望测算模式') }}
          </span>
        </div>
      </div>

      <!-- 四维测算卡片 -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-2 text-xs">
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

      <!-- 调整滑动条与按钮 -->
      <div class="flex flex-wrap items-center justify-between gap-3 pt-1">
        <div class="flex items-center space-x-2">
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
          class="px-3 py-1 rounded-lg border text-xs font-mono font-bold flex items-center space-x-1.5 cursor-pointer transition-all"
          style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);"
        >
          <Check v-if="copied" class="w-3.5 h-3.5 text-emerald-400" />
          <Copy v-else class="w-3.5 h-3.5" />
          <span>{{ copied ? (isEn ? 'Copied' : '已复制') : (isEn ? 'Copy Params' : '复制风控参数') }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

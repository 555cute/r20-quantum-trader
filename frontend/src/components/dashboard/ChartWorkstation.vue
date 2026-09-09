<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useDashboardStore } from '../../stores/dashboard'
import { useTheme } from '../../composables/useTheme'
import { useI18n } from '../../composables/useI18n'
import {
  init as initKLineChart,
  dispose as disposeKLineChart,
  registerIndicator,
  type Chart as KLineChartType,
  type KLineData,
} from 'klinecharts'
import {
  Sliders,
  RefreshCw,
  Copy,
  Check,
  RotateCcw,
  Zap,
  SlidersHorizontal,
  ChevronDown,
  Maximize,
  Minimize,
} from 'lucide-vue-next'
import BaseSegmented from '../base/BaseSegmented.vue'

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
}>()

const emit = defineEmits<{
  (e: 'select-symbol', symbol: string): void
}>()

/* 工作站全屏：自管浮层，滚动锁 */
const isFullscreen = ref(false)
watch(isFullscreen, (v) => {
  document.body.style.overflow = v ? 'hidden' : ''
})
onUnmounted(() => { document.body.style.overflow = '' })

const store = useDashboardStore()
const { theme, cvd } = useTheme()
const isDark = computed(() => theme.value === 'dark')

/* P4: mobile hides the always-on legend to stop multi-line overlay on narrow screens;
   desktop keeps it (data always readable). Re-applied when crossing the 640px breakpoint. */
function legendRule(): 'always' {
  // 用户要求图例常驻左上角；副图压缩 + 容器加高缓解遮挡
  return 'always'
}

/* P1: resolve design-token value at render time — chart follows theme & CVD switches */
function tok(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888888'
}
const { t, isEn } = useI18n()

// ==========================================
// 1. 标的池与周期切换 (动态读取系统监控池)
// ==========================================
const availableSymbols = computed(() => {
  const holdingSet = new Set<string>()
  const otherSet = new Set<string>()

  // 1. 优先提取当前持仓标的 (去重)
  if (Array.isArray(store.positions)) {
    store.positions.forEach((p: any) => {
      const sym = (p.name || p.instId?.replace('-USDT-SWAP', '').replace('-USDT', '') || '').toUpperCase()
      if (sym) holdingSet.add(sym)
    })
  }

  // 2. 优先提取挂单标的 (去重)
  if (Array.isArray(store.pendingOrders)) {
    store.pendingOrders.forEach((o: any) => {
      const sym = (o.name || o.instId?.replace('-USDT-SWAP', '').replace('-USDT', '') || '').toUpperCase()
      if (sym) holdingSet.add(sym)
    })
  }

  // 3. 提取全部监控池标的（store.factors 才是 dashboard store 真实暴露的字段；
  //    此前误写为 store.factorLibrary，恒为 undefined 导致动态标的整段被跳过，标签页永远只剩硬编码兜底）
  if (Array.isArray(store.factors)) {
    store.factors.forEach((f: any) => {
      const sym = (f.name || f.instId?.replace('-USDT-SWAP', '').replace('-USDT', '') || '').toUpperCase()
      if (sym && !holdingSet.has(sym)) {
        otherSet.add(sym)
      }
    })
  }

  // 冷启动兜底：首轮 /api/all 尚未返回时先给一个确定存在的标的（后台校验 btc_required 保证 BTC 恒在池内），
  // 不再伪造一份 6 币种清单——那会掩盖真实标的池并让扩容失效。
  if (holdingSet.size === 0 && otherSet.size === 0) {
    otherSet.add('BTC')
  }

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
const symbolMenu = ref<boolean>(false)

/** 持仓/挂单中的币种（选币下拉的徽标） */
const holdingSet = computed(() => {
  const s = new Set<string>()
  store.positions.forEach((p: any) => { const n = String(p.name || p.instId || '').split('-')[0].toUpperCase(); if (n) s.add(n) })
  store.pendingOrders.forEach((o: any) => { const n = String(o.name || o.instId || '').split('-')[0].toUpperCase(); if (n) s.add(n) })
  return s
})

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
  MACD: false,
  RSI: false,
  KDJ: false,
  OBV: false,
  WR: false,
})

// 记录已挂载的指标 Pane ID，以便精准开关

// 当前标的计算
const currentInstId = computed(() => `${currentSymbol.value}-USDT-SWAP`)
const factorItem = computed(() => {
  if (!Array.isArray(store.factors)) return undefined
  return store.factors.find(
    (f: any) =>
      f.instId === currentInstId.value ||
      f.instId === `${currentSymbol.value}-USDT` ||
      f.instId?.startsWith(currentSymbol.value)
  )
})

const currentAtr = computed(() => {
  const it: any = factorItem.value || {}
  const direct = Number(it.atr_1h || it.atr1h || 0)
  if (direct > 0) return direct
  // 退化路径：仅有百分比时用 价格×ATR% 还原，避免出现 $0.0
  const pct = Number(it.atr_pct || it.atr_1h_pct || 0)
  const px = Number(it.price || currentPrice.value || 0)
  if (pct > 0 && px > 0) return (px * pct) / 100
  return 0
})
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
  // 未持仓时按「可用余额 × 20%」估算单笔保证金（与执行层 R20_MAX_MARGIN_EQUITY_RATIO 同口径），
  // 不再写死 100U —— 那会让小资金账户看到与真实风险完全不符的预估盈亏。
  const availEq = Number(store.account?.avail_eq || store.account?.total_eq || 0)
  let activeMargin = availEq > 0 ? Math.round(availEq * 0.20 * 100) / 100 : 0
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

function getChartStyles(): any {
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
        upColor: tok('--up'),
        downColor: tok('--down'),
        noChangeColor: tok('--ink-3'),
        upBorderColor: tok('--up'),
        downBorderColor: tok('--down'),
        noChangeBorderColor: tok('--ink-3'),
        upWickColor: tok('--up'),
        downWickColor: tok('--down'),
        noChangeWickColor: tok('--ink-3'),
      },
      priceMark: {
        show: true,
        high: {
          show: false,
          color: tok('--ink-2'),
          textOffset: 4,
          textSize: 10,
        },
        low: {
          show: false,
          color: tok('--ink-2'),
          textOffset: 4,
          textSize: 10,
        },
        last: {
          show: true,
          upColor: tok('--up'),
          downColor: tok('--down'),
          noChangeColor: tok('--ink-3'),
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
            color: tok('--ink-1'),
          },
        },
      },
      tooltip: {
        showRule: legendRule(),
        showType: 'standard',
        text: {
          size: 11,
          family: 'JetBrains Mono, monospace',
          color: tok('--ink-2'),
        },
      },
    },
    indicator: {
      tooltip: {
        showRule: legendRule(),
        showType: 'standard',
      },
      ohlc: {
        upColor: tok('--up'),
        downColor: tok('--down'),
        noChangeColor: tok('--ink-3'),
      },
      lines: [
        { style: 'solid', smooth: false, size: 1.5, color: '#F59E0B' }, // MA5 / 黄
        { style: 'solid', smooth: false, size: 1.5, color: '#38BDF8' }, // MA10 / 蓝
        { style: 'solid', smooth: false, size: 1.5, color: '#A855F7' }, // MA20 / 紫
        { style: 'solid', smooth: false, size: 1.5, color: tok('--down') },
        { style: 'solid', smooth: false, size: 1.5, color: tok('--up') },
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
          color: tok('--ink-1'),
        },
      },
    },
    xAxis: {
      show: true,
      size: 'auto',
      axisLine: {
        show: true,
        color: tok('--surface-3'),
        size: 1,
      },
      tickText: {
        show: true,
        color: tok('--ink-3'),
        family: 'JetBrains Mono, monospace',
        size: 10,
      },
      tickLine: {
        show: true,
        size: 1,
        length: 3,
        color: tok('--surface-3'),
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
        color: tok('--surface-3'),
        size: 1,
      },
      tickText: {
        show: true,
        color: tok('--ink-2'),
        family: 'JetBrains Mono, monospace',
        size: 11,
      },
      tickLine: {
        show: false,
      },
    },
    separator: {
      size: 1,
      color: tok('--surface-3'),
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
          color: tok('--ink-3'),
        },
        text: {
          show: true,
          color: tok('--ink-1'),
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
          color: tok('--ink-3'),
        },
        text: {
          show: true,
          color: tok('--ink-1'),
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
        const paneId = klineChart?.createIndicator(
          {
            id: `sub_${ind.key}`,
            name: ind.name,
            calcParams: ind.defaultParams || [],
          },
          false // 独立副图 Pane
        )
        // 副图压缩到 64px，把高度还给主图（用户反馈主图太小）
        if (paneId) klineChart?.setPaneOptions({ id: paneId, height: 64, minHeight: 48 })
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
          `/api/v1/market/${currentInstId.value}/candles?bar=${currentPeriod.value}&limit=150&_t=${Date.now()}`,
          { cache: 'no-store' }
        )
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json()
        if (Array.isArray(data.candles) && data.candles.length > 0) {
          candles.value = data.candles
          const lastC = data.candles[data.candles.length - 1]
          if (lastC) {
            currentPrice.value = Number(lastC.close)
          }
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
          color: tok('--ink-1'),
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
          color: tok('--ink-1'),
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
          color: tok('--ink-1'),
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
      `/api/v1/market/${currentInstId.value}/candles?bar=${currentPeriod.value}&limit=150&_t=${Date.now()}`,
      { cache: 'no-store' }
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
        if (lastCandle) {
          const storeImp = (klineChart as any)._chartStore
          if (storeImp && typeof storeImp._addData === 'function') {
            storeImp._addData(lastCandle, 'update')
            // 确保十字星与右轴最新价标签即时重绘
            ;(klineChart as any).updatePane?.(1)
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
watch([isDark, cvd], () => {
  klineChart?.setStyles(getChartStyles())
})

/* re-apply legend rule only when crossing the mobile breakpoint (cheap) */
let lastMobile = typeof window !== 'undefined' && window.innerWidth < 640
function onLegendBreakpoint() {
  const m = window.innerWidth < 640
  if (m !== lastMobile) {
    lastMobile = m
    klineChart?.setStyles(getChartStyles())
  }
}
onMounted(() => window.addEventListener('resize', onLegendBreakpoint))
onUnmounted(() => window.removeEventListener('resize', onLegendBreakpoint))

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
    symbolMenu.value = false
  }
}

onMounted(() => {
  const initSym = props.initialSymbol || props.symbol
  if (initSym) currentSymbol.value = initSym.toUpperCase()
  document.addEventListener('click', handleClickOutside)
  nextTick(() => {
    initChart()
    // 3s 静默拉取最新数据，保证准确对齐与跳动
    timer = setInterval(() => {
      loadCandles(true, false)
    }, 3000)
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
    class="card overflow-hidden select-none"
    :class="isFullscreen ? 'fixed inset-0 z-[var(--z-float)] rounded-none' : ''"
  >
    <!-- 工具条：行情信息 + 工作站工具 -->
    <div
      class="flex flex-wrap items-center gap-x-3 gap-y-2 border-b px-3 py-2 sm:px-4"
      style="border-color: var(--line-1); background-color: var(--surface-1)"
    >
      <!-- 选币下拉 -->
      <div class="indicator-dropdown-container relative">
        <button
          class="flex h-8 cursor-pointer items-center gap-1.5 rounded-lg border px-2.5 transition-colors"
          style="border-color: var(--line-2); background-color: var(--surface-2)"
          :aria-expanded="symbolMenu"
          @click="symbolMenu = !symbolMenu"
        >
          <span class="text-sm font-bold" style="color: var(--ink-strong)">{{ currentSymbol }}</span>
          <span class="text-xs" style="color: var(--ink-3)">/USDT · {{ t('dash.matrix.chart.perp') }}</span>
          <ChevronDown class="h-3.5 w-3.5 transition-transform" :class="symbolMenu && 'rotate-180'" style="color: var(--ink-2)" />
        </button>
        <Transition name="pop">
          <div
            v-if="symbolMenu"
            class="float-panel absolute left-0 top-9 z-50 max-h-80 w-56 overflow-y-auto p-1.5"
          >
            <button
              v-for="sym in availableSymbols"
              :key="sym"
              class="flex w-full cursor-pointer items-center justify-between rounded-md px-2.5 py-1.5 text-left text-sm transition-colors hover:bg-[var(--surface-1)]"
              :style="sym === currentSymbol ? { color: 'var(--accent)', fontWeight: 600 } : { color: 'var(--ink-1)' }"
              @click="selectSymbol(sym); symbolMenu = false"
            >
              <span class="num">{{ sym }}</span>
              <span v-if="holdingSet.has(sym)" class="badge badge-accent !h-4 !px-1 !text-[10px]">{{ t('dash.matrix.chart.holding') }}</span>
            </button>
          </div>
        </Transition>
      </div>

      <!-- 现价 / 涨跌 / ATR -->
      <span class="num text-md font-bold" style="color: var(--ink-strong)">
        {{ currentPrice >= 100 ? currentPrice.toFixed(1) : currentPrice.toFixed(4) }}
      </span>
      <span class="num text-xs font-semibold" :class="liveChangePct >= 0 ? 'up' : 'down'">
        {{ liveChangePct >= 0 ? '+' : '' }}{{ liveChangePct.toFixed(2) }}%
      </span>
      <span class="chip hidden !h-6 md:inline-flex">
        <span class="dot dot-live" />{{ t('dash.matrix.chart.live') }}
      </span>
      <span class="t-faint num hidden text-xs lg:inline">1H ATR {{ currentAtr >= 100 ? '$' + currentAtr.toFixed(1) : (currentAtr * 100).toFixed(2) + '%' }}</span>

      <!-- 右侧工具组 -->
      <div class="ms-auto flex flex-wrap items-center gap-1.5">
        <span class="chip mr-1 hidden !h-7 sm:inline-flex">
          <span class="t-faint">{{ t('dash.matrix.chart.barCloseIn') }}</span>
          <b class="num" style="color: var(--warn)">{{ candleCountdown }}</b>
        </span>

        <BaseSegmented
          :model-value="currentPeriod"
          :options="periods.map((p) => ({ value: p.id, label: p.label }))"
          @update:model-value="(id: any) => selectPeriod(periods.find((p) => p.id === id))"
        />

        <!-- 指标菜单 -->
        <div class="indicator-dropdown-container relative">
          <button
            class="btn btn-sm"
            :class="showIndicatorMenu || activeIndicatorCount > 0 ? 'btn-primary' : 'btn-ghost'"
            :aria-expanded="showIndicatorMenu"
            @click="showIndicatorMenu = !showIndicatorMenu"
          >
            <SlidersHorizontal />
            {{ t('dash.matrix.chart.indicators') }}
            <span v-if="activeIndicatorCount > 0" class="num">{{ activeIndicatorCount }}</span>
            <ChevronDown class="h-3 w-3 transition-transform" :class="showIndicatorMenu && 'rotate-180'" />
          </button>
          <Transition name="pop">
            <div v-if="showIndicatorMenu" class="float-panel absolute right-0 top-9 z-50 max-h-[65vh] w-72 overflow-y-auto p-3 max-md:fixed max-md:inset-x-2 max-md:top-auto max-md:bottom-2 max-md:w-auto max-md:max-h-[70vh]">
              <p class="t-label mb-2">{{ t('dash.matrix.chart.indicatorHint') }}</p>
              <p class="t-label mb-1.5">{{ t('dash.matrix.chart.overlays') }}</p>
              <div class="mb-3 grid grid-cols-2 gap-1.5">
                <button
                  v-for="ind in mainIndicators"
                  :key="ind.key"
                  class="flex cursor-pointer items-center justify-between rounded-md border px-2 py-1.5 text-xs font-semibold transition-colors"
                  :style="activeIndicators[ind.key]
                    ? { backgroundColor: 'var(--accent-bg)', borderColor: 'var(--accent-line)', color: 'var(--ink-1)' }
                    : { backgroundColor: 'var(--surface-1)', borderColor: 'var(--line-1)', color: 'var(--ink-2)' }"
                  @click="toggleIndicatorKey(ind.key)"
                >
                  <span class="flex min-w-0 items-center gap-1.5">
                    <span class="h-2 w-2 shrink-0 rounded-full" :style="{ backgroundColor: ind.color }" />
                    <span class="truncate">{{ ind.label }}</span>
                  </span>
                  <Check v-if="activeIndicators[ind.key]" class="h-3.5 w-3.5 shrink-0" style="color: var(--up)" />
                </button>
              </div>
              <p class="t-label mb-1.5">{{ t('dash.matrix.chart.panes') }}</p>
              <div class="grid grid-cols-2 gap-1.5">
                <button
                  v-for="ind in subIndicators"
                  :key="ind.key"
                  class="flex cursor-pointer items-center justify-between rounded-md border px-2 py-1.5 text-xs font-semibold transition-colors"
                  :style="activeIndicators[ind.key]
                    ? { backgroundColor: 'var(--accent-bg)', borderColor: 'var(--accent-line)', color: 'var(--ink-1)' }
                    : { backgroundColor: 'var(--surface-1)', borderColor: 'var(--line-1)', color: 'var(--ink-2)' }"
                  @click="toggleIndicatorKey(ind.key)"
                >
                  <span class="flex min-w-0 items-center gap-1.5">
                    <span class="h-2 w-2 shrink-0 rounded-full" :style="{ backgroundColor: ind.color }" />
                    <span class="truncate">{{ ind.label }}</span>
                  </span>
                  <Check v-if="activeIndicators[ind.key]" class="h-3.5 w-3.5 shrink-0" style="color: var(--up)" />
                </button>
              </div>
            </div>
          </Transition>
        </div>

        <!-- 试算开关 -->
        <button
          class="btn btn-sm"
          :class="simMode ? 'btn-primary' : 'btn-ghost'"
          :title="simMode ? t('dash.matrix.chart.sim.exit') : t('dash.matrix.chart.sim.enter')"
          @click="simMode = !simMode; if (simMode) initSimulation(); updatePriceLines()"
        >
          <Sliders />
          <span class="hidden sm:inline">{{ simMode ? t('dash.matrix.chart.sim.exit') : t('dash.matrix.chart.simulate') }}</span>
        </button>

        <button class="btn btn-ghost btn-icon btn-sm" :title="t('common.refresh')" @click="loadCandles(false, true)">
          <RefreshCw :class="isLoading && 'animate-spin'" />
        </button>
        <button
          class="btn btn-ghost btn-icon btn-sm"
          :title="isFullscreen ? t('dash.matrix.chart.exitFullscreen') : t('dash.matrix.chart.fullscreen')"
          @click="isFullscreen = !isFullscreen"
        >
          <Minimize v-if="isFullscreen" />
          <Maximize v-else />
        </button>
      </div>
    </div>

    <!-- 图表画布 -->
    <div
      ref="chartContainer"
      class="relative w-full"
      :style="{ height: isFullscreen ? 'calc(100vh - 108px)' : '560px' }"
    ></div>

    <!-- 试算控制台 -->
    <div
      v-if="simMode"
      class="space-y-3 border-t p-3 sm:p-4"
      style="border-color: var(--line-1); background-color: var(--surface-1)"
    >
      <div class="flex flex-wrap items-center gap-2">
        <Zap class="h-4 w-4" style="color: var(--warn)" />
        <span class="text-sm font-semibold" style="color: var(--ink-strong)">{{ t('dash.matrix.chart.sim.title') }}</span>
        <span class="badge" :class="riskRewardMetrics.hasRealPosition ? 'badge-info' : ''">
          {{ riskRewardMetrics.hasRealPosition ? t('dash.matrix.chart.sim.linked') : t('dash.matrix.chart.sim.spec') }}
        </span>
      </div>

      <div class="grid grid-cols-2 gap-2 text-xs lg:grid-cols-4">
        <div
          class="flex flex-col justify-between rounded-lg border p-2.5"
          :style="{
            backgroundColor: riskRewardMetrics.isRrCompliant ? 'var(--up-bg)' : 'var(--warn-bg)',
            borderColor: riskRewardMetrics.isRrCompliant ? 'var(--up-line)' : 'var(--warn-line)',
          }"
        >
          <span class="t-label" :style="{ color: riskRewardMetrics.isRrCompliant ? 'var(--up)' : 'var(--warn)' }">
            {{ riskRewardMetrics.isRrCompliant ? t('dash.matrix.chart.sim.rrOk') : t('dash.matrix.chart.sim.rrLow') }}
          </span>
          <div class="mt-1 flex items-baseline gap-1.5">
            <span class="num text-lg font-bold" :style="{ color: riskRewardMetrics.isRrCompliant ? 'var(--up)' : 'var(--warn)' }">
              {{ riskRewardMetrics.rrRatio.toFixed(2) }} : 1
            </span>
            <span class="t-faint">{{ t('dash.matrix.chart.sim.rrMin') }}</span>
          </div>
        </div>

        <div class="rounded-lg border p-2.5" style="background-color: var(--surface-2); border-color: var(--line-1)">
          <span class="t-label">{{ t('dash.matrix.chart.sim.atr') }}</span>
          <div class="mt-1 flex items-baseline gap-1.5">
            <span class="num text-lg font-bold" :style="{ color: riskRewardMetrics.isAtrOptimal ? 'var(--accent)' : 'var(--warn)' }">
              {{ riskRewardMetrics.atrMultiple.toFixed(2) }}x
            </span>
            <span class="t-faint">{{ riskRewardMetrics.isAtrOptimal ? t('dash.matrix.chart.sim.atrOk') : t('dash.matrix.chart.sim.atrDev') }}</span>
          </div>
        </div>

        <div class="rounded-lg border p-2.5" style="background-color: var(--surface-2); border-color: var(--line-1)">
          <span class="t-label" style="color: var(--up)">{{ t('dash.matrix.chart.sim.tp') }}</span>
          <div class="mt-1 flex items-baseline gap-1.5">
            <span class="num text-lg font-bold up">+${{ riskRewardMetrics.estProfitUsd.toFixed(2) }}</span>
            <span class="num up">{{ riskRewardMetrics.rewardPct.toFixed(1) }}%</span>
          </div>
        </div>

        <div class="rounded-lg border p-2.5" style="background-color: var(--surface-2); border-color: var(--line-1)">
          <span class="t-label" style="color: var(--down)">{{ t('dash.matrix.chart.sim.sl') }}</span>
          <div class="mt-1 flex items-baseline gap-1.5">
            <span class="num text-lg font-bold down">-${{ riskRewardMetrics.estRiskUsd.toFixed(2) }}</span>
            <span class="num down">({{ riskRewardMetrics.riskPct.toFixed(1) }}%)</span>
          </div>
        </div>
      </div>

      <div class="flex flex-wrap items-center justify-end gap-2 pt-1">
        <button class="btn btn-ghost btn-sm" @click="resetSimulation">
          <RotateCcw />{{ t('dash.matrix.chart.sim.reset') }}
        </button>
        <button class="btn btn-primary btn-sm" @click="copySimulationSummary">
          <Check v-if="copied" style="color: var(--up)" />
          <Copy v-else />
          {{ copied ? t('common.copied') : t('dash.matrix.chart.sim.copy') }}
        </button>
      </div>
    </div>
  </div>
</template>

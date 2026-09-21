<script setup lang="ts">
import { chartStyles } from './chartStyles'
import { computeRiskReward, symbolPrecision } from './chartMath'
import { deriveLiveEntry, deriveLiveSide, deriveLiveStopLoss, deriveLiveTakeProfit } from './chartLiveLevels'
import { planPriceLines } from './chartOverlays'
import { countdownLabel } from './chartCountdown'
import { fetchCandles } from './chartCandles'
import { mainIndicators, subIndicators, DEFAULT_ACTIVE_INDICATORS } from './chartIndicators'
import { fmtDate, fmtHM, fmtClock } from '../../utils/format';
import { ref, computed, watch, onMounted, onUnmounted, nextTick, useId } from 'vue'
import { usePopoverFocus } from '../../composables/usePopoverFocus'
import { useDashboardStore } from '../../stores/dashboard'
import { symOf, instIdOf } from '../../utils/instId'
import { useTheme } from '../../composables/useTheme'
import { useI18n } from '../../composables/useI18n'
import { demotePositiveTabIndex } from '../../utils/tabOrder'
import {
  init as initKLineChart,
  dispose as disposeKLineChart,
  registerIndicator,
  registerOverlay,
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
import CryptoLogo from './CryptoLogo.vue'

// ==========================================
// 0. 注册原生 VWAP 指标与价格线渲染优化
// ==========================================
registerOverlay({
  name: 'priceLine',
  totalStep: 2,
  needDefaultPointFigure: false,
  needDefaultXAxisFigure: false,
  needDefaultYAxisFigure: true,
  createPointFigures: ({ coordinates, bounding, overlay }) => {
    const y = coordinates[0]?.y ?? 0
    const lineStyle = overlay.styles?.line || {}
    const textStyle = overlay.styles?.text || {}
    const label = String(overlay.extendData || '')

    let strokeColor = lineStyle.color || '#10B981'
    let strokeStyle = lineStyle.style || 'solid'
    let strokeDashed = lineStyle.dashedValue || [6, 4]
    let badgeBg = textStyle.backgroundColor || strokeColor
    let displayBadge = label

    const isEntry = label.includes('入场') || label.includes('Entry')
    const isTp = label.includes('TP') || label.includes('止盈')
    const isSl = label.includes('SL') || label.includes('止损') || label.includes('锁利') || label.includes('保本') || label.includes('Lock') || label.includes('Breakeven')

    // 垂直错位避让：入场与止盈胶囊贴线上方，止损/锁利胶囊贴线下方，避免相近点位相互覆盖
    let textBaseline: 'top' | 'bottom' | 'middle' = 'middle'
    let textYOffset = 0

    if (isEntry) {
      strokeColor = '#0284C7'
      strokeStyle = 'solid'
      badgeBg = '#0369A1'
      displayBadge = `⚡ ${label.replace(/^[▲▼⚡🛑🔒\s]+/, '')}`
      textBaseline = 'bottom'
      textYOffset = -2
    } else if (isTp) {
      strokeColor = '#10B981'
      strokeStyle = 'dashed'
      strokeDashed = [8, 4]
      badgeBg = '#047857'
      displayBadge = `🎯 ${label.replace(/^[▲▼🎯\s]+/, '')}`
      textBaseline = 'bottom'
      textYOffset = -2
    } else if (isSl) {
      const isLock = label.includes('锁利') || label.includes('保本') || label.includes('Lock') || label.includes('Breakeven')
      strokeColor = isLock ? '#10B981' : '#F43F5E'
      strokeStyle = 'dashed'
      strokeDashed = isLock ? [8, 4] : [5, 3]
      badgeBg = isLock ? '#047857' : '#BE123C'
      displayBadge = isLock ? `🔒 ${label.replace(/^[▲▼🛑🔒\s]+/, '')}` : `🛑 ${label.replace(/^[▲▼🛑\s]+/, '')}`
      textBaseline = 'top'
      textYOffset = 2
    }

    // 视口上下边界保护：当线贴近画布顶部时（y < 24），改贴线下方；
    // 当线贴近画布底部时（bounding.height && y > bounding.height - 24），改贴线上方
    if (y < 24) {
      textBaseline = 'top'
      textYOffset = 3
    } else if (bounding.height && y > bounding.height - 24) {
      textBaseline = 'bottom'
      textYOffset = -3
    }

    // 价格超出当前可视窗口时，自然不绘制（用户缩放/平移至该价位时自然展现，不强行拉扯或挤在角落）
    if (y < -20 || (bounding.height > 0 && y > bounding.height + 20)) {
      return []
    }

    const startX = y < 70 ? Math.min(240, bounding.width * 0.5) : 0

    return [
      {
        type: 'line',
        attrs: {
          coordinates: [{ x: startX, y }, { x: bounding.width, y }],
        },
        styles: {
          style: strokeStyle,
          dashedValue: strokeDashed,
          size: lineStyle.size || 1.2,
          color: strokeColor,
        },
      },
      {
        type: 'text',
        ignoreEvent: true,
        attrs: {
          x: Math.max(10, bounding.width - 6),
          y: y + textYOffset,
          text: displayBadge,
          align: 'right',
          baseline: textBaseline,
        },
        styles: {
          size: 10,
          family: 'JetBrains Mono, -apple-system, BlinkMacSystemFont, sans-serif',
          weight: 'bold',
          color: '#FFFFFF',
          backgroundColor: badgeBg,
          paddingLeft: 4,
          paddingRight: 4,
          paddingTop: 1.5,
          paddingBottom: 1.5,
          borderRadius: 2,
        },
      },
    ]
  },
})
registerIndicator({
  name: 'VWAP',
  shortName: 'VWAP',
  series: 'price',
  precision: 2,
  // 批 18：图例由 klinecharts 渲染为「shortName + figure.title + 值」，
  // 原先 title 也是 'VWAP: ' → 图例出现「VWAP VWAP: 97.740」这种重复。
  // 去掉 figure.title 的前缀，图例变为「VWAP 97.740」。
  figures: [{ key: 'vwap', title: '', type: 'line' }],
  styles: {
    // ⚠️ 批 13 说明：此处是本仓**唯一**刻意保留的颜色字面量。
    // `registerIndicator` 在模块求值时执行，而 `tok()` 依赖 document 与已生效的
    // `data-theme`；此刻取值不可靠。且该色最终落在 canvas（不解析 var()）。
    // 改这条线色需要目视验证 K 线工位；本仓不装浏览器，目视验证经 harness 的
    // Playwright MCP 进行。未验证前按 chartStyles.ts 模块头「不动渲染路径」的纪律保留原值。
    // 事件驱动的重绘会经 getChartStyles() 覆盖大部分指标样式，本值是注册期兜底。
    lines: [{ style: 'solid', smooth: false, size: 1.5, color: '#06B6D4' }], // 青蓝色
  },
  calc: (dataList: KLineData[]) => {
    let cumTypicalVol = 0
    let cumVol = 0
    let lastDay = ''

    return dataList.map((kLine) => {
      const day = fmtDate(kLine.timestamp)
      // 每天重置或者连续累计
      if (lastDay !== '' && day !== lastDay) {
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
  fill?: boolean
  chartHeight?: string
}>()

const emit = defineEmits<{
  (e: 'select-symbol', symbol: string): void
}>()

/* 工作站全屏：自管浮层，滚动锁与重排自适应 */
const isFullscreen = ref(false)
watch(isFullscreen, (v) => {
  document.body.style.overflow = v ? 'hidden' : ''
  nextTick(() => {
    if (klineChart) {
      klineChart.resize()
      const offset = typeof window !== 'undefined' && window.innerWidth < 640 ? 75 : 95
      klineChart.setOffsetRightDistance(offset)
      klineChart.scrollToRealTime()
    }
    if (!v) {
      requestAnimationFrame(() => {
        klineChart?.resize()
      })
    }
  })
})
onUnmounted(() => { document.body.style.overflow = '' })

const store = useDashboardStore()
const { theme, cvd } = useTheme()
const isDark = computed(() => theme.value === 'dark')

function legendRule(): 'always' {
  // 指标数值（VWAP、MA、VOL 等）常驻显示，满足量化操盘随时观察要求
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
      const sym = (p.name || symOf(p.instId))?.replace(/_+$/, '').toUpperCase()
      if (sym) holdingSet.add(sym)
    })
  }

  // 2. 优先提取挂单标的 (去重)
  if (Array.isArray(store.pendingOrders)) {
    store.pendingOrders.forEach((o: any) => {
      const sym = (o.name || symOf(o.instId))?.replace(/_+$/, '').toUpperCase()
      if (sym) holdingSet.add(sym)
    })
  }

  // 3. 提取全部监控池标的（store.factors 才是 dashboard store 真实暴露的字段；
  //    此前误写为 store.factorLibrary，恒为 undefined 导致动态标的整段被跳过，标签页永远只剩硬编码兜底）
  if (Array.isArray(store.factors)) {
    store.factors.forEach((f: any) => {
      const sym = (f.name || symOf(f.instId))?.replace(/_+$/, '').toUpperCase()
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
/* 批 104：指标下拉是**非模态**气泡（role=dialog 但无 aria-modal），
   打开时要把焦点交给面板 —— 此前键盘 Enter 打开后焦点仍停在触发按钮上。 */
const indicatorTrigger = ref<HTMLElement | null>(null)
const indicatorPanel = ref<HTMLElement | null>(null)
usePopoverFocus(indicatorPanel, indicatorTrigger, showIndicatorMenu)
const symbolMenu = ref<boolean>(false)
const symbolMenuId = useId()
/* 批 104：选币下拉同样是气泡，一并接管焦点交接。 */
const symbolTrigger = ref<HTMLElement | null>(null)
const symbolPanel = ref<HTMLElement | null>(null)
usePopoverFocus(symbolPanel, symbolTrigger, symbolMenu)
const indicatorMenuId = useId()

/** 持仓/挂单中的币种（选币下拉的徽标） */
const holdingSet = computed(() => {
  const s = new Set<string>()
  store.positions.forEach((p: any) => { const n = String(p.name || p.instId || '').split('-')[0].replace(/_+$/, '').toUpperCase(); if (n) s.add(n) })
  store.pendingOrders.forEach((o: any) => { const n = String(o.name || o.instId || '').split('-')[0].replace(/_+$/, '').toUpperCase(); if (n) s.add(n) })
  return s
})
const activeIndicators = ref<Record<string, boolean>>({ ...DEFAULT_ACTIVE_INDICATORS })


// 当前标的计算
const currentInstId = computed(() => instIdOf(currentSymbol.value))
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
// 涨跌幅：当前蜡烛价格相比其开盘价的实时变化百分比。
// ⚠️ 第一百九十八刀：**读不到就是读不到**。原先没有蜡烛时 `return Number(factorItem.value?.c_1h_ret || 0) * 100`
// —— 而 `c_1h_ret` 后端**从未发过**（全仓无生产者）⇒ 兜底恒为 0，界面会把"没有行情"渲染成
// 精确的 "+0.00%"（doctrine：读不到 ≠ 没有）。现改为返回 null，模板据此显示占位。
const liveChangePct = computed<number | null>(() => {
  if (candles.value.length > 0) {
    const last = candles.value[candles.value.length - 1]
    if (last && last.open > 0) {
      return ((currentPrice.value - last.open) / last.open) * 100
    }
  }
  return null
})

// 实盘在手持仓与在途委托
const activePosition = computed(() => {
  if (!Array.isArray(store.positions)) return undefined
  const target = currentSymbol.value.toUpperCase()
  return store.positions.find((p) => {
    const sym = (p.name || symOf(p.instId))
    return sym === target || p.instId === currentInstId.value
  })
})
const activeOrder = computed(() => {
  if (!Array.isArray(store.pendingOrders)) return undefined
  const target = currentSymbol.value.toUpperCase()
  return store.pendingOrders.find((o) => {
    const sym = (o.name || symOf(o.inst || o.instId))
    return sym === target || o.instId === currentInstId.value
  })
})

// 真实最新价格 (纯从当前已加载的实时蜡烛最后一根获取，与K线和最新Tick 100% 同源)
const currentPrice = ref<number>(0)

// 真实开仓成本与方向（推导逻辑见 ./chartLiveLevels.ts，本处只负责读 ref 与响应式追踪）
const liveEntry = computed(() => deriveLiveEntry({
  position: activePosition.value, order: activeOrder.value, price: currentPrice.value,
}))

const liveSide = computed<'long' | 'short'>(() => deriveLiveSide({
  position: activePosition.value, order: activeOrder.value,
}))

const liveStopLoss = computed(() => deriveLiveStopLoss({
  position: activePosition.value, order: activeOrder.value,
}))

const liveTakeProfit = computed(() => deriveLiveTakeProfit({
  position: activePosition.value, order: activeOrder.value,
}))

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
const riskRewardMetrics = computed(() => computeRiskReward({
  entry: effectiveEntry.value,
  sl: effectiveSL.value,
  tp: effectiveTP.value,
  side: liveSide.value,
  atr: currentAtr.value,
  activePosition: activePosition.value,
  // 可用权益在**调用点**读取：仍在 computed 求值期间发生，响应式追踪不变
  availEq: Number(store.account?.avail_eq || store.account?.total_eq || 0),
}))

// 价格精度自适应
function getSymbolPrecision(price: number): number {
  return symbolPrecision(price)
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
  // 批 13：主题已完全由 tok() 经 --chart-* 主题化色板承载，不再需要传 isDark
  return chartStyles(legendRule, tok)
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
        const md = fmtDate(timestamp).slice(5)
        const hm = fmtHM(timestamp)
        if (currentPeriod.value === '1D') {
          return md
        }
        if (currentPeriod.value === '4H') {
          return `${md} ${hm}`
        }
        return hm
      },
    },
  })

  // 库把容器设成了 tabIndex=1（正数会插队到「跳到主内容」之前），装完立刻归一化
  demotePositiveTabIndex(chartContainer.value)

  if (!klineChart) return
  ;(window as any).__klineChart = klineChart
  const rightOffset = typeof window !== 'undefined' && window.innerWidth < 640 ? 75 : 95
  klineChart.setOffsetRightDistance(rightOffset)

  // 默认 K 线根数设为之前的 3/4 (单根蜡烛宽度调整为 4/3，蜡烛更清晰平滑)
  klineChart.setBarSpace(10 * (4 / 3))

  // 必须显式设置默认 symbol 与 period，KLineChart 内部的 _dataLoader 才会触发加载！
  klineChart.setSymbol({
    ticker: `${currentSymbol.value}/USDT`,
    pricePrecision: 2,
    volumePrecision: 2,
  })
  const initP = periods.value.find((p) => p.id === currentPeriod.value) || { type: 'hour', span: 1 }
  klineChart.setPeriod({ type: initP.type, span: initP.span })

  // 配置 DataLoader 驱动
  klineChart.setDataLoader({
    getBars: async ({ callback }) => {
      try {
        // 取数与归一统一走 chartCandles.ts —— 本文件原先有两条**逐字重复**的
        // 取数路径（此处与下方 loadCandles），字段映射完全一样。
        const { candles: raw, klineList, lastClose } = await fetchCandles(
          currentInstId.value,
          currentPeriod.value,
        )
        if (klineList.length > 0) {
          candles.value = raw
          if (lastClose !== null) {
            currentPrice.value = lastClose
          }
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

  // 阶段 3·F4：建线描述符抽到 chartOverlays.ts（纯函数）；此处只负责"怎么画"。
  const plan = planPriceLines({
    entryPx,
    slPx,
    tpPx,
    hasPosOrOrder: !!hasPosOrOrder,
    isLong: liveSide.value === 'long',
    riskPct: riskRewardMetrics.value.riskPct,
    rewardPct: riskRewardMetrics.value.rewardPct,
    textColor: tok('--ink-1'),
    isEn: isEn.value,
    isLockProfit: riskRewardMetrics.value.isLockProfit,
    slDiffPct: riskRewardMetrics.value.slDiffPct,
  })

  if (plan.entry) {
    const entryRes = klineChart.createOverlay(plan.entry)
    entryOverlayId = typeof entryRes === 'string' ? entryRes : null
  }

  if (plan.sl) {
    const slRes = klineChart.createOverlay(plan.sl)
    slOverlayId = typeof slRes === 'string' ? slRes : null
  }

  if (plan.tp) {
    const tpRes = klineChart.createOverlay(plan.tp)
    tpOverlayId = typeof tpRes === 'string' ? tpRes : null
  }
}

// 倒计时
function updateCountdown() {
  // 阶段 3·F4：纯算术抽到 chartCountdown.ts。时区仍由本处解析（时间契约留在调用点）。
  const now = new Date()
  const [hr = 0, min = 0, sec = 0] = fmtClock(now).split(':').map(Number)
  candleCountdown.value = countdownLabel(currentPeriod.value, { hr, min, sec })
}

// 拉取行情蜡烛数据 (供定时静默刷新使用)
async function loadCandles(silent = false, resetTime = false) {
  if (!klineChart) return
  if (!silent) isLoading.value = true

  try {
    // 取数与归一统一走 chartCandles.ts（与 setDataLoader 的 getBars 同源）
    const { candles: raw, klineList, lastClose } = await fetchCandles(
      currentInstId.value,
      currentPeriod.value,
    )
    if (klineList.length > 0) {
      candles.value = raw

      // 配置标的价格精度
      const prec = getSymbolPrecision(currentPrice.value)
      klineChart.setSymbol({
        ticker: `${currentSymbol.value}/USDT`,
        pricePrecision: prec,
        volumePrecision: 2,
      })

      // 增量精准更新 vs 全量初始化
      const lastCandle = klineList[klineList.length - 1]
      if (lastClose !== null) {
        currentPrice.value = lastClose
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
  // 批 77：剪贴板文案此前硬编码中文 —— 英文界面下用户复制出来是中英混排。
  const text = t('dash.matrix.chart.sim.copySummary', undefined, {
    sym: currentSymbol.value,
    entry: effectiveEntry.value,
    sl: effectiveSL.value,
    tp: effectiveTP.value,
    rr: riskRewardMetrics.value.rrRatio.toFixed(2),
  })
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
    if (klineChart) {
      klineChart.setOffsetRightDistance(m ? 75 : 95)
    }
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

/* 批 104：两个下拉此前**只能靠点空白处关**，按 Escape 毫无反应。
   指标下拉声明的是 `role="dialog"` —— WAI-ARIA 明确要求 Escape 关闭对话框；
   选币下拉是 listbox，同样应支持 Escape。焦点交还给触发器由 usePopoverFocus 负责。
   本监听挂在 document 冒泡阶段：模态弹层（useModalFocus）会 stopPropagation，
   所以模态开着时不会误伤。 */
function handleKeydown(e: KeyboardEvent) {
  if (e.key !== 'Escape') return
  if (isFullscreen.value) {
    isFullscreen.value = false
    return
  }
  if (!showIndicatorMenu.value && !symbolMenu.value) return
  showIndicatorMenu.value = false
  symbolMenu.value = false
}

onMounted(() => {
  const initSym = props.initialSymbol || props.symbol
  if (initSym) currentSymbol.value = initSym.toUpperCase()
  document.addEventListener('click', handleClickOutside)
  document.addEventListener('keydown', handleKeydown)
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
  document.removeEventListener('keydown', handleKeydown)
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
    class="dsh-card select-none"
    :class="[
      isFullscreen ? 'fixed inset-0 z-[100] !bg-[var(--surface-0,#090d16)] !bg-none rounded-none flex flex-col' : '',
      fill ? 'h-full flex flex-col min-h-0' : '',
    ]"
  >
    <!-- 工具条：行情信息 + 工作站工具 -->
    <header
      class="dsh-card-header flex flex-wrap items-center justify-between gap-x-3 gap-y-2"
    >
      <div class="flex items-center gap-2 flex-wrap">
        <!-- 选币下拉 -->
        <div class="indicator-dropdown-container relative">
          <button type="button"
            ref="symbolTrigger"
            class="flex h-7 cursor-pointer items-center gap-1.5 rounded border border-[var(--line-1)] bg-[var(--surface-2)] px-2.5 transition-colors hover:bg-[var(--surface-3)]"
            :aria-expanded="symbolMenu"
            aria-haspopup="listbox"
            :aria-controls="symbolMenu ? symbolMenuId : undefined"
            @click="symbolMenu = !symbolMenu"
          >
            <CryptoLogo :symbol="currentSymbol" :size="16" />
            <span class="text-xs font-bold" style="color: var(--ink-strong)">{{ currentSymbol }}</span>
            <span class="text-3xs" style="color: var(--ink-3)">/USDT · {{ t('dash.matrix.chart.perp') }}</span>
            <ChevronDown class="h-3 w-3 transition-transform" :class="symbolMenu && 'rotate-180'" style="color: var(--ink-2)" />
          </button>
          <Transition name="pop">
            <div
              v-if="symbolMenu"
              :id="symbolMenuId"
              ref="symbolPanel"
              tabindex="-1"
              role="listbox"
              :aria-label="t('dash.matrix.chart.perp')"
              class="outline-none float-panel absolute left-0 top-8 z-[var(--z-float)] max-h-80 w-56 overflow-y-auto p-1.5"
            >
              <button type="button"
                v-for="sym in availableSymbols"
                :key="sym"
                class="flex w-full cursor-pointer items-center justify-between rounded px-2 py-1.5 text-left text-xs transition-colors hover:bg-[var(--surface-1)]"
                :style="sym === currentSymbol ? { color: 'var(--accent)', fontWeight: 600 } : { color: 'var(--ink-1)' }"
                @click="selectSymbol(sym); symbolMenu = false"
              >
                <div class="flex items-center gap-2">
                  <CryptoLogo :symbol="sym" :size="16" />
                  <span class="num font-mono">{{ sym }}</span>
                </div>
                <span v-if="holdingSet.has(sym)" class="badge badge-accent !h-4 !px-1 !text-4xs">{{ t('dash.matrix.chart.holding') }}</span>
              </button>
            </div>
          </Transition>
        </div>

        <!-- 现价 / 涨跌 / ATR -->
        <span class="num font-mono text-sm font-bold" style="color: var(--ink-strong)">
          {{ currentPrice >= 100 ? currentPrice.toFixed(1) : currentPrice.toFixed(4) }}
        </span>
        <span class="num font-mono text-xs font-semibold"
              :class="liveChangePct === null ? '' : (liveChangePct >= 0 ? 'up' : 'down')">
          <template v-if="liveChangePct === null">--</template>
          <template v-else>{{ liveChangePct >= 0 ? '+' : '' }}{{ liveChangePct.toFixed(2) }}%</template>
        </span>
        <span class="dsh-pill hidden md:inline-flex">
          <span class="dsh-status-dot active" aria-hidden="true" />{{ t('dash.matrix.chart.live') }}
        </span>
        <span class="t-faint num font-mono hidden text-3xs lg:inline">1H ATR {{ currentAtr >= 100 ? '$' + currentAtr.toFixed(1) : (currentAtr * 100).toFixed(2) + '%' }}</span>
      </div>

      <!-- 右侧动作按钮（全屏与刷新）：在窄屏保持在第 1 行右侧，在宽屏并入末端 -->
      <div class="flex items-center gap-1.5 md:hidden ms-auto">
        <button type="button" class="btn btn-ghost btn-icon btn-sm" :title="t('common.refresh')" @click="loadCandles(false, true)">
          <RefreshCw :class="isLoading && 'animate-spin shrink-0'" />
        </button>
        <button type="button"
          class="btn btn-sm cursor-pointer"
          :class="isFullscreen ? 'btn-primary px-2.5 font-semibold text-xs gap-1 shadow-sm' : 'btn-ghost btn-icon'"
          :title="isFullscreen ? t('dash.matrix.chart.exitFullscreen') : t('dash.matrix.chart.fullscreen')"
          :aria-label="isFullscreen ? t('dash.matrix.chart.exitFullscreen') : t('dash.matrix.chart.fullscreen')"
          @click="isFullscreen = !isFullscreen"
        >
          <Minimize v-if="isFullscreen" class="h-3.5 w-3.5" />
          <span v-if="isFullscreen">{{ t('dash.matrix.chart.exitFullscreen') }}</span>
          <Maximize v-else class="h-3.5 w-3.5" />
        </button>
      </div>

      <!-- 周期、指标与试算组：宽屏居右，窄屏整齐铺于第 2 行 -->
      <div class="flex flex-wrap items-center gap-1.5 max-md:w-full max-md:justify-between">
        <span class="dsh-pill mr-1 hidden sm:inline-flex">
          <span class="text-[var(--ink-3)]">{{ t('dash.matrix.chart.barCloseIn') }}</span>
          <b class="num font-mono" style="color: var(--warn)">{{ candleCountdown }}</b>
        </span>

        <BaseSegmented
          :model-value="currentPeriod"
          :label="t('dash.matrix.chart.tf')"
          :options="periods.map((p) => ({ value: p.id, label: p.label }))"
          @update:model-value="(id: any) => selectPeriod(periods.find((p) => p.id === id))"
        />

        <div class="flex items-center gap-1.5">
          <!-- 指标菜单 -->
          <div class="indicator-dropdown-container relative">
            <button type="button"
              ref="indicatorTrigger"
              class="btn btn-sm"
              :class="showIndicatorMenu || activeIndicatorCount > 0 ? 'bg-[var(--accent-bg)] text-[var(--accent)] border border-[var(--accent-line)]' : 'btn-ghost'"
              :aria-expanded="showIndicatorMenu"
              aria-haspopup="dialog"
              :aria-controls="showIndicatorMenu ? indicatorMenuId : undefined"
              @click="showIndicatorMenu = !showIndicatorMenu"
            >
              <SlidersHorizontal />
              {{ t('dash.matrix.chart.indicators') }}
              <span v-if="activeIndicatorCount > 0" class="num">{{ activeIndicatorCount }}</span>
              <ChevronDown class="h-3 w-3 transition-transform" :class="showIndicatorMenu && 'rotate-180'" />
            </button>
            <Transition name="pop">
              <div v-if="showIndicatorMenu" :id="indicatorMenuId" ref="indicatorPanel" tabindex="-1" role="dialog" :aria-label="t('dash.matrix.chart.indicators')" class="outline-none float-panel absolute right-0 top-9 z-[var(--z-float)] max-h-[65vh] w-72 overflow-y-auto p-3 max-md:fixed max-md:inset-x-2 max-md:top-auto max-md:bottom-2 max-md:w-auto max-md:max-h-[70vh]">
                <p class="t-label mb-2">{{ t('dash.matrix.chart.indicatorHint') }}</p>
                <p class="t-label mb-1.5">{{ t('dash.matrix.chart.overlays') }}</p>
                <div class="mb-3 grid grid-cols-2 gap-1.5">
                  <button type="button"
                    v-for="ind in mainIndicators"
                    :key="ind.key"
                    class="flex cursor-pointer items-center justify-between rounded-md border px-2 py-1.5 text-xs font-semibold transition-colors"
                    :class="
                      activeIndicators[ind.key]
                        ? 'bg-[var(--accent-bg)] border-[var(--accent-line)] text-[var(--ink-1)]'
                        : 'bg-[var(--surface-1)] border-[var(--line-1)] text-[var(--ink-2)] hover:bg-[var(--surface-3)] hover:text-[var(--ink-1)]'
                    "
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
                  <button type="button"
                    v-for="ind in subIndicators"
                    :key="ind.key"
                    class="flex cursor-pointer items-center justify-between rounded-md border px-2 py-1.5 text-xs font-semibold transition-colors"
                    :class="
                      activeIndicators[ind.key]
                        ? 'bg-[var(--accent-bg)] border-[var(--accent-line)] text-[var(--ink-1)]'
                        : 'bg-[var(--surface-1)] border-[var(--line-1)] text-[var(--ink-2)] hover:bg-[var(--surface-3)] hover:text-[var(--ink-1)]'
                    "
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
          <button type="button"
            class="btn btn-sm"
            :class="simMode ? 'btn-primary' : 'btn-ghost'"
            :title="simMode ? t('dash.matrix.chart.sim.exit') : t('dash.matrix.chart.sim.enter')"
            @click="simMode = !simMode; if (simMode) initSimulation(); updatePriceLines()"
          >
            <Sliders />
            <span class="hidden sm:inline">{{ simMode ? t('dash.matrix.chart.sim.exit') : t('dash.matrix.chart.simulate') }}</span>
          </button>
        </div>

        <!-- 桌面端刷新与全屏按钮 -->
        <div class="hidden md:flex items-center gap-1.5">
          <button type="button" class="btn btn-ghost btn-icon btn-sm" :title="t('common.refresh')" @click="loadCandles(false, true)">
            <RefreshCw :class="isLoading && 'animate-spin shrink-0'" />
          </button>
          <button type="button"
            class="btn btn-sm cursor-pointer"
            :class="isFullscreen ? 'btn-primary px-2.5 font-semibold text-xs gap-1 shadow-sm' : 'btn-ghost btn-icon'"
            :title="isFullscreen ? t('dash.matrix.chart.exitFullscreen') : t('dash.matrix.chart.fullscreen')"
            :aria-label="isFullscreen ? t('dash.matrix.chart.exitFullscreen') : t('dash.matrix.chart.fullscreen')"
            @click="isFullscreen = !isFullscreen"
          >
            <Minimize v-if="isFullscreen" class="h-3.5 w-3.5" />
            <span v-if="isFullscreen">{{ t('dash.matrix.chart.exitFullscreen') }}</span>
            <Maximize v-else class="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </header>

    <!-- 图表画布：高度响应式。旧固定 560px 在移动端占满整屏，把持仓面板顶到首屏外
         且自身吃满手势；改 dvh 自适应。
         批 84：全屏态原先走 `calc(100vh - 108px)` —— **该魔数既过时又无效**：
           · 卡片全屏时是 `fixed inset-0` + flex 列，画布带 `flex-1`，
             `flex-basis: 0%` 会直接覆盖内联 `height`，实测画布高 = 视口 − 真实 chrome；
           · 而 `108` 与真实 chrome 对不上：顶栏在 1440/1024 宽时是 63px（chrome 65），
             在 700 宽时换行成 99px（chrome 101）—— 常量在两种方向上都错（偏差 43 / 7）。
         故删掉该内联高度，全屏一律交给 flex；并让全屏态也拿到 `flex-1 min-h-0`
         （非 fill 用法在全屏下同样铺满，短视口不会被 `min-h-[340px]` 顶出裁切）。 -->
    <div
      ref="chartContainer"
      class="relative w-full overflow-hidden"
      :class="isFullscreen ? 'flex-1 min-h-0' : props.chartHeight === '100%' ? 'flex-1 min-h-[340px] xl:min-h-0' : 'min-h-0'"
      :style="{ height: isFullscreen ? undefined : (props.chartHeight === '100%' ? undefined : (props.chartHeight || 'clamp(340px, 60vw, 560px)')) }"
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
        <button type="button" class="btn btn-ghost btn-sm" @click="resetSimulation">
          <RotateCcw />{{ t('dash.matrix.chart.sim.reset') }}
        </button>
        <button type="button" class="btn btn-primary btn-sm" @click="copySimulationSummary">
          <Check v-if="copied" style="color: var(--up)" />
          <Copy v-else />
          {{ copied ? t('common.copied') : t('dash.matrix.chart.sim.copy') }}
        </button>
      </div>
    </div>
  </div>
</template>

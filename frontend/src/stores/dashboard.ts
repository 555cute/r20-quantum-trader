import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { DashboardResponse, InstrumentFactor, PositionItem, PendingOrderItem } from '../types/dashboard'

export const useDashboardStore = defineStore('dashboard', () => {
  const activeTab = ref<'trading' | 'factors' | 'news' | 'lab' | 'history'>('trading')
  const data = ref<DashboardResponse | null>(null)
  const loading = ref<boolean>(false)
  const isRefreshing = ref<boolean>(false)
  const error = ref<string | null>(null)
  const lastUpdated = ref<Date | null>(null)
  const isConnected = ref<boolean>(true)
  const pollingTimer = ref<any>(null)
  const showAboutModal = ref<boolean>(false)

  // Getters
  const account = computed(() => data.value?.account || null)
  const positions = computed<PositionItem[]>(() => data.value?.positions_summary?.items || [])
  const pendingOrders = computed<PendingOrderItem[]>(() => data.value?.pending_orders || [])
  const exchange = computed(() => String(data.value?.exchange || ''))
  const environment = computed(() => String(data.value?.environment || ''))
  const quantityUnit = computed(() => String(data.value?.quantity_unit || 'base'))
  const exchangeLabel = computed(() => {
    const ex = exchange.value.toLowerCase()
    const mode = environment.value.toUpperCase()
    if (ex === 'binance') return `BINANCE USD-M${mode ? ' ' + mode : ''}`
    if (ex === 'okx') return `OKX${mode ? ' ' + mode : ''}`
    return mode || 'EXCHANGE'
  })
  const usesPairedConditional = computed(() =>
    positions.value.some((p) => (p.ordType || p.protection_mechanism) === 'paired_conditional')
  )
  const factors = computed<InstrumentFactor[]>(() => {
    const rawFactors = data.value?.factors || []
    const libInstruments: any[] = (data.value as any)?.factor_library?.instruments || (data.value as any)?.factor_library_snapshot?.instruments || []
    const libMap = new Map<string, any>()
    for (const li of libInstruments) {
      if (li?.instId) libMap.set(li.instId, li)
    }
    return rawFactors.map((f: any) => {
      const lib = libMap.get(f.instId) || {}
      const calc = lib.calculus_dynamics || {}
      const vol = lib.volatility_channel || {}
      const sm = lib.smart_money_derivatives || f.smart_money || {}
      const trend = lib.trend_momentum || {}
      return {
        ...f,
        adx_1h: f.adx_1h ?? trend.adx_1h,
        // ATR 只存在于快照的 volatility_channel 中；此前未透出，导致图表头部显示 $0.0
        // 且风控面板 atrMultiple 恒为 0（永远判定"ATR 非最优"）。
        atr_1h: f.atr_1h ?? vol.atr_1h,
        atr_14: f.atr_14 ?? vol.atr_14,
        atr_pct: f.atr_pct ?? vol.atr_pct ?? vol.atr_1h_pct,
        volatility_regime: vol.volatility_regime,
        calculus: {
          velocity_1h: calc.velocity,
          accel_1h: calc.acceleration,
          jerk_1h: calc.jerk,
          impulse_1h: calc.impulse,
          energy_1h: (lib.definite_integrals || {}).energy_integral,
          action_area_1h: (lib.definite_integrals || {}).deviation_area_integral,
          state_1h: calc.regime,
        },
        smart_money: {
          weighted_long_pct: sm.weighted_long_pct ?? f.smart_money?.weighted_long_pct,
          net_flow_usdt: sm.smart_money_flow_usd ?? f.smart_money?.net_flow_usdt,
          top_win_rate: sm.top_win_rate,
        },
        decision: f.decision || {
          action: f.action,
          confidence: f.confidence,
          leverage: f.leverage,
          margin_usdt: f.margin_usdt,
          entry_price: f.entry_price,
          take_profit_price: f.take_profit_price,
          stop_loss_price: f.stop_loss_price,
          risk_reward_ratio: f.risk_reward_ratio || f.rr_ratio,
          summary_reason: f.decision?.summary_reason || f.reason,
        },
      }
    })
  })
  const macroAssessment = computed(() => data.value?.macro_assessment || '全市场宏观多周期多因子矩阵扫描中...')
  // 不再伪造默认模型名：数据缺失时返回空对象，由视图显式呈现「未配置」，避免界面谎报正在使用的模型。
  const llmRuntime = computed(() => data.value?.llm_runtime || {})
  const logs = computed(() => data.value?.logs || [])
  const isStale = computed(() => data.value?.is_stale ?? false)

  // Actions
  async function fetchDashboard(silent = false) {
    if (!silent) {
      isRefreshing.value = true
    }
    try {
      const resp = await fetch(`/api/all?_t=${Date.now()}`, {
        headers: {
          'Accept-Encoding': 'gzip, deflate, br',
        },
      })
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}: ${resp.statusText}`)
      }
      const json: DashboardResponse = await resp.json()
      data.value = json
      lastUpdated.value = new Date()
      isConnected.value = true
      error.value = null
    } catch (err: any) {
      console.error('[DashboardStore] fetch failed:', err)
      error.value = err.message || '获取数据失败'
      isConnected.value = false
    } finally {
      loading.value = false
      if (!silent) {
        setTimeout(() => {
          isRefreshing.value = false
        }, 300)
      }
    }
  }

  function startPolling(intervalMs = 3000) {
    stopPolling()
    fetchDashboard(false)
    pollingTimer.value = setInterval(() => {
      fetchDashboard(true)
    }, intervalMs)
  }

  function stopPolling() {
    if (pollingTimer.value) {
      clearInterval(pollingTimer.value)
      pollingTimer.value = null
    }
  }

  return {
    activeTab,
    data,
    loading,
    isRefreshing,
    error,
    lastUpdated,
    isConnected,
    account,
    exchange,
    environment,
    quantityUnit,
    exchangeLabel,
    usesPairedConditional,
    positions,
    pendingOrders,
    factors,
    macroAssessment,
    llmRuntime,
    logs,
    isStale,
    showAboutModal,
    fetchDashboard,
    startPolling,
    stopPolling,
  }
})

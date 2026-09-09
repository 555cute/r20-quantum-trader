import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { DashboardResponse, InstrumentFactor, PositionItem, PendingOrderItem } from '../types/dashboard'

export const useDashboardStore = defineStore('dashboard', () => {
  const activeTab = ref<'trading' | 'factors' | 'news' | 'lab' | 'history'>('trading')
  const data = ref<DashboardResponse | null>(null)
  const loading = ref<boolean>(true)
  const isRefreshing = ref<boolean>(false)
  const error = ref<string | null>(null)
  const lastUpdated = ref<Date | null>(null)
  const isConnected = ref<boolean>(true)
  const pollingTimer = ref<number | null>(null)
  const showAboutModal = ref<boolean>(false)
  let fetchInFlight = false

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
    return mode || ''
  })
  const usesPairedConditional = computed(() =>
    positions.value.some((p) => (p.ordType || p.protection_mechanism) === 'paired_conditional'),
  )
  const factors = computed<InstrumentFactor[]>(() => {
    const rawFactors = data.value?.factors || []
    return rawFactors.map((f) => {
      const volRaw = f.vol24h
      const vol24h =
        volRaw === null || volRaw === undefined
          ? null
          : Number.isFinite(Number(volRaw))
            ? Number(volRaw)
            : null
      return {
        ...f,
        vol24h,
        decision: f.decision || {
          action: f.action,
          confidence: f.confidence,
          leverage: f.leverage,
          margin_usdt: f.margin_usdt,
          entry_price: f.entry_price,
          take_profit_price: f.take_profit_price,
          stop_loss_price: f.stop_loss_price,
          risk_reward_ratio: f.risk_reward_ratio || f.rr_ratio,
          summary_reason: f.reason,
        },
      }
    })
  })
  const macroAssessment = computed(() => data.value?.macro_assessment || '全市场宏观多周期多因子矩阵扫描中...')
  const llmRuntime = computed(() => data.value?.llm_runtime || {})
  const logs = computed(() => [...(data.value?.logs || [])].reverse())
  const isStale = computed(() => data.value?.is_stale ?? false)

  async function fetchDashboard(silent = false) {
    if (fetchInFlight) return
    fetchInFlight = true
    if (!silent && data.value == null) {
      loading.value = true
    } else if (!silent) {
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
    } catch (err: unknown) {
      console.error('[DashboardStore] fetch failed:', err)
      error.value = err instanceof Error ? err.message : '获取数据失败'
      isConnected.value = false
    } finally {
      loading.value = false
      if (!silent && data.value != null) {
        setTimeout(() => {
          isRefreshing.value = false
        }, 300)
      }
      fetchInFlight = false
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

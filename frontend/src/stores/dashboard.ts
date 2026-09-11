import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { DashboardResponse, InstrumentFactor, PositionItem, PendingOrderItem } from '../types/dashboard'
import { VENUE_KEYS, normalizeEnv, normalizeVenue, venueOfRecord, type VenueEnv, type VenueKey } from '../utils/venueMeta'
import { venueOfSymbol } from '../utils/instId'

/**
 * US-002 · 大屏聚合 store：**场所对等**的数据视图。
 *
 * 平权改造要点（旧版把「无 venue 字段」的记录一律默认成 OKX，是单所偏置根因）：
 *   - `venueKey` 一律经 `venueOfRecord`（显式 venue/exchange 优先，其次合约码
 *     格式反推）得出，真未知 → null，**绝不默认任何所**；
 *   - 逐所统计恒定遍历 `VENUE_KEYS`，另设 `unknown` 桶如实暴露归属不明；
 *   - 轮询失败按指数退避重试并在 `error` 中说明，页面隐藏时自动暂停，
 *     三所数据始终来自同一次 `/api/all` 聚合快照（不存在新旧错配）。
 */

/** 带场所归属的记录视图（原字段全量保留，只增不改） */
export type VenueTagged<T> = T & {
  venueKey: VenueKey | null
  /** 归一后的资金环境档；后端未标 → null（渲染层显「—」） */
  envKey: VenueEnv | null
}

/** 逐所计数（三所恒定 + unknown 桶） */
export type VenueCounts = Record<VenueKey | 'unknown', number>

/** 跨所健康行（/api/all cross_venue.venues 归一） */
export interface CrossVenueHealthRow {
  key: VenueKey
  okCount: number
  failCount: number
  avgMs: number | null
  testnet: boolean
  present: boolean
}

/** 跨所逐币对照行（by_asset 优先，symbols 作旧快照兼容回退） */
export interface CrossVenueAssetRow {
  base: string
  okxLast: number | null
  binanceLast: number | null
  gateLast: number | null
  binanceBasisPct: number | null
  gateBasisPct: number | null
  binanceLs: number | null
  gateLs: number | null
  binanceFundingPct: number | null
  gateFundingPct: number | null
}

function finiteOrNull(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function tagRecord(item: any): { venueKey: VenueKey | null; envKey: VenueEnv | null } {
  return {
    venueKey: venueOfRecord(item, venueOfSymbol),
    envKey: item?.environment || item?.account_mode || item?.is_simulated !== undefined
      ? normalizeEnv(item.environment ?? item.account_mode ?? (item.is_simulated ? 'demo' : 'live'))
      : null,
  }
}

function emptyCounts(): VenueCounts {
  return { okx: 0, binance: 0, gate: 0, unknown: 0 }
}

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
  /** 连续失败次数（指数退避依据）与当前轮询节拍 */
  const consecutiveFailures = ref<number>(0)
  const baseIntervalMs = ref<number>(3000)
  const pollingPaused = ref<boolean>(false)

  // Getters
  const account = computed(() => data.value?.account || null)
  const positions = computed<PositionItem[]>(() => data.value?.positions_summary?.items || [])
  const pendingOrders = computed<PendingOrderItem[]>(() => data.value?.pending_orders || [])

  /** 持仓（带场所归属）：三所同构，未知归属显式 null。 */
  const positionsView = computed<VenueTagged<PositionItem>[]>(() =>
    positions.value.map((p: any) => ({ ...p, ...tagRecord(p) })))
  /** 挂单（带场所归属）。 */
  const ordersView = computed<VenueTagged<PendingOrderItem>[]>(() =>
    pendingOrders.value.map((o: any) => ({ ...o, ...tagRecord(o) })))

  /** 逐所持仓/挂单计数（恒定 3 所 + unknown 桶，供筛选器计数徽章共用）。 */
  const venueBreakdown = computed(() => {
    const pos = emptyCounts()
    const ord = emptyCounts()
    for (const p of positionsView.value) pos[p.venueKey ?? 'unknown'] += 1
    for (const o of ordersView.value) ord[o.venueKey ?? 'unknown'] += 1
    return { positions: pos, orders: ord }
  })

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
  // 巡检日志倒序展示：最新在前（后端按时间正序 tail，此处仅显示层反转）
  const logs = computed(() => [...(data.value?.logs || [])].reverse())
  const isStale = computed(() => data.value?.is_stale ?? false)

  /* ———— 跨所协调快照（/api/all cross_venue，三所对等消费面） ———— */

  const crossVenue = computed<any>(() => (data.value as any)?.cross_venue || null)

  /** 三所取数健康（恒定 3 行，顺序 = VENUE_KEYS）。 */
  const crossVenueHealth = computed<CrossVenueHealthRow[]>(() => {
    const v = crossVenue.value?.venues || {}
    return VENUE_KEYS.map((key) => {
      const x = v[key]
      if (!x || typeof x !== 'object') {
        return { key, okCount: 0, failCount: 0, avgMs: null, testnet: false, present: false }
      }
      return {
        key,
        okCount: Array.isArray(x.ok) ? x.ok.length : 0,
        failCount: x.failed && typeof x.failed === 'object' ? Object.keys(x.failed).length : 0,
        avgMs: finiteOrNull(x.avg_ms),
        testnet: !!x.testnet,
        present: true,
      }
    })
  })

  /** 逐币三所对照（by_asset 优先，symbols 兼容旧快照）。 */
  const crossVenueAssets = computed<Record<string, CrossVenueAssetRow>>(() => {
    const cv = crossVenue.value || {}
    const src = { ...(cv.symbols || {}), ...(cv.by_asset || {}) }
    const out: Record<string, CrossVenueAssetRow> = {}
    for (const [raw, row] of Object.entries<any>(src)) {
      const base = normalizeVenue(raw) ? '' : String(raw || '').toUpperCase().replace(/[-_]/g, '')
      if (!base || !row || typeof row !== 'object') continue
      out[base] = {
        base,
        okxLast: finiteOrNull(row.okx_last ?? row.okx),
        binanceLast: finiteOrNull(row.bin_last),
        gateLast: finiteOrNull(row.gate_last),
        binanceBasisPct: finiteOrNull(row.bin_basis_pct),
        gateBasisPct: finiteOrNull(row.gate_basis_pct),
        binanceLs: finiteOrNull(row.bin_ls),
        gateLs: finiteOrNull(row.gate_ls),
        binanceFundingPct: finiteOrNull(row.bin_funding_pct),
        gateFundingPct: finiteOrNull(row.gate_funding_pct),
      }
    }
    return out
  })

  function crossVenueRow(symbol: unknown): CrossVenueAssetRow | null {
    const key = String(symbol ?? '').toUpperCase().replace(/[-_]/g, '').replace(/USDT$/, '')
    return crossVenueAssets.value[key] || null
  }

  /** 组合风险占用（portfolio_risk，三所合并口径） */
  const portfolioRisk = computed<any>(() => (data.value as any)?.portfolio_risk || null)

  // Actions
  async function fetchDashboard(silent = false) {
    if (!silent) {
      isRefreshing.value = true
    }
    loading.value = true
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
      consecutiveFailures.value = 0
    } catch (err: any) {
      console.error('[DashboardStore] fetch failed:', err)
      error.value = err.message || '获取数据失败'
      isConnected.value = false
      consecutiveFailures.value += 1
      // 失败后自动降频（最长 30s），避免持续故障时把网关打满
      if (pollingTimer.value) restartTimer()
    } finally {
      loading.value = false
      if (!silent) {
        setTimeout(() => {
          isRefreshing.value = false
        }, 300)
      }
    }
  }

  /** 当前节拍：无故障 = 基准间隔；连续失败按 2 的幂退避，封顶 30s。 */
  function currentIntervalMs(): number {
    const base = baseIntervalMs.value || 3000
    const n = consecutiveFailures.value
    if (n <= 0) return base
    return Math.min(base * 2 ** Math.min(n, 4), 30_000)
  }

  function restartTimer() {
    if (pollingTimer.value) clearInterval(pollingTimer.value)
    pollingTimer.value = setInterval(() => {
      if (pollingPaused.value) return
      fetchDashboard(true)
    }, currentIntervalMs())
  }

  /** 页面不可见时暂停轮询（三所数据同等「冻结」，回到前台立即补一次）。 */
  function onVisibilityChange() {
    const hidden = typeof document !== 'undefined' && document.visibilityState === 'hidden'
    pollingPaused.value = hidden
    if (!hidden && pollingTimer.value) void fetchDashboard(true)
  }

  function startPolling(intervalMs = 3000) {
    stopPolling()
    baseIntervalMs.value = intervalMs
    fetchDashboard(false)
    restartTimer()
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibilityChange)
      pollingPaused.value = document.visibilityState === 'hidden'
    }
  }

  function stopPolling() {
    if (pollingTimer.value) {
      clearInterval(pollingTimer.value)
      pollingTimer.value = null
    }
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', onVisibilityChange)
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
    positions,
    pendingOrders,
    positionsView,
    ordersView,
    venueBreakdown,
    factors,
    macroAssessment,
    llmRuntime,
    logs,
    isStale,
    crossVenue,
    crossVenueHealth,
    crossVenueAssets,
    crossVenueRow,
    portfolioRisk,
    consecutiveFailures,
    pollingPaused,
    showAboutModal,
    fetchDashboard,
    startPolling,
    stopPolling,
  }
})

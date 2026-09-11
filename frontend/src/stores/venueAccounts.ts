import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useApi } from '../composables/useApi'
import { useAuthStore } from './auth'
import {
  VENUE_KEYS, envLabel, isSandboxEnv, numOrNull, venueBadge, venueBrandColor, venueLabel,
  normalizeEnv, type VenueEnv, type VenueKey,
} from '../utils/venueMeta'

/**
 * US-002 · 三所账户卡状态：**环境优先、场所对等**。
 *
 * 平权铁律（本 store 是账户面的单一事实源，组件不得自行推断）：
 *   - 场所键/顺序一律取自 `utils/venueMeta.VENUE_KEYS`，任何所都不是默认值；
 *   - 后端单端点聚合返回三所（/api/v1/venue_accounts），前端逐所独立容错：
 *     某所缺席/异常只污染该所的 `venueErrors`，其余两所数据照常呈现；
 *   - 未知≠0：缺值归一为 null，渲染层显「—」；
 *   - 请求去重 + 短 TTL 缓存：多组件同时挂载只打一次网络，且三所同批更新，
 *     杜绝「A 所数据新、B 所数据旧」的错配。
 */

export type { VenueEnv, VenueKey } from '../utils/venueMeta'
export type VenueStatus = 'ready' | 'unavailable' | 'not_implemented' | 'degraded'

export interface PortfolioSummary {
  environment: VenueEnv
  total_equity: number
  total_available: number
  margin_used: number
  utilization_pct: number
  risk_level: string
  positions_count: number
  open_orders_count: number
  active_venues_count: number
  reporting_venues: string[]
  asset_distribution: Partial<Record<VenueKey, { equity: number; share_pct: number }>>
}

export interface VenueAccount {
  status: VenueStatus
  /** 未知一律 null（后端铁律：绝不填 0 冒充）；渲染层显「—」 */
  equity: number | null
  available: number | null
  positions_count: number | null
  open_orders_count: number | null
  last_sync_ts: number | null
  reason: string
  /** 该所自报环境档（缺则跟随面板环境） */
  environment?: string
}

/** 逐所对等视图：恒定 3 条，顺序 = VENUE_KEYS */
export interface VenueRow {
  key: VenueKey
  label: string
  compact: string
  brand: string
  tone: string
  badgeCls: string
  account: VenueAccount | null
  /** 该所独立错误/降级原因（null = 无异常） */
  error: string | null
  /** 后端本次响应是否包含该所（false = 数据缺席，非「余额 0」） */
  present: boolean
}

/** 资产分布条行（三所恒定同构，share 未知为 null） */
export interface AssetShareRow {
  key: VenueKey
  label: string
  brand: string
  equity: number | null
  sharePct: number | null
}

const ENV_KEY = 'r20.venueAccounts.environment'
/** 多组件同挂载/同轮询的去重窗口 */
const CACHE_TTL_MS = 2_000

/** 后端可能缺键/给畸形值：逐字段归一，未知恒为 null */
function normalizeAccount(raw: unknown): VenueAccount | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const r = raw as Record<string, any>
  const status = String(r.status || '').trim().toLowerCase()
  return {
    status: (['ready', 'unavailable', 'not_implemented', 'degraded'].includes(status)
      ? status : 'degraded') as VenueStatus,
    equity: numOrNull(r.equity),
    available: numOrNull(r.available),
    positions_count: numOrNull(r.positions_count),
    open_orders_count: numOrNull(r.open_orders_count),
    last_sync_ts: numOrNull(r.last_sync_ts),
    reason: String(r.reason || ''),
    environment: r.environment ? String(r.environment) : undefined,
  }
}

export const useVenueAccountsStore = defineStore('venueAccounts', () => {
  const environment = ref<VenueEnv>(localStorage.getItem(ENV_KEY) === 'live' ? 'live' : 'demo')
  /** 逐所恒定同构（Partial 仅表达「后端可能缺该所」，遍历面一律走 venueRows） */
  const venues = ref<Partial<Record<VenueKey, VenueAccount>> | null>(null)
  const portfolioSummary = ref<PortfolioSummary | null>(null)
  const capturedAt = ref<number | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const needsAuth = ref(false)
  /** 逐所独立错误位：某所失败绝不连带清空其余两所 */
  const venueErrors = ref<Record<VenueKey, string | null>>({ okx: null, binance: null, gate: null })
  /** 本次响应真实带回的所集合（区分「数据缺席」与「余额为 0」） */
  const reportedVenues = ref<VenueKey[]>([])
  const { api } = useApi()

  let inflight: Promise<void> | null = null
  let lastFetchAt = 0

  /** 逐所归一：把后端 payload 摊平成三所同构记录，缺席所显式记错。 */
  function applyPayload(payload: Partial<Record<VenueKey, unknown>>, env: VenueEnv): void {
    const next: Partial<Record<VenueKey, VenueAccount>> = {}
    const errs = { okx: null, binance: null, gate: null } as Record<VenueKey, string | null>
    const reported: VenueKey[] = []
    for (const key of VENUE_KEYS) {
      const acc = normalizeAccount(payload?.[key])
      if (!acc) {
        errs[key] = '本次响应未包含该所数据'
        continue
      }
      // 环境错配（后端标了另一档）→ 该所降级提示，但数据仍如实呈现
      if (acc.environment && normalizeEnv(acc.environment) !== env) {
        acc.status = 'degraded'
        acc.reason = `${acc.reason || '状态'} · 环境错配 ${envLabel(acc.environment)}`
      }
      next[key] = acc
      reported.push(key)
      if (acc.status === 'unavailable' || acc.status === 'degraded') {
        errs[key] = acc.reason || '该所账户数据不可用'
      }
    }
    venues.value = next
    venueErrors.value = errs
    reportedVenues.value = reported
  }

  async function fetchFresh(): Promise<void> {
    loading.value = true
    error.value = null
    // 公开视图（/trading）不走 admin 路由守卫——若内存无 token 但 localStorage
    // 存有会话，先恢复再请求，保证已登录用户刷新实盘矩阵页仍可读真实账户。
    const auth = useAuthStore()
    if (!auth.token) auth.restoreSession()
    const env = environment.value
    try {
      const d = await api<{
        environment: VenueEnv
        venues: Partial<Record<VenueKey, unknown>>
        portfolio_summary?: PortfolioSummary
        captured_at_ms: number
      }>(`/api/v1/venue_accounts?environment=${env}`)
      // 响应回来时环境已再次切换 → 丢弃过期响应（三所同批，绝不部分应用）
      if (environment.value !== env) return
      applyPayload(d.venues || {}, env)
      portfolioSummary.value = d.portfolio_summary || null
      capturedAt.value = numOrNull(d.captured_at_ms)
      needsAuth.value = false
      lastFetchAt = Date.now()
    } catch (e) {
      if (environment.value !== env) return
      const msg = e instanceof Error ? e.message : String(e)
      // 整体失败：三所**同等**置空（不出现「只清空 Gate」这类不对称塌陷）
      venues.value = null
      portfolioSummary.value = null
      capturedAt.value = null
      reportedVenues.value = []
      venueErrors.value = { okx: msg, binance: msg, gate: msg }
      needsAuth.value = /\(401\)|401|会话|登录/.test(msg)
      error.value = msg
    } finally {
      loading.value = false
    }
  }

  /** 拉取三所账户（带去重窗口：并发/密集轮询只打一次网络）。 */
  async function refresh(force = false): Promise<void> {
    if (inflight) return inflight
    if (!force && venues.value && Date.now() - lastFetchAt < CACHE_TTL_MS) return
    inflight = fetchFresh().finally(() => { inflight = null })
    return inflight
  }

  /** 环境切换：三所同批换挡重新拉取；不清空旧数据，杜绝页面闪烁塌陷。 */
  function setEnvironment(env: VenueEnv): void {
    if (environment.value === env) return
    environment.value = env
    localStorage.setItem(ENV_KEY, env)
    void refresh(true)
  }

  /** 恒定 3 行的对等视图（顺序 = VENUE_KEYS，组件直接 v-for，不再各自拼顺序）。 */
  const venueRows = computed<VenueRow[]>(() =>
    VENUE_KEYS.map((key) => {
      const badge = venueBadge(key)
      return {
        key,
        label: badge.label,
        compact: badge.compact,
        brand: badge.brand,
        tone: badge.tone,
        badgeCls: badge.cls,
        account: venues.value?.[key] ?? null,
        error: venueErrors.value[key],
        present: !!venues.value?.[key],
      }
    }),
  )

  /** 三所资产分布（分布条/占比文案共用；未知显「—」，绝不补 0）。 */
  const assetDistribution = computed<AssetShareRow[]>(() => {
    const dist = portfolioSummary.value?.asset_distribution || {}
    return VENUE_KEYS.map((key) => ({
      key,
      label: venueLabel(key),
      brand: venueBrandColor(key),
      equity: numOrNull(dist[key]?.equity),
      sharePct: numOrNull(dist[key]?.share_pct),
    }))
  })

  /** 已接入（有真实权益数据）的场所数：以三所同构口径统计，不偏向任何所。 */
  const reportingVenueCount = computed(() => reportedVenues.value.length)
  const isLive = computed(() => !isSandboxEnv(environment.value))
  /** 三所全部缺席 = 面板整体不可用（区别于「某所未配凭证」） */
  const allUnavailable = computed(() => venueRows.value.every((r) => !r.present))

  return {
    environment, venues, portfolioSummary, capturedAt, loading, error, needsAuth,
    venueErrors, reportedVenues,
    refresh, setEnvironment,
    venueRows, assetDistribution, reportingVenueCount, isLive, allUnavailable,
    // 供组件复用的常量（单一事实源，避免组件内硬编码场所顺序）
    VENUE_KEYS,
  }
})

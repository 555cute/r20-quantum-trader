import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useApi } from '../composables/useApi'
import { useAuthStore } from './auth'
import { listingMeta, type ListingVenueStatus } from '../utils/listingMeta'
import { VENUE_KEYS, normalizeEnv, venueLabel, type VenueEnv, type VenueKey } from '../utils/venueMeta'

/**
 * US-007 前端配套 · 合约目录对账快照（环境优先，与 venueAccounts 同轴同刷新）。
 *
 * US-002 平权改造：
 *   - 场所键/顺序统一取自 `utils/venueMeta`（本文件不再自定义 VenueKey）；
 *   - 逐所独立容错：某所目录缺席只置该所 `listingErrors`，其余两所照常展示；
 *   - 请求去重 + 短 TTL 缓存，与账户面板同批发起，杜绝「两新一旧」错配。
 */

export type { VenueKey } from '../utils/venueMeta'

/** 逐所对等视图：恒定 3 条，顺序 = VENUE_KEYS */
export interface ListingRow {
  key: VenueKey
  label: string
  status: ListingVenueStatus | null
  /** listingMeta 归一后的展示口径（tone/数量/原因） */
  meta: ReturnType<typeof listingMeta>
  /** 本次响应是否包含该所（false = 未对账，非「0 个合约」） */
  present: boolean
  error: string | null
}

const CACHE_TTL_MS = 5_000

export const useListingStatusStore = defineStore('listingStatus', () => {
  const environment = ref<VenueEnv>('demo')
  const venues = ref<Partial<Record<VenueKey, ListingVenueStatus>> | null>(null)
  const capturedAt = ref<number | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const needsAuth = ref(false)
  const listingErrors = ref<Record<VenueKey, string | null>>({ okx: null, binance: null, gate: null })
  const { api } = useApi()

  let inflight: Promise<void> | null = null
  let lastFetchAt = 0

  async function fetchFresh(env: VenueEnv): Promise<void> {
    loading.value = true
    error.value = null
    const auth = useAuthStore()
    if (!auth.token) auth.restoreSession()
    try {
      const d = await api<{
        environment: VenueEnv
        venues: Partial<Record<VenueKey, ListingVenueStatus>>
        captured_at_ms: number
      }>(`/api/v1/listing_status?environment=${env}`)
      if (environment.value !== env) return // 过期响应丢弃（三所同批应用）
      const payload = d.venues || {}
      const errs = { okx: null, binance: null, gate: null } as Record<VenueKey, string | null>
      const next: Partial<Record<VenueKey, ListingVenueStatus>> = {}
      for (const key of VENUE_KEYS) {
        const s = payload[key]
        if (!s || typeof s !== 'object') {
          errs[key] = '本次响应未包含该所目录对账'
          continue
        }
        next[key] = s
        const m = listingMeta(s)
        if (m.tone !== 'ok') errs[key] = m.reason || '目录对账未完成'
      }
      venues.value = next
      listingErrors.value = errs
      capturedAt.value = Number.isFinite(d.captured_at_ms) ? d.captured_at_ms : null
      needsAuth.value = false
      lastFetchAt = Date.now()
    } catch (e) {
      if (environment.value !== env) return
      const msg = e instanceof Error ? e.message : String(e)
      // 整体失败：三所同等置空，绝不出现单所塌陷
      venues.value = null
      capturedAt.value = null
      listingErrors.value = { okx: msg, binance: msg, gate: msg }
      needsAuth.value = /\(401\)|401|会话|登录/.test(msg)
      error.value = msg
    } finally {
      loading.value = false
    }
  }

  async function refresh(env?: VenueEnv | string, force = false): Promise<void> {
    if (env) environment.value = normalizeEnv(env)
    if (inflight) return inflight
    if (!force && venues.value && Date.now() - lastFetchAt < CACHE_TTL_MS) return
    inflight = fetchFresh(environment.value).finally(() => { inflight = null })
    return inflight
  }

  /** 恒定 3 行的对等视图（组件直接 v-for，顺序与账户卡完全一致）。 */
  const listingRows = computed<ListingRow[]>(() =>
    VENUE_KEYS.map((key) => {
      const status = venues.value?.[key] ?? null
      return {
        key,
        label: venueLabel(key),
        status,
        meta: listingMeta(status),
        present: !!status,
        error: listingErrors.value[key],
      }
    }),
  )

  /** 已完成对账的所数（三所同构口径，用于「N/3 所已对账」文案）。 */
  const reconciledCount = computed(
    () => listingRows.value.filter((r) => r.meta.tone === 'ok').length,
  )

  return {
    environment, venues, capturedAt, loading, error, needsAuth, listingErrors,
    refresh, listingRows, reconciledCount, VENUE_KEYS,
  }
})

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '../composables/useApi'
import { useAuthStore } from './auth'

/** US-005 · 三所账户卡状态：环境优先（先 demo/live，再逐所读取）。 */

export type VenueEnv = 'demo' | 'live'
export type VenueStatus = 'ready' | 'unavailable' | 'not_implemented' | 'degraded'
export type VenueKey = 'okx' | 'gate' | 'binance'

export interface VenueAccount {
  status: VenueStatus
  /** 未知一律 null（后端铁律：绝不填 0 冒充）；渲染层显「—」 */
  equity: number | null
  available: number | null
  positions_count: number | null
  open_orders_count: number | null
  last_sync_ts: number | null
  reason: string
}

const ENV_KEY = 'r20.venueAccounts.environment'

export const useVenueAccountsStore = defineStore('venueAccounts', () => {
  const environment = ref<VenueEnv>(localStorage.getItem(ENV_KEY) === 'live' ? 'live' : 'demo')
  const venues = ref<Partial<Record<VenueKey, VenueAccount>> | null>(null)
  const capturedAt = ref<number | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const needsAuth = ref(false)
  const { api } = useApi()

  async function refresh(): Promise<void> {
    loading.value = true
    error.value = null
    // 公开视图（/trading）不走 admin 路由守卫——若内存无 token 但 localStorage
    // 存有会话，先恢复再请求，保证已登录用户刷新实盘矩阵页仍可读真实账户。
    const auth = useAuthStore()
    if (!auth.token) auth.restoreSession()
    try {
      const d = await api<{
        environment: VenueEnv
        venues: Partial<Record<VenueKey, VenueAccount>>
        captured_at_ms: number
      }>(`/api/v1/venue_accounts?environment=${environment.value}`)
      venues.value = d.venues
      capturedAt.value = d.captured_at_ms
      needsAuth.value = false
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      venues.value = null
      capturedAt.value = null
      needsAuth.value = /\(401\)|401|会话|登录/.test(msg)
      error.value = msg
    } finally {
      loading.value = false
    }
  }

  /** 环境切换：立即换轴并重新拉取；旧环境数据先行清空，防串显。 */
  function setEnvironment(env: VenueEnv): void {
    if (environment.value === env) return
    environment.value = env
    localStorage.setItem(ENV_KEY, env)
    venues.value = null
    capturedAt.value = null
    void refresh()
  }

  return { environment, venues, capturedAt, loading, error, needsAuth, refresh, setEnvironment }
})

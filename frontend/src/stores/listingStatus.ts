import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useApi } from '../composables/useApi'
import { useAuthStore } from './auth'
import type { ListingVenueStatus } from '../utils/listingMeta'

/** US-007 前端配套 · 合约目录对账快照（环境优先，与 venueAccounts 同轴同刷新）。 */

export type VenueKey = 'okx' | 'gate' | 'binance'

export const useListingStatusStore = defineStore('listingStatus', () => {
  const environment = ref<'demo' | 'live'>('demo')
  const venues = ref<Partial<Record<VenueKey, ListingVenueStatus>> | null>(null)
  const capturedAt = ref<number | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const needsAuth = ref(false)
  const { api } = useApi()

  async function refresh(env?: 'demo' | 'live'): Promise<void> {
    if (env) environment.value = env
    loading.value = true
    error.value = null
    const auth = useAuthStore()
    if (!auth.token) auth.restoreSession()
    try {
      const d = await api<{
        environment: 'demo' | 'live'
        venues: Partial<Record<VenueKey, ListingVenueStatus>>
        captured_at_ms: number
      }>(`/api/v1/listing_status?environment=${environment.value}`)
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

  return { environment, venues, capturedAt, loading, error, needsAuth, refresh }
})

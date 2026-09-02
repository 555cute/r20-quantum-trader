import { defineStore } from 'pinia'
import { computed, onUnmounted, ref } from 'vue'
import { api } from '../services/http'

export const useTerminalStore = defineStore('terminal', () => {
  const data = ref<any>({})
  const loading = ref(false)
  const latency = ref<number | null>(null)
  const error = ref('')
  let timer: number | undefined

  const account = computed(() => data.value.account || {})
  const positions = computed(() => data.value.positions_summary?.items || [])
  const orders = computed(() => data.value.pending_orders || [])
  const trades = computed(() => data.value.trades || [])
  const health = computed(() => {
    const raw = String(data.value.data_health?.status || (error.value ? 'OFFLINE' : 'CONNECTING')).toUpperCase()
    if (raw === 'STALE' && !Object.keys(account.value).length) return 'OFFLINE'
    return raw
  })

  async function refresh() {
    if (loading.value) return
    loading.value = true; error.value = ''
    const start = performance.now()
    try { data.value = await api(`/api/all?t=${Date.now()}`); latency.value = performance.now() - start }
    catch (e: any) { error.value = e.message || '数据加载失败'; latency.value = performance.now() - start }
    finally { loading.value = false }
  }
  function start() {
    stop(); refresh()
    const loop = async () => { if (!document.hidden) await refresh(); timer = window.setTimeout(loop, 4000) }
    timer = window.setTimeout(loop, 4000)
  }
  function stop() { if (timer) window.clearTimeout(timer) }
  onUnmounted(stop)
  return { data, loading, latency, error, account, positions, orders, trades, health, refresh, start, stop }
})

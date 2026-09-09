import { ref } from 'vue'
import { useAuthStore } from '../stores/auth'

export function useApi() {
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
    const auth = useAuthStore()
    loading.value = true
    error.value = null
    try {
      let resp: Response
      try {
        resp = await fetch(path, {
          ...options,
          headers: {
            'Content-Type': 'application/json',
            ...(auth.token ? { 'X-R20-Session': auth.token } : {}),
            ...(options.headers || {}),
          },
        })
      } catch {
        throw new Error('网络错误，请稍后重试')
      }
      let data: any = {}
      try {
        data = await resp.json()
      } catch {
        // empty body
      }
      if (resp.status === 401 && auth.token) {
        auth.logout()
        throw new Error('会话已过期，请重新登录')
      }
      if (!resp.ok) {
        const raw = data.detail ?? data.message
        const detail = Array.isArray(raw)
          ? raw.map((x: any) => `${(x.loc || []).slice(1).join('.') || '请求'}：${x.msg}`).join('；')
          : raw
        throw new Error(detail || `HTTP ${resp.status}`)
      }
      return data as T
    } catch (e: any) {
      error.value = e.message || String(e)
      throw e
    } finally {
      loading.value = false
    }
  }

  return { loading, error, api }
}

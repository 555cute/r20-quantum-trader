/**
 * 全局 HTTP 层 —— fetch 封装 + 会话头 + 错误归一
 * 迁移自旧 useApi()，行为保持：401 登出、FastAPI 422 detail 数组转中文提示。
 */
import { ref } from 'vue';
import { useAuthStore } from '../stores/auth';

export class HttpError extends Error {
  status: number;
  constructor(message: string, status = 0) {
    super(message);
    this.name = 'HttpError';
    this.status = status;
  }
}

/** 把 FastAPI 的 detail（字符串或 422 数组）归一为人话 */
export function normalizeDetail(data: any, status: number): string {
  const detail = data?.detail ?? data?.message;
  if (Array.isArray(detail)) {
    return detail
      .map((x: any) => `${(x.loc || []).slice(1).join('.') || '请求'}：${x.msg}`)
      .join('；');
  }
  if (typeof detail === 'string' && detail) return detail;
  return `HTTP ${status}`;
}

export async function http<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const auth = useAuthStore();
  let resp: Response;
  try {
    resp = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(auth.token ? { 'X-R20-Session': auth.token } : {}),
        ...(options.headers || {}),
      },
    });
  } catch (e: any) {
    throw new HttpError('网络错误，请稍后重试', 0);
  }

  let data: any = null;
  try {
    data = await resp.json();
  } catch {
    /* 空响应体 */
  }

  if (resp.status === 401 && auth.token) {
    auth.logout();
    throw new HttpError('会话已过期，请重新登录', 401);
  }
  if (!resp.ok) {
    throw new HttpError(normalizeDetail(data, resp.status), resp.status);
  }
  return data as T;
}

export const get = <T = any>(path: string) => http<T>(path);
export const post = <T = any>(path: string, body?: unknown) =>
  http<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T = any>(path: string, body?: unknown) =>
  http<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T = any>(path: string, body?: unknown) =>
  http<T>(path, { method: 'PATCH', body: body === undefined ? undefined : JSON.stringify(body) });
export const del = <T = any>(path: string) => http<T>(path, { method: 'DELETE' });

/** 组合式包装：需要 loading 态的调用方使用 */
export function useHttp() {
  const loading = ref(false);
  const error = ref<string | null>(null);
  async function request<T = any>(path: string, options: RequestInit = {}): Promise<T> {
    loading.value = true;
    error.value = null;
    try {
      return await http<T>(path, options);
    } catch (e: any) {
      error.value = e.message || String(e);
      throw e;
    } finally {
      loading.value = false;
    }
  }
  return { loading, error, request };
}

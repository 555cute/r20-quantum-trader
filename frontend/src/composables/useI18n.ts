import { ref, computed } from 'vue'
import { zhCN } from '../locales/zh'
import { enUS } from '../locales/en'
// ---- 迁移期兼容：旧组件仍引用旧键位，深合并保证不断档；旧页面清零后移除 ----
import { zhCN as zhLegacy } from '../locales/legacy/zh'
import { enUS as enLegacy } from '../locales/legacy/en'

export type LocaleType = 'zh-CN' | 'en-US'

const LOCALE_KEY = 'r20_locale'

type Dict = Record<string, any>

function deepMerge<T extends Dict>(base: T, over: T): T {
  const out: Dict = { ...base }
  for (const [k, v] of Object.entries(over)) {
    const b = out[k]
    out[k] = b && v && typeof b === 'object' && typeof v === 'object' && !Array.isArray(b) && !Array.isArray(v)
      ? deepMerge(b, v)
      : v
  }
  return out as T
}

const messages: Record<LocaleType, Dict> = {
  'zh-CN': deepMerge(zhLegacy as Dict, zhCN as Dict),
  'en-US': deepMerge(enLegacy as Dict, enUS as Dict),
}

let currentLocaleRaw = ref<LocaleType>('zh-CN')
let initialized = false

export function useI18n() {
  const currentLocale = currentLocaleRaw

  function applyLocale(locale: LocaleType) {
    currentLocale.value = locale
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('lang', locale)
      try {
        localStorage.setItem(LOCALE_KEY, locale)
      } catch {
        // ignore storage error in private browsing
      }
    }
  }

  function toggleLocale() {
    applyLocale(currentLocale.value === 'zh-CN' ? 'en-US' : 'zh-CN')
  }

  function initLocale() {
    if (initialized) return
    initialized = true

    let preferred: LocaleType = 'zh-CN'
    try {
      const saved = localStorage.getItem(LOCALE_KEY)
      if (saved === 'zh-CN' || saved === 'en-US') {
        preferred = saved
      } else if (typeof navigator !== 'undefined') {
        const navLang = (navigator.language || '').toLowerCase()
        if (navLang.startsWith('en')) {
          preferred = 'en-US'
        }
      }
    } catch {
      // fallback
    }

    applyLocale(preferred)
  }

  /**
   * Safe nested key getter with {placeholder} interpolation:
   *   t('common.confirmPhraseHint', undefined, { phrase: 'DELETE' })
   * 缺失键回退链：当前语言 → zh-CN → fallback → 键路径
   */
  function t(path: string, fallback?: string, params?: Record<string, string | number>): string {
    const lookup = (dict: Dict): string | null => {
      let curr: any = dict
      for (const p of path.split('.')) {
        if (curr && typeof curr === 'object' && p in curr) curr = curr[p]
        else return null
      }
      return typeof curr === 'string' ? curr : null
    }
    let out = lookup(messages[currentLocale.value]) ?? lookup(messages['zh-CN']) ?? fallback ?? path
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        out = out.replaceAll(`{${k}}`, String(v))
      }
    }
    return out
  }

  /** 取原始值（数组/对象），用于列表型文案 */
  function tm(path: string): any {
    const lookup = (dict: Dict): any => {
      let curr: any = dict
      for (const p of path.split('.')) {
        if (curr && typeof curr === 'object' && p in curr) curr = curr[p]
        else return undefined
      }
      return curr
    }
    return lookup(messages[currentLocale.value]) ?? lookup(messages['zh-CN'])
  }

  const isEn = computed(() => currentLocale.value === 'en-US')
  const locale = computed(() => currentLocale.value)

  return {
    locale,
    currentLocale,
    isEn,
    t,
    tm,
    setLocale: applyLocale,
    toggleLocale,
    initLocale,
  }
}

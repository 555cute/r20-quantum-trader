import { ref, computed } from 'vue'
import { zhCN } from '../locales/zh-CN'
import { enUS } from '../locales/en-US'

export type LocaleType = 'zh-CN' | 'en-US'

const LOCALE_KEY = 'r20_locale'

const currentLocale = ref<LocaleType>('zh-CN')
let initialized = false

const messages = {
  'zh-CN': zhCN,
  'en-US': enUS,
}

export function useI18n() {
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
   * Safe nested key getter: t('nav.tabMatrix', '实盘矩阵')
   */
  function t(path: string, fallback?: string): string {
    const dict = messages[currentLocale.value] || messages['zh-CN']
    const parts = path.split('.')
    let curr: any = dict
    for (const p of parts) {
      if (curr && typeof curr === 'object' && p in curr) {
        curr = curr[p]
      } else {
        return fallback || path
      }
    }
    return typeof curr === 'string' ? curr : (fallback || path)
  }

  const isEn = computed(() => currentLocale.value === 'en-US')
  const locale = computed(() => currentLocale.value)

  return {
    locale,
    currentLocale,
    isEn,
    t,
    setLocale: applyLocale,
    toggleLocale,
    initLocale,
  }
}

'use client'

import React, { createContext, useContext, useEffect, useState } from 'react'
import { Locale, translations } from './index'

interface I18nContextType {
  locale: Locale
  setLocale: (l: Locale) => void
  t: (typeof translations)['zh'] | (typeof translations)['en']
}

const I18nContext = createContext<I18nContextType | null>(null)

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>('zh')
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
    try {
      const saved = localStorage.getItem('r20_lang') as Locale
      if (saved === 'zh' || saved === 'en') {
        setLocaleState(saved)
        return
      }
      // Auto-detect browser and OS environment
      const browserLang = typeof navigator !== 'undefined' ? navigator.language : 'zh'
      if (browserLang.toLowerCase().startsWith('zh')) {
        setLocaleState('zh')
      } else {
        setLocaleState('en')
      }
    } catch {
      setLocaleState('zh')
    }
  }, [])

  function setLocale(newLocale: Locale) {
    setLocaleState(newLocale)
    try {
      localStorage.setItem('r20_lang', newLocale)
    } catch {}
  }

  const t = translations[locale]

  return (
    <I18nContext.Provider value={{ locale, setLocale, t }}>
      {children}
    </I18nContext.Provider>
  )
}

export function useI18n() {
  const ctx = useContext(I18nContext)
  if (!ctx) {
    throw new Error('useI18n must be used within I18nProvider')
  }
  return ctx
}

import { ref } from 'vue'

export type ThemeMode = 'dark' | 'light'

const currentTheme = ref<ThemeMode>('dark')
const cvdMode = ref(false)
let initialized = false

function applyCvd(on: boolean) {
  cvdMode.value = on
  if (typeof document !== 'undefined') {
    const el = document.documentElement
    if (on) el.setAttribute('data-cvd', 'true')
    else el.removeAttribute('data-cvd')
    try {
      localStorage.setItem('r20_cvd', on ? '1' : '0')
    } catch {
      // ignore
    }
  }
}

export function useTheme() {
  function applyTheme(theme: ThemeMode) {
    currentTheme.value = theme
    if (typeof document !== 'undefined') {
      const el = document.documentElement
      el.setAttribute('data-theme', theme)
      if (theme === 'dark') {
        el.classList.add('dark')
      } else {
        el.classList.remove('dark')
      }
      try {
        localStorage.setItem('r20_theme', theme)
      } catch {
        // ignore localStorage error in private mode
      }
    }
  }

  function toggleTheme() {
    applyTheme(currentTheme.value === 'dark' ? 'light' : 'dark')
  }

  function initTheme() {
    if (initialized) return
    initialized = true
    let saved: ThemeMode = 'dark'
    try {
      const stored = localStorage.getItem('r20_theme')
      if (stored === 'light' || stored === 'dark') {
        saved = stored
      }
      if (localStorage.getItem('r20_cvd') === '1') applyCvd(true)
    } catch {
      // fallback
    }
    applyTheme(saved)
  }

  return {
    theme: currentTheme,
    toggleTheme,
    setTheme: applyTheme,
    initTheme,
    cvd: cvdMode,
    setCvd: applyCvd,
    toggleCvd: () => applyCvd(!cvdMode.value),
  }
}

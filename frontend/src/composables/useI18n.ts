import { useI18nStore } from '../stores/i18n'

export function useI18n() {
  const store = useI18nStore()
  return {
    locale: store.locale,
    t: store.t,
    setLocale: store.setLocale,
    toggleLocale: store.toggleLocale,
  }
}

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export const useThemeStore = defineStore('theme', () => {
  const theme = ref<'light' | 'dark'>((document.documentElement.dataset.theme as any) || 'dark')
  const isLight = computed(() => theme.value === 'light')
  function apply(next: 'light' | 'dark') {
    theme.value = next
    document.documentElement.dataset.theme = next
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', next === 'light' ? '#f3f6fa' : '#080b10')
    try { localStorage.setItem('r20-theme', next) } catch (_) {}
  }
  function toggle() { apply(isLight.value ? 'dark' : 'light') }
  return { theme, isLight, apply, toggle }
})

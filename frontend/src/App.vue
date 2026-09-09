<script setup lang="ts">
import { onMounted } from 'vue'
import { useTheme } from './composables/useTheme'
import { useI18n } from './composables/useI18n'
import { useHotkeys } from './composables/useHotkeys'
import { useUi } from './composables/useUi'
import ToastHost from './components/base/ToastHost.vue'
import ConfirmHost from './components/base/ConfirmHost.vue'
import CommandPalette from './components/dashboard/CommandPalette.vue'
import PeekDrawer from './components/dashboard/PeekDrawer.vue'

const { initTheme } = useTheme()
const { initLocale } = useI18n()
const { cmdkOpen } = useUi()

useHotkeys({
  'mod+k': {
    handler: () => { cmdkOpen.value = !cmdkOpen.value },
    allowInInput: true,
  },
})

onMounted(() => {
  initTheme()
  initLocale()
})
</script>

<template>
  <router-view />
  <ToastHost />
  <ConfirmHost />
  <CommandPalette />
  <PeekDrawer />
</template>

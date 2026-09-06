<script setup lang="ts">
import { onMounted } from 'vue'
import { useTheme } from './composables/useTheme'
import { useI18n } from './composables/useI18n'

const { initTheme } = useTheme()
const { initLocale } = useI18n()

onMounted(() => {
  initTheme()
  initLocale()
})
</script>

<template>
  <router-view />
</template>

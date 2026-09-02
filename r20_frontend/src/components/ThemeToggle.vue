<script setup lang="ts">
import { Moon, Sun } from '@lucide/vue'
import { useThemeStore } from '../stores/theme'
const theme = useThemeStore()
</script>
<template>
  <button class="btn !h-10 !min-h-0 !w-10 !px-0 sm:!w-auto sm:!px-3" type="button" :title="theme.isLight ? '切换到暗色主题' : '切换到亮色主题'" @click="theme.toggle()">
    <Moon v-if="theme.isLight" :size="15"/><Sun v-else :size="15"/><span class="hidden xl:inline">{{ theme.isLight ? '暗色' : '亮色' }}</span>
  </button>
</template>

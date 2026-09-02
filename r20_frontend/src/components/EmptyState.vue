<script setup lang="ts">import { Inbox } from '@lucide/vue'; defineProps<{ text:string }>()</script>
<template><div class="flex min-h-28 flex-col items-center justify-center gap-2 muted text-xs"><Inbox :size="24"/><span>{{ text }}</span></div></template>

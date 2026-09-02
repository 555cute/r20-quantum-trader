<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { Activity, LayoutGrid, Cpu, Newspaper, Sparkles, Receipt, Settings, Code2 } from '@lucide/vue'
import ThemeToggle from '../components/ThemeToggle.vue'
import { useTerminalStore } from '../stores/terminal'

const store = useTerminalStore()
const nav = [
  {to:'/terminal/trading', label:'实盘矩阵', short:'实盘', icon:LayoutGrid},
  {to:'/terminal/ai', label:'AI 推演', short:'AI推演', icon:Cpu},
  {to:'/terminal/news', label:'全网舆情', short:'舆情', icon:Newspaper},
  {to:'/terminal/evolution', label:'自进化', short:'自进化', icon:Sparkles},
  {to:'/terminal/ledger', label:'交易台账', short:'台账', icon:Receipt},
]
const statusLabel = computed(() => ({LIVE:'OKX V5 在线',PARTIAL:'数据部分可用',STALE:'数据已延迟',OFFLINE:'数据离线',CONNECTING:'连接中'} as any)[store.health] || store.health)
const statusColor = computed(() => store.health==='LIVE'?'var(--profit)':store.health==='OFFLINE'?'var(--loss)':'var(--warning)')
onMounted(() => store.start())
</script>

<template>
  <div class="min-h-screen pb-24 md:pb-10">
    <header class="sticky top-0 z-40 border-b border-theme bg-app/95 backdrop-blur-xl">
      <div class="mx-auto flex h-[58px] max-w-[1720px] items-center justify-between gap-3 px-3 sm:px-6">
        <div class="flex min-w-0 items-center gap-3">
          <div class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-600 text-white"><Activity :size="17"/></div>
          <div class="min-w-0"><div class="flex items-center gap-2"><strong class="truncate font-mono text-xs min-[360px]:text-sm sm:text-base">R20 <span class="hidden min-[360px]:inline">QUANTUM</span></strong><span class="badge hidden sm:inline-flex">v6 PREVIEW</span></div></div>
          <div class="desktop-only ml-2 flex items-center gap-2 border-l border-theme pl-4 text-[10px] font-mono muted">
            <span class="h-2 w-2 rounded-full" :style="{background:statusColor}"></span><b class="text-main">{{ statusLabel }}</b><span>·</span><span>{{ store.latency ? `${Math.round(store.latency)}ms` : '--' }}</span><span>·</span><span>4S UI</span>
          </div>
        </div>

        <nav class="desktop-only flex items-center rounded-xl border border-theme bg-subtle p-1">
          <RouterLink v-for="item in nav" :key="item.to" :to="item.to" class="nav-link"><component :is="item.icon" :size="14"/>{{ item.label }}</RouterLink>
        </nav>

        <div class="flex shrink-0 items-center gap-1.5 sm:gap-2">
          <ThemeToggle/>
          <RouterLink to="/admin" class="btn !h-10 !min-h-0 !w-10 !px-0 sm:!w-auto sm:!px-3" title="管理后台"><Settings :size="16"/><span class="hidden sm:inline">管理</span></RouterLink>
          <a class="btn hidden !h-10 !min-h-0 !w-10 !px-0 sm:inline-flex" href="https://github.com/zi-fei-yu-2020/r20-quantum-trader" target="_blank" rel="noreferrer" title="GitHub"><Code2 :size="16"/></a>
        </div>
      </div>
    </header>

    <main class="mx-auto w-full max-w-[1720px] px-2.5 pt-3 min-[380px]:px-3 sm:px-6 sm:pt-4"><RouterView/></main>

    <nav class="mobile-only fixed inset-x-0 bottom-0 z-50 border-t border-theme bg-app/95 px-1.5 pb-[max(.4rem,env(safe-area-inset-bottom))] pt-1.5 backdrop-blur-xl">
      <div class="grid grid-cols-5 gap-0.5">
        <RouterLink v-for="item in nav" :key="item.to" :to="item.to" class="flex min-h-12 flex-col items-center justify-center gap-1 rounded-lg py-1.5 text-[9px] font-bold muted" active-class="!text-blue-500 bg-blue-500/10"><component :is="item.icon" :size="17"/>{{ item.short }}</RouterLink>
      </div>
    </nav>
  </div>
</template>

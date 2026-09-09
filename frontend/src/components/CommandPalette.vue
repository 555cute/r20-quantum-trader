<script setup lang="ts">
/** P3: global command palette (⌘K / Ctrl+K).
 *  Bloomberg-style keyboard-first navigation: jump to any page, toggle theme/CVD/language. */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from '../composables/useI18n'
import { useTheme } from '../composables/useTheme'
import { useDashboardStore } from '../stores/dashboard'
import {
  Search, Sun, Moon, Globe, Eye, CornerDownLeft, ArrowUp, ArrowDown,
  LayoutDashboard, Radio, Scroll, FileText, Users, Sparkles, Layers,
  ShieldAlert, ShieldCheck, Wallet, RefreshCw, Cpu, Package, FileCode,
  Info, UserCog, LayoutGrid, ArrowUpRight, Newspaper,
} from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const { t, isEn, toggleLocale } = useI18n()
const { theme, setTheme, cvd, toggleCvd } = useTheme()
const store = useDashboardStore()

const open = ref(false)
const q = ref('')
const idx = ref(0)
const inputEl = ref<HTMLInputElement | null>(null)

type Cmd = { id: string; label: string; hint?: string; icon: any; run: () => void; group: string }

const inAdmin = computed(() => route.path.startsWith('/admin'))

const commands = computed<Cmd[]>(() => {
  const nav = (id: string, key: string, icon: any): Cmd => ({
    id: `go-${id}`, label: t(key), icon, group: inAdmin.value ? t('cmd.pages') : t('cmd.tabs'),
    run: () => { inAdmin.value ? router.push(`/admin/${id}`) : (store.activeTab = id as any) },
  })
  const list: Cmd[] = inAdmin.value
    ? [
        nav('overview', 'admin.nOverview', LayoutDashboard),
        nav('decisions', 'admin.nDecisions', Radio),
        nav('audit', 'admin.nAudit', Scroll),
        nav('promptlib', 'admin.nPrompt', FileText),
        nav('council', 'admin.nCouncil', Users),
        nav('evolution', 'admin.nEvolution', Sparkles),
        nav('policy', 'admin.nPolicy', Layers),
        nav('news', 'admin.newsSources', Newspaper),
        nav('risk', 'admin.nRisk', ShieldAlert),
        nav('interceptors', 'admin.nInterceptors', ShieldCheck),
        nav('security', 'admin.nSecurity', Wallet),
        nav('gateway', 'admin.nGateway', RefreshCw),
        nav('llm', 'admin.nLlm', Cpu),
        nav('agents', 'admin.nAgents', Package),
        nav('plugins', 'admin.nPlugins', FileCode),
        nav('notify', 'admin.nNotify', Radio),
        nav('backup', 'admin.nBackup', FileCode),
        nav('adminsys', 'admin.nAdminSys', UserCog),
        nav('about', 'admin.nAbout', Info),
      ]
    : [
        nav('trading', 'nav.tabMatrix', LayoutGrid),
        nav('factors', 'nav.tabRadar', Cpu),
        nav('news', 'nav.tabNews', Scroll),
        nav('lab', 'nav.tabLab', Sparkles),
        nav('history', 'nav.tabLedger', Layers),
      ]

  list.push(
    { id: 'theme', label: theme.value === 'dark' ? t('cmd.toLight') : t('cmd.toDark'), icon: theme.value === 'dark' ? Sun : Moon, group: t('cmd.actions'),
      run: () => setTheme(theme.value === 'dark' ? 'light' : 'dark') },
    { id: 'cvd', label: cvd ? t('cmd.cvdOff') : t('cmd.cvdOn'), icon: Eye, group: t('cmd.actions'), run: toggleCvd },
    { id: 'lang', label: isEn ? '切换中文' : 'Switch to English', icon: Globe, group: t('cmd.actions'), run: toggleLocale },
  )
  if (inAdmin.value) {
    list.push({ id: 'terminal', label: t('cmd.openTerminal'), icon: ArrowUpRight, group: t('cmd.actions'),
      run: () => window.open('/', '_blank') })
  } else {
    list.push({ id: 'refresh', label: t('cmd.refresh'), icon: RefreshCw, group: t('cmd.actions'),
      run: () => store.fetchDashboard(false) })
    // jump to instrument: open its detail via factors tab later; for now switch+filter unsupported → skip
  }
  return list
})

const filtered = computed(() => {
  const s = q.value.trim().toLowerCase()
  if (!s) return commands.value
  return commands.value.filter((c) => c.label.toLowerCase().includes(s) || c.id.includes(s))
})

const grouped = computed(() => {
  const m = new Map<string, Cmd[]>()
  for (const c of filtered.value) {
    if (!m.has(c.group)) m.set(c.group, [])
    m.get(c.group)!.push(c)
  }
  return Array.from(m.entries())
})

const flat = computed(() => grouped.value.flatMap(([, items]) => items))

function onGlobalKey(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    open.value = !open.value
    if (open.value) { q.value = ''; idx.value = 0; setTimeout(() => inputEl.value?.focus(), 10) }
    return
  }
  if (!open.value) return
  if (e.key === 'Escape') { open.value = false }
  else if (e.key === 'ArrowDown') { e.preventDefault(); idx.value = Math.min(idx.value + 1, flat.value.length - 1) }
  else if (e.key === 'ArrowUp') { e.preventDefault(); idx.value = Math.max(idx.value - 1, 0) }
  else if (e.key === 'Enter') { e.preventDefault(); exec(flat.value[idx.value]) }
}

function exec(c?: Cmd) {
  if (!c) return
  open.value = false
  c.run()
}

onMounted(() => window.addEventListener('keydown', onGlobalKey))
onUnmounted(() => window.removeEventListener('keydown', onGlobalKey))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="fixed inset-0 z-[90]" @click.self="open = false">
      <div class="absolute inset-0" style="background-color: var(--bg-overlay); backdrop-filter: blur(2px);"></div>
      <div
        class="relative mx-auto mt-[12vh] w-[min(560px,92vw)] rounded-xl border shadow-lg overflow-hidden"
        style="background-color: var(--bg-elevated); border-color: var(--border-medium);"
      >
        <div class="flex items-center gap-2.5 px-3.5 py-3 border-b" style="border-color: var(--border-subtle);">
          <Search class="w-4 h-4 shrink-0" style="color: var(--text-faint);" />
          <input
            ref="inputEl"
            v-model="q"
            @input="idx = 0"
            type="text"
            autocomplete="off"
            :placeholder="t('cmd.placeholder')"
            class="flex-1 bg-transparent outline-none text-sm font-mono"
            style="color: var(--text-main);"
          />
          <kbd class="hidden sm:inline text-[11px] font-mono px-1.5 py-0.5 rounded border" style="color: var(--text-faint); border-color: var(--border-subtle);">esc</kbd>
        </div>

        <div class="max-h-[46vh] overflow-y-auto p-1.5">
          <template v-for="[group, items] in grouped" :key="group">
            <div class="px-2.5 pt-2 pb-1 text-[11px] font-mono font-bold uppercase tracking-wider" style="color: var(--text-faint);">{{ group }}</div>
            <button
              v-for="c in items"
              :key="c.id"
              @click="exec(c)"
              @mouseenter="idx = flat.indexOf(c)"
              class="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-left text-xs font-mono cursor-pointer transition-colors"
              :style="flat[idx]?.id === c.id
                ? { backgroundColor: 'var(--color-brand-bg)', color: 'var(--color-brand)' }
                : { color: 'var(--text-main)' }"
            >
              <component :is="c.icon" class="w-3.5 h-3.5 shrink-0" :style="flat[idx]?.id === c.id ? {} : { color: 'var(--text-muted)' }" />
              <span class="truncate">{{ c.label }}</span>
              <CornerDownLeft v-if="flat[idx]?.id === c.id" class="w-3 h-3 ml-auto shrink-0 opacity-60" />
            </button>
          </template>
          <div v-if="!flat.length" class="py-8 text-center text-xs font-mono" style="color: var(--text-faint);">
            {{ t('cmd.noMatch') }}
          </div>
        </div>

        <div class="flex items-center gap-3 px-3 py-1.5 border-t text-[11px] font-mono" style="border-color: var(--border-subtle); color: var(--text-faint);">
          <span class="flex items-center gap-1"><ArrowUp class="w-3 h-3" /><ArrowDown class="w-3 h-3" /> {{ t('cmd.navigate') }}</span>
          <span class="flex items-center gap-1"><CornerDownLeft class="w-3 h-3" /> {{ t('cmd.select') }}</span>
        </div>
      </div>
    </div>
  </Teleport>
</template>

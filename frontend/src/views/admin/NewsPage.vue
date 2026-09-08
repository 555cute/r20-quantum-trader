<script setup lang="ts">
import { computed, reactive, ref, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import {
  Newspaper,
  Save,
  Play,
  ExternalLink,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Info,
  RefreshCw,
} from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()

type BannerType = 'ok' | 'err' | 'warn'
type BusyState = 'save' | 'harvest' | ''

interface NewsSourceOption {
  id: string
  name: string
  description: string
}

interface NewsSourceStatus {
  status: string
  count: number
  updated_at: string | null
  error: string | null
}

interface NewsFeed {
  latest_news?: unknown[]
  coins_sentiment?: Record<string, unknown>
  macro_sentiment?: string
  enabled_sources?: string[]
  source_status?: Record<string, NewsSourceStatus>
  status?: string
  stale_sections?: boolean
  updated_at?: string
  news_fresh_at?: string
}

interface NewsConfigResponse {
  sources?: string[]
  available_sources?: NewsSourceOption[]
  feed?: NewsFeed
  effect?: string
  updated?: boolean
}


const loading = ref(true)
const loaded = ref(false)
const busy = ref<BusyState>('')
const bannerMsg = ref<{ text: string; type: BannerType } | null>(null)
const effectText = ref('')
const savedSources = ref<string[]>([])
const availableSources = ref<NewsSourceOption[]>([])
const feed = ref<NewsFeed>({})
const selected = reactive<Record<string, boolean>>({})

const draftSources = computed(() =>
  availableSources.value.map((opt) => opt.id).filter((id) => !!selected[id]),
)

const dirty = computed(() => !sameSources(draftSources.value, savedSources.value))

const harvestDisabled = computed(() =>
  busy.value !== ''
  || loading.value
  || dirty.value
  || !auth.isSuperadmin
  || draftSources.value.length === 0
  || savedSources.value.length === 0,
)

const harvestTitle = computed(() => {
  if (!auth.isSuperadmin) return '仅超级管理员可手动采集'
  if (dirty.value) return '请先保存新闻来源选择'
  if (savedSources.value.length === 0 || draftSources.value.length === 0) return '已禁用全部来源，无法采集'
  if (busy.value) return '请等待当前操作结束'
  return '按已保存来源立即采集'
})

const feedStatus = computed(() => String(feed.value.status || ''))

function sameSources(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false
  const left = [...a].sort()
  const right = [...b].sort()
  return left.every((id, i) => id === right[i])
}

function normalizeSources(raw: unknown): string[] {
  if (!Array.isArray(raw)) return []
  const allowed = new Set(availableSources.value.map((opt) => opt.id))
  const seen = new Set<string>()
  for (const item of raw) {
    if (typeof item !== 'string') continue
    const id = item.trim().toLowerCase()
    if (!id || !allowed.has(id) || seen.has(id)) continue
    seen.add(id)
  }
  return availableSources.value.map((opt) => opt.id).filter((id) => seen.has(id))
}

function normalizeOptions(raw: unknown): NewsSourceOption[] {
  if (!Array.isArray(raw) || raw.length === 0) throw new Error('服务端未提供新闻来源列表')
  const out: NewsSourceOption[] = []
  const seen = new Set<string>()
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const rec = item as Record<string, unknown>
    const id = typeof rec.id === 'string' ? rec.id.trim().toLowerCase() : ''
    if (!id || seen.has(id)) continue
    seen.add(id)
    out.push({
      id,
      name: typeof rec.name === 'string' && rec.name.trim() ? rec.name : id,
      description: typeof rec.description === 'string' ? rec.description : '',
    })
  }
  if (!out.length) throw new Error('服务端新闻来源列表无效')
  return out
}

function syncSelected(sources: string[]) {
  for (const key of Object.keys(selected)) delete selected[key]
  for (const opt of availableSources.value) {
    selected[opt.id] = sources.includes(opt.id)
  }
}

function applyPayload(res: NewsConfigResponse) {
  availableSources.value = normalizeOptions(res.available_sources)
  const sources = normalizeSources(res.sources)
  savedSources.value = sources
  syncSelected(sources)
  feed.value = res.feed && typeof res.feed === 'object' ? res.feed : {}
  effectText.value = typeof res.effect === 'string' ? res.effect : ''
  loaded.value = true
}

const statusRows = computed(() => {
  const map = feed.value.source_status || {}
  const seen = new Set<string>()
  const rows: Array<{ id: string; name: string } & NewsSourceStatus> = []
  for (const opt of availableSources.value) {
    seen.add(opt.id)
    const st = map[opt.id]
    rows.push({
      id: opt.id,
      name: opt.name,
      status: st?.status || (savedSources.value.includes(opt.id) ? 'pending' : 'disabled'),
      count: Number(st?.count || 0),
      updated_at: st?.updated_at ?? null,
      error: st?.error ?? null,
    })
  }
  for (const [id, st] of Object.entries(map)) {
    if (seen.has(id)) continue
    rows.push({
      id,
      name: id,
      status: st?.status || 'pending',
      count: Number(st?.count || 0),
      updated_at: st?.updated_at ?? null,
      error: st?.error ?? null,
    })
  }
  return rows
})

function statusLabel(status: string): string {
  switch (status) {
    case 'ok': return '正常'
    case 'empty': return '空结果'
    case 'error': return '失败'
    case 'stale': return '过期缓存'
    case 'disabled': return '已关闭'
    case 'pending': return '待采集'
    case 'partial': return '部分失败'
    default: return status || '未知'
  }
}

function feedStatusLabel(status: string): string {
  switch (status) {
    case 'ok': return '可用'
    case 'partial': return '部分失败'
    case 'error': return '采集失败'
    case 'disabled': return '已关闭'
    case 'pending': return '待采集'
    default: return status ? statusLabel(status) : '未知'
  }
}

function statusTone(status: string): Record<string, string> {
  if (status === 'ok') {
    return { backgroundColor: 'var(--color-up-bg)', borderColor: 'var(--color-up-border)', color: 'var(--color-up)' }
  }
  if (status === 'error') {
    return { backgroundColor: 'var(--color-down-bg)', borderColor: 'var(--color-down-border)', color: 'var(--color-down)' }
  }
  if (status === 'stale' || status === 'pending' || status === 'partial' || status === 'empty') {
    return { backgroundColor: 'var(--color-warn-bg)', borderColor: 'var(--color-warn-border)', color: 'var(--color-warn)' }
  }
  return { backgroundColor: 'var(--bg-badge)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }
}

async function loadConfig(silent = false): Promise<boolean> {
  if (!silent) loading.value = true
  try {
    const res = await api<NewsConfigResponse>('/api/v1/admin/news/config')
    applyPayload(res)
    if (!silent) bannerMsg.value = null
    return true
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : String(e)
    bannerMsg.value = { text: `加载失败: ${message}`, type: 'err' }
    return false
  } finally {
    loading.value = false
  }
}

async function saveSources() {
  if (!auth.isSuperadmin) {
    bannerMsg.value = { text: '仅超级管理员可修改新闻来源', type: 'err' }
    return
  }
  if (busy.value) return
  busy.value = 'save'
  bannerMsg.value = null
  try {
    const res = await api<NewsConfigResponse>('/api/v1/admin/news/config', {
      method: 'PUT',
      body: JSON.stringify({ sources: draftSources.value }),
    })
    applyPayload(res)
    const extra = effectText.value ? ` ${effectText.value}` : ''
    bannerMsg.value = {
      text: draftSources.value.length
        ? `新闻来源已保存，未触发采集。${extra}`
        : `已关闭全部新闻来源，未触发采集。${extra}`,
      type: 'ok',
    }
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : String(e)
    bannerMsg.value = { text: `保存失败: ${message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

async function harvestNow() {
  if (!auth.isSuperadmin) {
    bannerMsg.value = { text: '仅超级管理员可手动采集', type: 'err' }
    return
  }
  if (harvestDisabled.value || busy.value) return
  const names = availableSources.value.filter((source) => savedSources.value.includes(source.id)).map((source) => source.name).join('、')
  const ok = confirm(
    `立即采集将请求：${names}。\n不会使用交易密钥，也不会下单。\n确定现在采集？`,
  )
  if (!ok) return
  busy.value = 'harvest'
  bannerMsg.value = null
  try {
    await api('/api/v1/admin/gateway/jobs/news/run', {
      method: 'POST',
      body: JSON.stringify({}),
    })
    const refreshed = await loadConfig(true)
    bannerMsg.value = !refreshed
      ? { text: '采集已触发，但刷新配置失败。', type: 'warn' }
      : feed.value.status === 'ok'
        ? { text: '采集任务已完成，新闻缓存已刷新。', type: 'ok' }
        : { text: '采集任务已结束，但来源仍有异常。请查看下方状态与错误信息。', type: 'warn' }
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : String(e)
    bannerMsg.value = { text: `采集失败: ${message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

onMounted(() => {
  loadConfig()
})
</script>

<template>
  <div class="space-y-4 max-w-[1400px] mx-auto pb-24 min-w-0">
    <div class="panel-banner-compact">
      <div class="flex items-center space-x-2.5 min-w-0">
        <div class="panel-banner-icon">
          <Newspaper class="w-3.5 h-3.5" />
        </div>
        <div class="min-w-0">
          <h1 class="text-xs sm:text-[13px] font-black font-mono uppercase tracking-wide break-words" style="color: var(--text-main);">
            新闻来源 (Public News Feeds)
          </h1>
          <p class="text-[11px] font-mono mt-0.5 break-words" style="color: var(--text-muted);">
            选择进入策略提示词与大屏的公开资讯 · 保存只写配置 · 采集需单独确认
          </p>
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-2 shrink-0">
        <span
          class="badge-lever"
          :style="dirty
            ? { backgroundColor: 'var(--color-warn-bg)', color: 'var(--color-warn)', borderColor: 'var(--color-warn-border)' }
            : {}"
        >
          {{ !auth.isSuperadmin ? '只读' : (dirty ? '未保存' : '与线上口径一致') }}
        </span>
        <span
          v-if="feedStatus"
          class="text-[10px] font-mono px-2 py-0.5 rounded border"
          :style="statusTone(feedStatus)"
        >
          缓存 {{ feedStatusLabel(feedStatus) }}
        </span>
      </div>
    </div>

    <div class="p-3 rounded-lg text-[11px] font-mono border flex items-start gap-2 min-w-0" style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);">
      <Info class="w-3.5 h-3.5 shrink-0 mt-0.5" style="color: var(--accent, #3875F6);" />
      <div class="space-y-1 min-w-0 break-words">
        <p>{{ effectText || '保存后立即按勾选过滤禁用源的缓存；下次定时或手动采集才拉新。空选表示关闭全部来源。' }}</p>
        <p class="opacity-80">币安情报中心与 OKX 快讯对齐：官方公告 + 资讯中心媒体标题/链接。不编正文或牛熊分，不需要交易 API Key。媒体标题只进展示和模型；开仓熔断仍只认官方/已验证源。</p>
      </div>
    </div>

    <div
      v-if="bannerMsg"
      class="p-3 rounded-lg text-xs font-mono border"
      :class="bannerMsg.type === 'ok' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : bannerMsg.type === 'warn' ? 'bg-amber-500/10 border-amber-500/20 text-amber-400' : 'bg-rose-500/10 border-rose-500/20 text-rose-400'"
    >
      <div class="flex items-center gap-2 min-w-0">
        <CheckCircle2 v-if="bannerMsg.type === 'ok'" class="w-4 h-4 shrink-0" />
        <AlertCircle v-else class="w-4 h-4 shrink-0" />
        <span class="break-words">{{ bannerMsg.text }}</span>
      </div>
    </div>

    <div v-if="loading" class="flex items-center justify-center py-24">
      <Loader2 class="w-6 h-6 animate-spin" style="color: var(--text-muted);" />
    </div>

    <template v-else-if="loaded">
      <p
        v-if="!auth.isSuperadmin"
        class="text-[11px] font-mono px-1"
        style="color: var(--text-muted);"
      >
        当前为普通管理员，仅可查看来源与采集状态。
      </p>

      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label
          v-for="opt in availableSources"
          :key="opt.id"
          :for="`news-src-${opt.id}`"
          class="rounded-xl border p-4 min-w-0 flex items-start gap-3"
          :class="auth.isSuperadmin && busy === '' ? 'cursor-pointer' : 'cursor-default'"
          :style="selected[opt.id]
            ? { backgroundColor: 'var(--bg-card)', borderColor: 'var(--color-brand-border)' }
            : { backgroundColor: 'var(--bg-card)', borderColor: 'var(--border-subtle)' }"
        >
          <input
            :id="`news-src-${opt.id}`"
            v-model="selected[opt.id]"
            type="checkbox"
            class="accent-blue-500 w-4 h-4 mt-0.5 shrink-0"
            :disabled="!auth.isSuperadmin || busy !== ''"
          />
          <span class="min-w-0">
            <span class="flex items-center gap-2 flex-wrap">
              <span class="text-xs font-mono font-bold" style="color: var(--text-main);">{{ opt.name }}</span>
              <span class="text-[9px] font-mono px-1.5 py-0.5 rounded border" style="border-color: var(--border-subtle); color: var(--text-muted);">{{ opt.id }}</span>
            </span>
            <span class="block text-[10px] font-mono mt-1 leading-relaxed break-words" style="color: var(--text-muted);">{{ opt.description }}</span>
          </span>
        </label>
      </div>

      <div class="rounded-xl border overflow-hidden min-w-0" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="px-4 py-3 border-b flex flex-wrap items-center justify-between gap-2" style="border-color: var(--border-subtle);">
          <div>
            <h2 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">来源状态</h2>
            <p class="text-[10px] font-mono mt-0.5" style="color: var(--text-muted);">
              条数 · 最后采集时间 · 错误与过期缓存
              <span v-if="feed.stale_sections" class="ml-1" style="color: var(--color-warn);">· 含过期缓存</span>
            </p>
          </div>
          <span class="text-[10px] font-mono" style="color: var(--text-faint);">
            {{ feed.updated_at || feed.news_fresh_at || '尚未采集' }}
          </span>
        </div>
        <div class="divide-y" style="border-color: var(--border-subtle);">
          <div
            v-for="row in statusRows"
            :key="row.id"
            class="px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 min-w-0"
          >
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-xs font-mono font-bold" style="color: var(--text-main);">{{ row.name }}</span>
                <span class="text-[9px] font-mono px-1.5 py-0.5 rounded border" :style="statusTone(row.status)">
                  {{ statusLabel(row.status) }}
                </span>
                <span
                  v-if="row.status === 'stale'"
                  class="text-[9px] font-mono px-1.5 py-0.5 rounded border"
                  style="background-color: var(--color-warn-bg); border-color: var(--color-warn-border); color: var(--color-warn);"
                >
                  过期缓存
                </span>
              </div>
              <p v-if="row.error" class="text-[10px] font-mono mt-1 break-words" style="color: var(--color-down);">
                {{ row.error }}
              </p>
            </div>
            <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] font-mono shrink-0" style="color: var(--text-muted);">
              <span>条数 <strong style="color: var(--text-main);">{{ row.count }}</strong></span>
              <span>采集 <strong style="color: var(--text-main);">{{ row.updated_at || '--' }}</strong></span>
            </div>
          </div>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          class="btn-admin-primary disabled:opacity-40"
          :disabled="busy !== '' || !auth.isSuperadmin || !dirty"
          @click="saveSources"
        >
          <Save class="w-3.5 h-3.5" />
          <span>{{ busy === 'save' ? '保存中...' : '保存配置' }}</span>
        </button>
        <button
          type="button"
          class="btn-admin-secondary disabled:opacity-40"
          :disabled="harvestDisabled"
          :title="harvestTitle"
          @click="harvestNow"
        >
          <Play class="w-3.5 h-3.5" :class="{ 'animate-spin': busy === 'harvest' }" />
          <span>{{ busy === 'harvest' ? '采集中...' : '立即采集' }}</span>
        </button>
        <button
          type="button"
          class="btn-admin-secondary disabled:opacity-40"
          :disabled="busy !== '' || loading"
          @click="loadConfig()"
        >
          <RefreshCw class="w-3.5 h-3.5" :class="{ 'animate-spin': loading }" />
          <span>刷新状态</span>
        </button>
        <router-link
          to="/news"
          class="btn-admin-secondary"
        >
          <ExternalLink class="w-3.5 h-3.5" />
          <span>前台舆情</span>
        </router-link>
      </div>
    </template>

    <div
      v-else
      class="py-12 text-center border border-dashed rounded-xl space-y-3"
      style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
    >
      <p class="text-xs font-mono">无法加载新闻来源配置。</p>
      <button type="button" class="btn-admin-secondary" @click="loadConfig()">重新加载</button>
    </div>
  </div>
</template>

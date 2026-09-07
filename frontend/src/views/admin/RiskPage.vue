<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import {
  ShieldAlert,
  Save,
  RotateCcw,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Info,
  Layers,
  Target,
  Flame,
  TrendingUp,
} from 'lucide-vue-next'

const { api } = useApi()

const loading = ref(true)
const busy = ref<'save' | 'reset' | ''>('')
const bannerMsg = ref<{ text: string; type: 'ok' | 'err' | 'warn' } | null>(null)

const schema = ref<{ groups: any[]; params: any[] } | null>(null)
const suites = ref<any[]>([])
const effectText = ref('')
const serverValues = ref<Record<string, number>>({})
const draft = reactive<Record<string, number>>({})       // 原生值（比例类为小数）
const disp = reactive<Record<string, string>>({})        // 显示值字符串（用户编辑）

const activeSuiteId = computed(() => {
  if (dirtyKeys.value.length || !suites.value.length) return ''
  for (const s of suites.value) {
    const match = Object.entries(s.values as Record<string, number>).every(
      ([k, v]) => Math.abs((serverValues.value[k] ?? NaN) - v) < 1e-9)
    if (match) return s.id
  }
  return ''
})

async function applySuite(s: any) {
  if (busy.value) return
  busy.value = 'save'
  bannerMsg.value = null
  try {
    const res = await api('/api/v1/admin/risk', { method: 'POST', body: JSON.stringify({ suite_id: s.id }) })
    syncFromServer(res.values)
    bannerMsg.value = { text: `已一键应用「${s.name}」预设 ✓ ${res.effect}`, type: 'ok' }
  } catch (e: any) {
    bannerMsg.value = { text: `应用预设失败: ${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

const groupIcons: Record<string, any> = {
  exposure: Layers,
  per_trade: Target,
  stop_loss: Flame,
  pyramiding: TrendingUp,
}

function toDisplay(p: any, native: number): string {
  const v = native * (p.display_scale || 1)
  // 去掉浮点噪声，最多保留 4 位小数
  return String(Math.round(v * 10000) / 10000)
}

function fromDisplay(p: any, display: string): number | null {
  const raw = parseFloat(display)
  if (Number.isNaN(raw)) return null
  let native = raw / (p.display_scale || 1)
  if (p.type === 'int') native = Math.round(native)
  else native = Math.round(native * 1e6) / 1e6
  return native
}

function syncFromServer(values: Record<string, number>) {
  serverValues.value = { ...values }
  for (const p of schema.value!.params) {
    const v = values[p.key]
    draft[p.key] = v
    disp[p.key] = toDisplay(p, v)
  }
}

async function loadData() {
  loading.value = true
  try {
    const res = await api('/api/v1/admin/risk')
    schema.value = res.schema
    suites.value = res.suites || []
    effectText.value = res.effect || ''
    syncFromServer(res.values)
  } catch (e: any) {
    bannerMsg.value = { text: `加载失败: ${e.message}`, type: 'err' }
  } finally {
    loading.value = false
  }
}

const dirtyKeys = computed(() => {
  if (!schema.value) return []
  return schema.value.params
    .filter((p: any) => draft[p.key] !== undefined && serverValues.value[p.key] !== undefined
      && Math.abs((draft[p.key] ?? 0) - (serverValues.value[p.key] ?? 0)) > 1e-9)
    .map((p: any) => p.key)
})

function isCustomized(p: any): boolean {
  return serverValues.value[p.key] !== undefined
    && Math.abs(serverValues.value[p.key] - p.default) > 1e-9
}

function onFieldInput(p: any) {
  const native = fromDisplay(p, disp[p.key])
  if (native !== null) draft[p.key] = native
}

function revertOne(p: any) {
  draft[p.key] = p.default
  disp[p.key] = toDisplay(p, p.default)
}

const resetConfirm = ref('')

async function saveChanges() {
  if (!dirtyKeys.value.length) return
  const bad = schema.value!.params.filter((p: any) => {
    const v = draft[p.key]
    return dirtyKeys.value.includes(p.key) && (v < p.min || v > p.max)
  })
  if (bad.length) {
    bannerMsg.value = { text: `以下参数越界：${bad.map((p: any) => p.label).join('、')}`, type: 'err' }
    return
  }
  busy.value = 'save'
  bannerMsg.value = null
  try {
    const values: Record<string, number> = {}
    for (const k of dirtyKeys.value) values[k] = draft[k]
    const res = await api('/api/v1/admin/risk', { method: 'POST', body: JSON.stringify({ values }) })
    syncFromServer(res.values)
    resetConfirm.value = ''
    bannerMsg.value = { text: `已保存 ${res.updated.length} 项修改 ✓ ${res.effect}`, type: 'ok' }
  } catch (e: any) {
    bannerMsg.value = { text: `保存失败: ${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

async function resetAll() {
  if (resetConfirm.value.trim().toUpperCase() !== 'RESET RISK') {
    bannerMsg.value = { text: '确认短语必须精确为：RESET RISK', type: 'err' }
    return
  }
  busy.value = 'reset'
  bannerMsg.value = null
  try {
    const res = await api('/api/v1/admin/risk/reset', { method: 'POST', body: JSON.stringify({ confirmation: resetConfirm.value }) })
    syncFromServer(res.values)
    resetConfirm.value = ''
    bannerMsg.value = { text: `已恢复代码默认基线 ✓ ${res.effect}`, type: 'ok' }
  } catch (e: any) {
    bannerMsg.value = { text: `重置失败: ${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

onMounted(loadData)
</script>

<template>
  <div class="space-y-4 max-w-[1400px] mx-auto pb-24">
    <!-- Header -->
    <div class="panel-banner-compact">
      <div class="flex items-center space-x-2.5">
        <div class="panel-banner-icon">
          <ShieldAlert class="w-3.5 h-3.5" />
        </div>
        <div>
          <h1 class="text-xs sm:text-[13px] font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
            执行层风控管理 (Fail-Closed Hard Risk Gates)
          </h1>
          <p class="text-[11px] font-mono mt-0.5" style="color: var(--text-muted);">
            仓位敞口 · 单笔风险 · 止损熔断 · 金字塔加仓 —— 全部硬门禁集中配置
          </p>
        </div>
      </div>
      <span class="badge-lever">
        {{ dirtyKeys.length ? `${dirtyKeys.length} 项待保存` : '与线上口径一致' }}
      </span>
    </div>

    <!-- Effect banner -->
    <div class="p-3 rounded-lg text-[11px] font-mono border flex items-start gap-2" style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-muted);">
      <Info class="w-3.5 h-3.5 shrink-0 mt-0.5" style="color: var(--accent, #3875F6);" />
      <div class="space-y-1">
        <p>{{ effectText || '保存后下一巡检周期自动生效，无需重启。' }}</p>
        <p class="opacity-80">注：本页为执行层代码硬拦截；「物理拦截插件」与「提示词工坊」中的 AI 侧门禁（如置信度、顺势铁律）在各自页面独立配置，双层防线互为兜底。</p>
      </div>
    </div>

    <!-- Banner -->
    <div
      v-if="bannerMsg"
      class="p-3 rounded-lg text-xs font-mono border"
      :class="bannerMsg.type === 'ok' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : bannerMsg.type === 'warn' ? 'bg-amber-500/10 border-amber-500/20 text-amber-400' : 'bg-rose-500/10 border-rose-500/20 text-rose-400'"
    >
      <div class="flex items-center gap-2">
        <CheckCircle2 v-if="bannerMsg.type === 'ok'" class="w-4 h-4 shrink-0" />
        <AlertCircle v-else class="w-4 h-4 shrink-0" />
        <span>{{ bannerMsg.text }}</span>
      </div>
    </div>

    <div v-if="loading" class="flex items-center justify-center py-24">
      <Loader2 class="w-6 h-6 animate-spin" style="color: var(--text-muted);" />
    </div>

    <template v-else-if="schema">
      <!-- 优质预设套件 -->
      <div v-if="suites.length" class="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div
          v-for="s in suites"
          :key="s.id"
          class="rounded-xl border p-4 flex flex-col gap-2 transition-all"
          :style="activeSuiteId === s.id
            ? { backgroundColor: 'var(--bg-card)', borderColor: 'var(--text-main)', boxShadow: '0 0 0 1px var(--text-main)' }
            : { backgroundColor: 'var(--bg-card)', borderColor: 'var(--border-subtle)' }"
        >
          <div class="flex items-center justify-between gap-2">
            <h3 class="text-xs font-black font-mono" style="color: var(--text-main);">{{ s.name }}</h3>
            <span v-if="activeSuiteId === s.id" class="text-[9px] font-mono px-1.5 py-0.5 rounded border border-emerald-500/30 bg-emerald-500/10 text-emerald-400">当前生效</span>
            <span v-else class="text-[9px] font-mono opacity-60" style="color: var(--text-muted);">{{ s.tagline }}</span>
          </div>
          <p class="text-[10px] font-mono leading-relaxed flex-1" style="color: var(--text-muted);">{{ s.desc }}</p>
          <button
            @click="applySuite(s)"
            :disabled="busy !== '' || activeSuiteId === s.id"
            class="self-start mt-1 px-3 py-1.5 rounded-lg text-[11px] font-mono font-bold border transition-colors disabled:opacity-40"
            style="border-color: var(--border-subtle); color: var(--text-main);"
          >
            {{ activeSuiteId === s.id ? '已应用' : '一键应用此预设' }}
          </button>
        </div>
      </div>

      <!-- Group cards -->
      <div
        v-for="group in schema.groups"
        :key="group.id"
        class="rounded-xl border overflow-hidden"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="px-4 py-3 border-b flex items-center gap-2" style="border-color: var(--border-subtle);">
          <component :is="groupIcons[group.id] || ShieldAlert" class="w-4 h-4" style="color: var(--text-main);" />
          <div>
            <h2 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">{{ group.label }}</h2>
            <p class="text-[10px] font-mono mt-0.5" style="color: var(--text-muted);">{{ group.desc }}</p>
          </div>
        </div>

        <div class="divide-y" style="border-color: var(--border-subtle);">
          <div
            v-for="p in schema.params.filter((x: any) => x.group === group.id)"
            :key="p.key"
            class="px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4"
            style="border-color: var(--border-subtle);"
          >
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-xs font-mono font-bold" style="color: var(--text-main);">{{ p.label }}</span>
                <span v-if="isCustomized(p)" class="text-[9px] font-mono px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-500/10 text-amber-400">已自定义</span>
              </div>
              <p class="text-[10px] font-mono mt-1 leading-relaxed" style="color: var(--text-muted);">{{ p.desc }}</p>
              <p class="text-[9px] font-mono mt-0.5 opacity-60" style="color: var(--text-muted);">
                默认 {{ toDisplay(p, p.default) }} {{ p.unit }} · 范围 {{ toDisplay(p, p.min) }} ~ {{ toDisplay(p, p.max) }} {{ p.unit }} · <span class="opacity-70">{{ p.key }}</span>
              </p>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <div class="flex items-center rounded-lg border overflow-hidden" style="background-color: var(--bg-input); border-color: var(--border-subtle);">
                <input
                  v-model="disp[p.key]"
                  @input="onFieldInput(p)"
                  type="number"
                  :min="toDisplay(p, p.min)"
                  :max="toDisplay(p, p.max)"
                  :step="p.step * (p.display_scale || 1)"
                  class="w-24 sm:w-28 px-2.5 py-2 text-xs font-mono outline-none text-right"
                  style="background: transparent; color: var(--text-main);"
                  :class="draft[p.key] < p.min || draft[p.key] > p.max ? 'ring-1 ring-rose-500' : ''"
                />
                <span class="px-2 text-[10px] font-mono whitespace-nowrap select-none" style="color: var(--text-muted);">{{ p.unit }}</span>
              </div>
              <button
                v-if="Math.abs((draft[p.key] ?? 0) - p.default) > 1e-9"
                @click="revertOne(p)"
                class="p-2 rounded-lg border transition-colors hover:bg-[var(--bg-card-hover)]"
                style="border-color: var(--border-subtle); color: var(--text-muted);"
                title="恢复该项默认值"
              >
                <RotateCcw class="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Danger zone: reset -->
      <div class="rounded-xl border p-4" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <h2 class="text-xs font-black font-mono uppercase tracking-wide mb-1" style="color: var(--text-main);">恢复出厂基线</h2>
        <p class="text-[10px] font-mono mb-3" style="color: var(--text-muted);">清除全部自定义覆盖值，执行层回退到代码默认基线。需输入确认短语。</p>
        <div class="flex flex-col sm:flex-row gap-2 sm:items-center">
          <input
            v-model="resetConfirm"
            placeholder="输入 RESET RISK"
            class="flex-1 rounded-lg px-3 py-2 text-xs font-mono outline-none border"
            style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);"
          />
          <button
            @click="resetAll"
            :disabled="busy !== '' || !resetConfirm"
            class="px-4 py-2 rounded-lg text-xs font-mono font-bold border border-rose-500/30 text-rose-400 hover:bg-rose-500/10 disabled:opacity-40 transition-colors flex items-center gap-1.5"
          >
            <RotateCcw class="w-3.5 h-3.5" />
            {{ busy === 'reset' ? '重置中…' : '全部恢复默认' }}
          </button>
        </div>
      </div>
    </template>

    <!-- Sticky save bar -->
    <div
      v-if="schema && dirtyKeys.length"
      class="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 px-4 py-3 rounded-2xl border shadow-2xl flex items-center gap-3"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <span class="text-xs font-mono" style="color: var(--text-main);">{{ dirtyKeys.length }} 项修改未保存</span>
      <button
        @click="saveChanges"
        :disabled="busy !== ''"
        class="px-4 py-2 rounded-lg text-xs font-mono font-bold flex items-center gap-1.5 disabled:opacity-50"
        style="background-color: var(--text-main); color: var(--bg-card);"
      >
        <Save class="w-3.5 h-3.5" />
        {{ busy === 'save' ? '保存中…' : '保存并生效' }}
      </button>
    </div>
  </div>
</template>

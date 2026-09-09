<script setup lang="ts">
import { useToast } from '../../composables/useToast'
const toast = useToast()
import { ref, reactive, computed, onMounted } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
import { useApi } from '../../composables/useApi'
import PageHeader from '../../components/admin/PageHeader.vue'
import DangerZone from '../../components/admin/DangerZone.vue'
import {ShieldAlert,
  Save,
  RotateCcw,
  Loader2,
  Info,
  Layers,
  Target,
  Flame,
  TrendingUp} from 'lucide-vue-next'

const { api } = useApi()

const loading = ref(true)
const busy = ref<'save' | 'reset' | ''>('')

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
  try {
    const res = await api('/api/v1/admin/risk', { method: 'POST', body: JSON.stringify({ suite_id: s.id }) })
    syncFromServer(res.values)
    toast.ok(`已一键应用「${s.name}」预设 ✓ ${res.effect}`)
  } catch (e: any) {
    toast.err(`应用预设失败: ${e.message}`)
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

// 杠杆区间合并行的参数引用（schema 缺失时自动退回通用行渲染，不炸页面）
const levMinP = computed<any>(() => schema.value?.params.find((x: any) => x.key === 'R20_MIN_LEVERAGE') || null)
const levMaxP = computed<any>(() => schema.value?.params.find((x: any) => x.key === 'R20_MAX_LEVERAGE') || null)
const levInverted = computed(() => !!levMinP.value && !!levMaxP.value
  && (draft[levMinP.value.key] ?? 0) > (draft[levMaxP.value.key] ?? 0))

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
    toast.err(`加载失败: ${e.message}`)
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


async function saveChanges() {
  if (!dirtyKeys.value.length) return
  const bad = schema.value!.params.filter((p: any) => {
    const v = draft[p.key]
    return dirtyKeys.value.includes(p.key) && (v < p.min || v > p.max)
  })
  if (bad.length) {
    toast.err(`以下参数越界：${bad.map((p: any) => p.label).join('、')}`)
    return
  }
  if (levInverted.value) {
    toast.err('杠杆下限不能高于上限，请先修正「单笔杠杆区间」')
    return
  }
  if (levInverted.value) {
    toast.err('杠杆下限不能高于上限，请调整区间后再保存')
    return
  }
  busy.value = 'save'
  try {
    const values: Record<string, number> = {}
    for (const k of dirtyKeys.value) values[k] = draft[k]
    const res = await api('/api/v1/admin/risk', { method: 'POST', body: JSON.stringify({ values }) })
    syncFromServer(res.values)
    toast.ok(`已保存 ${res.updated.length} 项修改 ✓ ${res.effect}`)
  } catch (e: any) {
    toast.err(`保存失败: ${e.message}`)
  } finally {
    busy.value = ''
  }
}

async function resetAll() {
  busy.value = 'reset'
  try {
    const res = await api('/api/v1/admin/risk/reset', { method: 'POST', body: JSON.stringify({ confirmation: 'RESET RISK' }) })
    syncFromServer(res.values)
    toast.ok(`已恢复代码默认基线 ✓ ${res.effect}`)
  } catch (e: any) {
    toast.err(`重置失败: ${e.message}`)
  } finally {
    busy.value = ''
  }
}

onMounted(loadData)
</script>

<template>
  <div class="space-y-4 max-w-[1400px] mx-auto pb-24">
    <PageHeader
      :title="t('nav.admin.risk')"
      description="执行层硬风控集中配置：仓位敞口 · 单笔风险 · 止损熔断 · 金字塔加仓"
    >
      <template #actions>
        <span class="badge-lever">
          {{ dirtyKeys.length ? `${dirtyKeys.length} 项待保存` : '与线上口径一致' }}
        </span>
      </template>
    </PageHeader>

    <!-- Effect banner -->
    <div class="p-3 rounded-lg text-[11px] border flex items-start gap-2" style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-2);">
      <Info class="w-3.5 h-3.5 shrink-0 mt-0.5" style="color: var(--accent, var(--info));" />
      <div class="space-y-1">
        <p>{{ effectText || '保存后下一巡检周期自动生效，无需重启。' }}</p>
        <p class="opacity-80">注：本页为执行层代码硬拦截；「物理拦截插件」与「提示词工坊」中的 AI 侧门禁（如置信度、顺势铁律）在各自页面独立配置，双层防线互为兜底。</p>
      </div>
    </div>

    <div v-if="loading" class="flex items-center justify-center py-24">
      <Loader2 class="w-6 h-6 animate-spin" style="color: var(--ink-2);" />
    </div>

    <template v-else-if="schema">
      <!-- 优质预设套件 -->
      <div v-if="suites.length" class="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div
          v-for="s in suites"
          :key="s.id"
          class="rounded-xl border p-4 flex flex-col gap-2 transition-all"
          :style="activeSuiteId === s.id
            ? { backgroundColor: 'var(--surface-2)', borderColor: 'var(--ink-1)', boxShadow: '0 0 0 1px var(--ink-1)' }
            : { backgroundColor: 'var(--surface-2)', borderColor: 'var(--line-1)' }"
        >
          <div class="flex items-center justify-between gap-2">
            <h3 class="text-xs font-semibold" style="color: var(--ink-1);">{{ s.name }}</h3>
            <span v-if="activeSuiteId === s.id" class="text-[11px] px-1.5 py-0.5 rounded border border-emerald-500/30 bg-emerald-500/10 text-emerald-400">当前生效</span>
            <span v-else class="text-[11px] opacity-60" style="color: var(--ink-2);">{{ s.tagline }}</span>
          </div>
          <p class="text-[11px] leading-relaxed flex-1" style="color: var(--ink-2);">{{ s.desc }}</p>
          <button
            @click="applySuite(s)"
            :disabled="busy !== '' || activeSuiteId === s.id"
            class="self-start mt-1 px-3 py-1.5 rounded-lg text-[11px] font-bold border transition-colors disabled:opacity-40"
            style="border-color: var(--line-1); color: var(--ink-1);"
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
        style="background-color: var(--surface-2); border-color: var(--line-1);"
      >
        <div class="px-4 py-3 border-b flex items-center gap-2" style="border-color: var(--line-1);">
          <component :is="groupIcons[group.id] || ShieldAlert" class="w-4 h-4" style="color: var(--ink-1);" />
          <div>
            <h2 class="text-xs font-semibold" style="color: var(--ink-1);">{{ group.label }}</h2>
            <p class="text-[11px] mt-0.5" style="color: var(--ink-2);">{{ group.desc }}</p>
          </div>
        </div>

        <div class="divide-y" style="border-color: var(--line-1);">
          <!-- 杠杆区间合并行：下限~上限一体编辑（用户 2026-09-10 明确要求「下限到上限」形态） -->
          <div
            v-if="group.id === 'exposure' && levMinP && levMaxP"
            class="px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4"
            style="border-color: var(--line-1);"
          >
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-xs font-bold" style="color: var(--ink-1);">单笔杠杆区间（下限 ~ 上限）</span>
                <span v-if="isCustomized(levMinP) || isCustomized(levMaxP)" class="text-[11px] px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-500/10 text-amber-400">已自定义</span>
                <span v-if="levInverted" class="text-[11px] px-1.5 py-0.5 rounded border border-rose-500/40 bg-rose-500/10 text-rose-400">下限高于上限：保存将被拒绝</span>
              </div>
              <p class="text-[11px] mt-1 leading-relaxed" style="color: var(--ink-2);">AI 投委会/主脑在区间内按信号强度自主裁决杠杆；区间外执行层强制钳制。调高下限＝强制放大名义敞口，请配合日亏熔断使用。</p>
              <p class="text-[11px] mt-0.5 opacity-60" style="color: var(--ink-2);">
                默认 {{ toDisplay(levMinP, levMinP.default) }} ~ {{ toDisplay(levMaxP, levMaxP.default) }} x · 可配 {{ toDisplay(levMinP, levMinP.min) }} ~ {{ toDisplay(levMaxP, levMaxP.max) }} x · {{ levMinP.key }} / {{ levMaxP.key }}
              </p>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <div class="flex items-center rounded-lg border overflow-hidden" style="background-color: var(--surface-input); border-color: var(--line-1);">
                <input
                  v-model="disp[levMinP.key]"
                  @input="onFieldInput(levMinP)"
                  type="number"
                  :min="toDisplay(levMinP, levMinP.min)"
                  :max="toDisplay(levMinP, levMinP.max)"
                  :step="levMinP.step"
                  class="w-20 sm:w-24 px-2.5 py-2 text-xs outline-none text-right"
                  style="background: transparent; color: var(--ink-1);"
                  :class="draft[levMinP.key] < levMinP.min || draft[levMinP.key] > levMinP.max || levInverted ? 'ring-1 ring-rose-500' : ''"
                />
                <span class="px-1 text-[11px]" style="color: var(--ink-2);">x</span>
                <span class="px-0.5 text-[11px]" style="color: var(--ink-3);">~</span>
                <input
                  v-model="disp[levMaxP.key]"
                  @input="onFieldInput(levMaxP)"
                  type="number"
                  :min="toDisplay(levMaxP, levMaxP.min)"
                  :max="toDisplay(levMaxP, levMaxP.max)"
                  :step="levMaxP.step"
                  class="w-20 sm:w-24 px-2.5 py-2 text-xs outline-none text-right"
                  style="background: transparent; color: var(--ink-1);"
                  :class="draft[levMaxP.key] < levMaxP.min || draft[levMaxP.key] > levMaxP.max || levInverted ? 'ring-1 ring-rose-500' : ''"
                />
                <span class="px-2 text-[11px] whitespace-nowrap select-none" style="color: var(--ink-2);">x</span>
              </div>
            </div>
          </div>
          <div
            v-for="p in schema.params.filter((x: any) => x.group === group.id && x.key !== 'R20_MIN_LEVERAGE' && x.key !== 'R20_MAX_LEVERAGE')"
            :key="p.key"
            class="px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4"
            style="border-color: var(--line-1);"
          >
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-xs font-bold" style="color: var(--ink-1);">{{ p.label }}</span>
                <span v-if="isCustomized(p)" class="text-[11px] px-1.5 py-0.5 rounded border border-amber-500/30 bg-amber-500/10 text-amber-400">已自定义</span>
              </div>
              <p class="text-[11px] mt-1 leading-relaxed" style="color: var(--ink-2);">{{ p.desc }}</p>
              <p class="text-[11px] mt-0.5 opacity-60" style="color: var(--ink-2);">
                默认 {{ toDisplay(p, p.default) }} {{ p.unit }} · 范围 {{ toDisplay(p, p.min) }} ~ {{ toDisplay(p, p.max) }} {{ p.unit }} · <span class="opacity-70">{{ p.key }}</span>
              </p>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <div class="flex items-center rounded-lg border overflow-hidden" style="background-color: var(--surface-input); border-color: var(--line-1);">
                <input
                  v-model="disp[p.key]"
                  @input="onFieldInput(p)"
                  type="number"
                  :min="toDisplay(p, p.min)"
                  :max="toDisplay(p, p.max)"
                  :step="p.step * (p.display_scale || 1)"
                  class="w-24 sm:w-28 px-2.5 py-2 text-xs outline-none text-right"
                  style="background: transparent; color: var(--ink-1);"
                  :class="draft[p.key] < p.min || draft[p.key] > p.max ? 'ring-1 ring-rose-500' : ''"
                />
                <span class="px-2 text-[11px] whitespace-nowrap select-none" style="color: var(--ink-2);">{{ p.unit }}</span>
              </div>
              <button
                v-if="Math.abs((draft[p.key] ?? 0) - p.default) > 1e-9"
                @click="revertOne(p)"
                class="p-2 rounded-lg border transition-colors hover:bg-[var(--surface-3)]"
                style="border-color: var(--line-1); color: var(--ink-2);"
                title="恢复该项默认值"
              >
                <RotateCcw class="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Danger zone: reset (P2 shared component) -->
      <DangerZone
        title="恢复出厂基线"
        description="清除全部自定义覆盖值，执行层回退到代码默认基线；覆盖值本身不可恢复。"
        confirm-phrase="RESET RISK"
        :action-label="busy === 'reset' ? '重置中…' : '全部恢复默认'"
        @confirm="resetAll"
      />
    </template>

    <!-- Sticky save bar -->
    <div
      v-if="schema && dirtyKeys.length"
      class="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 px-4 py-3 rounded-2xl border shadow-2xl flex items-center gap-3"
      style="background-color: var(--surface-2); border-color: var(--line-1);"
    >
      <span class="text-xs" style="color: var(--ink-1);">{{ dirtyKeys.length }} 项修改未保存</span>
      <button
        @click="saveChanges"
        :disabled="busy !== ''"
        class="px-4 py-2 rounded-lg text-xs font-bold flex items-center gap-1.5 disabled:opacity-50"
        style="background-color: var(--accent); color: var(--accent-ink);"
      >
        <Save class="w-3.5 h-3.5" />
        {{ busy === 'save' ? '保存中…' : '保存并生效' }}
      </button>
    </div>
  </div>
</template>

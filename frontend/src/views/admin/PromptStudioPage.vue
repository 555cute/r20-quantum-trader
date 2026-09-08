<script setup lang="ts">
import { useToast } from '../../composables/useToast'
const toast = useToast()
import { ref, computed, onMounted , watch} from 'vue'
import { useI18n } from '../../composables/useI18n'
import PageHeader from '../../components/admin/PageHeader.vue'
const { t } = useI18n()
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import {
  FileText, Plus, ArrowUp, ArrowDown, Eye, CheckCircle2, Save,
  ToggleLeft, ToggleRight, History, RotateCcw, Trash2, Copy,
  Download, Upload, FileUp, Sparkles, X, Code, BookOpen, Layers
} from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()

const lib = ref<any>(null)
const loading = ref(true)

const selectedProfileId = ref<string>('')
const activePipeline = ref<'trading_system' | 'trading_user'>('trading_system')
const workingModules = ref<any[]>([])
const dirty = ref(false)
const historyVisible = ref(false)
const historyList = ref<any[]>([])

// Import Modal State
const importVisible = ref(false)
const importRawJson = ref('')
const importNameOverride = ref('')
const importFileError = ref('')

// Template Variables & Guide State
const variableGuideVisible = ref(false)
const showVarRibbon = ref(false)
const activeEditingIdx = ref<number>(0)
const previewMode = ref<'rendered' | 'template'>('rendered')

const pipelines = [
  { id: 'trading_system', label: '交易 System', desc: '发给交易主脑的规则与决策纪律' },
  { id: 'trading_user', label: '交易 User', desc: '每轮拼装实时行情、动力学与决策任务' },
] as const

const selectedProfile = computed(() => (lib.value?.profiles || []).find((p: any) => p.id === selectedProfileId.value) || null)
const templateVariables = computed(() => lib.value?.template_variables || [])

const compiledPreview = computed(() => {
  if (previewMode.value === 'template') {
    return workingModules.value
      .filter((m) => m.enabled && String(m.content || '').trim())
      .map((m) => `======================= 【${m.title}】 =======================\n${String(m.content).trim()}`)
      .join('\n\n')
  }
  if (!selectedProfile.value?.pipeline_views) return lib.value?.effective_templates?.[activePipeline.value] || ''
  return compileLocal()
})

function compileLocal(): string {
  return workingModules.value
    .filter((m) => m.enabled && String(m.content || '').trim())
    .map((m) => String(m.content).trim())
    .join('\n\n')
}

async function loadLib() {
  loading.value = true
  try {
    lib.value = await api('/api/v1/admin/prompt-library')
    if (!selectedProfileId.value || !(lib.value.profiles || []).some((p: any) => p.id === selectedProfileId.value)) {
      selectedProfileId.value = lib.value.active_profile_id || lib.value.profiles?.[0]?.id || ''
    }
    loadWorkingModules()
  } catch (e: any) {
    toast.err(`加载失败：${e.message}`)
  } finally {
    loading.value = false
  }
}

function loadWorkingModules() {
  const views = selectedProfile.value?.pipeline_views?.[activePipeline.value] || []
  workingModules.value = JSON.parse(JSON.stringify(views)).map((m: any) => ({
    ...m,
    locked: false, // 全量解锁，支持自由修改
  }))
  dirty.value = false
}

function selectProfile(id: string) {
  if (dirty.value && !confirm('当前修改尚未保存，切换方案将丢失修改。继续？')) return
  selectedProfileId.value = id
  loadWorkingModules()
}

function switchPipeline(id: any) {
  if (dirty.value && !confirm('当前模块修改尚未保存，切换管线将丢失修改。继续？')) return
  activePipeline.value = id
  loadWorkingModules()
}

function moveModule(idx: number, dir: -1 | 1) {
  const target = idx + dir
  if (target < 0 || target >= workingModules.value.length) return
  const arr = workingModules.value
  ;[arr[idx], arr[target]] = [arr[target], arr[idx]]
  activeEditingIdx.value = target
  dirty.value = true
}

function toggleModule(m: any) {
  m.enabled = !m.enabled
  dirty.value = true
}

// 在当前激活模块中一键插入变量占位符
function insertVarIntoActiveModule(key: string) {
  if (workingModules.value.length === 0) return
  const idx = Math.min(Math.max(0, activeEditingIdx.value), workingModules.value.length - 1)
  const m = workingModules.value[idx]
  const tag = `{{${key}}}`
  if (m.content && m.content.includes(tag)) {
    toast.warn(`模块「${m.title}」已包含变量 ${tag}`)
    return
  }
  m.content = m.content ? `${m.content.trim()}\n\n${tag}` : tag
  dirty.value = true
  toast.ok(`已插入变量插槽 ${tag} 到模块「${m.title}」`)
}

async function saveProfile() {
  try {
    const pipelinesMap: Record<string, any[]> = {}
    for (const p of pipelines) {
      pipelinesMap[p.id] = p.id === activePipeline.value
        ? workingModules.value
        : JSON.parse(JSON.stringify(selectedProfile.value.pipeline_views?.[p.id] || [])).map((m: any) => ({ ...m, locked: false }))
    }
    await api(`/api/v1/admin/prompt-profiles/${encodeURIComponent(selectedProfileId.value)}`, {
      method: 'PUT',
      body: JSON.stringify({
        name: selectedProfile.value.name,
        description: selectedProfile.value.description || '',
        enabled: true,
        editor_mode: 'modules',
        pipelines: pipelinesMap,
      }),
    })
    toast.ok(`方案「${selectedProfile.value.name}」· ${pipelines.find(p => p.id === activePipeline.value)?.label} 模块布局已保存，下一轮推演自动生效`)
    dirty.value = false
    await loadLib()
  } catch (e: any) {
    toast.err(`保存失败：${e.message}`)
  }
}

async function activateProfile() {
  try {
    await api(`/api/v1/admin/prompt-profiles/${encodeURIComponent(selectedProfileId.value)}/activate`, { method: 'POST', body: '{}' })
    toast.ok(`已激活方案「${selectedProfile.value?.name}」`)
    await loadLib()
  } catch (e: any) {
    toast.err(`激活失败：${e.message}`)
  }
}

async function duplicateProfile() {
  const name = prompt('新方案名称：', `${selectedProfile.value?.name || ''} 副本`)
  if (!name) return
  try {
    const res = await api('/api/v1/admin/prompt-profiles', {
      method: 'POST',
      body: JSON.stringify({ name, description: '', source_id: selectedProfileId.value }),
    })
    toast.ok(`已复制为可编辑方案「${res.profile.name}」`)
    selectedProfileId.value = res.profile.id
    await loadLib()
  } catch (e: any) {
    toast.err(`复制失败：${e.message}`)
  }
}

async function createProfile() {
  const name = prompt('新方案名称：', '我的策略')
  if (!name) return
  try {
    const res = await api('/api/v1/admin/prompt-profiles', {
      method: 'POST',
      body: JSON.stringify({ name, description: '', source_id: 'stable' }),
    })
    toast.ok(`已创建可编辑方案「${res.profile.name}」，现在可以自由增删改模块`)
    selectedProfileId.value = res.profile.id
    await loadLib()
  } catch (e: any) {
    toast.err(`创建失败：${e.message}`)
  }
}

function addModule() {
  workingModules.value.push({
    id: `module-${Date.now().toString(36)}`,
    title: `自定义规则模块 ${workingModules.value.length + 1}`,
    content: '',
    enabled: true,
    locked: false,
    source: 'custom',
  })
  activeEditingIdx.value = workingModules.value.length - 1
  dirty.value = true
}

function removeModule(idx: number) {
  if (!confirm('确定删除该模块？')) return
  workingModules.value.splice(idx, 1)
  if (activeEditingIdx.value >= workingModules.value.length) {
    activeEditingIdx.value = Math.max(0, workingModules.value.length - 1)
  }
  dirty.value = true
}

function duplicateModule(idx: number) {
  const m = workingModules.value[idx]
  if (!m) return
  workingModules.value.splice(idx + 1, 0, {
    ...JSON.parse(JSON.stringify(m)),
    id: `module-${Date.now().toString(36)}`,
    title: `${m.title} 副本`,
    locked: false,
    source: 'custom'
  })
  activeEditingIdx.value = idx + 1
  dirty.value = true
}

async function deleteProfile() {
  if (!confirm(`确定删除方案「${selectedProfile.value?.name}」？`)) return
  try {
    await api(`/api/v1/admin/prompt-profiles/${encodeURIComponent(selectedProfileId.value)}`, { method: 'DELETE' })
    selectedProfileId.value = ''
    await loadLib()
  } catch (e: any) {
    toast.err(`删除失败：${e.message}`)
  }
}

async function showHistory() {
  historyVisible.value = true
  try {
    const res = await api(`/api/v1/admin/prompt-profiles/${encodeURIComponent(selectedProfileId.value)}/history`)
    historyList.value = res.history || []
  } catch (e: any) {
    toast.err(`历史加载失败：${e.message}`)
  }
}

async function rollback(revId: string) {
  if (!confirm('回滚将覆盖当前方案内容，确定？')) return
  try {
    await api(`/api/v1/admin/prompt-profiles/${encodeURIComponent(selectedProfileId.value)}/rollback`, {
      method: 'POST',
      body: JSON.stringify({ revision_id: revId }),
    })
    toast.ok('已回滚到所选历史版本')
    historyVisible.value = false
    await loadLib()
  } catch (e: any) {
    toast.err(`回滚失败：${e.message}`)
  }
}

// 导出策略方案为 JSON
async function exportProfile() {
  if (!selectedProfileId.value) return
  try {
    const res = await api(`/api/v1/admin/prompt-profiles/${encodeURIComponent(selectedProfileId.value)}/export`)
    const blob = new Blob([JSON.stringify(res, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `r20-strategy-${selectedProfile.value?.name || 'profile'}-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(a.href)
    toast.ok(`方案「${selectedProfile.value?.name}」已成功导出为 JSON 策略包`)
  } catch (e: any) {
    toast.err(`导出失败：${e.message}`)
  }
}

// 处理导入文件选择
function handleFileSelect(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = (e) => {
    try {
      const text = e.target?.result as string
      JSON.parse(text)
      importRawJson.value = text
      importFileError.value = ''
      if (!importNameOverride.value && file.name) {
        importNameOverride.value = file.name.replace(/\.json$/i, '').replace(/^r20-strategy-/, '')
      }
    } catch {
      importFileError.value = '文件内容不是合法的 JSON 格式'
    }
  }
  reader.readAsText(file)
}

// 提交导入
async function submitImport() {
  importFileError.value = ''
  if (!importRawJson.value.trim()) {
    importFileError.value = '请先选择 JSON 策略文件或粘贴 JSON 内容'
    return
  }
  try {
    const payload = JSON.parse(importRawJson.value.trim())
    const res = await api('/api/v1/admin/prompt-profiles/import', {
      method: 'POST',
      body: JSON.stringify({
        payload,
        name_override: importNameOverride.value.trim() || undefined,
      }),
    })
    toast.ok(`成功导入策略方案「${res.profile.name}」！`)
    importVisible.value = false
    importRawJson.value = ''
    importNameOverride.value = ''
    selectedProfileId.value = res.profile.id
    await loadLib()
  } catch (e: any) {
    importFileError.value = `导入失败：${e.message}`
  }
}

function copyPreview() {
  navigator.clipboard.writeText(compiledPreview.value)
  toast.ok('编译后实发 Prompt 已复制')
}

onMounted(loadLib)
</script>

<template>

    <PageHeader :title="t('nav.admin.prompts')" :description="t('admin.promptDesc')" />
  <div class="space-y-4 text-xs">
    <!-- Header Summary & Plaza Gateway -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 pb-1">
      <div class="flex items-center space-x-2">
        <Sparkles class="w-4 h-4 text-blue-400 shrink-0" />
        <p class="text-xs text-[var(--ink-2)] font-sans">
          核心交易消息管线自由编排，支持标准语义变量插槽。右侧仅对照模板拼接文本与源码，不代表实时发送消息。
        </p>
      </div>
      <div class="flex items-center space-x-1.5 shrink-0">
        <button
          @click="showVarRibbon = !showVarRibbon"
          class="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg border text-xs transition-all cursor-pointer shadow-xs"
          :style="showVarRibbon ? { backgroundColor: 'var(--accent-bg)', borderColor: 'var(--accent-line)', color: 'var(--accent)', fontWeight: 'bold' } : { backgroundColor: 'var(--surface-1)', borderColor: 'var(--line-1)', color: 'var(--ink-2)' }"
          title="展开/收起快捷变量标签条"
        >
          <Layers class="w-3.5 h-3.5" />
          <span>{{ showVarRibbon ? '收起变量条' : '插入变量' }}</span>
        </button>
        <button
          @click="variableGuideVisible = true"
          class="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg border text-xs transition-all cursor-pointer shadow-xs"
          style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--accent);"
          title="查看所有可用数据插槽与变量字典"
        >
          <BookOpen class="w-3.5 h-3.5" />
          <span>变量字典</span>
        </button>
        <button
          @click="importVisible = true"
          class="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg border text-xs transition-all cursor-pointer shadow-xs"
          style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-1);"
          title="从本地文件或文本导入策略方案"
        >
          <Upload class="w-3.5 h-3.5" />
          <span>导入方案</span>
        </button>
        <button
          @click="exportProfile"
          class="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg border text-xs transition-all cursor-pointer shadow-xs"
          style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-1);"
          title="将当前方案导出为 JSON 策略包"
        >
          <Download class="w-3.5 h-3.5" />
          <span>导出策略</span>
        </button>
      </div>
    </div>

    <!-- Collapsible Quick Variable Inserter Ribbon -->
    <div
      v-if="showVarRibbon"
      class="rounded-xl border p-3 flex flex-wrap items-center gap-2 shadow-xs transition-colors"
      style="background-color: var(--surface-2); border-color: var(--line-1);"
    >
      <div class="flex items-center space-x-1.5 text-[11px] font-bold mr-1" style="color: var(--ink-2);">
        <Layers class="w-3.5 h-3.5" style="color: var(--accent);" />
        <span>快捷变量插槽:</span>
      </div>
      <button
        v-for="v in templateVariables"
        :key="v.key"
        @click="insertVarIntoActiveModule(v.key)"
        class="flex items-center space-x-1 px-2 py-1 rounded-lg border text-[11px] transition-all cursor-pointer shadow-xs hover:border-[var(--accent)]"
        style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-1);"
        :title="`${v.description}\n点击插入到正在编辑的模块 #${activeEditingIdx + 1}`"
      >
        <span class="font-bold" style="color: var(--accent);">+</span>
        <span class="font-sans font-medium">{{ v.label }}</span>
        <code class="text-[11px] ml-0.5 opacity-60">&#123;&#123;{{ v.key }}&#125;&#125;</code>
      </button>
    </div>

    <!-- Alert / Banner Message -->
    <!-- Loading State -->
    <div v-if="loading" class="py-12 text-center text-xs" style="color: var(--ink-2);">正在加载提示词策略库...</div>

    <!-- Main Workspace Grid -->
    <div v-else-if="lib" class="grid grid-cols-1 xl:grid-cols-[240px_minmax(0,1fr)_400px] gap-3.5 items-start">
      <!-- Left: Profile List -->
      <div class="rounded-xl border p-3 space-y-2 h-fit shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center justify-between px-1 pb-2 border-b" style="border-color: var(--line-1);">
          <span class="text-[11px] font-bold uppercase tracking-wider" style="color: var(--ink-3);">策略方案列表</span>
          <button
            v-if="auth.isSuperadmin"
            @click="createProfile"
            class="flex items-center space-x-1 px-2 py-1 rounded-lg text-[11px] font-bold cursor-pointer shadow-xs transition-colors"
            style="background-color: var(--accent); color: var(--accent-ink);"
          >
            <Plus class="w-3 h-3" />
            <span>新建方案</span>
          </button>
        </div>
        <div class="space-y-1.5 max-h-[calc(100vh-220px)] overflow-y-auto pr-0.5">
          <button
            v-for="p in lib.profiles"
            :key="p.id"
            @click="selectProfile(p.id)"
            class="w-full text-left p-2.5 rounded-xl border transition-all cursor-pointer group shadow-xs"
            :style="selectedProfileId === p.id
              ? { borderColor: 'var(--accent-line)', backgroundColor: 'var(--accent-bg)' }
              : { borderColor: 'var(--line-1)', backgroundColor: 'var(--surface-1)' }"
          >
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold transition-colors" style="color: var(--ink-1);">{{ p.name }}</span>
              <span v-if="p.id === lib.active_profile_id" class="text-[11px] font-bold px-1.5 py-0.2 rounded border" style="background-color: var(--up-bg); color: var(--up); border-color: var(--up-line);">
                当前生效
              </span>
            </div>
            <div class="text-[11px] mt-1 line-clamp-1" style="color: var(--ink-2);">
              {{ p.description || '无详细描述' }}
            </div>
          </button>
        </div>
      </div>

      <!-- Center: Modules Editor (100% Unlocked) -->
      <div class="rounded-xl border p-4 min-w-0 shadow-xs space-y-3 transition-colors flex flex-col" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <!-- Pipeline Navigation Tabs -->
        <div class="flex items-center justify-between border-b pb-2" style="border-color: var(--line-1);">
          <div class="flex space-x-1.5">
            <button
              v-for="p in pipelines"
              :key="p.id"
              @click="switchPipeline(p.id)"
              class="px-3.5 py-1.5 text-xs font-bold rounded-lg cursor-pointer transition-all"
              :style="activePipeline === p.id
                ? { backgroundColor: 'var(--ink-1)', color: 'var(--surface-2)' }
                : { color: 'var(--ink-2)' }"
            >
              {{ p.label }}
            </button>
          </div>
          <span class="text-[11px] font-bold" style="color: var(--ink-2);">
            当前方案：<span style="color: var(--ink-1);">{{ selectedProfile?.name }}</span>
          </span>
        </div>

        <!-- Module Cards List -->
        <div class="space-y-3 max-h-[calc(100vh-320px)] overflow-y-auto pr-1">
          <div
            v-for="(m, idx) in workingModules"
            :key="m.id"
            @click="activeEditingIdx = idx"
            class="border rounded-xl p-3.5 transition-all shadow-xs"
            :style="{
              backgroundColor: m.enabled ? 'var(--surface-1)' : 'var(--surface-2)',
              borderColor: activeEditingIdx === idx ? 'var(--accent-line)' : 'var(--line-1)',
              opacity: m.enabled ? '1' : '0.5'
            }"
            :class="activeEditingIdx === idx ? 'ring-1 ring-blue-500/30' : ''"
          >
            <div class="flex items-center justify-between mb-2 gap-2">
              <!-- Title & Ordering -->
              <div class="flex items-center space-x-2 min-w-0 flex-1">
                <span class="w-5 h-5 rounded font-bold text-[11px] flex items-center justify-center shrink-0 border" style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-2);">
                  #{{ idx + 1 }}
                </span>
                <button
                  @click.stop="moveModule(idx, -1)"
                  :disabled="idx === 0"
                  class="p-1 rounded disabled:opacity-20 cursor-pointer transition-colors"
                  style="color: var(--ink-2);"
                  title="上移模块"
                >
                  <ArrowUp class="w-3.5 h-3.5" />
                </button>
                <button
                  @click.stop="moveModule(idx, 1)"
                  :disabled="idx === workingModules.length - 1"
                  class="p-1 rounded disabled:opacity-20 cursor-pointer transition-colors"
                  style="color: var(--ink-2);"
                  title="下移模块"
                >
                  <ArrowDown class="w-3.5 h-3.5" />
                </button>
                <input
                  v-model="m.title"
                  class="bg-transparent border-b border-transparent focus:border-blue-500 text-xs font-bold outline-none flex-1 min-w-[120px] transition-colors"
                  style="color: var(--ink-1);"
                  placeholder="模块标题"
                  @input="dirty = true"
                />
              </div>

              <!-- Controls: Copy, Delete, Toggle -->
              <div class="flex items-center space-x-1.5 shrink-0">
                <button
                  @click.stop="duplicateModule(idx)"
                  class="p-1.5 rounded-lg cursor-pointer transition-colors"
                  style="color: var(--ink-2);"
                  title="复制模块"
                >
                  <Copy class="w-3.5 h-3.5" />
                </button>
                <button
                  @click.stop="removeModule(idx)"
                  class="p-1.5 rounded-lg text-rose-400 hover:opacity-80 cursor-pointer transition-opacity"
                  title="删除模块"
                >
                  <Trash2 class="w-3.5 h-3.5" />
                </button>
                <button
                  @click.stop="toggleModule(m)"
                  class="cursor-pointer transition-colors p-1"
                  :class="m.enabled ? 'text-emerald-500' : 'text-zinc-500'"
                  :title="m.enabled ? '已启用该模块 (点击禁用)' : '已禁用该模块 (点击启用)'"
                >
                  <ToggleRight v-if="m.enabled" class="w-5 h-5" />
                  <ToggleLeft v-else class="w-5 h-5" />
                </button>
              </div>
            </div>

            <!-- Content Area (100% Editable) -->
            <textarea
              v-model="m.content"
              @focus="activeEditingIdx = idx"
              rows="5"
              class="w-full rounded-lg px-3 py-2 text-xs outline-none border resize-y leading-relaxed transition-colors select-text"
              style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);"
              placeholder="编写该模块的提示词或插入 {{variable}} 数据插槽..."
              @input="dirty = true"
            ></textarea>
          </div>

          <!-- Add Module Button -->
          <button
            @click="addModule"
            class="w-full py-2.5 rounded-xl border border-dashed text-xs cursor-pointer flex items-center justify-center space-x-1.5 transition-all shadow-xs"
            style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-2);"
          >
            <Plus class="w-4 h-4" style="color: var(--accent);" />
            <span>新增自定义规则模块</span>
          </button>
        </div>

        <!-- Action Bar -->
        <div class="flex flex-wrap items-center justify-between gap-2 pt-3 border-t" style="border-color: var(--line-1);">
          <div class="flex flex-wrap items-center gap-2">
            <button
              @click="saveProfile"
              :disabled="!dirty"
              class="btn-primary-text flex items-center space-x-1.5 px-4 py-2 rounded-lg font-bold transition-all shadow-xs"
              :class="dirty ? 'cursor-pointer hover:bg-blue-600 active:scale-95' : 'opacity-40 cursor-not-allowed'"
              style="background-color: var(--accent); color: var(--accent-ink) !important;"
            >
              <Save class="w-4 h-4" style="color: #FFFFFF;" />
              <span style="color: #FFFFFF;">保存当前方案{{ dirty ? ' *' : '' }}</span>
            </button>
            <button
              v-if="selectedProfileId !== lib.active_profile_id && auth.isSuperadmin"
              @click="activateProfile"
              class="btn-primary-text flex items-center space-x-1.5 px-3.5 py-2 rounded-lg font-bold cursor-pointer hover:bg-emerald-600 transition-all shadow-xs"
              style="background-color: var(--up); color: #FFFFFF !important;"
            >
              <CheckCircle2 class="w-4 h-4" style="color: #FFFFFF;" />
              <span style="color: #FFFFFF;">激活为实盘方案</span>
            </button>
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <button
              v-if="auth.isSuperadmin"
              @click="duplicateProfile"
              class="flex items-center space-x-1 px-3 py-2 rounded-lg border text-xs font-bold cursor-pointer transition-all shadow-xs"
              style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);"
            >
              <Copy class="w-3.5 h-3.5" />
              <span>复制副本</span>
            </button>
            <button
              @click="showHistory"
              class="flex items-center space-x-1 px-3 py-2 rounded-lg border text-xs font-bold cursor-pointer transition-all shadow-xs"
              style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);"
            >
              <History class="w-3.5 h-3.5" />
              <span>历史版本</span>
            </button>
            <button
              v-if="selectedProfileId !== lib.active_profile_id && auth.isSuperadmin"
              @click="deleteProfile"
              class="flex items-center space-x-1 px-3 py-2 rounded-lg border text-xs font-bold cursor-pointer transition-all shadow-xs"
              style="background-color: var(--down-bg); border-color: var(--down-line); color: var(--down);"
            >
              <Trash2 class="w-3.5 h-3.5" />
              <span>删除</span>
            </button>
          </div>
        </div>
      </div>

      <!-- Right: Compiled Live Preview with Dual-Mode Toggle -->
      <div class="rounded-xl border p-4 h-fit shadow-xs space-y-3 transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center justify-between pb-2 border-b" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <Eye class="w-4 h-4 text-cyan-400" />
            <h3 class="text-xs font-bold uppercase tracking-wider" style="color: var(--ink-1);">模板组装预览</h3>
          </div>
          <div class="flex items-center space-x-1.5">
            <!-- Mode Switch -->
            <div class="flex p-0.5 rounded-lg border" style="background-color: var(--surface-1); border-color: var(--line-1);">
              <button
                @click="previewMode = 'rendered'"
                class="px-2 py-0.5 rounded text-[11px] font-bold cursor-pointer transition-all"
                :style="previewMode === 'rendered' ? { backgroundColor: 'var(--ink-1)', color: 'var(--surface-2)' } : { color: 'var(--ink-2)' }"
              >
                拼接文本
              </button>
              <button
                @click="previewMode = 'template'"
                class="px-2 py-0.5 rounded text-[11px] font-bold cursor-pointer transition-all"
                :style="previewMode === 'template' ? { backgroundColor: 'var(--ink-1)', color: 'var(--surface-2)' } : { color: 'var(--ink-2)' }"
              >
                模板源码
              </button>
            </div>
            <button
              @click="copyPreview"
              class="px-2 py-1 rounded-lg border text-[11px] cursor-pointer transition-all shadow-xs"
              style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);"
            >
              复制
            </button>
          </div>
        </div>
        <div class="text-[11px] flex items-center justify-between" style="color: var(--ink-3);">
          <span>{{ previewMode === 'rendered' ? '仅本地拼接，未代入实时数据；base合并以发送阶段为准' : '显示模块包含的原始模版语法与插槽' }}</span>
          <span class="num font-bold" style="color: var(--accent);">{{ compiledPreview.length }} 字符</span>
        </div>
        <pre class="border rounded-xl p-3 text-[11px] whitespace-pre-wrap leading-relaxed max-h-[calc(100vh-280px)] overflow-y-auto select-text" style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-1);">{{ compiledPreview || '（空）' }}</pre>
      </div>
    </div>

    <!-- Template Variables Guide Modal -->
    <div
      v-if="variableGuideVisible"
      class="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4"
      @click.self="variableGuideVisible = false"
    >
      <div class="border rounded-2xl p-5 sm:p-6 w-full max-w-2xl max-h-[90dvh] overflow-y-auto space-y-4 shadow-2xl transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center justify-between pb-3 border-b" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2.5">
            <div class="w-7 h-7 rounded-lg flex items-center justify-center border" style="background-color: var(--accent-bg); border-color: var(--accent-line); color: var(--accent);">
              <BookOpen class="w-4 h-4" />
            </div>
            <div>
              <h3 class="text-sm font-bold" style="color: var(--ink-1);">系统数据插槽与变量字典</h3>
              <p class="text-[11px]" style="color: var(--ink-2);">可以在任意提示词模块中自由引用，系统推演时将自动替换为最新真实数据</p>
            </div>
          </div>
          <button @click="variableGuideVisible = false" class="cursor-pointer p-1" style="color: var(--ink-2);">
            <X class="w-4 h-4" />
          </button>
        </div>

        <div class="space-y-3">
          <div
            v-for="v in templateVariables"
            :key="v.key"
            class="p-3.5 rounded-xl border space-y-2 transition-colors"
            style="background-color: var(--surface-1); border-color: var(--line-1);"
          >
            <div class="flex items-center justify-between">
              <div class="flex items-center space-x-2">
                <span class="px-2 py-0.5 rounded text-[11px] font-bold border" style="background-color: var(--accent-bg); border-color: var(--accent-line); color: var(--accent);">
                  {{ v.category }}
                </span>
                <span class="text-xs font-bold" style="color: var(--ink-1);">{{ v.label }}</span>
                <code class="px-2 py-0.5 rounded border text-[11px]" style="background-color: var(--surface-3); border-color: var(--line-1); color: var(--warn);">
                  &#123;&#123;{{ v.key }}&#125;&#125;
                </code>
              </div>
              <button
                @click="insertVarIntoActiveModule(v.key); variableGuideVisible = false"
                class="btn-primary-text px-2.5 py-1 rounded-lg font-bold text-[11px] cursor-pointer shadow-xs hover:bg-blue-600 transition-colors"
                style="background-color: var(--accent); color: var(--accent-ink) !important;"
              >
                <span style="color: #FFFFFF;">插入到当前模块</span>
              </button>
            </div>
            <p class="text-[11px] font-sans" style="color: var(--ink-2);">{{ v.description }}</p>
            <div v-if="v.sample" class="border rounded-lg p-2.5 text-[11px] whitespace-pre-wrap max-h-24 overflow-y-auto" style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-2);">
              {{ v.sample }}
            </div>
          </div>
        </div>

        <div class="flex justify-end pt-3 border-t" style="border-color: var(--line-1);">
          <button
            @click="variableGuideVisible = false"
            class="px-5 py-2 rounded-xl border text-xs cursor-pointer shadow-xs"
            style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-2);"
          >
            关闭字典
          </button>
        </div>
      </div>
    </div>

    <!-- Import Modal -->
    <div
      v-if="importVisible"
      class="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4"
      @click.self="importVisible = false"
    >
      <div class="border rounded-2xl p-5 sm:p-6 w-full max-w-xl max-h-[90dvh] overflow-y-auto space-y-4 shadow-2xl transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center justify-between pb-3 border-b" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2.5">
            <div class="w-7 h-7 rounded-lg flex items-center justify-center border" style="background-color: var(--accent-bg); border-color: var(--accent-line); color: var(--accent);">
              <Upload class="w-4 h-4" />
            </div>
            <div>
              <h3 class="text-sm font-bold" style="color: var(--ink-1);">导入策略方案包</h3>
              <p class="text-[11px]" style="color: var(--ink-2);">支持标准导出包 (v1~v4)、整库导出文件与裸方案对象三种 JSON 格式</p>
            </div>
          </div>
          <button @click="importVisible = false" class="cursor-pointer p-1" style="color: var(--ink-2);">
            <X class="w-4 h-4" />
          </button>
        </div>

        <div v-if="importFileError" class="p-3 rounded-lg text-xs border" style="background-color: var(--down-bg); border-color: var(--down-line); color: var(--down);">
          {{ importFileError }}
        </div>

        <div>
          <label class="block text-xs font-bold mb-1.5" style="color: var(--ink-1);">方式一：选择本地 .json 策略文件</label>
          <div class="flex items-center space-x-3">
            <label class="flex items-center space-x-2 px-3 py-2 rounded-xl border border-dashed text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--accent);">
              <FileUp class="w-4 h-4" />
              <span>选择策略文件 (.json)</span>
              <input type="file" accept=".json" class="hidden" @change="handleFileSelect" />
            </label>
          </div>
        </div>

        <div>
          <label class="block text-xs font-bold mb-1.5" style="color: var(--ink-1);">方式二：或直接粘贴策略 JSON 文本</label>
          <textarea
            v-model="importRawJson"
            rows="6"
            class="w-full border rounded-xl px-3 py-2 text-xs outline-none resize-y transition-colors"
            style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);"
            placeholder='{"format": "r20-prompt-profile", "version": 3, "profile": { ... }}'
          ></textarea>
        </div>

        <div>
          <label class="block text-xs font-bold mb-1.5" style="color: var(--ink-1);">自定义导入方案名称（可选）</label>
          <input
            v-model="importNameOverride"
            type="text"
            class="w-full border rounded-xl px-3 py-2 text-xs outline-none transition-colors"
            style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);"
            placeholder="留空则自动采用策略包内部的原始名称"
          />
        </div>

        <div class="flex items-center justify-end space-x-2 pt-3 border-t" style="border-color: var(--line-1);">
          <button
            @click="importVisible = false"
            class="px-4 py-2 rounded-xl border text-xs cursor-pointer shadow-xs"
            style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-2);"
          >
            取消
          </button>
          <button
            @click="submitImport"
            class="px-5 py-2 rounded-xl font-bold text-xs cursor-pointer transition-all shadow-xs"
            style="background-color: var(--accent); color: var(--accent-ink);"
          >
            确认导入并载入方案
          </button>
        </div>
      </div>
    </div>

    <!-- History Modal -->
    <div
      v-if="historyVisible"
      class="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4"
      @click.self="historyVisible = false"
    >
      <div class="border rounded-2xl p-5 sm:p-6 w-full max-w-[560px] max-h-[85dvh] overflow-y-auto shadow-2xl transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center justify-between mb-4 pb-3 border-b" style="border-color: var(--line-1);">
          <h3 class="text-sm font-bold" style="color: var(--ink-1);">版本历史 · {{ selectedProfile?.name }}</h3>
          <button @click="historyVisible = false" class="cursor-pointer text-xs p-1" style="color: var(--ink-2);">
            <X class="w-4 h-4" />
          </button>
        </div>
        <div v-if="historyList.length === 0" class="text-xs py-8 text-center" style="color: var(--ink-2);">暂无历史版本</div>
        <div
          v-for="h in historyList"
          :key="h.id || h.revision_id"
          class="flex items-center justify-between py-2.5 border-b"
          style="border-color: var(--line-1);"
        >
          <div>
            <div class="text-xs font-bold" style="color: var(--ink-1);">{{ h.note || h.summary || h.id || h.revision_id }}</div>
            <div class="text-[11px] num" style="color: var(--ink-3);">{{ h.created_at || h.time }} · {{ h.actor || 'system' }}</div>
          </div>
          <button
            @click="rollback(h.id || h.revision_id)"
            class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-[11px] cursor-pointer transition-all shadow-xs"
            style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);"
          >
            <RotateCcw class="w-3 h-3" />
            <span>回滚</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

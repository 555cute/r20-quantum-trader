<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import {
  Cpu,
  Plus,
  Zap,
  Trash2,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  Copy,
  Check,
  Search,
  ArrowLeft,
  Settings,
  Layers,
  Eye,
  EyeOff,
  DownloadCloud,
  Wrench,
  Image as ImageIcon,
  MessageSquare,
  Sparkles,
  Clock,
  Save,
} from 'lucide-vue-next'

const { api } = useApi()

// State
const cfg = ref<any>(null)
const loading = ref(true)
const searchQuery = ref('')

// Navigation: 'list' (一级：供应商列表) | 'detail' (二级：供应商配置与模型详情)
const currentView = ref<'list' | 'detail'>('list')
const selectedProvider = ref<any>(null)
const detailTab = ref<'config' | 'models'>('config')

// Password visibility toggles
const showApiKey = ref(false)

// Edit / Add Provider Form
const providerForm = ref<any>({
  id: '',
  name: '',
  type: 'OpenAI',
  group: '其他',
  enabled: true,
  multi_key_enabled: false,
  response_api_enabled: false,
  base_url: '',
  api_key: '',
  api_path: '/chat/completions',
  description: '',
})

// Test Connection State
const testResult = ref<any>(null)
const testLoading = ref(false)
const testingModelId = ref<string | null>(null)

// Remote Fetch State & Modal
const fetchModalVisible = ref(false)
const fetchingRemote = ref(false)
const remoteFetchResult = ref<any>(null)
const remoteSearch = ref('')
const customFetchUrl = ref('')
const customFetchKey = ref('')

// Add / Edit Single Model Modal
const modelModalVisible = ref(false)
const editingModel = ref<any>(null)
const modelForm = ref<any>({
  id: '',
  name: '',
  provider_id: '',
  capabilities: ['chat'],
  reasoning_effort: 'high',
  context_length: 128000,
  max_output_tokens: null as number | null,
  description: '',
})

// Copied feedback
const copiedText = ref<string | null>(null)

// Global Reasoning & Thinking Timeout State
const thinkingTimeoutInput = ref<number>(120)
const maxOutputTokensInput = ref<number>(4096)
const savingSettings = ref(false)
const settingsResult = ref<any>(null)

function setPresetTimeout(sec: number) {
  thinkingTimeoutInput.value = sec
}

async function saveGlobalSettings() {
  savingSettings.value = true
  settingsResult.value = null
  try {
    const res = await api('/api/v1/admin/llm/settings', {
      method: 'POST',
      body: JSON.stringify({
        thinking_timeout: Number(thinkingTimeoutInput.value) || 120,
        max_output_tokens: Number(maxOutputTokensInput.value) || 4096,
        active_model_id: cfg.value?.active_model_id,
        reasoning_effort: cfg.value?.active_reasoning_effort,
      }),
    })
    await loadConfig()
    settingsResult.value = { ok: true, message: `推演参数已保存：思考上限 ${thinkingTimeoutInput.value} 秒，输出上限 ${maxOutputTokensInput.value} tokens` }
    setTimeout(() => {
      settingsResult.value = null
    }, 3500)
  } catch (err: any) {
    settingsResult.value = { ok: false, error: err.message }
  } finally {
    savingSettings.value = false
  }
}

// ----------------- Data Loading -----------------
async function loadConfig() {
  loading.value = true
  try {
    cfg.value = await api('/api/v1/admin/llm/models')
    if (cfg.value?.thinking_timeout) {
      thinkingTimeoutInput.value = Number(cfg.value.thinking_timeout)
    }
    maxOutputTokensInput.value = Number(cfg.value?.max_output_tokens) || 4096
    if (selectedProvider.value) {
      const updated = cfg.value.providers?.find((p: any) => p.id === selectedProvider.value.id)
      if (updated) {
        selectedProvider.value = updated
      }
    }
  } catch (e: any) {
    console.error('Failed to load LLM config:', e)
  } finally {
    loading.value = false
  }
}

// Model Effort Options depending on model family
const availableEffortOptions = computed(() => {
  const mid = (modelForm.value.id || '').toLowerCase()
  // 智能自适应：支持 GPT-6、GPT-5 以及未来全系前沿具备极值推演能力的旗舰模型
  const supportsExtreme = mid.includes('gpt-6') || mid.includes('gpt-5') || mid.includes('o3') || mid.includes('o4') || mid.includes('ultra') || mid.includes('max')
  
  const options = [
    { value: 'high', label: '高 (high)' },
    { value: 'medium', label: '中 (medium)' },
    { value: 'low', label: '低 (low)' },
    { value: 'minimal', label: '极简 (minimal)' },
    { value: 'none', label: '关闭 (none)' },
    // 后端 STANDARD_REASONING_EFFORTS 含 auto/minimal：缺了会让已存 "auto" 的模型在下拉框中无法回显
    { value: 'auto', label: '自动 (auto)' },
  ]
  if (supportsExtreme) {
    options.unshift(
      { value: 'max', label: '极值 (max)' },
      { value: 'xhigh', label: '超高 (xhigh)' }
    )
  }
  return options
})

// ----------------- Filtered Providers -----------------
const filteredProviders = computed(() => {
  if (!cfg.value?.providers) return []
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return cfg.value.providers
  return cfg.value.providers.filter((p: any) =>
    p.name.toLowerCase().includes(q) ||
    (p.type && p.type.toLowerCase().includes(q)) ||
    (p.group && p.group.toLowerCase().includes(q)) ||
    (p.id && p.id.toLowerCase().includes(q))
  )
})

// Effective output cap matches get_active_llm_runtime: active_model_id in cfg.models, else global.
// Saved cfg only — not selectedProvider, modelForm, or unsaved maxOutputTokensInput.
const activeOutputCap = computed(() => {
  const globalCap = Number(cfg.value?.max_output_tokens) || 4096
  const activeId = cfg.value?.active_model_id
  const target = (cfg.value?.models || []).find((m: any) => m?.id === activeId)
  const modelMax = target?.max_output_tokens
  if (modelMax != null && modelMax !== '') {
    const parsed = Number(modelMax)
    if (Number.isFinite(parsed) && parsed >= 1) {
      return { tokens: parsed, label: '模型覆盖' }
    }
  }
  return { tokens: globalCap, label: '全局继承' }
})

// ----------------- Provider Actions -----------------
function openAddProviderModal() {
  selectedProvider.value = { id: '', name: '新建自定义供应商', is_new: true }
  providerForm.value = {
    id: '',
    name: '',
    type: 'OpenAI 兼容',
    group: '自定义',
    enabled: true,
    multi_key_enabled: false,
    response_api_enabled: false,
    api_format: 'openai_chat',
    base_url: '',
    api_key: '',
    api_path: '/chat/completions',
    description: '',
  }
  detailTab.value = 'config'
  currentView.value = 'detail'
  testResult.value = null
  showApiKey.value = false
}

function selectProvider(p: any) {
  selectedProvider.value = p
  const format = p.api_format || (p.id === 'claude' ? 'claude_messages' : 'openai_chat')
  providerForm.value = {
    id: p.id,
    name: p.name,
    type: p.type || p.name,
    group: p.group || '其他',
    enabled: !!p.enabled,
    multi_key_enabled: !!p.multi_key_enabled,
    response_api_enabled: !!p.response_api_enabled,
    api_format: format,
    base_url: p.base_url || '',
    api_key: '',
    api_path: p.api_path || (format === 'claude_messages' ? '/messages' : (format === 'openai_responses' ? '/responses' : '/chat/completions')),
    description: p.description || '',
  }
  detailTab.value = 'config'
  currentView.value = 'detail'
  testResult.value = null
  showApiKey.value = false
}

function onApiFormatChange() {
  const fmt = providerForm.value.api_format
  if (fmt === 'claude_messages') {
    if (!providerForm.value.api_path || providerForm.value.api_path === '/chat/completions' || providerForm.value.api_path === '/responses') {
      providerForm.value.api_path = '/messages'
    }
    providerForm.value.response_api_enabled = false
  } else if (fmt === 'openai_responses') {
    if (!providerForm.value.api_path || providerForm.value.api_path === '/chat/completions' || providerForm.value.api_path === '/messages') {
      providerForm.value.api_path = '/responses'
    }
    providerForm.value.response_api_enabled = true
  } else {
    if (!providerForm.value.api_path || providerForm.value.api_path === '/messages' || providerForm.value.api_path === '/responses') {
      providerForm.value.api_path = '/chat/completions'
    }
    providerForm.value.response_api_enabled = false
  }
}

function goBackToList() {
  currentView.value = 'list'
  selectedProvider.value = null
  testResult.value = null
}

async function toggleProviderQuick(p: any, e: Event) {
  e.stopPropagation()
  try {
    const res = await api(`/api/v1/admin/llm/providers/${encodeURIComponent(p.id)}/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled: !p.enabled }),
    })
    p.enabled = res.enabled
    await loadConfig()
  } catch (err: any) {
    alert(err.message)
  }
}

async function saveProviderConfig() {
  try {
    const payload = { ...providerForm.value }
    if (!payload.id) {
      payload.id = payload.name.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '_')
    }
    if (!payload.api_key) delete payload.api_key
    const saved = await api('/api/v1/admin/llm/providers', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    alert('供应商配置已成功保存！')
    await loadConfig()
    const savedId = saved?.id || payload.id
    const created = cfg.value.providers?.find((p: any) => p.id === savedId)
    if (created) {
      selectedProvider.value = created
    }
  } catch (err: any) {
    alert(err.message)
  }
}

async function clearCurrentProviderModels() {
  if (!selectedProvider.value) return
  if (!confirm(`确定要清空 ${selectedProvider.value.name} 旗下的全部模型吗？`)) return
  try {
    await api(`/api/v1/admin/llm/providers/${encodeURIComponent(selectedProvider.value.id)}/models`, {
      method: 'DELETE',
    })
    alert('已清空该供应商所有模型！')
    await loadConfig()
  } catch (err: any) {
    alert(err.message)
  }
}

async function removeProvider() {
  const p = selectedProvider.value
  if (!p || p.is_new) return
  const n = p.models_count ?? p.models?.length ?? 0
  const warn = n > 0 ? `\n其名下 ${n} 个模型将一并删除！` : ''
  if (!confirm(`确定要删除供应商「${p.name}」吗？${warn}\n（若它挂着当前主脑激活模型，需先切换模型）`)) return
  try {
    await api(`/api/v1/admin/llm/providers/${encodeURIComponent(p.id)}`, {
      method: 'DELETE',
    })
    alert(`供应商 ${p.name} 已删除`)
    goBackToList()
    await loadConfig()
  } catch (err: any) {
    alert(err.message)
  }
}

// ----------------- Remote Fetch -----------------
function openFetchDialog() {
  if (!selectedProvider.value) return
  customFetchUrl.value = selectedProvider.value.base_url || ''
  customFetchKey.value = ''
  remoteFetchResult.value = null
  remoteSearch.value = ''
  fetchModalVisible.value = true
  // 优化：若当前供应商已配置好 Base URL，弹窗打开时自动发起探测拉取，免除重复输入与多次点击
  executeRemoteFetch()
}

async function executeRemoteFetch() {
  if (!selectedProvider.value) return
  fetchingRemote.value = true
  remoteFetchResult.value = null
  try {
    const payload: any = {
      provider_id: selectedProvider.value.id,
      base_url: customFetchUrl.value.trim() || selectedProvider.value.base_url,
    }
    if (customFetchKey.value.trim()) {
      payload.api_key = customFetchKey.value.trim()
    }
    const res = await api('/api/v1/admin/llm/fetch-models', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    remoteFetchResult.value = res
  } catch (err: any) {
    remoteFetchResult.value = { ok: false, error: err.message }
  } finally {
    fetchingRemote.value = false
  }
}

const filteredRemoteModels = computed(() => {
  if (!remoteFetchResult.value?.models) return []
  const q = remoteSearch.value.trim().toLowerCase()
  if (!q) return remoteFetchResult.value.models
  return remoteFetchResult.value.models.filter((m: any) =>
    m.id.toLowerCase().includes(q) ||
    (m.name && m.name.toLowerCase().includes(q))
  )
})

async function importRemoteModel(m: any, autoActivate = false) {
  if (!selectedProvider.value) return
  try {
    const payload = {
      id: m.id,
      name: m.name || m.id,
      provider_id: selectedProvider.value.id,
      provider_name: selectedProvider.value.name,
      base_url: selectedProvider.value.base_url,
      api_format: m.api_format || selectedProvider.value.api_format || 'openai_chat',
      reasoning_type: m.reasoning_type || 'auto',
      reasoning_effort: m.default_effort || 'high',
      capabilities: m.capabilities || ['chat'],
      context_length: m.context_length,
      description: m.description ? m.description.slice(0, 100) : '从远端一键自动收录',
    }
    await api('/api/v1/admin/llm/models', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    if (autoActivate) {
      await api('/api/v1/admin/llm/activate', {
        method: 'POST',
        body: JSON.stringify({ model_id: m.id, reasoning_effort: payload.reasoning_effort }),
      })
    }
    await loadConfig()
    alert(autoActivate ? `已收录并激活主脑为 ${m.id}！` : `已成功添加 ${m.id} 到模型列表！`)
  } catch (err: any) {
    alert(err.message)
  }
}

async function importAllFilteredRemoteModels() {
  if (!selectedProvider.value || !filteredRemoteModels.value.length) return
  const list = filteredRemoteModels.value
  let successCount = 0
  for (const m of list) {
    try {
      const payload = {
        id: m.id,
        name: m.name || m.id,
        provider_id: selectedProvider.value.id,
        provider_name: selectedProvider.value.name,
        base_url: selectedProvider.value.base_url,
        api_format: m.api_format || selectedProvider.value.api_format || 'openai_chat',
        reasoning_type: m.reasoning_type || 'auto',
        reasoning_effort: m.default_effort || 'high',
        capabilities: m.capabilities || ['chat'],
        context_length: m.context_length,
        description: m.description ? m.description.slice(0, 100) : '从远端一键自动收录',
      }
      await api('/api/v1/admin/llm/models', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      successCount++
    } catch (e) {
      console.warn('Import model failed:', m.id, e)
    }
  }
  await loadConfig()
  alert(`成功批量收录 ${successCount} 个模型到 ${selectedProvider.value.name}！`)
}

// ----------------- Model Management -----------------
function openAddModelModal() {
  if (!selectedProvider.value) return
  editingModel.value = null
  modelForm.value = {
    id: '',
    name: '',
    provider_id: selectedProvider.value.id,
    capabilities: ['chat'],
    reasoning_effort: 'high',
    context_length: 128000,
    max_output_tokens: null,
    description: '',
  }
  modelModalVisible.value = true
}

function openEditModelModal(m: any) {
  editingModel.value = m
  modelForm.value = {
    id: m.id,
    name: m.name || m.id,
    provider_id: selectedProvider.value?.id || m.provider_id,
    capabilities: m.capabilities || ['chat'],
    reasoning_effort: m.reasoning_effort || 'high',
    context_length: m.context_length || 128000,
    max_output_tokens: m.max_output_tokens ?? null,
    description: m.description || '',
  }
  modelModalVisible.value = true
}

async function saveModelForm() {
  if (!selectedProvider.value) return
  try {
    const payload = {
      ...modelForm.value,
      max_output_tokens: modelForm.value.max_output_tokens === '' ? null : modelForm.value.max_output_tokens,
      provider_id: selectedProvider.value.id,
      provider_name: selectedProvider.value.name,
      base_url: selectedProvider.value.base_url,
      api_format: selectedProvider.value.api_format || 'openai_chat',
    }
    await api('/api/v1/admin/llm/models', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    modelModalVisible.value = false
    await loadConfig()
  } catch (err: any) {
    alert(err.message)
  }
}

async function activateModel(m: any) {
  try {
    await api('/api/v1/admin/llm/activate', {
      method: 'POST',
      body: JSON.stringify({
        model_id: m.id,
        reasoning_effort: m.reasoning_effort || 'high',
      }),
    })
    await loadConfig()
  } catch (err: any) {
    alert(err.message)
  }
}

async function deleteSingleModel(modelId: string) {
  if (!confirm(`确定删除模型 ${modelId} 吗？`)) return
  try {
    await api(`/api/v1/admin/llm/models/${encodeURIComponent(modelId)}`, {
      method: 'DELETE',
    })
    await loadConfig()
  } catch (err: any) {
    alert(err.message)
  }
}

// ----------------- Test Connection -----------------
async function runTestModel(m: any) {
  testLoading.value = true
  testingModelId.value = m.id
  testResult.value = null
  try {
    const prov = selectedProvider.value || cfg.value?.providers?.find((p: any) => p.id === m.provider_id)
    testResult.value = await api('/api/v1/admin/llm/test', {
      method: 'POST',
      body: JSON.stringify({
        model: m.id,
        base_url: prov?.base_url || m.base_url,
        api_format: prov?.api_format || m.api_format || 'openai_chat',
        reasoning_effort: m.reasoning_effort || 'auto',
      }),
    })
  } catch (e: any) {
    testResult.value = { ok: false, error: e.message }
  } finally {
    testLoading.value = false
    testingModelId.value = null
  }
}

function copyToClipboard(txt: string) {
  navigator.clipboard.writeText(txt)
  copiedText.value = txt
  setTimeout(() => {
    copiedText.value = null
  }, 1800)
}

function toggleCapability(cap: string) {
  const caps = modelForm.value.capabilities
  const idx = caps.indexOf(cap)
  if (idx > -1) {
    caps.splice(idx, 1)
  } else {
    caps.push(cap)
  }
}

onMounted(() => {
  loadConfig()
})
</script>

<template>
  <div class="space-y-4 max-w-4xl 2xl:max-w-6xl mx-auto font-sans text-xs">
    <!-- VIEW 1: 供应商列表页 (对应截图 1) -->
    <template v-if="currentView === 'list'">
      <!-- Top Title & Navigation Bar -->
      <div class="panel-banner-compact">
        <div class="flex items-center space-x-2.5">
          <div class="panel-banner-icon">
            <Cpu class="w-3.5 h-3.5" />
          </div>
          <div>
            <h1 class="text-xs sm:text-[13px] font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
              AI 模型供应商与直连矩阵
            </h1>
            <p class="text-[11px] font-mono mt-0.5" style="color: var(--text-muted);">
              管理大模型渠道矩阵、思考强度与 API 密钥直连
            </p>
          </div>
        </div>

        <!-- Right Quick Actions -->
        <div class="flex items-center space-x-2">
          <button
            @click="openAddProviderModal"
            class="btn-admin-primary"
            title="添加自定义供应商"
          >
            <Plus class="w-3.5 h-3.5" />
            <span>添加供应商</span>
          </button>

          <button
            @click="loadConfig"
            class="btn-admin-secondary px-2"
            title="刷新状态"
          >
            <RefreshCw class="w-3.5 h-3.5" :class="loading ? 'animate-spin' : ''" />
          </button>
        </div>
      </div>

      <!-- Global Reasoning & Thinking Timeout Configuration Card -->
      <div
        class="rounded-2xl border p-4 sm:p-5 shadow-xs transition-colors space-y-3"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="flex flex-wrap items-center justify-between gap-2 pb-3 border-b" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2.5">
            <div class="w-8 h-8 rounded-lg flex items-center justify-center bg-blue-500/10 text-blue-400 border border-blue-500/20">
              <Clock class="w-4 h-4" />
            </div>
            <div>
              <div class="flex items-center space-x-2">
                <h2 class="text-xs sm:text-[13px] font-bold font-mono" style="color: var(--text-main);">
                  全局思考推演与超时上限 (Reasoning & Thinking Timeout)
                </h2>
                <span
                  class="px-2 py-0.5 rounded text-[10px] font-mono font-bold border"
                  style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);"
                >
                  当前上限: {{ cfg?.thinking_timeout || 120 }}s
                </span>
              </div>
              <p class="text-[11px] font-mono mt-0.5" style="color: var(--text-muted);">
                针对长思考链旗舰模型（o1/o3、DeepSeek-R1、Claude 3.7 Thinking、Gemini 3 Pro 等）自定义推演等待上限，杜绝硬编码超时过早截断。
              </p>
            </div>
          </div>

          <button
            @click="saveGlobalSettings"
            :disabled="savingSettings"
            class="flex items-center space-x-1.5 px-3.5 py-1.5 rounded-xl text-xs font-bold text-white transition-all cursor-pointer shadow-xs disabled:opacity-40"
            style="background-color: #2563EB; border-color: #3B82F6;"
          >
            <RefreshCw v-if="savingSettings" class="w-3.5 h-3.5 animate-spin" />
            <Save v-else class="w-3.5 h-3.5" />
            <span>{{ savingSettings ? '保存中...' : '保存推演配置' }}</span>
          </button>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 pt-1">
          <!-- Active Model & Reasoning Effort Status -->
          <div class="p-3 rounded-xl border space-y-1.5 font-mono" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
            <div class="text-[10px]" style="color: var(--text-faint);">当前主脑活跃模型</div>
            <div class="text-xs font-bold truncate text-blue-400">
              {{ cfg?.active_model_id || '未选择' }}
            </div>
            <div class="text-[10px] flex items-center space-x-1" style="color: var(--text-muted);">
              <span>思考强度:</span>
              <span class="font-bold uppercase text-emerald-400">{{ cfg?.active_reasoning_effort || 'HIGH' }}</span>
            </div>
            <div class="text-[10px] flex items-center space-x-1" style="color: var(--text-muted);">
              <span>输出上限:</span>
              <span class="font-bold text-blue-400">{{ activeOutputCap.tokens }} tok</span>
              <span style="color: var(--text-faint);">{{ activeOutputCap.label }}</span>
            </div>
          </div>

          <!-- Thinking Timeout Input Field -->
          <div class="p-3 rounded-xl border space-y-1.5 font-mono sm:col-span-1 lg:col-span-2" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
            <div class="flex items-center justify-between">
              <label class="text-[10px] font-bold" style="color: var(--text-muted);">
                思考时间上限 (秒 / Seconds)
              </label>
              <span class="text-[10px]" style="color: var(--text-faint);">有效范围: 10 ~ 1800 秒</span>
            </div>
            <div class="flex flex-wrap items-center gap-2">
              <input
                v-model.number="thinkingTimeoutInput"
                type="number"
                min="10"
                max="1800"
                step="5"
                placeholder="120"
                class="w-28 rounded-lg px-3 py-1.5 text-xs outline-none border font-bold font-mono"
                style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-main);"
              />
              <span class="text-xs font-bold" style="color: var(--text-muted);">秒 (s)</span>

              <!-- Quick Presets -->
              <div class="flex flex-wrap items-center gap-1.5 pl-2">
                <button
                  type="button"
                  @click="setPresetTimeout(30)"
                  class="px-2 py-1 rounded text-[10px] border cursor-pointer transition-all"
                  :class="thinkingTimeoutInput === 30 ? 'bg-blue-500/20 text-blue-400 border-blue-500 font-bold' : 'text-gray-400 hover:text-white border-transparent'"
                >
                  30s (极速)
                </button>
                <button
                  type="button"
                  @click="setPresetTimeout(60)"
                  class="px-2 py-1 rounded text-[10px] border cursor-pointer transition-all"
                  :class="thinkingTimeoutInput === 60 ? 'bg-blue-500/20 text-blue-400 border-blue-500 font-bold' : 'text-gray-400 hover:text-white border-transparent'"
                >
                  60s (标准)
                </button>
                <button
                  type="button"
                  @click="setPresetTimeout(120)"
                  class="px-2 py-1 rounded text-[10px] border cursor-pointer transition-all"
                  :class="thinkingTimeoutInput === 120 ? 'bg-blue-500/20 text-blue-400 border-blue-500 font-bold' : 'text-gray-400 hover:text-white border-transparent'"
                >
                  120s (推荐)
                </button>
                <button
                  type="button"
                  @click="setPresetTimeout(180)"
                  class="px-2 py-1 rounded text-[10px] border cursor-pointer transition-all"
                  :class="thinkingTimeoutInput === 180 ? 'bg-blue-500/20 text-blue-400 border-blue-500 font-bold' : 'text-gray-400 hover:text-white border-transparent'"
                >
                  180s (深度)
                </button>
                <button
                  type="button"
                  @click="setPresetTimeout(300)"
                  class="px-2 py-1 rounded text-[10px] border cursor-pointer transition-all"
                  :class="thinkingTimeoutInput === 300 ? 'bg-blue-500/20 text-blue-400 border-blue-500 font-bold' : 'text-gray-400 hover:text-white border-transparent'"
                >
                  300s (长思考链)
                </button>
              </div>
            </div>
          </div>

          <!-- Max output tokens -->
          <div class="p-3 rounded-xl border space-y-1.5 font-mono sm:col-span-1 lg:col-span-3" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
            <div class="flex items-center justify-between">
              <label for="global-max-output-tokens" class="text-[10px] font-bold" style="color: var(--text-muted);">
                模型输出上限 (max_output_tokens)
              </label>
              <span class="text-[10px]" style="color: var(--text-faint);">有效范围: 1 ~ 128000</span>
            </div>
            <div class="flex flex-wrap items-center gap-2">
              <input
                v-model.number="maxOutputTokensInput"
                id="global-max-output-tokens"
                name="max_output_tokens"
                autocomplete="off"
                type="number"
                min="1"
                max="128000"
                step="1"
                placeholder="4096"
                class="w-28 rounded-lg px-3 py-1.5 text-xs outline-none focus-visible:ring-2 focus-visible:ring-blue-500 border font-bold font-mono"
                style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-main);"
              />
              <span class="text-xs font-bold" style="color: var(--text-muted);">tokens</span>
              <span class="text-[10px]" style="color: var(--text-faint);">写入 Responses 的 max_output_tokens，以及 Chat/Claude 兼容输出上限。默认 4096。</span>
            </div>
          </div>
        </div>

        <!-- Feedback Alert -->
        <div
          v-if="settingsResult"
          class="p-2.5 rounded-lg border text-xs font-mono flex items-center space-x-2"
          :style="settingsResult.ok
            ? { backgroundColor: 'var(--color-up-bg)', borderColor: 'var(--color-up-border)', color: 'var(--color-up)' }
            : { backgroundColor: 'var(--color-down-bg)', borderColor: 'var(--color-down-border)', color: 'var(--color-down)' }"
        >
          <CheckCircle2 v-if="settingsResult.ok" class="w-3.5 h-3.5 shrink-0" />
          <AlertCircle v-else class="w-3.5 h-3.5 shrink-0" />
          <span>{{ settingsResult.message || settingsResult.error }}</span>
        </div>
      </div>

      <!-- Search Box (对应截图 1 顶部的搜索栏) -->
      <div class="relative">
        <input
          v-model="searchQuery"
          placeholder="搜索供应商或分组"
          class="w-full rounded-2xl px-4 py-3 pl-11 text-xs outline-none border transition-colors shadow-xs"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
        />
        <Search class="w-4 h-4 absolute left-4 top-3.5 text-gray-400 pointer-events-none" />
      </div>

      <!-- Providers List Container -->
      <div
        class="rounded-2xl border overflow-hidden shadow-xs divide-y transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div
          v-for="prov in filteredProviders"
          :key="prov.id"
          @click="selectProvider(prov)"
          class="p-4 flex items-center justify-between hover:bg-[var(--bg-card-subtle)] transition-colors cursor-pointer group"
          style="border-color: var(--border-subtle);"
        >
          <!-- Left: Provider Logo / Icon & Name -->
          <div class="flex items-center space-x-3.5">
            <!-- Icon Avatar -->
            <div
              class="w-10 h-10 rounded-xl flex items-center justify-center border font-bold text-sm shrink-0 transition-transform group-hover:scale-105"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
            >
              <span v-if="prov.id === 'openai'" class="text-emerald-500">❖</span>
              <span v-else-if="prov.id === 'siliconflow'" class="text-purple-500">⚡</span>
              <span v-else-if="prov.id === 'gemini'" class="text-blue-500">✦</span>
              <span v-else-if="prov.id === 'openrouter'" class="text-indigo-500">◈</span>
              <span v-else-if="prov.id === 'deepseek'" class="text-sky-500">🐳</span>
              <span v-else-if="prov.id === 'claude'" class="text-amber-500">✳</span>
              <span v-else-if="prov.id === 'grok'" class="text-neutral-300">Ø</span>
              <span v-else-if="prov.id === 'volcengine'" class="text-cyan-500">📶</span>
              <span v-else-if="prov.id === 'dashscope'" class="text-orange-500">[-]</span>
              <span v-else-if="prov.id === 'zhipu'" class="text-violet-500">◆</span>
              <span v-else class="text-blue-400">❖</span>
            </div>

            <!-- Provider Name & Subtitle -->
            <div>
              <div class="flex items-center space-x-2">
                <span class="font-bold text-sm" style="color: var(--text-main);">{{ prov.name }}</span>
                <span
                  v-if="prov.models?.some((m: any) => m.id === cfg?.active_model_id)"
                  class="px-1.5 py-0.2 rounded text-[9px] font-bold border"
                  style="background-color: var(--color-up-bg); border-color: var(--color-up-border); color: var(--color-up);"
                >
                  主脑活跃
                </span>
              </div>
              <div class="text-[11px] mt-0.5" style="color: var(--text-faint);">
                {{ prov.models_count || 0 }} 个模型 · {{ prov.group || '其他' }}
              </div>
            </div>
          </div>

          <!-- Right: Enable / Disable Badge & Chevron Arrow (对齐截图 1) -->
          <div class="flex items-center space-x-2.5">
            <!-- Capsule Status Button -->
            <button
              @click="toggleProviderQuick(prov, $event)"
              class="px-3 py-1 rounded-full text-xs font-semibold border transition-all cursor-pointer shadow-2xs"
              :style="prov.enabled ? {
                backgroundColor: 'rgba(16, 185, 129, 0.12)',
                borderColor: 'rgba(16, 185, 129, 0.25)',
                color: '#10B981',
              } : {
                backgroundColor: 'rgba(239, 68, 68, 0.08)',
                borderColor: 'rgba(239, 68, 68, 0.2)',
                color: '#F87171',
              }"
            >
              {{ prov.enabled ? '启用' : '禁用' }}
            </button>

            <!-- Arrow Right -->
            <span class="text-gray-400 font-bold text-base select-none">›</span>
          </div>
        </div>
      </div>
    </template>

    <!-- VIEW 2: 供应商详情管理 (对应截图 2 配置 & 截图 3 模型) -->
    <template v-else-if="currentView === 'detail' && selectedProvider">
      <!-- Detail Top Navigation Bar -->
      <div
        class="rounded-2xl border p-4 flex items-center justify-between shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <button
          @click="goBackToList"
          class="flex items-center space-x-1.5 px-3 py-1.5 rounded-xl border text-xs font-bold cursor-pointer transition-all hover:bg-[var(--bg-card-subtle)]"
          style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
        >
          <ArrowLeft class="w-4 h-4" />
          <span>返回</span>
        </button>

        <div class="flex items-center space-x-2">
          <div
            class="w-7 h-7 rounded-lg flex items-center justify-center font-bold text-xs"
            style="background-color: var(--bg-card-subtle); color: var(--color-brand);"
          >
            ❖
          </div>
          <span class="font-bold text-sm sm:text-base" style="color: var(--text-main);">
            {{ selectedProvider.name }}
          </span>
        </div>

        <div class="w-16"></div>
      </div>

      <!-- SUB-VIEW A: 「配置」Tab (对齐截图 2) -->
      <div
        v-if="detailTab === 'config'"
        class="space-y-4 rounded-2xl border p-5 shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <!-- Section 1: 管理设置项列表 -->
        <div class="space-y-1">
          <div class="text-[11px] font-bold uppercase tracking-wider mb-2" style="color: var(--text-muted);">
            管理
          </div>

          <div
            class="rounded-xl border divide-y overflow-hidden text-xs"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
          >
            <!-- 供应商类型 -->
            <div class="p-3.5 flex items-center justify-between">
              <span class="font-medium" style="color: var(--text-main);">供应商类型</span>
              <div class="flex items-center space-x-1" style="color: var(--text-muted);">
                <span>{{ providerForm.type }}</span>
                <span class="text-gray-400">›</span>
              </div>
            </div>

            <!-- API 交互协议类型 (下拉选择) -->
            <div class="p-3.5 flex items-center justify-between">
              <div>
                <span class="font-medium" style="color: var(--text-main);">API 交互协议</span>
                <div class="text-[10px]" style="color: var(--text-faint);">选择该端点底层支持的通信协议标准</div>
              </div>
              <select
                v-model="providerForm.api_format"
                @change="onApiFormatChange"
                class="rounded-lg px-2.5 py-1.5 text-xs font-mono outline-none border cursor-pointer max-w-[200px]"
                style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
              >
                <option value="openai_chat">OpenAI Chat (/chat/completions)</option>
                <option value="claude_messages">Claude Messages (/messages)</option>
                <option value="openai_responses">OpenAI Responses (/responses)</option>
              </select>
            </div>

            <!-- 分组 -->
            <div class="p-3.5 flex items-center justify-between">
              <span class="font-medium" style="color: var(--text-main);">分组</span>
              <div class="flex items-center space-x-1" style="color: var(--text-muted);">
                <span>{{ providerForm.group }}</span>
                <span class="text-gray-400">›</span>
              </div>
            </div>

            <!-- 是否启用开关 -->
            <div class="p-3.5 flex items-center justify-between">
              <span class="font-medium" style="color: var(--text-main);">是否启用</span>
              <label class="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  v-model="providerForm.enabled"
                  class="sr-only peer"
                />
                <div class="w-11 h-6 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-indigo-600"></div>
              </label>
            </div>

            <!-- 多Key模式开关 -->
            <div class="p-3.5 flex items-center justify-between">
              <span class="font-medium" style="color: var(--text-main);">多Key模式</span>
              <label class="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  v-model="providerForm.multi_key_enabled"
                  class="sr-only peer"
                />
                <div class="w-11 h-6 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-indigo-600"></div>
              </label>
            </div>
          </div>
        </div>


        <!-- Section 2: 凭据与输入表单区 (对应截图 2 底部字段) -->
        <div class="space-y-3 pt-2">
          <!-- 供应商唯一标识 ID (仅新建自定义供应商时展示) -->
          <div v-if="selectedProvider.is_new">
            <label class="block text-xs font-bold mb-1.5" style="color: var(--text-muted);">供应商唯一标识 (ID)</label>
            <input
              v-model="providerForm.id"
              placeholder="例如: openrouter 或 my-proxy"
              class="w-full rounded-xl px-4 py-2.5 text-xs outline-none border transition-colors font-mono"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
            />
          </div>

          <!-- 名称 -->
          <div>
            <label class="block text-xs font-bold mb-1.5" style="color: var(--text-muted);">名称</label>
            <input
              v-model="providerForm.name"
              placeholder="OpenAI"
              class="w-full rounded-xl px-4 py-2.5 text-xs outline-none border transition-colors"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
            />
          </div>

          <!-- API Key -->
          <div>
            <div class="flex items-center justify-between mb-1.5">
              <label class="text-xs font-bold" style="color: var(--text-muted);">API Key</label>
              <span v-if="selectedProvider.has_key" class="text-[10px] text-emerald-500 font-bold">
                ✓ 密钥已就绪
              </span>
            </div>
            <div class="relative">
              <input
                v-model="providerForm.api_key"
                :type="showApiKey ? 'text' : 'password'"
                placeholder="••••••••••••••••••••••••"
                class="w-full rounded-xl px-4 py-2.5 pr-10 text-xs outline-none border transition-colors font-mono"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
              />
              <button
                type="button"
                @click="showApiKey = !showApiKey"
                class="absolute right-3 top-2.5 text-gray-400 hover:text-white cursor-pointer"
              >
                <EyeOff v-if="showApiKey" class="w-4 h-4" />
                <Eye v-else class="w-4 h-4" />
              </button>
            </div>
          </div>

          <!-- API Base URL -->
          <div>
            <label class="block text-xs font-bold mb-1.5" style="color: var(--text-muted);">API Base URL</label>
            <input
              v-model="providerForm.base_url"
              placeholder="https://api.openai.com/v1 或自建中继地址"
              class="w-full rounded-xl px-4 py-2.5 text-xs outline-none border transition-colors font-mono"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
            />
          </div>

          <!-- API 路径 -->
          <div>
            <label class="block text-xs font-bold mb-1.5" style="color: var(--text-muted);">API 路径</label>
            <input
              v-model="providerForm.api_path"
              placeholder="/chat/completions"
              class="w-full rounded-xl px-4 py-2.5 text-xs outline-none border transition-colors font-mono"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
            />
          </div>
        </div>

        <!-- Save Button -->
        <div class="pt-3 pb-16 flex items-center justify-between">
          <button
            v-if="!selectedProvider.is_new"
            @click="removeProvider"
            class="flex items-center space-x-1.5 px-4 py-2 rounded-xl text-xs font-bold transition-all cursor-pointer border"
            style="color: #F87171; border-color: rgba(239, 68, 68, 0.3); background-color: rgba(239, 68, 68, 0.06);"
          >
            <Trash2 class="w-3.5 h-3.5" />
            <span>删除供应商</span>
          </button>
          <span v-else></span>
          <button
            @click="saveProviderConfig"
            class="px-6 py-2 rounded-xl text-xs font-bold transition-all cursor-pointer shadow-xs btn-primary-text"
            style="background-color: #2563EB; color: #FFFFFF;"
          >
            保存供应商配置
          </button>
        </div>
      </div>

      <!-- SUB-VIEW B: 「模型」Tab (完美还原原生截图排版) -->
      <div v-else-if="detailTab === 'models'" class="space-y-4">
        <!-- Models List Container -->
        <div
          class="rounded-3xl border divide-y overflow-hidden shadow-xs transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <div
            v-for="m in selectedProvider.models"
            :key="m.id"
            class="p-4 sm:p-5 flex items-center justify-between hover:bg-[var(--bg-card-subtle)] transition-colors group"
            style="border-color: var(--border-subtle);"
          >
            <!-- Left: Sparkle Avatar + Model Title + Badges -->
            <div class="flex items-start sm:items-center space-x-3.5 min-w-0 pr-3">
              <!-- Avatar: 经典彩色四角星 Sparkle 图标 -->
              <div
                class="w-10 h-10 rounded-full flex items-center justify-center shrink-0 shadow-2xs border"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
              >
                <Sparkles class="w-5 h-5 text-indigo-400" />
              </div>

              <!-- Content Area -->
              <div class="min-w-0">
                <!-- Model ID & Status Badge -->
                <div class="flex flex-wrap items-center gap-2">
                  <span class="font-bold text-sm tracking-tight truncate max-w-[200px] sm:max-w-md font-mono" style="color: var(--text-main);">
                    {{ m.id }}
                  </span>
                  <span
                    v-if="m.id === cfg?.active_model_id"
                    class="px-2 py-0.5 rounded-full text-[10px] font-bold border"
                    style="background-color: rgba(16, 185, 129, 0.12); border-color: rgba(16, 185, 129, 0.25); color: #10B981;"
                  >
                    主脑生效
                  </span>
                </div>

                <!-- Capability Badges (对齐截图 3: 聊天、T图 > T、工具锤子、CoT思考) -->
                <div class="flex flex-wrap items-center gap-1.5 mt-2">
                  <span
                    v-if="m.capabilities?.includes('chat')"
                    class="px-2.5 py-0.5 rounded-full text-[11px] font-medium border"
                    style="background-color: rgba(99, 102, 241, 0.08); border-color: rgba(99, 102, 241, 0.2); color: #818CF8;"
                  >
                    聊天
                  </span>
                  <span
                    v-if="m.capabilities?.includes('vision')"
                    class="px-2.5 py-0.5 rounded-full text-[11px] font-medium border"
                    style="background-color: rgba(236, 72, 153, 0.08); border-color: rgba(236, 72, 153, 0.2); color: #F472B6;"
                  >
                    T图 &gt; T
                  </span>
                  <span
                    v-if="m.capabilities?.includes('tools')"
                    class="p-1 rounded-full border flex items-center justify-center"
                    style="background-color: rgba(59, 130, 246, 0.08); border-color: rgba(59, 130, 246, 0.2); color: #60A5FA;"
                    title="支持工具调用"
                  >
                    <Wrench class="w-3 h-3" />
                  </span>
                  <span
                    v-if="m.capabilities?.includes('reasoning') || m.reasoning_type !== 'none'"
                    class="px-2 py-0.5 rounded-full text-[10px] border flex items-center gap-1 font-bold text-amber-400"
                    style="background-color: rgba(245, 158, 11, 0.08); border-color: rgba(245, 158, 11, 0.2);"
                    title="支持长链推演"
                  >
                    🧠 思考
                  </span>
                  <span
                    v-if="m.context_length"
                    class="text-[11px] font-mono text-gray-400 ml-1"
                  >
                    {{ (m.context_length / 1000).toFixed(0) }}k
                  </span>
                </div>
              </div>
            </div>

            <!-- Right: Minimalist Action Controls -->
            <div class="flex items-center space-x-1.5 sm:space-x-2 shrink-0">
              <button
                v-if="m.id !== cfg?.active_model_id"
                @click="activateModel(m)"
                class="px-3 py-1 rounded-xl text-xs font-bold border transition-all cursor-pointer shadow-xs btn-primary-text"
                style="background-color: #2563EB; color: #FFFFFF;"
                title="一键设为主脑"
              >
                启用
              </button>

              <button
                @click="runTestModel(m)"
                :disabled="testLoading && testingModelId === m.id"
                class="p-2 rounded-xl border text-xs cursor-pointer hover:bg-[var(--bg-card)] transition-colors"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
                title="测试连通性"
              >
                <RefreshCw class="w-3.5 h-3.5" :class="testLoading && testingModelId === m.id ? 'animate-spin' : ''" />
              </button>

              <button
                @click="openEditModelModal(m)"
                class="p-2 rounded-xl border text-xs cursor-pointer hover:bg-[var(--bg-card)] transition-colors"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
                title="编辑参数"
              >
                <Settings class="w-3.5 h-3.5" />
              </button>

              <button
                @click="deleteSingleModel(m.id)"
                class="p-2 rounded-xl border text-xs cursor-pointer hover:bg-red-500/10 transition-colors text-red-400"
                style="border-color: var(--border-subtle);"
                title="删除该模型"
              >
                <Trash2 class="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          <div v-if="!selectedProvider.models?.length" class="py-16 text-center text-xs" style="color: var(--text-muted);">
            该供应商名下暂未配置模型，点击下方「获取」可一键从远端自动拉取。
          </div>
        </div>

        <!-- Diagnostic Response Box -->
        <div
          v-if="testResult"
          class="rounded-2xl border p-4 transition-all shadow-xs text-xs"
          :style="{
            backgroundColor: testResult.ok ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
            borderColor: testResult.ok ? 'var(--color-up-border)' : 'var(--color-down-border)',
            color: testResult.ok ? 'var(--color-up)' : 'var(--color-down)'
          }"
        >
          <div class="flex items-center justify-between mb-1.5">
            <div class="flex items-center space-x-2 font-bold text-sm">
              <CheckCircle2 v-if="testResult.ok" class="w-4 h-4" />
              <AlertCircle v-else class="w-4 h-4" />
              <span>{{ testResult.ok ? `模型测试通过 (耗时: ${testResult.latency_ms}ms)` : '连通性测试未通过' }}</span>
            </div>
            <span class="text-[10px] opacity-75 font-mono">状态: {{ testResult.status_code || 0 }}</span>
          </div>

          <div v-if="testResult.ok" class="space-y-1 text-xs" style="color: var(--text-main);">
            <div>输出预览: <span class="font-bold">{{ testResult.response_preview }}</span></div>
            <div v-if="testResult.reasoning_detected" class="text-emerald-500 font-bold">
              🧠 成功识别原生长思维链输出
            </div>
          </div>
          <div v-else class="text-xs break-all" style="color: var(--color-down);">
            {{ testResult.error || '连通性测试超时或未收到有效响应' }}
          </div>
        </div>

        <!-- Floating Bottom Operation Bar (完美对齐截图 3 椭圆气泡底栏: [获取] [+ 添加新模型] [清空]) -->
        <div class="flex items-center justify-center pt-2 pb-20">
          <div
            class="flex items-center space-x-3 p-1.5 rounded-full border shadow-2xl backdrop-blur-md"
            style="background-color: var(--bg-card); border-color: var(--border-subtle);"
          >
            <!-- 获取 (带方块立方体图标的大圆角按钮) -->
            <button
              @click="openFetchDialog"
              class="flex items-center space-x-2 px-5 py-2.5 rounded-full font-bold text-xs cursor-pointer border transition-all hover:opacity-90 shadow-2xs"
              style="background-color: rgba(99, 102, 241, 0.1); border-color: rgba(99, 102, 241, 0.25); color: #818CF8;"
            >
              <DownloadCloud class="w-4 h-4" />
              <span>获取</span>
            </button>

            <!-- + 添加新模型 -->
            <button
              @click="openAddModelModal"
              class="flex items-center space-x-2 px-5 py-2.5 rounded-full font-bold text-xs cursor-pointer border transition-all hover:opacity-90 shadow-2xs"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
            >
              <Plus class="w-4 h-4" />
              <span>添加新模型</span>
            </button>

            <!-- 清空删除图标 (带红晕气泡) -->
            <button
              @click="clearCurrentProviderModels"
              class="p-2.5 rounded-full border cursor-pointer hover:bg-red-500/10 transition-colors text-red-400"
              style="border-color: rgba(239, 68, 68, 0.2); background-color: rgba(239, 68, 68, 0.08);"
              title="清空该供应商所有模型"
            >
              <Trash2 class="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      <!-- Detail Bottom Tab Bar (对齐截图 2 & 截图 3 的底部「配置」与「模型」双Tab) -->
      <div
        class="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 flex items-center rounded-2xl border p-1 shadow-2xl backdrop-blur-md"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <button
          @click="detailTab = 'config'"
          class="flex items-center space-x-2 px-6 py-2.5 rounded-xl font-bold text-xs cursor-pointer transition-all border"
          :style="detailTab === 'config' ? {
            backgroundColor: '#2563EB',
            borderColor: '#1D4ED8',
            color: '#FFFFFF',
            boxShadow: '0 2px 10px rgba(37,99,235,0.35)',
          } : {
            backgroundColor: 'transparent',
            borderColor: 'transparent',
            color: 'var(--text-muted)',
          }"
        >
          <Settings class="w-4 h-4" />
          <span>配置</span>
        </button>

        <button
          @click="detailTab = 'models'"
          class="flex items-center space-x-2 px-6 py-2.5 rounded-xl font-bold text-xs cursor-pointer transition-all border"
          :style="detailTab === 'models' ? {
            backgroundColor: '#2563EB',
            borderColor: '#1D4ED8',
            color: '#FFFFFF',
            boxShadow: '0 2px 10px rgba(37,99,235,0.35)',
          } : {
            backgroundColor: 'transparent',
            borderColor: 'transparent',
            color: 'var(--text-muted)',
          }"
        >
          <Layers class="w-4 h-4" />
          <span>模型 ({{ selectedProvider.models?.length || 0 }})</span>
        </button>
      </div>
    </template>

    <!-- MODAL A: 远端一键获取模型抽屉/弹窗 -->
    <div
      v-if="fetchModalVisible"
      class="fixed inset-0 z-50 bg-black/60 backdrop-blur-md flex items-center justify-center p-4"
      @click.self="fetchModalVisible = false"
    >
      <div
        class="border rounded-2xl w-full max-w-2xl shadow-2xl p-5 space-y-4 text-xs max-h-[90dvh] flex flex-col"
        style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
      >
        <div class="flex items-center justify-between pb-3 border-b shrink-0" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <DownloadCloud class="w-4 h-4 text-blue-500" />
            <h3 class="text-sm font-bold uppercase" style="color: var(--text-main);">
              获取 {{ selectedProvider?.name }} 远端可用模型
            </h3>
          </div>
          <span class="text-[10px]" style="color: var(--text-faint);">探测 /models 兼容端点</span>
        </div>

        <!-- Probe Configuration (仅当需要微调或端点无预存 Key 时作为高级选项展开) -->
        <div class="p-3 rounded-xl border space-y-2 shrink-0 text-xs" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
          <div class="flex items-center justify-between">
            <div class="flex items-center space-x-2">
              <span class="font-bold text-[11px]" style="color: var(--text-main);">探测端点:</span>
              <span class="font-mono text-[11px] text-blue-400">{{ customFetchUrl || selectedProvider?.base_url }}</span>
            </div>
            <div class="flex items-center space-x-1.5">
              <span v-if="selectedProvider?.has_key" class="text-[10px] text-emerald-400 font-bold">
                ✓ 使用已存凭证
              </span>
              <button
                @click="executeRemoteFetch"
                :disabled="fetchingRemote"
                class="flex items-center space-x-1.5 px-3 py-1 rounded-lg text-xs font-bold transition-all cursor-pointer shadow-xs btn-primary-text"
                style="background-color: #2563EB; color: #FFFFFF;"
              >
                <RefreshCw class="w-3.5 h-3.5" :class="fetchingRemote ? 'animate-spin' : ''" />
                <span>{{ fetchingRemote ? '正在探测...' : '重新探测' }}</span>
              </button>
            </div>
          </div>
        </div>


        <!-- Status Banner -->
        <div v-if="remoteFetchResult" class="shrink-0">
          <div
            v-if="remoteFetchResult.ok"
            class="p-2.5 rounded-xl border text-xs flex items-center justify-between"
            style="background-color: var(--color-up-bg); border-color: var(--color-up-border); color: var(--color-up);"
          >
            <span class="font-bold">✓ 成功探测到 {{ remoteFetchResult.total }} 个可用模型</span>
            <span class="text-[10px] opacity-80 font-mono">{{ remoteFetchResult.endpoint_used }}</span>
          </div>
          <div
            v-else
            class="p-2.5 rounded-xl border text-xs"
            style="background-color: var(--color-down-bg); border-color: var(--color-down-border); color: var(--color-down);"
          >
            {{ remoteFetchResult.error }}
          </div>
        </div>

        <!-- Remote Search Box -->
        <div v-if="remoteFetchResult?.ok" class="relative shrink-0">
          <input
            v-model="remoteSearch"
            placeholder="过滤搜索模型 ID..."
            class="w-full rounded-xl px-3.5 py-1.5 pl-9 text-xs outline-none border font-mono"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
          />
          <Search class="w-3.5 h-3.5 absolute left-3 top-2.5 text-gray-400 pointer-events-none" />
        </div>

        <!-- Remote Scroll List -->
        <div v-if="remoteFetchResult?.ok" class="flex-1 overflow-y-auto space-y-2 pr-1 min-h-[200px]">
          <div
            v-for="rm in filteredRemoteModels"
            :key="rm.id"
            class="p-3 rounded-xl border flex items-center justify-between hover:border-[var(--border-strong)] transition-colors"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
          >
            <div>
              <div class="font-bold text-xs" style="color: var(--text-main);">{{ rm.name }}</div>
              <div class="text-[11px] font-mono text-blue-400">{{ rm.id }}</div>
            </div>

            <div class="flex items-center space-x-2 shrink-0">
              <button
                @click="importRemoteModel(rm, false)"
                class="px-2.5 py-1 rounded-lg text-xs font-medium border cursor-pointer hover:bg-[var(--bg-card)] transition-colors"
                style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
              >
                + 添加
              </button>
              <button
                @click="importRemoteModel(rm, true)"
                class="px-3 py-1 rounded-lg text-xs font-bold transition-all cursor-pointer shadow-xs btn-primary-text"
                style="background-color: #2563EB; color: #FFFFFF;"
              >
                添加并启用
              </button>
            </div>
          </div>
        </div>

        <div class="flex items-center justify-between pt-3 border-t shrink-0" style="border-color: var(--border-subtle);">
          <div class="text-[11px]" style="color: var(--text-muted);">
            <span v-if="filteredRemoteModels.length">当前显示 {{ filteredRemoteModels.length }} 个模型</span>
          </div>
          <div class="flex items-center space-x-2">
            <button
              v-if="filteredRemoteModels.length"
              @click="importAllFilteredRemoteModels"
              class="px-3 py-1.5 rounded-xl border text-xs font-bold cursor-pointer transition-all hover:opacity-90"
              style="background-color: rgba(99, 102, 241, 0.1); border-color: rgba(99, 102, 241, 0.25); color: #818CF8;"
            >
              一键添加当前全部 ({{ filteredRemoteModels.length }})
            </button>
            <button
              @click="fetchModalVisible = false"
              class="px-4 py-1.5 rounded-xl border text-xs cursor-pointer"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
            >
              完成
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- MODAL B: 手动添加 / 编辑单模型弹窗 -->
    <div
      v-if="modelModalVisible"
      class="fixed inset-0 z-50 bg-black/60 backdrop-blur-md flex items-center justify-center p-4"
      @click.self="modelModalVisible = false"
    >
      <div
        class="border rounded-2xl w-full max-w-lg shadow-2xl p-5 sm:p-6 space-y-4 text-xs"
        style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
      >
        <div class="flex items-center justify-between pb-3 border-b" style="border-color: var(--border-subtle);">
          <h3 class="text-sm font-bold uppercase" style="color: var(--text-main);">
            {{ editingModel ? '编辑模型' : '添加新模型' }}
          </h3>
          <span class="text-[10px]" style="color: var(--text-faint);">所属: {{ selectedProvider?.name }}</span>
        </div>

        <div class="space-y-3">
          <div>
            <label class="block text-[11px] font-bold mb-1" style="color: var(--text-muted);">模型 ID</label>
            <input
              v-model="modelForm.id"
              :readonly="!!editingModel"
              placeholder="例如 gemini-3.7-flash-high / deepseek-chat"
              class="w-full rounded-xl px-3.5 py-2 text-xs outline-none border font-mono"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
            />
          </div>

          <div>
            <label class="block text-[11px] font-bold mb-1" style="color: var(--text-muted);">展示名称</label>
            <input
              v-model="modelForm.name"
              placeholder="Gemini 3.8 Flash (高推演)"
              class="w-full rounded-xl px-3.5 py-2 text-xs outline-none border"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
            />
          </div>

          <!-- 模型能力标签选择 -->
          <div>
            <label class="block text-[11px] font-bold mb-1" style="color: var(--text-muted);">能力标签徽标</label>
            <div class="flex flex-wrap gap-2 pt-1">
              <button
                type="button"
                @click="toggleCapability('chat')"
                class="px-2.5 py-1 rounded-lg border text-xs font-medium cursor-pointer transition-all"
                :style="modelForm.capabilities.includes('chat') ? { backgroundColor: 'rgba(99, 102, 241, 0.2)', borderColor: '#818CF8', color: '#818CF8' } : { backgroundColor: 'var(--bg-card-subtle)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
              >
                聊天 (chat)
              </button>
              <button
                type="button"
                @click="toggleCapability('vision')"
                class="px-2.5 py-1 rounded-lg border text-xs font-medium cursor-pointer transition-all"
                :style="modelForm.capabilities.includes('vision') ? { backgroundColor: 'rgba(236, 72, 153, 0.2)', borderColor: '#F472B6', color: '#F472B6' } : { backgroundColor: 'var(--bg-card-subtle)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
              >
                T图 &gt; T (vision)
              </button>
              <button
                type="button"
                @click="toggleCapability('tools')"
                class="px-2.5 py-1 rounded-lg border text-xs font-medium cursor-pointer transition-all"
                :style="modelForm.capabilities.includes('tools') ? { backgroundColor: 'rgba(59, 130, 246, 0.2)', borderColor: '#60A5FA', color: '#60A5FA' } : { backgroundColor: 'var(--bg-card-subtle)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
              >
                工具调用 (tools)
              </button>
              <button
                type="button"
                @click="toggleCapability('reasoning')"
                class="px-2.5 py-1 rounded-lg border text-xs font-medium cursor-pointer transition-all"
                :style="modelForm.capabilities.includes('reasoning') ? { backgroundColor: 'rgba(245, 158, 11, 0.2)', borderColor: '#F59E0B', color: '#F59E0B' } : { backgroundColor: 'var(--bg-card-subtle)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
              >
                🧠 链式思考 (CoT)
              </button>
            </div>
          </div>

          <!-- 思考强度配置 (动态精简与自适应展示) -->
          <div>
            <label class="block text-[11px] font-bold mb-1" style="color: var(--text-muted);">思考推演强度</label>
            <select
              v-model="modelForm.reasoning_effort"
              class="w-full rounded-xl px-3.5 py-2 text-xs outline-none border cursor-pointer font-mono"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
            >
              <option
                v-for="opt in availableEffortOptions"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>
          </div>

          <div>
            <label class="block text-[11px] font-bold mb-1" style="color: var(--text-muted);">上下文上限长度 (Tokens)</label>
            <input
              v-model.number="modelForm.context_length"
              type="number"
              placeholder="1048576"
              class="w-full rounded-xl px-3.5 py-2 text-xs outline-none border font-mono"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
            />
          </div>

          <div>
            <label for="model-max-output-tokens" class="block text-[11px] font-bold mb-1" style="color: var(--text-muted);">输出上限 (max_output_tokens，留空继承全局)</label>
            <input
              v-model.number="modelForm.max_output_tokens"
              id="model-max-output-tokens"
              name="model_max_output_tokens"
              autocomplete="off"
              type="number"
              min="1"
              max="128000"
              placeholder="继承全局设置"
              class="w-full rounded-xl px-3.5 py-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-blue-500 border font-mono"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
            />
          </div>

        </div>

        <div class="flex justify-end space-x-2 pt-3 border-t" style="border-color: var(--border-subtle);">
          <button
            @click="modelModalVisible = false"
            class="px-4 py-1.5 rounded-xl border text-xs cursor-pointer"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
          >
            取消
          </button>
          <button
            @click="saveModelForm"
            class="px-5 py-1.5 rounded-xl text-xs font-bold transition-all cursor-pointer shadow-xs btn-primary-text"
            style="background-color: #2563EB; color: #FFFFFF;"
          >
            保存模型
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

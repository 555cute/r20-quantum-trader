<script setup lang="ts">
import { useToast } from '../../composables/useToast'
const toast = useToast()
import { ref, onMounted } from 'vue'
import PageHeader from '../../components/admin/PageHeader.vue'
import { useI18n } from '../../composables/useI18n'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import {Users,
  Shield,
  Zap,
  Cpu,
  Save,
  RotateCcw,
  Play,
  Plus,
  Trash2,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ToggleLeft,
  ToggleRight,
  Sliders,
  Download,
  Upload} from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()
const { t } = useI18n()

const loading = ref(true)
const saving = ref(false)
const testing = ref(false)

const councilConfig = ref<any>({
  enabled: false,
  consensus_mode: 'standard',
  timeout_seconds: 60.0,
  roles: {},
})

const availableSuites = ref<any[]>([])
const availableModels = ref<any[]>([])
const expandedRole = ref<string>('trader_trend')
const testResult = ref<any>(null)
const expandedReasoning = ref<Record<string, boolean>>({})

const consensusModes = [
  {
    id: 'standard',
    name: '标准提案模式',
    tag: '高效终审',
    desc: '各交易员提交首轮独立方案与审查汇报，汇编完整卷宗直接由 CIO 终审查决。',
  },
  {
    id: 'cross_examination',
    name: '双轮质询互评',
    tag: '深度攻防',
    desc: '第一轮独立方案 -> 第二轮同行交叉漏洞质询辩论 -> 第三轮 CIO 统筹审阅攻防并拍板。',
  },
]

const roleIcons: Record<string, any> = {
  trader_trend: Shield,
  trader_momentum: Zap,
  trader_quant: Cpu,
  cio: Users,
  custom: Sliders,
}

const roleColors: Record<string, string> = {
  trader_trend: 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10',
  trader_momentum: 'text-amber-400 border-amber-500/30 bg-amber-500/10',
  trader_quant: 'text-cyan-400 border-cyan-500/30 bg-cyan-500/10',
  cio: 'text-purple-400 border-purple-500/30 bg-purple-500/10',
  custom: 'text-blue-400 border-blue-500/30 bg-blue-500/10',
}

async function loadData() {
  loading.value = true
  try {
    const [cRes, mRes] = await Promise.all([
      api('/api/v1/admin/council/config'),
      api('/api/v1/admin/llm/models'),
    ])
    councilConfig.value = cRes
    availableSuites.value = cRes.available_suites || []
    availableModels.value = mRes.models || []
    const roleKeys = Object.keys(cRes.roles || {})
    if (roleKeys.length > 0 && !roleKeys.includes(expandedRole.value)) {
      expandedRole.value = roleKeys[0]
    }
  } catch (e: any) {
    toast.err(`加载配置失败: ${e.message}`)
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  if (!auth.isSuperadmin) {
    toast.err('仅超级管理员可修改投委会配置')
    return
  }
  saving.value = true
  try {
    const res = await api('/api/v1/admin/council/config', {
      method: 'PUT',
      body: JSON.stringify({
        enabled: councilConfig.value.enabled,
        consensus_mode: councilConfig.value.consensus_mode || 'standard',
        timeout_seconds: Number(councilConfig.value.timeout_seconds) || 60.0,
        roles: councilConfig.value.roles,
      }),
    })
    councilConfig.value = res.config
    toast.ok(councilConfig.value.enabled
        ? `对冲基金投委会配置已保存并生效（${consensusModes.find((m) => m.id === councilConfig.value.consensus_mode)?.name || '标准提案模式'}）`
        : '投委会配置已保存（当前为单模型直连决策）')
  } catch (e: any) {
    toast.err(`保存失败: ${e.message}`)
  } finally {
    saving.value = false
  }
}

// ===== 投委会配置导入 / 导出（对齐提示词工坊策略包体验） =====
const importVisible = ref(false)
const importRawJson = ref('')
const importFileError = ref('')
const importing = ref(false)

async function exportConfig() {
  try {
    const res = await api('/api/v1/admin/council/export')
    const blob = new Blob([JSON.stringify(res, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `r20-council-config-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(url)
    toast.ok('投委会配置已导出为 JSON 包（含全部席位提示词与议事规则）')
  } catch (e: any) {
    toast.err(`导出失败：${e.message}`)
  }
}

function pickImportFile(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    importRawJson.value = String(reader.result || '')
    importFileError.value = ''
  }
  reader.onerror = () => { importFileError.value = '文件读取失败，请重试或直接粘贴 JSON 内容' }
  reader.readAsText(file)
}

async function doImportConfig() {
  importFileError.value = ''
  let payload: any
  try {
    payload = JSON.parse(importRawJson.value)
  } catch {
    importFileError.value = 'JSON 格式不合法，请检查导出包内容'
    return
  }
  importing.value = true
  try {
    const res = await api('/api/v1/admin/council/import', {
      method: 'POST',
      body: JSON.stringify({ payload }),
    })
    await loadData()
    importVisible.value = false
    importRawJson.value = ''
    toast.ok(`投委会配置导入成功：席位 ${(res.roles || []).join(' / ')}${res.backup_file ? `；原配置已自动备份为 ${res.backup_file}` : ''}`)
  } catch (e: any) {
    importFileError.value = `导入失败：${e.message}`
  } finally {
    importing.value = false
  }
}

async function applySuite(suiteId: string) {
  if (!auth.isSuperadmin) return
  if (!confirm('确定载入对冲基金标准投委会套件吗？将恢复标准交易员阵容。')) return
  try {
    const res = await api('/api/v1/admin/council/apply-suite', {
      method: 'POST',
      body: JSON.stringify({ suite_id: suiteId }),
    })
    councilConfig.value = res.config
    toast.ok('已载入标准投委会阵容！')
  } catch (e: any) {
    toast.err(`载入失败: ${e.message}`)
  }
}

function addNewCustomTrader() {
  if (!auth.isSuperadmin) return
  const roleId = `trader_${Date.now().toString(36)}`
  councilConfig.value.roles[roleId] = {
    id: roleId,
    name: '自定义交易员',
    role_title: 'Custom Trader',
    description: '自主定制策略风格的交易员席位',
    prompt:
      '【角色：自定义资深交易员】\n' +
      '你作为对冲基金交易台的一线交易员，请核验账户可用资金、现有持仓与挂单，并对 6 大标的输出你的实战作战提案：\n' +
      '1. 现有持仓与挂单：逐一给出 HOLD/CLOSE_MARKET 或 CANCEL/KEEP 建议。\n' +
      '2. 作战提案：对 6 大币种逐一给出明确方向、限价、止损、止盈与拟用保证金。\n' +
      '3. 指出同行方案中的致命风险漏洞（50字内/币种）。',
    weight: 0.3,
    enabled: true,
    reasoning_effort: 'medium',
    temperature: 0.2,
    is_arbitrator: false,
    model_id: '',
  }
  expandedRole.value = roleId
  toast.ok('已添加自定义交易员席位，可直接编辑提示词与参数')
}

function removeRole(roleId: string) {
  if (!auth.isSuperadmin) return
  const role = councilConfig.value.roles[roleId]
  if (role?.is_arbitrator || roleId === 'cio') {
    alert('首席投资官 (CIO) 负责终审收口与发单，不可删除！')
    return
  }
  if (!confirm(`确定移除交易员【${role?.name || roleId}】席位吗？`)) return
  delete councilConfig.value.roles[roleId]
  toast.warn('已移除席位，点击右上角「保存配置」后生效')
}

async function resetRole(roleId: string) {
  if (!confirm(`确定将【${councilConfig.value.roles[roleId]?.name || roleId}】恢复出厂提示词吗？`)) return
  try {
    const res = await api('/api/v1/admin/council/reset-role', {
      method: 'POST',
      body: JSON.stringify({ role_id: roleId }),
    })
    councilConfig.value = res.config
    toast.ok('已重置为出厂标准模板')
  } catch (e: any) {
    toast.err(`重置失败: ${e.message}`)
  }
}

async function runDebateTest() {
  testing.value = true
  testResult.value = null
  expandedReasoning.value = {}
  toast.warn('投委会正在全息审阅资金与行情并组织交易员辩论（预计 10~25 秒）...')
  try {
    const res = await api('/api/v1/admin/council/test', {
      method: 'POST',
      body: JSON.stringify({}),
    })
    if (res.status === 'ok') {
      testResult.value = res
      toast.ok(`投委会辩论与 CIO 终审完成！耗时 ${res.transcript?.total_duration_ms || 0}ms`)
    } else {
      toast.err(`测试失败: ${res.error || '未知错误'}`)
    }
  } catch (e: any) {
    toast.err(`测试出错: ${e.message}`)
  } finally {
    testing.value = false
  }
}

onMounted(loadData)
</script>

<template>
  <div class="space-y-4">
    <!-- Notice Banner -->
    <!-- 1. Top Control Station: Switch, Consensus Mode & Actions -->
    <div class="rounded-2xl border p-4 sm:p-5 shadow-xs space-y-4" style="background-color: var(--surface-2); border-color: var(--line-1);">
      <!-- Header row -->
      <PageHeader :title="t('nav.admin.council')" description="多交易员独立提案、交叉质询，首席仲裁官统筹资金与敞口后终审发单" stacked>
        <template #actions>
        <div class="flex shrink-0 items-center justify-end gap-2">
          <span class="chip"><span class="dot" :class="councilConfig.enabled ? 'dot-up' : ''" />{{ councilConfig.enabled ? '议事中' : '单模型直连' }}</span>
          <!-- Toggle Button -->
          <button
            type="button"
            @click="auth.isSuperadmin && (councilConfig.enabled = !councilConfig.enabled)"
            class="btn-admin-secondary"
            :style="councilConfig.enabled ? { color: 'var(--up)', borderColor: 'var(--up-line)', backgroundColor: 'var(--up-bg)' } : {}"
            :disabled="!auth.isSuperadmin"
          >
            <ToggleRight v-if="councilConfig.enabled" class="w-3.5 h-3.5 text-emerald-400" />
            <ToggleLeft v-else class="w-3.5 h-3.5 opacity-50" />
            <span>{{ councilConfig.enabled ? '机制已开启' : '机制已关闭' }}</span>
          </button>

          <!-- Save Button -->
          <button
            @click="saveConfig"
            :disabled="saving || !auth.isSuperadmin"
            class="btn-admin-primary disabled:opacity-40"
          >
            <Save class="w-3.5 h-3.5" />
            <span>{{ saving ? '保存中...' : '保存配置' }}</span>
          </button>

          <!-- Test Button -->
          <button
            @click="runDebateTest"
            :disabled="testing"
            class="btn-admin-secondary disabled:opacity-40"
          >
            <Play class="w-3.5 h-3.5" :class="{ 'animate-spin': testing }" />
            <span>{{ testing ? '现场辩论中...' : '现场辩论测试' }}</span>
          </button>

          <!-- Export / Import Buttons -->
          <button
            @click="exportConfig"
            :disabled="!auth.isSuperadmin"
            class="btn-admin-secondary disabled:opacity-40"
            title="导出当前投委会席位、提示词与议事规则为 JSON 包"
          >
            <Download class="w-3.5 h-3.5" />
            <span>导出配置</span>
          </button>
          <button
            @click="auth.isSuperadmin && (importVisible = !importVisible)"
            :disabled="!auth.isSuperadmin"
            class="btn-admin-secondary disabled:opacity-40"
            title="导入投委会配置 JSON 包（导入前自动备份当前配置）"
          >
            <Upload class="w-3.5 h-3.5" />
            <span>导入配置</span>
          </button>
        </div>
        </template>
      </PageHeader>

      <!-- Import Panel -->
      <div
        v-if="importVisible"
        class="rounded-lg border p-3 space-y-2"
        style="border-color: var(--line-1); background: rgba(255, 255, 255, 0.02);"
      >
        <p class="text-[11px]" style="color: var(--ink-2);">
          选择 r20-council-config JSON 导出包，或直接粘贴其内容。导入前当前配置将自动备份（保留最近 10 份）；席位绑定的模型 ID 按导入包原样恢复，若本机无同名模型请导入后在席位卡片重新绑定。
        </p>
        <input
          type="file"
          accept="application/json,.json"
          @change="pickImportFile"
          class="text-[11px]"
          style="color: var(--ink-2);"
        />
        <textarea
          v-model="importRawJson"
          rows="8"
          placeholder='粘贴导出包 JSON：{"format":"r20-council-config","version":1,"config":{...}}'
          class="w-full text-[11px] rounded p-2 bg-transparent border"
          style="border-color: var(--line-1); color: var(--ink-1);"
        ></textarea>
        <p v-if="importFileError" class="text-[11px]" style="color: var(--down);">{{ importFileError }}</p>
        <div class="flex gap-2">
          <button
            @click="doImportConfig"
            :disabled="importing || !importRawJson.trim()"
            class="btn-admin-primary disabled:opacity-40"
          >
            <Upload class="w-3.5 h-3.5" />
            <span>{{ importing ? '导入中...' : '确认导入' }}</span>
          </button>
          <button
            @click="importVisible = false; importRawJson = ''; importFileError = ''"
            class="btn-admin-secondary"
          >
            <span>取消</span>
          </button>
        </div>
      </div>

      <!-- Consensus Mode Selection Grid -->
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div
          v-for="mode in consensusModes"
          :key="mode.id"
          @click="auth.isSuperadmin && (councilConfig.consensus_mode = mode.id)"
          class="p-3 rounded-xl border transition-all cursor-pointer shadow-xs"
          :style="councilConfig.consensus_mode === mode.id
            ? { borderColor: 'var(--accent-line)', backgroundColor: 'var(--accent-bg)' }
            : { borderColor: 'var(--line-1)', backgroundColor: 'var(--surface-1)' }"
        >
          <div class="flex items-center justify-between mb-1">
            <span class="text-xs font-bold" style="color: var(--ink-1);">{{ mode.name }}</span>
            <span
              class="text-[11px] px-1.5 py-0.5 rounded-[3px] border font-bold"
              :style="councilConfig.consensus_mode === mode.id
                ? { backgroundColor: 'var(--surface-2)', color: 'var(--ink-1)', borderColor: 'var(--line-2)' }
                : { backgroundColor: 'var(--surface-2)', color: 'var(--ink-2)', borderColor: 'var(--line-1)' }"
            >
              {{ mode.tag }}
            </span>
          </div>
          <p class="text-[11px] leading-relaxed" style="color: var(--ink-2);">
            {{ mode.desc }}
          </p>
        </div>
      </div>

      <!-- Quick Timeout & Preset Bar -->
      <div class="flex flex-wrap items-center justify-between gap-3 pt-2 border-t" style="border-color: var(--line-1);">
        <div class="flex items-center space-x-2">
          <span class="text-[11px] font-bold" style="color: var(--ink-2);">投委会超时保护:</span>
          <input
            v-model="councilConfig.timeout_seconds"
            type="number"
            min="10"
            max="180"
            step="5"
            class="w-16 rounded-lg px-2 py-1 text-xs outline-none border text-center"
            style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);"
            :disabled="!auth.isSuperadmin"
          />
          <span class="text-[11px] text-[var(--ink-2)]">秒 (超时自动降级为单模型决策)</span>
        </div>

        <div class="flex items-center space-x-2">
          <button
            @click="applySuite('hedge_fund_desk')"
            :disabled="!auth.isSuperadmin"
            class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-xs cursor-pointer transition-all"
            style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-1);"
          >
            <RotateCcw class="w-3 h-3 text-purple-400" />
            <span>恢复对冲基金标准阵容</span>
          </button>
          <button
            @click="addNewCustomTrader"
            :disabled="!auth.isSuperadmin"
            class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border border-dashed text-xs cursor-pointer transition-all"
            style="border-color: var(--accent); color: var(--accent);"
          >
            <Plus class="w-3 h-3" />
            <span>添加自定义交易员席位</span>
          </button>
        </div>
      </div>
    </div>

    <!-- 2. Trader Seats & CIO Desk (Core Cards) -->
    <div class="space-y-3">
      <div
        v-for="(role, roleId) in councilConfig.roles"
        :key="roleId"
        class="rounded-2xl border p-4 sm:p-5 transition-all shadow-xs"
        :style="{
          backgroundColor: expandedRole === roleId ? 'var(--surface-1)' : 'var(--surface-2)',
          borderColor: expandedRole === roleId ? 'var(--accent-line)' : 'var(--line-1)',
          opacity: role.enabled === false ? '0.6' : '1'
        }"
      >
        <!-- Seat Row -->
        <div class="flex flex-col md:flex-row md:items-center justify-between gap-3">
          <!-- Left: Identity -->
          <div class="flex items-center space-x-3 min-w-0 flex-1">
            <span
              class="w-9 h-9 rounded-xl flex items-center justify-center font-bold text-xs border shrink-0"
              :class="roleColors[roleId] || roleColors['custom']"
            >
              <component :is="roleIcons[roleId] || roleIcons['custom']" class="w-4 h-4" />
            </span>
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-2">
                <input
                  v-model="role.name"
                  class="bg-transparent border-b border-dashed text-sm font-bold outline-none max-w-[240px]"
                  style="border-color: var(--line-2); color: var(--ink-1);"
                  :readonly="!auth.isSuperadmin"
                  placeholder="角色名称"
                />
                <span
                  class="rounded px-2 py-0.5 text-[11px] border"
                  style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-2);"
                >
                  {{ role.role_title || (role.is_arbitrator ? 'CIO / 终审' : 'Senior Trader') }}
                </span>
                <span
                  v-if="role.is_arbitrator || roleId === 'cio'"
                  class="text-[11px] font-bold px-1.5 py-0.2 rounded border shrink-0 text-purple-400 border-purple-500/30 bg-purple-500/10"
                >
                  ⚖️ 终审发单席位
                </span>
                <span
                  v-else
                  class="text-[11px] px-1.5 py-0.2 rounded border shrink-0"
                  :style="role.enabled !== false ? { backgroundColor: 'var(--up-bg)', color: 'var(--up)', borderColor: 'var(--up-line)' } : { backgroundColor: 'var(--surface-3)', color: 'var(--ink-3)', borderColor: 'var(--line-1)' }"
                >
                  {{ role.enabled !== false ? '活跃参与' : '已静音' }}
                </span>
              </div>
              <p class="text-[11px] mt-0.5 truncate" style="color: var(--ink-2);">
                {{ role.description || '负责当前交易台的独立审查与实战方案提交' }}
              </p>
            </div>
          </div>

          <!-- Right: Model Binding, Weight & Controls -->
          <div class="flex flex-wrap items-center justify-end gap-2 shrink-0">
            <!-- Bound Model -->
            <div class="flex items-center space-x-1">
              <span class="text-[11px] text-[var(--ink-2)]">模型:</span>
              <select
                v-model="role.model_id"
                class="rounded-xl px-2 py-1 text-xs outline-none border cursor-pointer max-w-[150px]"
                style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);"
                :disabled="!auth.isSuperadmin"
              >
                <option value="">(继承全局主脑)</option>
                <option v-for="m in availableModels" :key="m.id" :value="m.id">
                  {{ m.name || m.id }}
                </option>
              </select>
            </div>

            <!-- Weight (For traders only) -->
            <div v-if="!role.is_arbitrator && roleId !== 'cio'" class="flex items-center space-x-1">
              <span class="text-[11px] text-[var(--ink-2)]">权重:</span>
              <input
                v-model="role.weight"
                type="number"
                step="0.05"
                min="0.1"
                max="1.0"
                class="w-14 rounded-xl px-1.5 py-1 text-xs outline-none text-center border"
                style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);"
                :disabled="!auth.isSuperadmin"
              />
            </div>

            <!-- Enable / Mute Toggle -->
            <button
              v-if="!role.is_arbitrator && roleId !== 'cio'"
              @click="role.enabled = role.enabled === false ? true : false"
              :disabled="!auth.isSuperadmin"
              class="cursor-pointer p-1"
              :class="role.enabled !== false ? 'text-emerald-400' : 'text-zinc-500'"
              :title="role.enabled !== false ? '静音此交易员' : '激活此交易员'"
            >
              <ToggleRight v-if="role.enabled !== false" class="w-5 h-5" />
              <ToggleLeft v-else class="w-5 h-5" />
            </button>

            <!-- Delete (Only for custom traders) -->
            <button
              v-if="!role.is_arbitrator && roleId !== 'cio' && !['trader_trend', 'trader_momentum', 'trader_quant'].includes(String(roleId))"
              @click="removeRole(String(roleId))"
              :disabled="!auth.isSuperadmin"
              class="p-1.5 rounded text-rose-400 hover:opacity-80 cursor-pointer"
              title="移除此席位"
            >
              <Trash2 class="w-3.5 h-3.5" />
            </button>

            <!-- Expand Accordion Button -->
            <button
              @click="expandedRole = expandedRole === roleId ? '' : String(roleId)"
              class="p-1.5 rounded cursor-pointer transition-colors"
              style="color: var(--ink-2);"
              title="展开/收起定制提示词"
            >
              <ChevronUp v-if="expandedRole === roleId" class="w-4 h-4" />
              <ChevronDown v-else class="w-4 h-4" />
            </button>
          </div>
        </div>

        <!-- Expanded Custom Prompt & Parameter Tuning -->
        <div v-if="expandedRole === roleId" class="mt-3 pt-3 border-t space-y-3" style="border-color: var(--line-1);">
          <div class="flex flex-wrap items-center justify-between gap-2 text-xs">
            <div class="flex items-center space-x-2">
              <span style="color: var(--ink-2);">采样温度:</span>
              <input
                v-model="role.temperature"
                type="number"
                step="0.05"
                min="0.0"
                max="1.0"
                class="w-16 rounded-lg px-2 py-0.5 text-xs outline-none text-center border"
                style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);"
                :disabled="!auth.isSuperadmin"
              />
              <span class="text-[11px] text-[var(--ink-2)]">(0.1~0.2 严格理性 / 0.3+ 进取)</span>
            </div>

            <!-- Quick Data Slots Inserter -->
            <div class="flex flex-wrap items-center gap-1 text-[11px]">
              <span class="text-[var(--ink-2)]">插入插槽:</span>
              <button
                v-for="slot in [
                  { k: 'macro_4h', label: '4H宏观' },
                  { k: 'calculus_1h', label: '微积分动能' },
                  { k: 'smart_money', label: '聪明钱' },
                  { k: 'orderbook_depth', label: '盘口深度' },
                  { k: 'sentiment', label: '情绪异动' },
                  { k: 'trading_memory', label: '长期心法' },
                ]"
                :key="slot.k"
                type="button"
                @click="role.prompt = role.prompt ? `${role.prompt.trim()}\n- 重点核验: {{${slot.k}}}` : `{{${slot.k}}}`"
                class="px-2 py-0.5 rounded-md border cursor-pointer hover:border-purple-400 transition-colors"
                style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-1);"
              >
                +&#123;&#123;{{ slot.k }}&#125;&#125;
              </button>

              <button
                @click="resetRole(String(roleId))"
                :disabled="!auth.isSuperadmin"
                class="ml-2 flex items-center space-x-1 text-[11px] text-purple-400 hover:underline cursor-pointer"
              >
                <RotateCcw class="w-3 h-3" />
                <span>恢复预设提示词</span>
              </button>
            </div>
          </div>

          <!-- Prompt Editor Textarea -->
          <textarea
            v-model="role.prompt"
            rows="6"
            class="w-full rounded-xl p-3 text-xs outline-none border leading-relaxed resize-y select-text transition-colors"
            style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);"
            :disabled="!auth.isSuperadmin"
            placeholder="编写该席位的实战职责、资金/持仓审查规范与作战提案指引..."
          ></textarea>
        </div>
      </div>
    </div>

    <!-- 3. Live Deliberation Docket & CIO Verdict (Only shown after test run) -->
    <div v-if="testResult" class="rounded-2xl border p-4 sm:p-5 space-y-4 shadow-lg" style="background-color: var(--surface-2); border-color: var(--accent-line);">
      <div class="flex items-center justify-between pb-3 border-b" style="border-color: var(--line-1);">
        <div class="flex flex-wrap items-center gap-2">
          <CheckCircle2 class="w-4 h-4 text-emerald-400" />
          <h3 class="text-sm font-bold" style="color: var(--ink-1);">投委会现场辩论与 CIO 裁定实录</h3>
          <span class="text-[11px] px-2 py-0.5 rounded border border-purple-500/30 bg-purple-500/10 text-purple-400">
            共识机制: {{ testResult.transcript?.consensus_mode }}
          </span>
          <span class="text-[11px] px-2 py-0.5 rounded border border-emerald-500/30 bg-emerald-500/10 text-emerald-400">
            全流程耗时 {{ testResult.transcript?.total_duration_ms }}ms
          </span>
        </div>
        <button
          @click="testResult = null"
          class="text-xs cursor-pointer px-3 py-1 rounded-lg border"
          style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-2);"
        >
          收起
        </button>
      </div>

      <!-- Traders' Proposals Grid -->
      <div class="space-y-1">
        <div class="text-xs font-bold text-zinc-400">第一轮：交易员独立实操审查与作战提案</div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div
            v-for="(adv, key) in testResult.transcript?.advisors || {}"
            :key="key"
            class="rounded-xl border p-3.5 space-y-2 flex flex-col justify-between"
            style="background-color: var(--surface-1); border-color: var(--line-1);"
          >
            <div class="space-y-1">
              <div class="flex items-center justify-between text-xs font-bold">
                <span style="color: var(--ink-1);">{{ adv.role_name }}</span>
                <span class="text-[11px] text-purple-400 truncate max-w-[120px]">{{ adv.model_used }}</span>
              </div>
              <div class="flex items-center justify-between text-[11px] text-[var(--ink-2)]">
                <span>响应: {{ adv.latency_ms }}ms</span>
                <span v-if="adv.proposal_id" class="text-zinc-500 text-[11px]">ID: {{ adv.proposal_id }}</span>
              </div>
              <p class="text-xs whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-auto pr-1 select-text" style="color: var(--ink-2);">
                {{ adv.content }}
              </p>
            </div>

            <div v-if="adv.reasoning" class="pt-2 border-t" style="border-color: var(--line-1);">
              <button
                @click="expandedReasoning[String(key)] = !expandedReasoning[String(key)]"
                class="text-[11px] text-purple-400 cursor-pointer"
              >
                <span>{{ expandedReasoning[String(key)] ? '收起思考链' : '展开思考链 (Reasoning)' }}</span>
              </button>
              <div
                v-if="expandedReasoning[String(key)]"
                class="mt-1.5 p-2 rounded text-[11px] whitespace-pre-wrap max-h-36 overflow-y-auto select-text border"
                style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-2);"
              >
                {{ adv.reasoning }}
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Optional: Round 2 Cross-Examinations Grid (only in cross_examination mode) -->
      <div v-if="testResult.transcript?.cross_examinations && Object.keys(testResult.transcript.cross_examinations).length > 0" class="space-y-1 pt-2">
        <div class="flex items-center space-x-2 text-xs font-bold text-amber-400">
          <span>第二轮：交叉质询与攻防实录</span>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div
            v-for="(crit, cKey) in testResult.transcript.cross_examinations"
            :key="cKey"
            class="rounded-xl border p-3.5 space-y-2"
            style="background-color: var(--surface-1); border-color: var(--line-1);"
          >
            <div class="flex items-center justify-between text-xs font-bold">
              <span style="color: var(--ink-1);">{{ crit.role_name || cKey }} 的质询</span>
              <span class="text-[11px]" :class="crit.status === 'ok' ? 'text-amber-400' : 'text-zinc-500'">
                {{ crit.status === 'ok' ? `${crit.latency_ms}ms` : (crit.status === 'skipped' ? '安全跳过' : '异常') }}
              </span>
            </div>
            <p class="text-xs whitespace-pre-wrap leading-relaxed max-h-40 overflow-y-auto pr-1 select-text" style="color: var(--ink-2);">
              {{ crit.content }}
            </p>
          </div>
        </div>
      </div>

      <!-- CIO Arbitrated Verdict & Order Dispatch -->
      <div class="rounded-xl border p-4 space-y-3" style="background-color: var(--surface-1); border-color: var(--accent-line);">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-2">
            <span class="text-xs font-bold text-purple-400">【首席投资官 (CIO) 终审批复】</span>
            <span class="text-[11px] text-[var(--ink-2)]">{{ testResult.transcript?.arbitrator?.model_used }} · 审阅耗时 {{ testResult.transcript?.arbitrator?.latency_ms }}ms</span>
          </div>
          <span class="text-[11px] font-bold px-2 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/20 text-emerald-400">
            方案采纳与点位落地
          </span>
        </div>

        <div class="text-xs font-bold leading-relaxed p-2.5 rounded border text-emerald-400" style="background-color: var(--surface-2); border-color: var(--line-1);">
          资金总括与决议: {{ testResult.brain_output?.macro_assessment }}
        </div>

        <!-- 6 Instruments Points Matrix -->
        <div v-if="testResult.brain_output?.decisions" class="space-y-1.5">
          <div class="text-xs font-bold" style="color: var(--ink-1);">六大标的落盘点位矩阵:</div>
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 text-xs">
            <div
              v-for="(dec, sym) in testResult.brain_output?.decisions"
              :key="sym"
              class="p-3 rounded-xl border flex flex-col justify-between space-y-2"
              style="background-color: var(--surface-2); border-color: var(--line-1);"
            >
              <div class="flex items-center justify-between gap-1.5">
                <span class="font-semibold text-sm" style="color: var(--ink-1);">{{ sym }}</span>
                <div class="flex items-center gap-1">
                  <span
                    v-if="dec.adopted_role"
                    class="px-1.5 py-0.5 rounded text-[11px] font-bold border"
                    :class="dec.adopted_role === 'REJECT_ALL' ? 'text-zinc-400 border-zinc-700 bg-zinc-800/40' : 'text-purple-300 border-purple-500/30 bg-purple-500/10'"
                  >
                    {{ dec.adopted_role === 'REJECT_ALL' ? '全员驳回' : `采纳: ${councilConfig.roles[dec.adopted_role]?.name || dec.adopted_role}` }}
                  </span>
                  <span
                    class="px-2 py-0.5 rounded text-[11px] font-bold border"
                    :style="{
                      backgroundColor: dec.action?.includes('BUY') ? 'var(--up-bg)' : dec.action?.includes('SELL') ? 'var(--down-bg)' : 'var(--surface-3)',
                      borderColor: dec.action?.includes('BUY') ? 'var(--up-line)' : dec.action?.includes('SELL') ? 'var(--down-line)' : 'var(--line-1)',
                      color: dec.action?.includes('BUY') ? 'var(--up)' : dec.action?.includes('SELL') ? 'var(--down)' : 'var(--ink-2)'
                    }"
                  >
                    {{ dec.action || 'WAIT' }} ({{ dec.confidence || 0 }}%)
                  </span>
                </div>
              </div>

              <!-- Price & Risk Metrics -->
              <div v-if="dec.action !== 'WAIT'" class="grid grid-cols-3 gap-1.5 p-2 rounded-lg bg-black/20 text-[11px] text-center">
                <div>
                  <div class="text-zinc-400">入场限价</div>
                  <div class="font-bold text-white mt-0.5">${{ dec.limit_price || dec.entry_price || '--' }}</div>
                </div>
                <div>
                  <div class="text-rose-400">2.0x止损</div>
                  <div class="font-bold text-rose-400 mt-0.5">${{ dec.stop_loss || '--' }}</div>
                </div>
                <div>
                  <div class="text-emerald-400">2.0R止盈</div>
                  <div class="font-bold text-emerald-400 mt-0.5">${{ dec.take_profit || '--' }}</div>
                </div>
              </div>
              <div v-else class="p-2 rounded-lg bg-black/10 text-[11px] text-zinc-500 italic">
                保持空仓防守，未达顺势回踩或微积分爆发要求。
              </div>

              <div class="text-[11px] text-zinc-400 line-clamp-3 leading-relaxed">
                {{ dec.reasoning || dec.reason || '遵从投委会综合裁定。' }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

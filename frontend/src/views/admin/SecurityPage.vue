<script setup lang="ts">
import { useToast } from '../../composables/useToast'
const toast = useToast()
import { ref, computed, onMounted } from 'vue'
import PageHeader from '../../components/admin/PageHeader.vue'
import SettingsSection from '../../components/admin/SettingsSection.vue'
import { useI18n } from '../../composables/useI18n'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import { fmtDateTime } from '../../utils/format'
import { labModeMeta } from '../../utils/labMode'
import { Wallet, Save, RefreshCw, Layers, Trash2, Zap } from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()
const { t } = useI18n()
const config = ref<any>(null)
const runtime = ref<any>(null)
const loading = ref(true)

type TabKey = 'venues' | 'pool' | 'emergency'
const activeTab = ref<TabKey>('venues')
const positionsLoadedOnce = ref(false)

function switchTab(tab: TabKey) {
  activeTab.value = tab
  if (tab === 'emergency' && !positionsLoadedOnce.value) {
    positionsLoadedOnce.value = true
    loadPositions()
  }
  if (tab === 'venues') loadLab()
}

// ---- LIVE / DEMO API keys (OKX) ----
const keys = ref({ live_key: '', live_secret: '', live_pass: '', demo_key: '', demo_secret: '', demo_pass: '' })

// ---- capital ----
const newCapital = ref<string>('')
const capitalConfirm = ref<string>('')
const savingCapital = ref(false)

// ---- instruments ----
const instruments = ref<any[]>([])
const instLimits = ref<any>({ minimum: 1, maximum: 20 })
const newInstId = ref('')

// ---- positions & close ----
const snapshot = ref<any>(null)
const snapshotState = ref('')
const manualClose = ref(false)
const closePassword = ref('')
const closeModal = ref<{ show: boolean; pos: any } | null>(null)
const closePhraseInput = ref('')
const closing = ref(false)

// ---- 多交易所数据源与凭证 (Binance / Gate) ----
const mx = ref<any>(null)
const mxForm = ref({ binance_api_key: '', binance_secret_key: '', gate_api_key: '', gate_secret_key: '' })
const mxTestnet = ref({ binance: false, gate: false })
const preferredVenue = ref('auto')
const gateExec = ref(false)
const gateExecPhrase = ref('')
const savingMx = ref(false)
const savingOkx = ref(false)

async function loadAll() {
  loading.value = true
  try {
    const [cfg, rt] = await Promise.all([
      api('/api/v1/admin/config'),
      api('/api/v1/admin/okx/runtime?refresh=1').catch(() => null),
    ])
    config.value = cfg
    applyRuntime(rt)
    newCapital.value = String(cfg.editable?.initial_capital ?? '')
    manualClose.value = !!cfg.editable?.manual_close_enabled
    const inst = await api('/api/v1/admin/instruments')
    instruments.value = inst.instruments || []
    instLimits.value = inst.limits || instLimits.value
  } catch (e: any) {
    toast.err(`加载失败：${e.message}`)
  } finally {
    loading.value = false
  }
}

function applyRuntime(rt: any) {
  runtime.value = rt
}

async function rediagnose() {
  try {
    applyRuntime(await api('/api/v1/admin/okx/runtime?refresh=1'))
    toast.ok('已刷新 OKX API Key 配置状态')
  } catch (e: any) {
    runtime.value = null
    toast.err(`诊断失败：${e.message}`)
  }
}

async function saveEnvironment() {
  const environment = config.value.editable.okx_environment
  if (environment === 'live') {
    const approved = prompt('切换到 LIVE 实盘环境\n输入 LIVE 确认已核对实盘 Key 权限与 IP 白名单')
    if (approved?.trim().toUpperCase() !== 'LIVE') {
      toast.warn('未输入 LIVE，环境未切换')
      return
    }
  }
  savingOkx.value = true
  try {
    const body: any = { okx_environment: environment }
    if (keys.value.live_key) body.okx_live_api_key = keys.value.live_key
    if (keys.value.live_secret) body.okx_live_secret_key = keys.value.live_secret
    if (keys.value.live_pass) body.okx_live_passphrase = keys.value.live_pass
    if (keys.value.demo_key) body.okx_demo_api_key = keys.value.demo_key
    if (keys.value.demo_secret) body.okx_demo_secret_key = keys.value.demo_secret
    if (keys.value.demo_pass) body.okx_demo_passphrase = keys.value.demo_pass
    await api('/api/v1/admin/config', { method: 'PUT', body: JSON.stringify(body) })
    keys.value = { live_key: '', live_secret: '', live_pass: '', demo_key: '', demo_secret: '', demo_pass: '' }
    toast.ok(`OKX ${environment.toUpperCase()} 环境与凭证已安全保存`)
    await loadAll()
  } catch (e: any) {
    toast.err(`保存失败：${e.message}`)
  } finally {
    savingOkx.value = false
  }
}

async function saveManualClose() {
  try {
    const d = await api('/api/v1/admin/config', { method: 'PUT', body: JSON.stringify({ manual_close_enabled: manualClose.value }) })
    manualClose.value = !!d.manual_close_enabled
    if (manualClose.value) toast.warn('后台手动平仓已启用'); else toast.ok('后台手动平仓已禁用')
  } catch (e: any) {
    toast.err(e.message)
  }
}

async function saveCapital() {
  if (!auth.isSuperadmin) { toast.err('仅超级管理员可修改初始本金'); return }
  if (capitalConfirm.value.trim().toUpperCase() !== 'UPDATE CAPITAL') { toast.err('确认短语必须精确为：UPDATE CAPITAL'); return }
  savingCapital.value = true
  try {
    const res = await api('/api/v1/admin/account-baseline', { method: 'PUT', body: JSON.stringify({ initial_capital: parseFloat(newCapital.value), confirmation: capitalConfirm.value }) })
    toast.ok(res.effect || `初始本金已调整为 ${res.initial_capital} USDT`)
    capitalConfirm.value = ''
    await loadAll()
  } catch (e: any) {
    toast.err(`更新失败：${e.message}`)
  } finally {
    savingCapital.value = false
  }
}

async function addInstrument() {
  const instId = newInstId.value.trim().toUpperCase()
  if (!/^[A-Z0-9]{2,15}-USDT-SWAP$/.test(instId)) { toast.err('格式示例：XRP-USDT-SWAP（仅 USDT 永续）'); return }
  try {
    const res = await api('/api/v1/admin/instruments', { method: 'POST', body: JSON.stringify({ inst_id: instId }) })
    toast.ok(res.message || `${instId} 已成功加入交易池`)
    newInstId.value = ''
    await loadAll()
  } catch (e: any) {
    toast.err(`添加失败：${e.message}`)
  }
}

async function removeInstrument(item: any) {
  if (item.protected) { toast.warn('系统保底标的不可删除'); return }
  if (!confirm(`确定将 ${item.instId} 从交易池移除？`)) return
  try {
    const res = await api(`/api/v1/admin/instruments/${encodeURIComponent(item.instId)}`, { method: 'DELETE' })
    toast.ok(res.message || `${item.instId} 已从交易池移除`)
    await loadAll()
  } catch (e: any) {
    toast.err(`删除失败：${e.message}`)
  }
}

async function loadPositions() {
  snapshotState.value = '正在从交易核心读取当前持仓与挂单…'
  try {
    const d = await api('/api/v1/admin/okx/account-snapshot')
    snapshot.value = d
    snapshotState.value = ''
  } catch (e: any) {
    snapshotState.value = e.message
    snapshot.value = null
  }
}

function openClose(pos: any) {
  if (!manualClose.value) { toast.err('请先在「应急平仓」页启用手动平仓开关'); return }
  closePhraseInput.value = ''
  closeModal.value = { show: true, pos }
}

async function confirmClose() {
  const pos = closeModal.value?.pos
  if (!pos) return
  if (!closePassword.value) { toast.err('请输入当前管理员密码'); return }
  if (!pos.close_token || !pos.close_confirmation) { toast.err('平仓令牌缺失，请刷新当前持仓'); return }
  if (closePhraseInput.value.trim().toUpperCase() !== pos.close_confirmation) {
    toast.err(`确认短语必须精确为：${pos.close_confirmation}`)
    return
  }
  closing.value = true
  try {
    const d = await api('/api/v1/admin/positions/close', {
      method: 'POST',
      body: JSON.stringify({ close_token: pos.close_token, admin_password: closePassword.value, confirmation: closePhraseInput.value.trim().toUpperCase() }),
    })
    toast.ok(`已确认平仓：${d.instId} ${d.closed_size}`)
    closeModal.value = null
    closePassword.value = ''
    await loadPositions()
  } catch (e: any) {
    toast.err(`平仓失败：${e.message}`)
  } finally {
    closing.value = false
  }
}

async function loadMx() {
  try {
    mx.value = await api('/api/v1/admin/multi-exchange')
    if (mx.value?.venues) {
      mxTestnet.value.binance = !!mx.value.venues.binance?.testnet
      mxTestnet.value.gate = !!mx.value.venues.gate?.testnet
      gateExec.value = !!mx.value.venues.gate?.execution_open
    }
    if (mx.value?.preferred_venue) {
      preferredVenue.value = mx.value.preferred_venue
    }
  } catch { mx.value = null }
  await loadLab()
}

// ---- Gate 试验田状态：模式/四道闸/在途/落账，纯本地只读 ----
const lab = ref<any>(null)
async function loadLab() {
  try { lab.value = await api('/api/v1/admin/multi-exchange/lab-status') } catch { lab.value = null }
}

async function saveMx() {
  savingMx.value = true
  try {
    const body: any = {
      binance_testnet: mxTestnet.value.binance,
      gate_testnet: mxTestnet.value.gate,
      preferred_venue: preferredVenue.value,
    }
    if (gateExec.value !== !!mx.value?.venues?.gate?.execution_open) {
      body.gate_execution = gateExec.value
      body.confirmation = gateExecPhrase.value.trim()
    }
    for (const k of ['binance_api_key', 'binance_secret_key', 'gate_api_key', 'gate_secret_key']) {
      const v = (mxForm.value as any)[k]
      if (v && v.trim()) body[k] = v.trim()
    }
    await api('/api/v1/admin/multi-exchange', { method: 'PUT', body: JSON.stringify(body) })
    toast.ok('多交易所凭证与路由配置已保存')
    mxForm.value = { binance_api_key: '', binance_secret_key: '', gate_api_key: '', gate_secret_key: '' }
    gateExecPhrase.value = ''
    await loadMx()
  } catch (e: any) {
    toast.err(`保存失败：${e.message}`)
  } finally {
    savingMx.value = false
  }
}

// ---- 总览派生（纯计算，零请求） ----
const okxLinked = computed(() => runtime.value?.status === 'READY' && runtime.value?.mode_configured === true)
const mxHealthChips = computed(() => {
  const venues = mx.value?.health?.venues
  if (!venues) return null
  return Object.entries(venues).map(([name, v]: [string, any]) => ({
    name,
    ok: (v.ok || []).length,
    total: (v.ok || []).length + Object.keys(v.failed || {}).length,
    avg_ms: v.avg_ms || 0,
    testnet: !!v.testnet,
  }))
})
const gateExecDirty = computed(() => gateExec.value !== !!mx.value?.venues?.gate?.execution_open)

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'venues', label: '交易所与路由对等' },
  { key: 'pool', label: '标的池与初始本金' },
  { key: 'emergency', label: '应急风控与持仓' },
]

function envBadge(env: string) {
  return (env || 'demo').toUpperCase()
}

onMounted(() => { loadAll(); loadMx() })
</script>

<template>
  <div class="space-y-4 text-xs">
    <PageHeader :title="t('nav.admin.security')" description="OKX、Binance、Gate 三交易所凭证、撮合路由策略与标的池对称管理">
      <template #actions>
        <span class="chip flex items-center gap-1.5">
          <span>选所模式</span>
          <b class="num" style="color: var(--accent);">{{ preferredVenue.toUpperCase() }}</b>
          <span class="text-[10px] opacity-70">·</span>
          <span>环境</span>
          <b class="num" :style="{ color: runtime?.environment === 'live' ? 'var(--down)' : 'var(--up)' }">{{ envBadge(runtime?.environment) }}</b>
        </span>
      </template>
    </PageHeader>

    <div v-if="loading" class="py-12 text-center" style="color: var(--ink-2);">正在同步配置…</div>

    <template v-else-if="config">
      <!-- 对称状态总览条 -->
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
        <div class="card card-pad flex flex-col justify-between" style="background-color: var(--surface-1);">
          <div class="flex items-center justify-between text-[11px]" style="color: var(--ink-3);">
            <span>OKX · V5 接口</span>
            <span class="dot" :class="okxLinked ? 'dot-up' : 'dot-down'" />
          </div>
          <div class="mt-1 flex items-baseline justify-between">
            <b class="text-xs font-bold" :style="{ color: okxLinked ? 'var(--up)' : 'var(--down)' }">
              {{ okxLinked ? '已接入 (READY)' : '未完整配置' }}
            </b>
            <span class="num text-[10px]" style="color: var(--ink-3);">{{ envBadge(runtime?.environment) }}</span>
          </div>
        </div>

        <div class="card card-pad flex flex-col justify-between" style="background-color: var(--surface-1);">
          <div class="flex items-center justify-between text-[11px]" style="color: var(--ink-3);">
            <span>Binance · USDT-M</span>
            <span class="dot" :class="mx?.venues?.binance?.has_api_key ? 'dot-up' : 'dot-warn'" />
          </div>
          <div class="mt-1 flex items-baseline justify-between">
            <b class="text-xs font-bold" :style="{ color: mx?.venues?.binance?.has_api_key ? 'var(--up)' : 'var(--warn)' }">
              {{ mx?.venues?.binance?.has_api_key ? '凭证已配置' : '免密公共行情' }}
            </b>
            <span class="num text-[10px]" style="color: var(--ink-3);">{{ mxTestnet.binance ? 'DEMO' : 'LIVE' }}</span>
          </div>
        </div>

        <div class="card card-pad flex flex-col justify-between" style="background-color: var(--surface-1);">
          <div class="flex items-center justify-between text-[11px]" style="color: var(--ink-3);">
            <span>Gate.io · 永续</span>
            <span class="dot" :class="mx?.venues?.gate?.has_api_key ? 'dot-up' : 'dot-warn'" />
          </div>
          <div class="mt-1 flex items-baseline justify-between">
            <b class="text-xs font-bold" :style="{ color: mx?.venues?.gate?.has_api_key ? 'var(--up)' : 'var(--warn)' }">
              {{ mx?.venues?.gate?.has_api_key ? (mx?.venues?.gate?.execution_open ? '已开闸实盘' : '已配·关闸中') : '免密公共行情' }}
            </b>
            <span class="num text-[10px]" style="color: var(--ink-3);">{{ mxTestnet.gate ? 'TESTNET' : 'LIVE' }}</span>
          </div>
        </div>

        <div class="card card-pad flex flex-col justify-between" style="background-color: var(--surface-1);">
          <div class="flex items-center justify-between text-[11px]" style="color: var(--ink-3);">
            <span>标的池 & 本金</span>
            <Layers class="h-3 w-3" style="color: var(--accent);" />
          </div>
          <div class="mt-1 flex items-baseline justify-between">
            <b class="num text-xs font-bold" style="color: var(--ink-1);">{{ instruments.length }}/{{ instLimits.maximum }} 标的</b>
            <span class="num text-[10px] font-bold" style="color: var(--up);">{{ config.editable.initial_capital }} U</span>
          </div>
        </div>
      </div>

      <!-- 选项卡切换 -->
      <div class="flex items-center gap-2 border-b pb-2 pt-1" style="border-color: var(--line-1);">
        <button
          v-for="tab in TABS" :key="tab.key"
          class="px-3 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer"
          :style="activeTab === tab.key ? { backgroundColor: 'var(--ink-1)', color: 'var(--surface-2)' } : { color: 'var(--ink-2)' }"
          @click="switchTab(tab.key)"
        >{{ tab.label }}</button>
      </div>

      <!-- ============ 页签 1：交易所与路由对等（三平台对称平权配置） ============ -->
      <div v-if="activeTab === 'venues'" class="space-y-4">
        <!-- 路由主策略 -->
        <SettingsSection title="撮合路由首选（三所对等）" description="配置 AI 信号的默认撮合交易所。可指定某所优先，或由智能评分路由按流动性、费率优势自动比选（支持 5% 滞回防抖）。">
          <template #actions>
            <button class="btn btn-primary" :disabled="savingMx" @click="saveMx"><Save class="h-3.5 w-3.5" /> {{ savingMx ? '保存中…' : '保存路由策略' }}</button>
          </template>
          <div class="space-y-3 rounded-lg border p-3.5" style="background-color: var(--surface-1); border-color: var(--line-1);">
            <div class="grid grid-cols-1 sm:grid-cols-4 gap-2">
              <label class="flex items-center gap-2 p-2.5 rounded-md border cursor-pointer transition-colors" :style="preferredVenue === 'auto' ? { borderColor: 'var(--accent)', backgroundColor: 'var(--surface-2)' } : { borderColor: 'var(--line-1)' }">
                <input v-model="preferredVenue" type="radio" value="auto" class="accent-[var(--accent)]" />
                <div>
                  <div class="text-xs font-bold" style="color: var(--ink-1);">Auto (智能路由)</div>
                  <div class="text-[10px]" style="color: var(--ink-3);">三所评分 + 滞回防抖</div>
                </div>
              </label>
              <label class="flex items-center gap-2 p-2.5 rounded-md border cursor-pointer transition-colors" :style="preferredVenue === 'okx' ? { borderColor: 'var(--accent)', backgroundColor: 'var(--surface-2)' } : { borderColor: 'var(--line-1)' }">
                <input v-model="preferredVenue" type="radio" value="okx" class="accent-[var(--accent)]" />
                <div>
                  <div class="text-xs font-bold" style="color: var(--ink-1);">优先 OKX</div>
                  <div class="text-[10px]" style="color: var(--ink-3);">首选 OKX 执行链路</div>
                </div>
              </label>
              <label class="flex items-center gap-2 p-2.5 rounded-md border cursor-pointer transition-colors" :style="preferredVenue === 'binance' ? { borderColor: 'var(--accent)', backgroundColor: 'var(--surface-2)' } : { borderColor: 'var(--line-1)' }">
                <input v-model="preferredVenue" type="radio" value="binance" class="accent-[var(--accent)]" />
                <div>
                  <div class="text-xs font-bold" style="color: var(--ink-1);">优先 Binance</div>
                  <div class="text-[10px]" style="color: var(--ink-3);">首选币安 USDT-M</div>
                </div>
              </label>
              <label class="flex items-center gap-2 p-2.5 rounded-md border cursor-pointer transition-colors" :style="preferredVenue === 'gate' ? { borderColor: 'var(--accent)', backgroundColor: 'var(--surface-2)' } : { borderColor: 'var(--line-1)' }">
                <input v-model="preferredVenue" type="radio" value="gate" class="accent-[var(--accent)]" />
                <div>
                  <div class="text-xs font-bold" style="color: var(--ink-1);">优先 Gate</div>
                  <div class="text-[10px]" style="color: var(--ink-3);">首选 Gate 永续试验田</div>
                </div>
              </label>
            </div>
            <p class="text-[11px] leading-relaxed" style="color: var(--ink-3);">
              当前生效：<b class="num" style="color: var(--accent);">{{ preferredVenue.toUpperCase() }}</b>。
              系统实行严密准入护栏：若所选交易所未配置密钥、未开闸或标的未上市，将自动平滑回退，并在决策日志留存证据。
            </p>
          </div>
        </SettingsSection>

        <!-- 三所凭证三列对称卡片 -->
        <SettingsSection title="三所接入凭证与环境（对称配置）" description="各交易所密钥仅在本机通过 Fernet 加密落盘；所有密钥留空表示不修改原有配置。">
          <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
            <!-- 1. OKX -->
            <div class="flex flex-col justify-between rounded-lg border p-3" style="background-color: var(--surface-1); border-color: var(--line-1);">
              <div class="space-y-2.5">
                <div class="flex items-center justify-between border-b pb-2" style="border-color: var(--line-1);">
                  <div>
                    <h4 class="text-xs font-bold" style="color: var(--ink-1);">OKX · 欧易</h4>
                    <span class="text-[10px]" style="color: var(--ink-3);">V5 REST API 直签</span>
                  </div>
                  <span class="text-[10px] px-1.5 py-0.5 rounded border font-bold" :style="okxLinked ? { color: 'var(--up)', borderColor: 'var(--up-line)' } : { color: 'var(--down)', borderColor: 'var(--down-line)' }">
                    {{ okxLinked ? 'READY' : '未就绪' }}
                  </span>
                </div>
                <div>
                  <label class="block text-[10px] mb-1" style="color: var(--ink-2);">OKX 资金环境</label>
                  <select v-model="config.editable.okx_environment" class="input w-full text-xs">
                    <option value="demo">模拟盘 (DEMO)</option>
                    <option value="live">实盘 (LIVE)</option>
                  </select>
                </div>
                <div class="space-y-1.5 pt-1">
                  <div class="text-[10px] font-semibold" style="color: var(--ink-2);">实盘 (LIVE) Key</div>
                  <input v-model="keys.live_key" type="password" placeholder="API Key（留空不改）" class="input w-full text-xs" />
                  <input v-model="keys.live_secret" type="password" placeholder="Secret Key" class="input w-full text-xs" />
                  <input v-model="keys.live_pass" type="password" placeholder="Passphrase" class="input w-full text-xs" />
                </div>
                <div class="space-y-1.5 pt-1">
                  <div class="text-[10px] font-semibold" style="color: var(--ink-2);">模拟盘 (DEMO) Key</div>
                  <input v-model="keys.demo_key" type="password" placeholder="API Key（留空不改）" class="input w-full text-xs" />
                  <input v-model="keys.demo_secret" type="password" placeholder="Secret Key" class="input w-full text-xs" />
                  <input v-model="keys.demo_pass" type="password" placeholder="Passphrase" class="input w-full text-xs" />
                </div>
              </div>
              <div class="pt-3 mt-2 border-t flex items-center justify-between" style="border-color: var(--line-1);">
                <button class="btn btn-quiet btn-sm" @click="rediagnose"><RefreshCw class="h-3 w-3" /> 检测</button>
                <button class="btn btn-primary btn-sm" :disabled="savingOkx" @click="saveEnvironment"><Save class="h-3 w-3" /> {{ savingOkx ? '保存中…' : '保存 OKX' }}</button>
              </div>
            </div>

            <!-- 2. Binance -->
            <div class="flex flex-col justify-between rounded-lg border p-3" style="background-color: var(--surface-1); border-color: var(--line-1);">
              <div class="space-y-2.5">
                <div class="flex items-center justify-between border-b pb-2" style="border-color: var(--line-1);">
                  <div>
                    <h4 class="text-xs font-bold" style="color: var(--ink-1);">Binance · 币安</h4>
                    <span class="text-[10px]" style="color: var(--ink-3);">USDT-M 永续合约</span>
                  </div>
                  <span class="text-[10px] px-1.5 py-0.5 rounded border font-bold" :style="mx?.venues?.binance?.has_api_key ? { color: 'var(--up)', borderColor: 'var(--up-line)' } : { color: 'var(--ink-3)', borderColor: 'var(--line-2)' }">
                    {{ mx?.venues?.binance?.has_api_key ? '已配置 Key' : '免密行情' }}
                  </span>
                </div>
                <div>
                  <label class="block text-[10px] mb-1" style="color: var(--ink-2);">端点网络档位</label>
                  <label class="flex items-center gap-1.5 text-[11px] cursor-pointer pt-1" style="color: var(--ink-2);">
                    <input v-model="mxTestnet.binance" type="checkbox" class="accent-[var(--accent)]" />
                    使用官方 Demo 沙盒 (demo-fapi)
                  </label>
                </div>
                <div class="space-y-1.5 pt-1">
                  <div class="text-[10px] font-semibold" style="color: var(--ink-2);">执行凭证（可选）</div>
                  <input v-model="mxForm.binance_api_key" type="text" placeholder="API Key（留空不改）" class="input w-full text-xs" />
                  <input v-model="mxForm.binance_secret_key" type="password" placeholder="API Secret" class="input w-full text-xs" />
                </div>
                <p class="text-[10px] leading-relaxed pt-2" style="color: var(--ink-3);">
                  跨所行情比对、资金费与深度比对免密即可全自动工作；执行面待独立条件单落地。
                </p>
              </div>
              <div class="pt-3 mt-2 border-t flex justify-end" style="border-color: var(--line-1);">
                <button class="btn btn-primary btn-sm" :disabled="savingMx" @click="saveMx"><Save class="h-3 w-3" /> {{ savingMx ? '保存中…' : '保存 Binance' }}</button>
              </div>
            </div>

            <!-- 3. Gate -->
            <div class="flex flex-col justify-between rounded-lg border p-3" style="background-color: var(--surface-1); border-color: var(--line-1);">
              <div class="space-y-2.5">
                <div class="flex items-center justify-between border-b pb-2" style="border-color: var(--line-1);">
                  <div>
                    <h4 class="text-xs font-bold" style="color: var(--ink-1);">Gate.io · 芝麻</h4>
                    <span class="text-[10px]" style="color: var(--ink-3);">V4 永续试验田</span>
                  </div>
                  <span class="text-[10px] px-1.5 py-0.5 rounded border font-bold" :style="mx?.venues?.gate?.has_api_key ? { color: 'var(--up)', borderColor: 'var(--up-line)' } : { color: 'var(--ink-3)', borderColor: 'var(--line-2)' }">
                    {{ mx?.venues?.gate?.has_api_key ? '已配置 Key' : '免密行情' }}
                  </span>
                </div>
                <div>
                  <label class="block text-[10px] mb-1" style="color: var(--ink-2);">端点网络档位</label>
                  <label class="flex items-center gap-1.5 text-[11px] cursor-pointer pt-1" style="color: var(--ink-2);">
                    <input v-model="mxTestnet.gate" type="checkbox" class="accent-[var(--accent)]" />
                    使用官方沙盒 (fx-api-testnet)
                  </label>
                </div>
                <div class="space-y-1.5 pt-1">
                  <div class="text-[10px] font-semibold" style="color: var(--ink-2);">执行凭证（实盘试验田必需）</div>
                  <input v-model="mxForm.gate_api_key" type="text" placeholder="API Key（留空不改）" class="input w-full text-xs" />
                  <input v-model="mxForm.gate_secret_key" type="password" placeholder="API Secret" class="input w-full text-xs" />
                </div>
                <div class="pt-1">
                  <label class="flex items-center gap-1.5 text-[11px] cursor-pointer font-bold" :style="{ color: gateExec ? 'var(--down)' : 'var(--ink-2)' }">
                    <input v-model="gateExec" type="checkbox" class="accent-[var(--accent)]" />
                    Gate 执行路由总闸 {{ mx?.venues?.gate?.execution_open ? '(已开闸)' : '(关闸)' }}
                  </label>
                  <input v-if="gateExecDirty && gateExec" v-model="gateExecPhrase" placeholder="输入短语：OPEN GATE EXECUTION" class="input w-full text-xs mt-1.5" />
                </div>
              </div>
              <div class="pt-3 mt-2 border-t flex justify-end" style="border-color: var(--line-1);">
                <button class="btn btn-primary btn-sm" :disabled="savingMx" @click="saveMx"><Save class="h-3 w-3" /> {{ savingMx ? '保存中…' : '保存 Gate' }}</button>
              </div>
            </div>
          </div>
        </SettingsSection>

        <!-- 行情健康与 Gate 试验田看板 -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <SettingsSection title="跨所行情健康容灾" description="公共行情每周期自动探活排序，零网络开销">
            <template #actions>
              <button class="btn btn-quiet btn-sm" @click="loadMx"><RefreshCw class="h-3 w-3" /> 重新检测</button>
            </template>
            <div v-if="mxHealthChips" class="flex flex-wrap gap-2 text-[11px]">
              <span v-for="h in mxHealthChips" :key="h.name" class="px-2 py-1 rounded border font-bold num" :style="h.ok === h.total ? { color: 'var(--up)', borderColor: 'var(--up-line)', backgroundColor: 'var(--up-bg)' } : { color: 'var(--warn)', borderColor: 'var(--warn-line)', backgroundColor: 'var(--warn-bg)' }">
                {{ h.name }} {{ h.ok }}/{{ h.total }} 币{{ h.avg_ms ? ' · ' + h.avg_ms + 'ms' : '' }}
              </span>
            </div>
            <div v-else class="text-[11px]" style="color: var(--ink-3);">尚无周期数据，等待下个 15 分钟周期。</div>
          </SettingsSection>

          <SettingsSection title="Gate 试验田实时状态" description="四道闸巡检与主台账落单核验">
            <template #actions>
              <button class="btn btn-quiet btn-sm" @click="loadLab"><RefreshCw class="h-3 w-3" /> 刷新</button>
            </template>
            <template v-if="lab">
              <div class="flex flex-wrap items-center gap-2 text-[11px]">
                <span class="px-2 py-0.5 rounded border font-bold" :style="{ color: labModeMeta(lab.mode).color, borderColor: labModeMeta(lab.mode).borderColor, backgroundColor: labModeMeta(lab.mode).backgroundColor }">
                  {{ labModeMeta(lab.mode).label }}
                </span>
                <span :style="{ color: lab.gates?.pool_nonempty ? 'var(--up)' : 'var(--down)' }">{{ lab.gates?.pool_nonempty ? '✓' : '✗' }} 币池 {{ (lab.pool?.assets || []).join('/') || '空' }}</span>
                <span :style="{ color: lab.gates?.execution_switch ? 'var(--up)' : 'var(--down)' }">{{ lab.gates?.execution_switch ? '✓' : '✗' }} 开关</span>
                <span :style="{ color: lab.gates?.credentials ? 'var(--up)' : 'var(--down)' }">{{ lab.gates?.credentials ? '✓' : '✗' }} 凭证</span>
              </div>
              <div v-if="lab.error" class="mt-1 text-[11px]" style="color: var(--warn);">{{ lab.error }}</div>
              <div v-if="(lab.trackers || []).length" class="mt-2 text-[11px]">在途仓位: {{ lab.trackers.length }} 笔</div>
            </template>
            <div v-else class="text-[11px]" style="color: var(--ink-3);">状态接口未就绪。</div>
          </SettingsSection>
        </div>
      </div>

      <!-- ============ 页签 2：标的池与初始本金 ============ -->
      <div v-if="activeTab === 'pool'" class="space-y-4">
        <SettingsSection title="交易标的池" :description="`当前 ${instruments.length}/${instLimits.maximum} 个 USDT 永续；变更实时同步全网大屏、因果雷达与决策核心。`">
          <template #actions>
            <input v-model="newInstId" placeholder="例如: XRP-USDT-SWAP" class="input w-44" @keyup.enter="addInstrument" />
            <button class="btn btn-primary" @click="addInstrument"><Layers class="h-3.5 w-3.5" /> 添加标的</button>
          </template>
          <div class="overflow-x-auto -mx-4 px-4">
            <table v-if="instruments.length" class="w-full text-left text-xs whitespace-nowrap">
              <thead>
                <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--line-1); color: var(--ink-2);">
                  <th class="py-2 pl-0 pr-4">合约代码</th>
                  <th class="py-2 px-3">名称</th>
                  <th class="py-2 px-3">类型</th>
                  <th class="py-2 px-3">风控状态</th>
                  <th class="py-2 px-4 text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in instruments" :key="item.instId" class="border-b last:border-b-0 transition-colors hover:bg-[var(--surface-3)]" style="border-color: var(--line-1);">
                  <td class="py-2 pl-0 pr-4 font-bold num" style="color: var(--ink-1);">{{ item.instId }}</td>
                  <td class="py-2 px-3" style="color: var(--ink-2);">{{ item.name }}</td>
                  <td class="py-2 px-3 num" style="color: var(--ink-3);">{{ item.ctType || 'SWAP' }}</td>
                  <td class="py-2 px-3">
                    <span v-if="item.protected" class="px-1.5 py-0.5 rounded-[3px] text-[11px] font-bold border" style="background-color: var(--warn-bg); border-color: var(--warn-line); color: var(--warn);">保底必选</span>
                    <span v-else-if="item.has_tracker" class="px-1.5 py-0.5 rounded-[3px] text-[11px] font-bold border" style="background-color: var(--accent-bg); border-color: var(--accent-line); color: var(--accent);">持仓中</span>
                    <span v-else class="text-[11px] px-1.5 py-0.5 rounded-[3px] border" style="background-color: var(--surface-3); border-color: var(--line-1); color: var(--ink-3);">可移除</span>
                  </td>
                  <td class="py-2 px-4 text-right">
                    <button :disabled="item.protected || item.has_tracker" class="p-1 rounded cursor-pointer transition-opacity hover:opacity-80 disabled:opacity-20" style="color: var(--down);" title="从标的池移除" @click="removeInstrument(item)">
                      <Trash2 class="h-3.5 w-3.5" />
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-else class="py-8 text-center text-xs" style="color: var(--ink-3);">标的池为空——至少保留保底标的 BTC-USDT-SWAP。</div>
          </div>
          <p class="pt-3 text-[11px]" style="color: var(--ink-3);">BTC 为系统保底标的不可删除；有在途追踪器的标的禁止移除；最多 {{ instLimits.maximum }} 个；仅支持 USDT 永续。</p>
        </SettingsSection>

        <SettingsSection title="初始本金基准" description="累计盈亏、收益率与权益走势的起算基准。修改本金会重置累计 ROI 起算点，不影响历史成交记录。">
          <div class="grid grid-cols-1 sm:grid-cols-[200px_1fr_auto] gap-3 items-end">
            <div>
              <label class="block text-[11px] mb-1" style="color: var(--ink-2);">新初始本金 (USDT)</label>
              <input v-model="newCapital" type="number" step="0.01" class="input w-full num" />
            </div>
            <div>
              <label class="block text-[11px] mb-1" style="color: var(--ink-2);">确认短语（UPDATE CAPITAL）</label>
              <input v-model="capitalConfirm" placeholder="输入 UPDATE CAPITAL" class="input w-full" />
            </div>
            <button class="btn btn-primary" :disabled="savingCapital" @click="saveCapital"><Wallet class="h-3.5 w-3.5" /> {{ savingCapital ? '更新中…' : '更新基准本金' }}</button>
          </div>
          <p class="pt-3 text-[11px]" style="color: var(--ink-3);">历史起算时间 {{ config.editable.initial_capital_reset_time }}；仅超级管理员可修改。</p>
        </SettingsSection>
      </div>

      <!-- ============ 页签 3：应急风控与持仓 ============ -->
      <div v-if="activeTab === 'emergency'" class="space-y-4">
        <SettingsSection title="后台手动平仓总闸" description="应急通道：默认禁用。启用后下方持仓表出现「快速平仓」，平仓仍需管理员密码 + 一次性令牌 + 确认短语三重确认。">
          <template #actions>
            <button class="btn btn-quiet" @click="saveManualClose"><Save class="h-3.5 w-3.5" /> 保存开关</button>
          </template>
          <label class="flex items-center gap-2 cursor-pointer w-fit">
            <input v-model="manualClose" type="checkbox" class="accent-[var(--accent)]" />
            <span class="text-xs" :style="{ color: manualClose ? 'var(--warn)' : 'var(--ink-2)', fontWeight: manualClose ? 700 : 400 }">
              {{ manualClose ? '已启用——允许后台市价应急平仓' : '已禁用——交易核心自主管理，后台不提供手动平仓' }}
            </span>
          </label>
        </SettingsSection>

        <SettingsSection title="当前活动持仓与挂单快照" description="从交易系统读取实时持仓与挂单快照（只读探针）；平仓流程：复核仓位 → 撤销冲突委托 → autoCxl 市价平仓 → 轮询确认归零。">
          <template #actions>
            <button class="btn btn-quiet" @click="loadPositions"><Zap class="h-3.5 w-3.5" /> 刷新持仓与挂单</button>
          </template>
          <div v-if="snapshotState" class="text-[11px] pb-2" style="color: var(--warn);">{{ snapshotState }}</div>
          <div v-if="snapshot" class="text-[11px] pb-2" style="color: var(--ink-2);">
            环境 <b :style="{ color: snapshot.environment === 'live' ? 'var(--down)' : 'var(--up)' }">{{ envBadge(snapshot.environment) }}</b>
            · 持仓 {{ snapshot.positions?.length ?? 0 }} · 挂单 {{ snapshot.orders?.length ?? 0 }} · {{ fmtDateTime(snapshot.captured_at_ms) }}
          </div>
          <div class="overflow-x-auto -mx-4 px-4">
            <table v-if="snapshot?.positions?.length" class="w-full text-left text-xs whitespace-nowrap">
              <thead>
                <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--line-1); color: var(--ink-2);">
                  <th class="py-2 pl-0 pr-4">仓位标的</th>
                  <th class="py-2 px-3">张数</th>
                  <th class="py-2 px-3">模式</th>
                  <th class="py-2 px-3">未实现盈亏</th>
                  <th class="py-2 px-4 text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in snapshot.positions" :key="p.instId + p.posSide" class="border-b last:border-b-0 transition-colors hover:bg-[var(--surface-3)]" style="border-color: var(--line-1);">
                  <td class="py-2 pl-0 pr-4">
                    <b class="num" style="color: var(--ink-1);">{{ p.instId }}</b>
                    <span class="ml-1.5 px-1.5 py-0.5 rounded text-[11px] font-bold border" :style="p.posSide === 'long' ? { backgroundColor: 'var(--up-bg)', borderColor: 'var(--up-line)', color: 'var(--up)' } : { backgroundColor: 'var(--down-bg)', borderColor: 'var(--down-line)', color: 'var(--down)' }">
                      {{ (p.posSide || 'net').toUpperCase() }}
                    </span>
                  </td>
                  <td class="py-2 px-3 num" style="color: var(--ink-2);">{{ p.pos || '0' }}</td>
                  <td class="py-2 px-3 text-[11px]" style="color: var(--ink-3);">{{ p.mgnMode || '--' }}</td>
                  <td class="py-2 px-3 font-bold num" :style="{ color: Number(p.upl || 0) >= 0 ? 'var(--up)' : 'var(--down)' }">{{ Number(p.upl || 0).toFixed(4) }}</td>
                  <td class="py-2 px-4 text-right">
                    <button class="px-2.5 py-1 rounded-md text-[11px] font-bold border cursor-pointer transition-all" style="background-color: var(--down-bg); border-color: var(--down-line); color: var(--down);" @click="openClose(p)">快速平仓</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-else-if="snapshot" class="py-8 text-center text-xs" style="color: var(--up);">✓ 当前环境 0 活跃持仓</div>
            <div v-else-if="!snapshotState" class="py-8 text-center text-xs" style="color: var(--ink-3);">点击「刷新持仓与挂单」读取最新实时状态。</div>
          </div>
        </SettingsSection>
      </div>
    </template>

    <!-- 平仓双确认弹窗 -->
    <div v-if="closeModal?.show" class="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4" @click.self="closeModal = null">
      <div class="rounded-xl border p-5 sm:p-6 w-full max-w-[460px] max-h-[88dvh] overflow-y-auto shadow-2xl" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <h3 class="text-sm font-bold mb-2" style="color: var(--down);">快速安全平仓</h3>
        <p class="text-[11px] leading-relaxed mb-3" style="color: var(--ink-2);">
          将从 <b :style="{ color: snapshot?.environment === 'live' ? 'var(--down)' : 'var(--up)' }">{{ envBadge(snapshot?.environment) }}</b> 环境重新核对并平掉
          <b style="color: var(--ink-1);">{{ closeModal.pos.instId }} {{ (closeModal.pos.posSide || 'net').toUpperCase() }} {{ Math.abs(Number(closeModal.pos.pos || 0)) }}</b>。
          令牌 90 秒有效且仅可使用一次。
        </p>
        <label class="block text-[11px] mb-1" style="color: var(--ink-2);">当前管理员密码</label>
        <input v-model="closePassword" type="password" class="input w-full mb-3" />
        <label class="block text-[11px] mb-1" style="color: var(--ink-2);">确认短语：{{ closeModal.pos.close_confirmation }}</label>
        <input v-model="closePhraseInput" :placeholder="closeModal.pos.close_confirmation" class="input w-full mb-4" />
        <div class="flex justify-end gap-2">
          <button class="btn btn-quiet" @click="closeModal = null">取消</button>
          <button class="px-3 py-2 rounded-lg text-xs font-bold cursor-pointer disabled:opacity-50 transition-all" style="background-color: var(--down-bg); border: 1px solid var(--down-line); color: var(--down);" :disabled="closing" @click="confirmClose">{{ closing ? '执行中，等待成交确认…' : '确认平仓' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.input {
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  outline: none;
  border: 1px solid var(--line-1);
  background-color: var(--surface-input);
  color: var(--ink-1);
  transition: border-color 0.15s ease;
}
.input:focus { border-color: var(--accent); }
</style>

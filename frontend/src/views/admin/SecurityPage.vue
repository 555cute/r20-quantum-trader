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
import VenueCredentialCard from '../../components/admin/VenueCredentialCard.vue'
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

// ---- 多交易所凭证与档位（Binance / Gate 各自独立，互不牵连保存） ----
const mx = ref<any>(null)
const mxForm = ref({ binance_api_key: '', binance_secret_key: '', gate_api_key: '', gate_secret_key: '' })
const mxTestnet = ref({ binance: false, gate: false })
const preferredVenue = ref('auto')
const gateExec = ref(false)
const gateExecPhrase = ref('')
const savingMx = ref(false)
const savingOkx = ref(false)
const savingVenue = ref<'binance' | 'gate' | ''>('')
const probingVenue = ref<'binance' | 'gate' | 'okx' | ''>('')

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
}

/** 单所档位/凭证连接诊断（US-003）：支持未保存凭证的实弹预检与公共网络连通性探测。 */
async function probeVenue(venue: 'binance' | 'gate' | 'okx') {
  probingVenue.value = venue
  try {
    const isDemo = venue === 'okx'
      ? (config.value?.editable?.okx_environment === 'demo')
      : !!mxTestnet.value[venue]
    const env = isDemo ? 'demo' : 'live'

    const payload: Record<string, any> = {
      venue,
      environment: env,
    }

    if (venue === 'binance') {
      const k = mxForm.value.binance_api_key.trim()
      const s = mxForm.value.binance_secret_key.trim()
      if (k) payload.api_key = k
      if (s) payload.secret_key = s
    } else if (venue === 'gate') {
      const k = mxForm.value.gate_api_key.trim()
      const s = mxForm.value.gate_secret_key.trim()
      if (k) payload.api_key = k
      if (s) payload.secret_key = s
    } else if (venue === 'okx') {
      if (isDemo) {
        if (keys.value.demo_key.trim()) payload.api_key = keys.value.demo_key.trim()
        if (keys.value.demo_secret.trim()) payload.secret_key = keys.value.demo_secret.trim()
        if (keys.value.demo_pass.trim()) payload.passphrase = keys.value.demo_pass.trim()
      } else {
        if (keys.value.live_key.trim()) payload.api_key = keys.value.live_key.trim()
        if (keys.value.live_secret.trim()) payload.secret_key = keys.value.live_secret.trim()
        if (keys.value.live_pass.trim()) payload.passphrase = keys.value.live_pass.trim()
      }
    }

    const res: any = await api('/api/v1/admin/multi-exchange/test-connection', {
      method: 'POST',
      body: JSON.stringify(payload),
    })

    if (res?.ok) {
      toast.ok(res.message || `${venue.toUpperCase()} 连接诊断成功`)
    } else {
      toast.err(res?.message || `${venue.toUpperCase()} 连接诊断失败`)
    }
    await loadMx()
  } catch (e: any) {
    toast.err(`检测失败：${e.message}`)
  } finally {
    probingVenue.value = ''
  }
}

/** 保存撮合路由首选（只写 preferred_venue，不牵连任何凭证字段）。 */
async function saveRouting() {
  savingMx.value = true
  try {
    await api('/api/v1/admin/multi-exchange', {
      method: 'PUT',
      body: JSON.stringify({ preferred_venue: preferredVenue.value }),
    })
    toast.ok(`撮合路由首选已保存：${preferredVenue.value.toUpperCase()}`)
    await loadMx()
  } catch (e: any) {
    toast.err(`保存失败：${e.message}`)
  } finally {
    savingMx.value = false
  }
}

/**
 * 逐所独立保存凭证与档位：只提交本所键位，留空即不改。
 * Gate 额外承载执行总闸（变更需精确确认短语，与 UPDATE CAPITAL 同族纪律）。
 */
async function saveVenue(venue: 'binance' | 'gate') {
  savingVenue.value = venue
  try {
    const body: any = {}
    if (venue === 'binance') {
      body.binance_testnet = mxTestnet.value.binance
      const k = mxForm.value.binance_api_key.trim()
      const s = mxForm.value.binance_secret_key.trim()
      if (k) body.binance_api_key = k
      if (s) body.binance_secret_key = s
    } else {
      body.gate_testnet = mxTestnet.value.gate
      const k = mxForm.value.gate_api_key.trim()
      const s = mxForm.value.gate_secret_key.trim()
      if (k) body.gate_api_key = k
      if (s) body.gate_secret_key = s
      if (gateExecDirty.value) {
        body.gate_execution = gateExec.value
        body.confirmation = gateExecPhrase.value.trim()
      }
    }
    await api('/api/v1/admin/multi-exchange', { method: 'PUT', body: JSON.stringify(body) })
    toast.ok(`${venue === 'binance' ? 'Binance' : 'Gate'} 凭证与档位已保存`)
    if (venue === 'binance') { mxForm.value.binance_api_key = ''; mxForm.value.binance_secret_key = '' }
    else { mxForm.value.gate_api_key = ''; mxForm.value.gate_secret_key = ''; gateExecPhrase.value = '' }
    await loadMx()
  } catch (e: any) {
    toast.err(`保存失败：${e.message}`)
  } finally {
    savingVenue.value = ''
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

/** 三所对称徽章派生：状态未知（后端未加载/所未注册）一律 warn + 文字「状态未知」，绝不升级为已就绪。 */
type VenueTone = 'up' | 'warn' | 'down'
function venueStatus(venue: 'binance' | 'gate'): { text: string; tone: VenueTone } {
  const v = mx.value?.venues?.[venue]
  if (!v) return { text: '状态未知', tone: 'warn' }
  return v.has_api_key
    ? { text: v.execution_open ? '已配置 Key · 执行开闸' : '已配置 Key · 关闸中', tone: 'up' }
    : { text: '免密公共行情', tone: 'warn' }
}
const binanceStatus = computed(() => venueStatus('binance'))
const gateStatus = computed(() => venueStatus('gate'))

/** 资金档位文字（身份必须有文字，颜色不作唯一识别） */
const okxEnvText = computed(() => {
  const env = String(config.value?.editable?.okx_environment || '')
  return env === 'live' ? 'LIVE 实盘' : env === 'demo' ? 'DEMO 模拟盘' : '未知'
})
function envTextOf(venue: 'binance' | 'gate', sandboxLabel: string) {
  if (!mx.value?.venues?.[venue]) return '未知'
  return mxTestnet.value[venue] ? sandboxLabel : 'LIVE 实盘'
}
const binanceEnvText = computed(() => envTextOf('binance', 'DEMO 沙盒'))
const gateEnvText = computed(() => envTextOf('gate', 'SANDBOX 沙盒'))

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'venues', label: '交易所与路由对等' },
  { key: 'pool', label: '交易标的池' },
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
            <span>活跃交易标的池</span>
            <Layers class="h-3 w-3" style="color: var(--accent);" />
          </div>
          <div class="mt-1 flex items-baseline justify-between">
            <b class="num text-xs font-bold" style="color: var(--ink-1);">{{ instruments.length }}/{{ instLimits.maximum }} 标的</b>
            <span class="text-[10px] font-medium" style="color: var(--ink-2);">USDT 永续</span>
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
            <button class="btn btn-primary" :disabled="savingMx" @click="saveRouting"><Save class="h-3.5 w-3.5" /> {{ savingMx ? '保存中…' : '保存路由策略' }}</button>
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
                  <div class="text-[10px]" style="color: var(--ink-3);">首选 Gate USDT 永续</div>
                </div>
              </label>
            </div>
            <p class="text-[11px] leading-relaxed" style="color: var(--ink-3);">
              当前生效：<b class="num" style="color: var(--accent);">{{ preferredVenue.toUpperCase() }}</b>。
              系统实行严密准入护栏：若所选交易所未配置密钥、未开闸或标的未上市，将自动平滑回退，并在决策日志留存证据。
            </p>
          </div>
        </SettingsSection>

        <!-- 三所对称凭证卡（同一外壳、同一槽位次序：环境 → 凭证 → 附加 → 检测/保存） -->
        <SettingsSection title="三所接入凭证与资金档位（对称配置）" description="OKX / Binance / Gate 凭证彼此独立保存，互不牵连；密钥仅在本机 Fernet 加密落盘，留空即不修改原有配置。">
          <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
            <!-- 1. OKX -->
            <VenueCredentialCard
              name="OKX · 欧易" api-label="V5 REST 直签"
              :status-text="okxLinked ? '已接入 READY' : '未就绪'" :tone="okxLinked ? 'up' : 'down'"
              :env-text="okxEnvText" env-label="当前生效资金环境"
            >
              <template #env>
                <label class="block text-[10px] mb-1" style="color: var(--ink-2);">资金环境档位</label>
                <select v-model="config.editable.okx_environment" class="input w-full text-xs">
                  <option value="demo">模拟盘 (DEMO)</option>
                  <option value="live">实盘 (LIVE)</option>
                </select>
              </template>
              <div class="space-y-1.5 pt-1">
                <div class="text-[10px] font-semibold" style="color: var(--ink-2);">实盘 (LIVE) 三件套</div>
                <input v-model="keys.live_key" type="password" placeholder="API Key（留空不改）" class="input w-full text-xs" />
                <input v-model="keys.live_secret" type="password" placeholder="Secret Key" class="input w-full text-xs" />
                <input v-model="keys.live_pass" type="password" placeholder="Passphrase" class="input w-full text-xs" />
              </div>
              <div class="space-y-1.5 pt-1">
                <div class="text-[10px] font-semibold" style="color: var(--ink-2);">模拟盘 (DEMO) 三件套</div>
                <input v-model="keys.demo_key" type="password" placeholder="API Key（留空不改）" class="input w-full text-xs" />
                <input v-model="keys.demo_secret" type="password" placeholder="Secret Key" class="input w-full text-xs" />
                <input v-model="keys.demo_pass" type="password" placeholder="Passphrase" class="input w-full text-xs" />
              </div>
              <template #extra>
                <p class="text-[10px] leading-relaxed pt-1" style="color: var(--ink-3);">
                  切换 LIVE 需二次确认；两档凭证同时保存，运行时按当前档位取用，禁止跨档读取。
                </p>
              </template>
              <template #probe>
                <button class="btn btn-quiet btn-sm" :disabled="probingVenue === 'okx'" @click="probeVenue('okx')"><RefreshCw class="h-3 w-3" :class="probingVenue === 'okx' ? 'animate-spin' : ''" /> {{ probingVenue === 'okx' ? '检测中…' : '检测' }}</button>
              </template>
              <template #save>
                <button class="btn btn-primary btn-sm" :disabled="savingOkx" @click="saveEnvironment"><Save class="h-3 w-3" /> {{ savingOkx ? '保存中…' : '保存 OKX' }}</button>
              </template>
            </VenueCredentialCard>

            <!-- 2. Binance -->
            <VenueCredentialCard
              name="Binance · 币安" api-label="USDT-M 永续合约"
              :status-text="binanceStatus.text" :tone="binanceStatus.tone"
              :env-text="binanceEnvText" env-label="当前生效资金环境"
            >
              <template #env>
                <label class="block text-[10px] mb-1" style="color: var(--ink-2);">端点网络档位</label>
                <label class="flex items-center gap-1.5 text-[11px] cursor-pointer" style="color: var(--ink-2);">
                  <input v-model="mxTestnet.binance" type="checkbox" class="accent-[var(--accent)]" />
                  使用官方 Demo 沙盒域 (demo-fapi)
                </label>
              </template>
              <div class="space-y-1.5 pt-1">
                <div class="text-[10px] font-semibold" style="color: var(--ink-2);">实盘 / 沙盒执行凭证（可选）</div>
                <input v-model="mxForm.binance_api_key" type="text" placeholder="API Key（留空不改）" class="input w-full text-xs" />
                <input v-model="mxForm.binance_secret_key" type="password" placeholder="API Secret" class="input w-full text-xs" />
              </div>
              <template #extra>
                <p class="text-[10px] leading-relaxed pt-1" style="color: var(--ink-3);">
                  公共行情、基差与资金费比对免密即可工作；配置密钥后账户面与执行面按档位直签。
                </p>
              </template>
              <template #probe>
                <button class="btn btn-quiet btn-sm" :disabled="probingVenue !== '' && probingVenue !== 'binance'" @click="probeVenue('binance')"><RefreshCw class="h-3 w-3" /> 检测</button>
              </template>
              <template #save>
                <button class="btn btn-primary btn-sm" :disabled="savingVenue !== ''" @click="saveVenue('binance')"><Save class="h-3 w-3" /> {{ savingVenue === 'binance' ? '保存中…' : '保存 Binance' }}</button>
              </template>
            </VenueCredentialCard>

            <!-- 3. Gate -->
            <VenueCredentialCard
              name="Gate.io · 芝麻" api-label="V4 USDT 永续合约"
              :status-text="gateStatus.text" :tone="gateStatus.tone"
              :env-text="gateEnvText" env-label="当前生效资金环境"
            >
              <template #env>
                <label class="block text-[10px] mb-1" style="color: var(--ink-2);">端点网络档位</label>
                <label class="flex items-center gap-1.5 text-[11px] cursor-pointer" style="color: var(--ink-2);">
                  <input v-model="mxTestnet.gate" type="checkbox" class="accent-[var(--accent)]" />
                  使用官方沙盒域 (fx-api-testnet)
                </label>
              </template>
              <div class="space-y-1.5 pt-1">
                <div class="text-[10px] font-semibold" style="color: var(--ink-2);">执行凭证（开启执行路由必需）</div>
                <input v-model="mxForm.gate_api_key" type="text" placeholder="API Key（留空不改）" class="input w-full text-xs" />
                <input v-model="mxForm.gate_secret_key" type="password" placeholder="API Secret" class="input w-full text-xs" />
              </div>
              <template #extra>
                <div class="pt-1">
                  <label class="flex items-center gap-1.5 text-[11px] cursor-pointer font-bold" :style="{ color: gateExec ? 'var(--down)' : 'var(--ink-2)' }">
                    <input v-model="gateExec" type="checkbox" class="accent-[var(--accent)]" />
                    Gate 执行路由总闸 {{ mx?.venues?.gate?.execution_open ? '(已开闸)' : '(关闸)' }}
                  </label>
                  <input v-if="gateExecDirty && gateExec" v-model="gateExecPhrase" placeholder="输入短语：OPEN GATE EXECUTION" class="input w-full text-xs mt-1.5" />
                </div>
                <p class="text-[10px] leading-relaxed pt-1" style="color: var(--ink-3);">
                  关闸状态下仅只读行情与账户探针；开闸后 Gate 才进入撮合路由候选集。
                </p>
              </template>
              <template #probe>
                <button class="btn btn-quiet btn-sm" :disabled="probingVenue !== '' && probingVenue !== 'gate'" @click="probeVenue('gate')"><RefreshCw class="h-3 w-3" /> 检测</button>
              </template>
              <template #save>
                <button class="btn btn-primary btn-sm" :disabled="savingVenue !== ''" @click="saveVenue('gate')"><Save class="h-3 w-3" /> {{ savingVenue === 'gate' ? '保存中…' : '保存 Gate' }}</button>
              </template>
            </VenueCredentialCard>
          </div>
        </SettingsSection>

        <!-- 跨所行情健康（只读探针，三所共用一行） -->
        <SettingsSection title="跨所行情健康容灾" description="公共行情每周期自动探活排序，零额外网络开销；任一所属行情劣化只降级该所候选资格，绝不阻塞其他所。">
          <template #actions>
            <button class="btn btn-quiet btn-sm" @click="loadMx"><RefreshCw class="h-3 w-3" /> 重新检测</button>
          </template>
          <div v-if="mxHealthChips" class="flex flex-wrap gap-2 text-[11px]">
            <span v-for="h in mxHealthChips" :key="h.name" class="px-2 py-1 rounded border font-bold num" :style="h.ok === h.total ? { color: 'var(--up)', borderColor: 'var(--up-line)', backgroundColor: 'var(--up-bg)' } : { color: 'var(--warn)', borderColor: 'var(--warn-line)', backgroundColor: 'var(--warn-bg)' }">
              {{ h.name }} {{ h.ok }}/{{ h.total }} 币{{ h.avg_ms ? ' · ' + h.avg_ms + 'ms' : '' }}{{ h.testnet ? ' · 沙盒' : '' }}
            </span>
          </div>
          <div v-else class="text-[11px]" style="color: var(--ink-3);">尚无周期数据，等待下个 15 分钟周期。</div>
        </SettingsSection>
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

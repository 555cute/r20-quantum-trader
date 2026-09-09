<script setup lang="ts">
/**
 * 账户与标的（/admin/security）——重做版 2026-09-09
 * 结构：状态总览条 + 四页签（账户接入 / 标的与基准 / 多交易所 / 应急平仓）。
 * 行为契约与旧版逐一对应（OAuth 设备码、CLI 安装短语、环境 LIVE 确认、
 * UPDATE CAPITAL、REMOVE <inst>、平仓双确认、多所凭证、Gate 执行开关
 * OPEN GATE EXECUTION）；端点与字段零改动。色彩全走 CSS 变量令牌。
 */
import { useToast } from '../../composables/useToast'
const toast = useToast()
import { ref, computed, onMounted } from 'vue'
import PageHeader from '../../components/admin/PageHeader.vue'
import SettingsSection from '../../components/admin/SettingsSection.vue'
import { useI18n } from '../../composables/useI18n'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import { Wallet, Save, KeyRound, RefreshCw, Layers, Trash2, Unlink, Terminal, FlaskConical, Zap } from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()
const { t } = useI18n()
const config = ref<any>(null)
const runtime = ref<any>(null)
const loading = ref(true)

type TabKey = 'okx' | 'pool' | 'venues' | 'emergency'
const activeTab = ref<TabKey>('okx')
const positionsLoadedOnce = ref(false)

function switchTab(tab: TabKey) {
  activeTab.value = tab
  if (tab === 'emergency' && !positionsLoadedOnce.value) {
    positionsLoadedOnce.value = true
    loadPositions()
  }
  if (tab === 'venues') loadLab()
}

// ---- OAuth ----
const oauthSite = ref('global')
const oauthState = ref('')
const oauthResult = ref<any>(null)
const startingOauth = ref(false)
const loggingOutOauth = ref(false)
const switchingAccount = ref(false)

// ---- CLI install ----
const cliCheck = ref<any>(null)
const installingCli = ref(false)

// ---- backup API keys ----
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

const sourceLabel: Record<string, string> = {
  'static-v5-key': '后台加密 API Key',
  'cli-oauth': 'OKX 官方 OAuth',
  'cli-api-key-profile': 'CLI Key Profile',
  none: '未就绪',
}

async function loadAll() {
  loading.value = true
  try {
    const [cfg, rt] = await Promise.all([api('/api/v1/admin/config'), api('/api/v1/admin/okx/runtime')])
    config.value = cfg
    applyRuntime(rt)
    newCapital.value = String(cfg.editable?.initial_capital ?? '')
    manualClose.value = !!cfg.editable?.manual_close_enabled
    oauthSite.value = rt?.oauth?.site || 'global'
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
    toast.ok('已重新诊断 OKX 连接与私有读取')
  } catch (e: any) {
    toast.err(`诊断失败：${e.message}`)
  }
}

async function startOauth() {
  startingOauth.value = true
  oauthState.value = '正在向 OKX 申请一次性授权码…'
  oauthResult.value = null
  try {
    const d = await api('/api/v1/admin/okx/oauth/start', { method: 'POST', body: JSON.stringify({ site: oauthSite.value }) })
    if (d.status === 'already_logged_in') {
      oauthState.value = ''
      oauthResult.value = { kind: 'logged_in', site: d.site, scopes: d.scopes || [] }
      await rediagnose()
    } else {
      oauthResult.value = { kind: 'device', ...d }
      oauthState.value = '请在 OKX 官方页面输入验证码完成授权'
    }
  } catch (e: any) {
    oauthState.value = ''
    oauthResult.value = { kind: 'error', message: e.message }
  } finally {
    startingOauth.value = false
  }
}

async function checkOauth() {
  try {
    const d = await api('/api/v1/admin/okx/oauth/status')
    if (d.status === 'logged_in') {
      oauthResult.value = { kind: 'logged_in', site: d.site, scopes: d.scopes || [] }
      toast.ok('OKX OAuth 授权成功')
      await rediagnose()
    } else if (d.status === 'pending') {
      toast.warn('授权尚未完成，请先在 OKX 页面确认')
    } else {
      oauthResult.value = { kind: 'error', message: `当前状态：${d.status}。${d.detail || '授权码可能已过期，请重新发起。'}` }
    }
  } catch (e: any) {
    toast.err(e.message)
  }
}

async function logoutOauth() {
  if (!confirm('确认解绑当前的 OKX OAuth 账户？解绑后可连接新账号。')) return
  loggingOutOauth.value = true
  try {
    const d = await api('/api/v1/admin/okx/oauth/logout', { method: 'POST' })
    toast.ok(d.message || 'OKX OAuth 账号已解绑')
    oauthResult.value = null
    oauthState.value = ''
    await rediagnose()
  } catch (e: any) {
    toast.err(`解绑失败：${e.message}`)
  } finally {
    loggingOutOauth.value = false
  }
}

async function switchOauthAccount() {
  if (!confirm('确认更换 OKX 账号？系统将解除当前授权并为您获取新的浏览器授权码。')) return
  switchingAccount.value = true
  oauthState.value = '正在切换并向 OKX 申请新的授权码…'
  oauthResult.value = null
  try {
    const d = await api('/api/v1/admin/okx/oauth/start', {
      method: 'POST',
      body: JSON.stringify({ site: oauthSite.value, force_relogin: true }),
    })
    oauthResult.value = { kind: 'device', ...d }
    oauthState.value = '请在 OKX 页面登录新账号并完成授权'
    await rediagnose()
  } catch (e: any) {
    oauthState.value = ''
    oauthResult.value = { kind: 'error', message: e.message }
  } finally {
    switchingAccount.value = false
  }
}

async function checkCli() {
  try {
    cliCheck.value = await api('/api/v1/admin/okx/cli-check')
  } catch (e: any) {
    toast.err(`CLI 检测失败：${e.message}`)
  }
}

async function installCli() {
  if (!cliCheck.value) {
    try { cliCheck.value = await api('/api/v1/admin/okx/cli-check') } catch { /* proceed with confirmation anyway */ }
  }
  const currentText = cliCheck.value?.okx_installed
    ? `当前已安装 ${cliCheck.value.okx_version || '未知版本'}（${cliCheck.value.okx_path || 'PATH 未知'}）。继续将执行安装校验或升级。`
    : '当前未检测到 OKX CLI，将执行首次安装。'
  const phrase = prompt(`一键安装 / 升级 OKX CLI\n${currentText}\n输入确认短语：INSTALL OKX CLI`)
  if (!phrase) return
  installingCli.value = true
  try {
    const d = await api('/api/v1/admin/okx/install-cli', { method: 'POST', body: JSON.stringify({ confirmation: phrase.trim().toUpperCase() }) })
    toast.ok(`OKX CLI 安装/校验成功：${d.path || ''} ${d.version || ''}`.trim())
    cliCheck.value = null
    await rediagnose()
  } catch (e: any) {
    toast.err(`CLI 安装失败：${e.message}`)
  } finally {
    installingCli.value = false
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
    const inst = await api('/api/v1/admin/instruments')
    instruments.value = inst.instruments || []
  } catch (e: any) {
    toast.err(`添加失败：${e.message}`)
  }
}

async function removeInstrument(item: any) {
  if (item.protected) { toast.err('BTC 为保底标的，不可删除'); return }
  if (item.has_tracker) { toast.err(`${item.name} 存在持仓追踪器，禁止移除`); return }
  const phrase = prompt(`删除交易池标的 ${item.instId}\n输入确认短语：REMOVE ${item.instId}`)
  if (!phrase) return
  try {
    const res = await api(`/api/v1/admin/instruments/${encodeURIComponent(item.instId)}`, { method: 'DELETE', body: JSON.stringify({ confirmation: phrase.trim().toUpperCase() }) })
    toast.ok(res.message || `${item.instId} 已从交易池移除`)
    const inst = await api('/api/v1/admin/instruments')
    instruments.value = inst.instruments || []
  } catch (e: any) {
    toast.err(`删除失败：${e.message}`)
  }
}

async function loadPositions() {
  snapshotState.value = '正在从 OKX 读取当前持仓与挂单…'
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

// ---- 多交易所数据源与凭证 (Binance / Gate) ----
const mx = ref<any>(null)
const mxForm = ref({ binance_api_key: '', binance_secret_key: '', gate_api_key: '', gate_secret_key: '' })
const mxTestnet = ref({ binance: false, gate: false })
const gateExec = ref(false)
const gateExecPhrase = ref('')
const savingMx = ref(false)

async function loadMx() {
  try {
    mx.value = await api('/api/v1/admin/multi-exchange')
    if (mx.value?.venues) {
      mxTestnet.value.binance = !!mx.value.venues.binance?.testnet
      mxTestnet.value.gate = !!mx.value.venues.gate?.testnet
      gateExec.value = !!mx.value.venues.gate?.execution_open
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
    const body: any = { binance_testnet: mxTestnet.value.binance, gate_testnet: mxTestnet.value.gate }
    if (gateExec.value !== !!mx.value?.venues?.gate?.execution_open) {
      body.gate_execution = gateExec.value
      body.confirmation = gateExecPhrase.value.trim()
    }
    for (const k of ['binance_api_key', 'binance_secret_key', 'gate_api_key', 'gate_secret_key']) {
      const v = (mxForm.value as any)[k]
      if (v && v.trim()) body[k] = v.trim()
    }
    await api('/api/v1/admin/multi-exchange', { method: 'PUT', body: JSON.stringify(body) })
    toast.ok('多交易所凭证与网络档位已保存')
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
const okxLinked = computed(() => runtime.value?.oauth?.status === 'logged_in' || runtime.value?.credential_source === 'static-v5-key' || runtime.value?.credential_source === 'cli-api-key-profile')
const runtimeTone = computed(() => {
  const rt = runtime.value
  if (!rt) return 'pending'
  if (rt.ready) return 'good'
  if (rt.degraded) return 'warn'
  return 'bad'
})
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
  { key: 'okx', label: '账户接入' },
  { key: 'pool', label: '标的与基准' },
  { key: 'venues', label: '多交易所' },
  { key: 'emergency', label: '应急平仓' },
]

function envBadge(env: string) {
  return (env || 'demo').toUpperCase()
}

/** OKX 上游业务故障（如模拟盘写接口 51001/503）的人话注解——只加提示不改语义 */
function errHint(msg: string): string {
  const m = String(msg || '')
  if (/51001|doesn'?t exist|Service temporarily|502|503/i.test(m)) {
    return m + '。这是 OKX 模拟盘接口当前异常（非后台问题）：只读数据不受影响，交易所侧已挂的止盈止损保护单仍然有效，请稍后重试或等 OKX 恢复。'
  }
  return m
}

onMounted(() => { loadAll(); loadMx() })
</script>

<template>
  <div class="space-y-4 text-xs">
    <PageHeader :title="t('nav.admin.security')" description="OKX 账号与环境、交易标的池、Binance / Gate 跨所数据源与 Gate 试验田的接入管理">
      <template #actions>
        <span v-if="runtime" class="chip">
          运行环境 <b class="num" :style="{ color: runtime.selected_mode === 'live' ? 'var(--down)' : 'var(--up)' }">{{ envBadge(runtime.selected_mode) }}</b>
          · 认证 <b>{{ sourceLabel[runtime.credential_source] || runtime.credential_source || '--' }}</b>
          · <b :style="{ color: runtimeTone === 'good' ? 'var(--up)' : runtimeTone === 'warn' ? 'var(--warn)' : 'var(--down)' }">{{ runtimeTone === 'good' ? '就绪' : runtimeTone === 'warn' ? '降级' : '未就绪' }}</b>
        </span>
      </template>
    </PageHeader>

    <div v-if="loading" class="py-12 text-center" style="color: var(--ink-2);">正在加载…</div>

    <template v-else-if="config">
      <!-- 状态总览条 -->
      <div class="flex flex-wrap gap-2">
        <span class="chip">
          OKX 连接
          <b :style="{ color: okxLinked ? 'var(--up)' : 'var(--down)' }">{{ okxLinked ? '已接入' : '未接入' }}</b>
          <template v-if="runtime?.oauth?.site"> · {{ runtime.oauth.site }}</template>
        </span>
        <span class="chip">初始本金 <b class="num" style="color: var(--up);">{{ config.editable.initial_capital }} U</b></span>
        <span class="chip">
          标的池 <b class="num">{{ instruments.length }}/{{ instLimits.maximum }}</b>
        </span>
        <span class="chip">
          跨所行情
          <template v-if="mxHealthChips">
            <b v-for="h in mxHealthChips" :key="h.name" class="num" :style="{ color: h.ok === h.total ? 'var(--up)' : 'var(--warn)', marginLeft: '4px' }">{{ h.name }} {{ h.ok }}/{{ h.total }}</b>
          </template>
          <b v-else style="color: var(--ink-3);">待周期</b>
        </span>
        <span class="chip">
          Gate 试验田
          <b v-if="lab" :style="{ color: lab.mode === 'live' ? 'var(--up)' : lab.mode === 'dry_run' ? 'var(--warn)' : 'var(--ink-3)' }">{{ lab.mode === 'live' ? 'LIVE 实单' : lab.mode === 'dry_run' ? 'DRY 演算' : 'OFF 停用' }}</b>
          <b v-else style="color: var(--ink-3);">--</b>
        </span>
      </div>

      <!-- 页签 -->
      <div class="flex items-center gap-1.5 border-b pb-2" style="border-color: var(--line-1);">
        <button
          v-for="tab in TABS" :key="tab.key"
          class="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer"
          :style="activeTab === tab.key ? { backgroundColor: 'var(--ink-1)', color: 'var(--surface-2)' } : { color: 'var(--ink-2)' }"
          @click="switchTab(tab.key)"
        >{{ tab.label }}</button>
      </div>

      <!-- ============ 页签 1：OKX 账户接入 ============ -->
      <div v-if="activeTab === 'okx'" class="space-y-4">
        <SettingsSection title="账号连接与运行诊断" description="OKX 官方 OAuth 授权（推荐）与运行链路诊断；无需向 R20 提供 OKX 密码或 2FA。">
          <template #actions>
            <button class="btn btn-quiet" @click="rediagnose"><RefreshCw class="h-3.5 w-3.5" /> 重新诊断</button>
          </template>

          <div v-if="runtime" class="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_340px] gap-4">
            <!-- 左：诊断明细 -->
            <div class="space-y-3">
              <div class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs leading-relaxed" style="color: var(--ink-2);">
                <div>当前环境 <b class="num" :style="{ color: runtime.selected_mode === 'live' ? 'var(--down)' : 'var(--up)', fontWeight: 700 }">{{ envBadge(runtime.selected_mode) }}</b></div>
                <div>认证来源 <b style="color: var(--accent);">{{ sourceLabel[runtime.credential_source] || runtime.credential_source || '--' }}</b></div>
                <div>CLI <span style="color: var(--ink-1);">{{ runtime.cli?.installed ? (runtime.cli.version || '已安装') : '未安装' }}</span></div>
                <div>OAuth <span style="color: var(--ink-1);">{{ runtime.oauth?.status }}{{ runtime.oauth?.site ? ' · ' + runtime.oauth.site : '' }}</span></div>
                <div>连接账号 <span style="color: var(--ink-1);">{{ runtime.oauth?.account_label || (runtime.oauth?.status === 'logged_in' ? '已连接（CLI 不返回昵称/UID）' : '--') }}</span></div>
                <div>权限 <span class="text-[11px]" style="color: var(--ink-3);">{{ (runtime.oauth?.scopes || []).join(', ') || '--' }}</span></div>
                <div class="col-span-2">只读探针 <span :style="{ color: runtime.read_probe?.ok ? 'var(--up)' : runtimeTone === 'warn' ? 'var(--warn)' : 'var(--down)' }">{{ runtime.read_probe?.detail || '--' }}</span></div>
                <div v-if="runtime.live_control_probe" class="col-span-2">LIVE 对照探针 <span :style="{ color: runtime.live_control_probe.ok ? 'var(--up)' : 'var(--down)' }">{{ runtime.live_control_probe.detail }}</span></div>
              </div>
              <div v-if="runtime.issues?.length" class="rounded-lg border p-2.5 space-y-0.5 text-[11px]" style="border-color: var(--down-line); background-color: var(--down-bg); color: var(--down);">
                <div v-for="(issue, i) in runtime.issues" :key="i">• {{ issue }}</div>
              </div>
              <div v-if="runtime.steps?.length" class="rounded-lg border p-2.5 space-y-0.5 text-[11px]" style="border-color: var(--line-1); background-color: var(--surface-1); color: var(--ink-2);">
                <div class="font-bold" style="color: var(--ink-1);">操作指引</div>
                <div v-for="(s, i) in runtime.steps" :key="i">• {{ s }}</div>
              </div>
              <div class="flex gap-2">
                <button class="btn btn-quiet" @click="checkCli"><Terminal class="h-3.5 w-3.5" /> 检测 Node / npm / CLI</button>
                <button v-if="auth.isSuperadmin" class="btn btn-quiet" :disabled="installingCli" @click="installCli">{{ installingCli ? '安装中…' : '安装 / 升级 CLI' }}</button>
              </div>
              <div v-if="cliCheck" class="rounded-lg border p-2.5 text-[11px] space-y-0.5" style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-2);">
                <div>Node.js：<span :style="{ color: cliCheck.node_installed ? 'var(--up)' : 'var(--down)' }">{{ cliCheck.node_installed ? `✓ ${cliCheck.node_version} (${cliCheck.node_path})` : '✗ 未安装' }}</span></div>
                <div>npm：<span :style="{ color: cliCheck.npm_installed ? 'var(--up)' : 'var(--down)' }">{{ cliCheck.npm_installed ? `✓ ${cliCheck.npm_version}` : '✗ 未安装' }}</span></div>
                <div>OKX CLI：<span :style="{ color: cliCheck.okx_installed ? 'var(--up)' : 'var(--down)' }">{{ cliCheck.okx_installed ? `✓ ${cliCheck.okx_version} (${cliCheck.okx_path})` : '✗ 未安装' }}</span></div>
              </div>
            </div>

            <!-- 右：OAuth 面板 -->
            <div class="rounded-lg border p-3.5 space-y-2" style="background-color: var(--surface-1); border-color: var(--line-1);">
              <div class="text-[11px] font-bold" style="color: var(--ink-1);">官方 OAuth 授权（推荐）</div>
              <label class="block text-[11px]" style="color: var(--ink-2);">OKX 站点</label>
              <select v-model="oauthSite" class="input w-full">
                <option value="global">Global · www.okx.com</option>
                <option value="eea">EEA · my.okx.com</option>
                <option value="us">US · app.okx.com</option>
                <option value="tr">TR · tr.okx.com</option>
              </select>
              <template v-if="runtime?.oauth?.status === 'logged_in'">
                <button v-if="auth.isSuperadmin" class="btn btn-primary w-full" :disabled="switchingAccount || loggingOutOauth" @click="switchOauthAccount" title="解除当前授权并重新在浏览器中连接新 OKX 账号">
                  <RefreshCw class="h-3.5 w-3.5" :class="switchingAccount ? 'animate-spin' : ''" /> {{ switchingAccount ? '切换中…' : '更换 OKX 账号' }}
                </button>
                <button v-if="auth.isSuperadmin" class="btn w-full" :disabled="loggingOutOauth || switchingAccount" style="color: var(--down); border: 1px solid var(--down-line); background: var(--surface-2);" @click="logoutOauth" title="解绑当前 OKX 账号并清除本地授权凭证">
                  <Unlink class="h-3.5 w-3.5" /> {{ loggingOutOauth ? '解绑中…' : '解绑账号' }}
                </button>
              </template>
              <button v-else-if="auth.isSuperadmin" class="btn btn-primary w-full" :disabled="startingOauth" @click="startOauth">
                <KeyRound class="h-3.5 w-3.5" /> {{ startingOauth ? '申请授权码中…' : '使用授权码连接 OKX' }}
              </button>
              <p v-if="!auth.isSuperadmin" class="text-[11px]" style="color: var(--ink-3);">仅超级管理员可管理账号连接。</p>
              <div v-if="oauthState" class="text-[11px]" style="color: var(--warn);">{{ oauthState }}</div>
              <div v-if="oauthResult?.kind === 'device'" class="rounded-lg border p-2.5 space-y-1.5" style="background-color: var(--accent-bg); border-color: var(--accent-line);">
                <div class="text-[11px] font-bold" style="color: var(--ink-1);">请在浏览器完成 OKX 官方授权</div>
                <div class="text-[11px] break-all"><a :href="oauthResult.verification_uri" target="_blank" rel="noopener" class="underline" style="color: var(--accent);">{{ oauthResult.verification_uri }}</a></div>
                <div class="text-center py-1.5 rounded border" style="background-color: var(--surface-2); border-color: var(--line-1);"><span class="text-lg font-semibold tracking-widest num" style="color: var(--ink-1);">{{ oauthResult.user_code }}</span></div>
                <div class="text-[11px]" style="color: var(--ink-2);">有效期约 {{ Math.ceil(Number(oauthResult.expires_in || 600) / 60) }} 分钟</div>
                <button class="btn btn-quiet w-full" @click="checkOauth">我已授权，检查状态</button>
              </div>
              <div v-else-if="oauthResult?.kind === 'logged_in'" class="rounded-lg border p-2.5 text-[11px]" style="background-color: var(--up-bg); border-color: var(--up-line); color: var(--up);">
                <div class="flex items-center justify-between"><span>已登录 · 站点 {{ oauthResult.site }}</span><span>已就绪</span></div>
                <div class="text-[11px] break-all mt-1" style="color: var(--ink-2);">{{ (oauthResult.scopes || []).join(', ') }}</div>
              </div>
              <div v-else-if="oauthResult?.kind === 'error'" class="rounded-lg border p-2.5 text-[11px]" style="background-color: var(--down-bg); border-color: var(--down-line); color: var(--down);">{{ oauthResult.message }}</div>
            </div>
          </div>
        </SettingsSection>

        <SettingsSection title="交易环境与备用凭证" description="模拟盘 / 实盘环境切换（切 LIVE 需确认短语）；备用 API Key 用于无人值守部署。">
          <template #actions>
            <button class="btn btn-primary" @click="saveEnvironment"><Save class="h-3.5 w-3.5" /> 保存环境与凭证</button>
          </template>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
            <div>
              <label class="block text-[11px] mb-1" style="color: var(--ink-2);">当前交易环境</label>
              <select v-model="config.editable.okx_environment" class="input w-full">
                <option value="demo">模拟盘 DEMO</option>
                <option value="live">实盘 LIVE</option>
              </select>
            </div>
            <div class="text-[11px] sm:col-span-2 pb-1" style="color: var(--ink-3);">
              环境切换即时生效于交易核心下一个周期；LIVE 切换须输入确认短语，并确保实盘 Key 权限与 IP 白名单已核对。
            </div>
          </div>
          <details class="mt-3">
            <summary class="cursor-pointer text-[11px] select-none" style="color: var(--accent);">备用方式：分别配置 LIVE / DEMO API Key（无人值守部署）</summary>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3 p-3 rounded-lg border" style="background-color: var(--surface-1); border-color: var(--line-1);">
              <div class="space-y-2">
                <div class="text-[11px] font-bold" style="color: var(--ink-1);">实盘 LIVE Key</div>
                <input v-model="keys.live_key" type="password" placeholder="API Key（留空保持现有）" class="input w-full" />
                <input v-model="keys.live_secret" type="password" placeholder="Secret Key" class="input w-full" />
                <input v-model="keys.live_pass" type="password" placeholder="Passphrase" class="input w-full" />
              </div>
              <div class="space-y-2">
                <div class="text-[11px] font-bold" style="color: var(--ink-1);">模拟盘 DEMO Key</div>
                <input v-model="keys.demo_key" type="password" placeholder="API Key（留空保持现有）" class="input w-full" />
                <input v-model="keys.demo_secret" type="password" placeholder="Secret Key" class="input w-full" />
                <input v-model="keys.demo_pass" type="password" placeholder="Passphrase" class="input w-full" />
              </div>
              <div class="sm:col-span-2 text-[11px]" style="color: var(--ink-3);">OAuth 与 API Key 二选一即可。不要为同一运行用户同时配置 CLI API Key Profile 和 OAuth。</div>
            </div>
          </details>
        </SettingsSection>
      </div>

      <!-- ============ 页签 2：标的与基准 ============ -->
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

        <SettingsSection title="主页盈亏基准 · 初始本金" description="用于主页累计盈亏与权益曲线的起算基准；修改不改变历史起算时间。">
          <template #actions>
            <span class="chip">当前基准 <b class="num" style="color: var(--up);">{{ config.editable.initial_capital }} USDT</b></span>
          </template>
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

      <!-- ============ 页签 3：多交易所 ============ -->
      <div v-if="activeTab === 'venues'" class="space-y-4">
        <SettingsSection title="行情容灾健康" description="币安 / Gate 公共端点按周期写入健康档案；跨所比对与备源排序自动使用，无需任何密钥。">
          <template #actions>
            <button class="btn btn-quiet" @click="loadMx"><RefreshCw class="h-3.5 w-3.5" /> 重新检测</button>
          </template>
          <div v-if="mxHealthChips" class="flex flex-wrap items-center gap-2 text-[11px]">
            <span style="color: var(--ink-3);">更新于 {{ mx?.health?.updated_utc || '--' }} UTC</span>
            <span v-for="h in mxHealthChips" :key="h.name" class="px-2 py-0.5 rounded border font-bold num" :style="h.ok === h.total ? { color: 'var(--up)', borderColor: 'var(--up-line)', backgroundColor: 'var(--up-bg)' } : { color: 'var(--warn)', borderColor: 'var(--warn-line)', backgroundColor: 'var(--warn-bg)' }">
              {{ h.name }} {{ h.ok }}/{{ h.total }} 币{{ h.avg_ms ? ' · ' + h.avg_ms + 'ms' : '' }}{{ h.testnet ? ' · 沙盒' : '' }}
            </span>
          </div>
          <div v-else class="text-[11px]" style="color: var(--ink-3);">尚无健康度数据——等待下一个 15 分钟决策周期自动写入。</div>
        </SettingsSection>

        <SettingsSection title="凭证与网络档位" description="密钥仅本机 Fernet 加密落盘；留空表示不修改。Gate 执行路由默认关闸——开闸需确认短语。">
          <template #actions>
            <button class="btn btn-primary" :disabled="savingMx" @click="saveMx"><Save class="h-3.5 w-3.5" /> {{ savingMx ? '保存中…' : '保存' }}</button>
          </template>
          <div class="grid sm:grid-cols-2 gap-4">
            <div class="space-y-2 rounded-lg border p-3" style="background-color: var(--surface-1); border-color: var(--line-1);">
              <div class="flex items-center justify-between">
                <h4 class="text-xs font-bold" style="color: var(--ink-1);">Binance · USDT-M</h4>
                <span v-if="mx?.venues?.binance?.has_api_key" class="text-[10px] px-1.5 py-0.5 rounded border" style="color: var(--up); border-color: var(--up-line);">凭证已配置</span>
                <span v-else class="text-[10px] px-1.5 py-0.5 rounded border" style="color: var(--ink-3); border-color: var(--line-2);">免登录行情就绪</span>
              </div>
              <input v-model="mxForm.binance_api_key" type="text" placeholder="API Key（可选，执行路由用）" class="input w-full" />
              <input v-model="mxForm.binance_secret_key" type="password" placeholder="API Secret（可选）" class="input w-full" />
              <label class="flex items-center gap-2 text-[11px] cursor-pointer" style="color: var(--ink-2);">
                <input v-model="mxTestnet.binance" type="checkbox" class="accent-[var(--accent)]" />
                官方 Demo 沙盒端点（demo-fapi）
              </label>
              <p class="text-[10px] leading-relaxed" style="color: var(--ink-3);">行情 / 比对 / 容灾无需密钥即生效；执行面待独立条件单双轨工程。</p>
            </div>
            <div class="space-y-2 rounded-lg border p-3" style="background-color: var(--surface-1); border-color: var(--line-1);">
              <div class="flex items-center justify-between">
                <h4 class="text-xs font-bold" style="color: var(--ink-1);">Gate.io · V4 永续</h4>
                <span v-if="mx?.venues?.gate?.has_api_key" class="text-[10px] px-1.5 py-0.5 rounded border" style="color: var(--up); border-color: var(--up-line);">凭证已配置</span>
                <span v-else class="text-[10px] px-1.5 py-0.5 rounded border" style="color: var(--ink-3); border-color: var(--line-2);">免登录行情就绪</span>
              </div>
              <input v-model="mxForm.gate_api_key" type="text" placeholder="API Key（试验田 live 必需）" class="input w-full" />
              <input v-model="mxForm.gate_secret_key" type="password" placeholder="API Secret（试验田 live 必需）" class="input w-full" />
              <label class="flex items-center gap-2 text-[11px] cursor-pointer" style="color: var(--ink-2);">
                <input v-model="mxTestnet.gate" type="checkbox" class="accent-[var(--accent)]" />
                官方永续沙盒端点（fx-api-testnet，实测偶发 502）
              </label>
              <label class="flex items-center gap-2 text-[11px] cursor-pointer font-bold" :style="{ color: gateExec ? 'var(--down)' : 'var(--ink-2)' }">
                <input v-model="gateExec" type="checkbox" class="accent-[var(--accent)]" />
                Gate 执行路由总闸 {{ mx?.venues?.gate?.execution_open ? '（当前：已开闸）' : '（当前：关闸）' }}
              </label>
              <input v-if="gateExecDirty && gateExec" v-model="gateExecPhrase" placeholder="输入确认短语：OPEN GATE EXECUTION" class="input w-full" />
              <p class="text-[10px] leading-relaxed" style="color: var(--ink-3);">开闸前须已录密钥且试验田币池预检通过；真金首笔建议 20U 最小单。</p>
            </div>
          </div>
        </SettingsSection>

        <SettingsSection title="Gate 试验田状态" description="验田模式、四道闸、在途仓位与主台账落账——纯本地只读，不触发任何交易所请求。">
          <template #actions>
            <button class="btn btn-quiet" @click="loadLab"><RefreshCw class="h-3.5 w-3.5" /> 刷新</button>
          </template>
          <template v-if="lab">
            <div class="flex flex-wrap items-center gap-2 text-[11px]">
              <span class="px-2 py-0.5 rounded border font-bold" :style="lab.mode === 'live' ? { color: 'var(--up)', borderColor: 'var(--up-line)', backgroundColor: 'var(--up-bg)' } : lab.mode === 'dry_run' ? { color: 'var(--warn)', borderColor: 'var(--warn-line)', backgroundColor: 'var(--warn-bg)' } : { color: 'var(--ink-3)', borderColor: 'var(--line-2)' }">
                {{ lab.mode === 'live' ? 'LIVE 实单' : lab.mode === 'dry_run' ? 'DRY 演算' : 'OFF 停用' }}
              </span>
              <span :style="{ color: lab.gates?.pool_nonempty ? 'var(--up)' : 'var(--down)' }">{{ lab.gates?.pool_nonempty ? '✓' : '✗' }} 币池 {{ (lab.pool?.assets || []).join('/') || '空' }}</span>
              <span :style="{ color: lab.gates?.execution_switch ? 'var(--up)' : 'var(--down)' }">{{ lab.gates?.execution_switch ? '✓' : '✗' }} 执行开关</span>
              <span :style="{ color: lab.gates?.credentials ? 'var(--up)' : 'var(--down)' }">{{ lab.gates?.credentials ? '✓' : '✗' }} 凭证</span>
              <span :style="{ color: lab.gates?.dry_run_off ? 'var(--up)' : 'var(--warn)' }">{{ lab.gates?.dry_run_off ? '✓' : '○' }} dry_run 关闭</span>
            </div>
            <div v-if="lab.error" class="mt-2 text-[11px]" style="color: var(--warn);">{{ lab.error }}</div>
            <div v-if="(lab.trackers || []).length" class="mt-3 overflow-x-auto">
              <table class="w-full text-[11px]" style="color: var(--ink-2);">
                <thead><tr class="text-left" style="color: var(--ink-3);">
                  <th class="py-1 pr-3 font-normal">币</th><th class="pr-3 font-normal">方向</th><th class="pr-3 font-normal">张数</th><th class="pr-3 font-normal">入场</th><th class="pr-3 font-normal">TP</th><th class="pr-3 font-normal">SL</th><th class="pr-3 font-normal">保证金U</th><th class="font-normal">模式</th>
                </tr></thead>
                <tbody>
                  <tr v-for="t in lab.trackers" :key="t.asset + (t.mode || '')" class="num" style="border-top: 1px solid var(--line-1);">
                    <td class="py-1 pr-3 font-bold" style="color: var(--ink-1);">{{ t.asset }}</td>
                    <td class="pr-3" :style="{ color: t.side === 'long' ? 'var(--up)' : 'var(--down)' }">{{ t.side === 'long' ? '多' : '空' }}</td>
                    <td class="pr-3">{{ t.contracts ?? t.size_signed ?? '--' }}</td>
                    <td class="pr-3">{{ t.entry_px ?? '--' }}</td>
                    <td class="pr-3">{{ t.tp_px ?? '--' }}</td>
                    <td class="pr-3">{{ t.sl_px ?? '--' }}</td>
                    <td class="pr-3">{{ t.margin_usdt ?? '--' }}</td>
                    <td>{{ t.mode === 'live' ? '实单' : '演算' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-else class="mt-2 text-[11px]" style="color: var(--ink-3);">试验田当前无在途仓位。</div>
            <div v-if="(lab.ledger_tail || []).length" class="mt-2 text-[10px] space-y-0.5" style="color: var(--ink-3);">
              <div v-for="(l, i) in lab.ledger_tail" :key="i"><FlaskConical class="inline h-3 w-3 mr-0.5" />落账 · {{ l.asset }} {{ l.reason || 'closed' }} · 入场 {{ l.entry_px ?? '--' }} × {{ l.contracts ?? '--' }} 张 · {{ l.close_ts ? new Date(l.close_ts * 1000).toLocaleString('zh-CN', { hour12: false }) : '' }}</div>
            </div>
          </template>
          <div v-else class="text-[11px]" style="color: var(--ink-3);">状态接口未就绪——随下次后端重启生效。</div>
        </SettingsSection>
      </div>

      <!-- ============ 页签 4：应急平仓 ============ -->
      <div v-if="activeTab === 'emergency'" class="space-y-4">
        <SettingsSection title="后台手动平仓" description="应急通道：默认禁用。启用后下方持仓表出现「快速平仓」，平仓仍需管理员密码 + 一次性令牌 + 确认短语三重确认。">
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

        <SettingsSection title="当前持仓与应急平仓" description="从 OKX 读取实时持仓与挂单快照（只读探针）；平仓流程：复核仓位 → 撤销冲突委托 → autoCxl 市价平仓 → 轮询确认归零。">
          <template #actions>
            <button class="btn btn-quiet" @click="loadPositions"><Zap class="h-3.5 w-3.5" /> 刷新持仓与挂单</button>
          </template>
          <div v-if="snapshotState" class="text-[11px] pb-2" style="color: var(--warn);">{{ snapshotState }}</div>
          <div v-if="snapshot" class="text-[11px] pb-2" style="color: var(--ink-2);">
            环境 <b :style="{ color: snapshot.environment === 'live' ? 'var(--down)' : 'var(--up)' }">{{ envBadge(snapshot.environment) }}</b>
            · 持仓 {{ snapshot.positions?.length ?? 0 }} · 挂单 {{ snapshot.orders?.length ?? 0 }} · {{ new Date(snapshot.captured_at_ms).toLocaleString('sv-SE') }}
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
            <div v-else-if="!snapshotState" class="py-8 text-center text-xs" style="color: var(--ink-3);">点击「刷新持仓与挂单」从 OKX 读取最新实时状态。</div>
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

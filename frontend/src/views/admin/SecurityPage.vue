<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import { ShieldAlert, Wallet, Save, Terminal, KeyRound, RefreshCw, Layers, Trash2, X } from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()
const config = ref<any>(null)
const runtime = ref<any>(null)
const loading = ref(true)
const bannerMsg = ref<{ text: string; type: 'ok' | 'err' | 'warn' } | null>(null)

// ---- OAuth ----
const oauthSite = ref('global')
const oauthState = ref('')
const oauthResult = ref<any>(null)
const startingOauth = ref(false)

// ---- CLI install ----
const cliCheck = ref<any>(null)
const installingCli = ref(false)

// ---- active exchange tab ----
const activeExchangeTab = ref<'okx' | 'binance' | 'gate'>('okx')
const primaryExchange = ref<string>('okx')
const binanceTestnet = ref<boolean>(true)
const gateTestnet = ref<boolean>(true)

// ---- backup API keys ----
const keysOpen = ref(false)
const keys = ref({
  live_key: '', live_secret: '', live_pass: '',
  demo_key: '', demo_secret: '', demo_pass: '',
  binance_key: '', binance_secret: '',
  gate_key: '', gate_secret: ''
})

// ---- capital ----
const newCapital = ref<string>('')
const capitalConfirm = ref<string>('')
const savingCapital = ref(false)

// ---- instruments ----
const instruments = ref<any[]>([])
const instLimits = ref<any>({ minimum: 1, maximum: 6 })
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
  'cli-oauth': 'OKX 官方 OAuth 授权码',
  'cli-api-key-profile': 'OKX CLI API Key Profile',
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
    primaryExchange.value = cfg.editable?.primary_exchange || 'okx'
    binanceTestnet.value = cfg.editable?.binance_testnet !== false
    gateTestnet.value = cfg.editable?.gate_testnet !== false
    oauthSite.value = rt?.oauth?.site || 'global'
    const inst = await api('/api/v1/admin/instruments')
    instruments.value = inst.instruments || []
    instLimits.value = inst.limits || instLimits.value
  } catch (e: any) {
    bannerMsg.value = { text: `加载失败：${e.message}`, type: 'err' }
  } finally {
    loading.value = false
  }
}

function applyRuntime(rt: any) {
  runtime.value = rt
}

async function rediagnose() {
  bannerMsg.value = { text: '正在检查 OKX CLI、OAuth 与私有读取…', type: 'warn' }
  try {
    applyRuntime(await api('/api/v1/admin/okx/runtime?refresh=1'))
    bannerMsg.value = null
  } catch (e: any) {
    bannerMsg.value = { text: `诊断失败：${e.message}`, type: 'err' }
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
      bannerMsg.value = { text: '✅ OKX OAuth 授权成功', type: 'ok' }
      await rediagnose()
    } else if (d.status === 'pending') {
      bannerMsg.value = { text: '授权尚未完成，请先在 OKX 页面确认', type: 'warn' }
    } else {
      oauthResult.value = { kind: 'error', message: `当前状态：${d.status}。${d.detail || '授权码可能已过期，请重新发起。'}` }
    }
  } catch (e: any) {
    bannerMsg.value = { text: e.message, type: 'err' }
  }
}

async function checkCli() {
  try {
    cliCheck.value = await api('/api/v1/admin/okx/cli-check')
  } catch (e: any) {
    bannerMsg.value = { text: `CLI 检测失败：${e.message}`, type: 'err' }
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
    bannerMsg.value = { text: `✅ OKX CLI 安装/校验成功：${d.path || ''} ${d.version || ''}`.trim(), type: 'ok' }
    cliCheck.value = null
    await rediagnose()
  } catch (e: any) {
    bannerMsg.value = { text: `CLI 安装失败：${e.message}`, type: 'err' }
  } finally {
    installingCli.value = false
  }
}

async function saveEnvironment() {
  const environment = config.value.editable.okx_environment
  if (environment === 'live') {
    const approved = prompt('切换到 LIVE 实盘环境\n输入 LIVE 确认已核对实盘 Key 权限与 IP 白名单')
    if (approved?.trim().toUpperCase() !== 'LIVE') {
      bannerMsg.value = { text: '未输入 LIVE，环境未切换', type: 'warn' }
      return
    }
  }
  try {
    const body: any = {
      okx_environment: environment,
      primary_exchange: primaryExchange.value,
      binance_testnet: binanceTestnet.value,
      gate_testnet: gateTestnet.value
    }
    if (keys.value.live_key) body.okx_live_api_key = keys.value.live_key
    if (keys.value.live_secret) body.okx_live_secret_key = keys.value.live_secret
    if (keys.value.live_pass) body.okx_live_passphrase = keys.value.live_pass
    if (keys.value.demo_key) body.okx_demo_api_key = keys.value.demo_key
    if (keys.value.demo_secret) body.okx_demo_secret_key = keys.value.demo_secret
    if (keys.value.demo_pass) body.okx_demo_passphrase = keys.value.demo_pass
    if (keys.value.binance_key) body.binance_api_key = keys.value.binance_key
    if (keys.value.binance_secret) body.binance_secret_key = keys.value.binance_secret
    if (keys.value.gate_key) body.gate_api_key = keys.value.gate_key
    if (keys.value.gate_secret) body.gate_secret_key = keys.value.gate_secret
    await api('/api/v1/admin/config', { method: 'PUT', body: JSON.stringify(body) })
    keys.value = {
      live_key: '', live_secret: '', live_pass: '',
      demo_key: '', demo_secret: '', demo_pass: '',
      binance_key: '', binance_secret: '',
      gate_key: '', gate_secret: ''
    }
    bannerMsg.value = { text: `交易所连接与凭证已安全保存`, type: 'ok' }
    await loadAll()
  } catch (e: any) {
    bannerMsg.value = { text: `保存失败：${e.message}`, type: 'err' }
  }
}

async function saveManualClose() {
  try {
    const d = await api('/api/v1/admin/config', { method: 'PUT', body: JSON.stringify({ manual_close_enabled: manualClose.value }) })
    manualClose.value = !!d.manual_close_enabled
    bannerMsg.value = { text: manualClose.value ? '⚠ 后台手动平仓已启用' : '后台手动平仓已禁用', type: manualClose.value ? 'warn' : 'ok' }
  } catch (e: any) {
    bannerMsg.value = { text: e.message, type: 'err' }
  }
}

async function saveCapital() {
  if (!auth.isSuperadmin) { bannerMsg.value = { text: '仅超级管理员可修改初始本金', type: 'err' }; return }
  if (capitalConfirm.value.trim().toUpperCase() !== 'UPDATE CAPITAL') { bannerMsg.value = { text: '确认短语必须精确为：UPDATE CAPITAL', type: 'err' }; return }
  savingCapital.value = true
  try {
    const res = await api('/api/v1/admin/account-baseline', { method: 'PUT', body: JSON.stringify({ initial_capital: parseFloat(newCapital.value), confirmation: capitalConfirm.value }) })
    bannerMsg.value = { text: res.effect || `初始本金已调整为 ${res.initial_capital} USDT`, type: 'ok' }
    capitalConfirm.value = ''
    await loadAll()
  } catch (e: any) {
    bannerMsg.value = { text: `更新失败：${e.message}`, type: 'err' }
  } finally {
    savingCapital.value = false
  }
}

async function addInstrument() {
  const instId = newInstId.value.trim().toUpperCase()
  if (!/^[A-Z0-9]{2,15}-USDT-SWAP$/.test(instId)) { bannerMsg.value = { text: '格式示例：XRP-USDT-SWAP（仅 USDT 永续）', type: 'err' }; return }
  try {
    const res = await api('/api/v1/admin/instruments', { method: 'POST', body: JSON.stringify({ inst_id: instId }) })
    bannerMsg.value = { text: res.message || `${instId} 已成功加入交易池并实时同步全网大屏与因果雷达`, type: 'ok' }
    newInstId.value = ''
    const inst = await api('/api/v1/admin/instruments')
    instruments.value = inst.instruments || []
  } catch (e: any) {
    bannerMsg.value = { text: `添加失败：${e.message}`, type: 'err' }
  }
}

async function removeInstrument(item: any) {
  if (item.protected) { bannerMsg.value = { text: 'BTC 为保底标的，不可删除', type: 'err' }; return }
  if (item.has_tracker) { bannerMsg.value = { text: `${item.name} 存在持仓追踪器，禁止移除`, type: 'err' }; return }
  const phrase = prompt(`删除交易池标的 ${item.instId}\n输入确认短语：REMOVE ${item.instId}`)
  if (!phrase) return
  try {
    const res = await api(`/api/v1/admin/instruments/${encodeURIComponent(item.instId)}`, { method: 'DELETE', body: JSON.stringify({ confirmation: phrase.trim().toUpperCase() }) })
    bannerMsg.value = { text: res.message || `${item.instId} 已从交易池移除并实时同步全网大屏与因果雷达`, type: 'ok' }
    const inst = await api('/api/v1/admin/instruments')
    instruments.value = inst.instruments || []
  } catch (e: any) {
    bannerMsg.value = { text: `删除失败：${e.message}`, type: 'err' }
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
  if (!manualClose.value) { bannerMsg.value = { text: '请先启用后台手动平仓并保存开关', type: 'err' }; return }
  closePhraseInput.value = ''
  closeModal.value = { show: true, pos }
}

async function confirmClose() {
  const pos = closeModal.value?.pos
  if (!pos) return
  if (!closePassword.value) { bannerMsg.value = { text: '请输入当前管理员密码', type: 'err' }; return }
  if (!pos.close_token || !pos.close_confirmation) { bannerMsg.value = { text: '平仓令牌缺失，请刷新当前持仓', type: 'err' }; return }
  if (closePhraseInput.value.trim().toUpperCase() !== pos.close_confirmation) {
    bannerMsg.value = { text: `确认短语必须精确为：${pos.close_confirmation}`, type: 'err' }
    return
  }
  closing.value = true
  try {
    const d = await api('/api/v1/admin/positions/close', {
      method: 'POST',
      body: JSON.stringify({ close_token: pos.close_token, admin_password: closePassword.value, confirmation: closePhraseInput.value.trim().toUpperCase() }),
    })
    bannerMsg.value = { text: `✅ 已确认平仓：${d.instId} ${d.closed_size}`, type: 'ok' }
    closeModal.value = null
    closePassword.value = ''
    await loadPositions()
  } catch (e: any) {
    bannerMsg.value = { text: `平仓失败：${e.message}`, type: 'err' }
  } finally {
    closing.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="space-y-4 font-mono text-xs">
    <!-- Header & Action Bar -->
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center space-x-2.5">
        <div class="w-8 h-8 rounded-lg flex items-center justify-center border shadow-xs" style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);">
          <Wallet class="w-4 h-4" />
        </div>
        <div>
          <h1 class="text-sm font-bold uppercase tracking-wide" style="color: var(--text-main);">多交易所连接与交易标的池</h1>
          <p class="text-[11px] font-sans" style="color: var(--text-muted);">
            OKX / Binance / Gate 多所行情聚合与凭证管理、实盘/模拟盘环境切换、初始本金基准、资产标的池管理与应急持仓处置。
          </p>
        </div>
      </div>
      <span class="text-[10px] px-2 py-1 rounded border font-bold" style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);">
        交易核心底座 · 2/4
      </span>
    </div>

    <div v-if="bannerMsg" class="sticky top-[76px] z-40 p-3 rounded-lg text-xs font-mono flex items-center gap-2 border shadow-lg" :class="bannerMsg.type === 'ok' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : bannerMsg.type === 'warn' ? 'bg-amber-500/10 border-amber-500/20 text-amber-400' : 'bg-rose-500/10 border-rose-500/20 text-rose-400'">
      <span>{{ bannerMsg.text }}</span>
      <button @click="bannerMsg = null" class="ml-auto cursor-pointer"><X class="w-3.5 h-3.5" /></button>
    </div>

    <div v-if="loading" class="py-12 text-center text-xs font-mono" style="color: var(--text-muted);">正在加载...</div>

    <template v-else-if="config">
      <!-- 1. OKX / Binance / Gate Multi-Exchange Workspace -->
      <div class="rounded-xl border p-4 sm:p-5 space-y-4 shadow-xs transition-colors" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="flex flex-wrap items-center justify-between pb-3 border-b gap-2" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <ShieldAlert class="w-4 h-4" style="color: var(--color-brand);" />
            <h2 class="text-sm font-bold font-mono" style="color: var(--text-main);">1. 多交易所环境、凭证与执行路由工作台</h2>
          </div>
          
          <!-- Global Target Exchange Selector -->
          <div class="flex items-center gap-2">
            <span class="text-[11px] font-mono" style="color: var(--text-muted);">全局执行路由目标:</span>
            <select v-model="primaryExchange" class="rounded-lg px-2.5 py-1 text-xs font-mono font-bold outline-none border cursor-pointer" style="background-color: var(--bg-input); border-color: var(--border-medium); color: var(--color-brand);">
              <option value="okx">OKX (官方原生实盘/模拟盘)</option>
              <option value="binance">Binance 币安 (USDT-M 合约)</option>
              <option value="gate">Gate.io 芝麻开门 (Futures V4)</option>
            </select>
          </div>
        </div>

        <!-- Exchange Tabs Switcher -->
        <div class="flex items-center gap-2 border-b pb-2" style="border-color: var(--border-subtle);">
          <button
            @click="activeExchangeTab = 'okx'"
            class="px-3.5 py-1.5 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer flex items-center gap-1.5"
            :style="activeExchangeTab === 'okx'
              ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
              : { backgroundColor: 'var(--bg-card-subtle)', color: 'var(--text-muted)' }"
          >
            <span>OKX 原生矩阵</span>
            <span v-if="runtime" class="text-[10px] px-1.5 py-0.2 rounded-full border" :class="runtime.ready ? 'border-emerald-500/40 text-emerald-400' : 'border-amber-500/40 text-amber-400'">
              {{ (runtime.selected_mode || 'demo').toUpperCase() }}
            </span>
          </button>

          <button
            @click="activeExchangeTab = 'binance'"
            class="px-3.5 py-1.5 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer flex items-center gap-1.5"
            :style="activeExchangeTab === 'binance'
              ? { backgroundColor: '#f3ba2f', color: '#000000' }
              : { backgroundColor: 'var(--bg-card-subtle)', color: 'var(--text-muted)' }"
          >
            <span>Binance 币安</span>
            <span class="text-[10px] px-1.5 py-0.2 rounded-full border border-current opacity-80">
              {{ config.editable.binance_configured ? '交易就绪' : '免登行情' }}
            </span>
          </button>

          <button
            @click="activeExchangeTab = 'gate'"
            class="px-3.5 py-1.5 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer flex items-center gap-1.5"
            :style="activeExchangeTab === 'gate'
              ? { backgroundColor: '#2354ff', color: '#ffffff' }
              : { backgroundColor: 'var(--bg-card-subtle)', color: 'var(--text-muted)' }"
          >
            <span>Gate.io 芝麻开门</span>
            <span class="text-[10px] px-1.5 py-0.2 rounded-full border border-current opacity-80">
              {{ config.editable.gate_configured ? '交易就绪' : '免登行情' }}
            </span>
          </button>
        </div>

        <!-- TAB 1: OKX PANEL -->
        <div v-if="activeExchangeTab === 'okx'" class="space-y-4">
          <div v-if="runtime" class="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px] gap-4">
            <!-- runtime detail -->
            <div>
              <div class="text-xs font-mono leading-relaxed space-y-1" style="color: var(--text-muted);">
                <div>OKX 环境：<strong style="color: var(--text-main);">{{ (runtime.selected_mode || 'demo').toUpperCase() }}</strong></div>
                <div>CLI：<span style="color: var(--text-main);">{{ runtime.cli?.installed ? (runtime.cli.version || '已安装') : '未安装' }} · {{ runtime.cli?.path || 'PATH 中不可见' }}</span></div>
                <div>认证来源：<span style="color: var(--color-brand);">{{ sourceLabel[runtime.credential_source] || runtime.credential_source }}</span></div>
                <div>连接账号：<span style="color: var(--text-main);">{{ runtime.oauth?.account_label || (runtime.oauth?.status === 'logged_in' ? 'OAuth 已连接' : '--') }}</span></div>
                <div>OAuth：<span style="color: var(--text-main);">{{ runtime.oauth?.status }}{{ runtime.oauth?.site ? ' · ' + runtime.oauth.site : '' }}</span></div>
                <div class="text-[10px]" style="color: var(--text-faint);">权限：{{ (runtime.oauth?.scopes || []).join(', ') || '--' }}</div>
                <div>只读探针：<span :class="runtime.read_probe?.ok ? 'text-emerald-500' : runtime.degraded ? 'text-amber-500' : 'text-rose-500'">{{ runtime.read_probe?.detail || '--' }}</span></div>
                <div v-if="runtime.live_control_probe" class="text-[10px]">LIVE 对照探针：<span :class="runtime.live_control_probe.ok ? 'text-emerald-500' : 'text-rose-500'">{{ runtime.live_control_probe.detail }}</span></div>
                <div v-if="runtime.issues?.length" class="mt-2 text-[11px]" :class="runtime.degraded ? 'text-amber-500' : 'text-rose-500'">
                  <div v-for="(issue, i) in runtime.issues" :key="i">• {{ issue }}</div>
                </div>
              </div>
              <div class="flex gap-2 mt-3">
                <button @click="rediagnose" class="flex items-center space-x-1 px-3 py-1.5 rounded-lg border text-xs font-mono cursor-pointer transition-all shadow-xs" style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);"><RefreshCw class="w-3.5 h-3.5" /><span>重新诊断</span></button>
                <button @click="checkCli" class="px-3 py-1.5 rounded-lg border text-xs font-mono cursor-pointer transition-all shadow-xs" style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);">检测 Node/npm/CLI</button>
              </div>
            </div>

            <!-- OAuth panel -->
            <div class="rounded-lg p-3.5 border shadow-xs" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
              <div class="text-[11px] font-bold font-mono mb-2" style="color: var(--text-main);">官方 OAuth 授权（推荐）</div>
              <div class="text-[10px] font-mono mb-2 leading-relaxed" style="color: var(--text-muted);">授权码登录，无需向系统提供明文 API Key 或 2FA。</div>
              <label class="block text-[10px] mb-1 font-mono" style="color: var(--text-muted);">OKX 站点</label>
              <select v-model="oauthSite" class="w-full rounded-lg px-2 py-1.5 text-xs font-mono outline-none border mb-2" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);">
                <option value="global">Global · www.okx.com</option>
                <option value="eea">EEA · my.okx.com</option>
                <option value="us">US · app.okx.com</option>
                <option value="tr">TR · tr.okx.com</option>
              </select>
              <div class="flex gap-2">
                <button
                  v-if="auth.isSuperadmin"
                  @click="startOauth"
                  :disabled="startingOauth"
                  class="flex-1 flex items-center justify-center space-x-1.5 px-3 py-2 rounded-lg text-xs font-mono font-bold cursor-pointer disabled:opacity-50 transition-all shadow-xs bg-blue-600 hover:bg-blue-500 text-white"
                  style="background-color: #2563EB; color: #FFFFFF;"
                >
                  <KeyRound class="w-3.5 h-3.5" />
                  <span>{{ startingOauth ? '申请授权码中…' : '使用授权码连接 OKX' }}</span>
                </button>
                <button
                  v-if="auth.isSuperadmin"
                  @click="installCli"
                  :disabled="installingCli"
                  class="px-2.5 py-2 rounded-lg border text-[11px] font-mono cursor-pointer disabled:opacity-50 transition-all shadow-xs"
                  style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-main);"
                >
                  {{ installingCli ? '安装中…' : '安装/升级 CLI' }}
                </button>
              </div>

              <div v-if="oauthState" class="mt-2 text-[11px] font-mono text-amber-500">{{ oauthState }}</div>

              <div v-if="oauthResult?.kind === 'device'" class="mt-2 p-2.5 rounded-lg border space-y-1.5" style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border);">
                <div class="text-[10px] font-bold font-mono" style="color: var(--text-main);">请在浏览器完成 OKX 官方授权</div>
                <div class="text-[10px] font-mono break-all"><a :href="oauthResult.verification_uri" target="_blank" rel="noopener" class="underline" style="color: var(--color-brand);">{{ oauthResult.verification_uri }}</a></div>
                <div class="text-center py-1.5 rounded border" style="background-color: var(--bg-card); border-color: var(--border-subtle);"><span class="text-lg font-black font-mono tracking-widest" style="color: var(--text-main);">{{ oauthResult.user_code }}</span></div>
                <button @click="checkOauth" class="w-full px-2 py-1.5 rounded-lg border text-[11px] font-mono cursor-pointer transition-all shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-main);">我已授权，检查状态</button>
              </div>
              <div v-else-if="oauthResult?.kind === 'logged_in'" class="mt-2 p-2.5 rounded-lg border text-[11px] font-mono text-emerald-500" style="background-color: var(--color-up-bg); border-color: var(--color-up-border);">
                ✅ 当前已经登录 · 站点 {{ oauthResult.site }}
              </div>
            </div>
          </div>

          <!-- OKX Environment & API Keys -->
          <div class="pt-3 border-t" style="border-color: var(--border-subtle);">
            <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
              <div>
                <label class="block text-[11px] mb-1 font-mono" style="color: var(--text-muted);">OKX 交易环境</label>
                <select v-model="config.editable.okx_environment" class="w-full rounded-lg px-3 py-2 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);">
                  <option value="demo">模拟盘 DEMO</option>
                  <option value="live">实盘 LIVE</option>
                </select>
              </div>
              <div class="flex items-end pb-1">
                <label class="flex items-center space-x-2 cursor-pointer">
                  <input type="checkbox" v-model="manualClose" class="accent-blue-500" />
                  <span class="text-xs font-mono" style="color: var(--text-muted);">允许后台手动平仓</span>
                </label>
              </div>
              <div class="flex gap-2">
                <button @click="saveEnvironment" class="flex-1 flex items-center justify-center space-x-1 px-3 py-2 rounded-lg text-xs font-mono font-bold cursor-pointer transition-all shadow-xs" style="background-color: var(--text-main); color: var(--bg-card);"><Save class="w-3.5 h-3.5" /><span>保存设置与凭证</span></button>
                <button @click="saveManualClose" class="px-3 py-2 rounded-lg border text-xs font-mono cursor-pointer transition-all shadow-xs" style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);">保存平仓开关</button>
              </div>
            </div>

            <details class="mt-3">
              <summary class="cursor-pointer text-[11px] font-mono select-none" style="color: var(--color-brand);">备用 API Key 授权（分别配置 LIVE / DEMO Key）</summary>
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3 p-3 rounded-lg border shadow-xs" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
                <div class="space-y-2">
                  <div class="text-[10px] font-bold font-mono" style="color: var(--text-main);">实盘 LIVE Key</div>
                  <input v-model="keys.live_key" type="password" placeholder="API Key（留空保持现有）" class="w-full rounded-lg px-2.5 py-1.5 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
                  <input v-model="keys.live_secret" type="password" placeholder="Secret Key" class="w-full rounded-lg px-2.5 py-1.5 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
                  <input v-model="keys.live_pass" type="password" placeholder="Passphrase" class="w-full rounded-lg px-2.5 py-1.5 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
                </div>
                <div class="space-y-2">
                  <div class="text-[10px] font-bold font-mono" style="color: var(--text-main);">模拟盘 DEMO Key</div>
                  <input v-model="keys.demo_key" type="password" placeholder="API Key（留空保持现有）" class="w-full rounded-lg px-2.5 py-1.5 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
                  <input v-model="keys.demo_secret" type="password" placeholder="Secret Key" class="w-full rounded-lg px-2.5 py-1.5 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
                  <input v-model="keys.demo_pass" type="password" placeholder="Passphrase" class="w-full rounded-lg px-2.5 py-1.5 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
                </div>
              </div>
            </details>
          </div>
        </div>

        <!-- TAB 2: BINANCE PANEL -->
        <div v-else-if="activeExchangeTab === 'binance'" class="space-y-4">
          <div class="p-4 rounded-xl border space-y-3" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2">
                <span class="w-2.5 h-2.5 rounded-full bg-amber-400"></span>
                <h3 class="text-xs font-bold font-mono text-amber-400">Binance 币安 USDT-M 合约交易与行情网关</h3>
              </div>
              <span class="text-[10px] font-mono px-2 py-0.5 rounded border" :class="config.editable.binance_configured ? 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10' : 'text-amber-400 border-amber-500/30 bg-amber-500/10'">
                {{ config.editable.binance_configured ? '● 私有交易凭证已就绪' : '○ 免登录全要素行情激活' }}
              </span>
            </div>
            
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs font-mono" style="color: var(--text-muted);">
              <div class="p-3 rounded-lg border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
                <div class="text-[10px] font-bold text-slate-400 mb-1">行情与动力学源</div>
                <div class="text-emerald-400 font-bold">免登录 Direct REST</div>
                <div class="text-[10px] mt-1" style="color: var(--text-faint);">15M/1H/4H K线 + 盘口深度 + 散度计算</div>
              </div>
              <div class="p-3 rounded-lg border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
                <div class="text-[10px] font-bold text-slate-400 mb-1">大户多空比数据源</div>
                <div class="text-indigo-400 font-bold">TopTrader L/S Ratio</div>
                <div class="text-[10px] mt-1" style="color: var(--text-faint);">实时注入 AI 交易大脑质询证据</div>
              </div>
              <div class="p-3 rounded-lg border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
                <div class="text-[10px] font-bold text-slate-400 mb-1">执行路由机制</div>
                <div class="text-amber-400 font-bold">ExecutionRouter 适配</div>
                <div class="text-[10px] mt-1" style="color: var(--text-faint);">精度换算 + 自动 STOP_MARKET OCO 挂载</div>
              </div>
            </div>

            <!-- Binance Credentials Inputs -->
            <div class="pt-3 border-t space-y-3" style="border-color: var(--border-subtle);">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="text-[11px] font-bold font-mono" style="color: var(--text-main);">币安 API 凭证与运行模式配置</div>
                <div class="flex items-center gap-2">
                  <label class="text-[11px] font-mono" style="color: var(--text-muted);">交易环境模式:</label>
                  <select v-model="binanceTestnet" class="rounded px-2 py-0.5 text-[11px] font-mono font-bold outline-none border cursor-pointer" style="background-color: var(--bg-input); border-color: var(--border-medium); color: binanceTestnet ? '#f3ba2f' : '#ef4444';">
                    <option :value="true">模拟测试网 (Futures Testnet)</option>
                    <option :value="false">实盘网络 (Futures LIVE)</option>
                  </select>
                </div>
              </div>

              <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label class="block text-[10px] mb-1 font-mono" style="color: var(--text-muted);">Binance API Key</label>
                  <input v-model="keys.binance_key" type="password" :placeholder="binanceTestnet ? 'Binance Testnet API Key（留空保持）' : 'Binance Live API Key（留空保持）'" class="w-full rounded-lg px-2.5 py-1.5 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
                </div>
                <div>
                  <label class="block text-[10px] mb-1 font-mono" style="color: var(--text-muted);">Binance Secret Key</label>
                  <input v-model="keys.binance_secret" type="password" placeholder="Binance Secret Key" class="w-full rounded-lg px-2.5 py-1.5 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
                </div>
              </div>
              <div class="flex justify-between items-center pt-2">
                <div class="text-[10px] font-mono text-emerald-400">
                  {{ binanceTestnet ? '当前切换为币安合约模拟盘（testnet.binancefuture.com），无资金风险。' : '⚠ 当前为币安实盘网络（fapi.binance.com），请确保 API 具备期货交易权限与 IP 白名单。' }}
                </div>
                <button @click="saveEnvironment" class="px-4 py-2 rounded-lg text-xs font-mono font-bold cursor-pointer transition-all shadow-xs flex items-center gap-1.5" style="background-color: #f3ba2f; color: #000000;">
                  <Save class="w-3.5 h-3.5" />
                  <span>保存币安设置与凭证</span>
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- TAB 3: GATE.IO PANEL -->
        <div v-else-if="activeExchangeTab === 'gate'" class="space-y-4">
          <div class="p-4 rounded-xl border space-y-3" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2">
                <span class="w-2.5 h-2.5 rounded-full bg-blue-500"></span>
                <h3 class="text-xs font-bold font-mono text-blue-400">Gate.io 芝麻开门 USDT 永续合约网关</h3>
              </div>
              <span class="text-[10px] font-mono px-2 py-0.5 rounded border" :class="config.editable.gate_configured ? 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10' : 'text-amber-400 border-amber-500/30 bg-amber-500/10'">
                {{ config.editable.gate_configured ? '● 私有交易凭证已就绪' : '○ 免登录全要素行情激活' }}
              </span>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs font-mono" style="color: var(--text-muted);">
              <div class="p-3 rounded-lg border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
                <div class="text-[10px] font-bold text-slate-400 mb-1">Gate V4 官方行情</div>
                <div class="text-emerald-400 font-bold">免登录 Direct API</div>
                <div class="text-[10px] mt-1" style="color: var(--text-faint);">实时 K 线 + 深度盘口 + 自动断网容灾</div>
              </div>
              <div class="p-3 rounded-lg border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
                <div class="text-[10px] font-bold text-slate-400 mb-1">资金费率与套利</div>
                <div class="text-blue-400 font-bold">Funding Rate Matrix</div>
                <div class="text-[10px] mt-1" style="color: var(--text-faint);">实时比对 Gate 与各所费率溢价</div>
              </div>
              <div class="p-3 rounded-lg border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
                <div class="text-[10px] font-bold text-slate-400 mb-1">执行路由机制</div>
                <div class="text-blue-400 font-bold">GateAdapter V4 签名</div>
                <div class="text-[10px] mt-1" style="color: var(--text-faint);">合约张数转换 + SHA-512 私钥签名</div>
              </div>
            </div>

            <!-- Gate Credentials Inputs -->
            <div class="pt-3 border-t space-y-3" style="border-color: var(--border-subtle);">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="text-[11px] font-bold font-mono" style="color: var(--text-main);">Gate.io API 凭证与运行模式配置</div>
                <div class="flex items-center gap-2">
                  <label class="text-[11px] font-mono" style="color: var(--text-muted);">交易环境模式:</label>
                  <select v-model="gateTestnet" class="rounded px-2 py-0.5 text-[11px] font-mono font-bold outline-none border cursor-pointer" style="background-color: var(--bg-input); border-color: var(--border-medium); color: gateTestnet ? '#3b82f6' : '#ef4444';">
                    <option :value="true">模拟测试网 (Futures Testnet)</option>
                    <option :value="false">实盘网络 (Futures LIVE)</option>
                  </select>
                </div>
              </div>

              <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label class="block text-[10px] mb-1 font-mono" style="color: var(--text-muted);">Gate API Key</label>
                  <input v-model="keys.gate_key" type="password" :placeholder="gateTestnet ? 'Gate Testnet API Key（留空保持）' : 'Gate Live API Key（留空保持）'" class="w-full rounded-lg px-2.5 py-1.5 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
                </div>
                <div>
                  <label class="block text-[10px] mb-1 font-mono" style="color: var(--text-muted);">Gate Secret Key</label>
                  <input v-model="keys.gate_secret" type="password" placeholder="Gate Secret Key" class="w-full rounded-lg px-2.5 py-1.5 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
                </div>
              </div>
              <div class="flex justify-between items-center pt-2">
                <div class="text-[10px] font-mono text-emerald-400">
                  {{ gateTestnet ? '当前切换为 Gate 模拟测试网（fx-api-testnet.gateio.ws），无真实资产风险。' : '⚠ 当前为 Gate.io 实盘网络（api.gateio.ws），请确认已开放合约交易权限。' }}
                </div>
                <button @click="saveEnvironment" class="px-4 py-2 rounded-lg text-xs font-mono font-bold cursor-pointer transition-all shadow-xs flex items-center gap-1.5" style="background-color: #2354ff; color: #ffffff;">
                  <Save class="w-3.5 h-3.5" />
                  <span>保存 Gate 设置与凭证</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 2. initial capital -->
      <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="flex items-center space-x-2 mb-4 pb-3 border-b" style="border-color: var(--border-subtle);">
          <Wallet class="w-4 h-4 text-emerald-500" />
          <h2 class="text-sm font-bold font-mono" style="color: var(--text-main);">2. 主页盈亏基准 · 初始本金</h2>
        </div>
        <div class="text-xs font-mono space-y-1.5 mb-4" style="color: var(--text-muted);">
          <div>当前基准本金: <strong class="text-emerald-500 text-sm num-tabular">{{ config.editable.initial_capital }} USDT</strong></div>
          <div>历史起算时间: <span style="color: var(--text-faint);">{{ config.editable.initial_capital_reset_time }}</span>（修改本金不改变起算时间）</div>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label class="block text-[11px] mb-1 font-mono" style="color: var(--text-muted);">新初始本金 (USDT)</label>
            <input v-model="newCapital" type="number" step="0.01" class="w-full rounded-lg px-3 py-2 text-xs font-mono outline-none border num-tabular" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
          </div>
          <div>
            <label class="block text-[11px] mb-1 font-mono" style="color: var(--text-muted);">确认短语 (UPDATE CAPITAL)</label>
            <input v-model="capitalConfirm" placeholder="输入 UPDATE CAPITAL" class="w-full rounded-lg px-3 py-2 text-xs font-mono outline-none border" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
          </div>
          <div class="flex items-end">
            <button @click="saveCapital" :disabled="savingCapital" class="w-full flex items-center justify-center space-x-1.5 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-mono font-bold cursor-pointer disabled:opacity-50 transition-all shadow-xs">
              <Save class="w-3.5 h-3.5" /><span>{{ savingCapital ? '更新中...' : '更新基准本金' }}</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 3. instruments -->
      <div class="rounded-xl border overflow-hidden shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="px-4 py-3 border-b flex items-center justify-between" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);">
          <div class="flex items-center space-x-2">
            <Layers class="w-4 h-4" style="color: var(--color-brand);" />
            <h2 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
              3. 多交易所交易标的池 ({{ instruments.length }}/{{ instLimits.maximum }})
            </h2>
          </div>
          <div class="flex gap-2">
            <input v-model="newInstId" placeholder="例如: XRP 或 XRP-USDT-SWAP" class="w-52 rounded-lg px-2.5 py-1.5 text-xs font-mono outline-none border transition-colors" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" @keyup.enter="addInstrument" />
            <button @click="addInstrument" class="px-3 py-1.5 rounded-lg text-xs font-mono font-bold transition-all cursor-pointer shadow-xs" style="background-color: var(--text-main); color: var(--bg-card);">添加标的</button>
          </div>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs font-mono whitespace-nowrap">
            <thead>
              <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle); color: var(--text-muted);">
                <th class="py-2.5 px-4">通用资产</th>
                <th class="py-2.5 px-3">OKX 合约</th>
                <th class="py-2.5 px-3">币安合约</th>
                <th class="py-2.5 px-3">Gate 合约</th>
                <th class="py-2.5 px-3">类型</th>
                <th class="py-2.5 px-3">风控状态</th>
                <th class="py-2.5 px-4 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in instruments" :key="item.instId" class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors" style="border-color: var(--border-subtle);">
                <td class="py-2.5 px-4 font-bold text-emerald-400">
                  {{ item.name || item.instId.split('-')[0] }}
                </td>
                <td class="py-2.5 px-3 font-mono" style="color: var(--text-main);">
                  {{ item.instId }}
                </td>
                <td class="py-2.5 px-3 font-mono text-amber-400">
                  {{ (item.name || item.instId.split('-')[0]) + 'USDT' }}
                </td>
                <td class="py-2.5 px-3 font-mono text-blue-400">
                  {{ (item.name || item.instId.split('-')[0]) + '_USDT' }}
                </td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-faint);">{{ item.ctType || 'SWAP' }}</td>
                <td class="py-2.5 px-3">
                  <span v-if="item.protected" class="px-1.5 py-0.2 rounded text-[10px] font-bold border" style="background-color: var(--color-warn-bg); border-color: var(--color-warn-border); color: var(--color-warn);">🔒 保底必选</span>
                  <span v-else-if="item.has_tracker" class="px-1.5 py-0.2 rounded text-[10px] font-bold border" style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);">持仓中</span>
                  <span v-else class="text-[11px]" style="color: var(--text-faint);">可移除</span>
                </td>
                <td class="py-2.5 px-4 text-right">
                  <button @click="removeInstrument(item)" :disabled="item.protected || item.has_tracker" class="p-1 rounded hover:opacity-80 text-rose-400 disabled:opacity-20 cursor-pointer transition-opacity" title="从标的池移除">
                    <Trash2 class="w-3.5 h-3.5" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="px-4 py-2 border-t text-[10px] font-mono" style="border-color: var(--border-subtle); color: var(--text-faint);">
          标的池统一维护：系统自动将通用资产（如 BTC/ETH/SOL）映射匹配到 OKX (SWAP)、币安 (USDT-M) 与 Gate (Futures V4)，支持三所全要素行情并发采集与按路由下单。
        </p>
      </div>

      <!-- 4. positions & emergency close -->
      <div class="rounded-xl border overflow-hidden shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="px-4 py-3 border-b flex items-center justify-between" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);">
          <div class="flex items-center space-x-2">
            <KeyRound class="w-4 h-4 text-rose-500" />
            <h2 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">4. 当前持仓与应急平仓</h2>
          </div>
          <button @click="loadPositions" class="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg border text-xs font-mono cursor-pointer transition-all shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-main);">
            <RefreshCw class="w-3.5 h-3.5" />
            <span>刷新持仓与挂单</span>
          </button>
        </div>
        <div v-if="snapshotState" class="px-4 pt-2 text-[11px] font-mono text-amber-500">{{ snapshotState }}</div>
        <div v-if="snapshot" class="px-4 pt-2 text-[11px] font-mono" style="color: var(--text-muted);">
          环境：<strong :class="snapshot.environment === 'live' ? 'text-rose-500' : 'text-emerald-500'">{{ (snapshot.environment || '').toUpperCase() }}</strong>
          · 持仓 {{ snapshot.positions?.length ?? 0 }} · 当前挂单 {{ snapshot.orders?.length ?? 0 }} · {{ new Date(snapshot.captured_at_ms).toLocaleString() }}
        </div>
        <div class="overflow-x-auto mt-2">
          <table v-if="snapshot?.positions?.length" class="w-full text-left text-xs font-mono whitespace-nowrap">
            <thead>
              <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle); color: var(--text-muted);">
                <th class="py-2.5 px-4">仓位标的</th>
                <th class="py-2.5 px-3">张数</th>
                <th class="py-2.5 px-3">模式</th>
                <th class="py-2.5 px-3">未实现盈亏</th>
                <th class="py-2.5 px-4 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in snapshot.positions" :key="p.instId + p.posSide" class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors" style="border-color: var(--border-subtle);">
                <td class="py-2.5 px-4">
                  <strong style="color: var(--text-main);">{{ p.instId }}</strong>
                  <span class="ml-1.5 px-1.5 py-0.2 rounded text-[9px] font-bold border" :style="p.posSide === 'long' ? { backgroundColor: 'var(--color-up-bg)', borderColor: 'var(--color-up-border)', color: 'var(--color-up)' } : { backgroundColor: 'var(--color-down-bg)', borderColor: 'var(--color-down-border)', color: 'var(--color-down)' }">
                    {{ (p.posSide || 'net').toUpperCase() }}
                  </span>
                </td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-muted);">{{ p.pos || '0' }}</td>
                <td class="py-2.5 px-3 text-[11px]" style="color: var(--text-faint);">{{ p.mgnMode || '--' }}</td>
                <td class="py-2.5 px-3 font-bold num-tabular" :class="Number(p.upl || 0) >= 0 ? 'text-emerald-500' : 'text-rose-500'">{{ Number(p.upl || 0).toFixed(4) }}</td>
                <td class="py-2.5 px-4 text-right">
                  <button @click="openClose(p)" class="px-2.5 py-1 rounded-md text-[10px] font-mono font-bold border transition-all cursor-pointer shadow-xs" style="background-color: var(--color-down-bg); border-color: var(--color-down-border); color: var(--color-down);">快速平仓</button>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else-if="snapshot" class="py-6 text-center text-xs font-mono text-emerald-500">✓ 当前环境 0 活跃持仓</div>
          <div v-else class="py-6 text-center text-xs font-mono" style="color: var(--text-faint);">点击"刷新持仓与挂单"从 OKX 读取最新实时状态</div>
        </div>
        <p class="px-4 py-2 border-t text-[10px] font-mono" style="border-color: var(--border-subtle); color: var(--text-faint);">平仓流程：复核环境与仓位 → 撤销同标的冲突委托 → autoCxl 市价平仓 → 轮询确认仓位归零。需先启用上方手动平仓开关。</p>
      </div>
    </template>

    <!-- Close confirm modal -->
    <div v-if="closeModal?.show" class="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4" @click.self="closeModal = null">
      <div class="rounded-xl border p-5 sm:p-6 w-full max-w-[460px] max-h-[88dvh] overflow-y-auto shadow-2xl transition-colors" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <h3 class="text-sm font-bold text-rose-500 mb-2 font-mono">快速安全平仓</h3>
        <p class="text-[11px] font-mono leading-relaxed mb-3" style="color: var(--text-muted);">
          将从 {{ (snapshot?.environment || 'demo').toUpperCase() }} 环境重新核对并平掉
          <strong style="color: var(--text-main);">{{ closeModal.pos.instId }} {{ (closeModal.pos.posSide || 'net').toUpperCase() }} {{ Math.abs(Number(closeModal.pos.pos || 0)) }}</strong>。
          令牌 90 秒有效且仅可使用一次。
        </p>
        <label class="block text-[11px] mb-1 font-mono" style="color: var(--text-muted);">当前管理员密码</label>
        <input v-model="closePassword" type="password" class="w-full rounded-lg px-3 py-2 text-xs font-mono outline-none border mb-3" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
        <label class="block text-[11px] mb-1 font-mono" style="color: var(--text-muted);">确认短语：{{ closeModal.pos.close_confirmation }}</label>
        <input v-model="closePhraseInput" :placeholder="closeModal.pos.close_confirmation" class="w-full rounded-lg px-3 py-2 text-xs font-mono outline-none border mb-4" style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);" />
        <div class="flex justify-end gap-2">
          <button @click="closeModal = null" class="px-3 py-2 rounded-lg border text-xs font-mono cursor-pointer transition-all shadow-xs" style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);">取消</button>
          <button @click="confirmClose" :disabled="closing" class="px-3 py-2 rounded-lg text-xs font-mono font-bold cursor-pointer disabled:opacity-50 transition-all shadow-xs" style="background-color: var(--color-down-bg); border-color: var(--color-down-border); color: var(--color-down);">{{ closing ? '执行中，等待成交确认…' : '确认平仓' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

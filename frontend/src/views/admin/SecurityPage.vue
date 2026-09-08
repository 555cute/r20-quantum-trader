<script setup lang="ts">
import { useToast } from '../../composables/useToast'
const toast = useToast()
import { ref, onMounted , watch} from 'vue'
import { useI18n } from '../../composables/useI18n'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import { ShieldAlert, Wallet, Save, Terminal, KeyRound, RefreshCw, Layers, Trash2, X, LogOut, Unlink } from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()
const { t } = useI18n()
const config = ref<any>(null)
const runtime = ref<any>(null)
const loading = ref(true)

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
const keysOpen = ref(false)
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
  toast.warn('正在检查 OKX CLI、OAuth 与私有读取…')
  try {
    applyRuntime(await api('/api/v1/admin/okx/runtime?refresh=1'))
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
    toast.ok(res.message || `${instId} 已成功加入交易池并实时同步全网大屏与因果雷达`)
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
    toast.ok(res.message || `${item.instId} 已从交易池移除并实时同步全网大屏与因果雷达`)
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
  if (!manualClose.value) { toast.err('请先启用后台手动平仓并保存开关'); return }
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

onMounted(loadAll)
</script>

<template>
  <div class="space-y-4 text-xs">
    <!-- Header & Action Bar -->
    <div class="panel-banner-compact">
      <div class="flex items-center space-x-2.5">
        <div class="panel-banner-icon">
          <Wallet class="w-3.5 h-3.5" />
        </div>
        <div>
          <h1 class="text-xs sm:text-[13px] font-semibold" style="color: var(--ink-1);">
            {{ t('nav.admin.security') }}
          </h1>
          <p class="text-[11px] mt-0.5" style="color: var(--ink-2);"> OKX 账户连接与交易标的池 —— OKX 官方账户授权连接、实盘/模拟盘环境切换、初始本金基准与交易标的管理 </p>
        </div>
      </div>
      <span class="badge-lever">
        交易核心底座 · 2/4
      </span>
    </div>
    <div v-if="loading" class="py-12 text-center text-xs" style="color: var(--ink-2);">正在加载...</div>

    <template v-else-if="config">
      <!-- 1. OKX account & environment -->
      <div class="rounded-xl border p-4 sm:p-5 space-y-4 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center justify-between pb-3 border-b" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <ShieldAlert class="w-4 h-4" style="color: var(--accent);" />
            <h2 class="text-sm font-bold" style="color: var(--ink-1);">1. OKX 账号连接与交易环境</h2>
          </div>
          <span v-if="runtime" class="text-[11px] px-2 py-0.5 rounded border font-bold" :class="runtime.ready ? 'text-emerald-500 border-emerald-500/30 bg-emerald-500/10' : runtime.degraded ? 'text-amber-500 border-amber-500/30 bg-amber-500/10' : 'text-rose-500 border-rose-500/30 bg-rose-500/10'">
            {{ runtime.ready ? 'READY · 可运行' : runtime.demo_oauth_unavailable ? 'DEGRADED · DEMO OAuth接口不可用' : runtime.degraded ? 'DEGRADED · OKX当前环境接口不可用' : 'NOT READY · 禁止交易' }}
          </span>
        </div>

        <div v-if="runtime" class="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px] gap-4">
          <!-- runtime detail -->
          <div>
            <div class="text-xs leading-relaxed space-y-1" style="color: var(--ink-2);">
              <div>当前环境：<strong style="color: var(--ink-1);">{{ (runtime.selected_mode || 'demo').toUpperCase() }}</strong></div>
              <div>CLI：<span style="color: var(--ink-1);">{{ runtime.cli?.installed ? (runtime.cli.version || '已安装') : '未安装' }} · {{ runtime.cli?.path || 'PATH 中不可见' }}</span></div>
              <div>认证来源：<span style="color: var(--accent);">{{ sourceLabel[runtime.credential_source] || runtime.credential_source }}</span></div>
              <div>连接账号：<span style="color: var(--ink-1);">{{ runtime.oauth?.account_label || (runtime.oauth?.status === 'logged_in' ? 'OAuth 已连接（OKX CLI 当前不返回账号昵称/UID）' : '--') }}</span></div>
              <div>OAuth：<span style="color: var(--ink-1);">{{ runtime.oauth?.status }}{{ runtime.oauth?.site ? ' · ' + runtime.oauth.site : '' }}</span></div>
              <div class="text-[11px]" style="color: var(--ink-3);">权限：{{ (runtime.oauth?.scopes || []).join(', ') || '--' }}</div>
              <div>只读探针：<span :class="runtime.read_probe?.ok ? 'text-emerald-500' : runtime.degraded ? 'text-amber-500' : 'text-rose-500'">{{ runtime.read_probe?.detail || '--' }}</span></div>
              <div v-if="runtime.live_control_probe" class="text-[11px]">LIVE 对照探针：<span :class="runtime.live_control_probe.ok ? 'text-emerald-500' : 'text-rose-500'">{{ runtime.live_control_probe.detail }}</span></div>
              <div v-if="runtime.issues?.length" class="mt-2 text-[11px]" :class="runtime.degraded ? 'text-amber-500' : 'text-rose-500'">
                <div v-for="(issue, i) in runtime.issues" :key="i">• {{ issue }}</div>
              </div>
              <div v-if="runtime.steps?.length" class="mt-2 text-[11px]" style="color: var(--ink-3);">
                <div class="font-bold mb-0.5" style="color: var(--ink-2);">操作指引</div>
                <div v-for="(s, i) in runtime.steps" :key="i">• {{ s }}</div>
              </div>
            </div>
            <div class="flex gap-2 mt-3">
              <button @click="rediagnose" class="flex items-center space-x-1 px-3 py-1.5 rounded-lg border text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);"><RefreshCw class="w-3.5 h-3.5" /><span>重新诊断</span></button>
              <button @click="checkCli" class="px-3 py-1.5 rounded-lg border text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);">检测 Node/npm/CLI</button>
            </div>
            <div v-if="cliCheck" class="mt-2 p-2.5 rounded-lg border text-[11px] space-y-0.5" style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-2);">
              <div>Node.js：{{ cliCheck.node_installed ? `✓ ${cliCheck.node_version} (${cliCheck.node_path})` : '✗ 未安装' }}</div>
              <div>npm：{{ cliCheck.npm_installed ? `✓ ${cliCheck.npm_version}` : '✗ 未安装' }}</div>
              <div>OKX CLI：{{ cliCheck.okx_installed ? `✓ ${cliCheck.okx_version} (${cliCheck.okx_path})` : '✗ 未安装' }}</div>
            </div>
          </div>

          <!-- OAuth panel -->
          <div class="rounded-lg p-3.5 border shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-1);">
            <div class="text-[11px] font-bold mb-2" style="color: var(--ink-1);">官方 OAuth 授权（推荐）</div>
            <div class="text-[11px] mb-2 leading-relaxed" style="color: var(--ink-2);">授权码登录，无需向 R20 提供 OKX 密码、API Key 或 2FA。</div>
            <label class="block text-[11px] mb-1" style="color: var(--ink-2);">OKX 站点</label>
            <select v-model="oauthSite" class="w-full rounded-lg px-2 py-1.5 text-xs outline-none border mb-2" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);">
              <option value="global">Global · www.okx.com</option>
              <option value="eea">EEA · my.okx.com</option>
              <option value="us">US · app.okx.com</option>
              <option value="tr">TR · tr.okx.com</option>
            </select>
            <div class="flex flex-wrap gap-2">
              <!-- When logged in: provide Unbind and Switch buttons -->
              <template v-if="runtime?.oauth?.status === 'logged_in'">
                <button
                  v-if="auth.isSuperadmin"
                  @click="switchOauthAccount"
                  :disabled="switchingAccount || loggingOutOauth"
                  class="flex-1 btn-admin-primary disabled:opacity-50 inline-flex items-center justify-center space-x-1"
                  title="解除当前授权并重新在浏览器中连接新 OKX 账号"
                >
                  <RefreshCw class="w-3.5 h-3.5" :class="switchingAccount ? 'animate-spin' : ''" />
                  <span>{{ switchingAccount ? '切换中…' : '更换 OKX 账号' }}</span>
                </button>
                <button
                  v-if="auth.isSuperadmin"
                  @click="logoutOauth"
                  :disabled="loggingOutOauth || switchingAccount"
                  class="px-3 py-1.5 rounded-lg border text-xs cursor-pointer transition-all shadow-xs disabled:opacity-50 inline-flex items-center space-x-1 text-rose-400 hover:bg-rose-950/30"
                  style="background-color: var(--surface-1); border-color: var(--down-line);"
                  title="解绑当前 OKX 账号并清除本地授权凭证"
                >
                  <Unlink class="w-3.5 h-3.5" />
                  <span>{{ loggingOutOauth ? '解绑中…' : '解绑账号' }}</span>
                </button>
              </template>
              <!-- When not logged in: standard connect button -->
              <template v-else>
                <button
                  v-if="auth.isSuperadmin"
                  @click="startOauth"
                  :disabled="startingOauth"
                  class="flex-1 btn-admin-primary disabled:opacity-50"
                >
                  <KeyRound class="w-3.5 h-3.5" />
                  <span>{{ startingOauth ? '申请授权码中…' : '使用授权码连接 OKX' }}</span>
                </button>
              </template>
              <button
                v-if="auth.isSuperadmin"
                @click="installCli"
                :disabled="installingCli"
                class="btn-admin-secondary disabled:opacity-50"
              >
                {{ installingCli ? '安装中…' : '安装/升级 CLI' }}
              </button>
            </div>

            <div v-if="oauthState" class="mt-2 text-[11px] text-amber-500">{{ oauthState }}</div>

            <div v-if="oauthResult?.kind === 'device'" class="mt-2 p-2.5 rounded-lg border space-y-1.5" style="background-color: var(--accent-bg); border-color: var(--accent-line);">
              <div class="text-[11px] font-bold" style="color: var(--ink-1);">请在浏览器完成 OKX 官方授权</div>
              <div class="text-[11px] break-all"><a :href="oauthResult.verification_uri" target="_blank" rel="noopener" class="underline" style="color: var(--accent);">{{ oauthResult.verification_uri }}</a></div>
              <div class="text-center py-1.5 rounded border" style="background-color: var(--surface-2); border-color: var(--line-1);"><span class="text-lg font-semibold tracking-widest" style="color: var(--ink-1);">{{ oauthResult.user_code }}</span></div>
              <div class="text-[11px]" style="color: var(--ink-2);">有效期约 {{ Math.ceil(Number(oauthResult.expires_in || 600) / 60) }} 分钟</div>
              <button @click="checkOauth" class="w-full px-2 py-1.5 rounded-lg border text-[11px] cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-2); color: var(--ink-1);">我已授权，检查状态</button>
            </div>
            <div v-else-if="oauthResult?.kind === 'logged_in'" class="mt-2 p-2.5 rounded-lg border text-[11px] text-emerald-500" style="background-color: var(--up-bg); border-color: var(--up-line);">
              <div class="flex items-center justify-between">
                <span>✅ 当前已经登录 · 站点 {{ oauthResult.site }}</span>
                <span class="text-[11px] text-emerald-400">已就绪</span>
              </div>
              <div class="text-[11px] break-all mt-1" style="color: var(--ink-2);">{{ (oauthResult.scopes || []).join(', ') }}</div>
            </div>
            <div v-else-if="oauthResult?.kind === 'error'" class="mt-2 p-2.5 rounded-lg border text-[11px] text-rose-500" style="background-color: var(--down-bg); border-color: var(--down-line);">{{ oauthResult.message }}</div>
          </div>
        </div>

        <!-- environment + backup keys -->
        <div class="pt-3 border-t" style="border-color: var(--line-1);">
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
            <div>
              <label class="block text-[11px] mb-1" style="color: var(--ink-2);">当前交易环境</label>
              <select v-model="config.editable.okx_environment" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);">
                <option value="demo">模拟盘 DEMO</option>
                <option value="live">实盘 LIVE</option>
              </select>
            </div>
            <div class="flex items-end pb-1">
              <label class="flex items-center space-x-2 cursor-pointer">
                <input type="checkbox" v-model="manualClose" class="accent-blue-500" />
                <span class="text-xs" style="color: var(--ink-2);">允许后台手动平仓</span>
              </label>
            </div>
            <div class="flex gap-2">
              <button @click="saveEnvironment" class="flex-1 btn-admin-primary"><Save class="w-3.5 h-3.5" /><span>保存环境与凭证</span></button>
              <button @click="saveManualClose" class="btn-admin-secondary">保存平仓开关</button>
            </div>
          </div>

          <details class="mt-3">
            <summary class="cursor-pointer text-[11px] select-none" style="color: var(--accent);">备用方式：分别配置 LIVE / DEMO API Key（无人值守部署）</summary>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3 p-3 rounded-lg border shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-1);">
              <div class="space-y-2">
                <div class="text-[11px] font-bold" style="color: var(--ink-1);">实盘 LIVE Key</div>
                <input v-model="keys.live_key" type="password" placeholder="API Key（留空保持现有）" class="w-full rounded-lg px-2.5 py-1.5 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
                <input v-model="keys.live_secret" type="password" placeholder="Secret Key" class="w-full rounded-lg px-2.5 py-1.5 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
                <input v-model="keys.live_pass" type="password" placeholder="Passphrase" class="w-full rounded-lg px-2.5 py-1.5 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
              </div>
              <div class="space-y-2">
                <div class="text-[11px] font-bold" style="color: var(--ink-1);">模拟盘 DEMO Key</div>
                <input v-model="keys.demo_key" type="password" placeholder="API Key（留空保持现有）" class="w-full rounded-lg px-2.5 py-1.5 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
                <input v-model="keys.demo_secret" type="password" placeholder="Secret Key" class="w-full rounded-lg px-2.5 py-1.5 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
                <input v-model="keys.demo_pass" type="password" placeholder="Passphrase" class="w-full rounded-lg px-2.5 py-1.5 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
              </div>
              <div class="sm:col-span-2 text-[11px]" style="color: var(--ink-3);">OAuth 与 API Key 二选一即可。不要为同一运行用户同时配置 CLI API Key Profile 和 OAuth。</div>
            </div>
          </details>
        </div>
      </div>

      <!-- 2. initial capital -->
      <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center space-x-2 mb-4 pb-3 border-b" style="border-color: var(--line-1);">
          <Wallet class="w-4 h-4 text-emerald-500" />
          <h2 class="text-sm font-bold" style="color: var(--ink-1);">2. 主页盈亏基准 · 初始本金</h2>
        </div>
        <div class="text-xs space-y-1.5 mb-4" style="color: var(--ink-2);">
          <div>当前基准本金: <strong class="text-emerald-500 text-sm num">{{ config.editable.initial_capital }} USDT</strong></div>
          <div>历史起算时间: <span style="color: var(--ink-3);">{{ config.editable.initial_capital_reset_time }}</span>（修改本金不改变起算时间）</div>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label class="block text-[11px] mb-1" style="color: var(--ink-2);">新初始本金 (USDT)</label>
            <input v-model="newCapital" type="number" step="0.01" class="w-full rounded-lg px-3 py-2 text-xs outline-none border num" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
          </div>
          <div>
            <label class="block text-[11px] mb-1" style="color: var(--ink-2);">确认短语 (UPDATE CAPITAL)</label>
            <input v-model="capitalConfirm" placeholder="输入 UPDATE CAPITAL" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
          </div>
          <div class="flex items-end">
            <button @click="saveCapital" :disabled="savingCapital" class="w-full btn-admin-primary disabled:opacity-50">
              <Save class="w-3.5 h-3.5" /><span>{{ savingCapital ? '更新中...' : '更新基准本金' }}</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 3. instruments -->
      <div class="rounded-xl border overflow-hidden shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="px-4 py-3 border-b flex items-center justify-between" style="border-color: var(--line-1); background-color: var(--surface-1);">
          <div class="flex items-center space-x-2">
            <Layers class="w-4 h-4" style="color: var(--accent);" />
            <h2 class="text-xs font-semibold" style="color: var(--ink-1);">
              3. 交易标的池 ({{ instruments.length }}/{{ instLimits.maximum }})
            </h2>
          </div>
          <div class="flex gap-2">
            <input v-model="newInstId" placeholder="例如: XRP-USDT-SWAP" class="w-44 rounded-lg px-2.5 py-1.5 text-xs outline-none border transition-colors" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" @keyup.enter="addInstrument" />
            <button @click="addInstrument" class="btn-admin-primary">添加标的</button>
          </div>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs whitespace-nowrap">
            <thead>
              <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--line-1); background-color: var(--surface-1); color: var(--ink-2);">
                <th class="py-2.5 px-4">合约代码</th>
                <th class="py-2.5 px-3">名称</th>
                <th class="py-2.5 px-3">类型</th>
                <th class="py-2.5 px-3">风控状态</th>
                <th class="py-2.5 px-4 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in instruments" :key="item.instId" class="border-b last:border-b-0 hover:bg-[var(--surface-3)] transition-colors" style="border-color: var(--line-1);">
                <td class="py-2.5 px-4 font-bold" style="color: var(--ink-1);">{{ item.instId }}</td>
                <td class="py-2.5 px-3" style="color: var(--ink-2);">{{ item.name }}</td>
                <td class="py-2.5 px-3 num" style="color: var(--ink-3);">{{ item.ctType || 'SWAP' }}</td>
                <td class="py-2.5 px-3">
                  <span v-if="item.protected" class="px-1.5 py-0.5 rounded-[3px] text-[11px] font-bold border" style="background-color: var(--warn-bg); border-color: var(--warn-line); color: var(--warn);">保底必选</span>
                  <span v-else-if="item.has_tracker" class="px-1.5 py-0.5 rounded-[3px] text-[11px] font-bold border" style="background-color: var(--accent-bg); border-color: var(--accent-line); color: var(--accent);">持仓中</span>
                  <span v-else class="text-[11px] px-1.5 py-0.5 rounded-[3px] border" style="background-color: var(--surface-3); border-color: var(--line-1); color: var(--ink-3);">可移除</span>
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
        <p class="px-4 py-2 border-t text-[11px]" style="border-color: var(--line-1); color: var(--ink-3);">BTC 为系统保底标的不可删除；有在途追踪器的标的禁止移除；最多 {{ instLimits.maximum }} 个。</p>
      </div>

      <!-- 4. positions & emergency close -->
      <div class="rounded-xl border overflow-hidden shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="px-4 py-3 border-b flex items-center justify-between" style="border-color: var(--line-1); background-color: var(--surface-1);">
          <div class="flex items-center space-x-2">
            <KeyRound class="w-4 h-4 text-rose-500" />
            <h2 class="text-xs font-semibold" style="color: var(--ink-1);">4. 当前持仓与应急平仓</h2>
          </div>
          <button @click="loadPositions" class="flex items-center space-x-1.5 px-2.5 py-1 rounded-lg border text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-2); border-color: var(--line-2); color: var(--ink-1);">
            <RefreshCw class="w-3.5 h-3.5" />
            <span>刷新持仓与挂单</span>
          </button>
        </div>
        <div v-if="snapshotState" class="px-4 pt-2 text-[11px] text-amber-500">{{ snapshotState }}</div>
        <div v-if="snapshot" class="px-4 pt-2 text-[11px]" style="color: var(--ink-2);">
          环境：<strong :class="snapshot.environment === 'live' ? 'text-rose-500' : 'text-emerald-500'">{{ (snapshot.environment || '').toUpperCase() }}</strong>
          · 持仓 {{ snapshot.positions?.length ?? 0 }} · 当前挂单 {{ snapshot.orders?.length ?? 0 }} · {{ new Date(snapshot.captured_at_ms).toLocaleString() }}
        </div>
        <div class="overflow-x-auto mt-2">
          <table v-if="snapshot?.positions?.length" class="w-full text-left text-xs whitespace-nowrap">
            <thead>
              <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--line-1); background-color: var(--surface-1); color: var(--ink-2);">
                <th class="py-2.5 px-4">仓位标的</th>
                <th class="py-2.5 px-3">张数</th>
                <th class="py-2.5 px-3">模式</th>
                <th class="py-2.5 px-3">未实现盈亏</th>
                <th class="py-2.5 px-4 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in snapshot.positions" :key="p.instId + p.posSide" class="border-b last:border-b-0 hover:bg-[var(--surface-3)] transition-colors" style="border-color: var(--line-1);">
                <td class="py-2.5 px-4">
                  <strong style="color: var(--ink-1);">{{ p.instId }}</strong>
                  <span class="ml-1.5 px-1.5 py-0.2 rounded text-[11px] font-bold border" :style="p.posSide === 'long' ? { backgroundColor: 'var(--up-bg)', borderColor: 'var(--up-line)', color: 'var(--up)' } : { backgroundColor: 'var(--down-bg)', borderColor: 'var(--down-line)', color: 'var(--down)' }">
                    {{ (p.posSide || 'net').toUpperCase() }}
                  </span>
                </td>
                <td class="py-2.5 px-3 num" style="color: var(--ink-2);">{{ p.pos || '0' }}</td>
                <td class="py-2.5 px-3 text-[11px]" style="color: var(--ink-3);">{{ p.mgnMode || '--' }}</td>
                <td class="py-2.5 px-3 font-bold num" :class="Number(p.upl || 0) >= 0 ? 'text-emerald-500' : 'text-rose-500'">{{ Number(p.upl || 0).toFixed(4) }}</td>
                <td class="py-2.5 px-4 text-right">
                  <button @click="openClose(p)" class="px-2.5 py-1 rounded-md text-[11px] font-bold border transition-all cursor-pointer shadow-xs" style="background-color: var(--down-bg); border-color: var(--down-line); color: var(--down);">快速平仓</button>
                </td>
              </tr>
            </tbody>
          </table>
          <div v-else-if="snapshot" class="py-6 text-center text-xs text-emerald-500">✓ 当前环境 0 活跃持仓</div>
          <div v-else class="py-6 text-center text-xs" style="color: var(--ink-3);">点击"刷新持仓与挂单"从 OKX 读取最新实时状态</div>
        </div>
        <p class="px-4 py-2 border-t text-[11px]" style="border-color: var(--line-1); color: var(--ink-3);">平仓流程：复核环境与仓位 → 撤销同标的冲突委托 → autoCxl 市价平仓 → 轮询确认仓位归零。需先启用上方手动平仓开关。</p>
      </div>
    </template>

    <!-- Close confirm modal -->
    <div v-if="closeModal?.show" class="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4" @click.self="closeModal = null">
      <div class="rounded-xl border p-5 sm:p-6 w-full max-w-[460px] max-h-[88dvh] overflow-y-auto shadow-2xl transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <h3 class="text-sm font-bold text-rose-500 mb-2">快速安全平仓</h3>
        <p class="text-[11px] leading-relaxed mb-3" style="color: var(--ink-2);">
          将从 {{ (snapshot?.environment || 'demo').toUpperCase() }} 环境重新核对并平掉
          <strong style="color: var(--ink-1);">{{ closeModal.pos.instId }} {{ (closeModal.pos.posSide || 'net').toUpperCase() }} {{ Math.abs(Number(closeModal.pos.pos || 0)) }}</strong>。
          令牌 90 秒有效且仅可使用一次。
        </p>
        <label class="block text-[11px] mb-1" style="color: var(--ink-2);">当前管理员密码</label>
        <input v-model="closePassword" type="password" class="w-full rounded-lg px-3 py-2 text-xs outline-none border mb-3" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
        <label class="block text-[11px] mb-1" style="color: var(--ink-2);">确认短语：{{ closeModal.pos.close_confirmation }}</label>
        <input v-model="closePhraseInput" :placeholder="closeModal.pos.close_confirmation" class="w-full rounded-lg px-3 py-2 text-xs outline-none border mb-4" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
        <div class="flex justify-end gap-2">
          <button @click="closeModal = null" class="px-3 py-2 rounded-lg border text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);">取消</button>
          <button @click="confirmClose" :disabled="closing" class="px-3 py-2 rounded-lg text-xs font-bold cursor-pointer disabled:opacity-50 transition-all shadow-xs" style="background-color: var(--down-bg); border-color: var(--down-line); color: var(--down);">{{ closing ? '执行中，等待成交确认…' : '确认平仓' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

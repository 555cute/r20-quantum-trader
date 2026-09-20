<script setup lang="ts">
/**
 * SecurityPage.vue · 交易场所与安全配置工位
 * ---------------------------------------------------------------------------
 * 骨架（推倒重来）：
 *   旧 = 页头内联 chip + 4 张总览小卡 + 下划线 Tab 条
 *        + 页签1：路由卡（**7 个手写 radio 卡，每个 6 行内联 :style 三元**）+ 三所凭证卡 + 健康 chip
 *        + 页签2：本金卡 + 标的池 DataTable
 *        + 页签3：手动平仓 checkbox + 持仓 DataTable + **手写 fixed 遮罩平仓弹窗**
 *   新 = 共享 PageHeader（路由态移入状态带）
 *        → **接入状态带**（OKX / Binance / Gate / 标的池）
 *        → **共享 `.seg` 三页签**
 *        → venues：路由策略（**radio 组全部数据驱动**）+ 三所凭证 + 跨所健康
 *        → pool：本金基线 + 标的池行式清单
 *        → emergency：手动平仓总闸（BaseSwitch）+ 持仓行式清单 + **BaseDialog 平仓双确认**
 *
 * 后端契约（逐字未改）：
 *   GET  /api/v1/admin/config · /api/v1/admin/okx/runtime?refresh=1 · /api/v1/admin/instruments
 *   GET  /api/v1/admin/multi-exchange · /api/v1/admin/okx/account-snapshot
 *   PUT  /api/v1/admin/config · /api/v1/admin/account-baseline · /api/v1/admin/multi-exchange
 *   POST /api/v1/admin/instruments · /api/v1/admin/multi-exchange/test-connection
 *        /api/v1/admin/positions/close
 *   DELETE /api/v1/admin/instruments/{instId}
 *
 * ⚠️ 高风险门禁逐字保留：切 LIVE 需逐字 `LIVE`；改本金需超管 + 逐字 `UPDATE CAPITAL`；
 *    删标的需逐字 `REMOVE <instId>`；Gate 开闸需短语；平仓需管理员密码 + 令牌短语。
 * ⚠️ 派生逻辑仍全部来自 `./securityLogic.ts`（未触碰）。
 */
import { useToast } from '../../composables/useToast'
import { useConfirm } from '../../composables/useConfirm'
const toast = useToast()
const { ask } = useConfirm()
import { ref, computed, onMounted } from 'vue'
import PageHeader from '../../components/admin/PageHeader.vue'
import SettingsSection from '../../components/admin/page-parts/SettingsSection.vue'
import { useI18n } from '../../composables/useI18n'
import { useRovingTabs } from '../../composables/useRovingTabs';
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import { fmtDateTime } from '../../utils/format'
import {
  deriveOkxLinked, deriveMxHealthChips, deriveGateExecDirty, deriveBinanceExecDirty,
  venueStatus, envTextOf, okxEnvText as okxEnvTextOf, envBadge,
} from './securityLogic'
import VenueCredentialCard from '../../components/admin/page-parts/VenueCredentialCard.vue'
import BaseSwitch from '../../components/base/BaseSwitch.vue'
import BaseDialog from '../../components/base/BaseDialog.vue'
import BaseEmpty from '../../components/base/BaseEmpty.vue'
import { Save, RefreshCw, Layers, Trash2, Zap, ShieldCheck, Route, KeyRound,
  Wallet, Activity, AlertTriangle, Loader2, Radar } from 'lucide-vue-next'
import BaseLoadingAnnounce from '../../components/base/BaseLoadingAnnounce.vue';

const { api } = useApi()
const auth = useAuthStore()
const { t } = useI18n()
const config = ref<any>(null)
const runtime = ref<any>(null)
const loading = ref(true)
/** 批 24：首屏加载失败的原因（留在页面上，配重试按钮；不再只靠一闪而过的 toast） */
const loadError = ref('')

type TabKey = 'venues' | 'pool' | 'emergency'
const activeTab = ref<TabKey>('venues')
const positionsLoadedOnce = ref(false)

/* 批 66：页签栏的漫游 tabindex 与方向键导航。 */
const { setRef: setSecTabRef, onKeydown: onSecTabKey, roving: secTabRoving } = useRovingTabs(
  () => TABS.value.length,
  (i) => { switchTab(TABS.value[i].key) },
)

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
const capitalAmountOk = computed(() => {
  const n = Number(newCapital.value)
  return !Number.isNaN(n) && n > 0
})
const capitalConfirmOk = computed(() => capitalConfirm.value.trim().toUpperCase() === 'UPDATE CAPITAL')

// ---- instruments ----
const instruments = ref<any[]>([])
const instLimits = ref<any>({ minimum: 1, maximum: 20 })
const newInstId = ref('')

// ---- positions & close ----
const snapshot = ref<any>(null)
const snapshotState = ref('')
/** 批 70：区分「正在加载」与「加载失败」—— 此前两者共用一个字符串，
 *  模板无条件渲染旋转图标，失败时用户看到的是「转圈 + 报错」，
 *  视觉上像是在继续加载（而不是已经失败），读屏器也收不到任何通报。 */
const snapshotError = ref(false)
const manualClose = ref(false)
const closePassword = ref('')
const closeModal = ref<{ show: boolean; pos: any } | null>(null)
const closePhraseInput = ref('')
const closing = ref(false)
const closePhraseOk = computed(() => {
  const pos = closeModal.value?.pos
  if (!pos?.close_confirmation) return false
  return closePhraseInput.value.trim().toUpperCase() === pos.close_confirmation
})
const closeReady = computed(() => {
  return !!closePassword.value && closePhraseOk.value
})

// ---- 多所凭证与档位（Binance / Gate 独立保存） ----
const mx = ref<any>(null)
const mxForm = ref({ binance_api_key: '', binance_secret_key: '', gate_api_key: '', gate_secret_key: '' })
const mxTestnet = ref({ binance: false, gate: false })
const preferredVenue = ref('auto')
const routingMode = ref('auto')
const gateExec = ref(false)
const gateExecPhrase = ref('')
const binanceExec = ref(false)
const binanceExecPhrase = ref('')
const okxCredViewLive = ref(false)
const venueLatencies = ref<Record<string, number>>({})
const savingMx = ref(false)
const savingOkx = ref(false)
const savingVenue = ref<'binance' | 'gate' | ''>('')
const probingVenue = ref<'binance' | 'gate' | 'okx' | ''>('')

async function loadAll() {
  loading.value = true
  loadError.value = ''
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
    // 批 24：除了 toast，还要把错误留在页面上（toast 3 秒即消失，用户回来只看到空白页）
    loadError.value = String(e?.message || e)
    toast.err(t('admin.security.errLoadFailed', undefined, { msg: e.message }))
  } finally {
    loading.value = false
  }
}

function applyRuntime(rt: any) {
  runtime.value = rt
}

// 批2(2026-09-13)：原 rediagnose() 与 loadAll() 重复（后者已带 refresh=1 拉取运行态），
// 且从未被模板调用（TS6133）→ 已删除，避免两套刷新口径。

async function saveEnvironment() {
  const environment = config.value.editable.okx_environment
  if (environment === 'live') {
    // 批C(2026-09-13)：切 LIVE 是全站最高风险动作（真实资金），原先用 prompt() 收短语
    // ——移动端 prompt 常被弱化，且样式/焦点不可控。改用项目危险操作确认框，
    // 要求逐字输入 LIVE（与其余危险操作同一套门禁语义）。
    const _ok = await ask({
      title: t('admin.security.confirmLiveTitle'),
      desc: t('admin.security.confirmLiveDesc'),
      detail: t('admin.security.confirmLiveDetail'),
      danger: true,
      confirmPhrase: 'LIVE',
      okText: t('common.switchLive'),
    })
    if (!_ok) {
      toast.warn(t('admin.security.warnNotConfirmed'))
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
    toast.ok(t('admin.security.toastEnvSaved', undefined, { env: environment.toUpperCase() }))
    await loadAll()
  } catch (e: any) {
    toast.err(t('admin.security.errSaveFailed', undefined, { msg: e.message }))
  } finally {
    savingOkx.value = false
  }
}

async function saveManualClose() {
  try {
    const d = await api('/api/v1/admin/config', { method: 'PUT', body: JSON.stringify({ manual_close_enabled: manualClose.value }) })
    // 审计①#4(2026-09-13)：PUT 复用 admin_config()，manual_close_enabled 嵌在
    // editable 之下——旧读顶层恒 undefined → 保存后开关弹回 OFF + toast 谎报。
    manualClose.value = !!(d?.editable?.manual_close_enabled ?? d?.manual_close_enabled)
    if (manualClose.value) toast.warn(t('admin.security.toastManualCloseOn')); else toast.ok(t('admin.security.toastManualCloseOff'))
  } catch (e: any) {
    toast.err(e.message)
  }
}

async function saveCapital() {
  if (!auth.isSuperadmin) { toast.err(t('admin.security.errSuperadminOnly')); return }
  if (capitalConfirm.value.trim().toUpperCase() !== 'UPDATE CAPITAL') { toast.err(t('admin.security.errPhraseCapital')); return }
  savingCapital.value = true
  try {
    const res = await api('/api/v1/admin/account-baseline', { method: 'PUT', body: JSON.stringify({ initial_capital: parseFloat(newCapital.value), confirmation: capitalConfirm.value }) })
    toast.ok(res.effect || t('admin.security.toastCapitalSet', undefined, { n: res.initial_capital }))
    capitalConfirm.value = ''
    await loadAll()
  } catch (e: any) {
    toast.err(t('admin.security.errUpdateFailed', undefined, { msg: e.message }))
  } finally {
    savingCapital.value = false
  }
}

async function addInstrument() {
  const instId = newInstId.value.trim().toUpperCase()
  if (!/^[A-Z0-9]{2,15}-USDT-SWAP$/.test(instId)) { toast.err(t('admin.security.errInstFormat')); return }
  try {
    const res = await api('/api/v1/admin/instruments', { method: 'POST', body: JSON.stringify({ inst_id: instId }) })
    toast.ok(res.message || t('admin.security.toastInstAdded', undefined, { inst: instId }))
    newInstId.value = ''
    await loadAll()
  } catch (e: any) {
    toast.err(t('admin.security.errAddFailed', undefined, { msg: e.message }))
  }
}

async function removeInstrument(item: any) {
  if (item.protected) { toast.warn(t('admin.security.warnProtectedInst')); return }
  // 审计 P1-5：后端已按实时持仓/追踪记录硬拒（删除会让该标的失去移动止损/时间止损/AI 平仓接管），
  // 前端不再承诺"既有持仓不受影响"，而是在入口就把真实原因说清楚。
  if (item.held_live || item.has_tracker) {
    toast.warn(t('admin.security.warnHeldInst', undefined, { venues: (item.held_venues || []).join('/') || t('admin.security.trackedRecord') }))
    return
  }
  if (item.holdings_unknown) { toast.warn(t('admin.security.warnHoldingsUnknown')); return }
  // 批C(2026-09-13)·危险操作确认收口：后端本就要求逐字短语 `REMOVE <instId>`，
  // 但前端把短语写死在请求体、只用原生 confirm() 小条挡一下——移动端随手一按就
  // 能把实盘标的移出交易池（同页平仓却要密码+短语双确认，强度不一致）。现将同一
  // 短语要求显式抬到 UI：必须逐字输入才可确认，前后端确认语义就此一致。
  const _ok = await ask({
    title: t('admin.security.confirmRemoveInstTitle'),
    desc: t('admin.security.confirmRemoveInstDesc', undefined, { inst: item.instId }),
    danger: true,
    confirmPhrase: `REMOVE ${item.instId}`,
    okText: t('common.remove'),
  })
  if (!_ok) return
  try {
    const res = await api(`/api/v1/admin/instruments/${encodeURIComponent(item.instId)}`, {
      method: 'DELETE',
      body: JSON.stringify({ confirmation: `REMOVE ${item.instId}` })
    })
    toast.ok(res.message || t('admin.security.toastInstRemoved', undefined, { inst: item.instId }))
    await loadAll()
  } catch (e: any) {
    toast.err(t('admin.security.errDeleteFailed', undefined, { msg: e.message }))
  }
}

async function loadPositions() {
  snapshotState.value = t('admin.security.loadingPositions')
  snapshotError.value = false
  try {
    const d = await api('/api/v1/admin/okx/account-snapshot')
    snapshot.value = d
    snapshotState.value = ''
  } catch (e: any) {
    snapshotState.value = e.message
    snapshotError.value = true
    snapshot.value = null
  }
}

function openClose(pos: any) {
  if (!manualClose.value) { toast.err(t('admin.security.errEnableManualFirst')); return }
  closePhraseInput.value = ''
  closeModal.value = { show: true, pos }
}

async function confirmClose() {
  if (closing.value) return
  const pos = closeModal.value?.pos
  if (!pos) return
  if (!closePassword.value) { toast.err(t('admin.security.errNeedPassword')); return }
  if (!pos.close_token || !pos.close_confirmation) { toast.err(t('admin.security.errCloseTokenMissing')); return }
  if (closePhraseInput.value.trim().toUpperCase() !== pos.close_confirmation) {
    toast.err(t('admin.security.errPhraseClose', undefined, { phrase: pos.close_confirmation }))
    return
  }
  closing.value = true
  try {
    const d = await api('/api/v1/admin/positions/close', {
      method: 'POST',
      body: JSON.stringify({ close_token: pos.close_token, admin_password: closePassword.value, confirmation: closePhraseInput.value.trim().toUpperCase(), venue: pos.venue || 'okx' }),
    })
    toast.ok(t('admin.security.toastCloseConfirmed', undefined, { inst: d.instId, size: d.closed_size }))
    closeModal.value = null
    closePassword.value = ''
    await loadPositions()
  } catch (e: any) {
    toast.err(t('admin.security.errCloseFailed', undefined, { msg: e.message }))
    // 一次性令牌可能已被消费/过期：自动刷新快照，并把弹窗指向新令牌的同仓位行，允许直接重试
    await loadPositions()
    const fresh = (snapshot.value?.positions || []).find((x: any) => x.instId === pos.instId && (x.posSide || 'net') === (pos.posSide || 'net') && (x.venue || 'okx') === (pos.venue || 'okx'))
    if (fresh) closeModal.value = { show: true, pos: fresh }
    else { closeModal.value = null; closePassword.value = '' }
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
      binanceExec.value = !!mx.value.venues.binance?.execution_open
    }
    if (mx.value?.health?.venues) {
      for (const [k, v] of Object.entries(mx.value.health.venues as Record<string, any>)) {
        if (v?.avg_ms) {
          venueLatencies.value[k] = v.avg_ms
        }
      }
    }
    if (mx.value?.preferred_venue) {
      preferredVenue.value = mx.value.preferred_venue
    }
    if (mx.value?.routing_mode) {
      routingMode.value = mx.value.routing_mode
    }
  } catch { mx.value = null }
}

/** 单所凭证连接诊断：支持未保存凭证的预检与公共连通性探测。 */
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

    if (res?.latency_ms) {
      venueLatencies.value[venue] = res.latency_ms
    }

    if (res?.ok) {
      toast.ok(res.message || t('admin.security.toastProbeOk', undefined, { venue: venue.toUpperCase() }))
    } else {
      toast.err(res?.message || t('admin.security.toastProbeFail', undefined, { venue: venue.toUpperCase() }))
    }
    await loadMx()
  } catch (e: any) {
    toast.err(t('admin.security.errProbeFailed', undefined, { msg: e.message }))
  } finally {
    probingVenue.value = ''
  }
}

/** 保存撮合路由首选与模式（只写路由两键，不牵连任何凭证字段）。 */
async function saveRouting() {
  savingMx.value = true
  try {
    await api('/api/v1/admin/multi-exchange', {
      method: 'PUT',
      body: JSON.stringify({ preferred_venue: preferredVenue.value, routing_mode: routingMode.value }),
    })
    toast.ok(t('admin.security.toastRoutingSaved', undefined, { venue: preferredVenue.value.toUpperCase(), mode: routingMode.value.toUpperCase() }))
    await loadMx()
  } catch (e: any) {
    toast.err(t('admin.security.errSaveFailed', undefined, { msg: e.message }))
  } finally {
    savingMx.value = false
  }
}

/** 逐所保存凭证与档位：只提交本所键位，留空即不改；Gate / Binance 承载执行总闸。 */
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
      if (binanceExecDirty.value) {
        body.binance_execution = binanceExec.value
        body.confirmation = binanceExecPhrase.value.trim()
      }
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
    toast.ok(t('admin.security.toastVenueSaved', undefined, { venue: venue === 'binance' ? 'Binance' : 'Gate' }))
    if (venue === 'binance') { mxForm.value.binance_api_key = ''; mxForm.value.binance_secret_key = ''; binanceExecPhrase.value = '' }
    else { mxForm.value.gate_api_key = ''; mxForm.value.gate_secret_key = ''; gateExecPhrase.value = '' }
    await loadMx()
  } catch (e: any) {
    toast.err(t('admin.security.errSaveFailed', undefined, { msg: e.message }))
  } finally {
    savingVenue.value = ''
  }
}

// ---- 总览派生（纯计算，零请求） ----
// 显示派生逻辑已抽至 ./securityLogic.ts（阶段 4·B3 第三十四刀）——
// 纯函数、可脱离组件单测；此处只保留响应式包装。
const okxLinked = computed(() => deriveOkxLinked(runtime.value))
const mxHealthChips = computed(() => deriveMxHealthChips(mx.value))
const gateExecDirty = computed(() => deriveGateExecDirty(gateExec.value, mx.value))
const binanceExecDirty = computed(() => deriveBinanceExecDirty(binanceExec.value, mx.value))

const binanceStatus = computed(() => venueStatus('binance', mx.value, t))
const gateStatus = computed(() => venueStatus('gate', mx.value, t))

const okxEnvText = computed(() => okxEnvTextOf(config.value?.editable?.okx_environment, t))
const binanceEnvText = computed(() => envTextOf('binance', t('admin.security.envDemoBinance'), mx.value, mxTestnet.value, t))
const gateEnvText = computed(() => envTextOf('gate', t('admin.security.envDemoGate'), mx.value, mxTestnet.value, t))

const okxTestnetSwitch = computed({
  get: () => config.value?.editable?.okx_environment !== 'live',
  set: async (val: boolean) => {
    if (!config.value?.editable) return
    if (!val) {
      const _ok = await ask({
        title: t('admin.security.confirmLiveTitle'),
        desc: t('admin.security.confirmLiveDesc'),
        detail: t('admin.security.confirmLiveDetail'),
        danger: true,
        confirmPhrase: 'LIVE',
        okText: t('common.switchLive'),
      })
      if (!_ok) {
        toast.warn(t('admin.security.warnNotConfirmed'))
        return
      }
      config.value.editable.okx_environment = 'live'
      okxCredViewLive.value = true
    } else {
      config.value.editable.okx_environment = 'demo'
      okxCredViewLive.value = false
    }
  }
})

const TABS = computed<Array<{ key: TabKey; label: string; icon: any }>>(() => [
  { key: 'venues', label: t('admin.security.tabVenues'), icon: Route },
  { key: 'pool', label: t('admin.security.tabPool'), icon: Layers },
  { key: 'emergency', label: t('admin.security.tabEmergency'), icon: Zap },
])

/** 路由模式三档（旧版 3 段手写 radio 卡） */
const ROUTING_MODES = [
  { value: 'balanced', labelKey: 'admin.security.modeA', descKey: 'admin.security.modeADesc' },
  { value: 'auto', labelKey: 'admin.security.modeB', descKey: 'admin.security.modeBDesc' },
  { value: 'split', labelKey: 'admin.security.modeC', descKey: 'admin.security.modeCDesc' },
]

/** 手选优先四档（旧版 4 段手写 radio 卡） */
const PREFERRED_VENUES = [
  { value: 'auto', labelKey: 'admin.security.noManual', descKey: 'admin.security.noManualDesc' },
  { value: 'okx', labelKey: 'admin.security.lockOkx', descKey: 'admin.security.lockOkxDesc' },
  { value: 'binance', labelKey: 'admin.security.lockBinance', descKey: 'admin.security.lockBinanceDesc' },
  { value: 'gate', labelKey: 'admin.security.lockGate', descKey: 'admin.security.lockGateDesc' },
]

/** 接入状态带（4 项事实） */
const bandFacts = computed(() => {
  const b = mx.value?.venues?.binance
  const g = mx.value?.venues?.gate
  return [
    {
      icon: ShieldCheck,
      label: t('admin.security.okxApi'),
      value: okxLinked.value ? t('admin.security.okxLinked') : t('admin.security.okxUnconfigured'),
      foot: envBadge(runtime.value?.environment),
      tone: okxLinked.value ? 'is-up' : 'is-down',
    },
    {
      icon: KeyRound,
      label: 'Binance · USDT-M',
      value: b?.has_api_key ? t('admin.security.binanceKeyed') : t('admin.security.publicMarket'),
      foot: mxTestnet.value.binance ? 'DEMO' : 'LIVE',
      tone: b?.has_api_key ? 'is-up' : 'is-warn',
    },
    {
      icon: KeyRound,
      label: t('admin.security.gatePerp'),
      value: g?.has_api_key
        ? (g?.execution_open ? t('admin.security.gateOpenLive') : t('admin.security.gateClosed'))
        : t('admin.security.publicMarket'),
      foot: mxTestnet.value.gate ? 'TESTNET' : 'LIVE',
      tone: g?.has_api_key ? 'is-up' : 'is-warn',
    },
    {
      icon: Layers,
      label: t('admin.security.activePool'),
      value: t('admin.security.poolCount', undefined, { count: instruments.value.length, max: instLimits.value.maximum }),
      foot: t('admin.security.usdtPerp'),
      tone: '',
    },
  ]
})

const healthAllOk = computed(() => {
  const chips = mxHealthChips.value || []
  return chips.length > 0 && chips.every((h: any) => h.ok === h.total)
})

onMounted(() => { loadAll(); loadMx() })
</script>

<template>
  <div class="sc">
    <PageHeader :title="t('nav.admin.security')" :description="t('admin.security.desc')">
      <template #actions>
        <span class="badge badge-accent mono">
          {{ t('admin.security.chipRouting') }} {{ routingMode.toUpperCase() }}
        </span>
        <span class="badge mono">
          {{ t('admin.security.chipPreferred') }} {{ preferredVenue.toUpperCase() }}
        </span>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="loadAll">
          <Loader2 v-if="loading && config" :size="14" class="animate-spin shrink-0" />
          <RefreshCw v-else :size="14" />
          <span>{{ t('common.refresh') }}</span>
        </button>
      </template>
    </PageHeader>

    <!-- 首屏骨架 -->
    <div v-if="loading && !config" class="sc-skel">
      <BaseLoadingAnnounce />
      <div v-for="i in 6" :key="i" class="skeleton skeleton-row" />
    </div>

    <!-- 加载失败：批 24 —— 此前失败只弹一个 3 秒就消失的 toast，
         config 保持 null，模板两个分支都不命中 → 页面只剩页头，一片空白且无重试入口。 -->
    <div v-else-if="loadError && !config" role="alert" class="state-block is-error">
      <span class="state-icon"><AlertTriangle :size="17" /></span>
      <p class="state-title">{{ t('common.loadFailed') }}</p>
      <p class="state-desc">{{ loadError }}</p>
      <button type="button" class="btn btn-ghost btn-sm mt-1" :disabled="loading" @click="loadAll">
        <RefreshCw :size="14" />
        <span>{{ t('common.retry') }}</span>
      </button>
    </div>

    <template v-else-if="config">
      <!-- ══ 接入状态带 ══ -->
      <section class="card band">
        <div v-for="f in bandFacts" :key="f.label" class="fact">
          <span class="fact-label"><component :is="f.icon" :size="12" />{{ f.label }}</span>
          <span class="fact-value" :class="f.tone">{{ f.value }}</span>
          <span class="fact-foot mono">{{ f.foot }}</span>
        </div>
      </section>

      <!-- ══ 页签 ══ -->
      <div class="seg seg-lg sc-tabs" role="tablist" :aria-label="t('admin.security.tabsLabel')">
        <button
          v-for="(tab, ti) in TABS"
          :key="tab.key"
          :ref="setSecTabRef(ti)"
          type="button"
          role="tab"
          :aria-selected="activeTab === tab.key"
          :tabindex="secTabRoving(activeTab === tab.key)"
          :class="{ 'seg-on': activeTab === tab.key }"
          @click="switchTab(tab.key)"
          @keydown="onSecTabKey($event, ti)"
        >
          <component :is="tab.icon" :size="13" aria-hidden="true" />
          <span>{{ tab.label }}</span>
        </button>
      </div>

      <!-- ══════════ 页签 1：交易所与路由 ══════════ -->
      <template v-if="activeTab === 'venues'">
        <SettingsSection :title="t('admin.security.routingTitle')" :description="t('admin.security.routingDesc')" :icon="Route">
          <template #actions>
            <button type="button" class="btn btn-primary btn-sm" :disabled="savingMx" @click="saveRouting">
              <Loader2 v-if="savingMx" :size="13" class="animate-spin shrink-0" />
              <Save v-else :size="13" />
              <span>{{ savingMx ? t('admin.security.saving') : t('admin.security.saveRouting') }}</span>
            </button>
          </template>

          <div class="sc-group">
            <span class="form-label">{{ t('admin.security.routingModeLabel') }}</span>
            <div class="sc-radios sc-radios-3" role="radiogroup" :aria-label="t('admin.security.routingModeLabel')">
              <label
                v-for="m in ROUTING_MODES"
                :key="m.value"
                class="sc-radio"
                :class="{ 'is-on': routingMode === m.value }"
              >
                <input v-model="routingMode" type="radio" name="routing-mode" :value="m.value" />
                <span class="sc-radio-text">
                  <span class="sc-radio-title">{{ t(m.labelKey) }}</span>
                  <span class="sc-radio-desc">{{ t(m.descKey) }}</span>
                </span>
              </label>
            </div>
          </div>

          <div class="sc-group">
            <span class="form-label">{{ t('admin.security.manualLabel') }}</span>
            <div class="sc-radios sc-radios-4" role="radiogroup" :aria-label="t('admin.security.manualLabel')">
              <label
                v-for="v in PREFERRED_VENUES"
                :key="v.value"
                class="sc-radio"
                :class="{ 'is-on': preferredVenue === v.value }"
              >
                <input v-model="preferredVenue" type="radio" name="preferred-venue" :value="v.value" />
                <span class="sc-radio-text">
                  <span class="sc-radio-title">{{ t(v.labelKey) }}</span>
                  <span class="sc-radio-desc">{{ t(v.descKey) }}</span>
                </span>
              </label>
            </div>
          </div>

          <p class="sc-note">
            <span class="label-caps">{{ t('admin.security.routingEffectiveTitle') }}</span>
            <span>
              <b class="mono is-accent">{{ routingMode.toUpperCase() }}</b>
              <template v-if="preferredVenue !== 'auto'">
                {{ t('admin.security.manualTag') }} <b class="mono is-accent">{{ preferredVenue.toUpperCase() }}</b>
              </template>
              — {{ t('admin.security.unconfiguredNote') }}
            </span>
          </p>
        </SettingsSection>

        <!-- 三所凭证 -->
        <SettingsSection :title="t('admin.security.credsTitle')" :description="t('admin.security.credsDesc')" :icon="KeyRound">
          <div class="sc-venues">
            <!-- OKX -->
            <VenueCredentialCard
              :name="t('admin.security.okxName')" :api-label="t('admin.security.okxApiLabel')"
              :status-text="okxLinked ? t('admin.security.okxReady') : t('admin.security.okxNotReady')"
              :tone="okxLinked ? 'up' : 'down'"
              :env-text="okxEnvText" :env-label="t('admin.security.fundEnv')"
            >
              <template #env>
                <div class="field-stack">
                  <span class="form-label">{{ t('admin.security.endpointTier') }}</span>
                  <label class="sc-check">
                    <BaseSwitch v-model="okxTestnetSwitch" :label="t('admin.security.okxDemoDomain')" />
                    <span>{{ t('admin.security.okxDemoDomain') }}</span>
                  </label>
                </div>
              </template>

              <div class="sc-creds">
                <div class="sc-creds-bar">
                  <span class="form-label">
                    {{ (okxCredViewLive ? t('admin.security.liveTrio') : t('admin.security.demoTrio')) }}
                  </span>
                  <button
                    type="button"
                    class="btn btn-quiet btn-sm"
                    @click="okxCredViewLive = !okxCredViewLive"
                  >
                    {{ okxCredViewLive ? t('admin.security.viewDemoCred') : t('admin.security.viewLiveCred') }}
                  </button>
                </div>

                <div v-show="!okxCredViewLive" class="sc-creds-group">
                  <input v-model="keys.demo_key" type="password" :placeholder="t('admin.security.apiKeyKeep')" class="field" :aria-label="t('admin.security.demoKeyAria')" />
                  <input v-model="keys.demo_secret" type="password" :placeholder="t('admin.security.phSecretKey')" class="field" :aria-label="t('admin.security.demoSecretAria')" />
                  <input v-model="keys.demo_pass" type="password" :placeholder="t('admin.security.phPassphrase')" class="field" :aria-label="t('admin.security.demoPassAria')" />
                </div>
                <div v-show="okxCredViewLive" class="sc-creds-group">
                  <input v-model="keys.live_key" type="password" :placeholder="t('admin.security.apiKeyKeep')" class="field" :aria-label="t('admin.security.liveKeyAria')" />
                  <input v-model="keys.live_secret" type="password" :placeholder="t('admin.security.phSecretKey')" class="field" :aria-label="t('admin.security.liveSecretAria')" />
                  <input v-model="keys.live_pass" type="password" :placeholder="t('admin.security.phPassphrase')" class="field" :aria-label="t('admin.security.livePassAria')" />
                </div>
              </div>

              <template #extra>
                <p class="sc-hint"><AlertTriangle :size="11" />{{ t('admin.security.liveConfirmNote') }}</p>
              </template>
              <template #footer-left>
                <span v-if="venueLatencies.okx" class="sc-latency mono num">
                  <Radar :size="12" />
                  <span>{{ venueLatencies.okx }}ms</span>
                </span>
              </template>
              <template #probe>
                <button type="button" class="btn btn-quiet btn-sm" :disabled="probingVenue === 'okx'" @click="probeVenue('okx')">
                  <RefreshCw :size="14" :class="probingVenue === 'okx' ? 'animate-spin shrink-0' : ''" />
                  <span>{{ probingVenue === 'okx' ? t('admin.security.probing') : t('admin.security.detect') }}</span>
                </button>
              </template>
              <template #save>
                <button type="button" class="btn btn-primary btn-sm" :disabled="savingOkx" @click="saveEnvironment">
                  <Loader2 v-if="savingOkx" :size="12" class="animate-spin shrink-0" />
                  <Save v-else :size="12" />
                  <span>{{ savingOkx ? t('admin.security.saving') : t('admin.security.saveOkx') }}</span>
                </button>
              </template>
            </VenueCredentialCard>

            <!-- Binance -->
            <VenueCredentialCard
              :name="t('admin.security.binanceName')" :api-label="t('admin.security.binanceApiLabel')"
              :status-text="binanceStatus.text" :tone="binanceStatus.tone"
              :env-text="binanceEnvText" :env-label="t('admin.security.fundEnv')"
            >
              <template #env>
                <div class="field-stack">
                  <span class="form-label">{{ t('admin.security.endpointTier') }}</span>
                  <label class="sc-check">
                    <BaseSwitch v-model="mxTestnet.binance" :label="t('admin.security.binanceDemoDomain')" />
                    <span>{{ t('admin.security.binanceDemoDomain') }}</span>
                  </label>
                </div>
              </template>

              <div class="sc-creds">
                <span class="form-label">{{ t('admin.security.binanceCredLabel') }}</span>
                <input v-model="mxForm.binance_api_key" type="text" :aria-label="t('admin.security.binanceKeyAria')" :placeholder="t('admin.security.apiKeyKeep')" class="field mono" />
                <input v-model="mxForm.binance_secret_key" type="password" :aria-label="t('admin.security.binanceSecretAria')" :placeholder="t('admin.security.phApiSecret')" class="field" />
              </div>

              <template #extra>
                <div class="sc-gate">
                  <label class="sc-check" :class="{ 'is-danger': binanceExec }">
                    <BaseSwitch v-model="binanceExec" :label="t('admin.security.binanceMaster')" />
                    <span>
                      {{ t('admin.security.binanceMaster') }}
                      <b>{{ mx?.venues?.binance?.execution_open ? t('admin.security.binanceMasterOpen') : t('admin.security.binanceMasterClosed') }}</b>
                    </span>
                  </label>
                  <input
                    v-if="binanceExecDirty && binanceExec"
                    v-model="binanceExecPhrase"
                    :aria-label="t('admin.security.binanceExecPhraseAria')"
                    :placeholder="t('admin.security.binancePhrasePlaceholder')"
                    class="field mono"
                  />
                </div>
                <p class="sc-hint">{{ t('admin.security.binanceExtra') }}</p>
              </template>
              <template #footer-left>
                <span v-if="venueLatencies.binance" class="sc-latency mono num">
                  <Radar :size="12" />
                  <span>{{ venueLatencies.binance }}ms</span>
                </span>
              </template>
              <template #probe>
                <button type="button" class="btn btn-quiet btn-sm" :disabled="probingVenue !== '' && probingVenue !== 'binance'" @click="probeVenue('binance')">
                  <RefreshCw :size="14" />
                  <span>{{ t('admin.security.detect') }}</span>
                </button>
              </template>
              <template #save>
                <button type="button" class="btn btn-primary btn-sm" :disabled="savingVenue !== ''" @click="saveVenue('binance')">
                  <Loader2 v-if="savingVenue === 'binance'" :size="12" class="animate-spin shrink-0" />
                  <Save v-else :size="12" />
                  <span>{{ savingVenue === 'binance' ? t('admin.security.saving') : t('admin.security.saveBinance') }}</span>
                </button>
              </template>
            </VenueCredentialCard>

            <!-- Gate -->
            <VenueCredentialCard
              :name="t('admin.security.gateName')" :api-label="t('admin.security.gateApiLabel')"
              :status-text="gateStatus.text" :tone="gateStatus.tone"
              :env-text="gateEnvText" :env-label="t('admin.security.fundEnv')"
            >
              <template #env>
                <div class="field-stack">
                  <span class="form-label">{{ t('admin.security.endpointTier') }}</span>
                  <label class="sc-check">
                    <BaseSwitch v-model="mxTestnet.gate" :label="t('admin.security.gateSandboxDomain')" />
                    <span>{{ t('admin.security.gateSandboxDomain') }}</span>
                  </label>
                </div>
              </template>

              <div class="sc-creds">
                <span class="form-label">{{ t('admin.security.gateCredLabel') }}</span>
                <input v-model="mxForm.gate_api_key" type="text" :aria-label="t('admin.security.gateKeyAria')" :placeholder="t('admin.security.apiKeyKeep')" class="field mono" />
                <input v-model="mxForm.gate_secret_key" type="password" :aria-label="t('admin.security.gateSecretAria')" :placeholder="t('admin.security.phApiSecret')" class="field" />
              </div>

              <template #extra>
                <div class="sc-gate">
                  <label class="sc-check" :class="{ 'is-danger': gateExec }">
                    <BaseSwitch v-model="gateExec" :label="t('admin.security.gateMaster')" />
                    <span>
                      {{ t('admin.security.gateMaster') }}
                      <b>{{ mx?.venues?.gate?.execution_open ? t('admin.security.gateMasterOpen') : t('admin.security.gateMasterClosed') }}</b>
                    </span>
                  </label>
                  <input
                    v-if="gateExecDirty && gateExec"
                    v-model="gateExecPhrase"
                    :aria-label="t('admin.security.gateExecPhraseAria')"
                    :placeholder="t('admin.security.gatePhrasePlaceholder')"
                    class="field mono"
                  />
                </div>
                <p class="sc-hint">{{ t('admin.security.gateExtra') }}</p>
              </template>
              <template #footer-left>
                <span v-if="venueLatencies.gate" class="sc-latency mono num">
                  <Radar :size="12" />
                  <span>{{ venueLatencies.gate }}ms</span>
                </span>
              </template>
              <template #probe>
                <button type="button" class="btn btn-quiet btn-sm" :disabled="probingVenue !== '' && probingVenue !== 'gate'" @click="probeVenue('gate')">
                  <RefreshCw :size="14" />
                  <span>{{ t('admin.security.detect') }}</span>
                </button>
              </template>
              <template #save>
                <button type="button" class="btn btn-primary btn-sm" :disabled="savingVenue !== ''" @click="saveVenue('gate')">
                  <Loader2 v-if="savingVenue === 'gate'" :size="12" class="animate-spin shrink-0" />
                  <Save v-else :size="12" />
                  <span>{{ savingVenue === 'gate' ? t('admin.security.saving') : t('admin.security.saveGate') }}</span>
                </button>
              </template>
            </VenueCredentialCard>
          </div>
        </SettingsSection>

        <!-- 跨所行情健康 -->
        <SettingsSection :title="t('admin.security.healthTitle')" :description="t('admin.security.healthDesc')" :icon="Activity">
          <template #actions>
            <span class="badge" :class="healthAllOk ? 'badge-up' : 'badge-warn'">
              {{ healthAllOk ? t('admin.security.healthOk') : t('admin.security.healthDegraded') }}
            </span>
            <button type="button" class="btn btn-quiet btn-sm" @click="loadMx">
              <RefreshCw :size="14" />
              <span>{{ t('admin.security.recheck') }}</span>
            </button>
          </template>

          <BaseEmpty v-if="!mxHealthChips" :text="t('admin.security.noHealthData')" />

          <div v-else class="sc-health">
            <div
              v-for="h in mxHealthChips"
              :key="h.name"
              class="sc-health-row"
              :class="{ 'is-ok': h.ok === h.total }"
            >
              <span class="sc-health-name">{{ h.name }}</span>
              <span class="sc-health-stat mono num">{{ h.ok }}/{{ h.total }} {{ t('admin.security.coinsUnit') }}</span>
              <span v-if="h.avg_ms" class="sc-health-ms mono num">{{ h.avg_ms }}ms</span>
              <span v-if="h.testnet" class="badge">{{ t('admin.security.sandboxTag') }}</span>
            </div>
          </div>
        </SettingsSection>
      </template>

      <!-- ══════════ 页签 2：标的池与初始本金 ══════════ -->
      <template v-if="activeTab === 'pool'">
        <SettingsSection :title="t('admin.security.capitalTitle')" :description="t('admin.security.capitalDesc')" :icon="Wallet">
          <template #actions>
            <button type="button"
              class="btn btn-primary btn-sm"
              :disabled="savingCapital || !auth.isSuperadmin || !capitalAmountOk || !capitalConfirmOk"
              @click="saveCapital"
            >
              <Loader2 v-if="savingCapital" :size="13" class="animate-spin shrink-0" />
              <Save v-else :size="13" />
              <span>{{ savingCapital ? t('admin.security.capitalSaving') : t('admin.security.capitalSave') }}</span>
            </button>
          </template>

          <div class="sc-form-2">
            <label class="field-stack">
              <span class="form-label">{{ t('admin.security.capitalAmount') }}</span>
              <input
                v-model="newCapital"
                type="text"
                class="field num"
                :class="{ 'is-bad': !!newCapital && !capitalAmountOk }"
                :aria-invalid="!!newCapital && !capitalAmountOk ? 'true' : undefined"
                inputmode="decimal"
              />
            </label>
            <label class="field-stack">
              <span class="form-label">{{ t('admin.security.capitalConfirmLabel') }}</span>
              <input
                v-model="capitalConfirm"
                type="text"
                autocomplete="off"
                spellcheck="false"
                class="field mono"
                :class="{ 'is-bad': !!capitalConfirm && !capitalConfirmOk }"
                :aria-invalid="!!capitalConfirm && !capitalConfirmOk ? 'true' : undefined"
                placeholder="UPDATE CAPITAL"
              />
            </label>
          </div>

          <p class="sc-hint pad">{{ t('admin.security.capitalFooter') }}</p>
        </SettingsSection>

        <SettingsSection :title="t('admin.security.poolTitle')" :description="t('admin.security.poolDesc')" :icon="Layers">
          <template #actions>
            <input
              v-model="newInstId"
              :aria-label="t('admin.security.instAria')"
              :placeholder="t('admin.security.instPlaceholder')"
              class="field mono sc-inst-input"
              @keyup.enter="addInstrument"
            />
            <button type="button" class="btn btn-primary btn-sm" @click="addInstrument">
              <Layers :size="13" />
              <span>{{ t('admin.security.addInstrument') }}</span>
            </button>
          </template>

          <BaseEmpty v-if="!instruments.length" :text="t('admin.security.poolEmpty')" />

          <div v-else class="sc-rows">
            <div class="sc-row sc-row-head">
              <span>{{ t('admin.security.colInstId') }}</span>
              <span>{{ t('admin.security.colName') }}</span>
              <span>{{ t('admin.security.colType') }}</span>
              <span>{{ t('admin.security.colRisk') }}</span>
              <span />
            </div>

            <article v-for="item in instruments" :key="item.instId" class="sc-row">
              <span class="sc-inst mono num">{{ item.instId }}</span>
              <span class="sc-name truncate">{{ item.name }}</span>
              <span class="sc-type mono">{{ item.ctType || 'SWAP' }}</span>
              <span class="sc-badges">
                <span v-if="item.protected" class="badge badge-warn">{{ t('admin.security.protectedBadge') }}</span>
                <span v-else-if="item.held_live" class="badge badge-accent">
                  {{ t('admin.security.holdingLiveBadge', undefined, { venues: (item.held_venues || []).join('/') || '—' }) }}
                </span>
                <span v-else-if="item.has_tracker" class="badge badge-accent">{{ t('admin.security.holdingBadge') }}</span>
                <span
                  v-else-if="item.holdings_unknown"
                  class="badge badge-warn"
                  :title="String(item.holdings_unknown)"
                >{{ t('admin.security.holdingUnknownBadge') }}</span>
                <span v-else class="badge">{{ t('admin.security.removableBadge') }}</span>
              </span>
              <span class="sc-actions">
                <button type="button"
                  :disabled="item.protected || item.has_tracker || item.held_live || item.holdings_unknown"
                  class="btn btn-quiet btn-icon btn-sm is-danger"
                  :title="item.held_live
                    ? t('admin.security.removeBlockedHoldings', undefined, { venues: (item.held_venues || []).join('/') || '—' })
                    : item.holdings_unknown
                      ? t('admin.security.removeBlockedUnknown')
                      : t('admin.security.removeTitle')"
                  @click="removeInstrument(item)"
                >
                  <Trash2 :size="13" />
                </button>
              </span>
            </article>
          </div>

          <p class="sc-hint pad">{{ t('admin.security.poolFooter', undefined, { max: instLimits.maximum }) }}</p>
        </SettingsSection>
      </template>

      <!-- ══════════ 页签 3：应急风控与持仓 ══════════ -->
      <template v-if="activeTab === 'emergency'">
        <SettingsSection :title="t('admin.security.manualTitle')" :description="t('admin.security.manualDesc')" :icon="Zap">
          <template #actions>
            <button type="button" class="btn btn-quiet btn-sm" @click="saveManualClose">
              <Save :size="13" />
              <span>{{ t('admin.security.saveSwitch') }}</span>
            </button>
          </template>

          <div class="sc-switch-row" :class="{ 'is-danger': manualClose }">
            <BaseSwitch v-model="manualClose" :label="t('admin.security.manualTitle')" />
            <span class="sc-switch-text">
              {{ manualClose ? t('admin.security.manualOn') : t('admin.security.manualOff') }}
            </span>
          </div>
        </SettingsSection>

        <SettingsSection :title="t('admin.security.snapshotTitle')" :description="t('admin.security.snapshotDesc')" :icon="Radar">
          <template #actions>
            <button type="button" class="btn btn-quiet btn-sm" @click="loadPositions">
              <Zap :size="13" />
              <span>{{ t('admin.security.refreshPositions') }}</span>
            </button>
          </template>

          <!-- 批 70：加载中 → status + 旋转图标；失败 → alert + 警示图标（不再转圈） -->
          <p
            v-if="snapshotState"
            class="sc-loading"
            :class="{ 'is-error': snapshotError }"
            :role="snapshotError ? 'alert' : 'status'"
            aria-live="polite"
          >
            <Loader2 v-if="!snapshotError" :size="12" class="animate-spin shrink-0" aria-hidden="true" />
            <AlertTriangle v-else :size="12" aria-hidden="true" />
            {{ snapshotState }}
          </p>

          <p v-else-if="snapshot" class="sc-snap-meta">
            <span class="label-caps">{{ t('admin.security.envWord') }}</span>
            <b :class="snapshot.environment === 'live' ? 'is-down' : 'is-up'">{{ envBadge(snapshot.environment) }}</b>
            <span class="sc-sep">·</span>
            <span>{{ t('admin.security.positionsWord') }} <b class="num">{{ snapshot.positions?.length ?? 0 }}</b></span>
            <span class="sc-sep">·</span>
            <span>{{ t('admin.security.ordersWord') }} <b class="num">{{ snapshot.orders?.length ?? 0 }}</b></span>
            <span class="sc-sep">·</span>
            <span class="mono">{{ fmtDateTime(snapshot.captured_at_ms) }}</span>
          </p>

          <BaseEmpty
            v-if="!snapshot?.positions?.length"
            :text="snapshot ? t('admin.security.noPositions') : t('admin.security.clickRefreshHint')"
          />

          <div v-else class="sc-rows">
            <div class="sc-row sc-pos-row sc-row-head">
              <span>{{ t('admin.security.colPosition') }}</span>
              <span>{{ t('admin.security.colContracts') }}</span>
              <span>{{ t('admin.security.colMode') }}</span>
              <span>{{ t('admin.security.colUpl') }}</span>
              <span />
            </div>

            <article
              v-for="p in snapshot.positions"
              :key="p.instId + p.posSide"
              class="sc-row sc-pos-row"
            >
              <span class="sc-pos-id">
                <b class="mono">{{ p.instId }}</b>
                <span v-if="p.venue" class="badge mono">{{ p.venue }}</span>
                <span class="badge" :class="p.posSide === 'long' ? 'badge-up' : 'badge-down'">
                  {{ (p.posSide || 'net').toUpperCase() }}
                </span>
              </span>
              <span class="sc-contracts num" :title="p.pos ? `${p.pos}` : undefined">
                {{ p.margin ? `${Number(p.margin).toFixed(2)} U` : (p.pos || '0') }}
              </span>
              <span class="sc-mode mono">{{ p.mgnMode || '--' }}</span>
              <span class="sc-upl num" :class="Number(p.upl || 0) >= 0 ? 'is-up' : 'is-down'">
                {{ Number(p.upl || 0).toFixed(4) }}
              </span>
              <span class="sc-actions">
                <button type="button" class="btn btn-danger btn-sm" @click="openClose(p)">
                  {{ t('admin.security.quickClose') }}
                </button>
              </span>
            </article>
          </div>
        </SettingsSection>
      </template>
    </template>

    <!-- ══════════ 平仓双确认 ══════════ -->
    <BaseDialog
      :open="!!closeModal?.show"
      :title="t('admin.security.closeModalTitle')"
      tone="danger"
      size="md"
      initial-focus="input"
      @close="closeModal = null"
    >
      <template v-if="closeModal?.pos">
        <p class="sc-close-desc">
          {{ t('admin.security.closePrefix') }}
          <b :class="snapshot?.environment === 'live' ? 'is-down' : 'is-up'">{{ envBadge(snapshot?.environment) }}</b>
          {{ t('admin.security.closeMiddle') }}
          <b class="sc-close-target mono">
            {{ closeModal.pos.instId }} {{ (closeModal.pos.posSide || 'net').toUpperCase() }}
            {{ Math.abs(Number(closeModal.pos.pos || 0)) }}
          </b>{{ t('admin.security.period') }}
          {{ t('admin.security.closeSuffix') }}
        </p>

        <form id="sc-close-form" class="sc-close-fields" @submit.prevent="confirmClose">
          <label class="field-stack">
            <span class="form-label">{{ t('admin.security.adminPasswordLabel') }}</span>
            <input v-model="closePassword" type="password" autocomplete="current-password" class="field" />
          </label>

          <label class="field-stack">
            <span class="form-label">
              {{ t('admin.security.confirmPhraseLabel') }}
              <code class="sc-close-phrase">{{ closeModal.pos.close_confirmation }}</code>
            </span>
            <input
              v-model="closePhraseInput"
              type="text"
              autocomplete="off"
              spellcheck="false"
              :class="{ 'is-bad': !!closePhraseInput && !closePhraseOk }"
              :aria-invalid="!!closePhraseInput && !closePhraseOk ? 'true' : undefined"
              :placeholder="closeModal.pos.close_confirmation"
              class="field mono"
            />
          </label>
        </form>
      </template>

      <template #footer>
        <button type="button" class="btn btn-ghost btn-sm" @click="closeModal = null">
          {{ t('admin.security.cancel') }}
        </button>
        <!-- 批 115：表单已挂 @submit.prevent="confirmClose"，type="submit" 按钮无需再挂 @click，避免单次点击触发两次平仓请求 -->
        <button class="btn btn-danger btn-sm" type="submit" form="sc-close-form" :disabled="closing || !closeReady">
          <Loader2 v-if="closing" :size="13" class="animate-spin shrink-0" />
          <span>{{ closing ? t('admin.security.closing') : t('admin.security.confirmClose') }}</span>
        </button>
      </template>
    </BaseDialog>
  </div>
</template>

<style scoped>
.sc {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-4);
}
.sc-skel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* ══ 状态带 ══ */










.sc-tabs {
  align-self: flex-start;
}

/* ══ 通用 ══ */
.sc-note {
  display: flex;
  align-items: baseline;
  gap: var(--ds-space-2);
  flex-wrap: wrap;
  margin-top: var(--ds-space-4);
  padding-top: var(--ds-space-3);
  border-top: 1px solid var(--ds-color-border-default);
  font-size: var(--text-3xs);
  line-height: var(--leading-body);
  color: var(--ds-color-text-description);
}
.sc-note .mono.is-accent {
  color: var(--ds-color-brand);
}
.sc-hint {
  display: flex;
  align-items: flex-start;
  gap:6px;
  font-size: var(--text-4xs);
  line-height: var(--leading-body);
  color: var(--ds-color-text-placeholder);
}
.sc-hint > svg {
  flex-shrink: 0;
  margin-top: 2px;
}
.sc-hint.pad {
  margin-top: var(--ds-space-3);
  padding-top: var(--ds-space-3);
  border-top: 1px solid var(--ds-color-border-default);
}

.sc-group + .sc-group {
  margin-top: var(--ds-space-4);
}

/* ══ radio 卡组（旧版 7 段手写卡片 → 数据驱动） ══ */
.sc-radios {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--ds-space-2);
  margin-top:8px;
}
@media (min-width: 620px) {
  .sc-radios-3 {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .sc-radios-4 {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
.sc-radio {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--ds-color-border-default);
  border-left: 2px solid transparent;
  border-radius: var(--r-ctl);
  background-color: var(--ds-color-bg-surface-inset);
  cursor: pointer;
  transition: all var(--dur-fast);
}
.sc-radio:hover {
  background-color: var(--ds-color-bg-hover);
}
.sc-radio.is-on {
  border-left-color: var(--ds-color-brand);
  background-color: var(--r20-brand-bg);
}
.sc-radio input {
  margin-top: 2px;
  accent-color: var(--ds-color-brand);
  flex-shrink: 0;
}
.sc-radio-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.sc-radio-title {
  font-size: var(--text-3xs);
  font-weight: 600;
  color: var(--ds-color-text-primary);
}
.sc-radio-desc {
  font-size: var(--text-4xs);
  line-height: var(--leading-body);
  color: var(--ds-color-text-placeholder);
}

/* ══ 三所凭证 ══ */
.sc-venues {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--ds-space-3);
}
@media (min-width: 900px) {
  .sc-venues {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

.sc-creds {
  display: flex;
  flex-direction: column;
  gap:6px;
}
.sc-creds-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.sc-creds-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.sc-latency {
  font-size: var(--text-3xs);
  color: var(--ds-color-text-secondary);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.sc-check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--text-3xs);
  color: var(--ds-color-text-description);
  cursor: pointer;
  /* 批 101：勾选行此前悬停毫无反馈。与同页 `.sc-radio:hover` / `.sc-row:hover`
     用同一语汇 `--ds-color-bg-hover`（底色而非文字色）—— 这样**危险变体也有反馈**：
     `.sc-check.is-danger span` 权重更高、始终是红的，若只改文字色，
     危险行会静默地「没有变化」（第一版就踩了这个坑）。 */
  border-radius: var(--r-xs);
  transition: background-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out);
}
.sc-check:hover {
  background-color: var(--ds-color-bg-hover);
}
.sc-check:hover:not(.is-danger) {
  color: var(--ds-color-text-primary);
}
.sc-check.is-danger span {
  color: var(--down);
}
.sc-gate {
  display: flex;
  flex-direction: column;
  gap:8px;
}
.sc-gate .field {
  margin-top: 2px;
}

/* ══ 健康 ══ */
.sc-health {
  display: flex;
  flex-direction: column;
}
.sc-health-row {
  display: flex;
  align-items: center;
  gap: var(--ds-space-3);
  padding:10px var(--ds-space-3);
  border-bottom: 1px solid var(--ds-color-border-default);
  border-left: 2px solid var(--warn);
  font-size: var(--text-3xs);
}
.sc-health-row:last-child {
  border-bottom: 0;
}
.sc-health-row.is-ok {
  border-left-color: var(--up);
}
.sc-health-name {
  font-weight: 600;
  color: var(--ds-color-text-primary);
  min-width: 0;
}
.sc-health-stat {
  margin-left: auto;
  color: var(--ds-color-text-secondary);
}
.sc-health-ms {
  color: var(--ds-color-text-placeholder);
}

/* ══ 表单 ══ */
.sc-form-2 {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--ds-space-3);
}
@media (min-width: 700px) {
  .sc-form-2 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.sc-inst-input {
  width: 190px;
}

/* ══ 行式清单 ══ */
.sc-rows {
  display: flex;
  flex-direction: column;
}
.sc-row {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr) 78px minmax(0, 1.3fr) 44px;
  align-items: center;
  gap: var(--ds-space-3);
  padding: 10px var(--ds-space-3);
  border-bottom: 1px solid var(--ds-color-border-default);
  font-size: var(--text-3xs);
}
.sc-row:last-child {
  border-bottom: 0;
}
.sc-row-head {
  min-height: 30px;
  padding-top: 0;
  padding-bottom: 0;
  background-color: var(--ds-color-bg-surface-inset);
  font-size: var(--text-4xs);
  font-weight: 500;
  letter-spacing: var(--track-label);
  text-transform: uppercase;
  color: var(--ds-color-text-placeholder);
}
.sc-row:not(.sc-row-head):hover {
  background-color: var(--ds-color-bg-hover);
}
.sc-inst {
  font-weight: 600;
  color: var(--ds-color-text-primary);
}
.sc-name {
  color: var(--ds-color-text-secondary);
  min-width: 0;
}
.sc-type {
  color: var(--ds-color-text-placeholder);
}
.sc-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 0;
}
.sc-actions {
  display: flex;
  justify-content: flex-end;
}

.sc-pos-row {
  grid-template-columns: minmax(0, 1.6fr) 80px 80px 110px 92px;
}
.sc-pos-id {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}
.sc-pos-id .mono {
  font-weight: 600;
  color: var(--ds-color-text-primary);
}
.sc-contracts,
.sc-mode {
  color: var(--ds-color-text-description);
}
.sc-upl {
  font-weight: 600;
  text-align: right;
}
.sc-upl.is-up {
  color: var(--up);
}
.sc-upl.is-down {
  color: var(--down);
}

@media (max-width: 900px) {
  .sc-row,
  .sc-pos-row {
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .sc-row-head {
    display: none;
  }
  .sc-badges,
  .sc-actions {
    grid-column: 2;
    justify-content: flex-end;
  }
}

/* ══ 应急 ══ */
.sc-switch-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding:12px var(--ds-space-3);
  border: 1px solid var(--ds-color-border-default);
  border-left: 2px solid var(--ds-color-border-strong);
  border-radius: var(--r-ctl);
  background-color: var(--ds-color-bg-surface-inset);
}
.sc-switch-row.is-danger {
  border-left-color: var(--warn);
  background-color: var(--warn-bg);
}
.sc-switch-text {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--ds-color-text-secondary);
}
.sc-switch-row.is-danger .sc-switch-text {
  color: var(--warn);
}

.sc-loading {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-3xs);
  color: var(--ds-color-text-placeholder);
}
/* 批 70：失败态用语义色与左竖线（本页既有语汇），不再沿用占位符灰 */
.sc-loading.is-error {
  color: var(--down);
  padding-left: var(--ds-space-2);
  border-left: 2px solid var(--down);
}
.sc-snap-meta {
  display: flex;
  align-items: baseline;
  gap:8px;
  flex-wrap: wrap;
  margin-bottom: var(--ds-space-3);
  font-size: var(--text-3xs);
  color: var(--ds-color-text-description);
}
.sc-snap-meta b.is-up {
  color: var(--up);
}
.sc-snap-meta b.is-down {
  color: var(--down);
}
.sc-sep {
  color: var(--ds-color-text-placeholder);
}

/* ══ 平仓弹窗 ══ */
.sc-close-desc {
  font-size: var(--text-3xs);
  line-height: var(--leading-body);
  color: var(--ds-color-text-description);
}
.sc-close-desc b.is-up {
  color: var(--up);
}
.sc-close-desc b.is-down {
  color: var(--down);
}
.sc-close-target {
  font-weight: 600;
  color: var(--ds-color-text-primary);
}
.sc-close-fields {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-3);
  margin-top: var(--ds-space-4);
}
.sc-close-phrase {
  padding:1px 6px;
  border-radius: var(--r-xs);
  background-color: var(--down-bg);
  color: var(--down);
  font-family: var(--ds-font-mono);
  font-weight: 600;
}
</style>

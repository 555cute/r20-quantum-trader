<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import {
  Activity, ArrowLeft, Bell, Bot, CheckCircle2, CircleAlert, Clock3, Cpu,
  DatabaseBackup, FileCheck2, FileX2, GitBranch, HardDrive, KeyRound, LogOut,
  Radio, RefreshCw, Save, Server, Settings, ShieldCheck,
  Sparkles, Users, WalletCards, XCircle,
} from '@lucide/vue'
import ThemeToggle from '../components/ThemeToggle.vue'
import SectionHeader from '../components/SectionHeader.vue'
import EmptyState from '../components/EmptyState.vue'
import AdminUsersPanel from '../components/AdminUsersPanel.vue'
import AdminPromptPanel from '../components/AdminPromptPanel.vue'
import AdminNotificationsPanel from '../components/AdminNotificationsPanel.vue'
import AdminBackupPanel from '../components/AdminBackupPanel.vue'
import AdminTradingPanel from '../components/AdminTradingPanel.vue'
import AdminGatewayPanel from '../components/AdminGatewayPanel.vue'
import { api } from '../services/http'

const session = ref(sessionStorage.getItem('r20-admin-session') || '')
const user = ref<any>(null)
const busy = ref(false)
const error = ref('')
const message = ref('')
const active = ref('overview')
const login = reactive({ username: 'admin', password: '' })

const overview = ref<any>({})
const gateway = ref<any>({})
const agents = ref<any>({})
const plugins = ref<any>({})
const config = ref<any>({})
const audit = ref<any>({})
const instruments = ref<any>({})
const notifications = ref<any>({})
const backups = ref<any>({})
const update = ref<any>({})

const editable = reactive<any>({
  okx_environment: 'demo', llm_base_url: '', llm_model: '', llm_reasoning_effort: 'high',
  notification_webhook: '', manual_close_enabled: false, llm_api_key: '',
  okx_demo_api_key: '', okx_demo_secret_key: '', okx_demo_passphrase: '',
})

const nav = [
  ['overview', '系统总览', Activity], ['config', '系统配置', Settings],
  ['gateway', 'Gateway', Server], ['prompts', '提示词库', Sparkles],
  ['notifications', '通知渠道', Bell], ['backups', '灾备系统', DatabaseBackup],
  ['trading', '安全交易', WalletCards], ['users', '管理员', Users],
  ['security', '安全审计', ShieldCheck],
] as any[]

const service = computed(() => overview.value.service || {})
const credentials = computed(() => overview.value.credentials || {})
const dataHealth = computed(() => overview.value.data_health || [])
const overviewAudit = computed(() => overview.value.audit || audit.value.records || [])
const logItems = computed(() => [
  { key: 'trader', name: '交易引擎', icon: WalletCards, text: overview.value.logs?.trader || '暂无日志' },
  { key: 'backend', name: '控制面后端', icon: Server, text: overview.value.logs?.backend || '暂无日志' },
  { key: 'scheduler', name: '任务调度器', icon: Clock3, text: overview.value.logs?.scheduler || '暂无日志' },
])
const channelItems = computed(() => [
  { key: 'webhook', name: 'Webhook', icon: Radio, enabled: notifications.value.webhook?.enabled, configured: Boolean(notifications.value.webhook?.url), detail: notifications.value.webhook?.url },
  { key: 'wechat', name: '企业微信', icon: Bell, enabled: notifications.value.wechat?.enabled, configured: Boolean(notifications.value.wechat?.webhook), detail: notifications.value.wechat?.webhook },
  { key: 'telegram', name: 'Telegram', icon: Radio, enabled: notifications.value.telegram?.enabled, configured: Boolean(notifications.value.telegram?.bot_token && notifications.value.telegram?.chat_id), detail: notifications.value.telegram?.chat_id },
  { key: 'qq', name: 'QQ Bot', icon: Bot, enabled: notifications.value.qq?.enabled, configured: Boolean(notifications.value.qq?.app_id && notifications.value.qq?.openid), detail: notifications.value.qq?.openid },
])

function notify(text: string, isError = false) {
  ;(isError ? error : message).value = text
  window.setTimeout(() => { error.value = ''; message.value = '' }, 3500)
}
function formatUptime(value: any) {
  const seconds = Number(value || 0); const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600); const minutes = Math.floor((seconds % 3600) / 60)
  return `${days ? `${days}天 ` : ''}${hours}小时 ${minutes}分钟`
}
function formatAge(value: any) {
  if (value === null || value === undefined) return '从未生成'
  const seconds = Number(value); if (seconds < 60) return `${Math.round(seconds)} 秒前`
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`
  return `${Math.round(seconds / 3600)} 小时前`
}
function detailText(detail: any) {
  if (!detail || typeof detail !== 'object') return String(detail || '—')
  return Object.entries(detail).map(([key, value]) => `${key}: ${value}`).join(' · ')
}
function maskDestination(value: any) {
  const text = String(value || '')
  if (!text) return '尚未配置'
  if (text.length < 18) return text
  return `${text.slice(0, 9)}••••${text.slice(-6)}`
}
function auditTitle(action: string) {
  const labels: Record<string, string> = {
    'admin.login': '管理员登录', 'admin.logout': '管理员退出', 'admin.user.create': '创建管理员',
    'prompt.profile.create': '创建提示词方案', 'prompt.profile.update': '更新提示词方案',
    'prompt.profile.activate': '启用提示词方案', 'backup.job.create': '创建灾备任务',
    'backup.job.update': '更新灾备任务', 'backup.simple.update': '更新快捷灾备',
  }
  return labels[action] || action || '系统事件'
}

async function signIn() {
  busy.value = true; error.value = ''
  try {
    const result: any = await api('/api/v1/admin/auth/login', { method: 'POST', body: JSON.stringify(login) })
    session.value = result.session_token; sessionStorage.setItem('r20-admin-session', result.session_token)
    user.value = result.user; await loadAll()
  } catch (e: any) { notify(e.message, true) } finally { busy.value = false }
}
async function signOut() {
  try { await api('/api/v1/admin/auth/logout', { method: 'POST' }) } catch (_) {}
  sessionStorage.removeItem('r20-admin-session'); sessionStorage.removeItem('r20.admin.session.id'); session.value = ''; user.value = null
}
async function safe(url: string) { try { return await api(url) } catch (e: any) { return { error: e.message } } }
async function loadAll() {
  busy.value = true
  try {
    const me: any = await api('/api/v1/admin/auth/me'); user.value = me.user
    const [o, g, a, p, c, au, i, n, b, u] = await Promise.all([
      safe('/api/v1/admin/overview'), safe('/api/v1/admin/gateway'), safe('/api/v1/admin/agents'),
      safe('/api/v1/admin/plugins'), safe('/api/v1/admin/config'), safe('/api/v1/admin/audit'),
      safe('/api/v1/admin/instruments'), safe('/api/v1/admin/notifications'),
      safe('/api/v1/admin/backups/simple'), safe('/api/v1/admin/update-status'),
    ])
    overview.value = o; gateway.value = g; agents.value = a; plugins.value = p; config.value = c
    audit.value = au; instruments.value = i; notifications.value = n; backups.value = b; update.value = u
    Object.assign(editable, c.editable || {})
  } catch (e: any) {
    if (String(e.message).includes('401') || String(e.message).includes('会话')) await signOut()
    else notify(e.message, true)
  } finally { busy.value = false }
}
async function saveConfig() {
  busy.value = true
  try {
    const payload: any = {
      okx_environment: editable.okx_environment, llm_base_url: editable.llm_base_url,
      llm_model: editable.llm_model, llm_reasoning_effort: editable.llm_reasoning_effort,
      notification_webhook: editable.notification_webhook, manual_close_enabled: editable.manual_close_enabled,
    }
    for (const key of ['llm_api_key', 'okx_demo_api_key', 'okx_demo_secret_key', 'okx_demo_passphrase']) if (editable[key]) payload[key] = editable[key]
    await api('/api/v1/admin/config', { method: 'PUT', body: JSON.stringify(payload) })
    editable.llm_api_key = editable.okx_demo_api_key = editable.okx_demo_secret_key = editable.okx_demo_passphrase = ''
    notify('配置已保存'); await loadAll()
  } catch (e: any) { notify(e.message, true) } finally { busy.value = false }
}
onMounted(async () => { if (session.value) await loadAll() })
</script>

<template>
  <div class="min-h-screen bg-app">
    <header class="sticky top-0 z-40 border-b border-theme bg-app/95 backdrop-blur-xl">
      <div class="mx-auto flex h-[60px] max-w-[1560px] items-center justify-between gap-2 px-2.5 min-[380px]:px-3 sm:px-6">
        <div class="flex min-w-0 items-center gap-2 min-[380px]:gap-3">
          <div class="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-blue-600 text-white"><ShieldCheck :size="17" /></div>
          <div class="min-w-0"><b class="block truncate font-mono text-xs min-[380px]:text-sm">R20 <span class="hidden min-[390px]:inline">CONTROL PLANE</span></b><p class="hidden text-[9px] muted sm:block">v6.0.0 Preview · Vue Console</p></div>
        </div>
        <div class="flex shrink-0 items-center gap-1.5 sm:gap-2">
          <ThemeToggle />
          <RouterLink class="btn !h-10 !min-h-0 !w-10 !px-0 sm:!w-auto sm:!px-3" to="/terminal/trading" title="返回终端"><ArrowLeft :size="15" /><span class="hidden sm:inline">返回终端</span></RouterLink>
          <button v-if="session" class="btn !h-10 !min-h-0 !w-10 !px-0" title="退出登录" @click="signOut"><LogOut :size="15" /></button>
        </div>
      </div>
    </header>

    <div v-if="!session" class="mx-auto grid min-h-[calc(100vh-60px)] max-w-md place-items-center px-4">
      <form class="panel key-card key-card-brand w-full p-6 sm:p-8" @submit.prevent="signIn">
        <span class="data-pill data-pill-profit"><span class="pulse-dot"></span>SECURE ADMIN</span>
        <h1 class="mt-4 text-2xl font-black">管理员登录</h1><p class="mt-2 text-sm leading-6 muted">首次登录账号为 admin，密码使用本地 R20_SETUP_TOKEN。</p>
        <label class="label mt-6">管理员账号</label><input v-model="login.username" class="input mb-4" autocomplete="username" />
        <label class="label">密码</label><input v-model="login.password" class="input" type="password" autocomplete="current-password" />
        <p v-if="error" class="mt-3 text-xs text-loss">{{ error }}</p><button class="btn btn-primary mt-5 w-full" :disabled="busy">{{ busy ? '正在验证…' : '安全登录' }}</button>
      </form>
    </div>

    <div v-else class="mx-auto grid max-w-[1560px] gap-3 px-2.5 py-3 min-[380px]:px-3 md:grid-cols-[220px_1fr] sm:gap-4 sm:px-6 sm:py-4">
      <aside class="panel h-fit p-3 md:sticky md:top-[76px]">
        <div class="border-b divider p-2 pb-4"><p class="text-xs font-bold">{{ user?.username }}</p><div class="mt-2 flex gap-1.5"><span class="badge badge-profit">{{ user?.role }}</span><span class="badge">SESSION ACTIVE</span></div></div>
        <nav class="mt-3 grid grid-cols-3 gap-1 min-[480px]:grid-cols-6 md:grid-cols-1">
          <button v-for="item in nav" :key="item[0]" class="nav-link min-h-11 justify-center !px-1 text-[10px] min-[380px]:text-[11px] md:justify-start md:!px-3 md:text-xs" :class="active === item[0] ? 'router-link-active' : ''" @click="active = item[0]"><component :is="item[2]" :size="14" /><span>{{ item[1] }}</span></button>
        </nav>
      </aside>

      <main class="min-w-0 space-y-4">
        <div v-if="message || error" class="panel border-l-4 p-3 text-sm" :style="{ borderLeftColor: error ? 'var(--loss)' : 'var(--profit)' }">{{ error || message }}</div>

        <template v-if="active === 'overview'">
          <section class="grid gap-3 min-[460px]:grid-cols-2 xl:grid-cols-4">
            <article class="panel key-card key-card-brand p-4"><div class="flex items-center justify-between"><span class="data-pill data-pill-brand"><Server :size="12" />服务版本</span><span class="badge badge-profit"><span class="pulse-dot"></span>运行中</span></div><strong class="key-number mt-4 block text-xl text-brand">v{{ service.version || '--' }}</strong><p class="mt-2 text-[11px] muted">PID {{ service.pid || '--' }} · {{ formatUptime(service.uptime_seconds) }}</p></article>
            <article class="panel key-card" :class="credentials.okx ? 'key-card-profit' : 'key-card-loss'" ><div class="p-4"><span class="data-pill" :class="credentials.okx ? 'data-pill-profit' : 'data-pill-loss'"><KeyRound :size="12" />OKX 凭证</span><strong class="key-number mt-4 block text-xl" :class="credentials.okx ? 'text-profit' : 'text-loss'">{{ credentials.okx ? '已配置' : '未配置' }}</strong><p class="mt-2 text-[11px] muted">交易所账户与持仓数据源</p></div></article>
            <article class="panel key-card" :class="credentials.llm ? 'key-card-profit' : 'key-card-loss'"><div class="p-4"><span class="data-pill" :class="credentials.llm ? 'data-pill-profit' : 'data-pill-loss'"><Sparkles :size="12" />LLM 凭证</span><strong class="key-number mt-4 block text-xl" :class="credentials.llm ? 'text-profit' : 'text-loss'">{{ credentials.llm ? '已配置' : '未配置' }}</strong><p class="mt-2 text-[11px] muted">AI 推演和自进化模型入口</p></div></article>
            <article class="panel key-card key-card-warning p-4"><span class="data-pill data-pill-warning"><Cpu :size="12" />运行数据</span><div class="mt-4 flex items-end justify-between"><div><strong class="key-number text-2xl text-warning">{{ overview.decisions?.length || 0 }}</strong><p class="text-[10px] muted">AI 决策</p></div><div class="text-right"><strong class="key-number text-2xl text-warning">{{ overview.trackers || 0 }}</strong><p class="text-[10px] muted">持仓 Tracker</p></div></div></article>
          </section>

          <section class="panel p-4 sm:p-5"><SectionHeader title="核心数据健康" subtitle="交易引擎持久化文件状态" :count="`${dataHealth.filter((x:any)=>x.fresh).length}/${dataHealth.length} 正常`" />
            <div class="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-4"><article v-for="item in dataHealth" :key="item.name" class="subpanel key-card p-3.5" :class="item.fresh ? 'key-card-profit' : item.exists ? 'key-card-warning' : 'key-card-loss'"><div class="flex items-start justify-between gap-2"><component :is="item.fresh ? FileCheck2 : FileX2" :size="18" :class="item.fresh ? 'text-profit' : 'text-loss'" /><span class="badge" :class="item.fresh ? 'badge-profit' : item.exists ? 'badge-warning' : 'badge-loss'">{{ item.fresh ? '新鲜' : item.exists ? '已过期' : '缺失' }}</span></div><b class="mt-3 block break-all font-mono text-xs">{{ item.name }}</b><p class="mt-2 text-[10px] muted">{{ formatAge(item.age_seconds) }}</p></article></div>
          </section>

          <section class="panel p-4 sm:p-5"><SectionHeader title="运行日志" subtitle="交易、后端与调度器最近输出" /><div class="grid gap-3 xl:grid-cols-3"><article v-for="log in logItems" :key="log.key" class="subpanel key-card key-card-brand overflow-hidden"><div class="flex items-center gap-2 border-b divider px-3 py-2.5"><component :is="log.icon" :size="14" class="text-brand" /><b class="text-xs">{{ log.name }}</b></div><pre class="max-h-52 min-h-28 overflow-auto whitespace-pre-wrap p-3 text-[10px] leading-5 muted">{{ log.text }}</pre></article></div></section>

          <section class="grid gap-4 xl:grid-cols-[1.25fr_.75fr]">
            <div class="panel p-4 sm:p-5"><SectionHeader title="最近安全审计" subtitle="管理员和系统配置变更记录" :count="overviewAudit.length" /><div v-if="overviewAudit.length" class="space-y-2"><article v-for="(record, index) in overviewAudit.slice(0, 10)" :key="index" class="subpanel flex gap-3 p-3"><span class="mt-1 h-2 w-2 shrink-0 rounded-full" :style="{background: record.status === 'success' ? 'var(--profit)' : 'var(--loss)'}"></span><div class="min-w-0 flex-1"><div class="flex flex-wrap items-center justify-between gap-2"><b class="text-xs">{{ auditTitle(record.action) }}</b><span class="data-pill" :class="record.status === 'success' ? 'data-pill-profit' : 'data-pill-loss'">{{ record.status }}</span></div><p class="mt-1 truncate text-[10px] muted">{{ detailText(record.detail) }}</p><time class="mt-1 block text-[9px] font-mono muted">{{ record.timestamp }}</time></div></article></div><EmptyState v-else text="暂无审计记录" /></div>
            <div class="panel p-4 sm:p-5"><SectionHeader title="代码版本" subtitle="本地与远程仓库状态"><template #actions><button class="btn !h-9" @click="loadAll"><RefreshCw :size="14" />刷新</button></template></SectionHeader><div class="space-y-2.5"><div class="subpanel flex items-center justify-between p-3"><span class="muted text-xs"><GitBranch :size="13" class="mr-1 inline" />当前分支</span><b class="data-pill data-pill-brand">{{ update.branch || '--' }}</b></div><div class="subpanel flex items-center justify-between p-3"><span class="muted text-xs">本地提交</span><b class="font-mono text-brand">{{ update.local || '--' }}</b></div><div class="subpanel flex items-center justify-between p-3"><span class="muted text-xs">远程提交</span><b class="font-mono">{{ update.remote || '--' }}</b></div><div class="grid grid-cols-2 gap-2"><div class="subpanel p-3 text-center"><b class="key-number text-xl">{{ update.ahead || 0 }}</b><p class="text-[9px] muted">领先提交</p></div><div class="subpanel p-3 text-center"><b class="key-number text-xl">{{ update.behind || 0 }}</b><p class="text-[9px] muted">落后提交</p></div></div><div class="data-pill w-full justify-center" :class="update.dirty ? 'data-pill-warning' : 'data-pill-profit'"><CircleAlert v-if="update.dirty" :size="12" /><CheckCircle2 v-else :size="12" />{{ update.dirty ? '存在本地未提交修改' : '工作区干净' }}</div></div></div>
          </section>
        </template>

        <section v-else-if="active === 'config'" class="panel p-4 sm:p-5"><SectionHeader title="核心系统配置" subtitle="密钥留空表示保持现有配置不变"><template #actions><button class="btn btn-primary" @click="saveConfig"><Save :size="14" />保存配置</button></template></SectionHeader><div class="mb-4 grid gap-2 min-[460px]:grid-cols-2 lg:grid-cols-4"><div v-for="(value, key) in config.configuration || {}" :key="key" class="subpanel key-card key-card-brand p-3"><span class="label">{{ key }}</span><b class="text-xs" :class="String(value).includes('未') || String(value).includes('禁用') ? 'text-loss' : 'text-profit'">{{ value }}</b></div></div><div class="grid gap-4 lg:grid-cols-2"><div><label class="label">OKX 环境</label><select v-model="editable.okx_environment" class="input"><option value="demo">模拟盘 DEMO</option><option value="live">实盘 LIVE</option></select></div><div><label class="label">推理强度</label><select v-model="editable.llm_reasoning_effort" class="input"><option>low</option><option>medium</option><option>high</option></select></div><div><label class="label">LLM Base URL</label><input v-model="editable.llm_base_url" class="input" /></div><div><label class="label">LLM Model</label><input v-model="editable.llm_model" class="input" /></div><div><label class="label">新的 LLM API Key</label><input v-model="editable.llm_api_key" class="input" type="password" placeholder="留空不修改" /></div><div><label class="label">通知 Webhook</label><input v-model="editable.notification_webhook" class="input" /></div><div><label class="label">DEMO API Key</label><input v-model="editable.okx_demo_api_key" class="input" type="password" placeholder="留空不修改" /></div><div><label class="label">DEMO Secret</label><input v-model="editable.okx_demo_secret_key" class="input" type="password" placeholder="留空不修改" /></div><div><label class="label">DEMO Passphrase</label><input v-model="editable.okx_demo_passphrase" class="input" type="password" placeholder="留空不修改" /></div><label class="subpanel flex items-center justify-between p-4"><span><b class="text-sm">允许手动平仓</b><p class="muted text-[10px]">高风险操作，仍需二次确认</p></span><input v-model="editable.manual_close_enabled" type="checkbox" /></label></div></section>

        <AdminGatewayPanel v-else-if="active === 'gateway'" />
        <template v-else-if="false">
          <section class="grid gap-3 min-[460px]:grid-cols-2 xl:grid-cols-4"><article class="panel key-card" :class="gateway.running ? 'key-card-profit' : 'key-card-loss'"><div class="p-4"><span class="data-pill" :class="gateway.running ? 'data-pill-profit' : 'data-pill-loss'"><span class="pulse-dot"></span>Gateway</span><strong class="key-number mt-4 block text-xl" :class="gateway.running ? 'text-profit' : 'text-loss'">{{ gateway.running ? '运行中' : '已停止' }}</strong><p class="mt-2 text-[10px] muted">PID {{ gateway.pid || '--' }} · v{{ gateway.version || '--' }}</p></div></article><article v-for="(value, key) in gateway.stats || {}" :key="key" class="panel key-card key-card-brand p-4"><span class="data-pill data-pill-brand">{{ key }}</span><strong class="key-number mt-4 block text-2xl text-brand">{{ value }}</strong><p class="mt-1 text-[10px] muted">投递任务</p></article></section>
          <section class="panel p-4 sm:p-5"><SectionHeader title="Agent 运行状态" subtitle="交易、同步和自进化执行单元" :count="agents.agents?.length || 0" /><div class="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-3"><article v-for="agent in agents.agents || []" :key="agent.id || agent.name" class="subpanel key-card p-3.5" :class="agent.running || agent.status === 'running' ? 'key-card-profit' : 'key-card-brand'"><div class="flex justify-between gap-2"><b class="text-xs">{{ agent.name || agent.id }}</b><span class="badge" :class="agent.running || agent.status === 'running' ? 'badge-profit' : ''">{{ agent.status || (agent.running ? 'running' : 'idle') }}</span></div><p class="mt-2 text-[10px] muted">{{ agent.description || agent.script || 'R20 内置执行单元' }}</p></article></div></section>
          <section class="panel p-4 sm:p-5"><SectionHeader title="内置插件" :subtitle="plugins.reason || '仅允许内置可信插件'" :count="plugins.plugins?.length || 0" /><div class="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-3"><article v-for="plugin in plugins.plugins || []" :key="plugin.name || plugin.id" class="subpanel flex items-center justify-between gap-3 p-3"><div><b class="text-xs">{{ plugin.name || plugin.id }}</b><p class="mt-1 text-[9px] muted">{{ plugin.description || plugin.type || 'builtin' }}</p></div><span class="badge" :class="plugin.enabled === false ? 'badge-loss' : 'badge-profit'">{{ plugin.enabled === false ? '禁用' : '可用' }}</span></article></div></section>
        </template>

        <AdminPromptPanel v-else-if="active === 'prompts'" />
        <AdminNotificationsPanel v-else-if="active === 'notifications'" />
        <AdminBackupPanel v-else-if="active === 'backups'" />
        <AdminTradingPanel v-else-if="active === 'trading'" />
        <AdminUsersPanel v-else-if="active === 'users'" :current-user="user" />

        <section v-else-if="false" class="space-y-4"><div class="grid gap-3 min-[460px]:grid-cols-2 xl:grid-cols-4"><article class="panel key-card" :class="backups.enabled ? 'key-card-profit' : 'key-card-loss'"><div class="p-4"><span class="data-pill" :class="backups.enabled ? 'data-pill-profit' : 'data-pill-loss'"><DatabaseBackup :size="12" />灾备任务</span><strong class="key-number mt-4 block text-xl" :class="backups.enabled ? 'text-profit' : 'text-loss'">{{ backups.enabled ? '已启用' : '已关闭' }}</strong><p class="mt-2 text-[10px] muted">{{ backups.job_id || '--' }}</p></div></article><article class="panel key-card key-card-brand p-4"><span class="data-pill data-pill-brand"><Clock3 :size="12" />执行计划</span><strong class="key-number mt-4 block text-xl text-brand">{{ backups.schedule_time || '--' }}</strong><p class="mt-2 text-[10px] muted">每日自动执行</p></article><article class="panel key-card key-card-warning p-4"><span class="data-pill data-pill-warning"><HardDrive :size="12" />目标位置</span><strong class="key-number mt-4 block text-xl text-warning">{{ backups.destination || '--' }}</strong><p class="mt-2 text-[10px] muted">保留 {{ backups.retention || 0 }} 份归档</p></article><article class="panel key-card" :class="backups.validation?.valid ? 'key-card-profit' : 'key-card-loss'"><div class="p-4"><span class="data-pill" :class="backups.validation?.valid ? 'data-pill-profit' : 'data-pill-loss'">配置校验</span><strong class="key-number mt-4 block text-xl" :class="backups.validation?.valid ? 'text-profit' : 'text-loss'">{{ backups.validation?.valid ? '通过' : '失败' }}</strong><p class="mt-2 text-[10px] muted">{{ backups.configured ? '凭证已配置' : '无需凭证或尚未配置' }}</p></div></article></div><div class="panel p-4 sm:p-5"><SectionHeader title="灾备目标详情" /><div class="grid gap-2.5 sm:grid-cols-2"><div v-for="(value, key) in backups.target || {}" :key="key" class="subpanel flex items-center justify-between gap-3 p-3"><span class="text-xs muted">{{ key }}</span><b class="font-mono text-xs">{{ value }}</b></div></div><div v-if="backups.validation?.warnings?.length" class="mt-3 data-pill data-pill-warning">{{ backups.validation.warnings.join(' · ') }}</div></div></section>

        <template v-else>
          <section class="panel p-4 sm:p-5"><SectionHeader title="安全审计" subtitle="管理员操作和关键配置变更" :count="(audit.records || overviewAudit).length" /><div v-if="(audit.records || overviewAudit).length" class="space-y-2"><article v-for="(record, index) in (audit.records || overviewAudit)" :key="index" class="subpanel flex gap-3 p-3"><span class="mt-1 h-2 w-2 shrink-0 rounded-full" :style="{ background: record.status === 'success' ? 'var(--profit)' : 'var(--loss)' }"></span><div class="min-w-0 flex-1"><div class="flex flex-wrap justify-between gap-2"><b class="text-xs">{{ auditTitle(record.action) }}</b><span class="data-pill" :class="record.status === 'success' ? 'data-pill-profit' : 'data-pill-loss'">{{ record.status }}</span></div><p class="mt-1 text-[10px] muted">{{ detailText(record.detail) }}</p><time class="mt-1 block text-[9px] font-mono muted">{{ record.timestamp }}</time></div></article></div><EmptyState v-else text="暂无安全审计记录" /></section>
          <section class="panel p-4 sm:p-5"><SectionHeader title="交易标的池" :subtitle="`允许 ${instruments.limits?.minimum || 1}-${instruments.limits?.maximum || 6} 个标的，BTC 必选`" :count="instruments.instruments?.length || 0" /><div class="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-3"><article v-for="item in instruments.instruments || []" :key="item.instId" class="subpanel key-card p-4" :class="item.protected ? 'key-card-profit' : 'key-card-brand'"><div class="flex justify-between"><div><b class="font-mono text-sm">{{ item.name }}</b><p class="mt-1 text-[9px] muted">{{ item.instId }}</p></div><span class="badge" :class="item.protected ? 'badge-profit' : ''">{{ item.protected ? '受保护' : '监控中' }}</span></div><div class="mt-3 grid grid-cols-2 gap-2 text-[10px] font-mono"><span class="data-pill">基础张数 <b>{{ item.base_sz }}</b></span><span class="data-pill">风险 <b>{{ item.risk_per_trade_usd }}U</b></span><span class="data-pill">面值 <b>{{ item.ctVal }}</b></span><span class="data-pill" :class="item.has_tracker ? 'data-pill-profit' : ''">Tracker <b>{{ item.has_tracker ? '在线' : '无' }}</b></span></div></article></div></section>
        </template>
      </main>
    </div>
  </div>
</template>

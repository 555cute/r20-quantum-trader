<script setup lang="ts">
import { useToast } from '../../composables/useToast'
const toast = useToast()
import { ref, computed, onMounted , watch} from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
import { useApi } from '../../composables/useApi'
import { MessageCircle, Zap, CheckCircle2, AlertCircle } from 'lucide-vue-next'

const { api } = useApi()
const config = ref<any>(null)
const loading = ref(true)
const testResults = ref<Record<string, any>>({})
const captureModal = ref(false)
const captureStatus = ref<any>(null)
let captureTimer: any = null

const enabledChannelsCount = computed(() => {
  if (!config.value) return 0
  return ['qq', 'telegram', 'wechat', 'webhook'].filter(k => config.value[k]?.enabled).length
})

async function loadConfig(silent = false) {
  if (!silent) loading.value = true
  try {
    const res = await api('/api/v1/admin/notifications')
    // Preserve local un-submitted secret inputs if any
    if (config.value) {
      if (config.value.qq?._secret) res.qq._secret = config.value.qq._secret
      if (config.value.telegram?._token) res.telegram._token = config.value.telegram._token
    }
    const schedule = await api('/api/v1/admin/notifications/schedule')
    res._briefingTimes = schedule.briefing_times?.join(', ') || ''
    config.value = res
  } catch (e: any) {
    console.error(e)
    toast.err('加载通知配置失败: ' + (e.message || String(e)))
  } finally {
    if (!silent) loading.value = false
  }
}

async function toggleChannel(channel: string, enabled: boolean) {
  try {
    const payload: any = { enabled }
    if (config.value) {
      if (channel === 'wechat' && config.value.wechat?.webhook) payload.wechat_webhook = config.value.wechat.webhook
      if (channel === 'webhook' && config.value.webhook?.url) payload.webhook_url = config.value.webhook.url
      if (channel === 'telegram') {
        if (config.value.telegram?._token) payload.telegram_bot_token = config.value.telegram._token
        if (config.value.telegram?.chat_id) payload.telegram_chat_id = config.value.telegram.chat_id
        if (config.value.telegram?.api_base) payload.telegram_api_base = config.value.telegram.api_base
      }
      if (channel === 'qq') {
        if (config.value.qq?.app_id) payload.qq_app_id = config.value.qq.app_id
        if (config.value.qq?._secret) payload.qq_client_secret = config.value.qq._secret
        if (config.value.qq?.openid) payload.qq_openid = config.value.qq.openid
      }
      // Optimistically flip visual state immediately
      if (config.value[channel]) {
        config.value[channel].enabled = enabled
      }
    }
    const res = await api(`/api/v1/admin/channels/${channel}/toggle`, { method: 'PUT', body: JSON.stringify(payload) })
    toast.ok(res.message || `${channel} 通道已成功${enabled ? '开启' : '关闭'}`)
    await loadConfig(true)
  } catch (e: any) {
    toast.err(e.message || '通道状态切换失败')
    await loadConfig(true)
  }
}

async function saveAll() {
  try {
    const body: any = {
      webhook_enabled: config.value.webhook.enabled,
      webhook_url: config.value.webhook.url,
      wechat_enabled: config.value.wechat.enabled,
      wechat_webhook: config.value.wechat.webhook,
      telegram_enabled: config.value.telegram.enabled,
      telegram_bot_token: config.value.telegram._token || undefined,
      telegram_chat_id: config.value.telegram.chat_id,
      telegram_api_base: config.value.telegram.api_base || undefined,
      qq_enabled: config.value.qq.enabled,
      qq_app_id: config.value.qq.app_id,
      qq_client_secret: config.value.qq._secret || undefined,
      qq_openid: config.value.qq.openid,
    }
    const res = await api('/api/v1/admin/notifications', { method: 'PUT', body: JSON.stringify(body) })
    toast.ok(res.message || '全部通知通道配置已保存')
    await loadConfig(true)
  } catch (e: any) {
    toast.err(e.message || '保存配置失败')
  }
}

async function diagnose(channel: string) {
  try {
    const res = await api('/api/v1/admin/notifications/diagnose', { method: 'POST', body: JSON.stringify({ channel }) })
    testResults.value[channel] = res.result
  } catch (e: any) {
    testResults.value[channel] = { status: 'failed', detail: e.message }
  }
}

async function startCapture() {
  try {
    const res = await api('/api/v1/admin/notifications/qq/capture-openid/start', { method: 'POST', body: JSON.stringify({ timeout: 60 }) })
    captureModal.value = true
    captureStatus.value = res
    pollCapture(res.capture_id)
  } catch (e: any) {
    alert(e.message)
  }
}

function pollCapture(captureId: string) {
  if (captureTimer) clearInterval(captureTimer)
  captureTimer = setInterval(async () => {
    try {
      const res = await api(`/api/v1/admin/notifications/qq/capture-openid/${captureId}`)
      captureStatus.value = res
      if (res.status === 'captured' || res.status === 'expired' || res.status === 'failed') {
        clearInterval(captureTimer)
        captureTimer = null
        if (res.status === 'captured') {
          await loadConfig()
          setTimeout(() => { captureModal.value = false }, 1800)
        }
      }
    } catch (e: any) {
      clearInterval(captureTimer)
      captureTimer = null
    }
  }, 1500)
}

// ---- QQ scan bind ----
const bindModal = ref(false)
const bindStatus = ref<any>(null)
let bindTimer: any = null
let bindTaskId = ''

function stopBindPolling() {
  if (bindTimer) { clearInterval(bindTimer); bindTimer = null }
}

async function startQqBind() {
  try {
    const d = await api('/api/v1/admin/notifications/qq/bind/start', { method: 'POST', body: '{}' })
    bindTaskId = d.task_id
    bindStatus.value = { qr: d.qr_data_uri || '', link: d.qr_data_uri ? '' : (d.connect_url || ''), text: `等待扫码…（${d.expires_in} 秒内有效）`, tone: 'blue' }
    bindModal.value = true
    stopBindPolling()
    bindTimer = setInterval(async () => {
      if (!bindTaskId) return
      try {
        const r = await api(`/api/v1/admin/notifications/qq/bind/${bindTaskId}`)
        if (r.status === 'bound') {
          bindStatus.value = { ...bindStatus.value, text: '绑定成功，凭证已写入本机加密库', tone: 'green' }
          stopBindPolling()
          await loadConfig()
          setTimeout(() => { bindModal.value = false }, 1800)
        } else if (r.status === 'awaiting_message') {
          stopBindPolling()
          bindModal.value = false
          alert('QQ 机器人授权成功，正在自动启动 OpenID 捕获…')
          startCapture()
        } else if (r.status === 'expired') {
          bindStatus.value = { ...bindStatus.value, text: '二维码已过期，请点击刷新', tone: 'amber' }
          stopBindPolling()
        } else if (r.status === 'failed') {
          bindStatus.value = { ...bindStatus.value, text: `绑定失败：${r.error || '未知错误'}`, tone: 'red' }
          stopBindPolling()
        } else {
          bindStatus.value = { ...bindStatus.value, text: `等待扫码…（${r.expires_in ?? '--'} 秒内有效）`, tone: 'blue' }
        }
      } catch (e: any) {
        bindStatus.value = { ...bindStatus.value, text: e.message, tone: 'red' }
        stopBindPolling()
      }
    }, 2000)
  } catch (e: any) {
    alert(e.message)
  }
}

function closeBindModal() {
  stopBindPolling()
  bindModal.value = false
}

// ---- protected test send ----
async function sendTest(channel: string) {
  try {
    testResults.value[channel] = { status: 'testing', detail: '正在请求测试发送…' }
    const res = await api('/api/v1/admin/notifications/test', {
      method: 'POST',
      body: JSON.stringify({ channel, confirmation: `SEND TEST ${channel.toUpperCase()}` }),
    })
    testResults.value[channel] = { status: res.result?.[channel]?.startsWith('accepted:') ? 'ready' : (res.result?.status || 'sent'), detail: `${res.result?.[channel] || res.result?.detail || '已发送'} · ${res.meaning || ''}` }
  } catch (e: any) {
    testResults.value[channel] = { status: 'failed', detail: e.message }
  }
}

async function saveSchedule() {
  const times = String(config.value._briefingTimes || '').split(/[,，\s]+/).filter(Boolean)
  if (!times.length) { alert('请至少填写一个 HH:MM 时间'); return }
  try {
    await api('/api/v1/admin/notifications/schedule', { method: 'PUT', body: JSON.stringify({ briefing_times: times }) })
    alert('简报时间已保存')
  } catch (e: any) {
    alert(e.message)
  }
}

onMounted(() => {
  loadConfig()
})
</script>

<template>
  <div class="space-y-4 max-w-[2048px] mx-auto">
    <div class="flex items-center justify-between">
      <p class="text-xs" style="color: var(--ink-2);"> QQ 官方应用 Bot —— 逐通道配置、仅诊断、发送测试；最后统一保存投递时间。 </p>
      <span
        class="text-[11px] px-2 py-1 rounded border font-bold"
        style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);"
      >
        集成通道 · {{ enabledChannelsCount }}/4
      </span>
    </div>

    <!-- Alert / Banner Message -->
    <div v-if="loading" class="py-12 text-center text-xs" style="color: var(--ink-2);">正在加载通知配置...</div>

    <template v-else-if="config">
      <!-- QQ Channel -->
      <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center justify-between mb-4">
          <div class="flex items-center space-x-2">
            <span class="inline-block w-2 h-2 rounded-full" :class="config.qq.enabled ? 'bg-emerald-500' : 'bg-zinc-500'"></span>
            <h2 class="text-sm font-bold" style="color: var(--ink-1);">{{ t('nav.admin.notify') }}</h2>
          </div>
          <div class="flex items-center space-x-3">
            <button @click="startQqBind" class="px-2.5 py-1 rounded-lg text-xs font-bold cursor-pointer transition-all shadow-xs" style="background-color: var(--ink-1); color: var(--surface-2);">扫码绑定</button>
            <button @click="startCapture" class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--accent-bg); border-color: var(--accent-line); color: var(--accent);">
              <Zap class="w-3 h-3" />
              <span>⚡ 自动获取 OpenID</span>
            </button>
            <div class="flex items-center space-x-2">
              <button
                type="button"
                @click="toggleChannel('qq', !config.qq.enabled)"
                class="relative inline-flex items-center cursor-pointer focus:outline-none"
                :title="config.qq.enabled ? '点击关闭 QQ 通知' : '点击开启 QQ 通知'"
              >
                <div
                  class="w-10 h-5 rounded-full transition-colors relative"
                  :style="{ backgroundColor: config.qq.enabled ? 'var(--up)' : 'var(--line-2)' }"
                >
                  <div
                    class="absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform shadow-xs"
                    :class="config.qq.enabled ? 'translate-x-5' : 'translate-x-0'"
                  ></div>
                </div>
              </button>
              <span
                class="text-xs font-bold select-none cursor-pointer"
                @click="toggleChannel('qq', !config.qq.enabled)"
                :style="{ color: config.qq.enabled ? 'var(--up)' : 'var(--ink-2)' }"
              >
                {{ config.qq.enabled ? '已开启' : '已关闭' }}
              </span>
            </div>
          </div>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div><label class="block text-[11px] mb-1" style="color: var(--ink-2);">App ID</label><input v-model="config.qq.app_id" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" /></div>
          <div><label class="block text-[11px] mb-1" style="color: var(--ink-2);">Client Secret</label><input v-model="config.qq._secret" type="password" placeholder="留空保持现有" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" /></div>
          <div class="sm:col-span-2"><label class="block text-[11px] mb-1" style="color: var(--ink-2);">目标用户 OpenID</label><input v-model="config.qq.openid" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" /></div>
        </div>
        <div class="flex space-x-2 mt-3">
          <button @click="diagnose('qq')" class="px-3 py-1.5 rounded-lg border text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);">仅诊断</button>
          <button @click="sendTest('qq')" class="px-3 py-1.5 rounded-lg border text-xs font-bold cursor-pointer transition-all shadow-xs" style="background-color: var(--up-bg); border-color: var(--up-line); color: var(--up);">发送测试</button>
        </div>
        <div v-if="testResults.qq" class="mt-2 text-xs" :class="testResults.qq.status === 'ready' ? 'text-emerald-500' : 'text-amber-500'">{{ testResults.qq.status }} · {{ testResults.qq.detail }}</div>
      </div>

      <!-- Telegram -->
      <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center justify-between mb-4">
          <div class="flex items-center space-x-2">
            <span class="inline-block w-2 h-2 rounded-full" :class="config.telegram.enabled ? 'bg-emerald-500' : 'bg-zinc-500'"></span>
            <h2 class="text-sm font-bold" style="color: var(--ink-1);">Telegram Bot</h2>
          </div>
          <div class="flex items-center space-x-2">
            <button
              type="button"
              @click="toggleChannel('telegram', !config.telegram.enabled)"
              class="relative inline-flex items-center cursor-pointer focus:outline-none"
              :title="config.telegram.enabled ? '点击关闭 Telegram 通知' : '点击开启 Telegram 通知'"
            >
              <div
                class="w-10 h-5 rounded-full transition-colors relative"
                :style="{ backgroundColor: config.telegram.enabled ? 'var(--up)' : 'var(--line-2)' }"
              >
                <div
                  class="absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform shadow-xs"
                  :class="config.telegram.enabled ? 'translate-x-5' : 'translate-x-0'"
                ></div>
              </div>
            </button>
            <span
              class="text-xs font-bold select-none cursor-pointer"
              @click="toggleChannel('telegram', !config.telegram.enabled)"
              :style="{ color: config.telegram.enabled ? 'var(--up)' : 'var(--ink-2)' }"
            >
              {{ config.telegram.enabled ? '已开启' : '已关闭' }}
            </span>
          </div>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div><label class="block text-[11px] mb-1" style="color: var(--ink-2);">Bot Token</label><input v-model="config.telegram._token" type="password" placeholder="留空保持现有" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" /></div>
          <div><label class="block text-[11px] mb-1" style="color: var(--ink-2);">Chat ID</label><input v-model="config.telegram.chat_id" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" /></div>
          <div class="sm:col-span-2"><label class="block text-[11px] mb-1" style="color: var(--ink-2);">API Base URL (国内反代)</label><input v-model="config.telegram.api_base" placeholder="https://api.telegram.org" class="w-full rounded-lg px-3 py-2 text-xs outline-none border" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" /></div>
        </div>
        <div class="flex space-x-2 mt-3">
          <button @click="diagnose('telegram')" class="px-3 py-1.5 rounded-lg border text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);">仅诊断</button>
          <button @click="sendTest('telegram')" class="px-3 py-1.5 rounded-lg border text-xs font-bold cursor-pointer transition-all shadow-xs" style="background-color: var(--up-bg); border-color: var(--up-line); color: var(--up);">发送测试</button>
        </div>
        <div v-if="testResults.telegram" class="mt-2 text-xs" :class="testResults.telegram.status === 'ready' ? 'text-emerald-500' : 'text-amber-500'">{{ testResults.telegram.status }} · {{ testResults.telegram.detail }}</div>
      </div>

      <!-- WeChat + Webhook -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
          <div class="flex items-center justify-between mb-4">
            <div class="flex items-center space-x-2"><span class="inline-block w-2 h-2 rounded-full" :class="config.wechat.enabled ? 'bg-emerald-500' : 'bg-zinc-500'"></span><h2 class="text-sm font-bold" style="color: var(--ink-1);">企业微信</h2></div>
            <div class="flex items-center space-x-2">
              <button
                type="button"
                @click="toggleChannel('wechat', !config.wechat.enabled)"
                class="relative inline-flex items-center cursor-pointer focus:outline-none"
                :title="config.wechat.enabled ? '点击关闭企业微信通知' : '点击开启企业微信通知'"
              >
                <div
                  class="w-10 h-5 rounded-full transition-colors relative"
                  :style="{ backgroundColor: config.wechat.enabled ? 'var(--up)' : 'var(--line-2)' }"
                >
                  <div
                    class="absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform shadow-xs"
                    :class="config.wechat.enabled ? 'translate-x-5' : 'translate-x-0'"
                  ></div>
                </div>
              </button>
              <span
                class="text-xs font-bold select-none cursor-pointer"
                @click="toggleChannel('wechat', !config.wechat.enabled)"
                :style="{ color: config.wechat.enabled ? 'var(--up)' : 'var(--ink-2)' }"
              >
                {{ config.wechat.enabled ? '已开启' : '已关闭' }}
              </span>
            </div>
          </div>
          <label class="block text-[11px] mb-1" style="color: var(--ink-2);">Webhook URL</label>
          <input v-model="config.wechat.webhook" class="w-full rounded-lg px-3 py-2 text-xs outline-none border mb-3" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
          <button @click="diagnose('wechat')" class="px-3 py-1.5 rounded-lg border text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);">仅诊断</button>
          <button @click="sendTest('wechat')" class="ml-2 px-3 py-1.5 rounded-lg border text-xs font-bold cursor-pointer transition-all shadow-xs" style="background-color: var(--up-bg); border-color: var(--up-line); color: var(--up);">发送测试</button>
          <div v-if="testResults.wechat" class="mt-2 text-xs" :class="testResults.wechat.status === 'ready' ? 'text-emerald-500' : 'text-amber-500'">{{ testResults.wechat.status }} · {{ testResults.wechat.detail }}</div>
        </div>
        <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
          <div class="flex items-center justify-between mb-4">
            <div class="flex items-center space-x-2"><span class="inline-block w-2 h-2 rounded-full" :class="config.webhook.enabled ? 'bg-emerald-500' : 'bg-zinc-500'"></span><h2 class="text-sm font-bold" style="color: var(--ink-1);">通用 Webhook</h2></div>
            <div class="flex items-center space-x-2">
              <button
                type="button"
                @click="toggleChannel('webhook', !config.webhook.enabled)"
                class="relative inline-flex items-center cursor-pointer focus:outline-none"
                :title="config.webhook.enabled ? '点击关闭通用 Webhook' : '点击开启通用 Webhook'"
              >
                <div
                  class="w-10 h-5 rounded-full transition-colors relative"
                  :style="{ backgroundColor: config.webhook.enabled ? 'var(--up)' : 'var(--line-2)' }"
                >
                  <div
                    class="absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full transition-transform shadow-xs"
                    :class="config.webhook.enabled ? 'translate-x-5' : 'translate-x-0'"
                  ></div>
                </div>
              </button>
              <span
                class="text-xs font-bold select-none cursor-pointer"
                @click="toggleChannel('webhook', !config.webhook.enabled)"
                :style="{ color: config.webhook.enabled ? 'var(--up)' : 'var(--ink-2)' }"
              >
                {{ config.webhook.enabled ? '已开启' : '已关闭' }}
              </span>
            </div>
          </div>
          <label class="block text-[11px] mb-1" style="color: var(--ink-2);">URL (智能兼容钉钉/飞书/Discord)</label>
          <input v-model="config.webhook.url" class="w-full rounded-lg px-3 py-2 text-xs outline-none border mb-3" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
          <button @click="diagnose('webhook')" class="px-3 py-1.5 rounded-lg border text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);">仅诊断</button>
          <button @click="sendTest('webhook')" class="ml-2 px-3 py-1.5 rounded-lg border text-xs font-bold cursor-pointer transition-all shadow-xs" style="background-color: var(--up-bg); border-color: var(--up-line); color: var(--up);">发送测试</button>
          <div v-if="testResults.webhook" class="mt-2 text-xs" :class="testResults.webhook.status === 'ready' ? 'text-emerald-500' : 'text-amber-500'">{{ testResults.webhook.status }} · {{ testResults.webhook.detail }}</div>
        </div>
      </div>

      <!-- Schedule + Notification Categories + Save -->
      <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors space-y-4" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div>
          <h2 class="text-sm font-bold mb-1" style="color: var(--ink-1);">通知类别与事件流</h2>
          <p class="text-xs" style="color: var(--ink-2);">系统底层事件已全面升级，针对不同关键节点自动化推送结构化卡片文案：</p>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2.5 text-xs">
          <div class="p-3 rounded-lg border space-y-1" style="background-color: var(--surface-1); border-color: var(--line-1);">
            <div class="flex items-center space-x-1.5 font-bold text-emerald-400">
              <span>🚀 实盘开仓触发 <code class="mono text-[10px] opacity-60">trade.opened</code></span>
            </div>
            <p class="text-[11px] leading-relaxed" style="color: var(--ink-2);">
              包含标的、多空方向、杠杆、开仓挂单价、OCO云端止盈/止损双轨及大模型因果决策逻辑。
            </p>
          </div>

          <div class="p-3 rounded-lg border space-y-1" style="background-color: var(--surface-1); border-color: var(--line-1);">
            <div class="flex items-center space-x-1.5 font-bold text-blue-400">
              <span>平仓结清提醒 <code class="mono text-[10px] opacity-60">trade.closed</code></span>
            </div>
            <p class="text-[11px] leading-relaxed" style="color: var(--ink-2);">
              智能区分「🎉 盈利落袋」、「⚖️ 保本结清」与「🛡️ 风控止损」，清晰输出净盈亏 U 数、ROI 与持仓时长。
            </p>
          </div>

          <div class="p-3 rounded-lg border space-y-1" style="background-color: var(--surface-1); border-color: var(--line-1);">
            <div class="flex items-center space-x-1.5 font-bold text-indigo-400">
              <span>保本锁利移损 <code class="mono text-[10px] opacity-60">trade.sl_updated</code></span>
            </div>
            <p class="text-[11px] leading-relaxed" style="color: var(--ink-2);">
              浮盈达标触发保本移损时，即刻播报原止损位与上移后的保本价，确认锁定本单胜率下限。
            </p>
          </div>

          <div class="p-3 rounded-lg border space-y-1" style="background-color: var(--surface-1); border-color: var(--line-1);">
            <div class="flex items-center space-x-1.5 font-bold text-purple-400">
              <span>AI 自进化心法 <code class="mono text-[10px] opacity-60">evolution.completed</code></span>
            </div>
            <p class="text-[11px] leading-relaxed" style="color: var(--ink-2);">
              每日闭环自进化完成后，实时推送当日全样本胜率、演进状态及大模型提炼的核心实战心法。
            </p>
          </div>

          <div class="p-3 rounded-lg border space-y-1" style="background-color: var(--surface-1); border-color: var(--line-1);">
            <div class="flex items-center space-x-1.5 font-bold text-red-400">
              <span>🚨 黑天鹅避险熔断 <code class="mono text-[10px] opacity-60">risk.triggered</code></span>
            </div>
            <p class="text-[11px] leading-relaxed" style="color: var(--ink-2);">
              全网舆情暴跌或流动性枯竭触发全自动熔断时，以 P0 最高优先级向全部通道进行声光告警。
            </p>
          </div>

          <div class="p-3 rounded-lg border space-y-1" style="background-color: var(--surface-1); border-color: var(--line-1);">
            <div class="flex items-center space-x-1.5 font-bold text-amber-400">
              <span>📊 每日晨/晚报 <code class="mono text-[10px] opacity-60">briefing.ready</code></span>
            </div>
            <p class="text-[11px] leading-relaxed" style="color: var(--ink-2);">
              按下方指定时间自动汇总在手仓位、资金净值、当日累计盈亏与宏观市场因果微积分综述。
            </p>
          </div>
        </div>

        <div class="pt-2 border-t" style="border-color: var(--line-1);">
          <label class="block text-[11px] mb-1 font-bold" style="color: var(--ink-2);">每日量化简报时间 (北京时间，多个用逗号隔开)</label>
          <input v-model="config._briefingTimes" placeholder="08:00, 20:00" class="w-full rounded-lg px-3 py-2 text-xs outline-none border mb-4" style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);" />
          <div class="flex items-center space-x-3">
            <button @click="saveAll" class="px-4 py-2 rounded-lg text-xs font-bold cursor-pointer transition-all shadow-xs" style="background-color: var(--ink-1); color: var(--surface-2);">保存全部通知通道</button>
            <button @click="saveSchedule" class="px-4 py-2 rounded-lg border text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);">保存通知时间</button>
          </div>
        </div>
      </div>
    </template>

    <!-- Capture Modal -->
    <div v-if="captureModal" class="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4" @click.self="captureModal = false">
      <div class="rounded-xl border p-6 w-full max-w-[520px] max-h-[88dvh] overflow-y-auto text-center shadow-2xl transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <h3 class="text-sm font-bold mb-3" style="color: var(--ink-1);">⚡ 自动捕获目标用户 OpenID</h3>
        <div class="text-4xl mb-3">📱 💬 🤖</div>
        <p class="text-sm font-bold mb-2" style="color: var(--ink-1);">{{ captureStatus?.bot_name || '连接中...' }}</p>
        <p class="text-xs mb-4 leading-relaxed" style="color: var(--ink-2);">用手机 QQ 打开与机器人的私聊窗口，发送任意文字（如：绑定）。系统将自动捕获并填入你的 OpenID。</p>
        <div class="border rounded-lg p-3 mb-4" style="background-color: var(--surface-1); border-color: var(--line-1);">
          <div class="font-bold text-sm" :class="captureStatus?.status === 'captured' ? 'text-emerald-500' : 'text-blue-500'">
            {{ captureStatus?.status === 'captured' ? '捕获成功！' : '正在监听...' }}
          </div>
          <div v-if="captureStatus?.expires_in" class="text-[11px] mt-1" style="color: var(--ink-3);">剩余时间：{{ captureStatus.expires_in }} 秒</div>
          <div v-if="captureStatus?.openid" class="text-xs mt-2" style="color: var(--accent);">OpenID: {{ captureStatus.openid }}</div>
        </div>
        <button @click="captureModal = false" class="px-4 py-2 rounded-lg border text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);">关闭</button>
      </div>
    </div>

    <!-- QQ Bind QR Modal -->
    <div v-if="bindModal" class="fixed inset-0 z-50 bg-black/60 backdrop-blur-xs flex items-center justify-center p-4" @click.self="closeBindModal">
      <div class="rounded-xl border p-5 sm:p-6 w-full max-w-[380px] max-h-[88dvh] overflow-y-auto text-center shadow-2xl transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <h3 class="text-sm font-bold mb-2" style="color: var(--ink-1);">绑定 QQ 机器人</h3>
        <p class="text-[11px] mb-3" style="color: var(--ink-2);">使用手机 QQ 扫一扫，或长按复制链接在 QQ 内打开。确认授权后本页自动完成绑定。</p>
        <img v-if="bindStatus?.qr" :src="bindStatus.qr" alt="QQ 绑定二维码" class="w-[220px] h-[220px] rounded-lg bg-white p-2.5 mx-auto mb-3 shadow-xs border" style="border-color: var(--line-1);" />
        <p v-if="bindStatus?.link" class="text-[11px] break-all mb-3" style="color: var(--accent);">{{ bindStatus.link }}</p>
        <p class="text-xs mb-4" :class="{ 'text-blue-500': bindStatus?.tone === 'blue', 'text-emerald-500': bindStatus?.tone === 'green', 'text-amber-500': bindStatus?.tone === 'amber', 'text-rose-500': bindStatus?.tone === 'red' }">{{ bindStatus?.text }}</p>
        <div class="flex justify-center space-x-2">
          <button @click="startQqBind" class="px-3 py-1.5 rounded-lg border text-xs cursor-pointer transition-all shadow-xs" style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);">刷新二维码</button>
          <button @click="closeBindModal" class="px-3 py-1.5 rounded-lg text-xs font-bold cursor-pointer transition-all shadow-xs" style="background-color: var(--ink-1); color: var(--surface-2);">关闭</button>
        </div>
      </div>
    </div>
  </div>
</template>

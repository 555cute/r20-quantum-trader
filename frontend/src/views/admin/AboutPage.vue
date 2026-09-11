<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
import { useApi } from '../../composables/useApi'
import { Info, GitBranch, Download, RefreshCw, CheckCircle2, AlertTriangle, ShieldCheck, Terminal } from 'lucide-vue-next'

const { api } = useApi()
const about = ref<any>(null)
const loading = ref(true)
const updateChecking = ref(false)
const updateRunning = ref(false)
const updateResult = ref<any>(null)
const showConfirmModal = ref(false)
const confirmPhrase = ref('')

async function loadAbout() {
  loading.value = true
  try {
    about.value = await api('/api/v1/admin/about')
  } catch (e: any) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

async function checkUpdate() {
  updateChecking.value = true
  updateResult.value = null
  try {
    const res = await api('/api/v1/admin/update/check', { method: 'POST' })
    if (about.value) {
      about.value.update = res
    }
    updateResult.value = {
      ok: true,
      message: res.behind > 0 ? `发现远端有 ${res.behind} 个新提交可更新 (远端 ${res.remote})` : '当前代码已是最新，与远端主分支保持同步。',
      data: res,
    }
  } catch (e: any) {
    updateResult.value = { error: e.message }
  } finally {
    updateChecking.value = false
  }
}

function openUpdateModal() {
  confirmPhrase.value = ''
  showConfirmModal.value = true
}

async function executeUpdate() {
  if (confirmPhrase.value.trim().toUpperCase() !== 'UPDATE R20') return
  updateRunning.value = true
  updateResult.value = null
  try {
    const res = await api('/api/v1/admin/update', {
      method: 'POST',
      body: JSON.stringify({ confirmation: 'UPDATE R20' }),
    })
    showConfirmModal.value = false
    updateResult.value = {
      ok: true,
      updated: res.updated,
      message: res.updated ? '系统更新成功！' : '当前分支已是最新。',
      git_output: res.git_output,
      restart_note: res.restart_note,
    }
    if (about.value && res.after) {
      about.value.update = res.after
    }
  } catch (e: any) {
    updateResult.value = { error: e.message }
  } finally {
    updateRunning.value = false
  }
}

onMounted(() => {
  loadAbout()
})
</script>

<template>
  <div class="space-y-4 text-xs">
    <div class="flex items-center justify-between">
      <p class="text-xs text-[var(--ink-3)]">确认版本状态，执行安全快进更新。</p>
      <span class="text-[11px] text-blue-400 bg-blue-500/10 px-2 py-1 rounded border border-blue-500/20">治理 · 3/3</span>
    </div>

    <div v-if="loading" class="py-12 text-center" style="color: var(--ink-2);">正在加载组件与版本数据...</div>

    <template v-else-if="about">
      <!-- About Cards -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
          <div class="flex items-center justify-between pb-3 mb-3 border-b" style="border-color: var(--line-1);">
            <div class="flex items-center space-x-2">
              <Info class="w-4 h-4" style="color: var(--accent);" />
              <h2 class="text-sm font-bold" style="color: var(--ink-1);">{{ t('nav.admin.about') }}</h2>
            </div>
            <span class="text-[11px] px-2 py-0.5 rounded border font-bold" style="background-color: var(--up-bg); color: var(--up); border-color: var(--up-line);">OPEN SOURCE</span>
          </div>
          <div class="space-y-1.5" style="color: var(--ink-2);">
            <div>产品架构: <strong style="color: var(--ink-1);">{{ about.product?.name }}</strong></div>
            <div>执行引擎: <span style="color: var(--ink-1);">三所平权对等架构 (OKX / Binance / Gate)</span></div>
            <div>系统版本: <strong style="color: var(--accent);">v{{ about.product?.version }}</strong></div>
            <div>网关控制面: <span style="color: var(--ink-1);">{{ about.product?.control_plane }} (v{{ about.product?.gateway_version }})</span></div>
            <div>运行环境: <span style="color: var(--ink-1);">Python {{ about.runtime?.python }}</span></div>
          </div>
          <a href="https://github.com/555cute/r20-quantum-trader" target="_blank" class="inline-flex items-center space-x-1.5 mt-4 px-3 py-1.5 rounded-lg border text-xs font-bold transition-all cursor-pointer shadow-xs" style="background-color: var(--accent); color: var(--accent-ink);">
            <GitBranch class="w-3.5 h-3.5" />
            <span>GitHub 官方代码仓库</span>
          </a>
        </div>

        <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
          <div class="flex items-center justify-between pb-3 mb-3 border-b" style="border-color: var(--line-1);">
            <h2 class="text-sm font-bold" style="color: var(--ink-1);">组件版本</h2>
            <span class="text-[11px]" style="color: var(--ink-3);">生产运行栈</span>
          </div>
          <div class="table-scroll-container">
            <table class="w-full text-left whitespace-nowrap">
              <tbody>
                <tr v-for="c in about.components" :key="c.name" class="border-b last:border-b-0 hover:bg-[var(--surface-3)] transition-colors" style="border-color: var(--line-1);">
                  <td class="py-2" style="color: var(--ink-2);">{{ c.name }}</td>
                  <td class="py-2 font-bold num" style="color: var(--ink-1);">{{ c.version }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- Update Section -->
      <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center justify-between pb-3 mb-3 border-b" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2">
            <ShieldCheck class="w-4 h-4 text-emerald-500" />
            <h2 class="text-sm font-bold" style="color: var(--ink-1);">安全更新</h2>
          </div>
          <span class="text-[11px] px-2 py-0.5 rounded border font-bold" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">FF-ONLY</span>
        </div>

        <!-- Git Status Telemetry Grid -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2.5 mb-4">
          <div class="p-2.5 rounded-lg border" style="background-color: var(--surface-1); border-color: var(--line-1);">
            <div class="text-[11px]" style="color: var(--ink-3);">当前分支</div>
            <div class="text-xs font-bold mt-0.5" style="color: var(--ink-1);">{{ about.update?.branch || 'main' }}</div>
          </div>
          <div class="p-2.5 rounded-lg border" style="background-color: var(--surface-1); border-color: var(--line-1);">
            <div class="text-[11px]" style="color: var(--ink-3);">本地提交 (HEAD)</div>
            <div class="text-xs font-bold mt-0.5 text-blue-400">{{ about.update?.local || '--' }}</div>
          </div>
          <div class="p-2.5 rounded-lg border" style="background-color: var(--surface-1); border-color: var(--line-1);">
            <div class="text-[11px]" style="color: var(--ink-3);">远端提交 (origin)</div>
            <div class="text-xs font-bold mt-0.5" style="color: var(--ink-1);">{{ about.update?.remote || '待检查' }}</div>
          </div>
          <div class="p-2.5 rounded-lg border" style="background-color: var(--surface-1); border-color: var(--line-1);">
            <div class="text-[11px]" style="color: var(--ink-3);">待同步差额</div>
            <div class="text-xs font-bold mt-0.5" :class="(about.update?.behind || 0) > 0 ? 'text-amber-400' : 'text-emerald-400'">
              {{ (about.update?.behind || 0) > 0 ? `落后 ${about.update?.behind} 提交` : '已最新' }}
              <span v-if="about.update?.ahead" class="text-[11px] text-gray-400 font-normal"> (领先 {{ about.update.ahead }})</span>
            </div>
          </div>
        </div>

        <!-- Action Buttons -->
        <div class="flex flex-wrap items-center gap-2.5">
          <button
            @click="checkUpdate"
            :disabled="updateChecking || updateRunning"
            class="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg border text-xs font-bold transition-all cursor-pointer shadow-xs"
            style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);"
          >
            <RefreshCw class="w-3.5 h-3.5" :class="updateChecking ? 'animate-spin' : ''" />
            <span>{{ updateChecking ? '正在连接远端...' : '检查远端更新' }}</span>
          </button>

          <button
            @click="openUpdateModal"
            :disabled="updateChecking || updateRunning"
            class="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg border text-xs font-bold transition-all cursor-pointer shadow-xs text-white"
            style="background-color: var(--accent); border-color: var(--accent); color: var(--accent-ink);"
          >
            <Download class="w-3.5 h-3.5" />
            <span>执行安全更新</span>
          </button>
        </div>

        <!-- Result / Feedback Banner -->
        <div v-if="updateResult" class="mt-3.5 space-y-2">
          <div
            class="text-xs p-3 rounded-lg border flex items-start space-x-2"
            :style="updateResult.error
              ? { backgroundColor: 'var(--down-bg)', borderColor: 'var(--down-line)', color: 'var(--down)' }
              : { backgroundColor: 'var(--up-bg)', borderColor: 'var(--up-line)', color: 'var(--up)' }"
          >
            <AlertTriangle v-if="updateResult.error" class="w-4 h-4 shrink-0 mt-0.5" />
            <CheckCircle2 v-else class="w-4 h-4 shrink-0 mt-0.5" />
            <div class="flex-1 space-y-1">
              <div class="font-bold">{{ updateResult.error || updateResult.message }}</div>
              <div v-if="updateResult.restart_note" class="text-[11px] opacity-90">
                💡 {{ updateResult.restart_note }}
              </div>
            </div>
          </div>

          <div v-if="updateResult.git_output" class="p-3 rounded-lg border bg-black/40 text-[11px] text-gray-300 space-y-1">
            <div class="flex items-center space-x-1 text-gray-400 text-[11px]">
              <Terminal class="w-3 h-3" />
              <span>Git 执行输出：</span>
            </div>
            <pre class="whitespace-pre-wrap leading-relaxed">{{ updateResult.git_output }}</pre>
          </div>
        </div>

        <p class="mt-3 text-[11px] leading-relaxed" style="color: var(--ink-3);">
          安全保护机制：执行更新时仅允许 Fast-Forward 快进合并；如果工作区有未提交的追踪代码冲突、远端不可达或无法快进，后台将自动拒绝更新以保护系统稳定性。
        </p>
      </div>
    </template>

    <!-- Confirmation Modal -->
    <div
      v-if="showConfirmModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4"
    >
      <div
        class="w-full max-w-md rounded-2xl border p-5 shadow-2xl space-y-4"
        style="background-color: var(--surface-2); border-color: var(--line-2);"
      >
        <div class="flex items-center space-x-2 pb-3 border-b" style="border-color: var(--line-1);">
          <AlertTriangle class="w-5 h-5 text-amber-500 shrink-0" />
          <div>
            <h3 class="text-sm font-bold" style="color: var(--ink-1);">确认更新 R20 系统</h3>
            <p class="text-[11px]" style="color: var(--ink-2);"> 关于 R20：执行 fast-forward 拉取最新主分支代码 </p>
          </div>
        </div>

        <div class="space-y-2 text-xs" style="color: var(--ink-2);">
          <p>
            为防止误操作，请在下方输入确认短语 <strong class="text-red-400 font-bold">UPDATE R20</strong>：
          </p>
          <input
            v-model="confirmPhrase"
            placeholder="请输入 UPDATE R20"
            class="w-full rounded-lg px-3 py-2 text-xs outline-none border uppercase"
            style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-1);"
            @keyup.enter="executeUpdate"
          />
        </div>

        <div class="flex justify-end space-x-2 pt-2">
          <button
            @click="showConfirmModal = false"
            :disabled="updateRunning"
            class="px-3 py-1.5 rounded-lg border text-xs cursor-pointer"
            style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-2);"
          >
            取消
          </button>
          <button
            @click="executeUpdate"
            :disabled="confirmPhrase.trim().toUpperCase() !== 'UPDATE R20' || updateRunning"
            class="flex items-center space-x-1.5 px-4 py-1.5 rounded-lg text-xs font-bold text-white transition-all cursor-pointer shadow-xs disabled:opacity-40 disabled:cursor-not-allowed"
            style="background-color: var(--accent); border-color: var(--accent); color: var(--accent-ink);"
          >
            <RefreshCw v-if="updateRunning" class="w-3.5 h-3.5 animate-spin" />
            <span>{{ updateRunning ? '正在更新中...' : '立即确认更新' }}</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

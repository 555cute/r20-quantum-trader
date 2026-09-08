<script setup lang="ts">
import { useToast } from '../../composables/useToast'
const toast = useToast()
import { ref, onMounted } from 'vue'
import PageHeader from '../../components/admin/PageHeader.vue'
import { useI18n } from '../../composables/useI18n'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import {ArrowUp, ArrowDown, Plus, Code, Trash2,
  ToggleLeft, ToggleRight, Play, AlertTriangle,
  X, Save, Download, FileCode, Sparkles} from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()
const { t } = useI18n()

const plugins = ref<any[]>([])
const loading = ref(true)

// Code Editor Modal State
const editorVisible = ref(false)
const editingFilename = ref('')
const editingCode = ref('')
const editingName = ref('')
const savingCode = ref(false)
const codeError = ref('')

// Sandbox Test State
const testing = ref(false)
const testResults = ref<any>(null)
const testModalVisible = ref(false)

// Create New Plugin State
const createModalVisible = ref(false)
const newFilename = ref('')
const newCode = ref('')
const createError = ref('')

async function loadPlugins() {
  loading.value = true
  try {
    const res = await api('/api/v1/admin/interceptors')
    plugins.value = res.plugins || []
  } catch (e: any) {
    toast.err(`加载插件失败：${e.message}`)
  } finally {
    loading.value = false
  }
}

async function togglePlugin(p: any) {
  try {
    const nextState = !p.enabled
    await api(`/api/v1/admin/interceptors/${encodeURIComponent(p.filename)}/toggle`, {
      method: 'PUT',
      body: JSON.stringify({ enabled: nextState }),
    })
    p.enabled = nextState
    toast.ok(`已${nextState ? '启用' : '停用'}拦截插件「${p.name || p.filename}」`)
  } catch (e: any) {
    toast.err(`操作失败：${e.message}`)
  }
}

async function movePlugin(idx: number, dir: -1 | 1) {
  const target = idx + dir
  if (target < 0 || target >= plugins.value.length) return
  const arr = [...plugins.value]
  ;[arr[idx], arr[target]] = [arr[target], arr[idx]]
  plugins.value = arr

  const newOrder = arr.map((x) => x.filename)
  try {
    const res = await api('/api/v1/admin/interceptors/reorder', {
      method: 'POST',
      body: JSON.stringify({ pipeline_order: newOrder }),
    })
    plugins.value = res.plugins || arr
    toast.ok('已更新拦截管线执行优先级顺序')
  } catch (e: any) {
    toast.err(`排序更新失败：${e.message}`)
    await loadPlugins()
  }
}

async function openEditor(p: any) {
  try {
    const detail = await api(`/api/v1/admin/interceptors/${encodeURIComponent(p.filename)}`)
    editingFilename.value = detail.filename
    editingName.value = detail.name || detail.filename
    editingCode.value = detail.code || ''
    codeError.value = ''
    editorVisible.value = true
  } catch (e: any) {
    toast.err(`读取插件源码失败：${e.message}`)
  }
}

async function saveCode() {
  savingCode.value = true
  codeError.value = ''
  try {
    await api(`/api/v1/admin/interceptors/${encodeURIComponent(editingFilename.value)}/code`, {
      method: 'PUT',
      body: JSON.stringify({ code: editingCode.value }),
    })
    toast.ok(`插件「${editingFilename.value}」代码已保存并热加载生效`)
    editorVisible.value = false
    await loadPlugins()
  } catch (e: any) {
    codeError.value = e.message
  } finally {
    savingCode.value = false
  }
}

function exportPluginCode(filename: string, code: string) {
  const blob = new Blob([code], { type: 'text/x-python' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

async function deletePlugin(p: any) {
  if (!confirm(`确定删除拦截插件「${p.name || p.filename}」？\n文件将被从磁盘彻底移除。`)) return
  try {
    await api(`/api/v1/admin/interceptors/${encodeURIComponent(p.filename)}`, { method: 'DELETE' })
    toast.ok(`已删除插件「${p.filename}」`)
    await loadPlugins()
  } catch (e: any) {
    toast.err(`删除失败：${e.message}`)
  }
}

async function runSandbox() {
  testing.value = true
  try {
    testResults.value = await api('/api/v1/admin/interceptors/test', {
      method: 'POST',
      body: '{}',
    })
    testModalVisible.value = true
  } catch (e: any) {
    toast.err(`沙箱回归测试执行失败：${e.message}`)
  } finally {
    testing.value = false
  }
}

function openCreateModal() {
  newFilename.value = `custom_interceptor_${Date.now().toString(36)}.py`
  newCode.value = `"""
R20 物理拦截插件规范
====================
id: my_custom_rule
name: 我的自定义风控规则
version: 1.0.0
author: ${auth.user?.username || 'Trader'}
description: 描述你的专有物理拦截规则
tags: 自定义, 策略广场
"""

def check_risk(package: dict, decision: dict, context: dict) -> tuple[bool, str]:
    """
    检查交易候选风控指标:
    - package: 包含标的行情与动力学数据 (macro_4h, velocity_v, acceleration_a, adx_1h 等)
    - decision: 包含 AI 主脑建议 (action, confidence, entry_price, take_profit_price, stop_loss_price)
    - context: 包含持仓上下文与可用资金

    返回 (True, "") 表示放行通过；
    返回 (False, "具体拦截原因") 表示拦截并安全重写为 WAIT。
    """
    action = str(decision.get("action", "WAIT")).upper()
    if action == "WAIT":
        return True, ""

    # 编写你的风控卡点规则...
    return True, ""
`
  createError.value = ''
  createModalVisible.value = true
}

async function submitCreate() {
  createError.value = ''
  if (!newFilename.value.trim()) {
    createError.value = '请输入插件文件名'
    return
  }
  try {
    const res = await api('/api/v1/admin/interceptors', {
      method: 'POST',
      body: JSON.stringify({
        filename: newFilename.value.trim(),
        code: newCode.value,
      }),
    })
    toast.ok(`成功创建拦截插件「${res.name || res.filename}」！`)
    createModalVisible.value = false
    await loadPlugins()
  } catch (e: any) {
    createError.value = e.message
  }
}

onMounted(loadPlugins)
</script>

<template>
  <div class="space-y-4 text-xs max-w-[2048px] mx-auto">
    <!-- Header & Action Bar -->
    <PageHeader :title="t('nav.admin.interceptors')" description="交易决策发出前必须逐层通过 Python 物理拦截管线，任何异常默认拒单">
      <template #actions>
      <div class="flex items-center space-x-2">
        <button
          @click="runSandbox"
          :disabled="testing"
          class="btn-admin-secondary text-xs disabled:opacity-50"
        >
          <Play class="w-3 h-3 text-emerald-400" />
          <span>{{ testing ? '正在回归测试...' : '现场沙箱回归测试' }}</span>
        </button>
        <button
          v-if="auth.isSuperadmin"
          @click="openCreateModal"
          class="btn-admin-primary text-xs"
        >
          <Plus class="w-3.5 h-3.5" />
          <span>新建插件</span>
        </button>
        <span class="chip"><span class="dot dot-up" />Fail-Closed 防线</span>
      </div>
      </template>
    </PageHeader>

    <!-- Alert / Banner Message -->
    <!-- Loading State -->
    <div v-if="loading" class="py-12 text-center text-xs" style="color: var(--ink-2);">正在扫描加载物理拦截插件...</div>

    <!-- Plugins Pipeline List -->
    <div v-else class="space-y-3">
      <div
        v-for="(p, idx) in plugins"
        :key="p.filename"
        class="border rounded-xl p-4 sm:p-5 transition-all shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4"
        :style="{
          backgroundColor: 'var(--surface-2)',
          borderColor: p.enabled ? 'var(--line-2)' : 'var(--line-1)',
          opacity: p.enabled ? '1' : '0.6'
        }"
      >
        <!-- Left: Order & Meta -->
        <div class="flex items-start space-x-3.5 min-w-0 flex-1">
          <!-- Ordering Buttons -->
          <div class="flex flex-col space-y-1 shrink-0 pt-0.5">
            <button
              @click="movePlugin(idx, -1)"
              :disabled="idx === 0"
              class="p-1 rounded disabled:opacity-20 cursor-pointer transition-colors"
              style="color: var(--ink-2);"
              title="提高执行优先级"
            >
              <ArrowUp class="w-3.5 h-3.5" />
            </button>
            <button
              @click="movePlugin(idx, 1)"
              :disabled="idx === plugins.length - 1"
              class="p-1 rounded disabled:opacity-20 cursor-pointer transition-colors"
              style="color: var(--ink-2);"
              title="降低执行优先级"
            >
              <ArrowDown class="w-3.5 h-3.5" />
            </button>
          </div>

          <!-- Title, Description & Tags -->
          <div class="space-y-1.5 min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-2">
              <span class="w-6 h-6 rounded-md border font-bold flex items-center justify-center text-[11px]" style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-1);">
                #{{ idx + 1 }}
              </span>
              <h3 class="text-sm font-bold tracking-wide truncate" style="color: var(--ink-1);">
                {{ p.name || p.filename }}
              </h3>
              <span class="px-2 py-0.5 rounded text-[11px] border" style="background-color: var(--surface-3); border-color: var(--line-1); color: var(--ink-2);">
                {{ p.filename }}
              </span>
              <span v-if="p.version" class="px-1.5 py-0.2 rounded text-[11px] font-bold border" style="background-color: var(--accent-bg); color: var(--accent); border-color: var(--accent-line);">
                v{{ p.version }}
              </span>
              <span v-if="p.author" class="text-[11px]" style="color: var(--ink-3);">
                by {{ p.author }}
              </span>
            </div>

            <p class="text-xs font-sans leading-relaxed" style="color: var(--ink-2);">
              {{ p.description || '暂无详细描述' }}
            </p>

            <!-- Tags -->
            <div v-if="p.tags && p.tags.length > 0" class="flex flex-wrap gap-1.5 pt-1">
              <span
                v-for="t in p.tags"
                :key="t"
                class="px-2 py-0.5 rounded text-[11px] border"
                style="background-color: var(--surface-1); border-color: var(--line-1); color: var(--ink-2);"
              >
                {{ t }}
              </span>
            </div>

            <div v-if="p.error" class="text-[11px] text-rose-500 flex items-center space-x-1 pt-1">
              <AlertTriangle class="w-3.5 h-3.5 shrink-0" />
              <span>{{ p.error }}</span>
            </div>
          </div>
        </div>

        <!-- Right: Controls & Actions -->
        <div class="flex items-center justify-end space-x-2 shrink-0 border-t md:border-t-0 pt-3 md:pt-0" style="border-color: var(--line-1);">
          <button
            @click="openEditor(p)"
            class="flex items-center space-x-1 px-3 py-1.5 rounded-lg border font-bold cursor-pointer transition-all shadow-xs"
            style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);"
            title="查看或修改 Python 源码"
          >
            <Code class="w-3.5 h-3.5" style="color: var(--accent);" />
            <span>源码与规则</span>
          </button>

          <button
            v-if="auth.isSuperadmin && !p.filename.startsWith('0')"
            @click="deletePlugin(p)"
            class="p-2 rounded-lg hover:bg-rose-500/10 text-rose-500 cursor-pointer transition-colors"
            title="删除插件"
          >
            <Trash2 class="w-4 h-4" />
          </button>

          <button
            @click="togglePlugin(p)"
            class="cursor-pointer transition-colors p-1"
            :class="p.enabled ? 'text-emerald-500' : 'text-zinc-400'"
            :title="p.enabled ? '已启用 (点击停用)' : '已停用 (点击启用)'"
          >
            <ToggleRight v-if="p.enabled" class="w-6 h-6" />
            <ToggleLeft v-else class="w-6 h-6" />
          </button>
        </div>
      </div>
    </div>

    <!-- Code Editor Modal -->
    <div
      v-if="editorVisible"
      class="fixed inset-0 z-50 bg-black/70 backdrop-blur-xs flex items-center justify-center p-2.5 sm:p-4"
      @click.self="editorVisible = false"
    >
      <div class="border rounded-2xl p-4 sm:p-6 w-full max-w-4xl max-h-[94dvh] flex flex-col shadow-2xl space-y-3 sm:space-y-4 transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <!-- Modal Header -->
        <div class="flex items-start justify-between gap-2.5 pb-3 border-b" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2.5 min-w-0 flex-1">
            <div class="w-8 h-8 rounded-lg shrink-0 flex items-center justify-center border shadow-xs" style="background-color: var(--accent-bg); border-color: var(--accent-line); color: var(--accent);">
              <FileCode class="w-4 h-4" />
            </div>
            <div class="min-w-0 flex-1">
              <h3 class="text-xs sm:text-sm font-bold flex flex-wrap items-center gap-1.5" style="color: var(--ink-1);">
                <span class="truncate max-w-[180px] sm:max-w-[320px]">{{ editingName }}</span>
                <span class="text-[11px] sm:text-xs font-normal truncate max-w-[140px] sm:max-w-[200px]" style="color: var(--ink-3);">({{ editingFilename }})</span>
              </h3>
              <p class="text-[11px] hidden sm:block truncate mt-0.5" style="color: var(--ink-2);">Python 源码热更新，保存后下一轮决策实时执行</p>
            </div>
          </div>
          <div class="flex items-center space-x-1.5 shrink-0">
            <button
              @click="exportPluginCode(editingFilename, editingCode)"
              class="flex items-center space-x-1 px-2 sm:px-2.5 py-1 rounded-lg border text-[11px] sm:text-xs cursor-pointer shadow-xs transition-colors"
              style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-1);"
              title="导出当前 .py 脚本文件"
            >
              <Download class="w-3.5 h-3.5" />
              <span class="hidden sm:inline">导出 .py</span>
            </button>
            <button
              @click="editorVisible = false"
              class="p-1 rounded-lg hover:bg-zinc-500/10 cursor-pointer transition-colors"
              style="color: var(--ink-2);"
            >
              <X class="w-4 h-4" />
            </button>
          </div>
        </div>

        <div v-if="codeError" class="p-2.5 rounded-lg text-xs border break-all" style="background-color: var(--down-bg); border-color: var(--down-line); color: var(--down);">
          {{ codeError }}
        </div>

        <!-- Code Textarea -->
        <div class="flex-1 min-h-[220px] sm:min-h-[380px] h-[50dvh] flex flex-col">
          <textarea
            v-model="editingCode"
            class="flex-1 w-full border rounded-xl p-3 sm:p-4 text-[11px] sm:text-xs leading-relaxed outline-none resize-none select-text transition-colors"
            style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);"
            spellcheck="false"
          ></textarea>
        </div>

        <!-- Modal Footer -->
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 pt-3 border-t" style="border-color: var(--line-1);">
          <div class="text-[11px] sm:text-[11px] truncate" style="color: var(--ink-3);" title="def check_risk(package, decision, context) -> tuple[bool, str]">
            <span class="font-bold">接口契约:</span> <code class="opacity-80">check_risk(package, decision, ctx)</code>
          </div>
          <div class="flex items-center justify-end space-x-2 shrink-0">
            <button
              @click="editorVisible = false"
              class="px-3.5 sm:px-4 py-1.5 sm:py-2 rounded-xl border text-xs cursor-pointer shadow-xs transition-colors"
              style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-2);"
            >
              取消
            </button>
            <button
              @click="saveCode"
              :disabled="savingCode"
              class="flex items-center space-x-1.5 px-4 sm:px-5 py-1.5 sm:py-2 rounded-xl font-bold text-xs cursor-pointer transition-all shadow-xs disabled:opacity-50"
              style="background-color: var(--accent); color: var(--accent-ink);"
            >
              <Save class="w-4 h-4" />
              <span>{{ savingCode ? '正在保存...' : '保存代码并热加载' }}</span>
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Create Modal -->
    <div
      v-if="createModalVisible"
      class="fixed inset-0 z-50 bg-black/70 backdrop-blur-xs flex items-center justify-center p-2.5 sm:p-4"
      @click.self="createModalVisible = false"
    >
      <div class="border rounded-2xl p-4 sm:p-6 w-full max-w-2xl max-h-[94dvh] overflow-y-auto flex flex-col shadow-2xl space-y-3 sm:space-y-4 transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center justify-between pb-3 border-b" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2.5">
            <div class="w-7 h-7 rounded-lg flex items-center justify-center border shadow-xs" style="background-color: var(--accent-bg); border-color: var(--accent-line); color: var(--accent);">
              <Sparkles class="w-4 h-4" />
            </div>
            <div>
              <h3 class="text-xs sm:text-sm font-bold" style="color: var(--ink-1);">新建物理拦截插件</h3>
              <p class="text-[11px] hidden sm:block" style="color: var(--ink-2);">编写自定义 Python 拦截规则，适配策略广场规范</p>
            </div>
          </div>
          <button @click="createModalVisible = false" class="cursor-pointer p-1" style="color: var(--ink-2);">
            <X class="w-4 h-4" />
          </button>
        </div>

        <div v-if="createError" class="p-2.5 rounded-lg text-xs border break-all" style="background-color: var(--down-bg); border-color: var(--down-line); color: var(--down);">
          {{ createError }}
        </div>

        <div>
          <label class="block text-xs font-bold mb-1.5" style="color: var(--ink-1);">插件文件名 (.py)</label>
          <input
            v-model="newFilename"
            type="text"
            class="w-full border rounded-xl px-3 py-2 text-xs outline-none transition-colors"
            style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);"
            placeholder="如: my_volatility_filter.py"
          />
        </div>

        <div class="flex-1 min-h-[200px] sm:min-h-[280px] flex flex-col">
          <label class="block text-xs font-bold mb-1.5" style="color: var(--ink-1);">插件 Python 源码</label>
          <textarea
            v-model="newCode"
            class="flex-1 w-full border rounded-xl p-3 sm:p-3.5 text-[11px] sm:text-xs leading-relaxed outline-none resize-y transition-colors min-h-[160px]"
            style="background-color: var(--surface-input); border-color: var(--line-1); color: var(--ink-1);"
            spellcheck="false"
          ></textarea>
        </div>

        <div class="flex items-center justify-end space-x-2 pt-3 border-t" style="border-color: var(--line-1);">
          <button
            @click="createModalVisible = false"
            class="px-3.5 sm:px-4 py-1.5 sm:py-2 rounded-xl border text-xs cursor-pointer shadow-xs"
            style="background-color: var(--surface-1); border-color: var(--line-2); color: var(--ink-2);"
          >
            取消
          </button>
          <button
            @click="submitCreate"
            class="px-4 sm:px-5 py-1.5 sm:py-2 rounded-xl font-bold text-xs cursor-pointer transition-all shadow-xs"
            style="background-color: var(--accent); color: var(--accent-ink);"
          >
            创建并加入管线
          </button>
        </div>
      </div>
    </div>

    <!-- Sandbox Test Results Modal -->
    <div
      v-if="testModalVisible && testResults"
      class="fixed inset-0 z-50 bg-black/70 backdrop-blur-xs flex items-center justify-center p-2.5 sm:p-4"
      @click.self="testModalVisible = false"
    >
      <div class="border rounded-2xl p-4 sm:p-6 w-full max-w-3xl max-h-[92dvh] overflow-y-auto shadow-2xl space-y-3 sm:space-y-4 transition-colors" style="background-color: var(--surface-2); border-color: var(--line-1);">
        <div class="flex items-center justify-between pb-3 border-b" style="border-color: var(--line-1);">
          <div class="flex items-center space-x-2.5">
            <div class="w-7 h-7 rounded-lg flex items-center justify-center border shadow-xs" style="background-color: var(--up-bg); border-color: var(--up-line); color: var(--up);">
              <Play class="w-4 h-4" />
            </div>
            <div>
              <h3 class="text-xs sm:text-sm font-bold" style="color: var(--ink-1);">沙箱拦截回归测试报告</h3>
              <p class="text-[11px]" style="color: var(--ink-2);">
                已激活 {{ testResults.enabled_plugins_count }}/{{ testResults.total_plugins_count }} 个拦截插件 · 总执行耗时 {{ testResults.duration_total_ms }}ms
              </p>
            </div>
          </div>
          <button @click="testModalVisible = false" class="cursor-pointer p-1" style="color: var(--ink-2);">
            <X class="w-4 h-4" />
          </button>
        </div>

        <div class="space-y-3">
          <div
            v-for="(r, i) in testResults.results"
            :key="i"
            class="p-3 sm:p-3.5 rounded-xl border transition-all"
            :style="r.intercepted
              ? { backgroundColor: 'var(--warn-bg)', borderColor: 'var(--warn-line)' }
              : { backgroundColor: 'var(--up-bg)', borderColor: 'var(--up-line)' }"
          >
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-1.5 mb-1.5">
              <span class="text-xs font-bold" style="color: var(--ink-1);">{{ r.scenario }}</span>
              <div class="flex items-center space-x-2">
                <span class="text-[11px]" style="color: var(--ink-3);">{{ r.duration_ms }}ms</span>
                <span
                  class="px-2 py-0.5 rounded text-[11px] font-bold border"
                  :style="r.intercepted
                    ? { backgroundColor: 'var(--surface-2)', borderColor: 'var(--warn-line)', color: 'var(--warn)' }
                    : { backgroundColor: 'var(--surface-2)', borderColor: 'var(--up-line)', color: 'var(--up)' }"
                >
                  {{ r.intercepted ? '🛑 已成功物理拦截 (WAIT)' : '🟢 顺势放行通过' }}
                </span>
              </div>
            </div>
            <div class="text-[11px] flex flex-wrap items-center gap-x-3 gap-y-1" style="color: var(--ink-2);">
              <span>原始意向: <strong style="color: var(--ink-1);">{{ r.raw_action }}</strong></span>
              <span>最终指令: <strong :style="{ color: r.final_action === 'WAIT' ? 'var(--warn)' : 'var(--up)' }">{{ r.final_action }}</strong></span>
              <span v-if="r.risk_reward !== '--'">盈亏比: {{ r.risk_reward }}</span>
            </div>
            <div v-if="r.reason" class="text-[11px] mt-1 font-sans break-words" style="color: var(--warn);">
              拦截审计：{{ r.reason }}
            </div>
          </div>
        </div>

        <div class="flex justify-end pt-3 border-t" style="border-color: var(--line-1);">
          <button
            @click="testModalVisible = false"
            class="px-4 sm:px-5 py-1.5 sm:py-2 rounded-xl text-xs font-bold cursor-pointer transition-all shadow-xs"
            style="background-color: var(--accent); color: var(--accent-ink);"
          >
            关闭测试报告
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

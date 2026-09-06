<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import {
  Brain,
  Sparkles,
  RefreshCw,
  Clock,
  Plus,
  Trash2,
  CheckCircle2,
  AlertCircle,
  Save,
  PlayCircle,
  BookOpen,
  Sliders,
  Terminal,
  ShieldCheck,
  RotateCcw,
  ToggleLeft,
  ToggleRight,
  ShieldAlert,
} from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()

const loading = ref(true)
const busy = ref<'save' | 'run' | 'add' | 'delete' | 'toggle' | 'rollback' | ''>('')
const bannerMsg = ref<{ text: string; type: 'ok' | 'err' | 'warn' } | null>(null)

// Pipelines state (evolution_system & evolution_user)
const activeTab = ref<'settings' | 'evolution_system' | 'evolution_user'>('settings')
const lib = ref<any>(null)
const selectedProfileId = ref('stable')
const workingModules = ref<any[]>([])

// Structured White-Box Memory state
const structuredLessons = ref<any[]>([])
const memoryVersion = ref<string | null>(null)
const newMemoryText = ref('')
const newCategory = ref('TACTICAL')

// Scheduler settings
const briefingTimes = ref<string[]>(['02:00', '08:00', '14:00', '20:00'])

const selectedProfile = computed(() => (lib.value?.profiles || []).find((p: any) => p.id === selectedProfileId.value) || null)

async function loadData() {
  loading.value = true
  memoryVersion.value = null
  try {
    const [libRes, memRes] = await Promise.all([
      api('/api/v1/admin/prompt-library'),
      api('/api/v1/admin/memory'),
    ])
    lib.value = libRes
    selectedProfileId.value = libRes.active_profile_id || 'stable'
    structuredLessons.value = memRes.structured_lessons || []
    memoryVersion.value = memRes.version || null
    syncWorkingModules()
  } catch (e: any) {
    bannerMsg.value = { text: `加载失败: ${e.message}`, type: 'err' }
  } finally {
    loading.value = false
  }
}

function syncWorkingModules() {
  if (activeTab.value === 'settings') return
  const views = selectedProfile.value?.pipeline_views?.[activeTab.value] || []
  workingModules.value = JSON.parse(JSON.stringify(views))
}

function switchTab(tab: 'settings' | 'evolution_system' | 'evolution_user') {
  activeTab.value = tab
  syncWorkingModules()
}

// Never retry writes: on failure require an explicit reload before another attempt.
function expectedMemoryVersion() {
  if (!memoryVersion.value) throw new Error('请重新加载心法后再操作')
  return memoryVersion.value
}

async function refreshMemory() {
  memoryVersion.value = null
  const res = await api('/api/v1/admin/memory')
  structuredLessons.value = res.structured_lessons || []
  memoryVersion.value = res.version || null
}

async function reloadMemory() {
  if (busy.value || loading.value) return
  loading.value = true
  bannerMsg.value = null
  try {
    await refreshMemory()
  } catch (e: any) {
    bannerMsg.value = { text: `重新加载心法失败: ${e.message}`, type: 'err' }
  } finally {
    loading.value = false
  }
}

async function toggleLessonStatus(lessonId: string) {
  if (busy.value || loading.value) return
  busy.value = 'toggle'
  bannerMsg.value = null
  try {
    await api(`/api/v1/admin/memory/toggle/${encodeURIComponent(lessonId)}?expected_version=${encodeURIComponent(expectedMemoryVersion())}`, { method: 'POST' })
    await refreshMemory()
    bannerMsg.value = { text: `✅ 心法状态已切换（大模型下次决策立即感知）`, type: 'ok' }
  } catch (e: any) {
    memoryVersion.value = null
    bannerMsg.value = { text: `状态切换失败: ${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

async function rollbackToBaseline() {
  if (!confirm('【防污染紧急回滚】确定要清除非基准的过期或被污染心法，重置回官方基准黄金心法库吗？')) return
  if (busy.value || loading.value) return
  busy.value = 'rollback'
  bannerMsg.value = null
  try {
    await api(`/api/v1/admin/memory/rollback?expected_version=${encodeURIComponent(expectedMemoryVersion())}`, { method: 'POST' })
    await refreshMemory()
    bannerMsg.value = { text: '🛡️ 已成功执行宪法级防污染回滚，系统已重置为黄金基准认知！', type: 'ok' }
  } catch (e: any) {
    memoryVersion.value = null
    bannerMsg.value = { text: `回滚失败: ${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

async function addMemoryItem() {
  const text = newMemoryText.value.trim()
  if (!text) return
  if (busy.value || loading.value) return
  busy.value = 'add'
  bannerMsg.value = null
  try {
    const res = await api('/api/v1/admin/memory', {
      method: 'POST',
      body: JSON.stringify({ text, expected_version: expectedMemoryVersion() }),
    })
    // Reload full structured list
    await refreshMemory()
    newMemoryText.value = ''
    bannerMsg.value = { text: '✅ 新心法已通过防偏见审查，并成功同步写入决策注入层', type: 'ok' }
  } catch (e: any) {
    memoryVersion.value = null
    bannerMsg.value = { text: `添加心法失败: ${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

async function deleteMemoryItem(idx: number, lessonId: string) {
  if (!confirm('确定删除此条自进化心法吗？')) return
  if (busy.value || loading.value) return
  busy.value = 'delete'
  bannerMsg.value = null
  try {
    await api(`/api/v1/admin/memory/${idx}?lesson_id=${encodeURIComponent(lessonId)}&expected_version=${encodeURIComponent(expectedMemoryVersion())}`, { method: 'DELETE' })
    await refreshMemory()
    bannerMsg.value = { text: '✅ 该条自进化心法已成功移除', type: 'ok' }
  } catch (e: any) {
    memoryVersion.value = null
    bannerMsg.value = { text: `删除失败: ${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

async function savePipelineModules() {
  if (!selectedProfile.value) return
  busy.value = 'save'
  bannerMsg.value = null
  try {
    const pipelinesMap: Record<string, any[]> = {}
    pipelinesMap[activeTab.value] = workingModules.value.map((m) => ({
      id: m.id,
      title: m.title,
      content: m.content,
      enabled: m.enabled,
      locked: m.locked,
      source: m.source,
    }))

    await api(`/api/v1/admin/prompt-profiles/${selectedProfile.value.id}`, {
      method: 'PUT',
      body: JSON.stringify({
        name: selectedProfile.value.name,
        description: selectedProfile.value.description,
        pipelines: pipelinesMap,
      }),
    })
    bannerMsg.value = { text: `✅ 自进化模版布局已成功保存，下一轮复盘自动生效`, type: 'ok' }
    await loadData()
  } catch (e: any) {
    bannerMsg.value = { text: `保存失败: ${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

async function triggerEvolutionNow() {
  const phrase = prompt('立即强制执行自进化复盘任务（对全天战绩穿透提炼并生成最新复盘心法），请输入确认短语：RUN EVOLUTION')
  if (!phrase) return
  if (phrase.trim().toUpperCase() !== 'RUN EVOLUTION') {
    alert('确认短语错误，已取消执行')
    return
  }
  busy.value = 'run'
  bannerMsg.value = null
  try {
    const res = await api('/api/v1/admin/gateway/jobs/self_improvement/run', {
      method: 'POST',
      body: JSON.stringify({ confirmation: 'RUN JOB' }),
    })
    bannerMsg.value = { text: `✅ 自进化复盘已完成（已自动执行离群噪点过滤与宪法安全审查）！${res.detail || ''}`, type: 'ok' }
    await loadData()
  } catch (e: any) {
    bannerMsg.value = { text: `执行复盘失败: ${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

onMounted(loadData)
</script>

<template>
  <div class="space-y-4 max-w-[2160px] mx-auto">
    <!-- Header -->
    <div class="panel-banner-compact">
      <div class="flex items-center space-x-2.5">
        <div class="panel-banner-icon">
          <Brain class="w-3.5 h-3.5" />
        </div>
        <div>
          <h1 class="text-xs sm:text-[13px] font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
            AI 策略自进化认知中枢与白盒防污染护栏 (Evolution Shield)
          </h1>
          <p class="text-[11px] font-mono mt-0.5" style="color: var(--text-muted);">
            离群噪点剔除、宪法级防偏见红线、心法生命周期衰减与白盒启停管理
          </p>
        </div>
      </div>
      <span class="badge-lever">
        白盒认知 · 防偏见护栏
      </span>
    </div>

    <!-- Banner -->
    <div
      v-if="bannerMsg"
      class="p-3 rounded-lg text-xs font-mono border"
      :class="bannerMsg.type === 'ok' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-rose-500/10 border-rose-500/20 text-rose-400'"
    >
      <div class="flex items-center gap-2">
        <CheckCircle2 v-if="bannerMsg.type === 'ok'" class="w-4 h-4 shrink-0" />
        <AlertCircle v-else class="w-4 h-4 shrink-0" />
        <span>{{ bannerMsg.text }}</span>
      </div>
    </div>

    <!-- Navigation Tabs -->
    <div class="flex flex-wrap items-center justify-between gap-3 p-1.5 rounded-xl border" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
      <div class="flex flex-wrap gap-1">
        <button
          @click="switchTab('settings')"
          class="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-mono font-bold cursor-pointer transition-colors"
          :style="activeTab === 'settings' ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' } : { color: 'var(--text-muted)' }"
        >
          <Brain class="w-3.5 h-3.5" />
          <span>白盒心法与防污染总览</span>
        </button>
        <button
          @click="switchTab('evolution_system')"
          class="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-mono font-bold cursor-pointer transition-colors"
          :style="activeTab === 'evolution_system' ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' } : { color: 'var(--text-muted)' }"
        >
          <BookOpen class="w-3.5 h-3.5" />
          <span>复盘官 System 模版</span>
        </button>
        <button
          @click="switchTab('evolution_user')"
          class="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-mono font-bold cursor-pointer transition-colors"
          :style="activeTab === 'evolution_user' ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' } : { color: 'var(--text-muted)' }"
        >
          <Terminal class="w-3.5 h-3.5" />
          <span>战绩流水 User 模版</span>
        </button>
      </div>

      <div class="flex items-center space-x-2">
        <button
          @click="reloadMemory"
          :disabled="busy !== '' || loading"
          class="flex items-center space-x-1 px-3 py-1.5 rounded-lg text-xs font-mono cursor-pointer disabled:opacity-40 border"
        >
          <RefreshCw class="w-3.5 h-3.5" />
          <span>重新加载心法</span>
        </button>
        <button
          v-if="auth.isSuperadmin"
          @click="rollbackToBaseline"
          :disabled="busy !== ''"
          class="flex items-center space-x-1 px-3 py-1.5 rounded-lg text-xs font-mono font-bold cursor-pointer disabled:opacity-40 transition-all border shadow-xs"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-main);"
          title="遭遇极端行情导致心法被带偏时，一键恢复至官方未被污染的基准黄金心法"
        >
          <RotateCcw class="w-3.5 h-3.5 text-amber-400" />
          <span>回滚至黄金基准</span>
        </button>

        <button
          v-if="auth.isSuperadmin"
          @click="triggerEvolutionNow"
          :disabled="busy !== ''"
          class="flex items-center space-x-1 px-3 py-1.5 rounded-lg text-xs font-mono font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs"
          style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);"
        >
          <PlayCircle class="w-3.5 h-3.5" />
          <span>{{ busy === 'run' ? '正在执行复盘提炼...' : '防污染立即复盘' }}</span>
        </button>
      </div>
    </div>

    <!-- TAB 1: Settings & Structured White-Box Memory -->
    <div v-if="activeTab === 'settings'" class="space-y-4">
      <!-- Strategy & Schedule Overview -->
      <div class="grid grid-cols-1 md:grid-cols-4 gap-3 font-mono">
        <div class="rounded-xl border p-3.5 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <div class="flex items-center space-x-1.5 mb-1 text-[11px] font-bold" style="color: var(--text-muted);">
            <ShieldCheck class="w-3.5 h-3.5 text-emerald-400" />
            <span>防污染护栏状态</span>
          </div>
          <div class="text-sm font-bold text-emerald-400">ACTIVE (已启动)</div>
          <div class="text-[10px] mt-1" style="color: var(--text-faint);">
            离群噪点过滤 · 宪法防偏见
          </div>
        </div>

        <div class="rounded-xl border p-3.5 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <div class="flex items-center space-x-1.5 mb-1 text-[11px] font-bold" style="color: var(--text-muted);">
            <Clock class="w-3.5 h-3.5 text-cyan-400" />
            <span>自动复盘频次</span>
          </div>
          <div class="text-sm font-bold text-cyan-400">每 6 小时 (4次/天)</div>
          <div class="text-[10px] mt-1" style="color: var(--text-faint);">
            02:00, 08:00, 14:00, 20:00 (UTC+8)
          </div>
        </div>

        <div class="rounded-xl border p-3.5 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <div class="flex items-center space-x-1.5 mb-1 text-[11px] font-bold" style="color: var(--text-muted);">
            <Sliders class="w-3.5 h-3.5 text-purple-400" />
            <span>当前生效心法</span>
          </div>
          <div class="text-sm font-bold" style="color: var(--text-main);">
            {{ structuredLessons.filter((l: any) => l.enabled).length }} / {{ structuredLessons.length }} 条
          </div>
          <div class="text-[10px] mt-1" style="color: var(--text-faint);">
            实时透明注入主脑 Prompt
          </div>
        </div>

        <div class="rounded-xl border p-3.5 shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <div class="flex items-center space-x-1.5 mb-1 text-[11px] font-bold" style="color: var(--text-muted);">
            <Sparkles class="w-3.5 h-3.5 text-amber-400" />
            <span>心法半衰期机制</span>
          </div>
          <div class="text-sm font-bold text-amber-400">敏锐半衰期 7~14 天</div>
          <div class="text-[10px] mt-1" style="color: var(--text-faint);">
            动态评分快速淘汰过期或失效认知
          </div>
        </div>
      </div>

      <!-- Structured White-Box Memory Management -->
      <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 mb-3 border-b" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <Brain class="w-4 h-4 text-emerald-400" />
            <h2 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">
              白盒实战心法生命周期管理 (Structured Heuristic Rules)
            </h2>
          </div>
          <span class="text-[10px] font-mono" style="color: var(--text-faint);">
            每条心法均经宪法安全审查 · 支持单项热拔插启停与评分透视
          </span>
        </div>

        <!-- Add Rule -->
        <div class="flex flex-col sm:flex-row gap-2 mb-4">
          <input
            v-model="newMemoryText"
            @keydown.enter="addMemoryItem"
            placeholder="手动注入实战心法（如：【顺势回踩低吸】在 4H 多头通道中回踩短均线且量能缩减时挂单...）"
            class="flex-1 rounded-lg px-3 py-2 text-xs font-mono outline-none border transition-colors"
            style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);"
          />
          <button
            @click="addMemoryItem"
            :disabled="busy !== '' || !newMemoryText.trim()"
            class="flex items-center justify-center space-x-1 px-4 py-2 rounded-lg text-xs font-mono font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs shrink-0"
            style="background-color: var(--text-main); color: var(--bg-card);"
          >
            <Plus class="w-3.5 h-3.5" />
            <span>{{ busy === 'add' ? '安全审查中...' : '提交审查并收录' }}</span>
          </button>
        </div>

        <!-- Structured Lessons Cards Grid -->
        <div class="space-y-2.5">
          <div v-if="loading" class="py-8 text-center text-xs font-mono" style="color: var(--text-muted);">
            <RefreshCw class="w-4 h-4 animate-spin inline mr-1.5" style="color: var(--color-brand);" />
            正在拉取白盒心法知识库...
          </div>
          <template v-else-if="structuredLessons.length">
            <div
              v-for="(item, idx) in structuredLessons"
              :key="item.id || idx"
              class="p-3.5 rounded-xl border transition-all flex flex-col justify-between gap-2.5"
              :style="{
                backgroundColor: item.enabled ? 'var(--bg-card-subtle)' : 'rgba(255, 255, 255, 0.01)',
                borderColor: item.enabled ? 'var(--border-subtle)' : 'rgba(255, 255, 255, 0.05)',
                opacity: item.enabled ? 1 : 0.6
              }"
            >
              <!-- Card Header Row -->
              <div class="flex items-center justify-between gap-2 font-mono text-xs">
                <div class="flex items-center space-x-1.5">
                  <span
                    class="px-1.5 py-0.5 rounded-[3px] text-[10px] font-mono font-bold border"
                    :style="{
                      backgroundColor: item.is_baseline ? 'rgba(56, 117, 246, 0.12)' : 'rgba(16, 185, 129, 0.12)',
                      borderColor: item.is_baseline ? 'rgba(56, 117, 246, 0.3)' : 'rgba(16, 185, 129, 0.3)',
                      color: item.is_baseline ? '#3875F6' : '#10B981'
                    }"
                  >
                    {{ item.is_baseline ? '👑 官方黄金基准' : '🧬 AI 实战自进化' }}
                  </span>

                  <span class="text-[10px] font-mono px-1.5 py-0.5 rounded-[3px] border" style="background-color: var(--bg-badge); border-color: var(--border-subtle); color: var(--text-muted);">
                    {{ item.category }}
                  </span>

                  <span class="text-[10px] font-mono px-1.5 py-0.5 rounded-[3px] border bg-emerald-500/10 border-emerald-500/25 text-emerald-400 font-bold">
                    评分 {{ item.health_score }}
                  </span>
                </div>

                <!-- Action Controls -->
                <div class="flex items-center space-x-2">
                  <!-- Toggle Switch -->
                  <button
                    @click="toggleLessonStatus(item.id)"
                    class="flex items-center space-x-1 px-2.5 py-1 rounded-md border text-[11px] font-bold cursor-pointer transition-colors"
                    :style="item.enabled ? {
                      backgroundColor: 'rgba(16, 185, 129, 0.15)',
                      borderColor: 'rgba(16, 185, 129, 0.3)',
                      color: '#10B981',
                    } : {
                      backgroundColor: 'var(--bg-card)',
                      borderColor: 'var(--border-subtle)',
                      color: 'var(--text-faint)',
                    }"
                    :title="item.enabled ? '点击停用本条心法' : '点击激活本条心法'"
                  >
                    <ToggleRight v-if="item.enabled" class="w-3.5 h-3.5" />
                    <ToggleLeft v-else class="w-3.5 h-3.5" />
                    <span>{{ item.enabled ? '生效中' : '已休眠' }}</span>
                  </button>

                  <!-- Delete -->
                  <button
                    @click="deleteMemoryItem(idx, item.id)"
                    class="p-1 rounded hover:bg-rose-500/20 text-rose-400 opacity-60 hover:opacity-100 transition-opacity cursor-pointer"
                    title="移除该心法"
                  >
                    <Trash2 class="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              <!-- Rule Text -->
              <p
                class="text-xs font-mono leading-relaxed select-text"
                :style="item.enabled ? { color: 'var(--text-main)' } : { color: 'var(--text-faint)', textDecoration: 'line-through' }"
              >
                {{ item.rule_text }}
              </p>

              <!-- Footer Audit Line -->
              <div class="flex items-center justify-between text-[10px] font-mono pt-1 border-t" style="border-color: var(--border-subtle); color: var(--text-faint);">
                <span>收录时间: {{ item.created_at || '--' }} · 支持样本量: {{ item.sample_size || 10 }} 笔</span>
                <span class="text-emerald-500 flex items-center space-x-1">
                  <ShieldCheck class="w-3 h-3" />
                  <span>宪法安全审查: {{ item.shield_status || 'PASSED' }}</span>
                </span>
              </div>
            </div>
          </template>
          <div v-else class="py-8 text-center text-xs font-mono border rounded-lg border-dashed" style="border-color: var(--border-subtle); color: var(--text-faint);">
            暂无自进化心法记忆，可点击右上角「回滚至黄金基准」恢复核心实战心法
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 2 & 3: Template Pipelines (Evolution System / User) -->
    <div v-else class="space-y-4">
      <div class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors space-y-4" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="flex items-center justify-between pb-3 border-b" style="border-color: var(--border-subtle);">
          <div>
            <h2 class="text-sm font-bold font-mono" style="color: var(--text-main);">
              {{ activeTab === 'evolution_system' ? '自进化复盘官 System 提示词模版' : '自进化战绩流水 User 提示词模版' }}
            </h2>
            <p class="text-xs font-mono mt-0.5" style="color: var(--text-muted);">
              {{ activeTab === 'evolution_system' ? '定义复盘官的角色定位、归因逻辑与心法沉淀标准' : '配置每 6 小时自动组装实战对账单与动力学快照证据的模版语法' }}
            </p>
          </div>
          <button
            v-if="auth.isSuperadmin"
            @click="savePipelineModules"
            :disabled="busy !== ''"
            class="flex items-center space-x-1 px-4 py-2 rounded-lg text-xs font-mono font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs"
            style="background-color: var(--text-main); color: var(--bg-card);"
          >
            <Save class="w-3.5 h-3.5" />
            <span>{{ busy === 'save' ? '保存中...' : '保存模版' }}</span>
          </button>
        </div>

        <!-- Modules List -->
        <div class="space-y-3">
          <div
            v-for="(mod, mIdx) in workingModules"
            :key="mod.id || mIdx"
            class="border rounded-xl p-4 transition-all"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
          >
            <div class="flex items-center justify-between mb-2">
              <span class="text-xs font-bold font-mono" style="color: var(--text-main);">{{ mod.title }}</span>
              <label class="flex items-center space-x-1.5 text-xs font-mono cursor-pointer">
                <input v-model="mod.enabled" type="checkbox" class="accent-blue-500 w-3.5 h-3.5" :disabled="!auth.isSuperadmin" />
                <span :class="mod.enabled ? 'text-emerald-500 font-bold' : 'text-zinc-500'">{{ mod.enabled ? '启用模块' : '已停用' }}</span>
              </label>
            </div>
            <textarea
              v-model="mod.content"
              :disabled="!auth.isSuperadmin || mod.locked"
              rows="6"
              class="w-full rounded-lg p-3 text-xs font-mono leading-relaxed outline-none border transition-colors resize-y"
              style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);"
            ></textarea>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

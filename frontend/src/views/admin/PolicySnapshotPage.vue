<script setup lang="ts">
import { ref, onMounted , watch} from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
import SaveBar from '../../components/admin/SaveBar.vue'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import {
  Layers,
  FileText,
  Sparkles,
  ShieldCheck,
  Users,
  RefreshCw,
  Hash,
  Activity,
  Clock,
  ArrowUpRight,
  BookmarkPlus,
  RotateCcw,
  Archive,
  History,
  CheckCircle2,
  AlertCircle,
  FolderDown,
  FileCode,
  Trash2,
} from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()

const loading = ref(true)
const refreshing = ref(false)
const archiving = ref(false)
const restoring = ref(false)
const deleting = ref<string | null>(null)

const snapshotData = ref<any>(null)
const archives = ref<any[]>([])
const errorMsg = ref<string | null>(null)
const bannerMsg = ref<{ text: string; type: 'ok' | 'err' | 'warn' } | null>(null)
const bannerSeq = ref(0)
watch(bannerMsg, () => { bannerSeq.value++ })

// Archive Dialog State
const showArchiveModal = ref(false)
const archiveName = ref('')
const archiveDesc = ref('')

async function fetchSnapshot() {
  refreshing.value = true
  errorMsg.value = null
  try {
    const [snapRes, arcRes] = await Promise.all([
      api('/api/v1/admin/policy/current-snapshot'),
      api('/api/v1/admin/policy/archives'),
    ])
    if (snapRes && snapRes.ok) {
      snapshotData.value = snapRes
    }
    if (arcRes && arcRes.ok) {
      archives.value = arcRes.archives || []
    }
  } catch (err: any) {
    errorMsg.value = err.message || '获取策略版本快照失败'
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function saveArchive() {
  if (!auth.isSuperadmin) {
    bannerMsg.value = { text: '仅超级管理员可归档策略版本', type: 'err' }
    return
  }
  if (!archiveName.value.trim()) {
    bannerMsg.value = { text: '请输入策略归档名称', type: 'warn' }
    return
  }
  archiving.value = true
  try {
    const res = await api('/api/v1/admin/policy/archive', {
      method: 'POST',
      body: JSON.stringify({
        name: archiveName.value.trim(),
        description: archiveDesc.value.trim(),
      }),
    })
    if (res && res.ok) {
      bannerMsg.value = { text: `🎉 策略版本已归档入库：${res.entry?.name} (#${res.entry?.policy_hash})`, type: 'ok' }
      showArchiveModal.value = false
      archiveName.value = ''
      archiveDesc.value = ''
      await fetchSnapshot()
    }
  } catch (err: any) {
    bannerMsg.value = { text: `归档失败: ${err.message}`, type: 'err' }
  } finally {
    archiving.value = false
  }
}

async function restorePolicy(hash: string, name: string) {
  if (!auth.isSuperadmin) return
  if (!confirm(`确定要将当前策略原子回滚至【${name}】(#${hash}) 吗？\n将同时恢复对应的提示词、心法、拦截器及投委会配置！`)) {
    return
  }
  restoring.value = true
  try {
    const res = await api('/api/v1/admin/policy/restore', {
      method: 'POST',
      body: JSON.stringify({ policy_hash: hash }),
    })
    if (res && res.ok) {
      bannerMsg.value = { text: `✅ 策略已原子回滚至【${name}】(#${hash})！下一决策周期将立即生效`, type: 'ok' }
      await fetchSnapshot()
    }
  } catch (err: any) {
    bannerMsg.value = { text: `回滚失败: ${err.message}`, type: 'err' }
  } finally {
    restoring.value = false
  }
}

async function deleteArchive(hash: string, name: string) {
  if (!auth.isSuperadmin) return
  if (!confirm(`确定要彻底删除已归档的策略版本【${name}】(#${hash}) 吗？\n删除后不可恢复！`)) {
    return
  }
  deleting.value = hash
  try {
    const res = await api(`/api/v1/admin/policy/archive/${hash}`, {
      method: 'DELETE',
    })
    if (res && res.ok) {
      bannerMsg.value = { text: `🗑️ 策略版本【${name}】已成功删除`, type: 'ok' }
      await fetchSnapshot()
    }
  } catch (err: any) {
    bannerMsg.value = { text: `删除失败: ${err.message}`, type: 'err' }
  } finally {
    deleting.value = null
  }
}

function formatTimestamp(ts: number) {
  if (!ts) return '未记录'
  const d = new Date(ts * 1000)
  return d.toLocaleString()
}

onMounted(() => {
  fetchSnapshot()
})
</script>

<template>
  <div class="space-y-4">
    <!-- Notice Banner -->
    <SaveBar
          :type="bannerMsg?.type || 'ok'"
          :text="bannerMsg?.text || ''"
          :nonce="bannerSeq"
          @dismiss="bannerMsg = null"
        />
    <!-- Header Control Station -->
    <div
      class="rounded-2xl border p-4 sm:p-5 2xl:p-6 shadow-xs flex flex-col sm:flex-row sm:items-center justify-between gap-4"
      style="background-color: var(--bg-card); border-color: var(--border-subtle);"
    >
      <div class="flex items-center space-x-3">
        <div
          class="p-2.5 2xl:p-3 rounded-xl border"
          style="background-color: var(--color-brand-bg); border-color: var(--color-brand-border); color: var(--color-brand);"
        >
          <Layers class="w-5 h-5 2xl:w-6 2xl:h-6" />
        </div>
        <div>
          <div class="flex items-center space-x-2">
            <h2 class="text-sm 2xl:text-base font-bold font-mono" style="color: var(--text-main);">
              {{ t('admin.nPolicy') }}
            </h2>
            <span
              v-if="snapshotData?.policy_version"
              class="text-[11px] 2xl:text-xs font-mono font-bold px-2 py-0.5 rounded border"
              style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);"
            >
              {{ snapshotData.policy_version }}
            </span>
          </div>
          <p class="text-xs 2xl:text-sm font-mono mt-0.5" style="color: var(--text-muted);"> 策略大一统版本快照 (Policy Snapshot Workbench) —— 四大策略单元（提示词、自进化、物理拦截、模型委员会）的不可变指纹聚合与具名归档/一键回滚。 </p>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2 shrink-0">
        <!-- Archive Button -->
        <button
          @click="showArchiveModal = true"
          :disabled="!auth.isSuperadmin"
          class="flex items-center space-x-1.5 px-3 py-1.5 2xl:px-4 2xl:py-2 rounded-xl text-xs 2xl:text-sm font-mono font-bold cursor-pointer transition-all shadow-xs"
          style="background-color: var(--text-main); color: var(--bg-card);"
        >
          <BookmarkPlus class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" />
          <span>归档为策略版本</span>
        </button>

        <!-- Refresh Button -->
        <button
          @click="fetchSnapshot"
          :disabled="refreshing"
          class="flex items-center space-x-1.5 px-3 py-1.5 2xl:px-4 2xl:py-2 rounded-xl border text-xs 2xl:text-sm font-mono font-bold cursor-pointer transition-all shadow-xs"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);"
        >
          <RefreshCw class="w-3.5 h-3.5 2xl:w-4 2xl:h-4" :class="{ 'animate-spin': refreshing }" />
          <span>{{ refreshing ? '抓取中...' : '刷新指纹' }}</span>
        </button>
      </div>
    </div>

    <!-- Error Alert -->
    <div
      v-if="errorMsg"
      class="p-3 rounded-xl text-xs font-mono border bg-rose-500/10 border-rose-500/20 text-rose-400"
    >
      {{ errorMsg }}
    </div>

    <!-- Loading Skeleton -->
    <div v-if="loading" class="py-12 text-center text-xs font-mono text-zinc-500">
      正在计算并聚合四大策略单元实时指纹...
    </div>

    <div v-else-if="snapshotData?.snapshot" class="space-y-4 2xl:space-y-6">
      <!-- 1. Master Identity Bar -->
      <div
        class="grid grid-cols-1 sm:grid-cols-3 gap-3 2xl:gap-4 p-4 2xl:p-5 rounded-2xl border font-mono text-xs 2xl:text-sm"
        style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
      >
        <div class="flex items-center space-x-2">
          <Hash class="w-4 h-4 2xl:w-5 2xl:h-5 text-purple-400 shrink-0" />
          <div>
            <div class="text-[11px] 2xl:text-xs text-[var(--text-muted)]">当前活跃策略版本 (Active Version)</div>
            <div class="font-bold text-sm 2xl:text-base mt-0.5" style="color: var(--text-main);">
              {{ snapshotData.snapshot.policy_version }}
            </div>
          </div>
        </div>
        <div class="flex items-center space-x-2">
          <Activity class="w-4 h-4 2xl:w-5 2xl:h-5 text-cyan-400 shrink-0" />
          <div>
            <div class="text-[11px] 2xl:text-xs text-[var(--text-muted)]">不可变指纹哈希 (Fingerprint Hash)</div>
            <div class="font-bold text-sm 2xl:text-base mt-0.5 text-cyan-400">
              #{{ snapshotData.snapshot.policy_hash }}
            </div>
          </div>
        </div>
        <div class="flex items-center space-x-2">
          <Clock class="w-4 h-4 2xl:w-5 2xl:h-5 text-emerald-400 shrink-0" />
          <div>
            <div class="text-[11px] 2xl:text-xs text-[var(--text-muted)]">快照生成时间 (Snapshot Time)</div>
            <div class="font-bold text-sm 2xl:text-base mt-0.5 text-emerald-400">
              {{ formatTimestamp(snapshotData.snapshot.timestamp) }}
            </div>
          </div>
        </div>
      </div>

      <!-- 2. Four Strategy Units Matrix -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 2xl:gap-5">
        <!-- Unit 1: Prompt Policy -->
        <div
          class="p-4 sm:p-5 rounded-2xl border space-y-3 flex flex-col justify-between"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <div class="space-y-2">
            <div class="flex items-center justify-between pb-2 border-b" style="border-color: var(--border-subtle);">
              <div class="flex items-center space-x-2">
                <span class="p-1.5 rounded-lg bg-blue-500/10 border border-blue-500/20 text-blue-400">
                  <FileText class="w-4 h-4" />
                </span>
                <span class="font-bold font-mono text-xs" style="color: var(--text-main);">提示词策略工作室 (Prompt Studio)</span>
              </div>
              <router-link
                to="/admin/promptlib"
                class="text-[11px] font-mono text-blue-400 flex items-center hover:underline"
              >
                <span>进入配置</span>
                <ArrowUpRight class="w-3 h-3 ml-0.5" />
              </router-link>
            </div>

            <div class="space-y-1 text-xs font-mono">
              <div class="flex justify-between py-1 border-b border-dashed" style="border-color: var(--border-subtle);">
                <span class="text-[var(--text-muted)]">当前方案名称:</span>
                <span class="font-bold" style="color: var(--text-main);">
                  {{ snapshotData.snapshot.units?.prompt_profile?.active_profile_name || snapshotData.snapshot.units?.prompt_profile?.active_profile_id }}
                </span>
              </div>
              <div class="flex justify-between py-1 border-b border-dashed" style="border-color: var(--border-subtle);">
                <span class="text-[var(--text-muted)]">模块布局指纹 Layout Hash:</span>
                <span class="text-blue-400 font-bold">#{{ snapshotData.snapshot.units?.prompt_profile?.layout_hash }}</span>
              </div>
              <div class="flex justify-between py-1 border-b border-dashed" style="border-color: var(--border-subtle);">
                <span class="text-[var(--text-muted)]">编辑模式 Mode:</span>
                <span style="color: var(--text-main);">{{ snapshotData.snapshot.units?.prompt_profile?.editor_mode }}</span>
              </div>
              <div class="flex justify-between py-1">
                <span class="text-[var(--text-muted)]">插槽延迟渲染保护:</span>
                <span class="text-emerald-400 font-bold">单次延迟渲染 · 未提供数据显式标识</span>
              </div>
            </div>
          </div>

          <div class="p-2 rounded-xl text-[11px] font-mono" style="background-color: var(--bg-card-subtle); color: var(--text-muted);">
            ✓ 模板占位符单次延迟渲染，未提供真实数据明确标记，绝不伪装为空仓。
          </div>
        </div>

        <!-- Unit 2: Evolution Shield -->
        <div
          class="p-4 sm:p-5 rounded-2xl border space-y-3 flex flex-col justify-between"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <div class="space-y-2">
            <div class="flex items-center justify-between pb-2 border-b" style="border-color: var(--border-subtle);">
              <div class="flex items-center space-x-2">
                <span class="p-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400">
                  <Sparkles class="w-4 h-4" />
                </span>
                <span class="font-bold font-mono text-xs" style="color: var(--text-main);">自进化心法防线 (Evolution Shield)</span>
              </div>
              <router-link
                to="/admin/evolution"
                class="text-[11px] font-mono text-emerald-400 flex items-center hover:underline"
              >
                <span>进入配置</span>
                <ArrowUpRight class="w-3 h-3 ml-0.5" />
              </router-link>
            </div>

            <div class="space-y-1 text-xs font-mono">
              <div class="flex justify-between py-1 border-b border-dashed" style="border-color: var(--border-subtle);">
                <span class="text-[var(--text-muted)]">已发布心法指纹 Version:</span>
                <span class="text-emerald-400 font-bold truncate max-w-[180px]" :title="snapshotData.snapshot.units?.evolution_mind?.version">
                  {{ snapshotData.snapshot.units?.evolution_mind?.version }}
                </span>
              </div>
              <div class="flex justify-between py-1 border-b border-dashed" style="border-color: var(--border-subtle);">
                <span class="text-[var(--text-muted)]">启用心法 / 总收录心法:</span>
                <span class="font-bold" style="color: var(--text-main);">
                  {{ snapshotData.snapshot.units?.evolution_mind?.enabled_count }} / {{ snapshotData.snapshot.units?.evolution_mind?.total_count }}
                </span>
              </div>
              <div class="flex justify-between py-1 border-b border-dashed" style="border-color: var(--border-subtle);">
                <span class="text-[var(--text-muted)]">白盒审核机制:</span>
                <span class="text-emerald-400 font-bold">红线防御 · 审核拒绝硬阻断</span>
              </div>
              <div class="flex justify-between py-1">
                <span class="text-[var(--text-muted)]">并发版本安全保护:</span>
                <span class="text-emerald-400 font-bold">CAS 乐观锁 · 428/409 拒绝过期覆盖</span>
              </div>
            </div>
          </div>

          <div class="p-2 rounded-xl text-[11px] font-mono" style="background-color: var(--bg-card-subtle); color: var(--text-muted);">
            ✓ 结构化原子发布 + 乐观版本锁，NO_CHANGE 与异常禁止重写交易心法。
          </div>
        </div>

        <!-- Unit 3: Interceptor Plugins -->
        <div
          class="p-4 sm:p-5 rounded-2xl border space-y-3 flex flex-col justify-between"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <div class="space-y-2">
            <div class="flex items-center justify-between pb-2 border-b" style="border-color: var(--border-subtle);">
              <div class="flex items-center space-x-2">
                <span class="p-1.5 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400">
                  <ShieldCheck class="w-4 h-4" />
                </span>
                <span class="font-bold font-mono text-xs" style="color: var(--text-main);">物理拦截插件 (Interceptors)</span>
              </div>
              <router-link
                to="/admin/interceptors"
                class="text-[11px] font-mono text-amber-400 flex items-center hover:underline"
              >
                <span>进入配置</span>
                <ArrowUpRight class="w-3 h-3 ml-0.5" />
              </router-link>
            </div>

            <div class="space-y-1 text-xs font-mono">
              <div class="flex justify-between py-1 border-b border-dashed" style="border-color: var(--border-subtle);">
                <span class="text-[var(--text-muted)]">核心不可禁用底座:</span>
                <span class="text-amber-400 font-bold">几何/有限性/75%置信/2.0R</span>
              </div>
              <div class="flex justify-between py-1 border-b border-dashed" style="border-color: var(--border-subtle);">
                <span class="text-[var(--text-muted)]">插件管线指纹 Plugins Hash:</span>
                <span class="text-amber-400 font-bold">#{{ snapshotData.snapshot.units?.physical_interceptors?.plugins_hash }}</span>
              </div>
              <div class="flex justify-between py-1 border-b border-dashed" style="border-color: var(--border-subtle);">
                <span class="text-[var(--text-muted)]">启用可选插件:</span>
                <span class="font-bold" style="color: var(--text-main);">
                  {{ snapshotData.snapshot.units?.physical_interceptors?.enabled_count }} / {{ snapshotData.snapshot.units?.physical_interceptors?.total_count }} 个插件
                </span>
              </div>
              <div class="flex justify-between py-1">
                <span class="text-[var(--text-muted)]">最终发单二次复验:</span>
                <span class="text-emerald-400 font-bold">生效报价缩放/舍入后复验</span>
              </div>
            </div>
          </div>

          <div class="p-2 rounded-xl text-[11px] font-mono" style="background-color: var(--bg-card-subtle); color: var(--text-muted);">
            ✓ 核心安全与可选插件彻底解耦，插件参数深拷贝隔离防篡改，缺失文件 Fail-Closed。
          </div>
        </div>

        <!-- Unit 4: Trading Desk Council -->
        <div
          class="p-4 sm:p-5 rounded-2xl border space-y-3 flex flex-col justify-between"
          style="background-color: var(--bg-card); border-color: var(--border-subtle);"
        >
          <div class="space-y-2">
            <div class="flex items-center justify-between pb-2 border-b" style="border-color: var(--border-subtle);">
              <div class="flex items-center space-x-2">
                <span class="p-1.5 rounded-lg bg-purple-500/10 border border-purple-500/20 text-purple-400">
                  <Users class="w-4 h-4" />
                </span>
                <span class="font-bold font-mono text-xs" style="color: var(--text-main);">模型委员会中枢 (Council Desk)</span>
              </div>
              <router-link
                to="/admin/council"
                class="text-[11px] font-mono text-purple-400 flex items-center hover:underline"
              >
                <span>进入配置</span>
                <ArrowUpRight class="w-3 h-3 ml-0.5" />
              </router-link>
            </div>

            <div class="space-y-1 text-xs font-mono">
              <div class="flex justify-between py-1 border-b border-dashed" style="border-color: var(--border-subtle);">
                <span class="text-[var(--text-muted)]">机制启停状态:</span>
                <span
                  class="font-bold"
                  :style="snapshotData.snapshot.units?.model_council?.enabled ? { color: 'var(--color-up)' } : { color: 'var(--text-faint)' }"
                >
                  {{ snapshotData.snapshot.units?.model_council?.enabled ? '● 投委会辩论模式' : '○ 单模型决策模式' }}
                </span>
              </div>
              <div class="flex justify-between py-1 border-b border-dashed" style="border-color: var(--border-subtle);">
                <span class="text-[var(--text-muted)]">真实共识模式 Mode:</span>
                <span class="text-purple-400 font-bold">
                  {{ snapshotData.snapshot.units?.model_council?.consensus_mode === 'cross_examination' ? '双轮真实质询 (Cross-Exam)' : '标准提案裁决 (Standard)' }}
                </span>
              </div>
              <div class="flex justify-between py-1 border-b border-dashed" style="border-color: var(--border-subtle);">
                <span class="text-[var(--text-muted)]">活跃交易员席位:</span>
                <span class="font-bold" style="color: var(--text-main);">
                  {{ snapshotData.snapshot.units?.model_council?.active_roles?.length || 0 }} 位一线交易员 + CIO
                </span>
              </div>
              <div class="flex justify-between py-1">
                <span class="text-[var(--text-muted)]">决策采纳追踪 Adopted Role:</span>
                <span class="text-emerald-400 font-bold">机器可追溯 · 动态截止时间保护</span>
              </div>
            </div>
          </div>

          <div class="p-2 rounded-xl text-[11px] font-mono" style="background-color: var(--bg-card-subtle); color: var(--text-muted);">
            ✓ 剔除虚假共识选项，实战双轮互评，超时毫秒级自适应安全降级。
          </div>
        </div>
      </div>

      <!-- 3. Policy Archive Vault (历史策略版本库) -->
      <div
        class="rounded-2xl border p-4 sm:p-5 shadow-xs space-y-3"
        style="background-color: var(--bg-card); border-color: var(--border-subtle);"
      >
        <div class="flex items-center justify-between pb-2 border-b" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2">
            <Archive class="w-4 h-4 text-purple-400" />
            <h3 class="text-sm font-bold font-mono" style="color: var(--text-main);">
              历史策略版本库 (Policy Archive Vault)
            </h3>
            <span class="text-[11px] font-mono px-2 py-0.5 rounded border" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);">
              {{ archives.length }} 个已归档策略包
            </span>
          </div>
          <span class="text-[11px] font-mono text-[var(--text-muted)]">
            可一键将提示词、心法、拦截器及委员会完整还原至指定瞬间
          </span>
        </div>

        <div v-if="archives.length === 0" class="py-8 text-center text-xs font-mono text-zinc-500">
          暂无已归档的策略版本。点击右上角「归档为策略版本」即可永久固化当前策略包。
        </div>

        <div v-else class="divide-y" style="border-color: var(--border-subtle);">
          <div
            v-for="arc in archives"
            :key="arc.policy_hash"
            class="py-3 flex flex-col md:flex-row md:items-center justify-between gap-3 font-mono text-xs hover:bg-[var(--bg-card-hover)] px-2 rounded-xl transition-colors"
          >
            <div class="space-y-1 flex-1 min-w-0">
              <div class="flex items-center space-x-2">
                <span class="font-bold text-sm" style="color: var(--text-main);">{{ arc.name }}</span>
                <span class="text-[11px] px-2 py-0.5 rounded border text-cyan-400 border-cyan-500/30 bg-cyan-500/10">
                  #{{ arc.policy_hash }}
                </span>
                <span
                  v-if="arc.policy_hash === snapshotData.snapshot.policy_hash"
                  class="text-[11px] font-bold px-2 py-0.2 rounded border text-emerald-400 border-emerald-500/30 bg-emerald-500/10"
                >
                  ● 当前正在运行
                </span>
              </div>
              <p v-if="arc.description" class="text-[11px]" style="color: var(--text-muted);">
                {{ arc.description }}
              </p>
              <div class="flex flex-wrap items-center gap-3 text-[11px] text-[var(--text-muted)]">
                <span>归档时间: {{ arc.archived_at }}</span>
                <span>创建者: {{ arc.author }}</span>
                <span class="truncate max-w-md">{{ arc.summary }}</span>
              </div>
            </div>

            <div class="flex items-center space-x-2 shrink-0">
              <button
                @click="restorePolicy(arc.policy_hash, arc.name)"
                :disabled="restoring || !auth.isSuperadmin || arc.policy_hash === snapshotData.snapshot.policy_hash"
                class="flex items-center space-x-1 px-3 py-1.5 rounded-xl border text-xs font-mono font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs"
                :style="arc.policy_hash === snapshotData.snapshot.policy_hash
                  ? { backgroundColor: 'var(--bg-card-subtle)', borderColor: 'var(--border-subtle)', color: 'var(--text-faint)' }
                  : { backgroundColor: 'var(--color-up-bg)', borderColor: 'var(--color-up-border)', color: 'var(--color-up)' }"
              >
                <RotateCcw class="w-3.5 h-3.5" :class="{ 'animate-spin': restoring }" />
                <span>{{ arc.policy_hash === snapshotData.snapshot.policy_hash ? '已是当前版本' : '一键回滚还原' }}</span>
              </button>

              <button
                @click="deleteArchive(arc.policy_hash, arc.name)"
                :disabled="deleting === arc.policy_hash || !auth.isSuperadmin"
                class="flex items-center space-x-1 px-2.5 py-1.5 rounded-xl border text-xs font-mono cursor-pointer transition-all hover:bg-rose-500/10 text-rose-400 border-rose-500/20"
                title="删除此归档版本"
              >
                <Trash2 class="w-3.5 h-3.5" :class="{ 'animate-pulse': deleting === arc.policy_hash }" />
                <span>删除</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Archive Dialog Modal -->
    <div
      v-if="showArchiveModal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-xs"
    >
      <div
        class="w-full max-w-md rounded-2xl border p-5 shadow-2xl space-y-4"
        style="background-color: var(--bg-card); border-color: var(--border-medium);"
      >
        <div class="flex items-center space-x-2">
          <BookmarkPlus class="w-5 h-5 text-purple-400" />
          <h3 class="text-sm font-bold font-mono" style="color: var(--text-main);">
            归档当前策略版本 (Create Policy Archive)
          </h3>
        </div>

        <p class="text-xs font-mono leading-relaxed" style="color: var(--text-muted);">
          将当前生效的提示词模块、自进化心法、物理拦截器及模型委员会配置打包固化为不可变版本快照，后续可随时一键全盘回滚。
        </p>

        <div class="space-y-3 font-mono text-xs">
          <div>
            <label class="block text-[11px] mb-1 font-bold" style="color: var(--text-muted);">策略名称 (必填):</label>
            <input
              v-model="archiveName"
              placeholder="例如: 2026-09 顺势回踩大牛市高胜率版"
              class="w-full rounded-xl px-3 py-2 text-xs outline-none border transition-colors"
              style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);"
            />
          </div>
          <div>
            <label class="block text-[11px] mb-1 font-bold" style="color: var(--text-muted);">策略描述与实盘备注 (选填):</label>
            <textarea
              v-model="archiveDesc"
              rows="3"
              placeholder="记录此版本的调参核心逻辑、回测表现或适用行情环境..."
              class="w-full rounded-xl p-3 text-xs outline-none border transition-colors resize-none"
              style="background-color: var(--bg-input); border-color: var(--border-subtle); color: var(--text-main);"
            ></textarea>
          </div>
        </div>

        <div class="flex items-center justify-end space-x-2 pt-2 border-t" style="border-color: var(--border-subtle);">
          <button
            @click="showArchiveModal = false"
            class="px-3.5 py-1.5 rounded-xl border text-xs font-mono cursor-pointer transition-colors"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);"
          >
            取消
          </button>
          <button
            @click="saveArchive"
            :disabled="archiving"
            class="px-4 py-1.5 rounded-xl text-xs font-mono font-bold cursor-pointer transition-all shadow-xs"
            style="background-color: var(--text-main); color: var(--bg-card);"
          >
            {{ archiving ? '正在归档中...' : '确认归档入库' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

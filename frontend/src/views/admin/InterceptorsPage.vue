<script setup lang="ts">
/**
 * InterceptorsPage.vue · 物理拦截管线工位
 * ---------------------------------------------------------------------------
 * 骨架（推倒重来）：
 *   旧 = 页头 + 每插件一张大卡（左侧上下箭头 + 元信息块 + 右侧动作）
 *        + **3 个手写 fixed 遮罩弹窗**（源码编辑器 / 新建插件 / 沙箱报告）
 *   新 = 共享 PageHeader（沙箱 / 新建 / fail-closed 徽章）
 *        → **拦截管线：单一面板内的执行序清单**
 *          （优先级箭头 · 序号 · 名称/文件/版本/作者 · 描述/标签/错误 · 动作）
 *        → 3 个弹窗全部改用 BaseDialog（xl / lg / xl）
 *
 * 后端契约（逐字未改）：
 *   GET    /api/v1/admin/interceptors
 *   GET    /api/v1/admin/interceptors/{filename}
 *   PUT    /api/v1/admin/interceptors/{filename}/toggle   { enabled }
 *   PUT    /api/v1/admin/interceptors/{filename}/code     { code }
 *   POST   /api/v1/admin/interceptors/reorder             { pipeline_order }
 *   POST   /api/v1/admin/interceptors                     { filename, code }
 *   POST   /api/v1/admin/interceptors/test                {}
 *   DELETE /api/v1/admin/interceptors/{filename}
 *
 * ⚠️ 删除门禁未动：逐字短语 `DELETE`；且序号以 `0` 开头的内建插件不显示删除入口。
 */
import { useToast } from '../../composables/useToast';
import { useConfirm } from '../../composables/useConfirm';
const toast = useToast();
const { ask } = useConfirm();
import { ref, onMounted } from 'vue';
import PageHeader from '../../components/admin/PageHeader.vue';
import BaseDialog from '../../components/base/BaseDialog.vue';
import BaseSwitch from '../../components/base/BaseSwitch.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';
import { useI18n } from '../../composables/useI18n';
import { useApi } from '../../composables/useApi';
import { useAuthStore } from '../../stores/auth';
import { ArrowUp, ArrowDown, Plus, Code, Trash2, Play, AlertTriangle,
  Save, Download, Loader2, ShieldCheck, RefreshCw, FileCode } from 'lucide-vue-next';
import BaseLoadingAnnounce from '../../components/base/BaseLoadingAnnounce.vue';

const { api } = useApi();
const auth = useAuthStore();
const { t } = useI18n();

const plugins = ref<any[]>([]);
const loading = ref(true);
const loadError = ref('');

// Code Editor Modal State
const editorVisible = ref(false);
const editingFilename = ref('');
const editingCode = ref('');
const editingName = ref('');
const savingCode = ref(false);
const codeError = ref('');

// Sandbox Test State
const testing = ref(false);
const testResults = ref<any>(null);
const testModalVisible = ref(false);

// Create New Plugin State
const createModalVisible = ref(false);
const newFilename = ref('');
const newCode = ref('');
const createError = ref('');
const creating = ref(false);

async function loadPlugins() {
  loading.value = true;
  loadError.value = '';
  try {
    const res = await api('/api/v1/admin/interceptors')
    plugins.value = res.plugins || []
  } catch (e: any) {
    loadError.value = e.message
    toast.err(t('admin.interceptors.loadFailed', undefined, { msg: e.message }))
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
    const name = p.name || p.filename
    toast.ok(
      nextState
        ? t('admin.interceptors.toggleOn', undefined, { name })
        : t('admin.interceptors.toggleOff', undefined, { name }),
    )
  } catch (e: any) {
    toast.err(t('admin.interceptors.opFailed', undefined, { msg: e.message }))
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
    toast.ok(t('admin.interceptors.reorderOk'))
  } catch (e: any) {
    toast.err(t('admin.interceptors.reorderFailed', undefined, { msg: e.message }))
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
    toast.err(t('admin.interceptors.readSourceFailed', undefined, { msg: e.message }))
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
    toast.ok(t('admin.interceptors.saveCodeOk', undefined, { file: editingFilename.value }))
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
  // 批C(2026-09-13)·不可逆操作收口：插件文件从磁盘彻底移除，原生 confirm 小条在
  // 移动端极易误触；改逐字短语确认（与项目危险操作约定一致）。
  const _ok = await ask({
    title: t('admin.interceptors.deleteConfirmTitle'),
    desc: t('admin.interceptors.deleteConfirmDesc', undefined, { name: p.name || p.filename }),
    danger: true,
    confirmPhrase: 'DELETE',
    okText: t('common.del'),
  })
  if (!_ok) return
  try {
    await api(`/api/v1/admin/interceptors/${encodeURIComponent(p.filename)}`, { method: 'DELETE' })
    toast.ok(t('admin.interceptors.deleteOk', undefined, { file: p.filename }))
    await loadPlugins()
  } catch (e: any) {
    toast.err(t('admin.interceptors.deleteFailed', undefined, { msg: e.message }))
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
    toast.err(t('admin.interceptors.sandboxFailed', undefined, { msg: e.message }))
  } finally {
    testing.value = false
  }
}

function openCreateModal() {
  newFilename.value = `custom_interceptor_${Date.now().toString(36)}.py`
  newCode.value = `"""
AstraQuant 物理拦截插件规范
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
  if (creating.value) return
  createError.value = ''
  if (!newFilename.value.trim()) {
    createError.value = t('admin.interceptors.filenameRequired')
    return
  }
  creating.value = true
  try {
    const res = await api('/api/v1/admin/interceptors', {
      method: 'POST',
      body: JSON.stringify({
        filename: newFilename.value.trim(),
        code: newCode.value,
      }),
    })
    toast.ok(t('admin.interceptors.createOk', undefined, { name: res.name || res.filename }))
    createModalVisible.value = false
    await loadPlugins()
  } catch (e: any) {
    createError.value = e.message
  } finally {
    creating.value = false
  }
}

function closeEditor() {
  editorVisible.value = false
}
function closeCreate() {
  createModalVisible.value = false
}

onMounted(loadPlugins)
</script>

<template>
  <div class="ip">
    <PageHeader :title="t('nav.admin.interceptors')" :description="t('admin.interceptors.desc')">
      <template #actions>
        <span class="dsh-pill">
          <span class="dsh-status-dot active" aria-hidden="true" />
          {{ t('admin.interceptors.failClosed') }}
        </span>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="loadPlugins">
          <RefreshCw :size="14" :class="loading && 'animate-spin shrink-0'" />
          <span>{{ t('common.refresh') }}</span>
        </button>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="testing" @click="runSandbox">
          <Loader2 v-if="testing" :size="14" class="animate-spin shrink-0" />
          <Play v-else :size="14" />
          <span>{{ testing ? t('admin.interceptors.testing') : t('admin.interceptors.runSandbox') }}</span>
        </button>
        <button type="button" v-if="auth.isSuperadmin" class="btn btn-primary btn-sm" @click="openCreateModal">
          <Plus :size="14" />
          <span>{{ t('admin.interceptors.newPlugin') }}</span>
        </button>
      </template>
    </PageHeader>

    <!-- 拉取失败 -->
    <div v-if="loadError" role="alert" class="state-block is-error">
      <span class="state-icon"><AlertTriangle :size="17" /></span>
      <p class="state-title">{{ t('common.loadFailed') }}</p>
      <p class="state-desc">{{ loadError }}</p>
      <button type="button" class="btn btn-ghost btn-sm mt-1" :disabled="loading" @click="loadPlugins">
        <RefreshCw :size="14" :class="loading && 'animate-spin shrink-0'" />
        <span>{{ t('common.retry') }}</span>
      </button>
    </div>

    <template v-else>
      <section class="card">
        <header class="card-head">
          <div>
            <h2 class="card-title"><ShieldCheck :size="14" />{{ t('admin.interceptors.pipelineTitle') }}</h2>
            <p class="card-sub">{{ t('admin.interceptors.pipelineDesc') }}</p>
          </div>
          <span class="badge mono">{{ plugins.length }}</span>
        </header>

        <!-- 骨架 -->
        <div v-if="loading" class="ip-skel">
          <BaseLoadingAnnounce />
          <div v-for="i in 5" :key="i" class="skeleton skeleton-row" />
        </div>

        <!-- 空态 -->
        <BaseEmpty v-else-if="!plugins.length" :text="t('admin.interceptors.empty')" />

        <!-- 执行序清单 -->
        <div v-else class="ip-rows">
          <article
            v-for="(p, idx) in plugins"
            :key="p.filename"
            class="ip-row"
            :class="{ 'is-off': !p.enabled }"
          >
            <!-- 优先级 -->
            <div class="ip-order">
              <button type="button"
                class="btn btn-quiet btn-icon btn-sm"
                :disabled="idx === 0"
                :title="t('admin.interceptors.raiseTitle')"
                @click="movePlugin(idx, -1)"
              >
                <ArrowUp :size="13" />
              </button>
              <button type="button"
                class="btn btn-quiet btn-icon btn-sm"
                :disabled="idx === plugins.length - 1"
                :title="t('admin.interceptors.lowerTitle')"
                @click="movePlugin(idx, 1)"
              >
                <ArrowDown :size="13" />
              </button>
            </div>

            <span class="ip-n mono">#{{ idx + 1 }}</span>

            <!-- 元信息 -->
            <div class="ip-main">
              <div class="ip-title">
                <span class="ip-name truncate">{{ p.name || p.filename }}</span>
                <span class="badge mono">{{ p.filename }}</span>
                <span v-if="p.version" class="badge badge-accent">v{{ p.version }}</span>
                <span v-if="p.author" class="ip-author">by {{ p.author }}</span>
              </div>

              <p class="ip-desc">{{ p.description || t('admin.interceptors.noDescription') }}</p>

              <div v-if="p.tags && p.tags.length" class="ip-tags">
                <span v-for="tag in p.tags" :key="tag" class="ip-tag">{{ tag }}</span>
              </div>

              <p v-if="p.error" class="ip-err">
                <AlertTriangle :size="12" />
                <span>{{ p.error }}</span>
              </p>
            </div>

            <!-- 动作 -->
            <div class="ip-actions">
              <button type="button"
                class="btn btn-ghost btn-sm"
                :title="t('admin.interceptors.sourceTitle')"
                @click="openEditor(p)"
              >
                <Code :size="13" />
                <span>{{ t('admin.interceptors.sourceCode') }}</span>
              </button>

              <button type="button"
                v-if="auth.isSuperadmin && !p.filename.startsWith('0')"
                class="btn btn-danger btn-sm"
                :title="t('admin.interceptors.deleteTitle')"
                @click="deletePlugin(p)"
              >
                <Trash2 :size="13" />
              </button>

              <BaseSwitch
                :model-value="p.enabled === true"
                :label="p.enabled ? t('admin.interceptors.enabledTitle') : t('admin.interceptors.disabledTitle')"
                @update:model-value="() => togglePlugin(p)"
              />
            </div>
          </article>
        </div>
      </section>
    </template>

    <!-- ══ 源码编辑器 ══ -->
    <BaseDialog
      :open="editorVisible"
      :title="t('admin.interceptors.editorTitle')"
      size="2xl"
      initial-focus="textarea"
      @close="closeEditor"
    >
      <template #title>
        <span class="ip-dlg-title">
          <FileCode :size="15" />
          <span>{{ t('admin.interceptors.editorTitle') }}</span>
          <span class="ip-dlg-sub mono">{{ editingName }} ({{ editingFilename }})</span>
        </span>
      </template>

      <p class="ip-dlg-hint">{{ t('admin.interceptors.editorHint') }}</p>

      <div v-if="codeError" class="ip-dlg-error" role="alert">
        <AlertTriangle :size="13" />
        <span>{{ codeError }}</span>
      </div>

      <textarea
        v-model="editingCode"
        rows="26"
        spellcheck="false"
        class="field ip-code"
        :aria-label="t('admin.interceptors.codeLabel')"
        :aria-invalid="!!codeError ? 'true' : undefined"
      />

      <template #footer>
        <span class="ip-contract mono">
          {{ t('admin.interceptors.contractLabel') }} check_risk(package, decision, ctx)
        </span>
        <button type="button" class="btn btn-ghost btn-sm" @click="exportPluginCode(editingFilename, editingCode)">
          <Download :size="14" />
          <span>{{ t('admin.interceptors.exportPy') }}</span>
        </button>
        <button type="button" class="btn btn-ghost btn-sm" @click="closeEditor">{{ t('admin.interceptors.cancel') }}</button>
        <button type="button" class="btn btn-primary btn-sm" :disabled="savingCode" @click="saveCode">
          <Loader2 v-if="savingCode" :size="14" class="animate-spin shrink-0" />
          <Save v-else :size="14" />
          <span>{{ savingCode ? t('admin.interceptors.saving') : t('admin.interceptors.saveAndReload') }}</span>
        </button>
      </template>
    </BaseDialog>

    <!-- ══ 新建插件 ══ -->
    <BaseDialog
      :open="createModalVisible"
      :title="t('admin.interceptors.createTitle')"
      :desc="t('admin.interceptors.createHint')"
      size="xl"
      initial-focus="input"
      @close="closeCreate"
    >
      <div v-if="createError" role="alert" class="ip-dlg-error">
        <AlertTriangle :size="13" />
        <span>{{ createError }}</span>
      </div>

      <form id="ip-create-form" @submit.prevent="submitCreate">
      <div class="field-stack ip-field">
        <span class="form-label">{{ t('admin.interceptors.filenameLabel') }}</span>
        <input
          v-model="newFilename"
          type="text"
          :aria-label="t('admin.interceptors.filenameLabel')"
          class="field mono"
          :class="{ 'is-bad': !!createError && !newFilename.trim() }"
          :aria-invalid="!!createError && !newFilename.trim() ? 'true' : undefined"
          :placeholder="t('admin.interceptors.filenamePlaceholder')"
        />
      </div>

      <div class="field-stack ip-field">
        <span class="form-label">{{ t('admin.interceptors.codeLabel') }}</span>
        <textarea
          v-model="newCode"
          rows="20"
          spellcheck="false"
          class="field ip-code"
          :aria-label="t('admin.interceptors.codeLabel')"
        />
      </div>
      </form>

      <template #footer>
        <button type="button" class="btn btn-ghost btn-sm" @click="closeCreate">{{ t('admin.interceptors.cancel') }}</button>
        <!-- 批 115：表单已挂 @submit.prevent="submitCreate"，type="submit" 按钮无需再挂 @click，避免单次点击触发两次创建请求 -->
        <button class="btn btn-primary btn-sm" type="submit" form="ip-create-form" :disabled="creating || !newFilename.trim()">
          <Loader2 v-if="creating" :size="14" class="animate-spin shrink-0" />
          <Plus v-else :size="14" />
          <span>{{ t('admin.interceptors.createAndAdd') }}</span>
        </button>
      </template>
    </BaseDialog>

    <!-- ══ 沙箱回归报告 ══ -->
    <BaseDialog
      :open="testModalVisible && !!testResults"
      :title="t('admin.interceptors.reportTitle')"
      :desc="testResults
        ? t('admin.interceptors.reportSummary', undefined, {
            enabled: testResults.enabled_plugins_count,
            total: testResults.total_plugins_count,
            ms: testResults.duration_total_ms,
          })
        : ''"
      size="xl"
      @close="testModalVisible = false"
    >
      <div class="ip-report">
        <article
          v-for="(r, i) in testResults?.results || []"
          :key="i"
          class="ip-case"
          :class="r.intercepted ? 'is-blocked' : 'is-passed'"
        >
          <header class="ip-case-head">
            <span class="ip-case-name">{{ r.scenario }}</span>
            <span class="badge mono">{{ r.duration_ms }}ms</span>
            <span class="badge" :class="r.intercepted ? 'badge-warn' : 'badge-up'">
              {{ r.intercepted ? t('admin.interceptors.intercepted') : t('admin.interceptors.passed') }}
            </span>
          </header>

          <div class="ip-case-meta">
            <span>{{ t('admin.interceptors.rawAction') }} <b>{{ r.raw_action }}</b></span>
            <span>
              {{ t('admin.interceptors.finalAction') }}
              <b :class="r.final_action === 'WAIT' ? 'is-warn' : 'is-up'">{{ r.final_action }}</b>
            </span>
            <span v-if="r.risk_reward !== '--'">{{ t('admin.interceptors.riskReward') }} <b>{{ r.risk_reward }}</b></span>
          </div>

          <p v-if="r.reason" class="ip-case-reason">
            {{ t('admin.interceptors.interceptAudit') }}{{ r.reason }}
          </p>
        </article>

        <BaseEmpty v-if="!testResults?.results?.length" :text="t('common.noData')" />
      </div>

      <template #footer>
        <button type="button" class="btn btn-primary btn-sm" @click="testModalVisible = false">
          {{ t('admin.interceptors.closeReport') }}
        </button>
      </template>
    </BaseDialog>
  </div>
</template>

<style scoped>
.ip {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-4);
}
.ip-skel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: var(--ds-space-4);
}

/* ══ 执行序清单 ══ */
.ip-rows {
  display: flex;
  flex-direction: column;
}
.ip-row {
  display: grid;
  grid-template-columns: auto 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--ds-space-3);
  padding: var(--ds-space-3) var(--ds-space-4);
  border-bottom: 1px solid var(--ds-color-border-default);
  transition: background-color var(--dur-fast);
}
.ip-row:last-child {
  border-bottom: 0;
}
.ip-row:hover {
  background-color: var(--ds-color-bg-hover);
}
.ip-row.is-off {
  opacity: 0.55;
}

.ip-order {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.ip-n {
  font-size: var(--text-4xs);
  color: var(--ds-color-text-placeholder);
  text-align: center;
}

.ip-main {
  display: flex;
  flex-direction: column;
  gap:4px;
  min-width: 0;
}
.ip-title {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}
.ip-name {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--ds-color-text-primary);
}
.ip-author {
  font-size: var(--text-4xs);
  color: var(--ds-color-text-placeholder);
}
.ip-desc {
  font-size: var(--text-3xs);
  line-height: var(--leading-body);
  color: var(--ds-color-text-description);
}
.ip-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.ip-tag {
  /* 批 35：全站徽标规范盒模型是 20px 高 / --text-3xs（见 .badge）；
     这条此前是 22px / 11px，是最后一处不在规范上的实体徽标。 */
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 6px;
  border-radius: var(--r-xs);
  border: 1px solid var(--ds-color-border-default);
  background-color: var(--ds-color-bg-surface-1);
  font-size: var(--text-3xs);
  color: var(--ds-color-text-placeholder);
}
.ip-err {
  display: flex;
  align-items: flex-start;
  gap:6px;
  font-size: var(--text-3xs);
  color: var(--down);
}
.ip-err > svg {
  flex-shrink: 0;
  margin-top: 2px;
}

.ip-actions {
  display: flex;
  align-items: center;
  gap: var(--ds-space-2);
  flex-shrink: 0;
}

@media (max-width: 900px) {
  .ip-row {
    grid-template-columns: auto 1fr;
  }
  .ip-main {
    grid-column: 2 / -1;
  }
  .ip-actions {
    grid-column: 1 / -1;
    justify-content: flex-end;
  }
}

/* ══ 对话框内 ══ */
.ip-dlg-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.ip-dlg-sub {
  font-size: var(--text-4xs);
  font-weight: 400;
  color: var(--ds-color-text-placeholder);
}
.ip-dlg-hint {
  margin-bottom: var(--ds-space-3);
  font-size: var(--text-3xs);
  color: var(--ds-color-text-description);
}
.ip-dlg-error {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-bottom: var(--ds-space-3);
  padding: 8px 10px;
  border-radius: var(--r-ctl);
  background-color: var(--down-bg);
  color: var(--down);
  font-size: var(--text-3xs);
  overflow-wrap: anywhere;
}
.ip-dlg-error > svg {
  flex-shrink: 0;
  margin-top: 2px;
}
.ip-code {
  width: 100%;
  min-height: 520px;
  resize: vertical;
  font-family: var(--ds-font-mono);
  font-size: var(--text-base);
  line-height: 1.6;
  tab-size: 4;
  padding: var(--ds-space-3-5) var(--ds-space-4);
  background-color: var(--ds-color-bg-code);
  border-radius: var(--r-ctl);
}

.ip-field + .ip-field {
  margin-top: var(--ds-space-4);
}
.ip-contract {
  margin-right: auto;
  font-size: var(--text-4xs);
  color: var(--ds-color-text-placeholder);
}

/* ══ 沙箱报告 ══ */
.ip-report {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-3);
}
.ip-case {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: var(--ds-space-3);
  border-radius: var(--r-ctl);
  border-left: 2px solid var(--up);
  background-color: var(--ds-color-bg-surface-inset);
}
.ip-case.is-blocked {
  border-left-color: var(--warn);
  background-color: var(--warn-bg);
}
.ip-case-head {
  display: flex;
  align-items: center;
  gap: var(--ds-space-2);
  flex-wrap: wrap;
}
.ip-case-name {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--ds-color-text-primary);
}
.ip-case-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ds-space-4);
  font-size: var(--text-3xs);
  color: var(--ds-color-text-description);
}
.ip-case-meta b {
  color: var(--ds-color-text-primary);
  font-weight: 600;
}
.ip-case-meta b.is-warn {
  color: var(--warn);
}
.ip-case-meta b.is-up {
  color: var(--up);
}
.ip-case-reason {
  font-size: var(--text-3xs);
  line-height: var(--leading-body);
  color: var(--warn);
  overflow-wrap: anywhere;
}
</style>

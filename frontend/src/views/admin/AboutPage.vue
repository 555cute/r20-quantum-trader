<script setup lang="ts">
/**
 * AboutPage.vue · 版本与安全更新工位
 * ---------------------------------------------------------------------------
 * 骨架（推倒重来）：
 *   旧 = 一行简介 + 徽章 + 两张卡（产品信息 / 组件表）+ 一张更新卡
 *        + **手写 fixed 遮罩确认弹窗**；错误只在 console.error，页面完全不可见
 *   新 = 共享 PageHeader（治理徽章 + 刷新）
 *        → **版本状态带**（系统版本 / 网关控制面 / 运行环境 / 待同步差额）
 *        → **产品信息面板**（kv 行 + 仓库入口）
 *        → **组件版本面板**（行式清单）
 *        → **安全更新面板**（FF-ONLY 徽章 + 4 项 git 遥测 + 动作 + 结果 / git 输出日志面板）
 *        → 确认弹窗改用 BaseDialog（逐字短语 `UPDATE R20` 门禁不变）
 *
 * ⚠️ 修复：`useResource` 的文档声明 `immediate` 默认 true，实现只在传入真值时取数，
 *    本页此前**从不自动加载**；且 onError 只 console.error，页面无任何提示。
 *    现显式 `immediate: true`，并把错误接到可见的失败态（可重试）。
 *
 * 后端契约（逐字未改）：
 *   GET  /api/v1/admin/about
 *   POST /api/v1/admin/update/check
 *   POST /api/v1/admin/update        { confirmation: 'UPDATE R20' }
 *
 * ⚠️ 展示层保留的既有语义：
 *   git 失败会回 HTTP 200 + `error` 字段（审计①#8），故 `res.error` 必须走红分支，
 *   文案强调「安全补丁可能静默脱班」。
 */
import { ref, computed } from 'vue';
import { useI18n } from '../../composables/useI18n';
const { t } = useI18n();
import { useApi } from '../../composables/useApi';
import { useResource } from '../../composables/useResource';
import { useAsyncAction } from '../../composables/useAsyncAction';
import PageHeader from '../../components/admin/PageHeader.vue';
import BaseDialog from '../../components/base/BaseDialog.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';
import { Info, GitBranch, Download, RefreshCw, CheckCircle2, AlertTriangle,
  ShieldCheck, Terminal, Loader2, ArrowUpRight, Link2, ExternalLink } from 'lucide-vue-next';
import BaseLoadingAnnounce from '../../components/base/BaseLoadingAnnounce.vue';
import CopyButton from '../../components/base/CopyButton.vue';

const { api } = useApi();

// F2：取数样板收成一行。取数失败由下方 `v-if="error && !about"` 的 role="alert"
// 横幅呈现（批 67 补的通报语义），故此处不再叠一条 toast。
const { data: about, loading, error, loaded, reload: load } = useResource<any>('/api/v1/admin/about', {
  immediate: true,
  onError: (e) => console.error(e),
});

const showSkeleton = computed(() => loading.value && !loaded.value);

const updateResult = ref<any>(null);
const showConfirmModal = ref(false);
const confirmPhrase = ref('');

// F2：动作类样板（busy + 统一错误出口）。原实现的错误出口是写进 updateResult，
// 故用 onError 一对一保留，不弹 toast、不改变页面表现。
const { run: checkUpdate, busy: updateChecking } = useAsyncAction(async () => {
  updateResult.value = null
  const res = await api<any>('/api/v1/admin/update/check', { method: 'POST' })
  if (about.value) {
    about.value.update = res
  }
  await load()
  updateResult.value = res.error
    // 模板以 .error 键判红（审计①#8）：git 失败回 HTTP 200+error 字段，必须走红分支
    ? { error: t('admin.about.updateCheckFailed', undefined, { msg: res.error }), data: res }
    : {
        ok: true,
        message: res.behind > 0
          ? t('admin.about.checkBehind', undefined, { behind: res.behind, remote: res.remote })
          : t('admin.about.checkUpToDate'),
        data: res,
      }
}, { onError: (e) => { updateResult.value = { error: e.message } } })

function openUpdateModal() {
  confirmPhrase.value = ''
  showConfirmModal.value = true
}

const { run: executeUpdate, busy: updateRunning } = useAsyncAction(async () => {
  if (confirmPhrase.value.trim().toUpperCase() !== 'UPDATE R20') return
  updateResult.value = null
  const res = await api<any>('/api/v1/admin/update', {
    method: 'POST',
    body: JSON.stringify({ confirmation: 'UPDATE R20' }),
  })
  showConfirmModal.value = false
  updateResult.value = {
    ok: true,
    updated: res.updated,
    message: res.updated ? t('admin.about.updateSuccess') : t('admin.about.updateNoop'),
    git_output: res.git_output,
    restart_note: res.restart_note,
  }
  if (about.value && res.after) {
    about.value.update = res.after
  }
  await load()
}, { onError: (e) => { updateResult.value = { error: e.message } } })

const phaseOk = computed(() => confirmPhrase.value.trim().toUpperCase() === 'UPDATE R20');

/** 注册通道：后端 `/api/v1/admin/about` 的 `channels`（链接与 OKX 经纪商 code 都来自接口）。
 *  顺序固定为 OKX → Gate → Binance，缺失项由后端省略时优雅跳过。 */
const CHANNEL_ORDER = ['okx', 'gate', 'binance'] as const;
const channelRows = computed(() => {
  const ch = about.value?.channels || {};
  return CHANNEL_ORDER
    .filter((key) => ch[key])
    .map((key) => ({ key, ...ch[key] }));
});

/** 版本状态带（4 项事实，全部取自 about.product / runtime / update） */
const bandFacts = computed(() => {
  const a = about.value
  if (!a) return []
  const behind = a.update?.behind || 0
  return [
    {
      icon: Info,
      label: t('admin.about.bandVersion'),
      value: a.product?.version ? `v${a.product.version}` : '--',
      foot: a.product?.name || '',
      tone: '',
    },
    {
      icon: ShieldCheck,
      label: t('admin.about.bandControlPlane'),
      value: a.product?.control_plane || '--',
      foot: a.product?.gateway_version ? `v${a.product.gateway_version}` : '',
      tone: '',
    },
    {
      icon: Terminal,
      label: t('admin.about.bandRuntime'),
      value: a.runtime?.python ? `Python ${a.runtime.python}` : '--',
      foot: a.update?.branch || 'main',
      tone: '',
    },
    {
      icon: behind > 0 ? ArrowUpRight : CheckCircle2,
      label: t('admin.about.bandSyncGap'),
      value: behind > 0
        ? t('admin.about.behind', undefined, { n: behind })
        : t('admin.about.upToDate'),
      foot: a.update?.ahead
        ? t('admin.about.ahead', undefined, { n: a.update.ahead })
        : (a.update?.local || '--'),
      tone: behind > 0 ? 'is-warn' : 'is-up',
    },
  ]
})
</script>

<template>
  <div class="ab">
    <PageHeader :title="t('nav.admin.about')" :description="t('admin.about.intro')">
      <template #actions>
        <span class="badge badge-accent mono">{{ t('admin.about.badge') }}</span>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="load">
          <Loader2 v-if="loading && loaded" :size="14" class="animate-spin shrink-0" />
          <RefreshCw v-else :size="14" />
          <span>{{ t('common.refresh') }}</span>
        </button>
      </template>
    </PageHeader>

    <!-- 取数失败（旧版仅 console.error，页面完全不可见） -->
    <div v-if="error && !about" role="alert" class="state-block is-error">
      <span class="state-icon"><AlertTriangle :size="17" /></span>
      <p class="state-title">{{ t('common.loadFailed') }}</p>
      <p class="state-desc">{{ error }}</p>
      <button type="button" class="btn btn-ghost btn-sm mt-1" :disabled="loading" @click="load">
        <RefreshCw :size="14" />
        <span>{{ t('common.retry') }}</span>
      </button>
    </div>

    <template v-else>
      <!-- ══ 版本状态带 ══ -->
      <section class="card band">
        <template v-if="showSkeleton">
          <BaseLoadingAnnounce />
          <div v-for="i in 4" :key="i" class="fact">
            <div class="skeleton skeleton-text" style="width: 48%" />
            <div class="skeleton skeleton-text skeleton-value" style="width: 62%" />
            <div class="skeleton skeleton-text" style="width: 36%" />
          </div>
        </template>

        <template v-else>
          <div v-for="f in bandFacts" :key="f.label" class="fact">
            <span class="fact-label"><component :is="f.icon" :size="12" />{{ f.label }}</span>
            <span class="fact-value" :class="f.tone">{{ f.value }}</span>
            <span class="fact-foot mono truncate" :title="f.foot">{{ f.foot }}</span>
          </div>
        </template>
      </section>

      <template v-if="about">
        <div class="ab-grid">
          <!-- ══ 产品信息 ══ -->
          <section class="card">
            <header class="card-head">
              <h2 class="card-title"><Info :size="14" />{{ t('admin.about.productTitle') }}</h2>
              <span class="badge badge-up">OPEN SOURCE</span>
            </header>

            <div class="ab-kv">
              <div class="kv-row">
                <span class="ab-kv-k">{{ t('admin.about.productArchitecture') }}</span>
                <span class="ab-kv-v">{{ about.product?.name }}</span>
              </div>
              <div class="kv-row">
                <span class="ab-kv-k">{{ t('admin.about.systemVersion') }}</span>
                <span class="ab-kv-v mono is-accent">v{{ about.product?.version }}</span>
              </div>
              <div class="kv-row">
                <span class="ab-kv-k">{{ t('admin.about.controlPlane') }}</span>
                <span class="ab-kv-v mono">{{ about.product?.control_plane }} (v{{ about.product?.gateway_version }})</span>
              </div>
              <div class="kv-row">
                <span class="ab-kv-k">{{ t('admin.about.runtime') }}</span>
                <span class="ab-kv-v mono">Python {{ about.runtime?.python }}</span>
              </div>
            </div>

            <footer class="ab-block-foot">
              <a
                href="https://github.com/555cute/astraquant"
                target="_blank"
                rel="noopener noreferrer"
                class="btn btn-primary btn-sm"
              >
                <GitBranch :size="13" aria-hidden="true" />
                <span>{{ t('admin.about.repoLink') }}</span>
                <span class="sr-only">{{ t('common.opensInNewTab') }}</span>
              </a>
            </footer>
          </section>

          <!-- ══ 组件版本 ══ -->
          <section class="card">
            <header class="card-head">
              <h2 class="card-title">{{ t('admin.about.componentsTitle') }}</h2>
              <span class="card-sub">{{ t('admin.about.componentsSub') }}</span>
            </header>

            <BaseEmpty v-if="!(about.components || []).length" :text="t('common.noRecords')" />

            <div v-else class="ab-comps">
              <div v-for="c in about.components" :key="c.name" class="kv-row">
                <span class="ab-comp-name">{{ c.name }}</span>
                <span class="ab-comp-ver mono num">{{ c.version }}</span>
              </div>
            </div>
          </section>
        </div>

        <!-- ══ 注册通道（横跨两栏）══ -->
        <section class="card ab-channels">
          <header class="card-head">
            <h2 class="card-title"><Link2 :size="14" />{{ t('admin.about.channelsTitle') }}</h2>
            <span class="card-sub">{{ t('admin.about.channelsSub') }}</span>
          </header>

          <p class="ab-channels-lead">{{ t('admin.about.channelsLead') }}</p>

          <div class="ab-channels-grid">
            <div v-for="ch in channelRows" :key="ch.key" class="ab-channel">
              <div class="ab-channel-head">
                <span class="ab-channel-name">{{ ch.name }}</span>
              </div>

              <template v-if="ch.invite_url">
                <span class="ab-channel-url mono truncate" :title="ch.invite_url">{{ ch.invite_url }}</span>
                <div class="ab-channel-actions">
                  <a :href="ch.invite_url" target="_blank" rel="noopener noreferrer"
                     class="btn btn-primary btn-sm">
                    <ExternalLink :size="13" aria-hidden="true" />
                    <span>{{ t('admin.about.channelOpen') }}</span>
                    <span class="sr-only">{{ t('common.opensInNewTab') }}</span>
                  </a>
                  <CopyButton :text="ch.invite_url" :label="true" />
                </div>
              </template>
              <span v-else class="ab-channel-url ab-channel-unset">{{ t('admin.about.channelUnset') }}</span>
            </div>
          </div>
        </section>

        <!-- ══ 安全更新 ══ -->
        <section class="card">
          <header class="card-head">
            <h2 class="card-title"><ShieldCheck :size="14" />{{ t('admin.about.securityUpdate') }}</h2>
            <span class="badge badge-accent mono">FF-ONLY</span>
          </header>

          <!-- git 遥测 -->
          <div class="ab-telemetry">
            <div class="ab-tel">
              <span class="label-caps">{{ t('admin.about.currentBranch') }}</span>
              <span class="ab-tel-v mono">{{ about.update?.branch || 'main' }}</span>
            </div>
            <div class="ab-tel">
              <span class="label-caps">{{ t('admin.about.localCommit') }}</span>
              <span class="ab-tel-v mono is-accent">{{ about.update?.local || '--' }}</span>
            </div>
            <div class="ab-tel">
              <span class="label-caps">{{ t('admin.about.remoteCommit') }}</span>
              <span class="ab-tel-v mono">{{ about.update?.remote || t('admin.about.pending') }}</span>
            </div>
            <div class="ab-tel">
              <span class="label-caps">{{ t('admin.about.syncGap') }}</span>
              <span
                class="ab-tel-v"
                :class="(about.update?.behind || 0) > 0 ? 'is-warn' : 'is-up'"
              >
                {{ (about.update?.behind || 0) > 0
                  ? t('admin.about.behind', undefined, { n: about.update?.behind })
                  : t('admin.about.upToDate') }}
                <span v-if="about.update?.ahead" class="ab-ahead">
                  {{ t('admin.about.ahead', undefined, { n: about.update.ahead }) }}
                </span>
              </span>
            </div>
          </div>

          <!-- 动作 -->
          <div class="ab-actions">
            <button type="button"
              class="btn btn-ghost btn-sm"
              :disabled="updateChecking || updateRunning"
              @click="checkUpdate"
            >
              <Loader2 v-if="updateChecking" :size="14" class="animate-spin shrink-0" />
              <RefreshCw v-else :size="14" />
              <span>{{ updateChecking ? t('admin.about.connecting') : t('admin.about.checkUpdate') }}</span>
            </button>

            <button type="button"
              class="btn btn-primary btn-sm"
              :disabled="updateChecking || updateRunning"
              @click="openUpdateModal"
            >
              <Download :size="14" />
              <span>{{ t('admin.about.runUpdate') }}</span>
            </button>
          </div>

          <!-- 结果 -->
          <!-- 批 70：本页刻意不弹 toast（错误出口一一写进 updateResult），
               所以这块内联结果就是**唯一反馈**，必须自己通报给读屏器。 -->
          <div
            v-if="updateResult"
            class="ab-result"
            :class="updateResult.error ? 'is-error' : 'is-ok'"
            :role="updateResult.error ? 'alert' : 'status'"
            aria-live="polite"
          >
            <span class="ab-result-icon">
              <AlertTriangle v-if="updateResult.error" :size="15" />
              <CheckCircle2 v-else :size="15" />
            </span>
            <div class="ab-result-body">
              <p class="ab-result-title">{{ updateResult.error || updateResult.message }}</p>
              <p v-if="updateResult.restart_note" class="ab-result-note">{{ updateResult.restart_note }}</p>
            </div>
          </div>

          <!-- git 输出 -->
          <div v-if="updateResult?.git_output" class="log-panel ab-git">
            <div class="ab-git-head">
              <Terminal :size="12" />
              <span>{{ t('admin.about.gitOutput') }}</span>
            </div>
            <pre class="ab-git-pre" tabindex="0">{{ updateResult.git_output }}</pre>
          </div>

          <footer class="ab-note">
            <AlertTriangle :size="12" />
            <span>{{ t('admin.about.safetyNote') }}</span>
          </footer>
        </section>
      </template>
    </template>

    <!-- ══ 更新确认 ══ -->
    <BaseDialog
      :open="showConfirmModal"
      :title="t('admin.about.confirmTitle')"
      :desc="t('admin.about.confirmSubtitle')"
      size="sm"
      initial-focus=".ab-confirm-input"
      @close="showConfirmModal = false"
    >
      <div class="ab-confirm">
        <p class="ab-confirm-text">
          {{ t('admin.about.confirmPrefix') }}
          <code class="ab-confirm-phrase">UPDATE R20</code>{{ t('admin.about.confirmSuffix') }}
        </p>
        <input
          v-model="confirmPhrase"
          type="text"
          autocomplete="off"
          spellcheck="false"
          :class="{ 'is-bad': !!confirmPhrase && !phaseOk }"
          :aria-invalid="!!confirmPhrase && !phaseOk ? 'true' : undefined"
          :aria-label="t('admin.about.phrasePlaceholder')"
          :placeholder="t('admin.about.phrasePlaceholder')"
          class="field mono ab-confirm-input"
          @keyup.enter="executeUpdate"
        />
      </div>

      <template #footer>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="updateRunning" @click="showConfirmModal = false">
          {{ t('admin.about.cancel') }}
        </button>
        <button type="button"
          class="btn btn-primary btn-sm"
          :disabled="!phaseOk || updateRunning"
          @click="executeUpdate"
        >
          <Loader2 v-if="updateRunning" :size="14" class="animate-spin shrink-0" />
          <span>{{ updateRunning ? t('admin.about.updating') : t('admin.about.confirmNow') }}</span>
        </button>
      </template>
    </BaseDialog>
  </div>
</template>

<style scoped>
.ab {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-4);
}

/* ══ 状态带 ══ */









/* ══ 注册通道 ══ */
.ab-channels {
  margin-top: var(--ds-space-4);
}
.ab-channels-lead {
  margin: 0 0 var(--ds-space-3);
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: var(--ds-color-text-description);
}
.ab-channels-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--ds-space-3);
}
@media (min-width: 760px) {
  .ab-channels-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
.ab-channel {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-2);
  padding: var(--ds-space-3);
  border: 1px solid var(--ds-color-border-default);
  border-radius: var(--r-card);
  background: var(--ds-color-bg-surface-inset);
  min-width: 0;
}
.ab-channel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ds-space-2);
}
.ab-channel-name {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--ds-color-text-primary);
}
.ab-channel-url {
  font-family: var(--ds-font-mono);
  font-size: var(--text-4xs);
  color: var(--ds-color-text-placeholder);
  min-width: 0;
}
.ab-channel-unset {
  color: var(--ds-color-text-placeholder);
  font-family: inherit;
}
.ab-channel-actions {
  display: flex;
  align-items: center;
  gap: var(--ds-space-2);
  margin-top: auto;
}

/* ══ 双栏 ══ */
.ab-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--ds-space-4);
  align-items: start;
}
@media (min-width: 1000px) {
  .ab-grid {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }
}

/* 产品信息 kv */
.ab-kv {
  display: flex;
  flex-direction: column;
}
.ab-kv-k {
  font-size: var(--text-xs);
  color: var(--ds-color-text-description);
}
.ab-kv-v {
  font-size: var(--text-xs);
  color: var(--ds-color-text-primary);
  text-align: right;
  overflow-wrap: anywhere;
}
.ab-kv-v.is-accent {
  color: var(--ds-color-brand);
}
.ab-block-foot {
  display: flex;
  padding: var(--ds-space-3) var(--ds-space-4);
  border-top: 1px solid var(--ds-color-border-default);
  background-color: var(--ds-color-bg-surface-inset);
}

/* 组件版本 */
.ab-comps {
  display: flex;
  flex-direction: column;
}
/* 批 96：此处原有 `.ab-comp:last-child { border-bottom: 0 }`（末行不封口）——
   批 90 把行本体并入 `.kv-row` 后，该类名已不在模板里，此条成了**死规则**；
   「末行不封口」的行为现由原件 `.kv-row:last-child` 统一提供。
   批 90 的判据只拉黑了 `ab-comp"`（带引号）这种**模板用法**，漏掉了
   **伪类形态的 CSS 规则**；本批把死 CSS 探针扩展到 scoped 样式后由它抓出。 */
.ab-comp-name {
  font-size: var(--text-xs);
  color: var(--ds-color-text-secondary);
}
.ab-comp-ver {
  font-size: var(--text-3xs);
  color: var(--ds-color-text-placeholder);
}

/* ══ git 遥测 ══ */
.ab-telemetry {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  border-bottom: 1px solid var(--ds-color-border-default);
}
@media (min-width: 900px) {
  .ab-telemetry {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
.ab-tel {
  display: flex;
  flex-direction: column;
  gap:4px;
  min-width: 0;
  padding: var(--ds-space-3) var(--ds-space-4);
  border-left: 1px solid var(--ds-color-border-default);
}
.ab-tel:nth-child(odd) {
  border-left: 0;
}
@media (min-width: 900px) {
  .ab-tel {
    border-left: 1px solid var(--ds-color-border-default);
  }
  .ab-tel:first-child {
    border-left: 0;
  }
}
.ab-tel-v {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--ds-color-text-primary);
  overflow-wrap: anywhere;
}
.ab-tel-v.is-accent {
  color: var(--ds-color-brand);
}
.ab-tel-v.is-up {
  color: var(--up);
}
.ab-tel-v.is-warn {
  color: var(--warn);
}
.ab-ahead {
  font-weight: 400;
  font-size: var(--text-4xs);
  color: var(--ds-color-text-placeholder);
}

.ab-actions {
  display: flex;
  align-items: center;
  gap: var(--ds-space-2);
  flex-wrap: wrap;
  padding: var(--ds-space-3) var(--ds-space-4);
}

/* 结果 */
.ab-result {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 0 var(--ds-space-4);
  padding: 10px var(--ds-space-3);
  border-radius: var(--r-ctl);
  font-size: var(--text-3xs);
  line-height: var(--leading-body);
}
.ab-result.is-ok {
  background-color: var(--up-bg);
  color: var(--up);
}
.ab-result.is-error {
  background-color: var(--down-bg);
  color: var(--down);
}
.ab-result-icon {
  flex-shrink: 0;
  margin-top: 1px;
}
.ab-result-title {
  font-weight: 600;
}
.ab-result-note {
  margin-top:4px;
  opacity: 0.9;
}

/* git 输出 */
.ab-git {
  margin: var(--ds-space-3) var(--ds-space-4) 0;
}
.ab-git-head {
  display: flex;
  align-items: center;
  gap:6px;
  padding: var(--ds-space-2) var(--ds-space-3);
  border-bottom: 1px solid var(--ds-color-border-default);
  font-size: var(--text-4xs);
  color: var(--ds-color-text-placeholder);
}
.ab-git-pre {
  margin: 0;
  padding: var(--ds-space-3);
  font-family: var(--ds-font-mono);
  font-size: var(--text-4xs);
  line-height: var(--leading-body);
  color: var(--ds-color-text-secondary);
  white-space: pre-wrap;
  max-height: 240px;
  overflow: auto;
}

.ab-note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: var(--ds-space-3) var(--ds-space-4);
  border-top: 1px solid var(--ds-color-border-default);
  font-size: var(--text-4xs);
  line-height: var(--leading-body);
  color: var(--ds-color-text-placeholder);
}
.ab-note > svg {
  flex-shrink: 0;
  margin-top: 2px;
}

/* ══ 确认弹窗 ══ */
.ab-confirm {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-3);
}
.ab-confirm-text {
  font-size: var(--text-xs);
  line-height: var(--leading-body);
  color: var(--ds-color-text-description);
}
.ab-confirm-phrase {
  padding:1px 6px;
  border-radius: var(--r-xs);
  background-color: var(--down-bg);
  color: var(--down);
  font-family: var(--ds-font-mono);
  font-weight: 600;
}
.ab-confirm-input {
  text-transform: uppercase;
}
</style>

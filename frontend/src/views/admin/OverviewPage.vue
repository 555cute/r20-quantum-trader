<script setup lang="ts">
/** 运行总览：服务健康四卡 → 快捷入口 → 决策快照 + 数据管道 + 最近审计 */
import { computed, onMounted, ref } from 'vue';
import {
  Server, Activity, Cpu, Wallet, Braces, Crosshair, Landmark,
  RefreshCw, ArrowRight, Database, ScrollText,
} from 'lucide-vue-next';
import { useApi } from '../../composables/useApi';
import { useI18n } from '../../composables/useI18n';
import { APP_VERSION } from '../../config/version';
import PageHeader from '../../components/admin/PageHeader.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';
import TimeAgo from '../../components/base/TimeAgo.vue';
import { fmtNum } from '../../utils/format';

const { t } = useI18n();
const { api } = useApi();

const runtime = ref<any>(null);
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    const [rt, cfg] = await Promise.all([
      api('/api/v1/admin/runtime').catch(() => null),
      api('/api/v1/admin/config').catch(() => null),
    ]);
    if (rt && cfg?.configuration) rt.configuration = { ...cfg.configuration, ...(rt.configuration || {}) };
    runtime.value = rt;
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const service = computed(() => runtime.value?.service || {});
const uptime = computed(() => {
  const s = Number(service.value.uptime_seconds || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
});
const llm = computed(() => runtime.value?.llm_runtime || {});
const creds = computed(() => runtime.value?.credentials || {});
const exchangeName = computed(() => String(creds.value.exchange || '').toUpperCase() || 'EXCHANGE');
const envLabel = computed(() => {
  const env = String(creds.value.environment || (creds.value.simulated_trading ? 'demo' : '') || '').toUpperCase();
  return env || '--';
});
const isDemo = computed(() => envLabel.value.includes('DEMO') || envLabel.value.includes('模拟'));
const credentialsReady = computed(() => Boolean(creds.value.configured));
const health = computed(() => runtime.value?.data_health || {});
const healthFiles = computed<any[]>(() => health.value.files || []);
const decisions = computed<any[]>(() => (runtime.value?.decisions || []).slice(0, 8));
const audits = computed<any[]>(() => (runtime.value?.audit || []).slice(0, 6));

const quickNavs = computed(() => [
  { to: '/admin/promptlib', icon: Braces, title: t('nav.admin.prompts'), desc: t('admin.overview.quick.prompts') },
  { to: '/admin/interceptors', icon: Crosshair, title: t('nav.admin.interceptors'), desc: t('admin.overview.quick.interceptors') },
  { to: '/admin/council', icon: Landmark, title: t('nav.admin.council'), desc: t('admin.overview.quick.council') },
  { to: '/admin/llm', icon: Cpu, title: t('nav.admin.llm'), desc: t('admin.overview.quick.llm') },
]);

function actionLabel(a: string): string {
  return String(a || '').replace('admin.', '');
}
</script>

<template>
  <div>
    <PageHeader :title="t('nav.admin.overview')" :description="t('admin.overview.desc')">
      <template #actions>
        <span class="badge badge-mono">{{ APP_VERSION }}</span>
        <button class="btn btn-ghost btn-sm" :disabled="loading" @click="load">
          <RefreshCw :class="loading && 'animate-spin'" />{{ t('common.refresh') }}
        </button>
      </template>
    </PageHeader>

    <!-- 健康四卡 -->
    <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <div class="card card-pad">
        <div class="flex items-center justify-between">
          <span class="t-label">{{ t('admin.overview.backend') }}</span>
          <Server class="h-4 w-4" style="color: var(--ink-3)" />
        </div>
        <p class="num mt-1.5 text-xl font-bold" style="color: var(--up)">{{ service.pid ? 'ONLINE' : '--' }}</p>
        <p class="t-faint num mt-0.5 text-xs">PID {{ service.pid || '--' }} · FastAPI</p>
      </div>

      <div class="card card-pad">
        <div class="flex items-center justify-between">
          <span class="t-label">{{ t('admin.overview.uptime') }}</span>
          <Activity class="h-4 w-4" style="color: var(--ink-3)" />
        </div>
        <p class="num mt-1.5 text-xl font-bold" style="color: var(--ink-strong)">{{ uptime }}</p>
        <p class="t-faint mt-0.5 text-xs">{{ t('status.running') }}</p>
      </div>

      <a class="card card-pad block transition-colors hover:bg-[var(--surface-3)]" href="/admin/llm">
        <div class="flex items-center justify-between">
          <span class="t-label">{{ t('admin.overview.brain') }}</span>
          <Cpu class="h-4 w-4" style="color: var(--ink-3)" />
        </div>
        <p class="num mt-1.5 truncate text-md font-bold" style="color: var(--ink-strong)">{{ llm.model || t('common.notConfigured') }}</p>
        <p class="t-faint mt-0.5 truncate text-xs">{{ llm.provider_name || '--' }} · {{ (llm.reasoning_effort || '').toUpperCase() }}</p>
      </a>

      <a class="card card-pad block transition-colors hover:bg-[var(--surface-3)]" href="/admin/security">
        <div class="flex items-center justify-between">
          <span class="t-label">{{ t('admin.overview.exchangeEnv') }}</span>
          <Wallet class="h-4 w-4" style="color: var(--ink-3)" />
        </div>
        <p class="mt-1.5 text-md font-bold" :style="{ color: isDemo ? 'var(--warn)' : 'var(--up)' }">{{ exchangeName }} {{ envLabel }}</p>
        <p class="t-faint mt-0.5 text-xs">{{ credentialsReady ? t('admin.overview.exchangeReady') : t('admin.overview.exchangeMissing') }} · {{ t('admin.overview.exchangeEnvHint') }}</p>
      </a>
    </div>

    <!-- 快捷入口 -->
    <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <RouterLink
        v-for="q in quickNavs"
        :key="q.to"
        :to="q.to"
        class="card card-pad group flex items-center gap-3 transition-colors hover:bg-[var(--surface-3)]"
      >
        <span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border" style="background-color: var(--surface-1); border-color: var(--line-1)">
          <component :is="q.icon" class="h-4 w-4" style="color: var(--accent)" />
        </span>
        <span class="min-w-0 flex-1">
          <span class="block truncate text-sm font-semibold" style="color: var(--ink-strong)">{{ q.title }}</span>
          <span class="block truncate text-xs" style="color: var(--ink-2)">{{ q.desc }}</span>
        </span>
        <ArrowRight class="h-4 w-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-60" style="color: var(--ink-2)" />
      </RouterLink>
    </div>

    <!-- 决策快照 + 数据管道 -->
    <div class="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-12">
      <section class="section xl:col-span-7">
        <div class="section-head">
          <div>
            <h2 class="section-title"><ScrollText class="h-4 w-4" style="color: var(--accent)" />{{ t('admin.overview.decisions') }}</h2>
            <p class="section-desc">{{ t('admin.overview.decisionsDesc') }}</p>
          </div>
          <RouterLink to="/admin/decisions" class="link text-xs">{{ t('admin.overview.viewAll') }} →</RouterLink>
        </div>
        <div class="section-body">
          <BaseEmpty v-if="!decisions.length" :text="t('common.noData')" />
          <div v-else class="table-scroll-container">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('dash.matrix.positions.col.symbol') }}</th>
                <th>{{ t('dash.radar.col.action') }}</th>
                <th class="col-num">{{ t('dash.matrix.matrix.col.conf') }}</th>
                <th>{{ t('dash.radar.detail.macro') }}</th>
                <th class="col-num">{{ t('dash.matrix.orders.decisionTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in decisions" :key="d.instId + d.updated_at">
                <td class="num font-semibold">{{ String(d.instId).split('-')[0] }}</td>
                <td>
                  <span class="dir" :class="d.action === 'BUY_LONG' ? 'dir-long' : d.action === 'SELL_SHORT' ? 'dir-short' : 'dir-flat'">
                    {{ d.action === 'BUY_LONG' ? t('common.dir.long') : d.action === 'SELL_SHORT' ? t('common.dir.short') : d.action }}
                  </span>
                </td>
                <td class="col-num">{{ fmtNum(d.confidence, 0) }}%</td>
                <td class="max-w-[280px] truncate text-xs" style="color: var(--ink-2)" :title="d.summary">{{ d.summary }}</td>
                <td class="col-num text-xs" style="color: var(--ink-3)">{{ String(d.updated_at).slice(11, 19) }}</td>
              </tr>
            </tbody>
          </table>
          </div>
        </div>
      </section>

      <section class="section xl:col-span-5">
        <div class="section-head">
          <div>
            <h2 class="section-title">
              <Database class="h-4 w-4" style="color: var(--accent)" />{{ t('admin.overview.dataHealth') }}
              <span class="badge" :class="health.overall === 'LIVE' ? 'badge-up' : 'badge-warn'">{{ health.overall || '--' }}</span>
            </h2>
            <p class="section-desc">{{ t('admin.overview.dataHealthDesc') }}</p>
          </div>
        </div>
        <div class="section-body">
          <div v-for="f in healthFiles" :key="f.name" class="flex items-center gap-3 border-b py-2 last:border-b-0" style="border-color: var(--line-1)">
            <span class="dot" :class="f.fresh ? 'dot-up' : 'dot-warn'" />
            <span class="num min-w-0 flex-1 truncate text-xs" style="color: var(--ink-1)">{{ f.name }}</span>
            <span class="num w-16 text-right text-xs" style="color: var(--ink-3)">{{ f.age_seconds != null ? Math.round(f.age_seconds / 60) + 'm' : '--' }}</span>
            <span class="num w-14 text-right text-xs" style="color: var(--ink-3)">{{ fmtNum((f.bytes || 0) / 1024, 0) }}K</span>
          </div>
        </div>
      </section>
    </div>

    <!-- 最近审计 -->
    <section class="section mt-3">
      <div class="section-head">
        <h2 class="section-title">{{ t('admin.overview.recentAudit') }}</h2>
        <RouterLink to="/admin/audit" class="link text-xs">{{ t('admin.overview.viewAll') }} →</RouterLink>
      </div>
      <div class="section-body">
        <BaseEmpty v-if="!audits.length" :text="t('common.noRecords')" />
        <div v-else class="space-y-1.5">
          <div v-for="(a, i) in audits" :key="i" class="flex items-center gap-3 text-xs">
            <span class="dot" :class="a.status === 'success' ? 'dot-up' : 'dot-down'" />
            <span class="num w-36 shrink-0" style="color: var(--ink-3)">{{ a.timestamp }}</span>
            <span class="num font-semibold" style="color: var(--ink-1)">{{ actionLabel(a.action) }}</span>
            <span class="min-w-0 flex-1 truncate" style="color: var(--ink-2)">{{ JSON.stringify(a.detail || {}) }}</span>
            <TimeAgo :time="a.timestamp" />
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

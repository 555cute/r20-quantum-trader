<script setup lang="ts">
/**
 * 运行总览页（Admin Runtime Overview）
 * 1. 服务核心指标四卡（服务状态、运行时长、大模型大脑、多所路由）
 * 2. 三所平权接入与路由中枢矩阵 (Tri-Venue Hub & Routing Matrix)
 * 3. 快捷管理入口 (Quick Navigations)
 * 4. 决策快照 + 数据管道健康 + 最近审计日志
 */
import { computed, onMounted, ref } from 'vue';
import {
  Server,
  Activity,
  Cpu,
  Wallet,
  Braces,
  Crosshair,
  Landmark,
  RefreshCw,
  ArrowRight,
  Database,
  ScrollText,
  ShieldCheck,
  Zap,
} from 'lucide-vue-next';
import { get } from '../../api/http';
import { useI18n } from '../../composables/useI18n';
import { APP_VERSION } from '../../config/version';
import { VENUE_KEYS, venueLabel, venueShortLabel } from '../../utils/venueMeta';
import { useVenueAccountsStore } from '../../stores/venueAccounts';
import { useDashboardStore } from '../../stores/dashboard';
import PageHeader from '../../components/admin/PageHeader.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';
import TimeAgo from '../../components/base/TimeAgo.vue';
import { fmtNum, fmtDateTime } from '../../utils/format';

const { t } = useI18n();
const venueStore = useVenueAccountsStore();
const dashStore = useDashboardStore();

const runtime = ref<any>(null);
const loading = ref(false);

async function load() {
  loading.value = true;
  try {
    const [rt, cfg] = await Promise.all([
      get('/api/v1/admin/runtime').catch(() => null),
      get('/api/v1/admin/config').catch(() => null),
      venueStore.refresh(true).catch(() => null),
      dashStore.fetchDashboard(false).catch(() => null),
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
const conf = computed<Record<string, string>>(() => runtime.value?.configuration || {});
const venueEnv = computed(() => conf.value['交易场所与路由'] || conf.value['OKX 当前环境'] || '三所对等 · DEMO');
const isDemo = computed(() => venueEnv.value.includes('DEMO') || venueEnv.value.includes('模拟'));
const health = computed(() => runtime.value?.data_health || {});
const healthFiles = computed<any[]>(() => health.value.files || []);
const decisions = computed<any[]>(() => (runtime.value?.decisions || []).slice(0, 8));
const audits = computed<any[]>(() => (runtime.value?.audit || []).slice(0, 6));

const quickNavs = computed(() => [
  { to: '/admin/security', icon: Wallet, title: '账户与标的', desc: '三所 API 凭证与合约标的池' },
  { to: '/admin/promptlib', icon: Braces, title: t('nav.admin.prompts'), desc: t('admin.overview.quick.prompts') },
  { to: '/admin/interceptors', icon: Crosshair, title: t('nav.admin.interceptors'), desc: t('admin.overview.quick.interceptors') },
  { to: '/admin/council', icon: Landmark, title: t('nav.admin.council'), desc: t('admin.overview.quick.council') },
]);

// 三所对等接入卡片数据
const venueCards = computed(() => {
  const brandColors: Record<string, string> = {
    okx: 'var(--venue-okx, #3880ff)',
    binance: 'var(--venue-binance, #f3ba2f)',
    gate: 'var(--venue-gate, #00be98)',
  };

  const healthList = dashStore.crossVenueHealth || [];

  return VENUE_KEYS.map((key) => {
    const acc = venueStore.venues?.[key];
    const h = healthList.find((item) => item.key === key);
    const avgMs = h?.avgMs != null ? `${Math.round(h.avgMs)}ms` : null;

    let statusText = '在线就绪';
    let statusCls = 'badge-up';
    let dotCls = 'dot-live';

    if (acc?.status === 'degraded' || (h?.failCount ?? 0) > 0) {
      statusText = '局部降级';
      statusCls = 'badge-warn';
      dotCls = 'dot-warn';
    } else if (acc?.status === 'unavailable' || (!acc && !h?.present)) {
      statusText = '未连接';
      statusCls = 'badge-down';
      dotCls = 'dot-down';
    }

    return {
      key,
      label: venueLabel(key),
      short: venueShortLabel(key),
      brandColor: brandColors[key],
      statusText,
      statusCls,
      dotCls,
      envText: isDemo.value ? 'DEMO 模拟' : 'LIVE 实盘',
      latency: avgMs || (acc ? '<50ms' : '--'),
      okCount: h?.okCount ?? (acc ? 1 : 0),
    };
  });
});

function actionLabel(a: string): string {
  return String(a || '').replace('admin.', '');
}
</script>

<template>
  <div class="space-y-3.5">
    <PageHeader :title="t('nav.admin.overview')" :description="t('admin.overview.desc')">
      <template #actions>
        <span class="badge badge-mono">{{ APP_VERSION }}</span>
        <button type="button" class="btn btn-ghost btn-sm cursor-pointer" :disabled="loading" @click="load">
          <RefreshCw :class="loading && 'animate-spin'" />{{ t('common.refresh') }}
        </button>
      </template>
    </PageHeader>

    <!-- 1. 服务健康四卡 -->
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

      <RouterLink class="card card-pad block transition-colors hover:bg-[var(--surface-3)]" to="/admin/llm">
        <div class="flex items-center justify-between">
          <span class="t-label">{{ t('admin.overview.brain') }}</span>
          <Cpu class="h-4 w-4" style="color: var(--ink-3)" />
        </div>
        <p class="num mt-1.5 truncate text-md font-bold" style="color: var(--ink-strong)">{{ llm.model || t('common.notConfigured') }}</p>
        <p class="t-faint mt-0.5 truncate text-xs">{{ llm.provider_name || '--' }} · {{ (llm.reasoning_effort || '').toUpperCase() }}</p>
      </RouterLink>

      <RouterLink class="card card-pad block transition-colors hover:bg-[var(--surface-3)]" to="/admin/security">
        <div class="flex items-center justify-between">
          <span class="t-label">选所路由策略</span>
          <Wallet class="h-4 w-4" style="color: var(--ink-3)" />
        </div>
        <p class="mt-1.5 text-md font-bold truncate" :style="{ color: isDemo ? 'var(--warn)' : 'var(--up)' }">
          {{ venueEnv }}
        </p>
        <p class="t-faint mt-0.5 text-xs">三所平权架构 · 点击配置</p>
      </RouterLink>
    </div>

    <!-- 2. 三所平权接入中枢矩阵 (Tri-Venue Parity Hub Matrix) -->
    <div class="card p-3.5 space-y-2.5 border" style="border-color: var(--line-1); background-color: var(--surface-1)">
      <div class="flex items-center justify-between border-b pb-2" style="border-color: var(--line-1)">
        <div class="flex items-center gap-1.5 text-xs font-bold text-[var(--ink-strong)]">
          <Wallet class="h-4 w-4 text-[var(--accent)]" />
          <span>多交易所平权接入中枢 (Tri-Venue Parity Matrix)</span>
        </div>
        <RouterLink to="/admin/security" class="link text-xs flex items-center gap-1">
          <span>三所凭证与标的池管理</span> →
        </RouterLink>
      </div>

      <!-- 三所卡片对等对称布局 -->
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
        <div
          v-for="v in venueCards"
          :key="v.key"
          class="p-3 rounded-lg border flex flex-col justify-between gap-1.5 transition-colors hover:bg-[var(--surface-2)]"
          style="background-color: var(--surface-2); border-color: var(--line-1)"
        >
          <div class="flex items-center justify-between">
            <span class="font-bold text-xs sm:text-sm" :style="{ color: v.brandColor }">
              {{ v.label }}
            </span>
            <span class="chip text-3xs" :class="v.statusCls">
              <span class="dot" :class="v.dotCls" />
              {{ v.statusText }}
            </span>
          </div>

          <div class="flex items-baseline justify-between text-xs mt-1">
            <span class="t-faint text-2xs">运行环境</span>
            <span class="badge badge-mono text-3xs font-semibold">{{ v.envText }}</span>
          </div>

          <div class="flex items-baseline justify-between text-xs">
            <span class="t-faint text-2xs">网络延迟</span>
            <span class="num font-semibold text-2xs flex items-center gap-0.5" style="color: var(--ink-1)">
              <Zap class="h-2.5 w-2.5 text-[var(--accent)]" />
              {{ v.latency }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 3. 快捷入口 -->
    <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
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
          <span class="block truncate text-xs sm:text-sm font-semibold" style="color: var(--ink-strong)">{{ q.title }}</span>
          <span class="block truncate text-2xs sm:text-xs" style="color: var(--ink-2)">{{ q.desc }}</span>
        </span>
        <ArrowRight class="h-4 w-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-60" style="color: var(--ink-2)" />
      </RouterLink>
    </div>

    <!-- 4. 决策快照 + 数据管道 -->
    <div class="grid grid-cols-1 gap-3 xl:grid-cols-12">
      <section class="section xl:col-span-7">
        <div class="section-head">
          <div>
            <h2 class="section-title">
              <ScrollText class="h-4 w-4" style="color: var(--accent)" />
              {{ t('admin.overview.decisions') }}
            </h2>
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
                  <td class="col-num text-xs" style="color: var(--ink-3)">{{ fmtDateTime(d.updated_at).slice(11, 19) }}</td>
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
              <Database class="h-4 w-4" style="color: var(--accent)" />
              {{ t('admin.overview.dataHealth') }}
              <span class="badge" :class="health.overall === 'LIVE' ? 'badge-up' : 'badge-warn'">{{ health.overall || '--' }}</span>
            </h2>
            <p class="section-desc">{{ t('admin.overview.dataHealthDesc') }}</p>
          </div>
        </div>
        <div class="section-body">
          <div
            v-for="f in healthFiles"
            :key="f.name"
            class="flex items-center gap-3 border-b py-2 last:border-b-0"
            style="border-color: var(--line-1)"
          >
            <span class="dot" :class="f.fresh ? 'dot-up' : 'dot-warn'" />
            <span class="num min-w-0 flex-1 truncate text-xs" style="color: var(--ink-1)">{{ f.name }}</span>
            <span class="num w-16 text-right text-xs" style="color: var(--ink-3)">
              {{ f.age_seconds != null ? Math.round(f.age_seconds / 60) + 'm' : '--' }}
            </span>
            <span class="num w-14 text-right text-xs" style="color: var(--ink-3)">
              {{ fmtNum((f.bytes || 0) / 1024, 0) }}K
            </span>
          </div>
        </div>
      </section>
    </div>

    <!-- 5. 最近审计日志 -->
    <section class="section">
      <div class="section-head">
        <h2 class="section-title">{{ t('admin.overview.recentAudit') }}</h2>
        <RouterLink to="/admin/audit" class="link text-xs">{{ t('admin.overview.viewAll') }} →</RouterLink>
      </div>
      <div class="section-body">
        <BaseEmpty v-if="!audits.length" :text="t('common.noRecords')" />
        <div v-else class="space-y-1.5">
          <div v-for="(a, i) in audits" :key="i" class="flex items-center gap-3 text-xs p-1.5 rounded hover:bg-[var(--surface-2)] transition-colors">
            <span class="dot" :class="a.status === 'success' ? 'dot-up' : 'dot-down'" />
            <span class="num w-36 shrink-0" style="color: var(--ink-3)">{{ fmtDateTime(a.timestamp) }}</span>
            <span class="num font-semibold text-[var(--accent)]">{{ actionLabel(a.action) }}</span>
            <span class="min-w-0 flex-1 truncate" style="color: var(--ink-2)">{{ JSON.stringify(a.detail || {}) }}</span>
            <TimeAgo :time="fmtDateTime(a.timestamp)" />
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

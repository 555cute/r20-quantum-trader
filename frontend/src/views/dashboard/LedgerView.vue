<script setup lang="ts">
import { fmtDate, fmtDateTime } from '../../utils/format';
/**
 * 交易台账视图：汇总带 → 筛选条 → 明细表（行点击 → 生命周期抽屉）→ 巡检日志折叠区。
 * 事实源：/api/all trades（交易所持仓史双源交叉验证重建）。
 */
import { computed, ref } from 'vue';
import { Download, ScrollText } from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtSigned, fmtPct, fmtPrice, arrow, dirClass, cleanReason } from '../../utils/format';
import BaseStat from '../../components/base/BaseStat.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';
import BaseSegmented from '../../components/base/BaseSegmented.vue';
import BaseCollapse from '../../components/base/BaseCollapse.vue';
import BasePager from '../../components/base/BasePager.vue';
import DirTag from '../../components/base/DirTag.vue';
import CryptoLogo from '../../components/dashboard/CryptoLogo.vue';
import LedgerDrawer from '../../components/dashboard/LedgerDrawer.vue';
import { useToast } from '../../composables/useToast';

const store = useDashboardStore();
const { t } = useI18n();
const toast = useToast();

const all = computed<any[]>(() => (store.data as any)?.trades || []);
const perf = computed<any>(() => (store.data as any)?.performance || {});

/* —— 筛选 —— */
const fVenue = ref<string>('all');
const fMode = ref<'all' | 'live' | 'demo'>('all');
const fStatus = ref<'all' | 'closed' | 'holding'>('closed');
const fSide = ref<'all' | 'long' | 'short'>('all');
const fResult = ref<'all' | 'win' | 'loss'>('all');
const fInst = ref('all');

const venueOptions = [
  { value: 'all', label: '全部场所' },
  { value: 'okx', label: 'OKX' },
  { value: 'binance', label: 'Binance' },
  { value: 'gate', label: 'Gate' },
];

const modeOptions = [
  { value: 'all', label: '全部账户' },
  { value: 'live', label: '实盘 (Live)' },
  { value: 'demo', label: '模拟 (Demo)' },
];

const instOptions = computed(() => {
  const set = new Set<string>(all.value.map((x) => x.inst));
  return [{ value: 'all', label: t('common.all') }, ...Array.from(set).sort().map((s) => ({ value: s, label: s }))];
});

const filtered = computed(() =>
  all.value.filter((x) => {
    if (fVenue.value !== 'all') {
      const v = String(x.venue || 'okx').toLowerCase();
      if (v !== fVenue.value) return false;
    }
    if (fMode.value !== 'all') {
      const m = String(x.account_mode || x.environment || 'live').toLowerCase();
      if (fMode.value === 'live' && !m.includes('live')) return false;
      if (fMode.value === 'demo' && !m.includes('demo')) return false;
    }
    if (fStatus.value === 'closed' && x.status === 'holding') return false;
    if (fStatus.value === 'holding' && x.status !== 'holding') return false;
    if (fSide.value !== 'all' && (fSide.value === 'long' ? x.side !== '多' : x.side !== '空')) return false;
    if (fResult.value === 'win' && !(Number(x.net_pnl) > 0)) return false;
    if (fResult.value === 'loss' && !(Number(x.net_pnl) <= 0)) return false;
    if (fInst.value !== 'all' && x.inst !== fInst.value) return false;
    return true;
  }),
);

/* —— 分页 —— */
const page = ref(1);
const PAGE = 20;
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE)));
const rows = computed(() => filtered.value.slice((page.value - 1) * PAGE, page.value * PAGE));

/* —— 汇总（US-009 包含资金费透视卡） —— */
const netSum = computed(() => filtered.value.reduce((s, x) => s + (Number(x.net_pnl) || 0), 0));
const feeSum = computed(() => filtered.value.reduce((s, x) => s + Math.abs(Number(x.fee) || 0), 0));
const fundingSum = computed(() => filtered.value.reduce((s, x) => s + (Number(x.funding_fee) || 0), 0));
const fundingIncome = computed(() => filtered.value.reduce((s, x) => s + Math.max(0, Number(x.funding_fee) || 0), 0));
const fundingExpense = computed(() => filtered.value.reduce((s, x) => s + Math.abs(Math.min(0, Number(x.funding_fee) || 0)), 0));
const wins = computed(() => filtered.value.filter((x) => Number(x.net_pnl) > 0).length);
const winRate = computed(() => (filtered.value.length ? Math.round((wins.value / filtered.value.length) * 1000) / 10 : null));
const best = computed(() => (perf.value.leaderboard || [])[0]);

/* —— 详情 —— */
const detail = ref<any>(null);

/* —— CSV 导出（US-009 包含 venue 与 account_mode） —— */
function exportCsv() {
  const head = ['inst', 'venue', 'account_mode', 'side', 'lever', 'open_time', 'open_px', 'close_time', 'close_px', 'margin', 'fee', 'funding_fee', 'net_pnl', 'roi_pct', 'duration', 'exit_reason', 'strategy'];
  const lines = [head.join(',')];
  for (const x of filtered.value) {
    lines.push(head.map((k) => `"${String((k === 'open_time' || k === 'close_time') ? (x[k] ? fmtDateTime(x[k]) + ' +08:00' : '') : (x[k] ?? '')).replaceAll('"', '""')}"`).join(','));
  }
  const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `r20-ledger-${fmtDate(new Date())}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast.ok(t('dash.ledger.exported'));
}

function dirOf(side: string): 'long' | 'short' {
  return side === '多' ? 'long' : 'short';
}

/** G10 场所徽章：行带 venue 才渲染；旧数据缺失不冒充（显示层不留假身份）。 */
const VENUE_LABELS: Record<string, string> = { okx: 'OKX', gate: 'Gate', binance: 'Binance' };
function venueLabel(v: unknown): string {
  return VENUE_LABELS[String(v || '').toLowerCase()] ?? String(v || '');
}
</script>

<template>
  <div class="space-y-3">

    <!-- 汇总带（US-009 包含资金费收支透视与净已实现盈亏） -->
    <div class="card grid grid-cols-2 gap-2 p-2 md:grid-cols-3 xl:grid-cols-6 xl:gap-0 xl:p-0">
      <BaseStat :label="t('dash.ledger.summary.total')" :value="fmtNum(filtered.length, 0)" />
      <BaseStat
        :label="t('dash.ledger.summary.winRate')"
        :value="winRate != null ? fmtNum(winRate, 1) + '%' : '--'"
        :delta="`${wins} / ${filtered.length}`"
        delta-tone="muted"
      />
      <BaseStat :label="t('dash.ledger.summary.net')" :value="fmtSigned(netSum)" :delta-tone="netSum >= 0 ? 'up' : 'down'" />
      <BaseStat :label="t('dash.ledger.summary.fees')" :value="`-${fmtNum(feeSum, 2)}`" delta-tone="muted" />
      <BaseStat
        label="资金费净收支"
        :value="fmtSigned(fundingSum)"
        :delta="`+${fmtNum(fundingIncome, 2)} / -${fmtNum(fundingExpense, 2)}`"
        :delta-tone="fundingSum >= 0 ? 'up' : 'down'"
        hint="累计资金费用净额与收支细分"
      />
      <BaseStat
        :label="t('dash.ledger.summary.pf')"
        :value="perf.profit_factor != null ? fmtNum(perf.profit_factor, 2) : '--'"
        :hint="t('dash.ledger.summary.tipPf')"
      />
    </div>

    <!-- 筛选条 + 明细表 -->
    <div class="card overflow-hidden">
      <div class="flex flex-wrap items-center gap-2 border-b px-3.5 py-2.5" style="border-color: var(--line-1)">
        <BaseSegmented
          v-model="fStatus"
          :options="[
            { value: 'all', label: t('common.all') },
            { value: 'closed', label: t('dash.ledger.status.closed') },
            { value: 'holding', label: t('status.running') },
          ]"
        />
        <BaseSegmented
          v-model="fSide"
          :options="[
            { value: 'all', label: t('common.all') },
            { value: 'long', label: t('common.dir.long') },
            { value: 'short', label: t('common.dir.short') },
          ]"
        />
        <BaseSegmented
          v-model="fResult"
          :options="[
            { value: 'all', label: t('common.all') },
            { value: 'win', label: t('dash.ledger.filters.results.win') },
            { value: 'loss', label: t('dash.ledger.filters.results.loss') },
          ]"
        />
        <select v-model="fVenue" class="field field-sm w-auto ms-auto" @change="page = 1">
          <option v-for="vo in venueOptions" :key="vo.value" :value="vo.value">{{ vo.label }}</option>
        </select>
        <select v-model="fMode" class="field field-sm w-auto" @change="page = 1">
          <option v-for="mo in modeOptions" :key="mo.value" :value="mo.value">{{ mo.label }}</option>
        </select>
        <select v-model="fInst" class="field field-sm w-auto" @change="page = 1">
          <option v-for="o in instOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <span class="t-faint num text-xs shrink-0">{{ t('dash.ledger.filters.n', undefined, { n: filtered.length, total: all.length }) }}</span>
        <button class="btn btn-ghost btn-sm shrink-0" :disabled="!filtered.length" @click="exportCsv">
          <Download />{{ t('dash.ledger.exportCsv') }}
        </button>
      </div>

      <BaseEmpty v-if="!filtered.length" :text="t('dash.ledger.empty')" />
      <template v-else>
        <div class="overflow-x-auto">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('dash.ledger.col.symbol') }}</th>
                <th class="col-num">{{ t('dash.ledger.col.entry') }}</th>
                <th class="col-num">{{ t('dash.ledger.col.exit') }}</th>
                <th class="col-num">{{ t('dash.ledger.col.pnl') }}</th>
                <th class="col-num">{{ t('dash.ledger.col.fees') }}</th>
                <th>{{ t('dash.ledger.col.hold') }}</th>
                <th>{{ t('dash.ledger.col.exitReason') }}</th>
                <th>{{ t('dash.ledger.col.time') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="x in rows" :key="x.id" class="clickable" @click="detail = x">
                <td>
                  <div class="flex items-center gap-1.5 flex-wrap">
                    <CryptoLogo :symbol="x.inst" :size="16" />
                    <span class="num font-semibold" style="color: var(--ink-strong)">{{ x.inst }}</span>
                    <DirTag :dir="dirOf(x.side)" />
                    <span class="badge badge-mono hidden xl:inline-flex">{{ x.lever }}</span>
                    <!-- US-009: 交易所与账户环境徽章 -->
                    <span
                      v-if="x.venue"
                      class="badge text-3xs font-bold px-1 py-0.2 rounded"
                      :style="String(x.venue).toLowerCase() === 'binance' ? { color: '#f3ba2f', borderColor: '#f3ba2f33', backgroundColor: '#f3ba2f15' } : String(x.venue).toLowerCase() === 'gate' ? { color: '#00be98', borderColor: '#00be9833', backgroundColor: '#00be9815' } : { color: '#3880ff', borderColor: '#3880ff33', backgroundColor: '#3880ff15' }"
                    >
                      {{ venueLabel(x.venue) }}
                    </span>
                    <span
                      class="badge text-3xs px-1 py-0.2 rounded"
                      :class="String(x.account_mode || x.environment || 'live').toUpperCase() === 'LIVE' ? 'badge-up' : 'badge-warn'"
                    >
                      {{ String(x.account_mode || x.environment || 'live').toUpperCase() }}
                    </span>
                    <span v-if="x.council?.ran" class="badge badge-up" :title="x.council.adopted_role ? t('dash.ledger.council.adopted', undefined, { seat: x.council.adopted_role }) : t('dash.ledger.council.ran')">🏛️</span>
                    <span v-else-if="x.council" class="badge badge-warn" :title="t('dash.ledger.council.degraded')">⚡</span>
                  </div>
                </td>
                <td class="col-num">{{ fmtPrice(x.open_px) }}</td>
                <td class="col-num" :class="x.status === 'holding' && 't-faint'">{{ x.status === 'holding' ? t('status.running') : fmtPrice(x.close_px) }}</td>
                <td class="col-num" :class="dirClass(x.net_pnl)">
                  {{ arrow(x.net_pnl) }} {{ fmtSigned(x.net_pnl) }}
                  <span class="t-faint block text-2xs">{{ fmtPct(x.roi_pct) }}</span>
                </td>
                <td class="col-num t-faint">
                  <span>{{ fmtNum(Math.abs(Number(x.fee) || 0), 2) }}</span>
                  <span v-if="Number(x.funding_fee || 0) !== 0" class="block text-3xs num" :class="Number(x.funding_fee) >= 0 ? 'up' : 'down'">
                    资: {{ Number(x.funding_fee) >= 0 ? '+' : '' }}{{ fmtNum(x.funding_fee, 2) }}
                  </span>
                </td>
                <td class="num text-xs" style="color: var(--ink-2)">{{ x.duration || '--' }}</td>
                <td class="text-xs" style="color: var(--ink-2)">{{ cleanReason(x.exit_reason) }}</td>
                <td class="num text-xs" style="color: var(--ink-3)">{{ fmtDateTime(x.close_time).slice(5, 16) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div style="border-top: 1px solid var(--line-1)">
          <BasePager v-model:page="page" :page-count="pageCount" :total="filtered.length" />
        </div>
      </template>
    </div>

    <!-- 巡检日志 -->
    <BaseCollapse>
      <template #head>
        <span class="flex items-center gap-2 text-sm font-semibold" style="color: var(--ink-strong)">
          <ScrollText class="h-4 w-4" style="color: var(--accent)" />{{ t('dash.ledger.logs.title') }}
          <span class="t-faint font-normal">{{ t('dash.ledger.logs.desc') }}</span>
        </span>
      </template>
      <div class="scroll-y max-h-80 p-2">
        <BaseEmpty v-if="!store.logs.length" :text="t('dash.ledger.logs.empty')" />
        <pre v-else class="code-block whitespace-pre-wrap">{{ store.logs.join('\n') }}</pre>
      </div>
    </BaseCollapse>

    <LedgerDrawer :trade="detail" @close="detail = null" />
  </div>
</template>

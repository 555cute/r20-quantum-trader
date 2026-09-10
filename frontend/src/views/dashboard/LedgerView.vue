<script setup lang="ts">
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
const fStatus = ref<'all' | 'closed' | 'holding'>('closed');
const fSide = ref<'all' | 'long' | 'short'>('all');
const fResult = ref<'all' | 'win' | 'loss'>('all');
const fInst = ref('all');

const instOptions = computed(() => {
  const set = new Set<string>(all.value.map((x) => x.inst));
  return [{ value: 'all', label: t('common.all') }, ...Array.from(set).sort().map((s) => ({ value: s, label: s }))];
});

const filtered = computed(() =>
  all.value.filter((x) => {
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

/* —— 汇总 —— */
const netSum = computed(() => filtered.value.reduce((s, x) => s + (Number(x.net_pnl) || 0), 0));
const feeSum = computed(() => filtered.value.reduce((s, x) => s + Math.abs(Number(x.fee) || 0), 0));
const wins = computed(() => filtered.value.filter((x) => Number(x.net_pnl) > 0).length);
const winRate = computed(() => (filtered.value.length ? Math.round((wins.value / filtered.value.length) * 1000) / 10 : null));
const best = computed(() => (perf.value.leaderboard || [])[0]);

/* —— 详情 —— */
const detail = ref<any>(null);

/* —— CSV 导出 —— */
function exportCsv() {
  const head = ['inst', 'side', 'lever', 'open_time', 'open_px', 'close_time', 'close_px', 'margin', 'fee', 'net_pnl', 'roi_pct', 'duration', 'exit_reason', 'strategy'];
  const lines = [head.join(',')];
  for (const x of filtered.value) {
    lines.push(head.map((k) => `"${String(x[k] ?? '').replaceAll('"', '""')}"`).join(','));
  }
  const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `r20-ledger-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast.ok(t('dash.ledger.exported'));
}

function dirOf(side: string): 'long' | 'short' {
  return side === '多' ? 'long' : 'short';
}
</script>

<template>
  <div class="space-y-3">

    <!-- 汇总带 -->
    <div class="card grid grid-cols-2 gap-2 p-2 md:grid-cols-3 xl:grid-cols-6 xl:gap-0 xl:p-0">
      <BaseStat :label="t('dash.ledger.summary.total')" :value="fmtNum(filtered.length, 0)" />
      <BaseStat
        :label="t('dash.ledger.summary.winRate')"
        :value="winRate != null ? fmtNum(winRate, 1) + '%' : '--'"
        :delta="`${wins} / ${filtered.length}`"
        delta-tone="muted"
       
      />
      <BaseStat
        :label="t('dash.ledger.summary.pf')"
        :value="perf.profit_factor != null ? fmtNum(perf.profit_factor, 2) : '--'"
        :hint="t('dash.ledger.summary.tipPf')"
       
      />
      <BaseStat :label="t('dash.ledger.summary.net')" :value="fmtSigned(netSum)" :delta-tone="netSum >= 0 ? 'up' : 'down'" />
      <BaseStat :label="t('dash.ledger.summary.fees')" :value="`-${fmtNum(feeSum, 2)}`" delta-tone="muted" />
      <BaseStat
        v-if="best"
        :label="t('dash.ledger.summary.best')"
        :value="best.inst"
        :delta="fmtSigned(best.pnl)"
        delta-tone="up"
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
        <select v-model="fInst" class="field field-sm w-auto ms-auto" @change="page = 1">
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
                  <div class="flex items-center gap-2">
                    <CryptoLogo :symbol="x.inst" :size="16" />
                    <span class="num font-semibold" style="color: var(--ink-strong)">{{ x.inst }}</span>
                    <DirTag :dir="dirOf(x.side)" />
                    <span class="badge badge-mono hidden xl:inline-flex">{{ x.lever }}</span>
                    <span v-if="x.council?.ran" class="badge badge-up" :title="x.council.adopted_role ? t('dash.ledger.council.adopted', { seat: x.council.adopted_role }) : t('dash.ledger.council.ran')">🏛️</span>
                    <span v-else-if="x.council" class="badge badge-warn" :title="t('dash.ledger.council.degraded')">⚡</span>
                  </div>
                </td>
                <td class="col-num">{{ fmtPrice(x.open_px) }}</td>
                <td class="col-num" :class="x.status === 'holding' && 't-faint'">{{ x.status === 'holding' ? t('status.running') : fmtPrice(x.close_px) }}</td>
                <td class="col-num" :class="dirClass(x.net_pnl)">
                  {{ arrow(x.net_pnl) }} {{ fmtSigned(x.net_pnl) }}
                  <span class="t-faint block text-2xs">{{ fmtPct(x.roi_pct) }}</span>
                </td>
                <td class="col-num t-faint">{{ fmtNum(Math.abs(Number(x.fee) || 0), 2) }}</td>
                <td class="num text-xs" style="color: var(--ink-2)">{{ x.duration || '--' }}</td>
                <td class="text-xs" style="color: var(--ink-2)">{{ cleanReason(x.exit_reason) }}</td>
                <td class="num text-xs" style="color: var(--ink-3)">{{ String(x.close_time || '').slice(5, 16) }}</td>
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

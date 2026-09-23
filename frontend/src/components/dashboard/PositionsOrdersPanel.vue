<script setup lang="ts">
/**
 * PositionsOrdersPanel.vue · DeepSeek Harness 开发者工作台持仓与挂单面板
 * 侧栏/工位双向联动，低饱和黑白/深灰主题，高密度表格与清晰订单状态
 */
import { computed, ref } from 'vue';
import { useDashboardStore } from '../../stores/dashboard';
import { useI18n } from '../../composables/useI18n';
import { useRovingTabs } from '../../composables/useRovingTabs';
import { fmtNum, fmtSigned, fmtPct, fmtPrice, arrow } from '../../utils/format';
import { venueToneCls } from '../../utils/venueMeta';
import { ShieldCheck, ShieldAlert } from 'lucide-vue-next';
import BaseSegmented from '../base/BaseSegmented.vue';
import BaseEmpty from '../base/BaseEmpty.vue';
import DirTag from '../base/DirTag.vue';
import TimeAgo from '../base/TimeAgo.vue';
import CryptoLogo from './CryptoLogo.vue';

const emit = defineEmits<{ (e: 'pick-symbol', instId: string): void }>();

const store = useDashboardStore();
const { t } = useI18n();

const tab = ref<'positions' | 'orders'>('positions');

type VenueFilter = 'all' | 'okx' | 'binance' | 'gate';
const selectedVenue = ref<VenueFilter>('all');

/** 批 66：场所过滤胶囊的选项表（原为模板内联字面量，无法索引，故上提为 computed）。 */
const venueTabs = computed<{ key: VenueFilter; label: string }[]>(() => [
  { key: 'all', label: t('common.all') },
  { key: 'okx', label: 'OKX' },
  { key: 'binance', label: 'Binance' },
  { key: 'gate', label: 'Gate' },
]);

// 漫游 tabindex + ←/→/Home/End：此前一组 4 个 role="tab" 全在 Tab 键顺序里且方向键无响应。
const { setRef: setVenueRef, onKeydown: onVenueKey, roving: venueRoving } = useRovingTabs(
  () => venueTabs.value.length,
  (i) => { selectedVenue.value = venueTabs.value[i].key; },
);

const positions = computed(() => store.positions);
const orders = computed(() => store.pendingOrders);

function getVenueOf(item: any): string {
  const v = String(item?.venue || item?.exchange || '').toLowerCase();
  if (v.includes('binance')) return 'binance';
  if (v.includes('gate')) return 'gate';
  return 'okx';
}

function getModeOf(item: any): 'LIVE' | 'DEMO' {
  if (item?.account_mode) return item.account_mode.toUpperCase() === 'LIVE' ? 'LIVE' : 'DEMO';
  if (item?.environment) return item.environment.toLowerCase() === 'live' ? 'LIVE' : 'DEMO';
  if (item?.is_simulated !== undefined) return item.is_simulated ? 'DEMO' : 'LIVE';
  const storeEnv = (store.data as any)?.environment || (store.account as any)?.environment;
  if (storeEnv) return String(storeEnv).toLowerCase() === 'live' ? 'LIVE' : 'DEMO';
  return 'DEMO';
}

const filteredPositions = computed(() => {
  if (selectedVenue.value === 'all') return positions.value;
  return positions.value.filter((p) => getVenueOf(p) === selectedVenue.value);
});

const filteredOrders = computed(() => {
  if (selectedVenue.value === 'all') return orders.value;
  return orders.value.filter((o) => getVenueOf(o) === selectedVenue.value);
});

function posPnl(p: any): number {
  return Number(p.upl ?? 0);
}
function posRoi(p: any): number {
  return Number(p.roi_pct ?? p.uplRatio ?? 0);
}
function ocoOk(p: any): boolean {
  return p.cloud_oco_verified !== false && p.protectionStatus !== 'unprotected';
}
/** 孤儿腿候选数（可归因：本方标签或台账同向同量已平记录）——**只报告**，撤销是显式运营动作。 */
function orphanCandidates(p: any): number {
  const o = p?.protectionOrphans;
  const sym = String(p?.name || p?.base || '').toUpperCase();
  const list = o && o.readable ? (o.attributed || []) : [];
  return (sym ? list.filter((x: any) => String(x.symbol || '').toUpperCase() === sym) : (o && o.readable ? o.attributed || [] : [])).length;
}
/** 归属不可判定的孤儿腿数（按纪律一律不碰）。 */
function orphanUnattributed(p: any): number {
  const o = p?.protectionOrphans;
  const sym = String(p?.name || p?.base || '').toUpperCase();
  const list = o && o.readable ? (o.unattributed || []) : [];
  return (sym ? list.filter((x: any) => String(x.symbol || '').toUpperCase() === sym) : (o && o.readable ? o.unattributed || [] : [])).length;
}
/** 方向或量与任何持仓都对不上的腿数（方向不符的不计覆盖；量不符的仍计覆盖）。 */
function orphanMismatch(p: any): number {
  const o = p?.protectionOrphans;
  if (!o || !o.readable) return 0;
  const sym = String(p?.name || p?.base || '').toUpperCase();
  const filterList = (arr: any[]) => sym ? arr.filter((x: any) => String(x.symbol || '').toUpperCase() === sym) : arr;
  return filterList(o.sideMismatch || []).length + filterList(o.sizeMismatch || []).length;
}
/** 读到了但认不出的腿数（认不出类型 + 行解析不了）：**不计入覆盖** ⇒ 覆盖可能被低估。 */
function orphanUnclassified(p: any): number {
  const o = p?.protectionOrphans;
  if (!o || !o.readable) return 0;
  return (o.foreignCount || 0) + (o.unparsedCount || 0);
}
/** 读腿失败 ⇒ 不可判定（**不是**没有孤儿腿）。 */
function orphanReadFailed(p: any): boolean {
  return !!p?.protectionOrphans && p.protectionOrphans.readable === false;
}
/** 保护腿触发价类型 → 短标签（`''` = 没有该类腿/后端未给 ⇒ **不显示**，不编）。 */
function slTriggerType(p: any): string {
  const v = String(p?.protectionSlTriggerPxType ?? '').toLowerCase();
  if (v === 'mark') return t('dash.matrix.positions.triggerMark');
  if (v === 'last') return t('dash.matrix.positions.triggerLast');
  if (v === 'index') return t('dash.matrix.positions.triggerIndex');
  // Binance 的自描述字面量（MARK_PRICE / CONTRACT_PRICE）
  if (v === 'mark_price') return t('dash.matrix.positions.triggerMarkPrice');
  if (v === 'contract_price') return t('dash.matrix.positions.triggerContractPrice');
  // Gate 的数字码：**原样显示**（本仓未核实官方映射 ⇒ 不翻译，避免编一个中文名）
  if (v.startsWith('price_type:')) return v;
  if (v === 'unknown') return t('dash.matrix.positions.triggerUnknown');
  return '';
}
/** 悬停说明：为什么这件事重要（`last` 一根插针就能提前打掉保护）。 */
function slTriggerTypeHint(p: any): string {
  const v = String(p?.protectionSlTriggerPxType ?? '').toLowerCase();
  if (v === 'mark') return t('dash.matrix.positions.triggerMarkHint');
  if (v === 'last') return t('dash.matrix.positions.triggerLastHint');
  if (v === 'index') return t('dash.matrix.positions.triggerIndexHint');
  if (v === 'mark_price') return t('dash.matrix.positions.triggerMarkHint');
  if (v === 'contract_price') return t('dash.matrix.positions.triggerLastHint');
  if (v.startsWith('price_type:')) return t('dash.matrix.positions.triggerRawCodeHint');
  if (v === 'unknown') return t('dash.matrix.positions.triggerUnknownHint');
  return '';
}
function orderDir(o: any): 'long' | 'short' {
  return String(o.posSide || (o.side === 'buy' ? 'long' : 'short')).toLowerCase() as any;
}
function symOf(x: { instId?: string; name?: string }): string {
  return x.name || String(x.instId || '').split('-')[0];
}
function getTp1(p: any): string | null {
  if (p?.scaleOutTp) return String(p.scaleOutTp);
  const desc = String(p?.stageDesc || '');
  const m = desc.match(/TP1:\s*([0-9.]+)/i);
  return m ? m[1] : null;
}

function orderQtyText(o: any): string {
  const raw = o?.sz !== undefined ? o.sz : o?.size;
  const n = Math.abs(Number(raw || 0));
  if (!Number.isFinite(n) || n === 0) return '--';
  const v = getVenueOf(o);
  if (v === 'binance') {
    return n < 1 ? fmtNum(n, 3) : (n < 10 ? fmtNum(n, 2) : fmtNum(n, 1));
  }
  return fmtNum(n, 0);
}

function orderNativeUnit(o: any): string {
  const v = getVenueOf(o);
  return v === 'binance' ? symOf(o) : t('dash.matrix.orders.contractsUnit');
}

/**
 * 挂单保证金（USDT）——**唯一权威是后端**。
 *
 * 后端按各所合约面值（`instrument_pool.ctVal`）与杠杆算好后放进 `margin_usdt`
 * （见 `dashboard_payload/order_view.py` 与 `multi_venue.py`）。前端**绝不**自己
 * 维护面值表：那种表一旦与池子漂移，屏幕上就会显示一个凭空捏造的保证金数字，
 * 而保证金正是交易员判断仓位大小的依据 —— 宁可显示原生张数，也不给假数字。
 *
 * 返回 0 表示"后端没给"（旧数据/字段缺失）→ 调用方回落到原生张数展示。
 */
function orderMargin(o: any): number {
  const m = Number(o?.margin_usdt);
  return Number.isFinite(m) && m > 0 ? m : 0;
}

function orderMarginText(o: any): string {
  const m = orderMargin(o);
  if (m > 0) {
    return `${fmtNum(m, 2)}U`;
  }
  const raw = orderQtyText(o);
  return raw !== '--' ? `${raw} ${orderNativeUnit(o)}` : '--';
}

function orderTooltipText(o: any): string {
  const m = orderMargin(o);
  const native = `${orderQtyText(o)} ${orderNativeUnit(o)}`;
  return m > 0 ? `${t('dash.matrix.orders.col.qty')} ${fmtNum(m, 2)}U (${native})` : native;
}
</script>

<template>
  <div class="dsh-card pop-panel flex h-full max-h-[58dvh] flex-col overflow-hidden xl:max-h-none">
    <!-- 面板头部：选项卡与场所过滤条 -->
    <header class="dsh-card-header flex flex-col sm:flex-row sm:items-center justify-between gap-2">
      <div class="flex items-center gap-2">
        <BaseSegmented
          v-model="tab"
          :label="t('dash.matrix.positionsOrders.tabsAria')"
          :options="[
            { value: 'positions', label: `${t('dash.matrix.positions.tab')} ${filteredPositions.length}` },
            { value: 'orders', label: `${t('dash.matrix.orders.tab')} ${filteredOrders.length}` },
          ]"
        />
        <span v-if="tab === 'positions' && !filteredPositions.length" class="text-3xs text-[var(--ink-3)] hidden sm:block">
          {{ t('dash.matrix.positions.aiManaged') }}
        </span>
      </div>

      <!-- 交易所过滤小胶囊 -->
      <div class="seg w-full sm:w-auto" role="tablist" :aria-label="t('dash.matrix.pop.venueLabel')">
        <button
          v-for="(v, vi) in venueTabs"
          :key="v.key"
          :ref="setVenueRef(vi)"
          type="button"
          role="tab"
          :aria-selected="selectedVenue === v.key"
          :tabindex="venueRoving(selectedVenue === v.key)"
          :class="{ 'seg-on': selectedVenue === v.key }"
          @click="selectedVenue = v.key"
          @keydown="onVenueKey($event, vi)"
        >
          {{ v.label }}
        </button>
      </div>
    </header>

    <!-- 持仓列表 -->
    <div v-if="tab === 'positions'" class="scroll-y flex-1 min-h-0 overflow-x-auto">
      <BaseEmpty v-if="!filteredPositions.length" :text="t('dash.matrix.positions.empty')" />
      <table v-else class="table pop-table w-full" :aria-label="t('dash.matrix.positions.title')">
        <thead>
          <tr>
            <th scope="col" class="min-w-[140px]">{{ t('dash.matrix.positions.col.symbol') }}</th>
            <th scope="col" class="col-num min-w-[85px]">{{ t('dash.matrix.positions.col.entry') }} / {{ t('dash.matrix.positions.col.mark') }}</th>
            <th scope="col" class="col-num min-w-[75px]">{{ t('dash.matrix.positions.col.lev') }} / {{ t('dash.matrix.positions.col.margin') }}</th>
            <th scope="col" class="col-num min-w-[85px]">{{ t('dash.matrix.positions.col.pnl') }}</th>
            <th scope="col" class="col-num min-w-[85px]">{{ t('dash.matrix.positions.col.sl') }} / {{ t('dash.matrix.positions.col.tp') }}</th>
            <th scope="col" class="text-center min-w-[50px]">{{ t('dash.matrix.positions.col.oco') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="p in filteredPositions"
            :key="p.instId + p.side"
            class="clickable transition-colors hover:bg-[var(--surface-2)]"
            :title="t('dash.matrix.chart.pickHint')"
            tabindex="0"
            @click="emit('pick-symbol', p.instId)"
            @keydown.enter="emit('pick-symbol', p.instId)"
            @keydown.space.prevent="emit('pick-symbol', p.instId)"
          >
            <td>
              <div class="flex items-center gap-1.5 flex-wrap">
                <CryptoLogo :symbol="symOf(p)" :size="16" />
                <span class="num font-mono font-semibold text-xs text-[var(--ink-strong)]">{{ symOf(p) }}</span>
                <DirTag :dir="p.side" />
                <span
                  class="rounded-full px-1.5 py-0.5 text-3xs font-mono font-semibold uppercase border"
                  :class="venueToneCls(getVenueOf(p))"
                >
                  {{ getVenueOf(p).toUpperCase() }}
                </span>
                <span
                  class="rounded-full px-1.5 py-0.5 text-3xs font-mono font-medium border"
                  :class="getModeOf(p) === 'LIVE' ? 'text-[var(--up)] border-[var(--up-line)] bg-[var(--up-bg)]' : 'text-[var(--warn)] border-[var(--warn-line)] bg-[var(--warn-bg)]'"
                >
                  {{ getModeOf(p) }}
                </span>
                <span
                  v-if="(p.scaleOutPhase ?? 0) >= 1"
                  class="rounded-full px-1.5 py-0.5 text-3xs font-mono font-semibold border text-[var(--up)] border-[var(--up-line)] bg-[var(--up-bg)]"
                  :title="t('dash.matrix.positions.scaleOutTitle')"
                >
                  🎯 {{ t('dash.matrix.positions.scaleOutPill') }}
                </span>
              </div>
              <p
                v-if="p.stageDesc"
                class="text-3xs leading-tight mt-0.5 truncate max-w-[170px]"
                :class="(p.scaleOutPhase ?? 0) >= 1 ? 'text-[var(--up)] font-medium' : 'text-[var(--ink-3)]'"
                :title="p.stageDesc"
              >{{ p.stageDesc }}</p>
            </td>
            <td class="col-num font-mono">
              <span class="block text-xs font-medium text-[var(--ink-strong)]">{{ fmtPrice(p.avgPx) }}</span>
              <span class="block text-3xs text-[var(--ink-3)]">{{ fmtPrice(p.markPx ?? p.last) }}</span>
            </td>
            <td class="col-num font-mono">
              <span class="block text-xs font-bold text-[var(--ink-strong)]">{{ p.lever }}x</span>
              <span class="block text-3xs text-[var(--ink-2)] font-medium">{{ p.margin_usdt ? `${fmtNum(p.margin_usdt, 2)}U` : '--' }}</span>
            </td>
            <td class="col-num font-mono" :class="posPnl(p) >= 0 ? 'up' : 'down'">
              <span class="block text-xs font-semibold">{{ arrow(posPnl(p)) }} {{ fmtSigned(posPnl(p)) }}</span>
              <span class="text-3xs block" :class="posPnl(p) >= 0 ? 'text-[var(--up)]' : 'text-[var(--down)]'">{{ fmtPct(posRoi(p)) }}</span>
            </td>
            <td class="col-num font-mono text-3xs">
              <span class="down block">
                SL {{ fmtPrice(p.exchangeSl ?? p.displayStop) }}
                <span
                  v-if="slTriggerType(p)"
                  class="text-3xs text-[var(--ink-2)]"
                  :title="slTriggerTypeHint(p)"
                >· {{ slTriggerType(p) }}</span>
              </span>
              <span v-if="getTp1(p)" class="up block font-medium" :title="p.stageDesc || t('dash.matrix.positions.scaleOutTitle')">
                TP1 {{ fmtPrice(getTp1(p)) }}
              </span>
              <span class="up block" :class="getTp1(p) ? 'text-[var(--ink-2)]' : ''">
                {{ getTp1(p) ? 'TP2' : 'TP' }} {{ fmtPrice(p.exchangeTp ?? p.displayTakeProfit) }}
              </span>
            </td>
            <td class="text-center">
              <span
                v-if="orphanCandidates(p) > 0"
                class="inline-flex items-center gap-1 text-3xs text-[var(--warn,#f59e0b)]"
                :title="t('dash.matrix.positions.orphanHint')"
              >⚠ {{ t('dash.matrix.positions.orphanPill') }} {{ orphanCandidates(p) }}</span>
              <span
                v-else-if="orphanUnattributed(p) > 0 || orphanReadFailed(p)"
                class="inline-flex items-center gap-1 text-3xs text-[var(--ink-2)]"
                :title="orphanReadFailed(p)
                  ? t('dash.matrix.positions.orphanReadFailHint')
                  : t('dash.matrix.positions.orphanUnknownHint')"
              >{{ t('dash.matrix.positions.orphanUnknownPill') }}</span>
              <span
                v-if="orphanUnclassified(p) > 0"
                class="inline-flex items-center gap-1 text-3xs text-[var(--ink-2)]"
                :title="t('dash.matrix.positions.unclassifiedHint')"
              >{{ t('dash.matrix.positions.unclassifiedPill') }} {{ orphanUnclassified(p) }}</span>
              <span
                v-if="orphanMismatch(p) > 0"
                class="inline-flex items-center gap-1 text-3xs text-[var(--ink-2)]"
                :title="t('dash.matrix.positions.mismatchHint')"
              >{{ t('dash.matrix.positions.mismatchPill') }} {{ orphanMismatch(p) }}</span>
              <span
                v-if="ocoOk(p)"
                class="inline-flex items-center gap-1 text-3xs text-[var(--up)]"
                :title="t('dash.matrix.positions.ocoOk')"
              >
                <ShieldCheck class="h-3.5 w-3.5" />
                <span class="hidden sm:inline">{{ t('dash.matrix.positions.ocoOk') }}</span>
              </span>
              <span
                v-else
                class="inline-flex items-center gap-1 text-3xs text-[var(--warn)]"
                :title="t('dash.matrix.positions.ocoMissHint')"
              >
                <ShieldAlert class="h-3.5 w-3.5" />
                <span class="hidden sm:inline">{{ t('dash.matrix.positions.ocoMiss') }}</span>
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 挂单列表 -->
    <div v-else class="scroll-y flex-1 min-h-0 overflow-x-auto">
      <BaseEmpty v-if="!filteredOrders.length" :text="t('dash.matrix.orders.empty')" />
      <table v-else class="table pop-table w-full" :aria-label="t('dash.matrix.orders.title')">
        <thead>
          <tr>
            <th scope="col" class="min-w-[140px]">{{ t('dash.matrix.orders.col.symbol') }}</th>
            <th scope="col" class="col-num min-w-[85px]">{{ t('dash.matrix.orders.col.price') }}</th>
            <th scope="col" class="col-num min-w-[75px]">{{ t('dash.matrix.orders.col.qty') }} / {{ t('dash.matrix.positions.col.lev') }}</th>
            <th scope="col" class="col-num min-w-[85px]">{{ t('dash.matrix.orders.col.sl') }} / {{ t('dash.matrix.orders.col.tp') }}</th>
            <th scope="col" class="pop-col-time text-right min-w-[80px]">{{ t('dash.matrix.orders.col.placed') }}</th>
            <th scope="col" class="text-center min-w-[60px]">{{ t('dash.matrix.orders.col.state') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="o in filteredOrders"
            :key="o.ordId"
            class="clickable transition-colors hover:bg-[var(--surface-2)]"
            :title="t('dash.matrix.chart.pickHint')"
            tabindex="0"
            @click="emit('pick-symbol', o.instId)"
            @keydown.enter="emit('pick-symbol', o.instId)"
            @keydown.space.prevent="emit('pick-symbol', o.instId)"
          >
            <td>
              <div class="flex items-center gap-1.5 flex-wrap">
                <CryptoLogo :symbol="symOf(o)" :size="16" />
                <span class="num font-mono font-semibold text-xs text-[var(--ink-strong)]">{{ symOf(o) }}</span>
                <DirTag :dir="orderDir(o)" />
                <span
                  class="rounded-full px-1.5 py-0.5 text-3xs font-mono font-semibold uppercase border"
                  :class="venueToneCls(getVenueOf(o))"
                >
                  {{ getVenueOf(o).toUpperCase() }}
                </span>
                <span
                  class="rounded-full px-1.5 py-0.5 text-3xs font-mono font-medium border"
                  :class="getModeOf(o) === 'LIVE' ? 'text-[var(--up)] border-[var(--up-line)] bg-[var(--up-bg)]' : 'text-[var(--warn)] border-[var(--warn-line)] bg-[var(--warn-bg)]'"
                >
                  {{ getModeOf(o) }}
                </span>
              </div>
            </td>
            <td class="col-num font-mono text-xs font-semibold text-[var(--ink-strong)]">
              {{ fmtPrice(o.px) }}
            </td>
            <td class="col-num font-mono">
              <span class="block text-xs font-medium text-[var(--ink-strong)]" :title="orderTooltipText(o)">
                {{ orderMarginText(o) }}
              </span>
              <span class="block text-3xs text-[var(--ink-2)]">{{ o.lever || '3x' }}</span>
            </td>
            <td class="col-num font-mono text-3xs">
              <span class="down block">SL {{ o.slTriggerPx ? fmtPrice(o.slTriggerPx) : (o.sl_px && String(o.sl_px) !== '--' ? fmtPrice(o.sl_px) : '--') }}</span>
              <span class="up block">TP {{ o.tpTriggerPx ? fmtPrice(o.tpTriggerPx) : (o.tp_px && String(o.tp_px) !== '--' ? fmtPrice(o.tp_px) : '--') }}</span>
            </td>
            <td class="pop-col-time text-3xs text-right text-[var(--ink-3)]">
              <TimeAgo :time="Number(o.cTime) || o.cTime" />
            </td>
            <td class="text-center">
              <span class="inline-block whitespace-nowrap rounded px-1.5 py-0.5 text-3xs border border-[var(--line-1)] bg-[var(--surface-2)] text-[var(--ink-2)]">
                {{ o.state === 'live' ? t('status.waiting') : o.state }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="filteredOrders.length" class="text-3xs text-[var(--ink-3)] border-t px-3.5 py-2" style="border-color: var(--line-1)">
        {{ t('dash.matrix.orders.aiManaged') }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.pop-panel {
  container-type: inline-size;
}

.pop-table {
  table-layout: auto;
}

.pop-table th,
.pop-table td {
  padding-left: var(--sp-3);
  padding-right: var(--sp-3);
}

@container (max-width: 480px) {
  .pop-col-time {
    display: none;
  }
  .pop-table th,
  .pop-table td {
    padding-left: var(--sp-2);
    padding-right: var(--sp-2);
  }
}
</style>

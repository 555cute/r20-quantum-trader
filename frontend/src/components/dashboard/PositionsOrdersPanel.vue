<script setup lang="ts">
/** 持仓 ⇄ 挂单 分段面板：行点击联动图表选币；OCO 状态白盒呈现 */
import { computed, ref } from 'vue';
import { useDashboardStore } from '../../stores/dashboard';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtSigned, fmtPct, fmtPrice, arrow } from '../../utils/format';
import BaseSegmented from '../base/BaseSegmented.vue';
import BaseEmpty from '../base/BaseEmpty.vue';
import DirTag from '../base/DirTag.vue';
import TimeAgo from '../base/TimeAgo.vue';

const emit = defineEmits<{ (e: 'pick-symbol', instId: string): void }>();

const store = useDashboardStore();
const { t } = useI18n();

const tab = ref<'positions' | 'orders'>('positions');

type VenueFilter = 'all' | 'okx' | 'binance' | 'gate';
const selectedVenue = ref<VenueFilter>('all');

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
  return 'LIVE';
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
function orderDir(o: any): 'long' | 'short' {
  return String(o.posSide || (o.side === 'buy' ? 'long' : 'short')).toLowerCase() as any;
}
function symOf(x: { instId?: string; name?: string }): string {
  return x.name || String(x.instId || '').split('-')[0];
}
</script>

<template>
  <div class="card flex h-full flex-col overflow-hidden">
    <!-- 面板头：分段 + 药丸筛选 (移动端响应式双行自适应) -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b px-2.5 sm:px-3 py-2 sm:py-2.5" style="border-color: var(--line-1)">
      <div class="flex items-center justify-between sm:justify-start gap-2">
        <BaseSegmented
          v-model="tab"
          :options="[
            { value: 'positions', label: `${t('dash.matrix.positions.tab')} ${filteredPositions.length}` },
            { value: 'orders', label: `${t('dash.matrix.orders.tab')} ${filteredOrders.length}` },
          ]"
        />
        <span v-if="tab === 'positions' && !filteredPositions.length" class="t-faint hidden sm:block text-xs">
          {{ t('dash.matrix.positions.aiManaged') }}
        </span>
      </div>

      <!-- US-007 · 交易所筛选药丸 (移动端全宽平铺，桌面端靠右) -->
      <div class="flex items-center justify-between sm:justify-end gap-1 rounded-md p-0.5 w-full sm:w-auto" style="background-color: var(--surface-2); border: 1px solid var(--line-1)">
        <button
          v-for="v in [
            { key: 'all', label: '全部' },
            { key: 'okx', label: 'OKX' },
            { key: 'binance', label: 'Binance' },
            { key: 'gate', label: 'Gate' },
          ]"
          :key="v.key"
          class="flex-1 sm:flex-initial text-center px-2 py-0.5 rounded text-2xs transition-colors"
          :style="selectedVenue === v.key ? { backgroundColor: 'var(--surface-3)', color: 'var(--ink-strong)', fontWeight: 'bold' } : { color: 'var(--ink-3)' }"
          @click="selectedVenue = v.key as any"
        >
          {{ v.label }}
        </button>
      </div>
    </div>

    <!-- 持仓表 -->
    <div v-if="tab === 'positions'" class="scroll-y flex-1 overflow-x-auto">
      <BaseEmpty v-if="!filteredPositions.length" :text="t('dash.matrix.positions.empty')" />
      <table v-else class="table">
        <thead>
          <tr>
            <th>{{ t('dash.matrix.positions.col.symbol') }}</th>
            <th class="col-num hidden sm:table-cell">{{ t('dash.matrix.positions.col.entry') }}</th>
            <th class="col-num">{{ t('dash.matrix.positions.col.mark') }}</th>
            <th class="col-num hidden md:table-cell">{{ t('dash.matrix.positions.col.lev') }}</th>
            <th class="col-num">{{ t('dash.matrix.positions.col.pnl') }}</th>
            <th class="col-num hidden 2xl:table-cell">{{ t('dash.matrix.positions.col.sl') }} / {{ t('dash.matrix.positions.col.tp') }}</th>
            <th class="text-center">{{ t('dash.matrix.positions.col.oco') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="p in filteredPositions"
            :key="p.instId + p.side"
            class="clickable"
            :title="t('dash.matrix.chart.pickHint')"
            @click="emit('pick-symbol', p.instId)"
          >
            <td>
              <div class="flex items-center gap-1.5 flex-wrap">
                <span class="num font-semibold text-xs sm:text-sm" style="color: var(--ink-strong)">{{ symOf(p) }}</span>
                <DirTag :dir="p.side" />
                <!-- US-007 · 交易所与环境模式标签 -->
                <span
                  class="badge text-3xs font-bold px-1 py-0.2 rounded"
                  :style="getVenueOf(p) === 'binance' ? { color: '#f3ba2f', borderColor: '#f3ba2f33', backgroundColor: '#f3ba2f15' } : getVenueOf(p) === 'gate' ? { color: '#00be98', borderColor: '#00be9833', backgroundColor: '#00be9815' } : { color: '#3880ff', borderColor: '#3880ff33', backgroundColor: '#3880ff15' }"
                >
                  {{ getVenueOf(p).toUpperCase() }}
                </span>
                <span
                  class="badge text-3xs px-1 py-0.2 rounded"
                  :class="getModeOf(p) === 'LIVE' ? 'badge-up' : 'badge-warn'"
                >
                  {{ getModeOf(p) }}
                </span>
              </div>
              <p v-if="p.stageDesc" class="t-faint text-2xs leading-tight mt-0.5">{{ p.stageDesc }}</p>
            </td>
            <td class="col-num hidden sm:table-cell">{{ fmtPrice(p.avgPx) }}</td>
            <td class="col-num">{{ fmtPrice(p.markPx ?? p.last) }}</td>
            <td class="col-num hidden md:table-cell">{{ p.lever }}x</td>
            <td class="col-num" :class="posPnl(p) >= 0 ? 'up' : 'down'">
              {{ arrow(posPnl(p)) }} {{ fmtSigned(posPnl(p)) }}
              <span class="t-faint block text-2xs">{{ fmtPct(posRoi(p)) }}</span>
            </td>
            <td class="col-num t-faint hidden 2xl:table-cell">
              <span class="down">{{ fmtPrice(p.exchangeSl ?? p.displayStop) }}</span>
              <span class="mx-1">/</span>
              <span class="up">{{ fmtPrice(p.exchangeTp ?? p.displayTakeProfit) }}</span>
            </td>
            <td class="text-center">
              <span v-if="ocoOk(p)" class="badge badge-up" :title="t('dash.matrix.positions.ocoOk')">
                <svg viewBox="0 0 24 24" class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/></svg>
                {{ t('dash.matrix.positions.ocoOk') }}
              </span>
              <span v-else class="badge badge-warn" :title="t('dash.matrix.positions.ocoMissHint')">
                {{ t('dash.matrix.positions.ocoMiss') }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 挂单表 -->
    <div v-else class="scroll-y flex-1 overflow-x-auto">
      <BaseEmpty v-if="!filteredOrders.length" :text="t('dash.matrix.orders.empty')" />
      <table v-else class="table">
        <thead>
          <tr>
            <th>{{ t('dash.matrix.orders.col.symbol') }}</th>
            <th class="col-num">{{ t('dash.matrix.orders.col.price') }}</th>
            <th class="col-num">{{ t('dash.matrix.orders.col.qty') }}</th>
            <th class="col-num hidden sm:table-cell">{{ t('dash.matrix.orders.col.sl') }} / {{ t('dash.matrix.orders.col.tp') }}</th>
            <th class="hidden md:table-cell">{{ t('dash.matrix.orders.col.placed') }}</th>
            <th class="text-center">{{ t('dash.matrix.orders.col.state') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="o in filteredOrders"
            :key="o.ordId"
            class="clickable"
            :title="t('dash.matrix.chart.pickHint')"
            @click="emit('pick-symbol', o.instId)"
          >
            <td>
              <div class="flex items-center gap-1.5 flex-wrap">
                <span class="num font-semibold" style="color: var(--ink-strong)">{{ symOf(o) }}</span>
                <DirTag :dir="orderDir(o)" />
                <!-- US-007 · 交易所与环境模式标签 -->
                <span
                  class="badge text-3xs font-bold px-1 py-0.2 rounded"
                  :style="getVenueOf(o) === 'binance' ? { color: '#f3ba2f', borderColor: '#f3ba2f33', backgroundColor: '#f3ba2f15' } : getVenueOf(o) === 'gate' ? { color: '#00be98', borderColor: '#00be9833', backgroundColor: '#00be9815' } : { color: '#3880ff', borderColor: '#3880ff33', backgroundColor: '#3880ff15' }"
                >
                  {{ getVenueOf(o).toUpperCase() }}
                </span>
                <span
                  class="badge text-3xs px-1 py-0.2 rounded"
                  :class="getModeOf(o) === 'LIVE' ? 'badge-up' : 'badge-warn'"
                >
                  {{ getModeOf(o) }}
                </span>
              </div>
            </td>
            <td class="col-num">{{ fmtPrice(o.px) }}</td>
            <td class="col-num">{{ fmtNum(Number(o.sz), 0) }}</td>
            <td class="col-num t-faint hidden sm:table-cell">
              <span class="down">{{ o.slTriggerPx ? fmtPrice(o.slTriggerPx) : '--' }}</span>
              <span class="mx-1">/</span>
              <span class="up">{{ o.tpTriggerPx ? fmtPrice(o.tpTriggerPx) : '--' }}</span>
            </td>
            <td class="hidden md:table-cell"><TimeAgo :time="Number(o.cTime) || o.cTime" /></td>
            <td class="text-center"><span class="badge">{{ o.state === 'live' ? t('status.waiting') : o.state }}</span></td>
          </tr>
        </tbody>
      </table>
      <p v-if="filteredOrders.length" class="t-faint border-t px-3.5 py-2 text-xs" style="border-color: var(--line-1)">
        {{ t('dash.matrix.orders.aiManaged') }}
      </p>
    </div>
  </div>
</template>

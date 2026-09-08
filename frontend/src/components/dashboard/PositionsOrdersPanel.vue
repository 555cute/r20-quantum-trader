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

const positions = computed(() => store.positions);
const orders = computed(() => store.pendingOrders);

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
    <!-- 面板头：分段 + 计数 -->
    <div class="flex items-center gap-2 border-b px-3 py-2.5" style="border-color: var(--line-1)">
      <BaseSegmented
        v-model="tab"
        :options="[
          { value: 'positions', label: `${t('dash.matrix.positions.tab')} ${positions.length}` },
          { value: 'orders', label: `${t('dash.matrix.orders.tab')} ${orders.length}` },
        ]"
      />
      <span v-if="tab === 'positions' && !positions.length" class="t-faint ms-auto hidden text-xs sm:block">
        {{ t('dash.matrix.positions.aiManaged') }}
      </span>
    </div>

    <!-- 持仓表 -->
    <div v-if="tab === 'positions'" class="scroll-y flex-1 overflow-x-auto">
      <BaseEmpty v-if="!positions.length" :text="t('dash.matrix.positions.empty')" />
      <table v-else class="table">
        <thead>
          <tr>
            <th>{{ t('dash.matrix.positions.col.symbol') }}</th>
            <th class="col-num">{{ t('dash.matrix.positions.col.entry') }}</th>
            <th class="col-num">{{ t('dash.matrix.positions.col.mark') }}</th>
            <th class="col-num">{{ t('dash.matrix.positions.col.lev') }}</th>
            <th class="col-num">{{ t('dash.matrix.positions.col.pnl') }}</th>
            <th class="col-num hidden 2xl:table-cell">{{ t('dash.matrix.positions.col.sl') }} / {{ t('dash.matrix.positions.col.tp') }}</th>
            <th class="text-center">{{ t('dash.matrix.positions.col.oco') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="p in positions"
            :key="p.instId + p.side"
            class="clickable"
            :title="t('dash.matrix.chart.pickHint')"
            @click="emit('pick-symbol', p.instId)"
          >
            <td>
              <div class="flex items-center gap-2">
                <span class="num font-semibold" style="color: var(--ink-strong)">{{ symOf(p) }}</span>
                <DirTag :dir="p.side" />
              </div>
              <p v-if="p.stageDesc" class="t-faint text-2xs leading-tight">{{ p.stageDesc }}</p>
            </td>
            <td class="col-num">{{ fmtPrice(p.avgPx) }}</td>
            <td class="col-num">{{ fmtPrice(p.markPx ?? p.last) }}</td>
            <td class="col-num">{{ p.lever }}x</td>
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
      <BaseEmpty v-if="!orders.length" :text="t('dash.matrix.orders.empty')" />
      <table v-else class="table">
        <thead>
          <tr>
            <th>{{ t('dash.matrix.orders.col.symbol') }}</th>
            <th class="col-num">{{ t('dash.matrix.orders.col.price') }}</th>
            <th class="col-num">{{ t('dash.matrix.orders.col.qty') }}</th>
            <th class="col-num">{{ t('dash.matrix.orders.col.sl') }} / {{ t('dash.matrix.orders.col.tp') }}</th>
            <th>{{ t('dash.matrix.orders.col.placed') }}</th>
            <th class="text-center">{{ t('dash.matrix.orders.col.state') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="o in orders"
            :key="o.ordId"
            class="clickable"
            :title="t('dash.matrix.chart.pickHint')"
            @click="emit('pick-symbol', o.instId)"
          >
            <td>
              <div class="flex items-center gap-2">
                <span class="num font-semibold" style="color: var(--ink-strong)">{{ symOf(o) }}</span>
                <DirTag :dir="orderDir(o)" />
              </div>
            </td>
            <td class="col-num">{{ fmtPrice(o.px) }}</td>
            <td class="col-num">{{ fmtNum(Number(o.sz), 0) }}</td>
            <td class="col-num t-faint">
              <span class="down">{{ o.slTriggerPx ? fmtPrice(o.slTriggerPx) : '--' }}</span>
              <span class="mx-1">/</span>
              <span class="up">{{ o.tpTriggerPx ? fmtPrice(o.tpTriggerPx) : '--' }}</span>
            </td>
            <td><TimeAgo :time="Number(o.cTime) || o.cTime" /></td>
            <td class="text-center"><span class="badge">{{ o.state === 'live' ? t('status.waiting') : o.state }}</span></td>
          </tr>
        </tbody>
      </table>
      <p v-if="orders.length" class="t-faint border-t px-3.5 py-2 text-xs" style="border-color: var(--line-1)">
        {{ t('dash.matrix.orders.aiManaged') }}
      </p>
    </div>
  </div>
</template>

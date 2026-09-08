<script setup lang="ts">
/** 决策审计抽屉：宏观研判 / 机会与持仓指令 / 委员会纪要 / 原始记录 */
import { computed, ref } from 'vue';
import BaseDrawer from '../base/BaseDrawer.vue';
import BaseTabs from '../base/BaseTabs.vue';
import BaseCollapse from '../base/BaseCollapse.vue';
import BaseCodeBlock from '../base/BaseCodeBlock.vue';
import BaseEmpty from '../base/BaseEmpty.vue';
import DirTag from '../base/DirTag.vue';
import ConfBadge from '../base/ConfBadge.vue';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtPrice } from '../../utils/format';

const props = defineProps<{ cycle: any | null }>();
const emit = defineEmits<{ (e: 'close'): void }>();

const { t } = useI18n();
const tab = ref('macro');

const c = computed(() => props.cycle || {});
const opps = computed<any[]>(() => c.value.top_opportunities || []);
const posMgmt = computed<any[]>(() => c.value.position_management || []);
const transcript = computed(() => c.value.council_transcript);

const tabs = computed(() => {
  const items = [
    { key: 'macro', label: t('dash.radar.detail.macro') },
    { key: 'quotes', label: t('dash.radar.detail.quotes'), count: opps.value.length + posMgmt.value.length },
  ];
  if (transcript.value) items.push({ key: 'council', label: t('dash.radar.council.title') });
  items.push({ key: 'raw', label: t('dash.radar.detail.raw') });
  return items;
});

function dirOf(a: string): 'long' | 'short' | 'flat' {
  const u = String(a).toUpperCase();
  return u.includes('LONG') ? 'long' : u.includes('SHORT') ? 'short' : 'flat';
}
</script>

<template>
  <BaseDrawer
    :open="!!cycle"
    width="680px"
    :title="t('dash.radar.detail.title', undefined, { t: cycle?.time || '' })"
    :subtitle="cycle?.policy_version || ''"
    @close="emit('close')"
  >
    <BaseTabs v-model="tab" :items="tabs" class="mb-4" />

    <!-- 宏观研判 -->
    <div v-if="tab === 'macro'" class="space-y-3">
      <div class="card-flat p-3.5 text-sm leading-relaxed" style="color: var(--ink-1)">
        {{ c.macro_assessment || '--' }}
        <p v-if="c.ai_last_prompt" class="num t-faint mt-3 border-t pt-2 text-xs" style="border-color: var(--line-1)">
          {{ t('dash.shell.peek.chars', undefined, { n: (c.ai_last_prompt || '').length }) }} · {{ t('dash.shell.peek.title') }}
        </p>
      </div>
    </div>

    <!-- 机会与指令 -->
    <div v-else-if="tab === 'quotes'" class="space-y-4">
      <div v-if="posMgmt.length">
        <p class="t-label mb-1.5">{{ t('dash.radar.detail.verdict') }} · position_management</p>
        <div class="card overflow-hidden">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('dash.matrix.positions.col.symbol') }}</th>
                <th>{{ t('dash.radar.col.action') }}</th>
                <th class="col-num">{{ t('dash.matrix.positions.col.sl') }}</th>
                <th>{{ t('dash.matrix.matrix.reason') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(m, i) in posMgmt" :key="'pm' + i">
                <td class="num font-semibold">{{ String(m.instId || '').split('-')[0] }}</td>
                <td><DirTag :dir="dirOf(m.action)" /></td>
                <td class="col-num t-faint">{{ m.suggested_sl_price ? fmtPrice(m.suggested_sl_price) : '--' }}</td>
                <td class="max-w-[300px] truncate text-xs" style="color: var(--ink-2)" :title="m.reason">{{ m.reason }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="opps.length">
        <p class="t-label mb-1.5">{{ t('dash.radar.detail.quotes') }} · top_opportunities</p>
        <div class="card overflow-hidden">
          <table class="table">
            <thead>
              <tr>
                <th>{{ t('dash.matrix.positions.col.symbol') }}</th>
                <th>{{ t('dash.radar.col.action') }}</th>
                <th class="col-num">{{ t('dash.radar.detail.plan') }}</th>
                <th class="col-num">R:R</th>
                <th>{{ t('dash.matrix.matrix.col.conf') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(o, i) in opps" :key="'op' + i">
                <td class="num font-semibold">{{ String(o.inst || '').split('-')[0] }}</td>
                <td><DirTag :dir="dirOf(o.action)" /></td>
                <td class="col-num t-faint">{{ fmtNum(o.margin_usdt, 0) }} U · {{ o.leverage }}x</td>
                <td class="col-num">{{ o.risk_reward_ratio || '--' }}</td>
                <td><ConfBadge :value="o.confidence" /></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <BaseEmpty v-if="!posMgmt.length && !opps.length" :text="t('dash.radar.detail.waitNote')" />
    </div>

    <!-- 委员会纪要 -->
    <div v-else-if="tab === 'council'" class="space-y-2">
      <pre class="code-block whitespace-pre-wrap">{{ typeof transcript === 'string' ? transcript : JSON.stringify(transcript, null, 2) }}</pre>
    </div>

    <!-- 原始记录 -->
    <div v-else>
      <BaseCollapse :default-open="true">
        <template #head><span class="text-sm">{{ t('dash.radar.detail.raw') }} JSON</span></template>
        <div class="p-2"><BaseCodeBlock :code="JSON.stringify(cycle, null, 2)" max-height="52vh" /></div>
      </BaseCollapse>
    </div>
  </BaseDrawer>
</template>

<script setup lang="ts">
/** AI 推演视图：决策周期时间线（最新在前），行点击 → 审计抽屉 */
import { computed, ref } from 'vue';
import { ChevronDown } from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useI18n } from '../../composables/useI18n';
import { fmtHM } from '../../utils/format';
import PageHead from '../../components/dashboard/PageHead.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';
import DirTag from '../../components/base/DirTag.vue';
import ConfBadge from '../../components/base/ConfBadge.vue';
import RadarDrawer from '../../components/dashboard/RadarDrawer.vue';

const store = useDashboardStore();
const { t } = useI18n();

const history = computed<any[]>(() => {
  const h = (store.data as any)?.ai_brain_history;
  return Array.isArray(h) ? [...h].reverse() : [];
});

const selected = ref<any>(null);

function timeOf(c: any): Date {
  return new Date(String(c.time).replace(' ', 'T'));
}
function hm(c: any) {
  const d = timeOf(c);
  return Number.isNaN(d.getTime()) ? String(c.time || '').slice(11, 16) : fmtHM(d);
}
function dayOf(c: any): string {
  const d = timeOf(c);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  return sameDay ? t('dash.news.feed.grouped.today') : d.toLocaleDateString(undefined, { month: '2-digit', day: '2-digit' });
}
/** 按日期分组，保持组内最新在前 */
const grouped = computed(() => {
  const out: { day: string; items: any[] }[] = [];
  for (const c of history.value) {
    const day = dayOf(c);
    if (!out.length || out[out.length - 1].day !== day) out.push({ day, items: [] });
    out[out.length - 1].items.push(c);
  }
  return out;
});

function actionsOf(c: any): { inst: string; dir: string; conf: number }[] {
  const list: { inst: string; dir: string; conf: number }[] = [];
  for (const m of c.position_management || []) {
    const a = String(m.action || '').toUpperCase();
    if (a && a !== 'WAIT' && a !== 'HOLD') list.push({ inst: String(m.instId || '').split('-')[0], dir: a.includes('LONG') ? 'long' : a.includes('SHORT') ? 'short' : 'flat', conf: Number(m.confidence || 0) });
  }
  for (const o of c.top_opportunities || []) {
    const a = String(o.action || '').toUpperCase();
    if (a === 'BUY_LONG' || a === 'SELL_SHORT') list.push({ inst: String(o.inst || '').split('-')[0], dir: a === 'BUY_LONG' ? 'long' : 'short', conf: Number(o.confidence || 0) });
  }
  return list.slice(0, 4);
}
</script>

<template>
  <div class="space-y-3">
    <PageHead :title="t('dash.radar.title')" :desc="t('dash.radar.desc')">
      <template #actions>
        <span class="badge num">{{ t('dash.radar.cycles', undefined, { n: history.length }) }}</span>
      </template>
    </PageHead>

    <div class="card overflow-hidden">
      <BaseEmpty v-if="!history.length" :text="t('dash.radar.empty')" />
      <div v-else>
        <template v-for="grp in grouped" :key="grp.day">
          <p class="t-label sticky top-0 z-[1] border-b bg-[var(--surface-1)] px-4 py-1.5" style="border-color: var(--line-1)">{{ grp.day }}</p>
          <button
            v-for="c in grp.items"
            :key="c.time"
            class="flex w-full cursor-pointer items-start gap-3 border-b px-4 py-2.5 text-left transition-colors last:border-b-0 hover:bg-[var(--surface-3)]"
            style="border-color: var(--line-1)"
            @click="selected = c"
          >
            <span class="num mt-0.5 w-[74px] shrink-0 text-xs font-semibold" style="color: var(--ink-strong)">{{ hm(c) }}</span>
            <span class="min-w-0 flex-1">
              <span class="block truncate text-sm" style="color: var(--ink-1)">{{ c.macro_assessment || t('dash.radar.empty') }}</span>
              <span class="mt-1 flex flex-wrap items-center gap-1.5">
                <span v-for="a in actionsOf(c)" :key="a.inst + a.dir" class="inline-flex items-center gap-1">
                  <span class="num text-2xs font-bold" style="color: var(--ink-2)">{{ a.inst }}</span>
                  <DirTag :dir="a.dir" />
                  <ConfBadge :value="a.conf" />
                </span>
                <span v-if="!actionsOf(c).length" class="t-faint text-2xs">{{ t('dash.radar.detail.waitNote') }}</span>
              </span>
            </span>
            <span v-if="c.policy_hash" class="badge badge-mono hidden shrink-0 sm:inline-flex" :title="c.policy_version">
              {{ c.policy_hash.slice(0, 8) }}
            </span>
            <ChevronDown class="mt-1 h-4 w-4 shrink-0 -rotate-90" style="color: var(--ink-3)" />
          </button>
        </template>
      </div>
    </div>

    <RadarDrawer :cycle="selected" @close="selected = null" />
  </div>
</template>

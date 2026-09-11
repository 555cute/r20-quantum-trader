<script setup lang="ts">
/**
 * 决策审计与日志审查页（Decisions & Audit Center）
 * 1. AI 决策终审卷宗（标的、方向、置信度、选所路由、风控门禁与宏观研判）
 * 2. 交易巡检 (Trader)、控制面 (Backend)、任务调度 (Scheduler) 三路实时日志流
 */
import { ref, computed, onMounted } from 'vue';
import { useI18n } from '../../composables/useI18n';
import { useApi } from '../../composables/useApi';
import { Terminal, RefreshCw, ScrollText, Search, ShieldCheck } from 'lucide-vue-next';
import PageHeader from '../../components/admin/PageHeader.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';
import { fmtDateTime, fmtNum } from '../../utils/format';
import { venueShortLabel } from '../../utils/venueMeta';

const { t } = useI18n();
const { api } = useApi();

const loading = ref(true);
const decisions = ref<any[]>([]);
const searchQ = ref('');

const activeLogTab = ref<'trader' | 'backend' | 'scheduler'>('trader');
const logContent = ref<string>('');
const logLoading = ref(false);

async function loadDecisions() {
  loading.value = true;
  try {
    const res = await api('/api/v1/admin/runtime');
    decisions.value = res.decisions || [];
    await fetchLogStream(activeLogTab.value);
  } catch (e: any) {
    console.error(e);
  } finally {
    loading.value = false;
  }
}

const filteredDecisions = computed(() => {
  if (!searchQ.value.trim()) return decisions.value;
  const q = searchQ.value.trim().toLowerCase();
  return decisions.value.filter((d) => {
    const sym = String(d.instId || '').toLowerCase();
    const act = String(d.action || '').toLowerCase();
    const sum = String(d.summary || '').toLowerCase();
    return sym.includes(q) || act.includes(q) || sum.includes(q);
  });
});

const ENTRY_START = /^(\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]|(INFO|WARNING|ERROR|CRITICAL|DEBUG)[:\s])/;

function reverseLogEntries(raw: string): string {
  const lines = raw.split('\n');
  const entries: string[][] = [];
  for (const line of lines) {
    if (entries.length === 0 || ENTRY_START.test(line)) {
      entries.push([line]);
    } else {
      entries[entries.length - 1].push(line);
    }
  }
  return entries.reverse().map((e) => e.join('\n')).join('\n');
}

async function fetchLogStream(type: 'trader' | 'backend' | 'scheduler') {
  activeLogTab.value = type;
  logLoading.value = true;
  try {
    const res = await api(`/api/v1/admin/logs?source=${type}&lines=100`);
    const raw: string = res.content || res.lines?.join('\n') || '';
    logContent.value = raw ? reverseLogEntries(raw) : '无实时日志';
  } catch (e: any) {
    logContent.value = `获取日志失败: ${e.message}`;
  } finally {
    logLoading.value = false;
  }
}

function getVenueOfDecision(d: any): string {
  const v = String(d.venue_decision?.target_venue || d.venue || d.exchange || 'okx').toLowerCase();
  if (v.includes('binance')) return 'binance';
  if (v.includes('gate')) return 'gate';
  return 'okx';
}

const brandColors: Record<string, string> = {
  okx: 'var(--venue-okx, #3880ff)',
  binance: 'var(--venue-binance, #f3ba2f)',
  gate: 'var(--venue-gate, #00be98)',
};

onMounted(loadDecisions);
</script>

<template>
  <div class="space-y-4 max-w-[2048px] mx-auto text-xs">
    <PageHeader :title="t('nav.admin.decisions')" description="核对 AI 宏观基调与逐币动作，审查多交易所撮合路由与三路实时运行日志">
      <template #actions>
        <button type="button" class="btn btn-ghost btn-sm cursor-pointer" :disabled="loading" @click="loadDecisions">
          <RefreshCw :class="loading && 'animate-spin'" />{{ t('common.refresh') }}
        </button>
      </template>
    </PageHeader>

    <!-- 1. AI 决策终审卷宗与选所路由结果表 -->
    <div class="card p-4 space-y-3 border" style="background-color: var(--surface-1); border-color: var(--line-1)">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b pb-2" style="border-color: var(--line-1)">
        <div class="flex items-center gap-2">
          <ScrollText class="h-4 w-4 text-[var(--accent)]" />
          <h2 class="text-xs font-bold text-[var(--ink-strong)]">最新 AI 终审决策卷宗与选所路由</h2>
          <span class="badge badge-quiet text-3xs">{{ filteredDecisions.length }} 笔记录</span>
        </div>

        <div class="relative flex items-center">
          <Search class="h-3 w-3 absolute left-2 text-[var(--ink-3)]" />
          <input
            v-model="searchQ"
            type="text"
            placeholder="按币种/动作/研判筛选…"
            class="h-7 w-48 pl-6 pr-2 rounded-md border text-2xs bg-transparent outline-none"
            style="border-color: var(--line-1); color: var(--ink-1)"
          />
        </div>
      </div>

      <BaseEmpty v-if="!filteredDecisions.length" :text="t('common.noData')" />
      <div v-else class="table-scroll-container">
        <table class="table">
          <thead>
            <tr>
              <th>标的代码</th>
              <th>决策动作</th>
              <th class="col-num">置信度</th>
              <th>撮合选所路由</th>
              <th>宏观研判与决策证据</th>
              <th class="col-num">决策时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="d in filteredDecisions" :key="d.instId + d.updated_at" class="hover:bg-[var(--surface-2)] transition-colors">
              <td class="num font-bold text-[var(--ink-strong)]">
                {{ String(d.instId).split('-')[0] }}
              </td>
              <td>
                <span
                  class="dir"
                  :class="d.action === 'BUY_LONG' ? 'dir-long' : d.action === 'SELL_SHORT' ? 'dir-short' : 'dir-flat'"
                >
                  {{ d.action === 'BUY_LONG' ? t('common.dir.long') : d.action === 'SELL_SHORT' ? t('common.dir.short') : d.action }}
                </span>
              </td>
              <td class="col-num font-semibold">{{ fmtNum(d.confidence, 0) }}%</td>
              <td>
                <span
                  class="badge text-3xs font-bold px-1.5 py-0.5 rounded border"
                  :style="{
                    color: brandColors[getVenueOfDecision(d)],
                    borderColor: 'var(--line-2)',
                    backgroundColor: 'var(--surface-2)',
                  }"
                >
                  {{ venueShortLabel(getVenueOfDecision(d)) }}
                </span>
              </td>
              <td class="max-w-[360px] truncate text-2xs text-[var(--ink-2)]" :title="d.summary">
                {{ d.summary }}
              </td>
              <td class="col-num text-2xs text-[var(--ink-3)]">
                {{ fmtDateTime(d.updated_at).slice(11, 19) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 2. 三路实时日志流审查 -->
    <div class="card p-4 space-y-3 border" style="background-color: var(--surface-1); border-color: var(--line-1)">
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b pb-2" style="border-color: var(--line-1)">
        <div class="flex items-center gap-2">
          <Terminal class="h-4 w-4 text-[var(--accent)]" />
          <h2 class="text-xs font-bold text-[var(--ink-strong)]">运行日志审查</h2>
          <span class="badge badge-mono text-3xs">最新在前</span>
        </div>

        <div class="flex items-center gap-1 p-0.5 rounded-lg border text-2xs" style="background-color: var(--surface-2); border-color: var(--line-1)">
          <button
            type="button"
            class="px-2.5 py-1 rounded font-semibold transition-colors cursor-pointer"
            :style="activeLogTab === 'trader' ? { backgroundColor: 'var(--surface-3)', color: 'var(--ink-strong)' } : { color: 'var(--ink-3)' }"
            @click="fetchLogStream('trader')"
          >
            交易巡检 (Trader)
          </button>
          <button
            type="button"
            class="px-2.5 py-1 rounded font-semibold transition-colors cursor-pointer"
            :style="activeLogTab === 'backend' ? { backgroundColor: 'var(--surface-3)', color: 'var(--ink-strong)' } : { color: 'var(--ink-3)' }"
            @click="fetchLogStream('backend')"
          >
            控制面服务 (Backend)
          </button>
          <button
            type="button"
            class="px-2.5 py-1 rounded font-semibold transition-colors cursor-pointer"
            :style="activeLogTab === 'scheduler' ? { backgroundColor: 'var(--surface-3)', color: 'var(--ink-strong)' } : { color: 'var(--ink-3)' }"
            @click="fetchLogStream('scheduler')"
          >
            任务调度器 (Scheduler)
          </button>
        </div>
      </div>

      <div class="relative">
        <div v-if="logLoading" class="absolute inset-0 bg-black/40 backdrop-blur-xs flex items-center justify-center text-xs text-[var(--accent)]">
          <RefreshCw class="w-4 h-4 animate-spin mr-1.5" />
          <span>正在拉取最新日志流…</span>
        </div>
        <pre class="border rounded-lg p-3 text-xs max-h-[460px] overflow-y-auto whitespace-pre-wrap leading-relaxed select-text font-mono" style="background-color: var(--surface-0); border-color: var(--line-1); color: var(--ink-1)">{{ logContent }}</pre>
      </div>
    </div>
  </div>
</template>

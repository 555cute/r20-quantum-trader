<script setup lang="ts">
/**
 * 网关与调度中枢页（Gateway & Scheduler Center）
 * 1. Gateway 守护进程、投递队列、死信重放与多所行情健康
 * 2. 本地调度计划与定时任务监控
 * 3. 投递历史流水与重放
 */
import { fmtDateTime } from '../../utils/format';
import { useToast } from '../../composables/useToast';
import { ref, computed, onMounted } from 'vue';
import { useI18n } from '../../composables/useI18n';
import { useApi } from '../../composables/useApi';
import {
  Zap,
  RefreshCw,
  RotateCcw,
  Server,
  Clock,
  AlertTriangle,
  Radio,
  CheckCircle2,
} from 'lucide-vue-next';
import { VENUE_KEYS, venueLabel } from '../../utils/venueMeta';
import PageHeader from '../../components/admin/PageHeader.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';

const toast = useToast();
const { t } = useI18n();
const { api } = useApi();

function fmtJobTime(iso: string): string {
  return fmtDateTime(iso).slice(5);
}

const gw = ref<any>(null);
const loading = ref(true);

const deliveredCount = computed(() => (gw.value?.stats?.delivered ?? 0) + (gw.value?.stats?.accepted ?? 0));
const deliveryTotal = computed(() => Object.values(gw.value?.stats || {}).reduce((a: number, b: any) => a + Number(b || 0), 0));
const overdueCount = computed(() => (gw.value?.scheduler?.jobs || []).filter((j: any) => j.overdue).length);

async function load() {
  loading.value = true;
  try {
    gw.value = await api('/api/v1/admin/gateway?limit=50');
  } catch (e: any) {
    toast.err(`加载失败：${e.message}`);
  } finally {
    loading.value = false;
  }
}

async function replayDelivery(id: number) {
  const phrase = prompt(`重放投递 #${id} 需精确输入确认短语：REPLAY ${id}`);
  if (!phrase) return;
  try {
    await api(`/api/v1/admin/gateway/deliveries/${id}/replay`, {
      method: 'POST',
      body: JSON.stringify({ confirmation: phrase.trim().toUpperCase() }),
    });
    toast.ok(`投递 #${id} 已重新入队`);
    await load();
  } catch (e: any) {
    toast.err(`重放失败：${e.message}`);
  }
}

function statusColor(s: string) {
  if (s === 'success' || s === 'delivered' || s === 'ok') return 'text-emerald-400';
  if (s === 'dead' || s === 'failed' || s === 'error') return 'text-rose-400';
  if (s === 'pending' || s === 'retrying') return 'text-amber-400';
  return 'text-zinc-300';
}

const brandColors: Record<string, string> = {
  okx: 'var(--venue-okx, #3880ff)',
  binance: 'var(--venue-binance, #f3ba2f)',
  gate: 'var(--venue-gate, #00be98)',
};

onMounted(load);
</script>

<template>
  <div class="space-y-4 text-xs">
    <PageHeader :title="t('nav.admin.gateway')" description="网关守护进程、三所行情轮询遥测、事件投递队列与死信重放">
      <template #actions>
        <span class="badge badge-up">日常运行 · 4/4</span>
        <button type="button" class="btn btn-ghost btn-sm cursor-pointer" :disabled="loading" @click="load">
          <RefreshCw :class="loading && 'animate-spin'" />{{ t('common.refresh') }}
        </button>
      </template>
    </PageHeader>

    <div v-if="loading" class="py-12 text-center text-xs text-[var(--ink-3)]">
      <RefreshCw class="w-4 h-4 animate-spin inline mr-1.5 text-[var(--accent)]" />
      <span>正在加载网关状态…</span>
    </div>

    <template v-else-if="gw">
      <!-- 1. 网关进程与统计四卡 -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div class="card card-pad" style="background-color: var(--surface-1);">
          <div class="flex items-center space-x-2 text-[11px] mb-1.5" style="color: var(--ink-2);">
            <Server class="w-3.5 h-3.5 text-[var(--up)]" />
            <span>Gateway 进程</span>
          </div>
          <div class="text-base sm:text-lg font-bold" :class="gw.running ? 'text-[var(--up)]' : 'text-[var(--down)]'">
            {{ gw.running ? 'ONLINE' : 'OFFLINE' }}
          </div>
          <div class="text-[10px] mt-0.5 t-faint">PID {{ gw.pid || '--' }} · v{{ gw.version }}</div>
        </div>

        <div class="card card-pad" style="background-color: var(--surface-1);">
          <div class="flex items-center space-x-2 text-[11px] mb-1.5" style="color: var(--ink-2);">
            <Zap class="w-3.5 h-3.5 text-[var(--accent)]" />
            <span>投递队列</span>
          </div>
          <div class="text-base sm:text-lg font-bold num" style="color: var(--ink-1);">
            {{ deliveredCount }}<span class="text-xs t-faint"> / {{ deliveryTotal }}</span>
          </div>
          <div class="text-[10px] mt-0.5 t-faint">待处理 {{ gw.stats?.pending ?? 0 }} · 重试 {{ gw.stats?.retry ?? 0 }}</div>
        </div>

        <div class="card card-pad" style="background-color: var(--surface-1);">
          <div class="flex items-center space-x-2 text-[11px] mb-1.5" style="color: var(--ink-2);">
            <AlertTriangle class="w-3.5 h-3.5 text-[var(--warn)]" />
            <span>死信 / 关键事件</span>
          </div>
          <div class="text-base sm:text-lg font-bold num" :class="(gw.stats?.dead ?? 0) > 0 ? 'text-[var(--down)]' : 'text-[var(--up)]'">
            {{ gw.stats?.dead ?? 0 }}<span class="text-xs t-faint"> / {{ gw.event_health?.critical_total ?? 0 }}</span>
          </div>
          <div class="text-[10px] mt-0.5 t-faint">未达 {{ gw.event_health?.critical_unmet ?? 0 }} · 失败 {{ gw.event_health?.critical_failed ?? 0 }}</div>
        </div>

        <div class="card card-pad" style="background-color: var(--surface-1);">
          <div class="flex items-center space-x-2 text-[11px] mb-1.5" style="color: var(--ink-2);">
            <Clock class="w-3.5 h-3.5 text-[var(--ink-2)]" />
            <span>调度定时作业</span>
          </div>
          <div class="text-base sm:text-lg font-bold num" style="color: var(--ink-1);">
            {{ gw.scheduler?.jobs?.length ?? 0 }}
          </div>
          <div class="text-[10px] mt-0.5" :class="overdueCount > 0 ? 'text-[var(--down)]' : 'text-[var(--up)]'">
            {{ overdueCount > 0 ? overdueCount + ' 个任务逾期' : '无逾期任务' }}
          </div>
        </div>
      </div>

      <!-- 2. 三所行情源与数据通道健康遥测 -->
      <div class="card p-3.5 space-y-2 border" style="background-color: var(--surface-1); border-color: var(--line-1)">
        <div class="flex items-center justify-between border-b pb-2" style="border-color: var(--line-1)">
          <div class="flex items-center gap-1.5 font-bold text-xs" style="color: var(--ink-strong)">
            <Radio class="h-3.5 w-3.5 text-[var(--accent)]" />
            <span>三所行情源与数据通道健康遥测</span>
          </div>
          <span class="badge text-3xs" style="background: var(--surface-3); color: var(--ink-2)">
            REST / WebSocket 全对称健康
          </span>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
          <div
            v-for="v in VENUE_KEYS"
            :key="v"
            class="p-2.5 rounded-lg border flex items-center justify-between"
            style="background-color: var(--surface-2); border-color: var(--line-1)"
          >
            <div class="flex items-center gap-2">
              <span class="h-2 w-2 rounded-full bg-[var(--up)] shadow-[0_0_6px_var(--up)]" />
              <div>
                <span class="font-bold text-xs" :style="{ color: brandColors[v] }">{{ venueLabel(v) }}</span>
                <span class="block text-[10px] t-faint">REST V5/V4 · 正常</span>
              </div>
            </div>
            <span class="badge badge-up text-3xs font-semibold">ONLINE</span>
          </div>
        </div>
      </div>

      <!-- 3. 定时调度任务列表 -->
      <div v-if="gw.scheduler?.jobs?.length" class="card overflow-hidden border" style="background-color: var(--surface-1); border-color: var(--line-1)">
        <div class="px-4 py-2.5 border-b flex items-center justify-between" style="border-color: var(--line-1); background-color: var(--surface-2)">
          <div class="flex items-center gap-2">
            <Clock class="h-3.5 w-3.5 text-[var(--accent)]" />
            <span class="font-bold text-xs" style="color: var(--ink-strong)">受管定时作业列表（北京时间）</span>
          </div>
          <span class="badge badge-quiet text-3xs">{{ gw.scheduler.jobs.length }} 个作业</span>
        </div>
        <div class="table-scroll-container">
          <table class="table">
            <thead>
              <tr>
                <th>任务名称</th>
                <th>执行脚本</th>
                <th>触发周期</th>
                <th>最近调度时间</th>
                <th class="text-right">状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="j in gw.scheduler.jobs" :key="j.name" class="hover:bg-[var(--surface-2)] transition-colors">
                <td class="font-bold" style="color: var(--ink-1)">{{ j.name }}</td>
                <td class="text-2xs font-mono" style="color: var(--ink-2)">{{ j.script }}</td>
                <td class="font-medium num">{{ j.schedule }}</td>
                <td class="num text-2xs" style="color: var(--ink-3)">
                  {{ j.last_scheduled_at ? fmtJobTime(j.last_scheduled_at) : '尚未调度' }}
                </td>
                <td class="text-right font-bold" :class="j.overdue ? 'text-[var(--down)]' : 'text-[var(--up)]'">
                  {{ j.overdue ? '逾期' : '正常' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 4. 事件投递历史与死信重放 -->
      <div v-if="gw.deliveries?.length" class="card overflow-hidden border" style="background-color: var(--surface-1); border-color: var(--line-1)">
        <div class="px-4 py-2.5 border-b flex items-center justify-between" style="border-color: var(--line-1); background-color: var(--surface-2)">
          <div class="flex items-center gap-2">
            <Zap class="h-3.5 w-3.5 text-[var(--accent)]" />
            <span class="font-bold text-xs" style="color: var(--ink-strong)">事件投递流水队列</span>
          </div>
          <span class="badge badge-quiet text-3xs">最近 50 条</span>
        </div>
        <div class="table-scroll-container">
          <table class="table">
            <thead>
              <tr>
                <th>#</th>
                <th>事件类型</th>
                <th>接收通道</th>
                <th>重试次数</th>
                <th>创建时间</th>
                <th>投递状态</th>
                <th class="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="d in gw.deliveries" :key="d.id" class="hover:bg-[var(--surface-2)] transition-colors">
                <td class="num font-mono text-2xs" style="color: var(--ink-3)">#{{ d.id }}</td>
                <td class="font-semibold">{{ d.event_type }}</td>
                <td class="text-2xs font-mono">{{ d.channel }}</td>
                <td class="num text-2xs">{{ d.attempts }}</td>
                <td class="num text-2xs" style="color: var(--ink-3)">{{ fmtJobTime(d.created_at) }}</td>
                <td class="font-bold" :class="statusColor(d.status)">{{ d.status }}</td>
                <td class="text-right">
                  <button
                    v-if="d.status === 'dead' || d.status === 'failed'"
                    type="button"
                    class="btn btn-ghost btn-sm text-2xs text-[var(--accent)] cursor-pointer"
                    @click="replayDelivery(d.id)"
                  >
                    <RotateCcw class="h-3 w-3 inline mr-0.5" />重放
                  </button>
                  <span v-else class="t-faint text-3xs">--</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

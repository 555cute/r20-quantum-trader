<script setup lang="ts">
/**
 * US-005 · 账户区「环境优先」面板：先选实盘/模拟，再看三所同构卡。
 * 铁律：两环境数据绝不加总（后端无合计字段，前端也不求和展示）；
 * 未知显「—」+文字状态徽章（颜色不作唯一识别）；身份必须有文字。
 */
import { computed, onMounted } from 'vue';
import { FlaskConical, RefreshCw, ShieldCheck } from 'lucide-vue-next';
import { useI18n } from '../../composables/useI18n';
import { useVenueAccountsStore, type VenueKey } from '../../stores/venueAccounts';
import VenueAccountCard from './VenueAccountCard.vue';

const store = useVenueAccountsStore();
const { t } = useI18n();

const VENUES: VenueKey[] = ['okx', 'gate', 'binance'];
const isDemo = computed(() => store.environment === 'demo');

onMounted(() => {
  void store.refresh();
});
</script>

<template>
  <section class="card card-pad space-y-3" data-test="venue-accounts-panel">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div class="min-w-0">
        <h2 class="text-sm font-bold" style="color: var(--ink-strong)">{{ t('dash.venueAccounts.title') }}</h2>
        <p class="t-faint text-xs">{{ t('dash.venueAccounts.desc') }}</p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <!-- 环境优先切换：文字标签 + 图标，颜色仅作辅助 -->
        <div
          class="flex items-center gap-1 rounded-lg p-1"
          style="background: var(--surface-3); border: 1px solid var(--line-1)"
          role="group"
          :aria-label="t('dash.venueAccounts.envLabel')"
          data-test="env-switch"
        >
          <button
            class="btn btn-sm" :class="isDemo ? 'btn-filled' : 'btn-ghost'"
            :aria-pressed="isDemo" data-test="env-demo" @click="store.setEnvironment('demo')"
          >
            <FlaskConical class="h-3.5 w-3.5" />
            {{ t('dash.venueAccounts.envDemo') }}
          </button>
          <button
            class="btn btn-sm" :class="!isDemo ? 'btn-filled' : 'btn-ghost'"
            :aria-pressed="!isDemo" data-test="env-live" @click="store.setEnvironment('live')"
          >
            <ShieldCheck class="h-3.5 w-3.5" />
            {{ t('dash.venueAccounts.envLive') }}
          </button>
        </div>
        <button
          class="btn btn-ghost btn-icon btn-sm" :title="t('dash.venueAccounts.refresh')"
          data-test="venue-refresh" :disabled="store.loading" @click="store.refresh()"
        >
          <RefreshCw :class="store.loading && 'animate-spin'" />
        </button>
      </div>
    </div>

    <div v-if="store.needsAuth" class="chip w-fit" data-test="needs-auth">
      <span class="dot dot-warn" />
      {{ t('dash.venueAccounts.needsAuth') }}
    </div>

    <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
      <VenueAccountCard
        v-for="v in VENUES"
        :key="`${store.environment}-${v}`"
        :venue="v"
        :account="store.venues?.[v] ?? null"
        :loading="store.loading"
      />
    </div>

    <p class="t-faint flex items-center gap-2 text-[11px]">
      <span>{{ isDemo ? t('dash.venueAccounts.envDemo') : t('dash.venueAccounts.envLive') }}</span>
      <span v-if="store.capturedAt">· {{ t('dash.venueAccounts.captured') }} {{ new Date(store.capturedAt).toLocaleString() }}</span>
      <span v-if="store.error && !store.needsAuth" data-test="fetch-error" style="color: var(--warn)">· {{ store.error }}</span>
    </p>
  </section>
</template>

import { defineStore } from 'pinia';
import { ref, watch } from 'vue';

/**
 * US-005 资金环境优先轴：live（实盘）| demo（模拟）。
 * 身份铁律（设计 §3/§8）：environment 是资金环境轴，与本地 execution_mode
 * （observe/dry_run/enabled）两轴独立；两环境数据永不加总混显。
 */
export type VenueEnvironment = 'live' | 'demo';

const ENV_KEY = 'r20_venv';

export const useEnvironmentStore = defineStore('environment', () => {
  let saved: VenueEnvironment = 'demo';
  try {
    const raw = localStorage.getItem(ENV_KEY);
    if (raw === 'live' || raw === 'demo') saved = raw;
  } catch {
    /* 隐私模式等场景静默：仅失去持久化，不炸 UI */
  }
  const environment = ref<VenueEnvironment>(saved);

  function setEnvironment(env: VenueEnvironment) {
    environment.value = env;
  }

  watch(environment, (v) => {
    try {
      localStorage.setItem(ENV_KEY, v);
    } catch {
      /* ignore */
    }
  });

  return { environment, setEnvironment };
});

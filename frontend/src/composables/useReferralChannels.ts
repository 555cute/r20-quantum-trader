/**
 * 注册/返佣通道（2026-09）。
 *
 * ## 为什么要有这个 composable
 *
 * 这三条链接原先**硬编码在 `AboutModal.vue` 里**，与后端 `r20_backend/config.py`
 * 的 `okx_invite_url` / `gate_invite_url` 是两份各说各话的字面量 —— 于是
 * "用环境变量把通道换成自己的"这个能力，**在用户唯一看得见的那一处完全失效**：
 * 后端改了，前端照旧显示旧链接（Binance 更是压根没出现在界面上）。
 *
 * 现在后端 `GET /api/v1/referral-channels`（**公开只读、无需鉴权**）是唯一事实源，
 * 前端只负责渲染。取不到时**退化成"不显示这一块"**，绝不回落到写死的旧链接 ——
 * 那正是本次要消灭的东西。
 *
 * ⚠️ 与订单上的经纪商 `tag` 是两件事：`tag` 随每笔订单发出、负责把成交归属到
 * 经纪商（这才是返佣机制，与用户是否点过这里的链接无关）；本链接是给用户
 * **开户**用的入口（顺带可叠加节点返佣）。
 *
 * 模块级缓存：`AboutModal` 与 `FirstRunGuide` 会同时用到，避免重复请求。
 */
import { ref, type Ref } from 'vue';
import { http } from '../api/http';

export interface ReferralChannel {
  key: string;
  name: string;
  invite_url: string;
  /** 展示用短码（后端从链接里抽；抽不到为空串） */
  code: string;
}

const channels: Ref<ReferralChannel[]> = ref([]);
const loaded = ref(false);
let inflight: Promise<ReferralChannel[]> | null = null;

export function useReferralChannels() {
  async function load(): Promise<ReferralChannel[]> {
    if (loaded.value) return channels.value;
    if (inflight) return inflight;
    inflight = http<{ channels?: ReferralChannel[] }>('/api/v1/referral-channels')
      .then((res) => {
        // 只保留真的有地址的项：后端没配的通道不渲染空卡片
        channels.value = (res?.channels || []).filter((c) => !!c?.invite_url);
        loaded.value = true;
        return channels.value;
      })
      .catch(() => {
        // 取不到就保持空 ⇒ 调用方整块不渲染。**不回落写死链接**。
        loaded.value = true;
        channels.value = [];
        return channels.value;
      })
      .finally(() => {
        inflight = null;
      });
    return inflight;
  }

  return { channels, loaded, load };
}

/**
 * 全局确认服务 —— 替换 window.confirm 与各页散落确认框
 * 用法：
 *   const { ask } = useConfirm();
 *   if (await ask({ title: '撤销挂单', desc: 'BTC 限价多单 @109,200 将被撤销' })) { ... }
 *   危险操作：{ danger: true, confirmPhrase: 'DELETE' } —— 需逐字输入短语才可确认
 * 渲染：App.vue 挂 <ConfirmHost />
 */
import { ref, readonly } from 'vue';

export interface ConfirmOptions {
  title: string;
  desc?: string;
  /** 详情区补充说明（支持简单 HTML 由调用方负责转义） */
  detail?: string;
  okText?: string;
  cancelText?: string;
  danger?: boolean;
  /** 需要逐字输入的确认短语（危险操作） */
  confirmPhrase?: string;
}

interface ConfirmState extends ConfirmOptions {
  open: boolean;
  id: number;
}

const state = ref<ConfirmState>({ open: false, id: 0, title: '' });
let resolver: ((v: boolean) => void) | null = null;

export function useConfirm() {
  function ask(opts: ConfirmOptions): Promise<boolean> {
    // 前一个未结算则视为取消
    resolver?.(false);
    state.value = { ...opts, open: true, id: ++seq };
    return new Promise<boolean>((resolve) => {
      resolver = resolve;
    });
  }
  function settle(v: boolean) {
    if (!state.value.open) return;
    state.value.open = false;
    resolver?.(v);
    resolver = null;
  }
  return { state: readonly(state), ask, settle };
}

let seq = 0;

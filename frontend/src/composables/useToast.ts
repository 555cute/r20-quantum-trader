/**
 * 全局轻提示服务 —— 替换全部手写 bannerMsg/内联横幅
 * 用法：const toast = useToast(); toast.ok('已保存'); toast.err('网络错误', '重试建议文案');
 * 渲染：App.vue 挂 <ToastHost />
 */
import { ref, readonly } from 'vue';

export type ToastKind = 'ok' | 'err' | 'warn' | 'info';

export interface ToastItem {
  id: number;
  kind: ToastKind;
  title: string;
  desc?: string;
  /** 毫秒；0 = 不自动消失 */
  duration: number;
}

const items = ref<ToastItem[]>([]);
let seq = 0;

function push(kind: ToastKind, title: string, desc?: string, duration = 0): number {
  const id = ++seq;
  const ttl = duration || (kind === 'err' ? 6000 : 3200);
  items.value.push({ id, kind, title, desc, duration: ttl });
  // 同屏最多 4 条，超出丢最旧
  if (items.value.length > 4) items.value.splice(0, items.value.length - 4);
  if (ttl > 0) window.setTimeout(() => dismiss(id), ttl);
  return id;
}

function dismiss(id: number) {
  const i = items.value.findIndex((x) => x.id === id);
  if (i >= 0) items.value.splice(i, 1);
}

export function useToast() {
  return {
    items: readonly(items),
    ok: (title: string, desc?: string) => push('ok', title, desc),
    err: (title: string, desc?: string) => push('err', title, desc),
    warn: (title: string, desc?: string) => push('warn', title, desc),
    info: (title: string, desc?: string) => push('info', title, desc),
    dismiss,
  };
}

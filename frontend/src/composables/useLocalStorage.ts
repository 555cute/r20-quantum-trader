/** 持久化 ref —— localStorage 读写封装（JSON 序列化，异常静默降级） */
import { ref, watch, type Ref } from 'vue';

export function useLocalStorage<T>(key: string, initial: T): Ref<T> {
  let value = initial;
  try {
    const raw = localStorage.getItem(key);
    if (raw !== null) value = JSON.parse(raw) as T;
  } catch {
    /* ignore */
  }
  const r = ref(value) as Ref<T>;
  watch(
    r,
    (v) => {
      try {
        localStorage.setItem(key, JSON.stringify(v));
      } catch {
        /* ignore */
      }
    },
    { deep: true },
  );
  return r;
}

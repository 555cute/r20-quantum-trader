/**
 * 全局快捷键 —— 'mod+k' 兼容 Cmd/Ctrl；输入框聚焦时默认不触发（可 allowInInput）
 */
import { onMounted, onUnmounted } from 'vue';

type Handler = (e: KeyboardEvent) => void;
type HotkeyMap = Record<string, { handler: Handler; allowInInput?: boolean; preventDefault?: boolean }>;

function comboOf(e: KeyboardEvent): string {
  const parts: string[] = [];
  if (e.metaKey || e.ctrlKey) parts.push('mod');
  if (e.shiftKey) parts.push('shift');
  if (e.altKey) parts.push('alt');
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key.toLowerCase();
  parts.push(key);
  return parts.join('+');
}

function inEditable(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

export function useHotkeys(map: HotkeyMap) {
  function onKey(e: KeyboardEvent) {
    const combo = comboOf(e);
    const entry = map[combo];
    if (!entry) return;
    if (inEditable(e) && !entry.allowInInput) return;
    if (entry.preventDefault !== false) e.preventDefault();
    entry.handler(e);
  }
  onMounted(() => window.addEventListener('keydown', onKey));
  onUnmounted(() => window.removeEventListener('keydown', onKey));
}

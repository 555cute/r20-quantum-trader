/** 全局 UI 浮层状态：关于弹窗 / 决策透视抽屉 / 命令面板 / 币种聚焦跳转 */
import { ref } from 'vue';

export const aboutOpen = ref(false);
export const peekOpen = ref(false);
export const cmdkOpen = ref(false);
/** ⌘K 选币直达：MatrixView 监听并切换选中标的 */
export const focusSymbol = ref<string | null>(null);

export function useUi() {
  return { aboutOpen, peekOpen, cmdkOpen, focusSymbol };
}

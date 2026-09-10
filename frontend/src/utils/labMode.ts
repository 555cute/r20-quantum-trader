/**
 * Gate lab effective_mode 徽章 —— 单一事实源（admin/SecurityPage 专用）。
 *
 * 后端契约（US-002 双轴，routing_policy.effective_mode）：
 *   {off | dry_run | demo | live}。
 * demo = 交易所沙盒资金环境 + 执行开闸 → **真实发送模拟盘订单**，
 * 依时效审计字面绝不得标/混成 LIVE 实盘；未知值回退 OFF 样式
 * （显示层 fail-safe：绝不把未知状态渲染成更强的执行态）。
 * 原则：文字徽章为主识别，颜色仅辅助——四态颜色互不重复。
 */
export interface LabModeMeta {
  label: string
  color: string
  borderColor: string
  backgroundColor: string
}

const OFF: LabModeMeta = {
  label: 'OFF 停用',
  color: 'var(--ink-3)',
  borderColor: 'var(--line-2)',
  backgroundColor: 'transparent',
}

const META: Record<string, LabModeMeta> = {
  live: {
    label: 'LIVE 实单',
    color: 'var(--up)',
    borderColor: 'var(--up-line)',
    backgroundColor: 'var(--up-bg)',
  },
  demo: {
    label: 'DEMO 模拟盘',
    color: 'var(--down)',
    borderColor: 'var(--down-line)',
    backgroundColor: 'var(--down-bg)',
  },
  dry_run: {
    label: 'DRY 演算',
    color: 'var(--warn)',
    borderColor: 'var(--warn-line)',
    backgroundColor: 'var(--warn-bg)',
  },
  off: OFF,
}

export function labModeMeta(mode: string | null | undefined): LabModeMeta {
  return META[String(mode || '').trim()] ?? OFF
}

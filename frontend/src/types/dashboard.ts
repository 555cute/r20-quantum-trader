export interface AccountSummary {
  total_eq: number
  avail_eq: number
  cash_bal?: number
  upl?: number
  pos_upl_total?: number
  margin_usage_pct?: number
  margin_ratio?: number
  risk_level?: string
  currency?: string
  initial_capital?: number
  cum_net_pnl?: number
  cum_realized_pnl?: number
  cum_roi_pct?: number
  cum_total_fees?: number
}

export interface PositionItem {
  instId: string
  name: string
  side: 'long' | 'short'
  posSide?: string
  pos: string
  lever: string
  margin: string
  margin_usdt?: number
  margin_source?: string
  notional_usdt?: number
  avgPx: string
  last: string
  markPx?: number | string
  upl: string
  uplRatio: string
  roi_pct?: number
  liqPx?: string | number
  bePx?: string | number
  trailingSl?: number
  exchangeSl?: number
  exchangeTp?: number
  displayStop?: number
  displayTakeProfit?: number
  cloud_oco_verified?: boolean
  protectionStatus?: string
  protectionCoveragePct?: number
  slTriggerPx?: number | string
  tpTriggerPx?: number | string
  stageDesc?: string
  strategyTag?: string
  venue?: string
  environment?: string
  account_mode?: string
}

export interface PendingOrderItem {
  ordId: string
  instId: string
  name: string
  inst?: string
  side: 'buy' | 'sell'
  side_raw?: string
  posSide: 'long' | 'short'
  px: string
  sz: string
  state: string
  cTime: string
  time?: string
  sl_px?: number
  tp_px?: number
  tpTriggerPx?: string
  slTriggerPx?: string
  venue?: string
  environment?: string
  account_mode?: string
}

export interface InstrumentFactor {
  instId: string
  name: string
  type: string
  price: number
  chg24h: number
  high24h: number
  low24h: number
  vol24h: number
  rsi: number
  macd_hist: number
  trend_direction: string
  action?: string
  confidence?: number
  leverage?: number
  margin_usdt?: number
  entry_price?: number
  take_profit_price?: number
  stop_loss_price?: number
  risk_reward_ratio?: string
  reason?: string
  fundingRate?: number
  oiUsd?: number
  lsRatio?: number
  market_regime?: string
  c_1h_ret?: number
  atr_pct?: number
  adx_1h?: number
  calculus?: {
    velocity_1h?: number
    accel_1h?: number
    jerk_1h?: number
    impulse_1h?: number
    energy_1h?: number
    action_area_1h?: number
    state_1h?: string
  }
  smart_money?: {
    weighted_long_pct?: number
    net_flow_usdt?: string
    top_win_rate?: string
  }
  decision?: {
    action: 'BUY_LONG' | 'SELL_SHORT' | 'WAIT'
    confidence: number
    leverage: number
    margin_usdt: number
    entry_price: number
    take_profit_price: number
    stop_loss_price: number
    risk_reward_ratio: string
    summary_reason: string
    venue_decision?: VenueDecisionEvidence | null
  }
  thought_process?: {
    market_structure?: string
    calculus_dynamics?: string
    math_prob_rationale?: string
    volume_and_oi?: string
    risk_reward_evaluation?: string
  }
  position?: any
  /**
   * US-004 · 选所决策证据（US-003 路由落盘 → 决策缓存 → /api/all 透传）。
   * 后端未接线/老快照时为 null/undefined——消费端必须优雅降级（缺 ≠ 0）。
   */
  venue_decision?: VenueDecisionEvidence | null
}

/** 被淘汰的候选交易所及淘汰阶段（venue_router._stage_of 口径） */
export interface VenueRejectedRow {
  venue?: string
  stage?: string
  reason?: string
}

/** US-004 · venue_decision 段的线上结构（纯附加字段，逐键可选） */
export interface VenueDecisionEvidence {
  /** 手动选所优先项：'auto' = 评分路由 */
  preferred_venue?: string
  /** 中选交易所；null = 全部候选被硬筛淘汰 */
  venue?: string | null
  /** OK / OK_HYSTERESIS / ALL_REJECTED / NO_CANDIDATES */
  reason_code?: string
  /** 逐所评分/判定明细（人读文本） */
  reasons?: string[]
  /** 被淘汰候选及原因 */
  rejected?: VenueRejectedRow[]
  hysteresis_applied?: boolean
  /** 多所分配切片（分配开关 off 时为 null） */
  allocation?: Array<{ venue?: string; amount_usdt?: number | null }> | null
  decided_utc?: string
  /** selected / rejected / budget_rejected / budget_error */
  outcome?: string
  skip_reason?: string
  executed_venue?: string | null
  budget?: Record<string, unknown> | null
}

/**
 * US-004 · 账户区组合风险行（/api/all 顶层 portfolio_risk；US-001 预留层口径）。
 * 任一字段 null/缺失 = 未知，前端显「--」，严禁填假 0。
 */
export interface PortfolioRiskRow {
  /** 该数据所属资金环境（如 demo-trading / live）；缺省不展示比对 */
  environment?: string
  total_budget_usdt?: number | null
  reserved_usdt?: number | null
  available_usdt?: number | null
  updated_utc?: string
}

export interface LLMRuntime {
  model: string
  provider_name: string
  reasoning_effort: string
  api_format: string
}

export interface DashboardResponse {
  timestamp: string
  is_stale: boolean
  account: AccountSummary
  positions_summary: {
    total_count: number
    long_count: number
    short_count: number
    items: PositionItem[]
  }
  pending_orders: PendingOrderItem[]
  factors: InstrumentFactor[]
  macro_assessment?: string
  llm_runtime?: LLMRuntime
  logs: string[]
  trades: any[]
  ai_last_prompt?: string
  today_stats?: any
  performance?: any
  news_intelligence?: any[]
  review?: any
  ai_trading_memory_md?: string
  factor_library?: any
  ai_brain_history?: any[]
  data_health?: any
  state_snapshot?: any
  /** US-004 · 组合风险占用（预算/已预留/可用余量；未接入时为缺省） */
  portfolio_risk?: PortfolioRiskRow | null
  [key: string]: any
}

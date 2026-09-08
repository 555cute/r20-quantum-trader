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
  pos: string
  lever: string
  margin: string
  margin_source?: string
  avgPx: string
  last: string
  upl: string
  uplRatio: string
  displayStop?: number
  takeProfitPx?: number
  cloud_oco_verified?: boolean
  protectionStatus?: string
  protectionCoveragePct?: number
  protection_mechanism?: string
  ordType?: string
  quantity_unit?: string
}

export interface PendingOrderItem {
  ordId: string
  instId: string
  name: string
  side: 'buy' | 'sell'
  posSide: 'long' | 'short'
  px: string
  sz: string
  state: string
  cTime: string
  tpTriggerPx?: string
  slTriggerPx?: string
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
  }
  thought_process?: {
    market_structure?: string
    calculus_dynamics?: string
    math_prob_rationale?: string
    volume_and_oi?: string
    risk_reward_evaluation?: string
  }
  position?: any
}

export interface LLMRuntime {
  model: string
  provider_name: string
  reasoning_effort: string
  api_format: string
}

export type TradingMemoryStatus = 'ready' | 'uninitialized' | 'empty' | 'unavailable'

export interface DashboardResponse {
  timestamp: string
  is_stale: boolean
  exchange?: string
  environment?: string
  quantity_unit?: string
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
  ai_trading_memory_status?: TradingMemoryStatus
  factor_library?: any
}

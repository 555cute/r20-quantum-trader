/**
 * US-007 前端配套 · 合约目录对账徽章 —— 单一事实源（venue 账户卡专用）。
 *
 * 后端契约（GET /api/v1/listing_status，r20_backend/exchanges/listing.py）：
 *   {ok, reason, checked_at, source: 'cache'|'fresh'|'unavailable', listed_count}
 * 铁律（与账户卡同源）：
 *   - 未知≠0：listed_count=null 绝不渲染成 0 项；
 *   - 目录拉取失败 = fail-open（ok=true + source='unavailable'）——对账是增强
 *     不是风控闸门，徽章只提示「目录不可用·已跳过」，不得渲染成下单阻断；
 *   - 未知/缺字段 fail-safe 回退未知样式，绝不升级成已对账。
 * 原则：文字徽章为主识别，tone 仅辅助（与 labMode 同构）。
 */
export type ListingTone = 'ok' | 'warn' | 'unknown'

export interface ListingVenueStatus {
  ok: boolean
  reason: string | null
  checked_at: string | null
  source: string
  listed_count: number | null
}

export interface ListingMeta {
  tone: ListingTone
  /** 已知合约数；未知一律 null（渲染层显「—」） */
  listedCount: number | null
  /** 透传后端 reason（fail-open 提示 / 结构性错误说明），无则 null */
  reason: string | null
}

const UNKNOWN: ListingMeta = { tone: 'unknown', listedCount: null, reason: null }

export function listingMeta(s: ListingVenueStatus | null | undefined): ListingMeta {
  if (!s || typeof s !== 'object') return UNKNOWN
  const source = String(s.source ?? '').trim()
  const count = typeof s.listed_count === 'number' && Number.isFinite(s.listed_count)
    ? s.listed_count : null
  if (source === 'unavailable') {
    // fail-open：目录不可用但交易不被阻塞——warn 提示，绝不渲染成「已对账」
    return { tone: 'warn', listedCount: null, reason: s.reason || null }
  }
  if (s.ok === true && (source === 'fresh' || source === 'cache') && count !== null) {
    return { tone: 'ok', listedCount: count, reason: s.reason || null }
  }
  if (s.ok === false) {
    // 结构性错误（如未知环境档）：显式暴露，不装没事
    return { tone: 'warn', listedCount: null, reason: s.reason || null }
  }
  return UNKNOWN
}

/**
 * 通用标的归一化层（三所平权 · 单一事实源）
 *
 * 设计铁律：前端不再假设「全池 = OKX USDT 本位永续」。所有标的以 **canonical
 * 裸币种**（如 `BTC`）为中枢，向各所原生合约码双向映射：
 *
 *   | venue   | 模板               | 示例        |
 *   |---------|--------------------|-------------|
 *   | okx     | `{base}-USDT-SWAP` | BTC-USDT-SWAP |
 *   | binance | `{base}USDT`       | BTCUSDT     |
 *   | gate    | `{base}_USDT`      | BTC_USDT    |
 *
 * 与后端 `r20_backend/exchanges/base.canonical_base` +
 * `registry.native_symbol_pure`（各适配器 `capabilities.symbol_template`）
 * 逐字节对齐：改这里 = 同时改前后端映射口径，勿在组件内再手写后缀。
 *
 * 兼容面：`symOf` / `instIdOf` / `pairLabel` 保留原签名与默认行为
 * （`instIdOf` 默认 OKX 档，因行情端点 `/api/v1/market/{inst_id}` 仍校验
 * `-SWAP` 后缀），新代码请优先用 `canonicalBase` / `nativeSymbol`。
 */
import type { VenueKey } from './venueMeta'
import { VENUE_KEYS, normalizeVenue } from './venueMeta'

/** 全池统一报价币种（USDT 本位） */
export const QUOTE = 'USDT'

/** 各所合约码模板 —— 与后端 capabilities.symbol_template 同源，勿单点修改 */
export const VENUE_SYMBOL_TEMPLATE: Record<VenueKey, string> = {
  okx: `{base}-${QUOTE}-SWAP`,
  binance: `{base}${QUOTE}`,
  gate: `{base}_${QUOTE}`,
}

/** 后端 canonical_base 的剥除顺序（长标记优先，避免 "BTC-USDT-SWAP" 被误剥成 "BTC-USDT"） */
const QUOTE_MARKERS = [`-${QUOTE}-SWAP`, `_${QUOTE}_PERP`, `-${QUOTE}-PERP`, `${QUOTE}`, `_${QUOTE}`, `-${QUOTE}`]

/**
 * 任意写法（BTC / btc / BTC-USDT-SWAP / BTCUSDT / BTC_USDT / BTC/USDT）
 * → 裸币种 "BTC"。镜像后端 canonical_base：未知格式只去分隔符，绝不猜所。
 * 极端输入（裸币种本身即报价币种，如 "USDT"）回退原值，不返回空串。
 */
export function canonicalBase(symbol: unknown): string {
  const s = String(symbol ?? '').trim().toUpperCase()
  if (!s) return ''
  let out = s
  for (const marker of QUOTE_MARKERS) {
    if (out.endsWith(marker) && out.length > marker.length) {
      out = out.slice(0, -marker.length)
      break
    }
  }
  out = out.replace(/[-_/]/g, '').replace(/\.PERP$/, '')
  return out || s
}

/** 裸币种 + 场所 → 该所原生合约码；未知场所回退 canonical（不抛、不猜所，与后端一致） */
export function nativeSymbol(symbol: unknown, venue: VenueKey | string): string {
  const base = canonicalBase(symbol)
  if (!base) return ''
  const key = normalizeVenue(venue)
  if (!key) return base
  return VENUE_SYMBOL_TEMPLATE[key].replace('{base}', base)
}

/**
 * 同一标的在三所的原生合约码矩阵（跨所映射表 / 对账面板直接渲染用）。
 * 三所恒定同构输出，绝不因「只有 OKX 数据」而省略键位。
 */
export function venueSymbolMatrix(symbol: unknown): Record<VenueKey, string> {
  const base = canonicalBase(symbol)
  return {
    okx: nativeSymbol(base, 'okx'),
    binance: nativeSymbol(base, 'binance'),
    gate: nativeSymbol(base, 'gate'),
  }
}

/**
 * 由合约码格式反推所属交易所；无法判定返回 **null**。
 * 铁律：绝不把「未知来源」默认成 OKX（历史单所偏置的根因）。
 */
export function venueOfSymbol(symbol: unknown): VenueKey | null {
  const s = String(symbol ?? '').trim().toUpperCase()
  if (!s) return null
  if (s.endsWith(`-${QUOTE}-SWAP`) || s.endsWith(`-${QUOTE}-PERP`)) return 'okx'
  if (s.endsWith(`_${QUOTE}`) || s.includes(`_${QUOTE}_`)) return 'gate'
  // 币安写法无分隔符：BTCUSDT / 1000PEPEUSDT
  if (!/[-_/.]/.test(s) && s.endsWith(QUOTE) && s.length > QUOTE.length) return 'binance'
  return null
}

/** 解析报价币种（BTC-USDT-SWAP / BTCUSDT / BTC_USDC → USDT / USDC）；未知 null */
export function quoteOf(symbol: unknown): string | null {
  const s = String(symbol ?? '').trim().toUpperCase()
  const m = s.match(/[-_/]([A-Z]{3,5})(?:[-_](?:SWAP|PERP|PERPETUAL))?$/)
  if (m) return m[1]
  const bare = s.match(/([A-Z]{3,5})$/)
  return bare ? bare[1] : null
}

/* ————————————————— 兼容旧调用面 ————————————————— */

/** "BTC-USDT-SWAP" → "BTC"（任意所格式均可正确剥离） */
export function symOf(instId: string | undefined | null): string {
  return canonicalBase(instId)
}

/**
 * "BTC" → 指定所原生合约码（默认 OKX，兼容既有行情端点调用）。
 * 已是完整格式则等价于「先归一再拼回」，输出稳定幂等。
 */
export function instIdOf(sym: string, venue: VenueKey | string = 'okx'): string {
  const raw = String(sym || '').toUpperCase()
  if (!raw) return ''
  return nativeSymbol(raw, venue)
}

/** 展示用交易对名："BTC" / "BTCUSDT" / "BTC_USDT" → "BTC/USDT" */
export function pairLabel(sym: string, quote?: string): string {
  const base = canonicalBase(sym)
  if (!base) return ''
  if (String(sym || '').includes('/')) return String(sym).toUpperCase()
  return `${base}/${quote || quoteOf(sym) || QUOTE}`
}

/** 场所列表（供映射表遍历，顺序恒定） */
export const SYMBOL_VENUE_KEYS: readonly VenueKey[] = VENUE_KEYS

/** instId 格式工具：规范内部 ID 为 BASE-USDT-SWAP（Binance 展示码如 BTCUSDT 不得当作内部 ID）。 */
const SUFFIX = '-USDT-SWAP'

/** "BTC-USDT-SWAP" → "BTC"（非标准格式原样返回） */
export function symOf(instId: string | undefined | null): string {
  if (!instId) return ''
  const up = instId.toUpperCase()
  if (up.endsWith(SUFFIX)) return up.slice(0, -SUFFIX.length)
  if (up.endsWith('-USDT')) return up.slice(0, -'-USDT'.length)
  return up
}

/** "BTC" → "BTC-USDT-SWAP"（已是完整格式则原样返回） */
export function instIdOf(sym: string): string {
  const up = (sym || '').toUpperCase()
  if (!up || up.includes('-')) return up
  return up + SUFFIX
}

/** 展示用交易对名："BTC" → "BTC/USDT" */
export function pairLabel(sym: string): string {
  const base = symOf(sym) || (sym || '').toUpperCase()
  return base.includes('/') ? base : `${base}/USDT`
}

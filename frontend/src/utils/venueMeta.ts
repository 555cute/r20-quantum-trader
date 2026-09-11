/**
 * 多交易所（场所）元数据单一事实源 —— 三所平权的前端数据层基座。
 *
 * 本文件同时承担两个角色，均**不得**在组件内另立口径：
 *   ① 场所注册表：`VenueKey` / `VENUE_KEYS` / 别名归一 / 品牌色 / 徽章渲染
 *      / 资金环境（LIVE·DEMO）助手 —— 供 stores、图表、账户卡、路由页共用；
 *   ② 选所决策证据渲染（决策抽屉 + 账户区组合风险行共用）。
 *
 * 后端契约（scripts/ai_factor_trader.py `_decision_payload`，随
 * data/ai_brain_decisions.json 的 venue_decision 键落盘）：
 *   { preferred_venue, venue, reason_code, reasons[],
 *     rejected: [{venue, stage, reason}], hysteresis_applied, allocation,
 *     decided_utc, outcome?, skip_reason?, budget? }
 * stage 取值对齐 venue_router._stage_of：executable/listing/precision/
 * freshness/budget/unknown。
 *
 * 组合风险行契约（/api/all 顶层 portfolio_risk，US-001 预留层口径）：
 *   { environment?, total_budget_usdt, reserved_usdt, available_usdt, updated_utc? }
 *
 * 铁律（三所平权 + 色盲安全）：
 *   - 缺值/null 一律归一为 null，渲染层显「—」，绝不以 0 冒充未知；
 *   - 徽章主识别是文字（场所名/阶段词/原因码），色点与色调仅作辅助；
 *   - 场所遍历恒定走 `VENUE_KEYS`，任何所都**不是**默认值/兜底值；
 *   - 未知场所原样透传（不吞数据），未知来源返回 null（绝不默认 OKX）。
 */

/* ══════════════════ ① 场所注册表 ══════════════════ */

/** 已注册场所键（与后端 r20_backend/exchanges/registry._ADAPTERS 对齐） */
export type VenueKey = 'okx' | 'binance' | 'gate'

/**
 * 场所遍历唯一顺序（注册表序）。所有列表/网格/分布条一律用它，
 * 组件内不得再手写 `['okx','gate','binance']` 之类的私有顺序。
 */
export const VENUE_KEYS: readonly VenueKey[] = ['okx', 'binance', 'gate']

/** 别名 → 场所键（前端/后端/人工录入的常见写法全部收敛到同一键） */
const VENUE_ALIASES: Record<string, VenueKey> = {
  okx: 'okx', 欧易: 'okx', 'okx.com': 'okx',
  binance: 'binance', 币安: 'binance', bin: 'binance', bn: 'binance', 'binance.com': 'binance',
  gate: 'gate', gateio: 'gate', 'gate.io': 'gate', 芝麻: 'gate', 芝麻开门: 'gate',
}

/** 任意写法 → 场所键；未知返回 null（不猜所、不兜底 OKX） */
export function normalizeVenue(v: unknown): VenueKey | null {
  const raw = String(v ?? '').trim().toLowerCase()
  if (!raw) return null
  return VENUE_ALIASES[raw] || VENUE_ALIASES[raw.replace(/[\s.]/g, '')] || null
}

export function isVenueKey(v: unknown): v is VenueKey {
  return normalizeVenue(v) !== null
}

/** 场所展示名：已知所给统一品牌名，未知场所原样透传（不吞数据） */
const VENUE_NAMES: Record<string, string> = {
  okx: 'OKX',
  binance: 'Binance',
  gate: 'Gate.io',
}

/** 紧凑名（表格/分布条等窄位用） */
const VENUE_SHORT_NAMES: Record<string, string> = {
  okx: 'OKX',
  binance: 'Binance',
  gate: 'Gate',
}

export function venueLabel(v: unknown): string {
  const raw = String(v ?? '').trim()
  if (!raw) return '--'
  const key = normalizeVenue(raw)
  return key ? VENUE_NAMES[key] : raw
}

export function venueShortLabel(v: unknown): string {
  const raw = String(v ?? '').trim()
  if (!raw) return '--'
  const key = normalizeVenue(raw)
  return key ? VENUE_SHORT_NAMES[key] : raw
}

/* 语义色板：只做辅助识别（色盲安全：文字永远在场），沿用既有 --up/--warn/--info */
const VENUE_COLORS: Record<string, string> = {
  okx: 'var(--up)',
  binance: 'var(--warn)',
  gate: 'var(--info)',
}

/**
 * 品牌色（US-001 设计令牌 `--venue-*` 的消费口，带回退值）：
 * 用于 Logo、资产分布条、跨所映射表等「需要区分所」的图形元素。
 * 语义色 `venueColor()` 仍用于状态/涨跌，两套互不替换。
 */
const VENUE_BRAND: Record<VenueKey, { token: string; hex: string }> = {
  okx: { token: 'var(--venue-okx, #3880ff)', hex: '#3880ff' },
  binance: { token: 'var(--venue-binance, #f3ba2f)', hex: '#f3ba2f' },
  gate: { token: 'var(--venue-gate, #00be98)', hex: '#00be98' },
}

/** 语义辅助色（状态点/文字色调） */
export function venueColor(v: unknown): string {
  const key = normalizeVenue(v)
  return key ? VENUE_COLORS[key] : 'var(--ink-3)'
}

/** 品牌色（CSS 变量表达式，US-001 未落令牌时自动回退官方十六进制） */
export function venueBrandColor(v: unknown): string {
  const key = normalizeVenue(v)
  return key ? VENUE_BRAND[key].token : 'var(--ink-3)'
}

/** 品牌色原始十六进制（Canvas/SVG/图表序列等不吃 CSS 变量的场景） */
export function venueBrandHex(v: unknown): string {
  const key = normalizeVenue(v)
  return key ? VENUE_BRAND[key].hex : '#707a8a'
}

/** 统一徽章渲染器：文字主识别 + 品牌辅助色，三所同构输出 */
export interface VenueBadge {
  /** 归一后的场所键；未知为 null（渲染层仍显示原文，绝不隐藏数据） */
  key: VenueKey | null
  /** 全称（OKX / Binance / Gate.io） */
  label: string
  /** 紧凑名（OKX / Binance / Gate） */
  compact: string
  /** 品牌色 CSS 表达式 */
  brand: string
  /** 语义辅助色 CSS 表达式 */
  tone: string
  /** 可直接绑定的 class 前缀（venue-badge--okx 等） */
  cls: string
}

export function venueBadge(v: unknown): VenueBadge {
  const key = normalizeVenue(v)
  return {
    key,
    label: venueLabel(v),
    compact: venueShortLabel(v),
    brand: venueBrandColor(v),
    tone: venueColor(v),
    cls: `venue-badge venue-badge--${key || 'unknown'}`,
  }
}

/**
 * 从任意后端记录推断场所：显式 venue/exchange 字段优先，其次合约码格式反推。
 * 返回 null = 真未知（渲染层显「—」），**不再默认 OKX**。
 */
export function venueOfRecord(
  item: { venue?: unknown; exchange?: unknown; instId?: unknown; inst?: unknown; symbol?: unknown } | null | undefined,
  infer?: (symbol: unknown) => VenueKey | null,
): VenueKey | null {
  if (!item) return null
  const explicit = normalizeVenue(item.venue ?? item.exchange)
  if (explicit) return explicit
  if (typeof infer === 'function') return infer(item.instId ?? item.inst ?? item.symbol ?? '')
  return null
}

/* ———— 资金环境轴（与后端 env_profiles / identity.is_sandbox_environment 对齐） ———— */

export type VenueEnv = 'demo' | 'live'

/** 任意写法 → 环境档；沙盒族（demo/testnet/sandbox/paper）归一为 demo，其余 live */
export function normalizeEnv(v: unknown): VenueEnv {
  const raw = String(v ?? '').trim().toLowerCase()
  if (raw === 'live' || raw === 'prod' || raw === 'production') return 'live'
  if (raw === 'demo' || raw === 'testnet' || raw === 'sandbox' || raw === 'paper' || raw === 'staging') return 'demo'
  // 未知档保守归 demo：UI 绝不把「说不清的环境」标成实盘
  return 'demo'
}

export function isSandboxEnv(v: unknown): boolean {
  return normalizeEnv(v) === 'demo'
}

/** 环境展示文案（zh 默认；en 传 lang='en'） */
export function envLabel(v: unknown, lang: 'zh' | 'en' = 'zh'): string {
  const env = normalizeEnv(v)
  if (lang === 'en') return env === 'live' ? 'LIVE' : 'DEMO'
  return env === 'live' ? '实盘 LIVE' : '模拟盘 DEMO'
}

/** 环境色调：实盘=警示红（真金白银），模拟=安全绿 */
export function envTone(v: unknown): string {
  return normalizeEnv(v) === 'live' ? 'var(--down)' : 'var(--up)'
}

/* ══════════════════ ② 选所决策证据渲染 ══════════════════ */

/** 有限数值才是已知；null/undefined/''/NaN/Infinity → null（未知） */
export function numOrNull(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** 淘汰阶段 → 中文阶段名（对齐 venue_router._stage_of；未知阶段原样透传） */
export function stageLabel(stage: unknown): string {
  const s = String(stage ?? '').trim();
  if (!s) return '--';
  const map: Record<string, string> = {
    executable: '执行开闸',
    listing: '合约目录',
    precision: '精度/最小量',
    freshness: '行情新鲜度',
    budget: '预算',
    unknown: '未分类',
  };
  return map[s.toLowerCase()] || s;
}

/**
 * 选所结果徽章色调：绿=中选 / 红=全拒 / 黄=预算闸拒 / 灰=其余。
 * 文字徽章（reason_code + 中选所名）始终是主识别，这里只定辅助色。
 */
export function decisionBadgeCls(raw: { reason_code?: unknown; outcome?: unknown } | null | undefined): string {
  const code = String(raw?.reason_code ?? '').trim().toUpperCase();
  const outcome = String(raw?.outcome ?? '').trim().toLowerCase();
  if (outcome === 'selected' && (code === 'OK' || code === 'OK_HYSTERESIS')) return 'badge badge-up';
  if (outcome.startsWith('budget')) return 'badge badge-warn';
  if (code === 'ALL_REJECTED' || code === 'NO_CANDIDATES' || outcome === 'rejected') return 'badge badge-down';
  return 'badge';
}

/** 对象判定：非 null、非数组的真对象（后端字段缺失/畸形时优雅降级用） */
export function isPlainObj(v: unknown): v is Record<string, any> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

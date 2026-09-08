/**
 * 数字/时间/价格格式化 —— 全站唯一实现
 * 原则：金融数字一律 tabular-nums；涨跌必带符号与方向箭头（不依赖颜色）。
 */

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '--';
  return Number(v).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** 带符号数字（盈亏类）：+15.85 / -3.20 */
export function fmtSigned(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '--';
  const n = Number(v);
  const s = fmtNum(Math.abs(n), digits);
  if (n > 0) return `+${s}`;
  if (n < 0) return `-${s}`;
  return s;
}

export function fmtUsdt(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '--';
  return `${fmtSigned(v, digits)} USDT`;
}

export function fmtPct(v: number | null | undefined, digits = 2, signed = true): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '--';
  const n = Number(v);
  return `${signed && n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

/** 价格自适应精度：大数 2 位、小币 4~6 位 */
export function fmtPrice(v: number | string | null | undefined): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  const abs = Math.abs(n);
  const digits = abs >= 1000 ? 2 : abs >= 10 ? 3 : abs >= 1 ? 4 : abs >= 0.01 ? 5 : 6;
  return fmtNum(n, digits);
}

/** 大数缩写：1.2M / 345K */
export function fmtCompact(v: number | null | undefined, digits = 1): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return '--';
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${(n / 1e9).toFixed(digits)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(digits)}M`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(digits)}K`;
  return fmtNum(n, abs >= 100 ? 0 : 2);
}

/** 涨跌方向语义类（配合 .up/.down 与 ▲▼） */
export function dirClass(v: number | null | undefined): string {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return '';
  return n > 0 ? 'up' : 'down';
}

export function arrow(v: number | null | undefined): string {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return '—';
  return n > 0 ? '▲' : '▼';
}

/* —— 时间 —— */

function pad(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

export function fmtClock(d: Date): string {
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function fmtDateTime(input: Date | string | number | null | undefined): string {
  if (input === null || input === undefined || input === '') return '--';
  const d = input instanceof Date ? input : new Date(input);
  if (Number.isNaN(d.getTime())) return String(input);
  return `${fmtDate(d)} ${fmtClock(d)}`;
}

export function fmtHM(input: Date | string | number | null | undefined): string {
  if (input === null || input === undefined || input === '') return '--';
  const d = input instanceof Date ? input : new Date(input);
  if (Number.isNaN(d.getTime())) return String(input);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 置信度 → 档位（未校准概率不展示裸数字，悬停/详情给原值） */
export function confTier(v: number | null | undefined): { tier: 'high' | 'mid' | 'low'; label: string } | null {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return null;
  if (n >= 80) return { tier: 'high', label: '高' };
  if (n >= 65) return { tier: 'mid', label: '中' };
  return { tier: 'low', label: '低' };
}

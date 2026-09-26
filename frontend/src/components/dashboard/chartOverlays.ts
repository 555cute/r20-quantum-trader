/**
 * K 线工位的价格线 Overlay 描述符（结构优化阶段 3·F4 第二轮抽离）。
 *
 * ## 为什么能抽
 *
 * 这四块原先内嵌在 `ChartWorkstation.vue` 的 `updatePriceLines()`（92 行）里。
 * 它们**不读组件状态**：把入场/止损/止盈价、方向、文案、文字色作为入参传进来，
 * 只返回"该建哪条线"的**纯描述对象**。真正调用图表库（`createOverlay` /
 * `removeOverlay`）的仍然留在组件里 —— 也就是"算什么"与"怎么画"分开。
 *
 * 与同目录的 `chartMath.ts` / `chartStyles.ts` 同属"阶段 3·F4 抽离"系列。
 *
 * ## 一处刻意的入参：`textColor`
 *
 * 原实现里文字色取 `tok('--ink-1')`（读 CSS 变量）。把 `tok` 抽进来会让本模块
 * 依赖 `window`/`document`，不再可独立检查。故改为**调用方解析后传入** ——
 * 组件仍在同一处调用 `tok`，取值与时机都没变。
 *
 * ## 行为等价的要点
 *
 * - 三条线的 `name` 都是 `'priceLine'`、`paneId` 都是 `'candle_pane'`；
 * - 只有入场线是 `solid`（且颜色随多空变），止损/止盈都是 `dashed` + `dashedValue:[6,4]`；
 * - 止损恒 `#F43F5E`、止盈恒 `#10B981`，**不随多空反转**；
 * - 文案里的百分比固定 `toFixed(1)`。
 *
 * ## 分批止盈（2026-09）
 *
 * 止盈从"一条线"改成"一档一条"：分批建仓下首批 TP1 与终点 TP2 是两个价位，
 * 只画终点等于图上没有首批目标。档位由 `deriveLiveTakeProfits()` 给出（纯函数，
 * 含图签后缀与百分比），本模块只把每档变成描述符 —— 仍是"算什么 / 怎么画"分离。
 */

import type { TpLevel } from './chartLiveLevels'

/** 一条价格线的建线描述；字段名与图表库 `createOverlay` 的入参一致。 */
export interface PriceLineOverlay {
  name: 'priceLine'
  paneId: 'candle_pane'
  points: Array<{ value: number }>
  styles: {
    line: {
      style: 'solid' | 'dashed'
      dashedValue?: number[]
      size: number
      color: string
    }
    text: {
      size: number
      color: string
      backgroundColor: string
    }
  }
  extendData: string
}

const LONG_COLOR = '#10B981'
const SHORT_COLOR = '#F43F5E'
const LINE_SIZE = 1.5
const TEXT_SIZE = 11

export interface EntryOverlayInput {
  price: number
  isLong: boolean
  /** 解析后的文字色（组件传 `tok('--ink-1')`） */
  textColor: string
  /** 界面语言是否英文 */
  isEn: boolean
}

/** 入场价线：实线，颜色随多空。 */
export function buildEntryOverlay(input: EntryOverlayInput): PriceLineOverlay {
  const { price, isLong, textColor, isEn } = input
  const color = isLong ? LONG_COLOR : SHORT_COLOR
  return {
    name: 'priceLine',
    paneId: 'candle_pane',
    points: [{ value: price }],
    styles: {
      line: { style: 'solid', size: LINE_SIZE, color },
      text: { size: TEXT_SIZE, color: textColor, backgroundColor: color },
    },
    extendData: isLong
      ? (isEn ? 'Entry Long' : '多头入场')
      : (isEn ? 'Entry Short' : '空头入场'),
  }
}

export interface SlOverlayInput {
  price: number
  /** `computeRiskReward(...).riskPct` */
  riskPct: number
  textColor: string
  isEn: boolean
  /** 是否为移动保本/锁利状态（SL 已优于 Entry） */
  isLockProfit?: boolean
  /** 相对开仓成本的真实百分比变动（含正负） */
  slDiffPct?: number
}

/** 止损价线：虚线，锁利为绿色，风险为警戒红色。 */
export function buildSlOverlay(input: SlOverlayInput): PriceLineOverlay {
  const { price, riskPct, textColor, isEn, isLockProfit, slDiffPct } = input
  const isLock = !!isLockProfit

  let badgeText = ''
  if (isLock) {
    const diff = Math.abs(slDiffPct ?? riskPct)
    if (diff >= 0.1) {
      badgeText = isEn ? `Lock +${diff.toFixed(1)}%` : `锁利 +${diff.toFixed(1)}%`
    } else {
      badgeText = isEn ? 'Breakeven' : '保本'
    }
  } else {
    const diff = Math.abs(slDiffPct ?? riskPct)
    badgeText = `▼ ${isEn ? 'SL' : '止损SL'} -${diff.toFixed(1)}%`
  }

  return {
    name: 'priceLine',
    paneId: 'candle_pane',
    points: [{ value: price }],
    styles: {
      line: {
        style: 'dashed',
        dashedValue: [6, 4],
        size: LINE_SIZE,
        color: SHORT_COLOR,
      },
      text: { size: TEXT_SIZE, color: textColor, backgroundColor: SHORT_COLOR },
    },
    extendData: badgeText,
  }
}

export interface TpOverlayInput {
  price: number
  /** 相对开仓成本的百分比距离（`TpLevel.pct`） */
  pct: number
  /** 图签后缀：`TP`（未分批）/ `TP1` / `TP2`（分批挂单） */
  label: 'TP' | 'TP1' | 'TP2'
  textColor: string
  isEn: boolean
}

/** 止盈价线：虚线，固定 reward 色。分批止盈时同一形态复制多档，仅图签后缀不同。 */
export function buildTpOverlay(input: TpOverlayInput): PriceLineOverlay {
  const { price, pct, label, textColor, isEn } = input
  return {
    name: 'priceLine',
    paneId: 'candle_pane',
    points: [{ value: price }],
    styles: {
      line: {
        style: 'dashed',
        dashedValue: [6, 4],
        size: LINE_SIZE,
        color: LONG_COLOR,
      },
      text: { size: TEXT_SIZE, color: textColor, backgroundColor: LONG_COLOR },
    },
    extendData: `▲ ${isEn ? label : `止盈${label}`} +${pct.toFixed(1)}%`,
  }
}

/**
 * 决定本次要画哪几条线 —— 这是原 `if (hasPosOrOrder && entryPx > 0)` 等三条守卫的
 * **纯判定版**：返回 `{entry?, sl?, tps}`，缺省表示"不画这条"。
 *
 * 守卫口径与原实现逐条一致：
 * - 入场线：`hasPosOrOrder && entryPx > 0`
 * - 止损线：`slPx > 0`（**不要求** hasPosOrOrder）
 * - 止盈线：逐档 `price > 0`（同上）；分批建仓有两档（TP1/TP2），单档时只有一条
 */
export function planPriceLines(input: {
  entryPx: number
  slPx: number
  /** 全部止盈档位（`deriveLiveTakeProfits` 的结果；空数组 = 未设止盈） */
  tpLevels: TpLevel[]
  hasPosOrOrder: boolean
  isLong: boolean
  riskPct: number
  textColor: string
  isEn: boolean
  isLockProfit?: boolean
  slDiffPct?: number
}): { entry?: PriceLineOverlay; sl?: PriceLineOverlay; tps: PriceLineOverlay[] } {
  const { entryPx, slPx, tpLevels, hasPosOrOrder, isLong,
          riskPct, textColor, isEn, isLockProfit, slDiffPct } = input
  const plan: { entry?: PriceLineOverlay; sl?: PriceLineOverlay; tps: PriceLineOverlay[] } = { tps: [] }

  if (hasPosOrOrder && entryPx > 0) {
    plan.entry = buildEntryOverlay({ price: entryPx, isLong, textColor, isEn })
  }
  if (slPx > 0) {
    plan.sl = buildSlOverlay({ price: slPx, riskPct, textColor, isEn, isLockProfit, slDiffPct })
  }
  for (const lv of tpLevels) {
    if (lv.price > 0) {
      plan.tps.push(buildTpOverlay({ price: lv.price, pct: lv.pct, label: lv.label, textColor, isEn }))
    }
  }
  return plan
}

/**
 * 把仓位自己的价格线并进价格轴的可视范围。
 *
 * ## 为什么必须自己并
 *
 * 实测 klinecharts 10.0.3：价格轴范围（内部 `createRangeImp`）**只统计可见蜡烛的
 * `low/high` 与指标值，从头到尾不看 overlay**。于是远离现价的止损/止盈线会被画到
 * 画布外 —— 分批建仓的 TP2 常落在这一档。
 *
 * 真机取证（XRP：开 1.582 / 止损 1.545 / TP1 1.6264 / TP2 1.6708）：轴范围实测
 * `1.4334 ~ 1.6656`，四条 overlay 全都建出来了，但 `TP2` 换算成像素是 **y = −9px**
 * —— 线在，只是被裁在面板上沿之外。读者于是"只看得到一个止盈点"。
 *
 * ## 口径：只扩不缩
 *
 * 返回的范围**一定包含**默认范围，绝不缩小。无有效价位时原样返回默认范围 ——
 * 也就是无仓无单时行为与改前逐字一致。库侧只读 `realFrom` / `realTo` 两个字段
 * 再自行派生其余（`realRange` / `from` / `to` / `display*`），故此处只需给出这两项；
 * 一并回填其余字段只为让返回对象自洽。
 */
export function expandRangeToLevels<T extends { realFrom: number; realTo: number }>(
  defaultRange: T,
  levels: number[],
): T {
  const valid = levels.filter((p) => Number.isFinite(p) && p > 0)
  if (valid.length === 0) return defaultRange

  const lo = Math.min(defaultRange.realFrom, ...valid)
  const hi = Math.max(defaultRange.realTo, ...valid)
  if (lo === defaultRange.realFrom && hi === defaultRange.realTo) return defaultRange

  return { ...defaultRange, realFrom: lo, realTo: hi }
}


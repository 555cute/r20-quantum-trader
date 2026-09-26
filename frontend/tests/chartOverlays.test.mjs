/**
 * `src/components/dashboard/chartOverlays.ts` 行为契约。
 *
 * ## 守什么
 *
 * 1. **分批止盈**：一笔仓位是分两批挂止盈的（首批 50% 打 TP1，剩余打 TP2）。
 *    改前 K 线只画 `deriveLiveTakeProfit()` 那一条终点线，图上看不到 TP1
 *    （用户反馈："只会显示一个止盈点，但平时都是分批挂单"）。`planPriceLines`
 *    现在按档位出线，本文件钉住"档位数 = 线数、图签后缀正确"。
 *
 * 2. **价格轴范围**：klinecharts 10.0.3 的价格轴只统计**可见蜡烛 + 指标**，
 *    从不看 overlay（真机取证：XRP 的 TP2 = 1.6708 落在轴顶 1.6656 之外，
 *    换算像素 y = −9px ⇒ 线建了但看不见）。`expandRangeToLevels` 负责把仓位
 *    价格线并进范围，本文件钉住它**只扩不缩**、无有效价位时原样返回。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  planPriceLines,
  expandRangeToLevels,
  buildTpOverlay,
} from '../src/components/dashboard/chartOverlays.ts';

// ---------- 分批止盈出线 ----------

const base = {
  entryPx: 1.582,
  slPx: 1.545,
  hasPosOrOrder: true,
  isLong: true,
  riskPct: 2.3,
  textColor: '#fff',
  isEn: false,
};

test('分批止盈：两档出两条线，图签分别是 TP1 / TP2', () => {
  const plan = planPriceLines({
    ...base,
    tpLevels: [
      { price: 1.6264, pct: 2.8, label: 'TP1' },
      { price: 1.6708, pct: 5.6, label: 'TP2' },
    ],
  });
  assert.equal(plan.tps.length, 2, '改前恒为 1 条（只画终点），分批目标在图上不存在');
  assert.deepEqual(plan.tps.map((o) => o.points[0].value), [1.6264, 1.6708]);
  assert.deepEqual(plan.tps.map((o) => o.extendData), ['▲ 止盈TP1 +2.8%', '▲ 止盈TP2 +5.6%']);
});

test('单档时图签仍是 TP（不硬凑成 TP1/TP2）', () => {
  const plan = planPriceLines({ ...base, tpLevels: [{ price: 65000, pct: 8.3, label: 'TP' }] });
  assert.equal(plan.tps.length, 1);
  assert.equal(plan.tps[0].extendData, '▲ 止盈TP +8.3%');
  assert.equal(
    planPriceLines({ ...base, tpLevels: [], isEn: true }).tps.length,
    0,
    '未设止盈时不得凭空出线',
  );
});

test('英文图签不带中文前缀', () => {
  const ov = buildTpOverlay({ price: 2, pct: 5, label: 'TP1', textColor: '#fff', isEn: true });
  assert.equal(ov.extendData, '▲ TP1 +5.0%');
});

test('止盈线形态不变：虚线、不随多空反转、恒 reward 色', () => {
  const long = buildTpOverlay({ price: 2, pct: 1, label: 'TP', textColor: '#fff', isEn: false });
  assert.equal(long.styles.line.style, 'dashed');
  assert.deepEqual(long.styles.line.dashedValue, [6, 4]);
  assert.equal(long.styles.line.color, '#10B981');
  assert.equal(long.name, 'priceLine');
  assert.equal(long.paneId, 'candle_pane');
});

test('入场/止损仍按原守卫出线；止盈逐档判 price > 0', () => {
  const plan = planPriceLines({
    ...base,
    entryPx: 0,
    slPx: 0,
    tpLevels: [
      { price: 0, pct: 1, label: 'TP1' },
      { price: 1.6, pct: 2, label: 'TP2' },
    ],
  });
  assert.equal(plan.entry, undefined, 'entryPx > 0 是入场线的硬守卫');
  assert.equal(plan.sl, undefined, 'slPx > 0 是止损线的硬守卫');
  assert.equal(plan.tps.length, 1, 'price = 0 的档位不出线');
});

// ---------- 价格轴范围 ----------

const rng = { from: 1.4334, to: 1.6656, range: 0.2322, realFrom: 1.4334, realTo: 1.6656, realRange: 0.2322 };

test('轴范围：远档止盈把上沿推出去（真机 TP2 被裁在 y=-9px 的那一档）', () => {
  const next = expandRangeToLevels(rng, [1.545, 1.582, 1.6264, 1.6708]);
  assert.equal(next.realFrom, rng.realFrom, '下方没超出的不许动');
  assert.ok(next.realTo >= 1.6708, 'TP2 必须落进范围里，否则线仍画在面板外');
});

test('轴范围：低于现价的止损同样把下沿推出去', () => {
  const next = expandRangeToLevels(rng, [1.30]);
  assert.equal(next.realTo, rng.realTo);
  assert.equal(next.realFrom, 1.30);
});

test('轴范围：只扩不缩 —— 全部价位都在范围内时原样返回同一个对象', () => {
  const next = expandRangeToLevels(rng, [1.5, 1.6]);
  assert.equal(next, rng, '未扩张必须返回同一引用，避免无谓的轴重算');
});

test('轴范围：空/非法价位一律忽略，不得把范围写成 0 或 NaN', () => {
  for (const bad of [[], [0], [-1], [NaN], [Infinity], [0, NaN]]) {
    assert.equal(expandRangeToLevels(rng, bad), rng, `价位表 ${JSON.stringify(bad)} 不应改变范围`);
  }
});

test('轴范围：返回对象仍保留其余字段（库侧还会读 realRange 之外的派生值）', () => {
  const next = expandRangeToLevels(rng, [1.6708]);
  assert.equal(next.range, rng.range);
  assert.equal(typeof next.realRange, 'number');
  assert.equal(next.realFrom, rng.realFrom);
});

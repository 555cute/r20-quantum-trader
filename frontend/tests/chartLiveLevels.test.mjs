/**
 * `src/components/dashboard/chartLiveLevels.ts` 行为契约（结构优化阶段 4·F4）。
 *
 * ## 守什么
 *
 * `ChartWorkstation.vue` 的 `liveEntry` / `liveSide` / `liveStopLoss` / `liveTakeProfit`
 * 四个 computed 决定图表上**止损/止盈线画在哪、方向箭头指向哪、入场价标多少**。
 * 它们原先只能靠肉眼看组件源码来"确认"，现在被抽成纯函数并由本文件钉住。
 *
 * 重点守两条**容易被"顺手优化"改坏**的既有语义：
 *   1. `??` 是空值合并：持仓 `displayStop: 0` 会**截断**回退链 ⇒ 返回 0，
 *      **不会**落到 `exchangeSl`，也**不会**落到挂单 `sl_px`；
 *   2. 入场价用 `||`：`avgPx` 为 `0` / `""` / `"0"` 时都回退到当前价。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  deriveLiveEntry,
  deriveLiveSide,
  deriveLiveStopLoss,
  deriveLiveTakeProfit,
  deriveLiveTakeProfits,
} from '../src/components/dashboard/chartLiveLevels.ts';

// ---------- 入场价 ----------

test('入场价：持仓成本优先，其次挂单价，最后当前价', () => {
  assert.equal(deriveLiveEntry({ position: { avgPx: '60123.5' }, order: { px: '60000' }, price: 59000 }), 60123.5);
  assert.equal(deriveLiveEntry({ position: null, order: { px: 60000 }, price: 59000 }), 60000);
  assert.equal(deriveLiveEntry({ position: null, order: null, price: 59000 }), 59000);
});

test('入场价用 || 而非 ??:假值与缺值一样回退到当前价', () => {
  for (const bad of [0, '', null, undefined]) {
    assert.equal(deriveLiveEntry({ position: { avgPx: bad }, order: null, price: 12345 }), 12345, String(bad));
  }
});

test('入场价：字符串 "0" 是**真值** ⇒ 显示 0，不回退（既有行为，勿"优化"）', () => {
  assert.equal(deriveLiveEntry({ position: { avgPx: '0' }, order: null, price: 12345 }), 0);
  assert.equal(deriveLiveEntry({ position: { avgPx: '0.0' }, order: null, price: 12345 }), 0);
});

test('入场价：持仓在但 avgPx 假值时**不会**退到挂单价（改走当前价）', () => {
  assert.equal(deriveLiveEntry({ position: { avgPx: 0 }, order: { px: 60000 }, price: 59000 }), 59000);
});

// ---------- 方向 ----------

test('方向：持仓 side 只认 short，其余一律 long', () => {
  assert.equal(deriveLiveSide({ position: { side: 'short' }, order: null }), 'short');
  assert.equal(deriveLiveSide({ position: { side: 'SHORT' }, order: null }), 'long', '大写不识别，与既有实现一致');
  assert.equal(deriveLiveSide({ position: { side: 'long' }, order: null }), 'long');
  assert.equal(deriveLiveSide({ position: {}, order: null }), 'long');
});

test('方向：无持仓时看挂单 sell/空 关键字，side 优先于 side_raw', () => {
  assert.equal(deriveLiveSide({ position: null, order: { side: 'SELL' } }), 'short');
  assert.equal(deriveLiveSide({ position: null, order: { side: '开空' } }), 'short');
  assert.equal(deriveLiveSide({ position: null, order: { side_raw: 'sell_limit' } }), 'short');
  assert.equal(deriveLiveSide({ position: null, order: { side: 'buy' } }), 'long');
  assert.equal(deriveLiveSide({ position: null, order: {} }), 'long');
  assert.equal(deriveLiveSide({ position: null, order: null }), 'long');
});

// ---------- 止损 ----------

test('止损：持仓四级回退按顺序命中', () => {
  assert.equal(deriveLiveStopLoss({ position: { displayStop: 58000, exchangeSl: 57000 }, order: null }), 58000);
  assert.equal(deriveLiveStopLoss({ position: { exchangeSl: 57000, slTriggerPx: 56000 }, order: null }), 57000);
  assert.equal(deriveLiveStopLoss({ position: { slTriggerPx: 56000, trailingSl: 55000 }, order: null }), 56000);
  assert.equal(deriveLiveStopLoss({ position: { trailingSl: '55000' }, order: null }), 55000);
});

test('止损：displayStop 为 0 截断**持仓层**回退链（勿"优化"）', () => {
  assert.equal(
    deriveLiveStopLoss({ position: { displayStop: 0, exchangeSl: 57000 }, order: null }),
    0,
    '0 非空 ⇒ 持仓层在此截断，不再取 exchangeSl',
  );
  assert.equal(
    deriveLiveStopLoss({ position: { displayStop: 0, exchangeSl: 57000 }, order: { sl_px: 56000 } }),
    56000,
    '截断只作用于持仓层：该值不 > 0，仍会落到挂单层',
  );
});

test('止损：持仓给不出正数时才看挂单', () => {
  assert.equal(deriveLiveStopLoss({ position: {}, order: { sl_px: 55555 } }), 55555);
  assert.equal(deriveLiveStopLoss({ position: { displayStop: -1 }, order: { sl_px: 55555 } }), 55555,
    '负数不算数（>0 才算），继续回退');
  assert.equal(deriveLiveStopLoss({ position: { displayStop: 0 }, order: null }), 0);
  assert.equal(deriveLiveStopLoss({ position: null, order: { sl_px: null } }), 0);
  assert.equal(deriveLiveStopLoss({ position: null, order: null }), 0);
});

// ---------- 止盈 ----------

test('止盈：持仓三级回退 + 挂单兜底', () => {
  assert.equal(deriveLiveTakeProfit({ position: { displayTakeProfit: 65000, exchangeTp: 64000 }, order: null }), 65000);
  assert.equal(deriveLiveTakeProfit({ position: { exchangeTp: 64000, tpTriggerPx: 63000 }, order: null }), 64000);
  assert.equal(deriveLiveTakeProfit({ position: { tpTriggerPx: '63000' }, order: null }), 63000);
  assert.equal(deriveLiveTakeProfit({ position: {}, order: { tp_px: 61000 } }), 61000);
  assert.equal(deriveLiveTakeProfit({ position: null, order: null }), 0);
});

test('止盈：displayTakeProfit 为 0 同样只截断持仓层', () => {
  assert.equal(
    deriveLiveTakeProfit({ position: { displayTakeProfit: 0, exchangeTp: 64000 }, order: null }),
    0,
    '不再取 exchangeTp',
  );
  assert.equal(
    deriveLiveTakeProfit({ position: { displayTakeProfit: 0, exchangeTp: 64000 }, order: { tp_px: 63000 } }),
    63000,
    '仍会落到挂单层',
  );
});

test('止损/止盈互不影响：只读各自字段', () => {
  const position = { displayStop: 58000, displayTakeProfit: 65000 };
  assert.equal(deriveLiveStopLoss({ position, order: null }), 58000);
  assert.equal(deriveLiveTakeProfit({ position, order: null }), 65000);
});

// ---------- 分批止盈档位 ----------

test('分批止盈：首批 TP1 + 终点 TP2 两档都给出（真机 XRP 数据）', () => {
  // 真机：entry 1.582 · scaleOutTp 1.6264（首批）· exchangeTp 1.6708（终点）
  const levels = deriveLiveTakeProfits({
    position: { avgPx: 1.582, scaleOutTp: 1.6264, exchangeTp: 1.6708 },
    order: null,
    entry: 1.582,
    side: 'long',
  });
  assert.equal(levels.length, 2, '原先只画一条终点线，首批目标在图上不存在');
  assert.deepEqual(levels.map((l) => l.label), ['TP1', 'TP2']);
  assert.deepEqual(levels.map((l) => l.price), [1.6264, 1.6708]);
  // 百分比与 computeRiskReward 同口径：|tp-entry| / entry * 100
  assert.equal(levels[0].pct.toFixed(2), (((1.6264 - 1.582) / 1.582) * 100).toFixed(2));
  assert.equal(levels[1].pct.toFixed(2), (((1.6708 - 1.582) / 1.582) * 100).toFixed(2));
});

test('分批止盈：空头档位按反向距离算，且首批仍在前', () => {
  // 真机：UNI short entry 10.15 · TP1 9.39 · TP2 8.63
  const levels = deriveLiveTakeProfits({
    position: { scaleOutTp: 9.39, exchangeTp: 8.63 },
    order: null,
    entry: 10.15,
    side: 'short',
  });
  assert.deepEqual(levels.map((l) => l.label), ['TP1', 'TP2']);
  assert.ok(levels[0].pct > 0 && levels[1].pct > 0, '百分比恒为正（距离，不带方向符号）');
  assert.ok(levels[0].pct < levels[1].pct, 'TP1 离成本比 TP2 近');
});

test('没有分批计划时退化回单档，图签仍是 TP（不出现 TP1）', () => {
  const levels = deriveLiveTakeProfits({
    position: { exchangeTp: 65000 },
    order: null,
    entry: 60000,
    side: 'long',
  });
  assert.equal(levels.length, 1);
  assert.equal(levels[0].label, 'TP');
});

test('首批与终点同价只留一条，避免两条线叠在一起', () => {
  const levels = deriveLiveTakeProfits({
    position: { scaleOutTp: 65000, exchangeTp: 65000 },
    order: null,
    entry: 60000,
    side: 'long',
  });
  assert.equal(levels.length, 1, '同价不得产生叠线');
  assert.equal(levels[0].label, 'TP');
});

test('挂单兜底也能给出档位；无仓无单则空数组', () => {
  const fromOrder = deriveLiveTakeProfits({
    position: null,
    order: { tp_px: 61000 },
    entry: 60000,
    side: 'long',
  });
  assert.deepEqual(fromOrder.map((l) => l.price), [61000]);
  assert.equal(fromOrder[0].label, 'TP', '挂单层没有分批信息');

  assert.deepEqual(deriveLiveTakeProfits({ position: null, order: null, entry: 60000, side: 'long' }), []);
});

test('终点档与 deriveLiveTakeProfit() 严格同价（不改动既有终点线）', () => {
  for (const position of [
    { displayTakeProfit: 65000, exchangeTp: 64000 },
    { exchangeTp: 64000, tpTriggerPx: 63000 },
    { tpTriggerPx: '63000' },
    // `displayTakeProfit: 0` 按既有语义**截断**回退链（文档「两条容易看错」之一）
    // ⇒ 单值版返回 0，档位表也必须为空（不得绕过截断把 exchangeTp 捡回来）
    { displayTakeProfit: 0, exchangeTp: 64000 },
  ]) {
    const single = deriveLiveTakeProfit({ position, order: null });
    const levels = deriveLiveTakeProfits({ position, order: null, entry: 60000, side: 'long' });
    if (single === 0) {
      assert.deepEqual(levels, [], '单值版为 0 时不得凭空多出一条止盈线');
      continue;
    }
    const finalLabel = levels.find((l) => l.label !== 'TP1');
    assert.equal(finalLabel.price, single, '终点线的价位必须与原实现逐字一致');
  }
});

test('开仓成本为 0 时百分比给 0，不产生 NaN / Infinity', () => {
  const levels = deriveLiveTakeProfits({
    position: { scaleOutTp: 100, exchangeTp: 120 },
    order: null,
    entry: 0,
    side: 'long',
  });
  assert.deepEqual(levels.map((l) => l.pct), [0, 0]);
});

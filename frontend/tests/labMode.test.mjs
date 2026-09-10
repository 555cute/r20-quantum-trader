import assert from 'node:assert/strict';
import { test } from 'node:test';
import { labModeMeta } from '../src/utils/labMode.ts';

// 后端契约：routing_policy.effective_mode() ∈ {off|dry_run|demo|live}（US-002 双轴）。
// 徽章必须四态齐全且语义互不冒充——尤其 demo 不得落入 OFF（误显示）或 LIVE（危险）。
test('lab.mode four states all render distinct text labels', () => {
  assert.equal(labModeMeta('live').label, 'LIVE 实单');
  assert.equal(labModeMeta('demo').label, 'DEMO 模拟盘');
  assert.equal(labModeMeta('dry_run').label, 'DRY 演算');
  assert.equal(labModeMeta('off').label, 'OFF 停用');
  const labels = ['live', 'demo', 'dry_run', 'off'].map(m => labModeMeta(m).label);
  assert.equal(new Set(labels).size, 4, '四态文字徽章互不重复');
  const colors = ['live', 'demo', 'dry_run', 'off'].map(m => labModeMeta(m).color);
  assert.equal(new Set(colors).size, 4, '颜色仅作辅助，四态色互不重复');
});

test('demo is its own axis value: never OFF fallback, never LIVE wording', () => {
  const demo = labModeMeta('demo');
  assert.notEqual(demo.label, labModeMeta('off').label);
  assert.ok(!demo.label.includes('LIVE'), '模拟盘标注绝不得含 LIVE（审计字面）');
  assert.ok(demo.label.includes('模拟'), 'demo 必须显式文字标识模拟盘');
});

test('unknown/empty/null mode fails safe to OFF styling, never upgrades', () => {
  const off = labModeMeta('off');
  for (const junk of ['', null, undefined, '  ', 'weird', 'LIVE']) {
    assert.deepEqual(labModeMeta(junk), off, `未知态 ${String(junk)} 须回退 OFF`);
  }
});

test('labels carry text so color is never the only signal (a11y 铁律)', () => {
  for (const m of ['live', 'demo', 'dry_run', 'off']) {
    assert.ok(labModeMeta(m).label.trim().length >= 3);
    assert.ok(labModeMeta(m).borderColor.startsWith('var('));
  }
});

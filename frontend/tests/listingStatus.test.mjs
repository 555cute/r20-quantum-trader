import assert from 'node:assert/strict';
import { test } from 'node:test';
import { listingMeta } from '../src/utils/listingMeta.ts';

// US-007 前端契约（GET /api/v1/listing_status）：
// 未知≠0、fail-open 只提示不阻塞、未知字段 fail-safe 回退、绝不升级成已对账。
test('fresh/cache with count renders ok with exact count', () => {
  for (const source of ['fresh', 'cache']) {
    const m = listingMeta({ ok: true, reason: null, checked_at: '2026-09-10T00:00:00Z', source, listed_count: 512 });
    assert.equal(m.tone, 'ok');
    assert.equal(m.listedCount, 512);
  }
});

test('unavailable (fail-open) is warn, never fakes a count', () => {
  const m = listingMeta({ ok: true, reason: '行情目录不可用，跳过对账', checked_at: 'x', source: 'unavailable', listed_count: null });
  assert.equal(m.tone, 'warn');
  assert.equal(m.listedCount, null);
  assert.equal(m.reason, '行情目录不可用，跳过对账');
});

test('structural error (ok=false) surfaces reason, never ok styling', () => {
  const m = listingMeta({ ok: false, reason: '未知环境档 okx/paper', checked_at: 'x', source: 'unavailable', listed_count: null });
  assert.equal(m.tone, 'warn');
  assert.equal(m.reason, '未知环境档 okx/paper');
});

test('unknown/junk payload fails safe to unknown, never upgrades to ok', () => {
  const u = listingMeta(null);
  assert.equal(u.tone, 'unknown');
  assert.equal(u.listedCount, null);
  for (const junk of [undefined, null, {}, { ok: true, source: 'weird', listed_count: 3 }, { ok: true, source: 'fresh', listed_count: null }, 'fresh']) {
    assert.equal(listingMeta(junk).tone === 'ok', false, `${JSON.stringify(junk)} 不得渲染成已对账`);
  }
});

test('listed_count=0 is a real count (empty directory), not unknown', () => {
  const m = listingMeta({ ok: true, reason: null, checked_at: 'x', source: 'fresh', listed_count: 0 });
  assert.equal(m.tone, 'ok');
  assert.equal(m.listedCount, 0, '0 是真实目录值，绝不与未知混同');
});

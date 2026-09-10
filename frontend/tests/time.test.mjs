import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fmtDate, fmtClock, fmtHM, fmtDateTime, parseTime, utcStrToBj } from '../src/utils/format.ts';

test('all representations of an instant render Beijing, independent of process/browser timezone', () => {
  const ms = Date.parse('2026-09-09T23:30:13Z');
  for (const input of [ms, String(ms), ms/1000, String(ms/1000), new Date(ms), '2026-09-09T23:30:13Z', '2026-09-10T07:30:13+08:00', '2026-09-09T16:30:13-07:00', '2026-09-10 07:30:13', '2026-09-10T07:30:13', '2026-09-09 23:30:13 UTC']) {
    assert.equal(fmtDateTime(input), '2026-09-10 07:30:13', String(input));
    assert.equal(parseTime(input).getTime(), ms, String(input));
  }
});
test('UTC-specific fields and existing offsets never double-shift', () => {
  assert.equal(utcStrToBj('2026-09-09 23:30:13', true), '2026-09-10 07:30:13');
  for (const input of ['2026-09-09T23:30:13Z', '2026-09-10T07:30:13+08:00']) {
    assert.equal(utcStrToBj(input, true), '2026-09-10 07:30:13');
  }
  assert.equal(utcStrToBj('2026-09-09T23:30:13Z'), '07:30:13');
});
test('Beijing midnight/year boundary, chart ticks and day grouping', () => {
  assert.equal(fmtDate('2026-12-31T16:00:00Z'), '2027-01-01');
  assert.equal(fmtClock('2026-12-31T16:00:00Z'), '00:00:00');
  assert.equal(fmtDate('2026-09-09T15:59:59Z'), '2026-09-09');
  assert.equal(fmtDate('2026-09-09T16:00:00Z'), '2026-09-10');
  assert.equal(fmtHM('2026-09-09T23:30:13Z'), '07:30');
  assert.equal(fmtDateTime('2026-09-10'), '2026-09-10 00:00:00');
});
test('relative age of naive Beijing news is correct, not 8h late/future', () => {
  assert.equal(Date.parse('2026-09-09T23:31:13Z') - parseTime('2026-09-10 07:30:13').getTime(), 60_000);
});
test('invalid/empty inputs do not invent dates', () => {
  for (const input of [null, undefined, '', '--', 'nonsense', '09/10/2026 07:30']) assert.equal(fmtDateTime(input), '--');
  assert.equal(fmtDateTime(0), '1970-01-01 08:00:00');
});

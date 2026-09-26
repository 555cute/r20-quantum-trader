/**
 * 邀请链接与经纪商 code **不得硬编码在前端**（2026-09）。
 *
 * ## 这个门在防什么（真实事故形状）
 *
 * 同一对值（邀请链接 / 经纪商 code）曾同时存在于**四处**：
 *
 * | 位置 | 角色 |
 * |---|---|
 * | `scripts/okx_rest.py` | **权威**：随每笔订单发出的经纪商 tag |
 * | `r20_backend/config.py` | 权威：三条邀请链接（环境变量可覆盖） |
 * | `dashboard/AboutModal.vue` | 副本：用户可见的通道卡（硬编码） |
 * | `views/admin/SecurityPage.vue` | 副本：后台凭证页的 code 与注册按钮（硬编码） |
 *
 * 危害不是"显示旧了"，而是：**分发副本的人用环境变量把自己的通道换上去之后，
 * 前端那几处照旧显示原作者的链接与 code** —— 用户顺着点，就注册到原作者名下，
 * 也就是"钱进别人账上"。后端改了、环境变量覆盖了，界面却纹丝不动，最要命。
 *
 * 现约定：**前端只渲染后端出值**（`/api/v1/referral-channels` 公开版，
 * `/api/v1/admin/referral-channels` 管理员版）。本门把"再写回字面量"按住。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

/** 已出现在生产配置里的三个邀请域名 —— 任何一个都不许出现在前端源码里。 */
const BANNED_DOMAINS = ['mitxcqvwnhj', 'gatesites', 'bsmkweb'];

/**
 * OKX Broker code 的形状：12 位小写十六进制 + 4 位大写字母。
 * 官方文档给的样例 `6099c63a8d75SCDE` 与在用的 `6e2191f027c6SUDE` 都符合；
 * 收紧到这个形状是为了**不必**把具体那串码写进本文件（写进来本身就是又一份副本）。
 */
const BROKER_CODE_SHAPE = /\b[0-9a-f]{12}[A-Z]{4}\b/;

function sources(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) sources(p, out);
    else if (/\.(vue|ts|js)$/.test(name)) out.push(p);
  }
  return out;
}

const stripComments = (t) =>
  t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1').replace(/<!--[\s\S]*?-->/g, '');

test('前端不得硬编码邀请域名', () => {
  const bad = [];
  for (const file of sources(SRC)) {
    const rel = path.relative(SRC, file).replace(/\\/g, '/');
    const text = stripComments(readFileSync(file, 'utf8'));
    for (const domain of BANNED_DOMAINS) {
      if (text.includes(domain)) bad.push(`${rel} :: ${domain}`);
    }
  }
  assert.deepEqual(bad, [],
    '这些地方把邀请链接写死在前端了 —— 后端换通道时它们不会跟着变，用户会注册到别人名下：\n  '
    + bad.join('\n  '));
});

test('前端不得硬编码经纪商 code', () => {
  const bad = [];
  for (const file of sources(SRC)) {
    const rel = path.relative(SRC, file).replace(/\\/g, '/');
    const text = stripComments(readFileSync(file, 'utf8'));
    if (BROKER_CODE_SHAPE.test(text)) {
      bad.push(`${rel} :: ${(text.match(BROKER_CODE_SHAPE) || [])[0]}`);
    }
  }
  assert.deepEqual(bad, [],
    '经纪商 code 必须由后端出值（`/api/v1/admin/referral-channels` 的 broker_code，'
    + '与实发订单上的 tag 同源）。前端写死一份 = 又一个会漂移的副本：\n  ' + bad.join('\n  '));
});

test('判据自检：扫描面非空（否则本门空转变绿）', () => {
  const files = sources(SRC);
  assert.ok(files.length > 50, `只扫到 ${files.length} 个文件 —— 遍历写错了，本门会空转`);
  assert.ok(files.some((f) => f.endsWith('AboutModal.vue')), '至少该扫到 AboutModal.vue');
  assert.ok(files.some((f) => f.endsWith('SecurityPage.vue')), '至少该扫到 SecurityPage.vue');
});

test('判据自检：能真的命中（拿两个样例字面量验证）', () => {
  // 否则正则写错了也会"全绿"，这个门就成了摆设。
  assert.ok(BANNED_DOMAINS.some((d) => `https://www.${d}.example/join/1`.includes(d)));
  assert.ok(BROKER_CODE_SHAPE.test('6e2191f027c6SUDE'), '在用的 code 必须被形状判据认出来');
  assert.ok(BROKER_CODE_SHAPE.test('6099c63a8d75SCDE'), '文档样例必须被认出来');
  assert.ok(!BROKER_CODE_SHAPE.test('48039151'), '纯数字邀请码不该被误判为经纪商 code');
  assert.ok(!BROKER_CODE_SHAPE.test('MCHDBKYF'), 'Gate 邀请码不该被误判为经纪商 code');
});

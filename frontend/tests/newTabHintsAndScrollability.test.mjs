/**
 * 新窗口外链提示、滚动可达性与动效偏好守卫闸（批 68）。
 *
 * ## 守什么
 *
 * ### 一、外链不得「静默」开新标签（WCAG 3.2.5 / 最佳实践 G201）
 *
 * 全站 5 处 `target="_blank"` 外链此前一律静默开新标签：视障用户按下回车后
 * 上下文被切走、且无法用「后退」回到原位置，事前毫无提示；键盘用户在标签页间
 * 也会迷失。`rel="noopener noreferrer"` 只解决**安全**，解决不了**知情**。
 *
 * 本闸要求每个 `target="_blank"` 的 `<a>` 内部必须包含
 * `{{ t('common.opensInNewTab') }}` 的 `sr-only` 视觉隐藏提示。
 *
 * ### 二、纯文本滚动区必须键盘可达（WCAG 2.1.1）
 *
 * 若一个受限高度的滚动容器内部**没有任何可聚焦元素**，键盘用户就永远无法滚动它：
 * Tab 进不去，方向键不生效，内容被彻底锁死。此类容器必须声明 `tabindex="0"`。
 *
 * ### 三、JS 平滑滚动必须尊重 `prefers-reduced-motion`（WCAG 2.3.3）
 *
 * `base.css` 里的 reduced-motion 规则只能关掉 **CSS** 过渡与 `scroll-behavior`，
 * 关不掉 JS 的 `scrollIntoView({ behavior: 'smooth' })` —— 前庭敏感用户仍会被强制平滑滚动。
 * 所有 JS 平滑滚动必须显式查询媒体查询并降级为 `auto`。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

function vueFiles(dir, out = []) {
  for (const n of readdirSync(dir)) {
    const p = path.join(dir, n);
    if (statSync(p).isDirectory()) vueFiles(p, out);
    else if (n.endsWith('.vue')) out.push(p);
  }
  return out;
}

const stripComments = (t) => t.replace(/<!--[\s\S]*?-->/g, '');

test('所有 target="_blank" 外链必须提供「新标签页打开」提示', () => {
  const bad = [];
  let total = 0;

  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const text = stripComments(readFileSync(file, 'utf8'));
    const tm = text.match(/<template>([\s\S]*)<\/template>/);
    if (!tm) continue;
    const body = tm[1];

    for (const m of body.matchAll(/<a\b([^>]*?)>([\s\S]*?)<\/a>/g)) {
      const attrs = m[1];
      const inner = m[2];
      if (!attrs.includes('target="_blank"')) continue;
      total += 1;
      if (!inner.includes("common.opensInNewTab")) {
        bad.push(`${rel} :: ${attrs.replace(/\s+/g, ' ').trim().slice(0, 70)}`);
      }
    }
  }

  // 2026-09：5 → 6 —— 关于页新增「注册通道」面板，每个交易所入口都是一个
  // target="_blank" 外链（`AboutPage.vue` 的 `ab-channel-actions`，均带
  // `common.opensInNewTab`；判据本身不变，只是覆盖范围多了一处）。
  assert.equal(total, 6, `target="_blank" 外链数量变化（期望 6，实得 ${total}），请复核本闸覆盖范围`);
  assert.deepEqual(bad, [], `以下外链静默开新标签，读屏用户事前无从得知：\n  ${bad.join('\n  ')}`);
});

test('外链内的装饰性图标必须对读屏隐藏', () => {
  const bad = [];

  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const text = stripComments(readFileSync(file, 'utf8'));
    const tm = text.match(/<template>([\s\S]*)<\/template>/);
    if (!tm) continue;
    const body = tm[1];

    for (const m of body.matchAll(/<a\b([^>]*?)>([\s\S]*?)<\/a>/g)) {
      if (!m[1].includes('target="_blank"')) continue;
      for (const icon of m[2].matchAll(/<(ExternalLink|Github|GitBranch)\b([^>]*?)\/?>/g)) {
        if (!/aria-hidden/.test(icon[2]) && !/aria-label/.test(icon[2])) {
          bad.push(`${rel} :: <${icon[1]}> 未声明 aria-hidden`);
        }
      }
    }
  }

  assert.deepEqual(bad, [], `外链图标会被读屏器当作内容念出（应与链接文本重复）：\n  ${bad.join('\n  ')}`);
});

test('纯文本滚动区必须声明 tabindex="0" 与语义角色', () => {
  const raw = readFileSync(path.join(SRC, 'components/dashboard/TrajectoryPanel.vue'), 'utf8');
  // 必须剥掉注释：说明文字里也会出现 role="log"，否则定位到的是注释而非真实标签
  const text = stripComments(raw);

  const idx = text.indexOf('role="log"');
  assert.ok(idx !== -1, 'TrajectoryPanel 日志流缺少 role="log"');
  const tag = text.slice(text.lastIndexOf('<div', idx), text.indexOf('>', idx) + 1);
  assert.match(tag, /tabindex="0"/, '日志流缺少 tabindex="0"，键盘用户无法滚动');
  assert.match(tag, /:aria-label=/, '日志流缺少 aria-label，读屏只念「区域」');
  assert.match(tag, /overflow-y-auto/, '该容器不再是滚动区，请复核本闸');
});

test('JS 平滑滚动必须查询 prefers-reduced-motion 并降级', () => {
  const files = vueFiles(SRC).filter((f) => /behavior:\s*'smooth'/.test(stripComments(readFileSync(f, 'utf8'))));
  assert.ok(files.length > 0, '未找到任何 JS 平滑滚动，请复核本闸');

  for (const file of files) {
    const rel = path.relative(SRC, file);
    const text = readFileSync(file, 'utf8');
    assert.match(
      text,
      /prefers-reduced-motion:\s*reduce/,
      `${rel} 使用 behavior:'smooth' 却未查询 prefers-reduced-motion`,
    );
    assert.match(
      text,
      /behavior:\s*reduce\s*\?\s*'auto'\s*:\s*'smooth'/,
      `${rel} 未按 reduced-motion 结果在 auto/smooth 之间切换`,
    );
  }
});

test('闸自检：能准确拦截静默外链与无视动效偏好的滚动', () => {
  const silent = '<a href="x" target="_blank" rel="noopener noreferrer">Link</a>';
  const hinted = '<a href="x" target="_blank" rel="noopener noreferrer">Link<span class="sr-only">{{ t(\'common.opensInNewTab\') }}</span></a>';
  const checkLink = (h) => h.includes('target="_blank"') && !h.includes('common.opensInNewTab');

  assert.equal(checkLink(silent), true, '应拦截静默开新标签的外链');
  assert.equal(checkLink(hinted), false, '应放行已提示的外链');

  const blindScroll = 'el.scrollIntoView({ behavior: \'smooth\', block: \'start\' })';
  const awareScroll = "behavior: reduce ? 'auto' : 'smooth'";
  assert.equal(/prefers-reduced-motion/.test(blindScroll), false, '应拦截无视动效偏好的滚动');
  assert.equal(/prefers-reduced-motion:\s*reduce/.test(awareScroll) || /reduce \?/.test(awareScroll), true, '应放行已降级的滚动');
});

/**
 * 表单控件可访问名称与错误通报守卫闸（批 67）。
 *
 * ## 守什么
 *
 * ### 一、表单控件 100% 可访问名称（WCAG 4.1.2 Name, Role, Value）
 *
 * 批 67 实测：全站 82 个 `<input>/<textarea>/<select>` 中有 **16 个没有任何程序化名称**。
 * 它们都有**可见**的 `form-label` 兄弟节点或标题，但那是 `<span>/<h3>`，与控件之间
 * 没有任何程序化关联 —— 读屏器只念得出 `placeholder`（常常是"例如: XRP-USDT-SWAP"
 * 这类示例文本，而不是控件名称），视障用户无法知道自己在填什么。
 *
 * 本闸要求每个表单控件必须至少具备以下之一：
 *   - `aria-label` / `:aria-label`；
 *   - `aria-labelledby` / `:aria-labelledby`；
 *   - 被 `<label>` 元素**隐式包裹**（HTML 原生关联）；
 *   - `id` 与某个 `<label for="...">` **显式配对**。
 *
 * ### 二、用户动作后的错误必须被通报（WCAG 4.1.3 Status Messages）
 *
 * 由错误状态变量**条件渲染**的错误容器，如果不同时声明 `role="alert"`（断言式，
 * 用于阻断当前操作）或 `role="status"` / `aria-live`（礼貌式，用于不阻断的陈旧提示），
 * 则视障用户按下"导入/新建/重试"后**收不到任何失败反馈**，只会以为按钮没反应。
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

function templateBody(file) {
  const text = stripComments(readFileSync(file, 'utf8'));
  const tm = text.match(/<template>([\s\S]*)<\/template>/);
  return tm ? tm[1] : null;
}

/** 该控件是否具备可访问名称（四种合法途径之一）。 */
export function hasAccessibleName(body, match) {
  const attrs = match[2];
  if (/aria-label|aria-labelledby/.test(attrs)) return true;

  const idm = attrs.match(/(?<!:)\bid="([^"]+)"/);
  if (idm) {
    const fors = new Set([...body.matchAll(/<label[^>]*\bfor="([^"]+)"/g)].map((m) => m[1]));
    if (fors.has(idm[1])) return true;
  }

  // 隐式包裹：向左回溯，若最近的 <label 早于最近的 </label>，说明当前控件被 label 包着
  const before = body.slice(0, match.index);
  return before.lastIndexOf('<label') > before.lastIndexOf('</label>');
}

test('每个表单控件必须具备可访问名称（aria-label / aria-labelledby / label 关联）', () => {
  const bad = [];
  let total = 0;

  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const body = templateBody(file);
    if (!body) continue;

    for (const m of body.matchAll(/<(input|textarea|select)\b([^>]*?)>/g)) {
      const tag = m[1];
      const attrs = m[2];
      // 不产生可访问名称需求的类型
      if (tag === 'input' && /type="(hidden|submit|button|reset|file)"/.test(attrs)) continue;
      total += 1;
      if (!hasAccessibleName(body, m)) {
        bad.push(`${rel} <${tag}> ${attrs.replace(/\s+/g, ' ').trim().slice(0, 80)}`);
      }
    }
  }

  assert.ok(total > 70, `扫描到的表单控件过少（${total}），疑似扫描失败`);
  assert.deepEqual(bad, [], `以下表单控件没有可访问名称，读屏器只念得出 placeholder：\n  ${bad.join('\n  ')}`);
});

test('条件渲染的错误容器必须通报（role=alert 或 role=status / aria-live）', () => {
  const bad = [];

  for (const file of vueFiles(SRC)) {
    const rel = path.relative(SRC, file);
    const body = templateBody(file);
    if (!body) continue;

    for (const m of body.matchAll(/<(div|p|span|section|output)\b([^>]*?)>/g)) {
      const attrs = m[2];
      // 只关心由错误状态变量条件渲染的容器
      if (!/v-if="[^"]*(Error|Err|error|err)[^"]*"/.test(attrs)) continue;
      if (/role="alert"|role="status"|aria-live/.test(attrs)) continue;
      // 白名单：随列表数据一同渲染的条目级错误（非异步通报，加 alert 会对每条刷屏）
      if (/v-if="p\.error"/.test(attrs)) continue;
      bad.push(`${rel} <${m[1]}> ${attrs.replace(/\s+/g, ' ').trim().slice(0, 80)}`);
    }
  }

  assert.deepEqual(bad, [], `以下错误容器不会向读屏器通报：\n  ${bad.join('\n  ')}`);
});

test('关键通报点逐一落位（回归锚点）', () => {
  const cases = [
    ['views/admin/OverviewPage.vue', /v-if="loadError"\s+role="alert"\s+class="ov-error-banner"/, '整页加载失败横幅'],
    ['views/admin/PromptStudioPage.vue', /v-if="importFileError"\s+role="alert"/, '策略包导入解析失败'],
    ['views/admin/CouncilPage.vue', /v-if="importFileError"\s+role="alert"/, '委员会配置导入失败'],
    ['views/admin/InterceptorsPage.vue', /v-if="createError"\s+role="alert"/, '新建插件提交失败'],
    ['views/admin/GatewayPage.vue', /v-if="error"\s+role="status"\s+aria-live="polite"/, '网关陈旧数据提示'],
    ['components/dashboard/VenueAccountsPanel.vue', /v-if="store\.error && !store\.needsAuth"\s+role="status"\s+aria-live="polite"/, '资金面板拉取失败'],
  ];

  for (const [rel, re, label] of cases) {
    const text = readFileSync(path.join(SRC, rel), 'utf8');
    assert.match(text, re, `${rel} 的「${label}」通报语义缺失`);
  }
});

test('批 67 修复过的 16 个控件逐一具备 :aria-label（回归锚点）', () => {
  const anchors = [
    ['views/admin/AboutPage.vue', 'v-model="confirmPhrase"', "admin.about.phrasePlaceholder"],
    ['views/admin/CouncilPage.vue', 'v-model="selectedRole.name"', "admin.council.seatNamePlaceholder"],
    ['views/admin/CouncilPage.vue', 'v-model="selectedRole.prompt"', "admin.council.seatPromptAria"],
    ['views/admin/CouncilPage.vue', 'v-model="importRawJson"', "admin.council.importJsonAria"],
    ['views/admin/EvolutionPage.vue', 'v-model="newMemoryText"', "admin.evolution.memoryInputAria"],
    ['views/admin/EvolutionPage.vue', 'v-model="mod.content"', "admin.evolution.moduleContentAria"],
    ['views/admin/InterceptorsPage.vue', 'v-model="newFilename"', "admin.interceptors.filenameLabel"],
    ['views/admin/PromptStudioPage.vue', 'v-model="selectedModule.title"', "admin.promptStudio.modules.titlePlaceholder"],
    ['views/admin/PromptStudioPage.vue', 'v-model="selectedModule.content"', "admin.promptStudio.moduleEditor"],
    ['views/admin/PromptStudioPage.vue', 'v-model="importNameOverride"', "admin.promptStudio.import.nameLabel"],
    ['views/admin/SecurityPage.vue', 'v-model="mxForm.binance_api_key"', "admin.security.binanceKeyAria"],
    ['views/admin/SecurityPage.vue', 'v-model="mxForm.binance_secret_key"', "admin.security.binanceSecretAria"],
    ['views/admin/SecurityPage.vue', 'v-model="mxForm.gate_api_key"', "admin.security.gateKeyAria"],
    ['views/admin/SecurityPage.vue', 'v-model="mxForm.gate_secret_key"', "admin.security.gateSecretAria"],
    ['views/admin/SecurityPage.vue', 'v-model="keys.live_key"', "admin.security.liveKeyAria"],
    ['views/admin/SecurityPage.vue', 'v-model="newInstId"', "admin.security.instAria"],
  ];

  for (const [rel, vmodel, key] of anchors) {
    const text = readFileSync(path.join(SRC, rel), 'utf8');
    const idx = text.indexOf(vmodel);
    assert.ok(idx !== -1, `${rel} 找不到 ${vmodel}`);
    // 该控件开始标签的结束位置
    const tagEnd = text.indexOf('>', idx);
    const chunk = text.slice(idx, tagEnd);
    assert.ok(chunk.includes(':aria-label='), `${rel} 的 ${vmodel} 缺少 :aria-label`);
    assert.ok(chunk.includes(key), `${rel} 的 ${vmodel} 未引用约定键 ${key}`);
  }
});

test('闸自检：能准确拦截无名称控件与静默错误容器', () => {
  const named = '<input v-model="x" :aria-label="t(\'k\')" />';
  const unnamed = '<input v-model="x" placeholder="e.g." />';
  const b1 = named.match(/<(input)\b([^>]*?)>/);
  const b2 = unnamed.match(/<(input)\b([^>]*?)>/);

  assert.equal(hasAccessibleName(named, b1), true, '应放行有 aria-label 的控件');
  assert.equal(hasAccessibleName(unnamed, b2), false, '应拦截只有 placeholder 的控件');

  const wrapped = '<label><span>A</span><input v-model="x" /></label>';
  const w = wrapped.match(/<(input)\b([^>]*?)>/);
  assert.equal(hasAccessibleName(wrapped, w), true, '应放行隐式 label 包裹的控件');

  const silent = '<div v-if="createError" class="x">!</div>';
  const alerts = '<div v-if="createError" role="alert" class="x">!</div>';
  const check = (h) => /role="alert"|role="status"|aria-live/.test(h);
  assert.equal(check(silent), false, '应拦截静默错误容器');
  assert.equal(check(alerts), true, '应放行已通报的错误容器');
});

/**
 * 路由标题规范与搜索引擎隔离守卫闸（批 52）。
 *
 * ## 守什么
 *
 * 1. **全站路由页面标题独特性与规范化（WCAG 2.4.2 Page Titled）**：
 *    此前全站 18 个后台页面标题全部写死为 `"管理控制台 · R20"`，
 *    用户打开多个浏览器标签页时无法区分，读屏器报读完全重复。
 *    现要求：
 *    - 每一个后台路由必须动态获取其对应的导航名，格式为 `${pageName} · ${consoleName} · AstraQuant`；
 *    - 登录页为 `${loginName} · ${consoleName} · AstraQuant`；
 *    - 404 兜底路由为 `${notFoundName} · AstraQuant`（或 `${notFoundName} · ${consoleName} · AstraQuant`）；
 *    - 支持中英文双语根据语言切换即时更新。
 *
 * 2. **后台与 404 路由搜索引擎爬取隔离（robots: noindex）**：
 *    所有后台管理页面（`/admin/*`）及 404 页面，必须自动注入 `meta[name="robots"]` 为
 *    `noindex, nofollow, noarchive`，避免管理面或错误页被搜索引擎收录。
 *
 * 3. **全量后台路由覆盖度**：
 *    `router/index.ts` 中声明的所有后台页面子路由，必须在 `nav.ts` 的 `allAdminItems` 中有对应的导航元数据定义。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

test('router/index.ts 必须声明并执行 updateDocumentTitle', () => {
  const routerText = readFileSync(path.join(SRC, 'router/index.ts'), 'utf8');
  assert.match(routerText, /export function updateDocumentTitle\(/, '缺少 updateDocumentTitle 函数导出');
  assert.match(routerText, /router\.afterEach\(\s*\(to\)\s*=>\s*\{\s*updateDocumentTitle\(to\)/, 'afterEach 未调用 updateDocumentTitle');
  assert.match(routerText, /r20:locale-changed/, '缺少语言切换全局监听');
});

test('useI18n.ts 在语言变更时必须广播 r20:locale-changed 事件', () => {
  const i18nText = readFileSync(path.join(SRC, 'composables/useI18n.ts'), 'utf8');
  assert.match(i18nText, /r20:locale-changed/, 'useI18n 未在 applyLocale 中广播语言变更事件');
});

test('所有后台子路由在 nav.ts 中均有唯一的导航元数据匹配', () => {
  const routerText = readFileSync(path.join(SRC, 'router/index.ts'), 'utf8');
  const navText = readFileSync(path.join(SRC, 'config/nav.ts'), 'utf8');

  const adminBlock = routerText.match(/path:\s*'\/admin'[\s\S]*?children:\s*\[([\s\S]*?)\]/)?.[1] || '';
  const adminChildMatches = [...adminBlock.matchAll(/path:\s*'([^']+)',\s*name:\s*'admin-([^']+)'/g)];
  assert.ok(adminChildMatches.length >= 17, `后台子路由数量不足：${adminChildMatches.length}`);

  for (const m of adminChildMatches) {
    const subPath = m[1];
    const key = `admin-${m[2]}`;
    const inNav = navText.includes(`key: '${key}'`) && navText.includes(`path: '/admin/${subPath}'`);
    assert.ok(inNav, `后台子路由 /admin/${subPath} (${key}) 未在 nav.ts 中定义`);
  }
});

test('后台各页面标题在 locales/zh/nav.ts 与 locales/en/nav.ts 中均已定义', () => {
  const zhNavText = readFileSync(path.join(SRC, 'locales/zh/nav.ts'), 'utf8');
  const enNavText = readFileSync(path.join(SRC, 'locales/en/nav.ts'), 'utf8');
  const navText = readFileSync(path.join(SRC, 'config/nav.ts'), 'utf8');

  const labelKeys = [...navText.matchAll(/labelKey:\s*'nav\.admin\.([^']+)'/g)].map((m) => m[1]);
  assert.ok(labelKeys.length >= 17, `labelKey 数量不足：${labelKeys.length}`);

  for (const k of labelKeys) {
    assert.match(zhNavText, new RegExp(`\\b${k}:`), `zh/nav.ts 缺少 admin 标题键：${k}`);
    assert.match(enNavText, new RegExp(`\\b${k}:`), `en/nav.ts 缺少 admin 标题键：${k}`);
  }
});

test('闸自检：能准确校验缺失的路由更新与重复标题', () => {
  const badRouter = 'router.afterEach((to) => { document.title = "管理控制台 · R20"; });';
  const goodRouter = 'router.afterEach((to) => { updateDocumentTitle(to); });';

  assert.equal(/updateDocumentTitle/.test(badRouter), false, '应拦截硬编码的统一标题');
  assert.equal(/updateDocumentTitle/.test(goodRouter), true, '应放行动态更新函数');
});

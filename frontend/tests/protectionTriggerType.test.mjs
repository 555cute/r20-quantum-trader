/**
 * 保护腿「按什么价触发」的展示契约（第一百六十八刀）。
 *
 * ## 为什么需要这个门
 *
 * 后端（第一百六十七刀）已经三个状态如实输出 `protectionSlTriggerPxType`：
 *
 * | 后端值 | 含义 |
 * |---|---|
 * | `mark` / `last` / `index` | 交易所上报的触发价类型 |
 * | `'unknown'` | **腿在，但该所未上报** —— 按什么价触发不可判定 |
 * | `null` / 缺省 | **没有该类腿**（不同于"未上报"）|
 *
 * 展示层最容易出的两种错，本门把它们钉死：
 *
 * 1. **把"未上报"画成"标记价"** —— 那就是用我们期望的语义顶替交易所的事实，
 *    正是本会话反复出现的那类谎（"读不到 ≠ 没有"）；
 * 2. **把 `null`（没有这类腿）也画成"未上报"** —— 把"没有这东西"说成"读不到"。
 *
 * 另外钉住：这次改动的**唯一**目的是展示，不得顺手改动保护判据
 * （前端 KPI 判据字符串与 `tests/ui/test_protection_contract.py` 逐字对齐）。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const PANEL = 'src/components/dashboard/PositionsOrdersPanel.vue';
const ZH = 'src/locales/zh/dash/matrix.ts';
const EN = 'src/locales/en/dash/matrix.ts';
const TYPES = 'src/types/dashboard.ts';

const panel = readFileSync(PANEL, 'utf8');
const zh = readFileSync(ZH, 'utf8');
const en = readFileSync(EN, 'utf8');
const types = readFileSync(TYPES, 'utf8');

test('面板把四种取值分别映射（且 unknown 不等于 mark）', () => {
  const fn = panel.slice(panel.indexOf('function slTriggerType('));
  const body = fn.slice(0, fn.indexOf('\n}'));
  // 四种取值都要有分支
  for (const v of ['mark', 'last', 'index', 'unknown']) {
    assert.match(body, new RegExp(`===\\s*'${v}'`), `slTriggerType 未处理 ${v}`);
  }
  // **不得**把 unknown 归到 mark 的文案上：两个分支必须指向不同的键
  const unknownKey = body.match(/===\s*'unknown'\)\s*return\s*t\('([^']+)'\)/)?.[1];
  const markKey = body.match(/===\s*'mark'\)\s*return\s*t\('([^']+)'\)/)?.[1];
  assert.ok(unknownKey && markKey, '未能解析 unknown/mark 的文案键');
  assert.notEqual(unknownKey, markKey, '「未上报」被渲染成了「标记价」的文案');
  // 其余（null/缺省）⇒ 空串 ⇒ 模板 v-if 不显示（不编）
  assert.match(body, /return\s+''/, 'slTriggerType 的默认分支必须返回空串');
});

test('外所取值：Binance 字面量映射、Gate 数字码原样不翻译', () => {
  const fn = panel.slice(panel.indexOf('function slTriggerType('));
  const body = fn.slice(0, fn.indexOf('\n}'));
  assert.match(body, /===\s*'mark_price'/, '未处理 Binance MARK_PRICE');
  assert.match(body, /===\s*'contract_price'/, '未处理 Binance CONTRACT_PRICE');
  // Gate 数字码：必须原样返回（`v.startsWith('price_type:')`），**不得**落到任何中文文案键
  const rawBranch = body.match(/startsWith\('price_type:'\)\)\s*return\s+([^;]+);/)?.[1];
  assert.ok(rawBranch, '未处理 Gate 数字码（price_type:<n>）');
  assert.doesNotMatch(rawBranch, /t\(/, 'Gate 数字码被翻译成了文案 ⇒ 本仓未核实其映射，不许猜');
  // CONTRACT_PRICE 不得被画成"标记价"的文案键
  const cpKey = body.match(/===\s*'contract_price'\).*?t\('([^']+)'\)/)?.[1];
  const markKey = body.match(/===\s*'mark'\).*?t\('([^']+)'\)/)?.[1];
  assert.notEqual(cpKey, markKey, 'CONTRACT_PRICE 被画成了标记价');
});

test('悬停说明覆盖四态，且 last 明确提示插针风险', () => {
  const fn = panel.slice(panel.indexOf('function slTriggerTypeHint('));
  const body = fn.slice(0, fn.indexOf('\n}'));
  for (const v of ['mark', 'last', 'index', 'unknown']) {
    assert.match(body, new RegExp(`===\\s*'${v}'`), `slTriggerTypeHint 未处理 ${v}`);
  }
  assert.match(en, /triggerLastHint:.*wick/i, '英文提示未说明"一根插针可提前打掉"');
  assert.match(zh, /triggerLastHint:.*插针/, '中文提示未说明插针风险');
  assert.match(zh, /triggerUnknownHint:.*不可判定|triggerUnknownHint:.*未上报/, 'unknown 的中文提示必须如实');
});

test('两个语言的文案键齐备', () => {
  const keys = [
    'triggerMark', 'triggerLast', 'triggerIndex', 'triggerUnknown',
    'triggerMarkHint', 'triggerLastHint', 'triggerIndexHint', 'triggerUnknownHint',
    'triggerMarkPrice', 'triggerContractPrice', 'triggerRawCodeHint',
  ];
  for (const key of keys) {
    assert.match(zh, new RegExp(`\\b${key}:`), `中文缺键 ${key}`);
    assert.match(en, new RegExp(`\\b${key}:`), `英文缺键 ${key}`);
  }
});

test('模板只在有值时显示标签（没有该类腿就什么都不显示）', () => {
  assert.match(panel, /v-if="slTriggerType\(p\)"/, '模板未用 v-if 守卫 ⇒ 空值会渲染出空标签');
  assert.match(panel, /:title="slTriggerTypeHint\(p\)"/, '模板未接悬停说明');
});

test('类型定义允许 null 与 unknown 两态并存', () => {
  assert.match(types, /protectionSlTriggerPxType\?:\s*string\s*\|\s*null/,
               'TS 类型未允许 null（会把"没有该类腿"和"未上报"混为一谈）');
});

test('本刀只做展示：保护判据字符串未被改动', () => {
  // 与 `tests/ui/test_protection_contract.py` 逐字对齐的判据（后端门也在盯它）
  assert.match(panel, /return p\.cloud_oco_verified !== false && p\.protectionStatus !== 'unprotected';/,
               '前端保护判据被改动 ⇒ 必须同步 tests/ui/test_protection_contract.py');
});

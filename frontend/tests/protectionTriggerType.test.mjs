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

// ── 孤儿腿候选（第一百七十五刀）────────────────────────────────────────────────

test('孤儿候选徽标：只数可归因者，不可判定与读失败分开呈现', () => {
  const fnOk = panel.slice(panel.indexOf('function orphanCandidates('));
  assert.ok(fnOk, '缺 orphanCandidates');
  const bodyOk = fnOk.slice(0, fnOk.indexOf('\n}'));
  // 结构化断言（**不是**"出现过 readable 这个词"——我第一版就是这么写的，
  // 反向验证时被一行含 "readable" 的注释骗过 ⇒ 这里要求真的有三元守卫 `readable ?`）
  assert.match(bodyOk, /readable\s*\?/, '可归因计数未用 readable 守卫 ⇒ 读失败会被算成 0 候选（读不到≠没有）');

  const fnUn = panel.slice(panel.indexOf('function orphanUnattributed('));
  const bodyUn = fnUn.slice(0, fnUn.indexOf('\n}'));
  assert.match(bodyUn, /unattributed/, '不可判定计数未取 unattributed');

  const fnFail = panel.slice(panel.indexOf('function orphanReadFailed('));
  assert.match(fnFail.slice(0, fnFail.indexOf('\n}')), /readable === false/,
               '缺"读腿失败 ⇒ 不可判定"的判据');
});

test('孤儿候选提示明确"系统绝不自动撤"', () => {
  // 两个 token 各自断言（提示文案是多行拼接，用"窗口距离"匹配容易被长度绊倒）
  assert.match(zh, /orphanHint:/, '中文缺 orphanHint');
  assert.match(zh, /绝不自动撤/, '中文提示未写明"绝不自动撤"');
  assert.match(en, /orphanHint:/, '英文缺 orphanHint');
  assert.match(en, /never cancels automatically/i, '英文提示未写明"绝不自动撤"');
  for (const [file, needle] of []) {
    assert.match(file, needle, '提示未写明"绝不自动撤"⇒ 运营可能以为系统会自己清理');
  }
  for (const key of ['orphanPill', 'orphanHint', 'orphanUnknownPill', 'orphanUnknownHint',
                     'orphanReadFailHint']) {
    assert.match(zh, new RegExp(`\\b${key}:`), `中文缺键 ${key}`);
    assert.match(en, new RegExp(`\\b${key}:`), `英文缺键 ${key}`);
  }
});

test('面板不出现任何撤销调用（撤销只走显式运营动作）', () => {
  for (const forbidden of ['cancel_price_order', 'cancel_protective_orders', 'cancelAlgo',
                           'cancel_order']) {
    assert.ok(!panel.includes(forbidden), `面板出现了撤销调用 ${forbidden}`);
  }
});

// ── 归属存疑腿（第一百八十一刀）────────────────────────────────────────────────

test('归属存疑徽标：两种 mismatch 计数且受 readable 守卫', () => {
  const fn = panel.slice(panel.indexOf('function orphanMismatch('));
  assert.ok(fn.startsWith('function orphanMismatch('), '缺 orphanMismatch');
  const body = fn.slice(0, fn.indexOf('\n}'));
  // 接受两种等价写法（三元 `readable ?` 或早退 `if (!o.readable) return 0`），
  // 但必须**真的**有 readable 参与判断 —— 只出现这个词不算（本会话踩过这个坑）
  assert.match(body, /(readable\s*\?)|(!\s*o\.readable)/,
               '归属存疑计数没有 readable 守卫 ⇒ 读腿失败会被算成 0 条');
  assert.match(body, /sideMismatch/, '未统计方向不符的腿');
  assert.match(body, /sizeMismatch/, '未统计量不符的腿');
  assert.match(panel, /mismatchPill/, '面板没有渲染归属存疑徽标');
  assert.match(panel, /mismatchHint/, '徽标缺少悬停说明');
});

test('归属存疑提示写明两种语义的差别', () => {
  for (const [file, name] of [[zh, '中文'], [en, '英文']]) {
    for (const key of ['mismatchPill', 'mismatchHint']) {
      assert.match(file, new RegExp(`\\b${key}:`), `${name}缺键 ${key}`);
    }
  }
  assert.match(zh, /不计入覆盖/, '中文提示未写明"方向不符的不计入覆盖"');
  assert.match(zh, /仍被计入覆盖/, '中文提示未写明"量不符的仍计入覆盖"');
  assert.match(en, /NOT counted as coverage/i, '英文提示未写明 side-mismatch 不计覆盖');
  assert.match(en, /ARE counted as coverage/i, '英文提示未写明 size-mismatch 仍计覆盖');
});

test('腿认不出徽标：计数受 readable 守卫且写明"覆盖可能被低估"', () => {
  const fn = panel.slice(panel.indexOf('function orphanUnclassified('));
  assert.ok(fn.startsWith('function orphanUnclassified('), '缺 orphanUnclassified');
  const body = fn.slice(0, fn.indexOf('\n}'));
  assert.match(body, /(!\s*o\.readable)|(readable\s*\?)/, '缺 readable 守卫');
  assert.match(body, /foreignCount/, '未统计认不出类型的腿');
  assert.match(body, /unparsedCount/, '未统计解析不了的腿');
  assert.match(panel, /unclassifiedPill/, '面板没有渲染该徽标');
  assert.match(zh, /覆盖会被\*\*低估\*\*/, '中文提示未写明"覆盖可能被低估"');
  assert.match(en, /UNDERESTIMATED/i, '英文提示未写明 UNDERESTIMATED');
});

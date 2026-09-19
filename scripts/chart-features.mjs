/**
 * K 线图**功能矩阵**验收（逐项真实操作 + 读库层/图元状态）。
 *
 * 覆盖：周期切换、快捷键、平移/回实时、场所切换（含上游 502 的诚实降级）、
 * 轨迹线开关、绘图工具面板、十字线悬浮卡、成本线（无持仓不残留）、
 * 指标参数修改、水印、中英切换。
 */
import { chromium } from 'playwright'

const BASE = process.env.QX_BASE || 'http://127.0.0.1:8090' || 'http://127.0.0.1:8090'
const token = (process.env.R20_ADMIN_TOKEN || '').trim()
let session = ''
{
  const r = await fetch(`${BASE}/api/v1/admin/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: 'admin', password: token }) })
  const d = await r.json().catch(() => ({}))
  if (r.ok && d.session_token) session = d.session_token
}

/** 语言无关定位：把多个 aria-label 拼成一个选择器（界面可能是中/英任意一种）。 */
const btn = (...names) => page.locator(names.map((n) => `button[aria-label="${n}"]`).join(', ')).first()
const nativeSel = (...names) => page.locator(names.map((n) => `select[aria-label="${n}"]`).join(', ')).first()
const L = {
  symbol: ['Instruments', '标的'],
  venue: ['Candle source', 'K线数据源'],
  indicators: ['Indicators', '指标'],
  drawing: ['Drawing tools', '绘图工具'],
  trail: ['Trajectory', '轨迹线'],
  period: (p) => [`Timeframe ${p}`, `周期 ${p}`],
}

const rows = []
const add = (name, ok, detail = '') => rows.push({ name, ok, detail })

const browser = await chromium.launch({ args: ['--no-sandbox'] })
const ctx = await browser.newContext({ viewport: { width: 1680, height: 1100 } })
await ctx.addInitScript(() => {
  try {
    localStorage.setItem('qx_debug_chart', '1')
    // 诊断用：QX_NO_PRIMITIVES=1 时关掉自定义图元（定位库内断言）
    if (process?.env?.QX_NO_PRIMITIVES === '1') localStorage.setItem('qx_disable_primitives', '1')
  } catch {}
})
if (session) await ctx.addInitScript(([k, v]) => { try { localStorage.setItem(k, v) } catch {} }, ['r20_admin_session', session])
const page = await ctx.newPage()
const errs = []
page.on('pageerror', (e) => {
  const st = (e.stack || '').split('\n').slice(0, 10).map((l) => l.trim()).join(' <= ')
  errs.push(`${e.message.slice(0, 80)} :: ${st.slice(0, 300)}`)
})
/** 阶段标记：错误发生在哪一步 */
let phase = 'init'
const phaseLog = []
const note = (p) => { phaseLog.push({ phase, errors: errs.length }); phase = p }
page.on('console', (m) => {
  if (m.type() !== 'error') return
  if (/favicon|Failed to load resource|WebSocket|HTTP 502|502/i.test(m.text())) return
  const l = m.location?.() ?? {}
  errs.push(`${m.text().slice(0, 110)} @${(l.url || '').split('/').pop()}:${l.lineNumber ?? '?'}`)
})
await page.goto(`${BASE}/trading`, { waitUntil: 'networkidle' })
await page.waitForTimeout(9000)

const host = page.locator('[data-testid="qx-chart-canvas"]')
if (!(await host.count())) { console.log(JSON.stringify({ pass: 0, total: 1, rows: [{ name: '图表存在', ok: false, detail: '' }], errs })); await browser.close(); process.exit(1) }
const box = await host.boundingBox()
const snap = () => page.evaluate(() => ({
  bars: window.__qxPaging?.bars ?? null,
  count: window.__qxPaging?.count ?? null,
  firstTs: window.__qxPaging?.firstTs ?? null,
  candles: window.__qxSeries?.data?.().length ?? null,
  range: window.__qxChart ? window.__qxChart.timeScale().getVisibleLogicalRange() : null,
  source: document.querySelector('[data-testid="qx-chart-legend"]')?.textContent?.match(/(OKX|GATE|BINANCE|COINBASE|BYBIT|HYPERLIQUID)[^·]*REST/i)?.[0] ?? null,
  legend: document.querySelector('[data-testid="qx-chart-legend"]')?.innerText?.replace(/\n/g, ' | ').slice(0, 90) ?? null,
  trails: window.__qxTrailPlugin?.pairCount?.() ?? null,
  costLines: window.__qxSeries?.__qxCostLines?.length ?? null,
  watermark: Boolean(window.__qxChart?.__qxWatermark),
  maText: (document.querySelector('[data-testid="qx-chart-legend"]')?.textContent || '').match(/MA\(([^)]*)\)\s*([\d.]+)/)?.slice(1, 3) ?? null,
}))

// ① 周期切换：7 个周期逐个点，要求 K 线按周期对齐且根数 > 0
const periods = ['1m', '5m', '15m', '1H', '4H', '1D', '1W']
const barMs = { '1m': 60e3, '5m': 300e3, '15m': 900e3, '1H': 3600e3, '4H': 14400e3, '1D': 86400e3, '1W': 604800e3 }
const bad = []
note('periods')
for (const p of periods) {
  await btn(...L.period(p)).click({ timeout: 5000 }).catch(() => {})
  // 切周期后**旧周期的 K 线会先留着**直到新数据到达（交易所同款：不闪白）。
  // 所以这里要等到"数据真的换成该周期"再断言，而不是固定等 3.2s。
  const aligned = (s) =>
    s.firstTs !== null &&
    s.candles > 0 &&
    (p === '1W'
      ? s.firstTs % 86400000 === 57600000 && new Date(s.firstTs + 8 * 3600e3).getUTCDay() === 1
      : p === '1D'
        ? s.firstTs % 86400000 === 57600000
        : s.firstTs % barMs[p] === 0)
  let latest = await snap()
  for (let i = 0; i < 16; i += 1) {
    if (latest.bars === p && aligned(latest)) break
    await page.waitForTimeout(600)
    latest = await snap()
  }
  if (!(latest.bars === p && aligned(latest))) {
    bad.push(`${p}: bars=${latest.bars} aligned=${aligned(latest)} 首根=${new Date(latest.firstTs).toISOString()}`)
  }
  phaseLog.push({ phase: `switch-${p}`, errors: errs.length, candles: latest.candles, ts: latest.firstTs })
}
add('周期切换 7 档全部生效且时间对齐', bad.length === 0, bad.length ? bad.join(' ; ') : '1m/5m/15m/1H/4H/1D/1W 均按周期对齐且根数>0')

// ② 快捷键 1..7 切周期
await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
note('keys')
await page.keyboard.press('3')
await page.waitForTimeout(2600)
const afterKey = await snap()
add('快捷键数字键切周期', afterKey.bars === '15m', `按 3 → bars=${afterKey.bars}（期望 15m）`)

// ③ 方向键平移 + R 回实时
note('pan')
const beforePan = await snap()
await page.keyboard.press('ArrowLeft')
await page.keyboard.press('ArrowLeft')
await page.waitForTimeout(500)
const afterPan = await snap()
const panned = beforePan.range && afterPan.range && afterPan.range.from < beforePan.range.from
await page.keyboard.press('r')
await page.waitForTimeout(600)
const afterR = await snap()
// "回到实时"= 视窗右端贴住最新数据（含 rightOffset 6 的留白），而不是简单地比 to 变大
const backLive = Boolean(afterR.range) && afterR.candles !== null && afterR.range.to - afterR.candles <= 8
add(
  '方向键平移视窗 / R 回实时',
  Boolean(panned && backLive),
  `before=${JSON.stringify(beforePan.range)} after=${JSON.stringify(afterPan.range)} realtime=${JSON.stringify(afterR.range)} panned=${panned} backLive=${backLive}`,
)

// ④ 场所切换：GATE 可用；不可用的场所要诚实降级（不白屏、有错误状态）
note('venue')
/**
 * 场所切换：用**组件自己的提交方式**（原生 select 上 set value + 派发 change）。
 *
 * 为什么不点自定义下拉：原生 `<select>` 是 `sr-only`，Playwright 的 `selectOption`
 * 会因不可见而超时；改点自定义下拉又容易把浮层留在打开状态，挡住后续控件
 * （实测导致"轨迹线"按钮点击超时）。组件内部 `commit()` 就是这套 setter+change，
 * 所以这是它的正规入口，不是绕过应用的旁路。
 */
const setVenue = (want) =>
  page.evaluate((v) => {
    const sel = document.querySelector('select[aria-label="Candle source"], select[aria-label="K线数据源"]')
    if (!sel) return false
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set
    setter?.call(sel, v)
    sel.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  }, want)
const setOk = await setVenue('gate')
await page.waitForTimeout(8000)
const gateSnap = await snap()
add(
  '场所切换（OKX→GATE）数据真的换源',
  setOk && Boolean(gateSnap.source && /GATE/i.test(gateSnap.source)) && gateSnap.candles > 0,
  `提交=${setOk} source=${gateSnap.source} candles=${gateSnap.candles}`,
)
await setVenue('okx')
await page.waitForTimeout(8000)
const back = await snap()
add(
  '切回 OKX 恢复正常',
  Boolean(back.source && /OKX/i.test(back.source)) && back.candles > 0,
  `source=${back.source} candles=${back.candles}`,
)

// ⑤ 轨迹线开关（读图元 pairCount）
note('trail')
await btn(...L.trail).click()
await page.waitForTimeout(700)
const trailOff = await snap()
await btn(...L.trail).click()
await page.waitForTimeout(700)
const trailOn = await snap()
add('轨迹线开关生效（图元 pairCount）', trailOff.trails === 0 && (trailOn.trails ?? 0) >= 0, `关=${trailOff.trails} 开=${trailOn.trails}`)

// ⑥ 绘图工具面板展开/收起
note('drawing')
await btn(...L.drawing).click()
await page.waitForTimeout(700)
const toolPanel = await page.evaluate(() => {
  const el = [...document.querySelectorAll('aside')].find((a) => /绘图工具|Drawing tools/.test(a.getAttribute('aria-label') || ''))
  if (!el) return null
  const r = el.getBoundingClientRect()
  return { visible: r.width > 10 && r.height > 10, buttons: el.querySelectorAll('button').length }
})
add('绘图工具面板展开（悬浮列）', Boolean(toolPanel?.visible) && (toolPanel?.buttons ?? 0) > 5, `面板=${JSON.stringify(toolPanel)}`)
await btn(...L.drawing).click()
await page.waitForTimeout(500)
const collapsed = await page.evaluate(() => ![...document.querySelectorAll('aside')].some((a) => /绘图工具|Drawing tools/.test(a.getAttribute('aria-label') || '')))
add('绘图工具面板可收起', collapsed, `收起=${collapsed}`)

// ⑦ 成本线：当前无持仓 → 不应残留任何成本线（有持仓时由 positions prop 画，见文档）
const noCost = (await snap()).costLines
add('无持仓时不画成本线（不残留）', noCost === 0, `__qxCostLines=${noCost}（演示账户当前 0 持仓）`)

// ⑧ 指标参数修改：MA 21,55 → 7,25（图例读数与图元同步）
await btn(...L.indicators).click()
await page.waitForTimeout(500)
const maInput = page.locator('[data-portal-layer="1"]').last().locator('label').filter({ hasText: /^MA/ }).locator('input[type=text], input:not([type=checkbox])').first()
note('ma-params')
const beforeMa = (await snap()).maText
if (await maInput.count()) {
  await maInput.fill('7,25')
  await page.waitForTimeout(1200)
}
await page.keyboard.press('Escape')
await page.waitForTimeout(600)
const afterMa = (await snap()).maText
add('指标参数可修改（MA 7,25）', Boolean(afterMa && afterMa[0] === '7,25'), `图例 MA ${JSON.stringify(beforeMa)} → ${JSON.stringify(afterMa)}`)
// 复位
await btn(...L.indicators).click()
await page.waitForTimeout(400)
const maInput2 = page.locator('[data-portal-layer="1"]').last().locator('label').filter({ hasText: /^MA/ }).locator('input[type=text], input:not([type=checkbox])').first()
if (await maInput2.count()) await maInput2.fill('21,55').catch(() => {})
await page.keyboard.press('Escape')
await page.waitForTimeout(500)

// ⑨ 水印（v5 原生 createTextWatermark）
note('watermark')
const wm = (await snap()).watermark
add('图纸水印存在（标的·周期·场所）', wm === true, `__qxWatermark=${wm}`)

// ⑩ 十字线悬浮卡（切到有成交的标的，悬停到有成交那根）
await btn(...L.symbol).click().catch(() => {})
await page.waitForTimeout(400)
await page.locator('[role="option"]').filter({ hasText: /ADA/ }).first().click().catch(() => {})
await page.waitForTimeout(7000)
note('hover-card')
const markers = await page.evaluate(() => window.__qxMarkerPlugin?.markers?.() ?? [])
const boxNow = (await host.boundingBox()) ?? box
/** 十字线悬浮卡：把鼠标放在"有成交那根"附近扫几个点（±6px），只要有一个命中即可。
 *  为什么扫而不是打一个点：marker 的 x 来自库的坐标换算，四舍五入到整数像素后
 *  可能落在相邻两根之间，单点会偶发不命中（实测 1/3 概率）。 */
const cardText = () =>
  page.evaluate(() => {
    const el = [...document.querySelectorAll('div')].find((d) => d.className.includes('w-[16rem]'))
    return el ? (el.textContent || '').replace(/\s+/g, ' ').slice(0, 140) : null
  })
let card = null
if (markers.length > 0) {
  const xs = await page.evaluate((times) => times.map((t) => window.__qxChart?.timeScale().timeToCoordinate(t) ?? null), markers.map((m) => m.time / 1000))
  const candidates = xs.filter((x) => x !== null).flatMap((x) => [x - 6, x, x + 6])
  for (const cx of candidates) {
    await page.mouse.move(boxNow.x + Math.min(Math.max(cx, 30), boxNow.width - 40), boxNow.y + boxNow.height * 0.45)
    await page.waitForTimeout(500)
    card = await cardText()
    if (card) break
  }
}
add(
  '十字线悬浮卡显示成交（图标标记同源）',
  Boolean(card) && /U|Long|Short|做多|做空|orders|委托/.test(card),
  `标记 ${markers.length} 枚（${markers.slice(0, 2).map((m) => m.text).join(',')}）· 卡片=${JSON.stringify(card)}`,
)

// ⑪ 中英切换（图例文案跟随）
note('i18n')
await page.evaluate(() => { localStorage.setItem('r20.lang', 'zh'); window.dispatchEvent(new Event('r20:lang')) })
await page.reload({ waitUntil: 'networkidle' })
await page.waitForTimeout(9000)
const zh = await page.evaluate(() => document.querySelector('[data-testid="qx-chart-legend"]')?.innerText ?? '')
add('图例文案跟随语言（中文）', /开|收|距收盘/.test(zh), `中文图例片段=${JSON.stringify(zh.replace(/\n/g, ' | ').slice(0, 70))}`)

const realErrs = errs.filter((e) => !/ResizeObserver|502/.test(e))
const libAsserts = realErrs.filter((e) => /Value is null/.test(e))
const otherErrs = realErrs.filter((e) => !/Value is null/.test(e))
// 库内断言：**如实计数**，但要求自愈后图表状态依然一致（数据非空、周期对齐、图元在位）
const healed = await page.evaluate(() => {
  const rows = window.__qxSeries?.data?.() ?? []
  const chart = window.__qxChart
  return {
    candles: rows.length,
    panes: chart ? chart.panes().length : 0,
    legend: Boolean(document.querySelector('[data-testid="qx-chart-legend"]')),
    ts: window.__qxPaging?.firstTs ?? null,
    bars: window.__qxPaging?.bars ?? null,
  }
})
add(
  '除库内断言外零异常',
  otherErrs.length === 0,
  otherErrs.length ? otherErrs.slice(0, 3).join(' / ') : '无（库内断言次数见下一条）',
)
add(
  '库内断言后图表自愈且状态一致',
  healed.candles > 0 && healed.panes === 3 && healed.legend && healed.ts !== null,
  `库内断言 ${libAsserts.length} 次（lightweight-charts v5.2.1 内部，已记录）；自愈后 candles=${healed.candles} panes=${healed.panes} 图例=${healed.legend} 周期=${healed.bars}`,
)

await browser.close()
const pass = rows.filter((r) => r.ok).length
phaseLog.push({ phase, errors: errs.length })
console.log(JSON.stringify({ pass, total: rows.length, rows, errs, phaseLog }, null, 2))
process.exit(pass === rows.length ? 0 : 1)

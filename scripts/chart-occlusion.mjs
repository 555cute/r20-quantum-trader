/**
 * QuantX PRO — 图表遮挡专项验收（多视口，逐根蜡烛判定）
 *
 * 存在的理由：最初的"不遮挡"断言只比较了**整图的最高点**，漏掉了
 * "图例覆盖的那段横坐标内、某些蜡烛顶到图例下沿"这种情况——实测在
 * 1600×900 下有 26 根蜡烛被图例压住（最差 42px），而当时的断言是绿的。
 * 现在按"图例横向范围内的每一根蜡烛"逐根判定，并在多个视口复跑。
 *
 * 用法：
 *   QX_BASE=http://host.docker.internal:8090 R20_ADMIN_TOKEN=<密码> node scripts/chart-occlusion.mjs
 * 退出码：0 = 全部视口 0 重叠；1 = 有重叠。
 */
import { chromium } from 'playwright'

const BASE = process.env.QX_BASE || 'http://127.0.0.1:8090'
const token = (process.env.R20_ADMIN_TOKEN || '').trim()
const VIEWPORTS = [
  [1920, 1080],
  [1680, 1300],
  [1600, 900],
  [1440, 1200],
  [1280, 800],
]

let session = ''
if (token) {
  const r = await fetch(`${BASE}/api/v1/admin/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'admin', password: token }),
  })
  const d = await r.json().catch(() => ({}))
  if (r.ok && d.session_token) session = d.session_token
}

const rows = []
const browser = await chromium.launch({ args: ['--no-sandbox'] })
for (const [w, h] of VIEWPORTS) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } })
  await ctx.addInitScript(() => {
    try {
      localStorage.setItem('qx_debug_chart', '1')
    } catch {}
  })
  if (session) {
    await ctx.addInitScript(([k, v]) => {
      try {
        localStorage.setItem(k, v)
      } catch {}
    }, ['r20_admin_session', session])
  }
  const page = await ctx.newPage()
  /**
   * 等图表就绪：图例在位、有数据、坐标可算，且 series 实例在 800ms 内**没有换过**。
   *
   * 为什么要实例稳定性：页面数据到位时会重建一次图表，重建窗口里 `window.__qxSeries`
   * 仍指向**已销毁**的旧实例（`data()` 为空），此时判定会"检查 0 根"空过。
   * 为什么要重试：上游 K 线偶发失败（本沙箱实测 502）时页面根本不会有图例与蜡烛，
   * 这时不能当成"0 重叠通过"，要重载一次；仍失败则明确报"未就绪"。
   */
  /**
   * 等图表就绪：图例在位、有数据、坐标可算，且 series 实例在 800ms 内没换过。
   *
   * **为什么手动轮询而不是 `page.waitForFunction`**：本站在 CSP 里禁用了字符串求值，
   * 而 `waitForFunction` 的轮询机制会在页内 `eval` 表达式 → 直接被 CSP 拒
   * （`EvalError: Refused to evaluate a string as JavaScript`）。
   * 之前那段 `waitForFunction(...).catch(() => {})` 其实一直没生效，是靠后面的固定等待蒙对的。
   * `page.evaluate`(函数) 走的是 `Runtime.callFunctionOn`，不受该限制。
   *
   * 状态链会记录下来（失败时打印），避免再出现"空过但显示 PASS"。
   */
  const probe = () =>
    page.evaluate(() => {
      try {
        const w = window
        const legend = document.querySelector('[data-testid="qx-chart-legend"]')
        const series = w.__qxSeries
        const chart = w.__qxChart
        if (!legend || !series || !chart) return { s: `missing(legend=${!!legend},series=${!!series},chart=${!!chart})` }
        const rows = series.data()
        if (!rows.length) return { s: 'no-rows' }
        const mid = rows[Math.floor(rows.length / 2)]
        if (mid?.time === undefined) return { s: 'mid-no-time' }
        const x = chart.timeScale().timeToCoordinate(mid.time)
        if (x === null || x === undefined) return { s: 'x-null' }
        if (w.__qxSeriesSeen !== series) {
          w.__qxSeriesSeen = series
          w.__qxSeriesStableAt = Date.now()
          return { s: 'series-changed' }
        }
        return { s: Date.now() - (w.__qxSeriesStableAt ?? 0) > 800 ? 'stable' : 'settling' }
      } catch (e) {
        return { s: 'throw:' + String(e).slice(0, 60) }
      }
    })
  const ready = async (attempts = 32) => {
    const chain = []
    for (let i = 0; i < attempts; i += 1) {
      const { s: st } = await probe()
      chain.push(st)
      if (st === 'stable') return { ok: true, chain }
      await page.waitForTimeout(700)
    }
    return { ok: false, chain }
  }
  await page.goto(`${BASE}/trading`, { waitUntil: 'networkidle' })
  let res = await ready()
  if (!res.ok) {
    await page.reload({ waitUntil: 'networkidle' })
    res = await ready()
  }
  await page.waitForTimeout(1500)
  if (!res.ok) {
    const why = await page.evaluate(() => ({
      paging: window.__qxPaging ?? null,
      legend: document.querySelector('[data-testid="qx-chart-legend"]')?.textContent?.slice(0, 60) ?? null,
      seriesLen: window.__qxSeries?.data?.().length ?? null,
      bodyLen: document.body.innerText.length,
    }))
    rows.push({ viewport: `${w}x${h}`, notReady: true, why, chain: res.chain.slice(-6) })
    await ctx.close()
    continue
  }
  const m = await page.evaluate(() => {
    const host = document.querySelector('[data-testid="qx-chart-canvas"]')
    // **只判定我们自己的常驻浮层**（按 data-testid 白名单）。
    // 不能用"所有绝对定位 div"：绘图库（lightweight-charts-drawing）会自建覆盖整个
    // 图区的图层（无 className），那会把它自己当成遮挡物，判定全错。
    const overlays = [
      ...host.parentElement.querySelectorAll('[data-testid="qx-chart-legend"], [data-testid="qx-chart-overlay-bar"]'),
    ].filter((el) => {
      const cs = getComputedStyle(el)
      if (cs.display === 'none' || cs.visibility === 'hidden') return false
      const r = el.getBoundingClientRect()
      return r.width > 2 && r.height > 2
    })
    const hostRect = host.getBoundingClientRect()
    const rects = overlays.map((el) => {
      const r = el.getBoundingClientRect()
      return {
        cls: el.getAttribute('data-testid') || '',
        top: r.top - hostRect.top,
        bottom: r.bottom - hostRect.top,
        left: r.left - hostRect.left,
        right: r.right - hostRect.left,
      }
    })
    const chart = window.__qxChart
    const series = window.__qxSeries
    const ts = chart.timeScale()
    const offenders = []
    let checked = 0
    for (const d of series.data()) {
      if (d.high === undefined) continue
      const x = ts.timeToCoordinate(d.time)
      if (x === null) continue
      const yHigh = series.priceToCoordinate(d.high)
      if (yHigh === null) continue
      checked += 1
      for (const r of rects) {
        // 只判定"横坐标落在浮层范围内"的蜡烛
        if (x < r.left - 6 || x > r.right + 6) continue
        if (yHigh < r.bottom - 1) {
          offenders.push({ time: d.time, x: Math.round(x), yHigh: Math.round(yHigh), overlay: r.cls, overlapPx: Math.round(r.bottom - yHigh) })
          break
        }
      }
    }
    return {
      host: [host.clientWidth, host.clientHeight],
      paneH: chart.panes()[0].getHeight(),
      scaleTop: chart.priceScale('right').options().scaleMargins.top,
      legendBottom: rects.find((r) => r.cls.includes('legend'))?.bottom ?? null,
      overlays: rects.length,
      checked,
      offenders: offenders.slice(0, 5),
      offenderCount: offenders.length,
    }
  })
  rows.push({ viewport: `${w}x${h}`, ...m })
  await ctx.close()
}
await browser.close()

// **空过保护**：没有检查到任何蜡烛、或没找到图例，都算失败（否则"0 重叠"毫无意义）
const bad = rows.filter((r) => r.notReady || r.offenderCount > 0 || r.checked === 0 || r.legendBottom === null)
for (const r of rows) {
  if (r.notReady) {
    console.log(`  FAIL ${r.viewport} 图表未就绪（重载后仍无数据/图例）：${JSON.stringify(r.why)} 状态链=${JSON.stringify(r.chain)}`)
    continue
  }
  const vacuous = r.checked === 0 || r.legendBottom === null
  const mark = r.offenderCount === 0 && !vacuous ? 'PASS' : 'FAIL'
  console.log(
    `  ${mark} ${r.viewport} 图区=${r.host.join('×')} 主图pane=${r.paneH}px 留白=${(r.scaleTop * 100).toFixed(1)}% 图例下沿=${Math.round(r.legendBottom ?? -1)}px 检查 ${r.checked} 根 → 重叠 ${r.offenderCount} 根${vacuous ? '（空过：未取到图例/蜡烛 → 判定失败）' : ''}${r.offenderCount ? ' ' + JSON.stringify(r.offenders) : ''}`,
  )
}
console.log(`\n遮挡专项：${rows.length - bad.length} / ${rows.length} 个视口 0 重叠（空过按失败计）`)
process.exit(bad.length === 0 ? 0 : 1)

import { chromium } from 'playwright-core'
import { mkdirSync } from 'node:fs'

/**
 * WEB-FIX-405 — tier 3, and cluster C's two captures, driven against THIS repository.
 *
 * `run-gate.sh` drives `gate/fixture-repo`: nine synthetic documents and a stand-in agent that
 * prints nothing. `WEB-FIX-248` and `WEB-FIX-261` are the two tickets about believing that is a
 * screen somebody can read, so everything below is asked of the real backlog — 400-odd tickets,
 * the epics, this round's own unshipped work — from inside the lane this ticket is being run in.
 *
 * Three things a person does:
 *   1. opens the tickets list and searches it — the SEARCH row `WEB-FIX-334` put in the design and
 *      never photographed, which is why the screenshot ratchet has been red on `main`;
 *   2. opens `SRV-FIX-351`, the ticket about a session in a lane being able to run a gate and read
 *      what it wrote — read here, in a lane, by a session that has just done exactly that;
 *   3. opens a requirement and the lane rail, which is where cluster A's delivery badges and
 *      cluster B's seat total are drawn.
 */

const WEB = process.env.TIER3_WEB || 'http://localhost:7889'
const API = process.env.TIER3_API || 'http://127.0.0.1:7887'
const SHOTS = process.env.TIER3_SHOTS
const REPORTS = process.env.TIER3_REPORTS
const CHROME = process.env.CHROME_PATH || '/usr/bin/google-chrome'

const say = (...a) => console.log(...a)
for (const d of [SHOTS, `${REPORTS}/WEB-FIX-334`, `${REPORTS}/SRV-FIX-351`]) mkdirSync(d, { recursive: true })

const browser = await chromium.launch({
  executablePath: CHROME, headless: true, args: ['--no-sandbox', '--font-render-hinting=none'],
})

const health = await (await fetch(`${API}/api/health`)).json()
say(`the repository this server is serving: ${health.repo?.root} · manifest ${health.manifest}`)

async function drive(theme) {
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1100 } })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))

  await page.goto(WEB, { waitUntil: 'networkidle' })
  await page.evaluate((t) => {
    localStorage.setItem('factory.theme', t)
    document.documentElement.setAttribute('data-theme', t)
  }, theme)
  await page.reload({ waitUntil: 'networkidle' })

  // ── 1 · WEB-FIX-334's SEARCH row, on the real backlog ─────────────────────────────────────
  await page.goto(`${WEB}/#/tickets`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="tickets-by-epic"]', { timeout: 20000 }).catch(() => {})
  const box = page.locator('[data-testid="tickets-search"]').first()
  await box.fill('WEB-FIX-405')
  await page.waitForTimeout(600)
  const result = (await page.locator('[data-testid="search-result"]').innerText()).trim()
  say(`[${theme}] #/tickets · searched "WEB-FIX-405" · result line = ${result.trim()}`)
  await page.screenshot({ path: `${REPORTS}/WEB-FIX-334/WEB-FIX-334-search-row-${theme}.png`,
                          fullPage: false })
  await box.fill('')
  await page.waitForTimeout(600)
  const cleared = await page.locator('[data-testid="search-result"]').count()
  const groups = await page.locator('[data-testid="epic-group"]').count()
  const pill = (await page.locator('[data-testid="tickets-by-epic"] .pill').innerText()).trim()
  say(`[${theme}] #/tickets · cleared · result lines on screen = ${cleared}`
    + ` · epic groups drawn = ${groups} · header pill = ${pill}`)
  await page.screenshot({ path: `${REPORTS}/WEB-FIX-334/WEB-FIX-334-tickets-unfiltered-${theme}.png` })

  // ── 2 · SRV-FIX-351, read in a lane by a session that has just run a gate in one ──────────
  await page.goto(`${WEB}/#/tickets/SRV-FIX-351`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  const t351 = await page.locator('body').innerText()
  say(`[${theme}] #/tickets/SRV-FIX-351 · title drawn = ${t351.includes('SRV-FIX-351')}`
    + ` · status drawn = ${/pass/i.test(t351)}`
    + ` · stages drawn = ${t351.includes('GATE') && t351.includes('QA')}`)
  await page.screenshot({ path: `${REPORTS}/SRV-FIX-351/SRV-FIX-351-ticket-surface-${theme}.png` })

  // ── 3 · delivery badges on a real requirement (cluster A) ─────────────────────────────────
  const epics = await (await fetch(`${API}/api/requirements?q=&filter=all`)).json()
  const first = (epics.requirements ?? epics.rows ?? [])[0]?.id
  for (const id of [first, 'standalone'].filter(Boolean)) {
    await page.goto(`${WEB}/#/requirements/${id}`, { waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="ticket-row"]', { timeout: 20000 }).catch(() => {})
    const drawn = await page.$$eval('[data-testid="delivery-badge"]',
      (els) => els.map((e) => `${e.getAttribute('data-state')}/alarm=${e.getAttribute('data-alarm')}`))
    const served = await (await fetch(`${API}/api/requirements/${id}`)).json()
    const wire = [served.delivery, ...(served.groups ?? []).flatMap((g) => g.tickets ?? [])
      .map((x) => x.delivery)].filter(Boolean)
    const byState = new Map()
    for (const d of wire) if (!byState.has(d.state)) byState.set(d.state, d.needs_you)
    say(`[${theme}] #/requirements/${id} · the tool says ${[...byState.entries()]
      .map(([s, n]) => `${s}(needs_you=${n})`).sort().join(' · ')}`)
    say(`[${theme}] #/requirements/${id} · badges on screen = ${
      [...new Set(drawn)].sort().join(' · ') || 'none'}`)
    await page.screenshot({ path: `${SHOTS}/requirement-${id}-${theme}.png` })
  }

  // ── 4 · the lane rail's seat total (cluster B2) ───────────────────────────────────────────
  await page.goto(`${WEB}/#/lanes`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  const lanes = await (await fetch(`${API}/api/lanes`)).json()
  const rail = (await page.locator('[data-testid="sidenav"]').innerText()).replace(/\n/g, ' | ')
  say(`[${theme}] #/lanes · E9 seats = ${JSON.stringify(lanes.seats)} · rail says: ${rail}`)
  await page.screenshot({ path: `${SHOTS}/lanes-${theme}.png` })

  // ── 5 · this ticket, on the screen a person opens ─────────────────────────────────────────
  await page.goto(`${WEB}/#/tickets/WEB-FIX-405`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)
  const mine = await page.locator('body').innerText()
  say(`[${theme}] #/tickets/WEB-FIX-405 · drawn = ${mine.includes('WEB-FIX-405')}`
    + ` · parent FAC-EPIC-376 on screen = ${mine.includes('FAC-EPIC-376')}`
    + ` · seams S4 on screen = ${mine.includes('S4')}`)
  await page.screenshot({ path: `${SHOTS}/ticket-405-${theme}.png` })

  say(`[${theme}] page errors: ${errors.length ? errors.join(' | ') : 'none'}`)
  await ctx.close()
}

for (const theme of ['dark', 'light']) await drive(theme)
await browser.close()

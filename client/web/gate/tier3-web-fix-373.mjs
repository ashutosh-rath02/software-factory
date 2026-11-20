import { chromium } from 'playwright-core'

/**
 * WEB-FIX-373 — tier 3, driven against THIS repository rather than `gate/fixture-repo`.
 *
 * The derivation this ticket lands rests on two things a person can see: the delivery words §4.11's
 * sheet ships, and the badges a requirement screen actually draws. The browser gate asks both of a
 * synthetic nine-ticket fixture, which is what `WEB-FIX-248` and `WEB-FIX-261` are about — so they
 * are asked here of the real backlog, in both themes.
 */

const WEB = process.env.TIER3_WEB || 'http://localhost:7889'
const API = process.env.TIER3_API || 'http://127.0.0.1:7887'
const SHOTS = process.env.TIER3_SHOTS
const CHROME = process.env.CHROME_PATH || '/usr/bin/google-chrome'

const say = (...a) => console.log(...a)
const browser = await chromium.launch({
  executablePath: CHROME, headless: true, args: ['--no-sandbox', '--font-render-hinting=none'],
})

const epics = await (await fetch(`${API}/api/requirements?q=&filter=all`)).json()
const listed = epics.requirements ?? epics.rows ?? []
// TWO requirements, and they do not agree: the first declared one, whose tickets are all merged or
// never started, and `standalone`, which holds this round's own unshipped work. A single
// requirement on this backlog reaches ONE badge state, and on a population of one every set
// comparison below is the identity — `_TEMPLATE.md` §2's rule about a reduction.
const ROUTES = [listed[0]?.id, 'standalone'].filter(Boolean)
say(`the repository on screen: ${listed.length} requirements · driving ${ROUTES.join(' · ')}`)

async function drive(theme) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e)))

  await page.goto(WEB, { waitUntil: 'networkidle' })
  await page.evaluate((t) => {
    localStorage.setItem('factory.theme', t)
    document.documentElement.setAttribute('data-theme', t)
  }, theme)
  await page.reload({ waitUntil: 'networkidle' })

  // ── 1 · the vocabulary sheet — the derivation's first input, on the real product ───────────
  await page.goto(`${WEB}/#/vocabulary`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="delivery-value"]', { timeout: 10000 })
  const words = await page.$$eval('[data-testid="delivery-value"]',
    (els) => els.map((e) => ({ state: e.getAttribute('data-state'), text: e.innerText.trim() })))
  say(`[${theme}] vocabulary sheet · delivery words a person reads = `
    + words.map((w) => `${w.state}:"${w.text.split('\n')[0]}"`).join(' · '))
  say(`[${theme}] vocabulary sheet · every row carries its VALUE, not only its label = `
    + `${words.length > 0 && words.every((w) => w.state)}`)
  await page.screenshot({ path: `${SHOTS}/vocabulary-${theme}.png` })

  // ── 2 · requirement screens on the REAL backlog — the derivation's second input ───────────
  const seen = []
  for (const epicId of ROUTES) {
  await page.goto(`${WEB}/#/requirements/${epicId}`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="ticket-row"]', { timeout: 15000 }).catch(() => {})
  const drawn = await page.$$eval('[data-testid="delivery-badge"]',
    (els) => els.map((e) => e.getAttribute('data-state')))
  const rows = await page.$$eval('[data-testid="ticket-row"]', (els) => els.length)
  const served = await (await fetch(`${API}/api/requirements/${epicId}`)).json()
  const fromWire = [served.delivery?.state,
    ...(served.groups ?? []).flatMap((g) => (g.tickets ?? []).map((t) => t.delivery?.state))]
    .filter(Boolean)
  const badgeWords = new Set(words.map((w) => w.state))
  const expected = [...new Set(fromWire.filter((s) => badgeWords.has(s)))].sort()
  const painted = [...new Set(drawn)].sort()
  const offBadge = [...new Set(fromWire.filter((s) => !badgeWords.has(s)))].sort()
  say(`[${theme}] ${epicId} · ${rows} ticket rows · the tool says ${[...new Set(fromWire)].sort().join(' · ')}`)
  say(`[${theme}] ${epicId} · badges on screen = ${painted.join(' · ') || 'none'}`)
  say(`[${theme}] ${epicId} · screen == what the fixture-free derivation would expect: ${
    JSON.stringify(painted) === JSON.stringify(expected)}`)
  say(`[${theme}] ${epicId} · states with no badge word draw nothing: ${offBadge.join(' · ') || 'none'}`
    + ` · none of them painted = ${offBadge.every((s) => !painted.includes(s))}`)
  seen.push(...painted)
  await page.screenshot({ path: `${SHOTS}/requirement-${epicId}-${theme}.png` })
  }
  say(`[${theme}] over both requirements the screen drew ${
    [...new Set(seen)].sort().join(' · ')} — ${new Set(seen).size} badge states, not one`)

  // ── 3 · the ticket this round is about, on the screen a person opens ──────────────────────
  await page.goto(`${WEB}/#/tickets/WEB-FIX-373`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  const t = await page.locator('body').innerText()
  say(`[${theme}] ticket surface · WEB-FIX-373 drawn = ${t.includes('WEB-FIX-373')}`
    + ` · escaped_from SRV-FIX-453 on screen = ${t.includes('SRV-FIX-453')}`
    + ` · stages drawn = ${t.includes('DESIGN') && t.includes('GATE')}`)
  await page.screenshot({ path: `${SHOTS}/ticket-373-${theme}.png` })

  say(`[${theme}] page errors: ${errors.length ? errors.join(' | ') : 'none'}`)
  await ctx.close()
}

for (const theme of ['dark', 'light']) await drive(theme)
await browser.close()

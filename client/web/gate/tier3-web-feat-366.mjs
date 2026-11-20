/**
 * WEB-FEAT-366 — tier 3, driven against a REAL repository rather than the gate's fixture.
 *
 * §1 is a sentence about a person: *on the lanes surface, on the ticket rail and on the board's
 * lane cards, read under each lane's state line what its agent last said or last called; the line
 * moves while the agent works and nothing is reloaded; a lane whose agent has said nothing has no
 * line, not a blank one.* Every clause of that is walked here, on the built bundle, in front of
 * the compiled server, with a lane the tool claimed and an agent the tool spawned.
 *
 * `run-tier3-366.sh` sets it up and hands it the ports, the lane and the ticket. The gate drives
 * `gate/fixture-repo` — nine documents and an agent that prints nothing — which is exactly what
 * WEB-FIX-248 and WEB-FIX-261 say a green gate does not settle.
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from 'playwright-core'

const BASE = process.env.BASE
const API = process.env.API
const LANE = process.env.LANE
const TICKET = process.env.TICKET
const OUT = process.env.OUT
const CHROME = process.env.CHROME_PATH || '/opt/google/chrome/chrome'
const SHOTS = join(OUT, 'shots')
mkdirSync(SHOTS, { recursive: true })

let failed = 0
const say = (...a) => console.log(...a)
const ok = (what, cond, detail = '') => {
  say(`  ${cond ? 'PASS' : 'FAIL'}  ${what}${detail ? ` — ${detail}` : ''}`)
  if (!cond) failed += 1
}
const api = async (method, path, body) => {
  const r = await fetch(`${API}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const e9 = async () => ((await api('GET', '/api/lanes')).body?.lanes ?? [])
const rowFor = async (name) => (await e9()).find((l) => l.lane === name) ?? null

const browser = await chromium.launch({ executablePath: CHROME, headless: true,
                                        args: ['--no-sandbox', '--font-render-hinting=none'] })
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })

/** Every `GET /api/lanes` this page puts on the wire, so "nothing is reloaded" is measured. */
const e9Reads = []
page.on('request', (r) => {
  const u = new URL(r.url())
  if (u.pathname === '/api/lanes' && r.method() === 'GET') e9Reads.push(Date.now())
})

const shot = async (name) => {
  await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: false })
  return `${name}.png`
}
const setTheme = async (theme) => {
  await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForTimeout(400)
}
/** The words element's DOM text on whatever is on screen, one entry per card, in DOM order. */
const wordsOnScreen = () =>
  page.$$eval('[data-testid="lane-words"]', (ns) => ns.map((n) => n.textContent))

const served = await rowFor(LANE)
say('')
say('── what E9 says about the lane this walk is about ─────────────────────────────')
say(`  ${LANE} · ${served?.state?.process} · last_words ${JSON.stringify(served?.last_words)}`)
const said = served?.last_words?.text ?? null

say('')
say('── §4.14 · the lanes surface ──────────────────────────────────────────────────')
await page.goto(`${BASE}/#/lanes`, { waitUntil: 'networkidle' })
await page.waitForTimeout(600)
{
  const cards = await page.$$eval('[data-testid="lane-card"]', (ns) => ns.length)
  const drawn = await wordsOnScreen()
  ok('the surface draws the lane the tool claimed', cards >= 1, `${cards} card(s)`)
  ok('the words line on screen is E9\'s own string, verbatim',
    said !== null && drawn.length === 1 && drawn[0] === said,
    `screen ${JSON.stringify(drawn)} · E9 ${JSON.stringify(said)}`)
  // The `>` is the design's and belongs to CSS, so it is in what a person SEES and not in the
  // text — the two are asked separately because that split is the whole pre-decided rule.
  const seen = await page.$eval('[data-testid="lane-words"]',
    (n) => getComputedStyle(n, '::before').content)
  const clip = await page.$eval('[data-testid="lane-words"]',
    (n) => { const s = getComputedStyle(n)
             return `${s.whiteSpace} · ${s.overflow} · ${s.textOverflow}` })
  ok('a person sees the design\'s glyph in front of it, from CSS and not from the DOM text',
    seen.includes('›'), `::before ${seen}`)
  ok('and it is one clipped line', clip === 'nowrap · hidden · ellipsis', clip)
  say(`  shot ${await shot('lanes-dark')}`)
  await setTheme('light'); say(`  shot ${await shot('lanes-light')}`)
  await setTheme('dark')
}

say('')
say('── §4.4 · the ticket rail ─────────────────────────────────────────────────────')
await page.goto(`${BASE}/#/tickets/${encodeURIComponent(TICKET)}`, { waitUntil: 'networkidle' })
await page.waitForTimeout(700)
{
  const drawn = await wordsOnScreen()
  ok('the rail\'s card carries the same line as the lanes surface',
    drawn.length >= 1 && drawn[0] === said, `${JSON.stringify(drawn)}`)
  say(`  shot ${await shot('rail-dark')}`)
  await setTheme('light'); say(`  shot ${await shot('rail-light')}`)
  await setTheme('dark')
}

say('')
say('── §4.18 · the board ──────────────────────────────────────────────────────────')
await page.goto(`${BASE}/#/`, { waitUntil: 'networkidle' })
await page.waitForTimeout(700)
{
  const drawn = await wordsOnScreen()
  const seats = await page.$$eval('[data-testid="lane-free"]', (ns) => ns.length)
  ok('the board\'s lane card carries it as the state cell\'s second line',
    drawn.length >= 1 && drawn[0] === said, `${JSON.stringify(drawn)}`)
  ok('and the FREE seats beside it draw no words element at all — nothing said, nothing drawn',
    seats >= 1 && drawn.length === 1, `${seats} free seat(s) · ${drawn.length} words line(s)`)
  say(`  shot ${await shot('board-dark')}`)
  await setTheme('light'); say(`  shot ${await shot('board-light')}`)
  await setTheme('dark')
}

say('')
say('── the line MOVES while the agent works, and nothing is reloaded ──────────────')
await page.goto(`${BASE}/#/lanes`, { waitUntil: 'networkidle' })
await page.waitForTimeout(600)
{
  const before = await wordsOnScreen()
  const readsBefore = e9Reads.length
  // Wait for the AGENT to say something else — a fact the thing itself produces, never a sleep
  // that is read as one. The stand-in is replaying a capture, so its next block is on its way.
  let moved = null
  const deadline = Date.now() + 40_000
  while (Date.now() < deadline) {
    const now = await wordsOnScreen()
    if (now.length === 1 && now[0] !== before[0]) { moved = now[0]; break }
    await page.waitForTimeout(250)
  }
  ok('the line changed on the open page while the agent kept working',
    moved !== null, `${JSON.stringify(before)} → ${JSON.stringify(moved)}`)
  const wire = (await rowFor(LANE))?.last_words?.text ?? null
  ok('and what it changed to is what E9 now serves — one field, one string, no rewriting',
    moved !== null && moved === wire, `screen ${JSON.stringify(moved)} · E9 ${JSON.stringify(wire)}`)
  ok('NOTHING WAS RELOADED — no GET /api/lanes while the line moved',
    e9Reads.length === readsBefore, `${e9Reads.length - readsBefore} extra E9 read(s)`)
  say(`  shot ${await shot('lanes-moved-dark')}`)
}

say('')
say('── the negative, on the screen: a lane that has said nothing draws no line ────')
{
  // Released through the TOOL, which is the only thing that can answer whether a lane is live.
  const gone = await api('DELETE', `/api/lanes/${encodeURIComponent(LANE)}?force=true`)
  ok('E13 released the lane', gone.status === 200, `status ${gone.status}`)
  await page.goto(`${BASE}/#/lanes`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)
  const drawn = await wordsOnScreen()
  const rows = await e9()
  ok('with no lane holding words, the surface draws no words element anywhere — not an empty one',
    drawn.length === 0,
    `${rows.length} lane(s) on the wire · ${drawn.length} words line(s) on screen`)
  say(`  shot ${await shot('lanes-empty-dark')}`)
}

say('')
ok('no page error and no console error on any surface walked', errors.length === 0,
  errors.slice(0, 3).join(' | ') || 'none')

writeFileSync(join(OUT, 'e9-reads.json'), JSON.stringify({ reads: e9Reads.length }, null, 2))
await browser.close()
say('')
say(failed === 0 ? '── TIER 3 GREEN ──' : `── TIER 3: ${failed} failed ──`)
process.exit(failed === 0 ? 0 : 1)

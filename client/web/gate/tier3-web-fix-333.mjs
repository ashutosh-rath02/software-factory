/**
 * WEB-FIX-333 — tier 3, driven against THIS repository rather than the gate's fixture.
 *
 * §1 is a MEASUREMENT: it counts the `GET /api/tickets` a real browser puts on the wire while a
 * person types, against a repository whose catalog has no index and no cache, so every one of them
 * is a full sweep of the ticket tree. `gate/fixture-repo` holds nine tickets; the sweep this ticket
 * exists to stop only means anything at the size the real backlog is. So this script reproduces
 * §1's own measurement, at §1's own keystrokes, on the screen a person opens.
 *
 *   node gate/tier3-web-fix-333.mjs <base-url> <out-dir>
 *
 * It types with the KEYBOARD — `page.type` with a per-character delay — rather than setting the
 * field's value in one call. A debounce is a claim about the gaps between keystrokes, and a
 * single `fill()` has no gaps in it to measure.
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from 'playwright-core'

const BASE = process.argv[2]
const OUT = process.argv[3]
const CHROME = process.env.CHROME_PATH || '/opt/google/chrome/chrome'
if (!BASE || !OUT) { console.error('usage: tier3-web-fix-333.mjs <base-url> <out-dir>'); process.exit(2) }
mkdirSync(OUT, { recursive: true })

const say = (...a) => console.log(...a)
/** ≈100 WPM, the gap "ordinary typing speed" in criterion 1 means. Well inside the 300 ms window. */
const KEY_GAP_MS = 120
const WINDOW_MS = 300

const browser = await chromium.launch({ executablePath: CHROME, headless: true,
                                        args: ['--no-sandbox', '--font-render-hinting=none'] })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })

/** Every `GET /api/tickets` this page makes, with the `q` it carried and when. */
const asked = []
page.on('request', (r) => {
  const u = new URL(r.url())
  if (u.pathname === '/api/tickets') asked.push({ q: u.searchParams.get('q') ?? '', at: Date.now() })
})
const since = (mark) => asked.slice(mark)
const qs = (rows) => rows.map((r) => `q=${r.q === '' ? '<empty>' : r.q}`).join('  ')

const read = async () => await page.evaluate(() => ({
  pill: document.querySelector('[data-testid="tickets-by-epic"] .block-head .pill')?.textContent ?? null,
  result: document.querySelector('[data-testid="search-result"]')?.textContent ?? null,
  field: document.querySelector('[data-testid="tickets-search"]')?.value ?? null,
  rows: document.querySelectorAll('[data-testid="epic-group"] tbody tr').length,
  empty: document.querySelector('[data-testid="tickets-empty"]')?.textContent ?? null,
}))

const findings = []
const ok = (what, pass, detail) => {
  findings.push({ what, pass, detail })
  say(`  ${pass ? 'PASS' : 'FAIL'}  ${what} — ${detail}`)
}

const open = async () => {
  await page.goto('about:blank')
  await page.goto(`${BASE}/#/tickets`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="tickets-by-epic"] .block-head .pill')
  await page.waitForTimeout(WINDOW_MS + 200)     // let the mount's own read finish and go quiet
}

// ── 1 · a person types an id and the server is asked once (criterion 1) ─────────────────────
const ID = 'WEB-FIX-329'
await open()
const before = await read()
say(`\nthe screen a person opens: header pill ${before.pill} · ${before.rows} rows · `
    + `result line ${before.result === null ? 'absent' : `"${before.result}"`}`)
await page.screenshot({ path: join(OUT, 'tickets-before.png') })

let mark = asked.length
await page.click('[data-testid="tickets-search"]')
await page.type('[data-testid="tickets-search"]', ID, { delay: KEY_GAP_MS })
const duringTyping = since(mark).length
await page.waitForTimeout(WINDOW_MS + 1200)
const typed = since(mark)
const after = await read()
await page.screenshot({ path: join(OUT, 'tickets-typed.png') })

say(`\ntyped "${ID}" one character at a time, ${KEY_GAP_MS} ms apart:`)
say(`  requests fired while typing: ${duringTyping}`)
say(`  requests in all, typing + the pause after it: ${typed.length}   ${qs(typed)}`)
say(`  the screen now: ${after.rows} rows · field "${after.field}" · result line "${after.result}"`)

ok('typing an id asks the server exactly once, and with the whole id',
   typed.length === 1 && typed[0].q === ID,
   `${typed.length} request(s): ${qs(typed) || 'none'}`)

// The set is DERIVED from the value typed, never a list of the prefixes I thought of.
const prefixes = [...ID].map((_, i) => ID.slice(0, i + 1)).slice(0, -1)
const prefixesAsked = typed.filter((r) => prefixes.includes(r.q))
ok('and not one prefix on the way to it',
   prefixes.length === ID.length - 1 && prefixesAsked.length === 0,
   `${prefixes.length} prefixes checked (${prefixes[0]} … ${prefixes[prefixes.length - 1]}), `
   + `${prefixesAsked.length} of them asked`)

ok('the settled query arrived on its own — no blur, no Enter, nothing clicked (criterion 2)',
   duringTyping === 0 && typed.length === 1 && after.rows > 0 && after.result !== null,
   `0 requests during the keystrokes, 1 after the pause · ${after.rows} row(s) · `
   + `result line "${after.result}"`)

// ── 2 · MANDATORY NEGATIVE · a query abandoned mid-word is never asked (criterion 3) ────────
await open()
const backlogBefore = await read()
mark = asked.length
await page.click('[data-testid="tickets-search"]')
await page.type('[data-testid="tickets-search"]', '32', { delay: 60 })
await page.keyboard.press('Backspace')
await page.waitForTimeout(60)
await page.keyboard.press('Backspace')
await page.waitForTimeout(WINDOW_MS + 1500)
const abandoned = since(mark)
const backlogAfter = await read()
await page.screenshot({ path: join(OUT, 'tickets-abandoned.png') })

say(`\ntyped "3", "32", then deleted both, 60 ms apart:`)
say(`  requests since: ${abandoned.length}   ${qs(abandoned) || '(none)'}`)
say(`  the screen now: ${backlogAfter.rows} rows · field "${backlogAfter.field}" · `
    + `result line ${backlogAfter.result === null ? 'absent' : `"${backlogAfter.result}"`}`)

ok('an abandoned query is never asked — not for "3", not for "32", not at all',
   abandoned.length === 0,
   `${abandoned.length} request(s) after four keystrokes and ${WINDOW_MS + 1500} ms: `
   + `${qs(abandoned) || 'none'}`)
ok('and the surface is the unfiltered backlog it started as, saying nothing about a query',
   backlogAfter.rows === backlogBefore.rows && backlogAfter.field === ''
     && backlogAfter.result === null && backlogAfter.pill === backlogBefore.pill,
   `${backlogAfter.rows} rows (was ${backlogBefore.rows}) · pill ${backlogAfter.pill} `
   + `(was ${backlogBefore.pill}) · field "${backlogAfter.field}" · `
   + `result line ${backlogAfter.result === null ? 'absent' : `"${backlogAfter.result}"`}`)

// ── 3 · the surface is left mounted and untouched, and owns no clock (criterion 4) ──────────
await open()
mark = asked.length
say(`\nleft mounted and untouched for 30 s:`)
await page.waitForTimeout(30000)
const idle = since(mark)
ok('a surface at rest asks for nothing — the timer exists only while a value is unsettled',
   idle.length === 0, `${idle.length} request(s) in 30 s: ${qs(idle) || 'none'}`)

ok('and the page raised no error while any of it happened', errors.length === 0,
   errors.length ? errors.join(' | ') : 'no page error, no console error')

writeFileSync(join(OUT, 'tier3-findings.json'),
              JSON.stringify({ base: BASE, findings, asked }, null, 2))

const failed = findings.filter((f) => !f.pass)
say(`\n${findings.length - failed.length}/${findings.length} checks passed`)
await browser.close()
process.exit(failed.length ? 1 : 0)

#!/usr/bin/env node
/**
 * WEB-FEAT-006 — the browser gate.
 *
 * §1: "a headless-browser gate loading the BUILT client against the REAL C++ server pointed at a
 * fixture repository". A green unit suite is not this ticket's gate; this is. Unit tests against a
 * stubbed DOM prove code paths — a screen has already shipped from this project with 1.12:1
 * contrast and passing tests.
 *
 * It drives the system Chrome through playwright-core, so nothing is downloaded and the browser
 * is the one on this machine.
 *
 *   node gate/browser-gate.mjs <base-url> <screenshot-dir>
 *
 * Run it before the components exist and it fails — that failure is what proves it runs.
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { dirname, isAbsolute, join, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'
import { settleOnLoaded } from './wait.mjs'
import { deferred, schemaCoverage, sweepRequest } from './sweep.mjs'
import { LIVE_SURFACES, SURFACES, TICKETS_QUERY, resolveScope, withAPageOpen,
  withTheSearchBoxFilled } from './surfaces.mjs'
import { alarmStates, judgeDeliveryBadges, statesIn } from './delivery-states.mjs'
import { deliveryCheck, orchardCheck, producingControlCheck, standaloneIsLast } from './fixture-checks.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const SCHEMA = JSON.parse(readFileSync(join(HERE, '../../../docs/technical/contracts/http.schema.json'), 'utf8'))

// Given, never guessed — the same rule the server applies to its repository root. A default
// here is a gate that quietly drives whatever page another session left running.
const BASE = process.argv[2]
if (!BASE) {
  console.error('usage: browser-gate.mjs <base-url> [screenshot-dir] — run it through gate/run-gate.sh')
  process.exit(2)
}
const SHOTS = process.argv[3] || 'shots'
// The repository the server was pointed at — SRV-FIX-059 round 5 builds a leftover in it.
const FIXTURE = process.argv[4] || ''
const CHROME = process.env.CHROME_PATH || '/usr/bin/google-chrome'

/**
 * WHAT THIS RUN PHOTOGRAPHS (WEB-FEAT-379).
 *
 * `--surfaces a,b` / `--themes dark` on the argv `run-gate.sh` passes through, or
 * `FACTORY_GATE_SURFACES` / `FACTORY_GATE_THEMES`, resolved by the one function in
 * `surfaces.mjs` — the same one the script calls before it takes the browser lock, so there is one
 * implementation of *the argument wins over the environment* rather than two that agree.
 *
 * With nothing named this is the whole declared set and `inScope()` answers true to every name, so
 * an unscoped run is the run this gate has always done.
 */
let SCOPE
try {
  SCOPE = resolveScope({ argv: process.argv, env: process.env })
} catch (e) {
  console.error(e instanceof Error ? e.message : String(e))
  process.exit(2)
}

/**
 * A check this run is not making, because the surface it is about is outside the scope.
 *
 * NOT `skipped()` below, which is a FAILURE with a reason attached: that one exists because a
 * section whose setup did not happen must not read as green (`WEB-FIX-037`). This one is a
 * deliberate narrowing a person asked for, so it is counted on its own line and decides nothing —
 * §1 criterion 3, *a scoped run's exit status is decided by the checks it ran*. It is printed one
 * line per check rather than summed silently, because `docs/operations/ESCAPES.md`, 2026-07-29 is what a gate
 * assertion nobody notices did not run costs.
 */
function outOfScope(name, surface) {
  SCOPE.skip(name, surface)
  console.log(`  SKIP(scope)  ${name} — ${surface} is outside this run's scope`)
  return undefined
}

/** The themes to sweep `surface` in — none, and one skip line, when it is out of scope. */
function themesFor(surface, name) {
  const wanted = Array.isArray(surface) ? surface : [surface]
  if (wanted.some((s) => SCOPE.inScope(s))) return SCOPE.themes
  outOfScope(name, wanted.join(' / '))
  return []
}

/**
 * Does this run's scope want a whole SECTION? — and one `SKIP(scope)` line when it does not.
 *
 * A section is named for the surfaces it DRIVES, not for the ones it photographs: §1 criterion 3
 * is about *an assertion that needs a surface outside the scope*, and section 8f's composer needs
 * `#/tickets/<id>/session` whether or not it takes a picture of it. This is the difference between
 * a scoped run costing six minutes and costing three.
 */
function sectionInScope(name, ...surfaces) {
  if (surfaces.some((s) => SCOPE.inScope(s))) return true
  outOfScope(name, surfaces.join(' / '))
  return false
}

/** `ok()`, unless the surface it is about is outside the scope. */
function okScoped(surface, name, condition, detail = '') {
  if (!SCOPE.inScope(surface)) return outOfScope(name, surface)
  return ok(name, condition, detail)
}

/**
 * How a check's own name says which themes it covered, so an unscoped run's log is unchanged and a
 * scoped one does not claim both themes over one.
 */
function themeWords() {
  return SCOPE.themes.length === SCOPE.declared.themes.length
    ? 'both themes' : `the ${SCOPE.themes.join(' and ')} theme`
}

/**
 * Break mode (WEB-FEAT-083). `drop-shot` suppresses exactly ONE screenshot write — section 9's
 * `shell-and-requirements` in the dark theme — so that section 11 can be watched failing. The env
 * var is the ordinary switch; the `gate/GATE_BREAK` file is its equivalent for a shell that cannot
 * export one. A leftover file cannot hide: every break-mode run announces itself and ends red.
 */
const BREAK = process.env.GATE_BREAK
  || (existsSync(join(HERE, 'GATE_BREAK')) ? readFileSync(join(HERE, 'GATE_BREAK'), 'utf8').trim() : '')

/**
 * When THIS run started. Section 11 judges every photograph against it, because the shots
 * directory outlives runs: the TEST-stage capture for WEB-FEAT-083 dropped one write and the
 * previous run's PNG of the same name sat on disk satisfying any existence check. A photograph
 * older than the run that claims it is not evidence of this run.
 */
const GATE_START = Date.now()

let failures = 0
let checks = 0
let skips = 0

/**
 * Which nodes of `http.schema.json` this run actually judged, and which it deferred (WEB-FIX-213).
 *
 * `SRV-FIX-188` added `E26` to a set the sweep quantifies over and nothing noticed that the sweep
 * could not ask for it — the failure looked like a schema mismatch rather than a wrong route. So
 * the run keeps a ledger and the last check requires the two to cover the file: a node deferred to
 * a section that does not judge it, or a node added to the schema that no section reaches, is red.
 */
const judged = new Set()
const deferredNodes = new Map()

/**
 * Every photograph the gate takes goes through here (WEB-FEAT-083).
 *
 * The intent is recorded BEFORE the write, in `driven`, and the row only reaches the index after
 * `page.screenshot` returned — so a write that throws, or one break mode suppressed, leaves a
 * driven surface with no row, which is exactly the hole section 11 exists to name. A screenshot
 * call outside this helper is a screenshot nothing asserts; there are none.
 */
const shotIndex = []
const driven = []
async function shot(page, surface, theme, state = 'static', laneState) {
  // WEB-FEAT-379 — every photograph this gate takes comes through here, so this one line is
  // what makes `shots/index.json` hold EXACTLY the scope. Before `driven`, because a surface
  // nobody drove is not a hole for section 11 to name.
  if (!SCOPE.inScope(surface)) return outOfScope(`the photograph of ${surface}`, surface)
  driven.push({ surface, theme, state })
  if (BREAK === 'drop-shot' && surface === 'shell-and-requirements' && theme === 'dark') return
  const file = `${surface}-${theme}.png`
  const text = `${surface}-${theme}.txt`
  const row = { surface, state, theme, file, text }
  if (state === 'live') row.lane_state = laneState ?? ''
  try {
    await page.screenshot({ path: join(SHOTS, file) })
    // WEB-FEAT-085 round 3 — what the surface RENDERED, captured at the same instant as the
    // picture of it. Without this an audit row is checkable only by its own author: the three
    // blind analyses found that §1's original criteria were all relations among artefacts one
    // session writes, so every one of them could pass on a table describing an audit that never
    // happened. A row that quotes this file can be falsified by a second party without re-judging
    // the image. It does NOT prove anyone looked at pixels, and §1 says so out loud.
    writeFileSync(join(SHOTS, text), await page.evaluate(() => document.body.innerText), 'utf8')
    shotIndex.push(row)
  } catch (e) {
    console.log(`  WARN  screenshot ${file} did not happen: ${e}`)
  }
}

function ok(name, condition, detail = '') {
  checks += 1
  if (condition) {
    console.log(`  PASS  ${name}${detail ? ` — ${detail}` : ''}`)
  } else {
    failures += 1
    console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`)
  }
}

/**
 * A section whose setup did not happen (WEB-FIX-037).
 *
 * Several sections here are guarded — `if (silent) { … }`, `if (producing) { … }` — because the
 * lane they need may not have been claimed. A guard that simply omits the block makes a check that
 * DID NOT RUN indistinguishable from a check that PASSED, and the summary line still reads
 * `n/n checks passed`. That is the same shape as the escape this file's own 8f came from: five
 * lanes wrote assertions, none of them executed one, and every report was honestly green.
 *
 * So a skip is a failure with its reason attached. There is no flag to make it quiet.
 */
function skipped(name, why) {
  checks += 1
  failures += 1
  skips += 1
  console.log(`  SKIP  ${name} — NOT RUN: ${why}`)
}

/**
 * Enough JSON Schema to judge a response body: type, required, properties, items and $ref.
 *
 * The client's types are GENERATED from this schema, so the schema is what the UI believes about
 * the wire. Believing it is not the same as checking it — a transcription of the C++ that nothing
 * runs against the C++ is a second source of truth waiting to disagree. This runs it against the
 * real server, so a drift turns a gate red instead of blanking a screen.
 */
function validate(node, value, where, out) {
  if (node.$ref) return validate(SCHEMA.$defs[node.$ref.split('/').pop()], value, where, out)
  const types = Array.isArray(node.type) ? node.type : [node.type]
  const actual = value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value
  const matches = types.some((t) =>
    t === undefined
    || (t === 'integer' && Number.isInteger(value))
    || (t === 'number' && actual === 'number')
    || t === actual)
  if (!matches) { out.push(`${where}: expected ${types.join('|')}, got ${actual}`); return out }
  if (actual === 'array' && node.items) {
    value.forEach((item, i) => validate(node.items, item, `${where}[${i}]`, out))
  }
  if (actual === 'object') {
    for (const key of node.required ?? []) {
      if (!(key in value)) out.push(`${where}: missing required "${key}"`)
    }
    for (const [key, child] of Object.entries(node.properties ?? {})) {
      if (key in value) validate(child, value[key], `${where}.${key}`, out)
    }
  }
  return out
}

/** Click, and report a miss instead of crashing the run on it. */
async function tryClick(page, selector) {
  try {
    await page.click(selector, { timeout: 8000 })
    return true
  } catch {
    failures += 1
    console.log(`  FAIL  could not click ${selector}`)
    return false
  }
}

/**
 * WCAG relative luminance of an `rgb(r, g, b)` string.
 *
 * Only that one form, and it throws on anything else. The page hands over colours it has already
 * flattened to 8-bit sRGB against the ground behind them, so a string this cannot read is a bug in
 * the measurement rather than a colour to wave through — and the version of this function that
 * returned `null` was skipping every `oklch()` in the palette without saying so.
 */
function luminance(colour) {
  const m = colour.match(/^rgb\((\d+), (\d+), (\d+)\)$/)
  if (!m) throw new Error(`unmeasurable colour: ${colour}`)
  const lin = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
  const [r, g, b] = m.slice(1, 4).map((v) => lin(Number(v) / 255))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(fg, bg) {
  const a = luminance(fg)
  const b = luminance(bg)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

/**
 * WCAG's floor for this text: 4.5:1 normally, 3.0:1 for the small mono type the design uses for
 * its state words, per WEB-FIX-026 §1. There is no exemption list and no third case — a colour
 * that cannot clear its floor is a red build, which is the only shape of this check that cannot
 * grow an escape hatch.
 */
const floorFor = (size) => (size >= 14 ? 4.5 : 3.0)

/**
 * Every visible text node's foreground against the first opaque background behind it.
 *
 * No exemption list. WEB-FEAT-006 shipped this with a floor of 2.5:1 and one token waved through,
 * because §1 had frozen the design and the design painted `not started` at 1.75:1; WEB-FIX-026
 * changed the design, so the floor is WCAG's and the list is gone. A check with a list of things
 * it is allowed to ignore is a check the next unreadable colour gets added to.
 *
 * Colours are flattened in the page, by painting them, rather than parsed here. `getComputedStyle`
 * returns `oklch(...)` verbatim for the four state tones, and the string parser this replaces
 * returned `null` for every one of them — which the caller silently skipped. Painting the
 * foreground over the ground behind it also resolves alpha correctly, and an unpaintable colour
 * comes back as the ground itself, i.e. 1:1, i.e. loudly failing.
 */
async function measureContrast(page) {
  return page.evaluate(() => {
    const ctx = document.createElement('canvas').getContext('2d', { willReadFrequently: true })
    const pixel = () => {
      const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data
      return { rgb: `rgb(${r}, ${g}, ${b})`, a }
    }
    /** Is this colour fully opaque? A ground that is not cannot be the ground. */
    const opaque = (colour) => {
      ctx.clearRect(0, 0, 1, 1)
      ctx.fillStyle = 'rgba(0, 0, 0, 0)'
      ctx.fillStyle = colour
      ctx.fillRect(0, 0, 1, 1)
      return pixel().a === 255
    }
    /** `colour` composited over `over`, as 8-bit sRGB. */
    const flatten = (colour, over) => {
      ctx.fillStyle = over
      ctx.fillRect(0, 0, 1, 1)
      ctx.fillStyle = colour
      ctx.fillRect(0, 0, 1, 1)
      return pixel().rgb
    }
    /** Enough to find the element again from the log: `span.tkt-state "not started"`. */
    const name = (el) => el.tagName.toLowerCase()
      + (typeof el.className === 'string' && el.className ? `.${el.className.trim().split(/\s+/).join('.')}` : '')
    const out = []
    for (const el of document.querySelectorAll('body *')) {
      const text = Array.from(el.childNodes)
        .filter((n) => n.nodeType === 3 && n.textContent.trim())
        .map((n) => n.textContent.trim()).join(' ')
      if (!text) continue
      const box = el.getBoundingClientRect()
      if (box.width < 1 || box.height < 1) continue
      const style = getComputedStyle(el)
      if (style.visibility === 'hidden' || style.opacity === '0') continue
      let ground = 'rgb(0, 0, 0)'
      for (let node = el; node; node = node.parentElement) {
        const c = getComputedStyle(node).backgroundColor
        if (opaque(c)) { ground = flatten(c, 'rgb(0, 0, 0)'); break }
      }
      out.push({
        el: name(el), text: text.slice(0, 48),
        fg: flatten(style.color, ground), bg: ground,
        size: parseFloat(style.fontSize),
      })
    }
    return out
  })
}

async function main() {
  mkdirSync(SHOTS, { recursive: true })
  // WEB-FEAT-379 — the shots directory is this run's output, so it starts as this run's output and
  // nothing else. Without it a scoped run's `index.json` would name two files beside sixty a
  // previous run left, and section 11's stray check would be red on every scoped run.
  for (const f of readdirSync(SHOTS)) {
    if (/\.(png|txt)$/.test(f) || f === 'index.json' || f === 'scope.json') {
      rmSync(join(SHOTS, f), { force: true })
    }
  }
  // …and what the scope WAS is written beside them, because a corpus that is a subset by design is
  // indistinguishable from a corpus with holes in it (`tests/gate-surfaces.test.ts` reads this).
  writeFileSync(join(SHOTS, 'scope.json'), `${JSON.stringify({
    surfaces: SCOPE.surfaces, themes: SCOPE.themes,
    surfacesScoped: SCOPE.surfacesScoped, themesScoped: SCOPE.themesScoped,
  }, null, 2)}\n`)
  console.log(`  ${SCOPE.line()}`)
  const browser = await chromium.launch({
    executablePath: CHROME, headless: true, args: ['--no-sandbox', '--font-render-hinting=none'],
  })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

  // Every frame this page was SENT (WEB-FEAT-079). Standing instrumentation, and it earns its
  // place: when a screen does not show a fact the server holds, the only two suspects are "the
  // frame never came" and "the client did nothing with it", and nothing else here can tell them
  // apart. Registered at page creation because `websocket` fires on the socket's OPENING.
  const received = []
  page.on('websocket', (ws) => ws.on('framereceived', (data) => received.push(String(data.payload))))

  // An uncaught exception is always a failure. A console error is a failure unless it is the
  // browser's own note about a response the product deliberately expects to be missing — E6's
  // 404 IS 4.9's empty state (SPEC.md §4.9), and no amount of correct behaviour can stop Chrome
  // logging a failed request. So the two are counted separately and the exception is named.
  const EXPECTED_404 = ['/api/tickets/TKT-142/report']
  const pageErrors = []
  const consoleErrors = []
  const badResponses = []
  // Section 8b cuts the network on purpose, and Chrome logs a failed WebSocket as an error of its
  // own. The window in which that is expected is narrow and named, rather than a permanent hole in
  // the check: outside it, a socket error is still a failure.
  let offlineWindow = false
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  page.on('console', (m) => {
    if (m.type() !== 'error') return
    const url = m.location()?.url ?? ''
    if (EXPECTED_404.some((p) => url.includes(p))) return
    if (offlineWindow && /websocket|failed to fetch|net::err/i.test(m.text())) return
    consoleErrors.push(`${m.text()} @ ${url}`)
  })
  page.on('response', (r) => {
    if (r.status() < 400) return
    const url = new URL(r.url()).pathname
    if (!EXPECTED_404.includes(url)) badResponses.push(`${r.status()} ${url}`)
  })

  console.log(`\n=== WEB-FEAT-006 browser gate · ${BASE} · ${new Date().toISOString()}\n`)
  if (BREAK) console.log(`  BREAK MODE ${BREAK} — this run must end red, naming the hole\n`)

  // ── 1 · the requirements list renders its rows ────────────────────────────────────────────
  await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="requirement-row"]', { timeout: 10000 }).catch(() => {})
  const rows = await page.$$('[data-testid="requirement-row"]')
  // five documents under docs/requirements, and the synthetic `standalone` row after them
  ok('requirements list renders its rows', rows.length === 6, `${rows.length} rows`)
  const listText = await page.textContent('[data-testid="surface"]').catch(() => '')
  ok('a row carries the requirement it came from',
    listText.includes('Never ship an artifact the working tree cannot reproduce'))
  const count = await page.textContent('[data-testid="count-line"]').catch(() => '')
  ok('the count line comes from the server', /6 requirements/.test(count), count.trim())

  // ── 1b · the tickets no requirement claims are on the screen (WEB-FEAT-041) ───────────────
  //
  // TKT-160 in the fixture says `parent: standalone`. Before this it was on no list at all, so
  // the assertion is not "a row exists" but "the row is last, says how many are in it, and opens".
  const orphanRow = await page.$('[data-testid="requirement-row"][data-standalone="true"]')
  ok('the standalone group has a row of its own', orphanRow !== null)
  const orphanText = orphanRow ? (await orphanRow.textContent()).trim() : ''
  // `1 ticket` and not `1 tickets`: the columns are adjacent spans, so the text runs together
  // (`…core1 ticket10s`) and only the negative lookahead separates singular from plural here.
  ok('and it names itself and its size',
    /Standalone/.test(orphanText) && /1 ticket(?!s)/.test(orphanText), orphanText)
  const lastIsStandalone = await page.$eval('[data-testid="rows"]', standaloneIsLast)
    .catch(() => false)
  ok('after the real requirements, never among them', lastIsStandalone === true)

  await page.goto(`${BASE}/#/requirements/standalone`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="ticket-row"]', { timeout: 10000 }).catch(() => {})
  const orphanTickets = await page.$$eval('[data-testid="ticket-row"] .tkt-id',
    (n) => n.map((e) => e.textContent.trim())).catch(() => [])
  ok('it opens on the tickets it stands for', orphanTickets.join(',') === 'TKT-160',
    orphanTickets.join(' · ') || 'no ticket rows')
  const orphanGroup = await page.textContent('[data-testid="group-name"]').catch(() => '')
  ok('grouped by workspace like any requirement', orphanGroup.trim() === 'core', orphanGroup.trim())

  await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="requirement-row"]', { timeout: 10000 }).catch(() => {})

  // ── 1c · `done` keeps what landed, and only that (SRV-FEAT-165) ───────────────────────────
  //
  // The fixture holds exactly ONE requirement every ticket of which landed: REQ-031, whose single
  // TKT-170 has its report on `main`. REQ-011 is the discriminating row — TKT-128 landed and
  // TKT-133 never started — and `SRV-FIX-166` is the reason it must be DROPPED: `done` means the
  // work is on the base branch, and one ticket of two is not that. REQ-014 aggregates to `stalled`,
  // REQ-009 to `running`, the standalone row to `not_started`.
  //
  // So this one assertion separates three wrong filters from the right one: one that keeps
  // everything, one that keeps nothing, and one that keeps a requirement on the strength of a
  // single landed child.
  const idsOf = () => page.$$eval('[data-testid="requirement-row"] .req-id',
    (n) => n.map((e) => e.textContent.trim()))
  // The surface's own loading state, sampled with the row count so a reader of one is a reader of
  // the other. `useEndpoint` clears its result whenever its key changes, and the key carries the
  // filter, so every press of a chip empties this list before it refills it.
  const sampleRows = () => page.evaluate(() => ({
    loading: Boolean(document.querySelector('[data-testid="placeholder"]')),
    rows: document.querySelectorAll('[data-testid="requirement-row"]').length,
  }))
  // Wait for the ANSWER and then for the surface to settle on it — never for the row count to
  // become "different from what it was", which the empty loading state satisfies on its way past.
  // Round 1 waited on the change and QA failed this block twice on a build where it was correct
  // (SRV-FEAT-165 QA finding 1); `tests/gate-wait.test.ts` holds the trajectory it measured.
  const settleAfter = async (chip, marker) => {
    const answered = page.waitForResponse(
      (r) => r.url().includes('/api/requirements') && r.url().includes(marker),
      { timeout: 10000 }).catch(() => null)
    await tryClick(page, chip)
    await answered
    return settleOnLoaded(sampleRows)
  }

  const allIds = await idsOf()
  const doneSettled = await settleAfter('.filter-chip:has-text("done")', 'filter=done')
  ok('the surface settled before the `done` rows were read',
    doneSettled.timedOut === false,
    `loading=${doneSettled.loading} rows=${doneSettled.rows} timedOut=${doneSettled.timedOut}`)

  const doneIds = await idsOf()
  ok('the `done` chip keeps only the requirements EVERY ticket of which landed',
    doneIds.join(',') === 'REQ-031',
    `${doneIds.join(' · ') || 'no rows'} — out of ${allIds.join(' · ')}`)
  ok('and a requirement with one landed ticket and one never started is not among them',
    !doneIds.includes('REQ-011'),
    `REQ-011 ${doneIds.includes('REQ-011') ? 'survived the filter' : 'was dropped'}`)
  const doneBadges = await page.$$eval(
    '[data-testid="requirement-row"] [data-testid="delivery-badge"]',
    (n) => n.map((e) => e.getAttribute('data-state')))
  ok('and every row it kept says `done` on the badge a person reads',
    doneBadges.length > 0 && doneBadges.every((s) => s === 'done'),
    doneBadges.join(' · ') || 'no badges')
  const doneCount = (await page.textContent('[data-testid="count-line"]').catch(() => '')).trim()
  ok('the count line is the filtered count and comes from the server',
    new RegExp(`^${doneIds.length} requirements`).test(doneCount), doneCount)

  // The `done` state photographed, in both themes. Round 1 added three assertions about a state
  // that appeared in no screenshot on either theme, which QA named in its visual pass: every other
  // surface the sweep drives has one.
  for (const theme of themesFor('requirements-filtered-done',
    'the `done` state is photographed in each theme')) {
    await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="requirement-row"], [data-testid="empty"]',
      { timeout: 10000 }).catch(() => {})
    const shown = await settleAfter('.filter-chip:has-text("done")', 'filter=done')
    await page.mouse.move(0, 0)
    await page.waitForTimeout(200)
    const applied = await page.getAttribute('html', 'data-theme')
    await shot(page, 'requirements-filtered-done', theme)
    ok(`the \`done\` state is photographed in the ${theme} theme`,
      applied === theme && shown.timedOut === false && shown.rows > 0,
      `theme ${applied}, ${shown.rows} rows, settled=${!shown.timedOut}`)
  }
  await page.evaluate((t) => localStorage.setItem('factory.theme', t), SCOPE.themes[0])
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="requirement-row"]', { timeout: 10000 }).catch(() => {})

  const restored = await settleAfter('.filter-chip:has-text("all")', 'filter=all')
  ok('the surface settled before the restored rows were read', restored.timedOut === false,
    `loading=${restored.loading} rows=${restored.rows} timedOut=${restored.timedOut}`)
  const restoredIds = await idsOf()
  ok('and `all` gives the whole list back', restoredIds.join(',') === allIds.join(','),
    restoredIds.join(' · '))

  // ── 2 · the socket chip reads open ────────────────────────────────────────────────────────
  const chip = (await page.textContent('[data-testid="socket-chip"]').catch(() => '')).trim()
  ok('the socket chip reads open', /socket live/.test(chip), chip)

  // ── 3 · a requirement opens ───────────────────────────────────────────────────────────────
  await tryClick(page, '[data-testid="requirement-row"]:has-text("REQ-014")')
  await page.waitForSelector('[data-testid="workspace-group"]', { timeout: 10000 }).catch(() => {})
  const groups = await page.$$('[data-testid="workspace-group"]')
  ok('a requirement opens, grouped by workspace', groups.length === 3, `${groups.length} groups`)
  const groupNames = await page.$$eval('[data-testid="group-name"]', (n) => n.map((e) => e.textContent.trim()))
  ok('the undeclared workspace lands in a final `unassigned` group',
    groupNames[groupNames.length - 1] === 'unassigned', groupNames.join(' · '))
  const seqs = await page.$$eval('[data-testid="workspace-group"]', (n) => n.map((e) => e.dataset.sequence))
  ok('each group carries its OWN stage sequence', new Set(seqs).size === seqs.length,
    seqs.join(' | '))

  // ── 4 · a ticket's markdown appears ───────────────────────────────────────────────────────
  //
  // A row lands on the ticket's FIRST tab, which WEB-FEAT-014 made SESSION — §4.6 is the first
  // surface the design draws and the one a person opening a ticket wants. TICKET.MD is now a tab
  // away, and it is reached the way a person reaches it, by clicking the tab: that also proves the
  // registry WEB-FEAT-013 built renders a row that actually navigates, which no unit test can.
  await tryClick(page, '[data-testid="ticket-row"]:has-text("TKT-142")')
  await tryClick(page, '.tabs [data-tab="ticket"]')
  await page.waitForSelector('[data-testid="doc-body"]', { timeout: 10000 }).catch(() => {})
  const onTicketTab = await page.getAttribute('.tabs [data-tab="ticket"]', 'aria-selected')
  ok('clicking a tab in the registry row actually goes there', onTicketTab === 'true', String(onTicketTab))
  await page.waitForSelector('[data-testid="doc-body"] h1', { timeout: 10000 }).catch(() => {})
  const heading = (await page.textContent('[data-testid="doc-body"] h1').catch(() => '')).trim()
  ok("a ticket's markdown appears",
    heading === 'TKT-142 — Guard against flashing a stale build artifact', heading)
  const docPath = (await page.textContent('[data-testid="doc-path"]').catch(() => '')).trim()
  ok('with its path and the words `read only`',
    /TKT-142-stale-artifact-guard\.md/.test(docPath) && /read only/.test(docPath), docPath)
  const listItems = await page.$$('[data-testid="doc-body"] ol li')
  ok('the markdown is rendered, not dumped', listItems.length === 3, `${listItems.length} <li>`)

  // the markdown decision, in the real browser: embedded HTML is escaped, never executed
  const pwned = await page.evaluate(() => window.__pwned)
  const scripts = await page.$$('[data-testid="doc-body"] script')
  const bodyText = await page.textContent('[data-testid="doc-body"]')
  ok('embedded HTML is escaped, not executed',
    pwned === undefined && scripts.length === 0 && bodyText.includes('<b>embedded HTML</b>'))

  // ── 5 · the report, present and absent ────────────────────────────────────────────────────
  await page.goto(`${BASE}/#/tickets/TKT-139/report`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="evidence-banner"]', { timeout: 10000 }).catch(() => {})
  const banner = (await page.textContent('[data-testid="evidence-banner"]').catch(() => '')).trim()
  ok('a report that cites a run says so',
    /cites run run-2026-07-27-0003/.test(banner), banner)

  await page.goto(`${BASE}/#/tickets/TKT-128/report`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="evidence-banner"]', { timeout: 10000 }).catch(() => {})
  const alarm = await page.getAttribute('[data-testid="evidence-banner"]', 'data-alarm').catch(() => null)
  const alarmText = (await page.textContent('[data-testid="evidence-banner"]').catch(() => '')).trim()
  ok('a report that cites nothing carries the alarm',
    alarm === 'true' && /claims done · cites no run/.test(alarmText), alarmText)
  const alarmBorder = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="evidence-banner"]')
    return el ? getComputedStyle(el).borderTopWidth : null
  })
  ok('and the alarm is a border the browser actually paints',
    alarmBorder !== null && parseFloat(alarmBorder) > 0, String(alarmBorder))

  await page.goto(`${BASE}/#/tickets/TKT-142/report`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="report-empty"]', { timeout: 10000 }).catch(() => {})
  const empty = (await page.textContent('[data-testid="report-empty"]').catch(() => '')).trim()
  ok('an absent report is the empty state, not an error',
    /NO DELIVERY REPORT/.test(empty) && /with or without the evidence to back it/.test(empty))

  // ── 6 · the vocabulary sheet ──────────────────────────────────────────────────────────────
  await page.goto(`${BASE}/#/vocabulary`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="axis-process"]', { timeout: 10000 }).catch(() => {})
  const axisValues = await page.$$('[data-testid="axis-value"]')
  ok('the vocabulary sheet renders all three axes', axisValues.length === 13,
    `${axisValues.length} values`)
  // §4.11's sheet is the product's own delivery vocabulary, shipped so an implementation is gated
  // against it, and section 7 derives its domain from it (WEB-FIX-373). The VALUE, not the label
  // beside it: the five labels happen to equal the five values today and the design owns the
  // label, so a gate reading one to learn the other would rest on that accident.
  const deliveryWords = await page.$$eval('[data-testid="delivery-value"]',
    (els) => els.map((e) => e.getAttribute('data-state')).filter(Boolean))
  const stageStrip = (await page.textContent('[data-testid="stage-strip"]').catch(() => '')).trim()
  ok("the stage sample is the fixture repository's own stages",
    /SPEC/.test(stageStrip) && /DELIVER/.test(stageStrip), stageStrip)

  // ── 6b · the live wire against the schema the client's types were generated from ─────────
  //
  // ASKED BY THE VERB THE NODE DECLARES (WEB-FIX-213). This loop used to GET everything, because
  // the schema recorded a path and no verb. `E26` is `POST /api/tickets/ids`, so the GET reached
  // `GET /api/tickets/{id}` — a different endpoint — with `id` bound to the literal string `ids`,
  // and this check validated THAT endpoint's 404 against `E26`'s schema. It read as a schema
  // mismatch, which is why it survived: `missing required "id" · missing required "prefix"` is
  // what a wrong route looks like when nothing here knows there was a route to get wrong.
  //
  // `{run_id}` and `{lane}` name things that do not exist until a run has been recorded and a
  // lane claimed, so those two are checked in section 8d instead — against real ids, once there
  // are some. The nodes that says so is the node itself (`checked_by`), not a set kept here:
  // a private `const LATER = new Set([…])` is a set nobody can test and one that grows by one
  // line. Nothing is skipped silently either way — the ledger at the end of this run requires
  // EVERY node to have been judged somewhere, so an endpoint that reaches neither place is red.
  for (const [id, node] of Object.entries(SCHEMA.responses)) {
    const why = deferred(node)
    if (why) { deferredNodes.set(id, why); continue }
    const { method, path, status, body: send } = sweepRequest(id, node)
    ok(`${id}'s path has no placeholder left in it`, !/\{/.test(path), path)
    const response = await fetch(`${BASE}${path}`, {
      method,
      headers: send ? { 'content-type': 'application/json' } : undefined,
      body: send ? JSON.stringify(send) : undefined,
    })
    const body = await response.json()
    const problems = validate(node, body, id, [])
    judged.add(id)
    ok(`${id} ${method} ${path} matches http.schema.json`,
      response.status === status && problems.length === 0,
      problems.slice(0, 3).join(' · ') || `HTTP ${response.status} (want ${status})`)
  }

  // The mandatory negative, live (WEB-FIX-213 §1). A check that only asserted `status === 200`
  // would pass on any 200 from any route, so this one asserts the ANSWER IS E26'S OWN: the four
  // members `AllocateTicketId` renders, and no `error` — which is what the 404 this used to read
  // carried instead. Against the implementation that keeps the GET the body here is
  // `{"error":{"code":"not_found","message":"no ticket with id ids"…}}`.
  const e26 = sweepRequest('E26', SCHEMA.responses.E26)
  const allocated = await fetch(`${BASE}${e26.path}`, {
    method: e26.method,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(e26.body),
  })
  const allocatedBody = await allocated.json().catch(() => null)
  ok('test_a_node_whose_path_collides_with_another_route_is_not_read_as_that_route',
    allocated.status === 201
      && allocatedBody?.error === undefined
      && typeof allocatedBody?.id === 'string'
      && typeof allocatedBody?.prefix === 'string'
      && typeof allocatedBody?.series === 'string'
      && Number.isInteger(allocatedBody?.number),
    `HTTP ${allocated.status} ${JSON.stringify(allocatedBody)}`)

  // ── 6c · the tickets list reports the server's count (WEB-FIX-337) ───────────────────────
  //
  // Section 9 photographs this surface; a photograph is not an assertion, and a screen drawing
  // `0 of 0` over three rows is a picture as green as any other. `TicketsByEpic` sends the query
  // to the SERVER — the counts beside the controls are the server's facets, because a client
  // filtering its own rows would draw counts for one set and rows for another — so the two numbers
  // on the result line and the collection under them are ONE claim and are asserted as one
  // (`_TEMPLATE.md` §2: a claim about a collection is asserted in the same case as the collection).
  //
  // The E29 answers are read here rather than derived from the screen, and the two of them are
  // taken with the query and without it, so the matched total and the whole backlog are different
  // numbers: a surface reading the wrong one of those two cannot pass this.
  {
    const tickets = async (q) => {
      const r = await fetch(
        `${BASE}/api/tickets?provenance=all&stage=&q=${encodeURIComponent(q)}&page=1`)
      return await r.json().catch(() => ({}))
    }
    const drawn = () => page.$$eval(
      '[data-testid="epic-group"] table.tickets tbody tr td:first-child a',
      (n) => n.map((e) => e.textContent.trim()).sort())
    const onTheWire = (answer) =>
      (answer.groups ?? []).flatMap((g) => (g.tickets ?? []).map((t) => t.id)).sort()

    const matched = await tickets(TICKETS_QUERY)
    const whole = await tickets('')

    await page.goto(`${BASE}/#/tickets`, { waitUntil: 'networkidle' })
    await withTheSearchBoxFilled(page)
    const line = (await page.textContent('[data-testid="search-result"]').catch(() => '')).trim()
    const rows = await drawn()
    const want = `${matched.total} of ${whole.total} · matches id, title, owner and lane`
    ok("the tickets list reports the server's count",
      line === want
        && rows.join(' ') === onTheWire(matched).join(' ')
        && matched.total > 0 && matched.total < whole.total,
      `"${line}" (want "${want}") · on screen ${rows.join(' ') || 'no rows'} `
        + `· E29 ${onTheWire(matched).join(' ') || 'no rows'}`)

    // A count asserted at one query is a count that could be a constant. This is the same claim at
    // the other end of the range, and it is the end the empty state owns: `q` matching nothing must
    // move the rows to none and leave the BACKLOG — the number that is not about the query — where
    // it was.
    const NONE = 'zzz-no-such-ticket'
    await page.fill('[data-testid="tickets-search"]', NONE)
    await page.waitForSelector('[data-testid="tickets-empty"]', { timeout: 10000 }).catch(() => {})
    const emptyState = (await page.textContent('[data-testid="tickets-empty"]')
      .catch(() => '')).trim()
    const stillDrawn = await drawn()
    const none = await tickets(NONE)
    ok('and a query that matches nothing draws the empty state over the same backlog',
      none.total === 0 && stillDrawn.length === 0
        && emptyState === `No ticket matches “${NONE}”. The repository holds ${whole.total} in all.`,
      `"${emptyState}" · ${stillDrawn.length} rows on screen · E29 total ${none.total}`)
  }

  // The one caller of the wire from this script. Declared here rather than beside section 8, its
  // first user before WEB-FIX-344, because section 6d asks the server the same questions and a
  // second copy of it is the shape a gate drifts through.
  const api = async (method, path, body) => {
    const response = await fetch(`${BASE}${path}`, {
      method,
      headers: body ? { 'content-type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    return { status: response.status, body: await response.json().catch(() => null) }
  }

  // ── 6d · the six destinations the rail offers that no gate run opened (WEB-FIX-344) ───────
  //
  // Section 9 photographs them now; a photograph is not an assertion, and `docs/operations/ESCAPES.md`'s
  // 2026-08-26 row is a shot name read as coverage of a row that was in none of the captures. So
  // each of the six is asked ONE question it cannot answer from itself: a fact drawn on the
  // screen, against the ANSWER THE SERVER GAVE for the same question. The endpoint is read here
  // rather than derived from the screen, and every case requires its collection to be non-empty —
  // a surface drawing nothing agrees with an endpoint serving nothing, and that is the pair §1
  // says must not pass.
  {
    const get = async (path) => api('GET', path)
    const texts = (sel) => page.$$eval(sel, (ns) => ns.map((n) => n.textContent.trim()))
    const one = async (sel) => (await page.textContent(sel).catch(() => '')).trim()
    const open = async (hash, marker) => {
      await page.goto(`${BASE}/${hash}`, { waitUntil: 'networkidle' })
      await page.waitForSelector(marker, { timeout: 10000 }).catch(() => {})
    }

    // ── #/escapes · the ledger's columns and rows are the FILE's, whatever they are ──────────
    const e28 = (await get('/api/escapes')).body
    await open('#/escapes', '[data-testid="escapes-page"]')
    const columnsDrawn = await texts('table.escapes thead th')
    const firstCells = await texts('table.escapes tbody tr td:first-child')
    const escapesPill = await one('[data-testid="escapes-page"] .pill')
    ok('the escapes ledger draws the file’s own columns over the rows E28 served',
      Array.isArray(e28?.rows) && e28.rows.length > 0
        && columnsDrawn.join(' | ') === (e28.columns ?? []).map((c) => c.toUpperCase()).join(' | ')
        && firstCells.join(' | ') === e28.rows.map((r) => r.cells[0].text).join(' | ')
        && escapesPill === String(e28.total),
      `pill "${escapesPill}" vs E28 total ${e28?.total} · columns ${columnsDrawn.join('/')} `
        + `· ${firstCells.length} rows on screen of ${e28?.rows?.length ?? 0} served`)

    // ── #/wiki · the index is E27's sections, and the open page is that section's body ───────
    const e27 = (await get('/api/wiki')).body
    await open('#/wiki', '[data-testid="wiki-page"]')
    const wikiPill = await one('[data-testid="wiki-page"] .pill')
    const namesDrawn = (await texts('.wiki-row .wiki-name')).sort()
    const served = (e27?.sections ?? []).map((s) => s.path.split('/').pop()).sort()
    // The same preparation section 9 photographs under, and here it may not throw: a wiki that
    // cannot be opened is ONE red check with what it found attached, not a crash that takes the
    // four assertions below it off the board. Nothing is hidden by the catch — the assertion
    // reads the opened page immediately, so an unopened one fails it.
    await withAPageOpen(page).catch(() => {})
    const openTitle = await one('[data-testid="wiki-open"] h3')
    const openBody = await one('[data-testid="wiki-open"] .md')
    const shown = (e27?.sections ?? []).find((s) => s.title === openTitle)
    // The first heading of the page E27 sent under that title — the body has to be THAT page's,
    // not merely some markdown. A surface that opened the wrong row draws a body just as full.
    const firstHeading = shown ? (/^#+\s+(.+)$/m.exec(shown.body_markdown) ?? [])[1] ?? '' : ''
    ok("the wiki index is E27's own pages, and the page it opens is that page's body",
      served.length > 0 && wikiPill === String(e27?.total)
        && namesDrawn.join(' | ') === served.join(' | ')
        && shown !== undefined && firstHeading.length > 0 && openBody.includes(firstHeading),
      `pill "${wikiPill}" vs E27 total ${e27?.total} · index ${namesDrawn.join('/')} `
        + `· opened "${openTitle}" · first heading of E27's copy "${firstHeading}" `
        + `${openBody.includes(firstHeading) ? 'is' : 'is NOT'} in the frame`)

    // ── #/seams · the repository's own §2, and the count over it ────────────────────────────
    //
    // `SeamsPage` renders MODEL.md's `## 2. Seams` rather than a dashboard of states nothing in
    // this tool computes, so the question it can be asked is whether the seams on the screen are
    // the seams that document declares. Both come from E27 here: the headings are read out of the
    // section's own markdown and compared with the headings the browser drew from it.
    const model = (e27?.sections ?? []).find((s) => s.id === 'MODEL')
    const seamsSection = model ? model.body_markdown.slice(model.body_markdown.indexOf('## 2. Seams')) : ''
    const declared = [...seamsSection.slice(0, (seamsSection.search(/\n## (?!2\. Seams)/) + 1) || undefined)
      .matchAll(/^### (S\d+)\b/gm)].map((m) => m[1])
    await open('#/seams', '[data-testid="seams-page"]')
    const seamsPill = await one('[data-testid="seams-page"] .pill')
    const drawnSeams = (await texts('[data-testid="seams-page"] .md h3'))
      .map((t) => (/^(S\d+)\b/.exec(t) ?? [])[1]).filter(Boolean)
    ok("the seams surface draws the seams MODEL.md declares, and counts them",
      declared.length > 1 && drawnSeams.join(' ') === declared.join(' ')
        && seamsPill === String(declared.length),
      `pill "${seamsPill}" · on screen ${drawnSeams.join(' ') || 'none'} `
        + `· declared in E27's MODEL ${declared.join(' ') || 'none'}`)

    // ── #/runs · every run this tool started, across every ticket ───────────────────────────
    const e31 = (await get('/api/runs')).body
    await open('#/runs', '[data-testid="runs-page"]')
    const runsPill = await one('[data-testid="runs-page"] .pill')
    const runTickets = await texts('[data-testid="runs-page"] table.tickets tbody tr td:nth-child(2) a')
    const outcomes = await texts('[data-testid="runs-page"] table.tickets tbody tr td:last-child .state')
    ok('the all-runs surface draws every run E31 served, in its order, with E31’s own outcomes',
      (e31?.runs?.length ?? 0) > 0 && runsPill === String(e31.total)
        && runTickets.join(' | ') === e31.runs.map((r) => r.ticket_id).join(' | ')
        && outcomes.join(' | ') === e31.runs.map((r) => r.outcome).join(' | '),
      `pill "${runsPill}" vs E31 total ${e31?.total} · ${runTickets.length} rows on screen `
        + `of ${e31?.runs?.length ?? 0} · outcomes ${outcomes.join('/')}`)

    // ── #/delivery · four blocks, four sources, and no number summed across two ─────────────
    const e30 = (await get('/api/home')).body
    await open('#/delivery', '[data-testid="delivery-page"]')
    const drawnBlocked = await texts('[data-testid="deliv-blocked"] table.tickets tbody tr td:first-child a')
    const escapesMetric = await one(
      '[data-testid="deliv-quality"] .metric:first-child .metric-value')
    const delivery = deliveryCheck(e30, e28, drawnBlocked, escapesMetric)
    ok(delivery.name, delivery.passed, delivery.detail)

    // ── #/orchard · a monorepo is a SET of repositories, and E2 is where it says so ─────────
    const e2 = (await get('/api/manifest')).body
    const e29 = (await get('/api/tickets?provenance=all&stage=&q=&page=1')).body
    await open('#/orchard', '[data-testid="orchard-page"]')
    const repoNames = await texts('[data-testid="orchard-repo"] .lane-name')
    const repoCounts = await texts('[data-testid="orchard-repo"] .lane-detail')
    const orchardPill = await one('[data-testid="orchard-page"] .pill')
    const orchard = orchardCheck(e2, e29, repoNames, repoCounts, orchardPill)
    ok(orchard.name, orchard.passed, orchard.detail)
  }

  // ── 7 · delivery: what actually happened, on the row (SRV-FEAT-028 · F30) ────────────────
  //
  // The fixture repository holds a history built by run-gate.sh out of real git state, and a
  // badge is only worth anything if the browser paints it. WHICH states it must paint is not a
  // list typed here (WEB-FIX-373): `SRV-FIX-453` narrowed `running` to a lane the tool started,
  // no static frame of this fixture can reach one, and a five-element array typed into this file
  // went on demanding it for every client ticket after that merge. So the domain is derived from
  // what the sweep's own fixture answers — `E4` for each route, the requirement's own state and
  // its tickets' — and judged against the vocabulary the PRODUCT ships, read off §4.11's sheet
  // rather than off the label beside it, which the design owns and may rename.
  //
  // `running` did not lose its coverage; it moved to section 10, where a lane is actually running.
  const produced = []
  const badges = new Map()
  for (const route of ['#/requirements/REQ-014', '#/requirements/REQ-009']) {
    const id = route.split('/').pop()
    produced.push(...statesIn(id, (await api('GET', `/api/requirements/${id}`)).body))
    await page.goto(`${BASE}/${route}`, { waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="delivery-badge"]', { timeout: 10000 }).catch(() => {})
    for (const b of await page.$$eval('[data-testid="delivery-badge"]', (els) => els.map((e) => ({
      state: e.getAttribute('data-state'),
      alarm: e.getAttribute('data-alarm'),
      disagrees: e.getAttribute('data-disagrees'),
      text: e.textContent.trim(),
      border: getComputedStyle(e).borderTopColor,
    })))) {
      if (!badges.has(b.state)) badges.set(b.state, b)
    }
  }
  // WEB-FIX-405 — and the DOMAIN is the contract's, which is `SPEC.md` §3.5's table. The fixture
  // and the vocabulary sheet are two things this gate happens to be looking at; the enumeration is
  // what the product says exists, so a state in either of them that `http.schema.json` does not
  // enumerate is the product disagreeing with its own contract and is refused by name.
  const deliveryVerdict = judgeDeliveryBadges({
    produced, words: deliveryWords, drawn: [...badges.keys()],
    domain: SCHEMA.$defs.delivery.properties.state.enum ?? [],
  })
  ok('every delivery state this sweep’s fixture reaches is drawn, and nothing else is',
    deliveryVerdict.ok, deliveryVerdict.line)

  // WEB-FIX-405 — WHICH states paint an alarm border is the server's answer, not a pair typed
  // here. `probe.cpp` sets `needs_you`, `E4` serves it, and the browser is judged against it: a
  // state the wire marks must paint a border, one it does not must not. `['stalled','unshipped']`
  // stood here beside a derived domain, which is the same defect one line lower — and it is why
  // this loop can now see a third state the moment the product marks one.
  const transparent = (c) => /rgba\(\s*0,\s*0,\s*0,\s*0\s*\)/.test(c)
  const alarms = alarmStates(produced)
  ok('the fixture answered with states that need a person AND states that do not, so the border '
     + 'rule below is not the identity',
    alarms.alarm.length > 0 && alarms.calm.length > 0 && alarms.disagree.length === 0,
    `alarm ${alarms.alarm.join(' · ') || 'none'} · calm ${alarms.calm.join(' · ') || 'none'}`
    + (alarms.disagree.length ? ` · E4 answered both ways for ${alarms.disagree.join(' · ')}` : ''))
  for (const [state, wantsAlarm] of [...alarms.alarm.map((s) => [s, true]),
                                     ...alarms.calm.map((s) => [s, false])]) {
    const b = badges.get(state)
    if (b === undefined) continue      // not drawn at all is section 7's verdict, above, not this one
    ok(`${state} ${wantsAlarm ? 'carries' : 'does not carry'} an alarm border the browser paints`,
      b.alarm === String(wantsAlarm) && transparent(b.border) !== wantsAlarm,
      `data-alarm=${b.alarm} · border ${b.border}`)
  }

  // the disagreement, which is the most valuable thing on the row
  const unshipped = badges.get('unshipped')
  ok('a row whose document disagrees says so, and shows the derived state',
    unshipped !== undefined && unshipped.disagrees === 'true'
    && /unshipped/.test(unshipped.text) && /document says todo/.test(unshipped.text),
    unshipped ? unshipped.text : 'absent')

  // ...and on the surface a person opens to look at ONE ticket, which had it last. Every check
  // above passed for two years' worth of rounds while `TicketHeader` rendered no badge at all:
  // the row said `unshipped · document says todo` and the ticket's own page said NOT STARTED.
  // A gate that only ever visits the list cannot see a surface missing from the list.
  await page.goto(`${BASE}/#/tickets/TKT-142`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="ticket-header"]', { timeout: 10000 }).catch(() => {})
  const onTicket = await page.$$eval('[data-testid="ticket-header"] [data-testid="delivery-badge"]',
    (els) => els.map((e) => ({ state: e.getAttribute('data-state'),
                               disagrees: e.getAttribute('data-disagrees'),
                               text: e.textContent.trim() })))
  ok('the ticket page says what actually happened, not only what the document claims',
    onTicket.length === 1 && onTicket[0].state === 'unshipped'
    && onTicket[0].disagrees === 'true' && /document says/.test(onTicket[0].text),
    onTicket.length ? onTicket[0].text : 'no delivery badge on the ticket header')

  // ── 7b · and the rows that need a person are above the fold ───────────────────────────────
  await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="requirement-row"]', { timeout: 10000 }).catch(() => {})
  const marked = await page.$$eval('[data-testid="requirement-row"]', (rows) => rows
    .filter((r) => r.querySelector('[data-testid="row-needs-pill"]'))
    .map((r) => ({
      id: r.querySelector('.req-id')?.textContent?.trim() ?? '?',
      bottom: r.getBoundingClientRect().bottom,
    })))
  const viewport = page.viewportSize().height
  // Three, since SRV-FIX-166 added REQ-031. The count is a guard against this passing vacuously on
  // an empty list, not the property under test — and it is three rather than two because in this
  // fixture a ticket is `done` BY having its report committed to `main`, while a report that cites
  // no run is `claimed · no proof`. So every all-done requirement here necessarily needs a person,
  // exactly as REQ-011 and REQ-014 already did. The property itself is unchanged: all of them are
  // above the fold.
  ok('the rows that need a person are visible without scrolling',
    marked.length === 3 && marked.every((m) => m.bottom > 0 && m.bottom <= viewport),
    marked.map((m) => `${m.id}@${Math.round(m.bottom)}`).join(' · ') + ` of ${viewport}px`)

  // ── 8 · the lane rail, the three chips and the counter (WEB-FEAT-013 · §4.1, §4.4, §4.5) ──
  //
  // Lanes are claimed HERE rather than in run-gate.sh, and after section 7 on purpose: claiming
  // one writes a branch and a worktree, which is a change to what §3.5 derives, and the delivery
  // badges above are read before any of it happens. The states are driven through the tool's own
  // endpoints — a real claim, a real confined process, and the injected clock — because a rail
  // painted from a fixture file would be a picture of a JSON document rather than of the product.
  const claim = async (ticket, workspace) => {
    const r = await api('POST', '/api/lanes', { ticket_id: ticket, workspace })
    if (r.status !== 201) {
      failures += 1
      console.log(`  FAIL  could not claim a lane for ${ticket}: ${r.status} ${JSON.stringify(r.body)}`)
      return null
    }
    return r.body.lane
  }

  // running · silent — a live process, and then two minutes of nothing
  const silent = await claim('TKT-142', 'core')
  if (silent) await api('POST', '/api/_test/lane-process', { lane: silent, command: 'sleep 600' })
  // an exclusive resource, held and then wanted: the second lane is `queued`, with its position
  const holding = await claim('TKT-151', 'edge')
  const queued = await claim('TKT-133', 'edge')
  // two lanes whose EVIDENCE differs, off documents the fixture already holds: TKT-139's report
  // cites a run the tool assigned and TKT-128's cites nothing, which is §3.4's whole mechanism
  const proven = await claim('TKT-139', 'web')
  const unproven = await claim('TKT-128', 'web')
  await api('POST', '/api/_test/clock', { advance_s: 130 })
  const producing = await claim('TKT-147', 'core')
  if (producing) {
    const started = await api('POST', '/api/_test/lane-process', {
      lane: producing, command: "for gate_tick in $(seq 1 600); do printf 'gate output\\n'; sleep 1; done",
    })
    ok('the producing fixture has a confined process that emits output', started.status === 202,
      `${started.status} ${JSON.stringify(started.body)}`)
  }

  const wire = (await api('GET', '/api/lanes')).body
  const laneRows = wire?.lanes ?? []
  ok('the server holds one lane in each state the rail draws', laneRows.length === 6,
    laneRows.map((l) => `${l.lane}:${l.state.process}/${l.state.motion.value ?? '—'}/${l.state.evidence}`)
      .join(' · '))
  ok('E9 /api/lanes matches http.schema.json',
    validate(SCHEMA.responses.E9, wire ?? {}, 'E9', []).length === 0,
    validate(SCHEMA.responses.E9, wire ?? {}, 'E9', []).slice(0, 3).join(' · ') || 'clean')

  // ── 8-081 · the epic page tells the truth while lanes burn (SRV-FIX-081) ──────────────────
  //
  // The operator's defect: E9 said `running` and the requirement detail one click away said
  // `not started · 0/N · —` about the same tickets in the same minute, because the catalog
  // renders derived `process` from finished runs only. The lanes above were claimed over E12;
  // this drives the epic page and reads the child row ON FIRST PAINT — a hash navigation mounts
  // the surface fresh and its one E4 fetch is what is judged. No reload, no socket frame waited
  // on. TKT-147's lane is the one still producing, so its row's word is `running`; TKT-133's
  // lane is the one behind `climate-chamber`, so its word is `queued`.
  await page.goto(`${BASE}/#/requirements/REQ-014`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="ticket-row"]', { timeout: 10000 }).catch(() => {})
  const rowText = async (id) => page.$$eval('[data-testid="ticket-row"]',
    (rows, wanted) => (rows.find((r) => r.textContent.includes(wanted))?.textContent ?? '').trim(),
    id).catch(() => '')
  const runningRow = await rowText('TKT-147')
  ok('a ticket a lane is running says `running` on the epic page, on first paint, with no reload',
    /running/.test(runningRow), runningRow.replace(/\s+/g, ' ').slice(0, 110) || 'no row')

  await page.goto(`${BASE}/#/requirements/REQ-011`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="ticket-row"]', { timeout: 10000 }).catch(() => {})
  const queuedRow2 = await rowText('TKT-133')
  ok('and a ticket whose lane waits in a queue says `queued`, the same way',
    /queued/.test(queuedRow2), queuedRow2.replace(/\s+/g, ' ').slice(0, 110) || 'no row')

  const readCards = () => page.$$eval('[data-testid="lane-card"]', (els) => els.map((e) => ({
    lane: e.getAttribute('data-lane'),
    ticket: e.getAttribute('data-ticket'),
    selected: e.getAttribute('data-selected'),
    alarm: e.getAttribute('data-alarm'),
    state: e.querySelector('.lane-state')?.textContent?.trim() ?? '',
    sub: e.querySelector('[data-testid="lane-sub"]')?.textContent?.trim() ?? '',
    elapsed: e.querySelector('[data-testid="lane-elapsed"]')?.textContent?.trim() ?? '',
    border: getComputedStyle(e).borderTopColor,
  })))
  const transparentBorder = (c) => /rgba\(\s*0,\s*0,\s*0,\s*0\s*\)/.test(c)

  // ── 8a · §4.14 — every lane, on a surface of its own (WEB-FEAT-050) ───────────────────────
  //
  // The half that proves nothing was lost when the rail below stopped showing everything: the
  // whole of E9's answer, reached from the chrome, drawn with the SAME card, and one click from
  // any lane to the ticket running it. Six lanes on six different tickets is what makes both
  // halves falsifiable.
  //
  // It runs HERE, before the rail block and directly after the six lanes were built, because
  // THIS is the moment the fixture holds one lane in each state the card can draw.
  await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
  const lanesHref = await page.getAttribute('.bar a:has-text("LANES")', 'href').catch(() => null)
  ok('the top bar reaches the lanes surface', lanesHref === '#/lanes', String(lanesHref))

  // clicked, the way a person reaches it — a link nothing navigates to is a link nobody has tried
  await tryClick(page, '.bar a:has-text("LANES")')
  ok('and clicking it actually goes there', new URL(page.url()).hash === '#/lanes', page.url())

  // The click above is a SAME-DOCUMENT navigation, so the app still holds the lane set it read at
  // page load — before any of section 8's six lanes existed. The reload is what makes the browser
  // ask E9 again, and it is here for the same reason the rail block below carries one.
  //
  // FINDING, and it belongs to nobody in this batch: a lane CLAIMED while a page is open still does
  // not announce itself. The server broadcasts no lane frame on a claim — only `M8 resource`, which
  // names no lane — so the client's unknown-lane refetch cannot fire (WEB-FEAT-013 §7, WEB-FEAT-044
  // known limits). What §4.14 does get live is every SUBSEQUENT change to a lane it already holds:
  // `M2`/`M3`/`M4` are routed to the `requirements` scope this surface subscribes to, which is the
  // thing that made a global surface possible with no server change at all.
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="lane-card"]', { timeout: 10000 }).catch(() => {})

  const allRows = (await api('GET', '/api/lanes')).body?.lanes ?? []
  const allCards = await readCards()
  ok('every lane E9 holds is on the surface, including the five nobody has open',
    allRows.length > 1 && allCards.length === allRows.length
      && allRows.every((row) => allCards.some((c) => c.lane === row.lane)),
    `${allCards.length} cards for ${allRows.length} lanes: `
      + (allCards.map((c) => `${c.lane}(${c.ticket})`).join(' · ') || 'none'))

  // it is the RAIL's card, so every state line the rail can draw is drawn here in the same words
  ok('and it is the same card the rail draws — a state line and a clock on every one',
    allCards.length > 0
      && allCards.every((c) => c.state !== '' && /\d+m \d\ds/.test(c.elapsed)),
    allCards.map((c) => `${c.lane} "${c.state}"`).join(' | '))
  const allLines = new Set(allCards.map((c) => c.state))
  ok('a lane that is going nowhere does not read like one that is working, here either',
    allLines.has('running · silent') && allLines.has('running · producing'),
    [...allLines].join(' · '))
  const queuedCard = allCards.find((c) => c.lane === queued)
  ok('a queued lane names the resource it is behind and its place in the line',
    queuedCard !== undefined && /queued for climate-chamber/.test(queuedCard.sub),
    queuedCard ? queuedCard.sub : 'absent')
  const needsHere = allCards.filter((c) => c.alarm === 'true')
  ok('a card that needs a person takes a painted border on this surface too',
    needsHere.length > 0 && needsHere.every((c) => !transparentBorder(c.border)),
    needsHere.map((c) => `${c.lane} ${c.border}`).join(' | ') || 'none')
  // nothing is selected, because nothing is open; and E13 is not offered, because this reads
  ok('nothing is selected and nothing can be released from here',
    allCards.every((c) => c.selected === 'false')
      && (await page.$$('[data-testid="lane-release"]')).length === 0)

  // grouped by workspace — §4.3's grouping for §4.3's reason, in the manifest's declared order
  const declaredOrder = Object.keys((await api('GET', '/api/manifest')).body?.workspaces ?? {})
  const laneGroups = await page.$$eval('[data-testid="lane-group"]', (els) => els.map((e) => ({
    name: e.querySelector('[data-testid="group-name"]')?.textContent?.trim() ?? '',
    count: e.querySelector('[data-testid="group-count"]')?.textContent?.trim() ?? '',
    lanes: Array.from(e.querySelectorAll('[data-testid="lane-card"]'))
      .map((c) => c.getAttribute('data-lane')),
  })))
  const expectedOrder = declaredOrder.filter(
    (w) => allRows.some((row) => row.workspace === w))
  ok('grouped by workspace, in the order the manifest declares them',
    laneGroups.length === expectedOrder.length
      && laneGroups.map((g) => g.name).join(',') === expectedOrder.join(','),
    `${laneGroups.map((g) => `${g.name} ${g.count}`).join(' · ') || 'no groups'}`
      + ` — manifest declares ${declaredOrder.join(',')}`)
  ok('and every lane sits in the group its own workspace names',
    laneGroups.every((g) => g.lanes.every(
      (name) => allRows.find((row) => row.lane === name)?.workspace === g.name)),
    laneGroups.map((g) => `${g.name}:[${g.lanes.join(',')}]`).join(' '))

  // ── the two surfaces are ONE PATH: a card goes to its ticket, and the rail there holds it ──
  const target = allRows.find((row) => row.ticket_id === 'TKT-151')
  if (target) {
    await tryClick(page, `[data-testid="lane-card"][data-lane="${target.lane}"]`)
    await page.waitForSelector('[data-testid="lane-rail"]', { timeout: 10000 }).catch(() => {})
    ok('a card goes to the ticket its lane is running',
      new URL(page.url()).hash === '#/tickets/TKT-151', page.url())
    const landed = await readCards()
    ok('and the rail there shows that lane, and only that ticket\'s',
      landed.length > 0 && landed.every((c) => c.ticket === 'TKT-151')
        && landed.some((c) => c.lane === target.lane),
      landed.map((c) => `${c.lane}(${c.ticket})`).join(' · ') || 'no cards')
  } else {
    skipped('a card goes to the ticket its lane is running',
      'section 8 claimed no lane for TKT-151')
  }


  // A goto that changes only the hash is a SAME-DOCUMENT navigation: the app keeps every piece of
  // React state it had, including the lane set it read before any of these lanes existed. The
  // reload is what makes the browser ask the server again — and it has to, because a `lane` frame
  // is routed to the scope that subscribed to it (§6.2 M14) and a page looking at TKT-142 is not
  // told about a lane claimed for TKT-151. That routing is the specification's, not a defect, and
  // WEB-FEAT-013 wrote it up as the one thing §4.4 and §6 did not quite agree about. WEB-FEAT-050
  // is where the disagreement ends on this surface: this rail is now ABOUT TKT-142, so a lane
  // claimed for another ticket is not something it was ever going to show. §4.14's surface, which
  // is the one that shows everything, subscribes to the `requirements` scope every lane frame is
  // also routed to (`laneStateScopes`) — see section 8i.
  await page.goto(`${BASE}/#/tickets/TKT-142`, { waitUntil: 'networkidle' })
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="lane-card"]', { timeout: 10000 }).catch(() => {})

  const cards = await readCards()

  // ── §4.4 as WEB-FEAT-050 rewrote it: this rail is about THIS ticket ──────────────────────
  // The fixture holds lanes on six different tickets by now, so a rail that still showed them all
  // would pass an "at least one card" check and fail this one.
  const ownLanes = laneRows.filter((l) => l.ticket_id === 'TKT-142')
  ok('the rail shows only this ticket\'s lanes, and the server holds more',
    laneRows.length > ownLanes.length
      && cards.length === ownLanes.length
      && cards.every((c) => c.ticket === 'TKT-142'),
    `${cards.length} of ${laneRows.length}: ` + (cards.map((c) => `${c.lane}(${c.ticket})`).join(' · ') || 'none'))
  const railCount = (await page.textContent('[data-testid="lane-count"]').catch(() => '')).trim()
  ok('and the count beside LANES is that number, not the repository\'s total',
    railCount === String(ownLanes.length) && railCount !== String(laneRows.length),
    `count reads "${railCount}", this ticket has ${ownLanes.length}, `
      + `the repository has ${laneRows.length}`)
  ok('every card carries a state line and an elapsed clock',
    cards.length > 0 && cards.every((c) => c.state !== '' && /\d+m \d\ds/.test(c.elapsed)),
    cards.map((c) => `${c.lane} "${c.state}" ${c.elapsed}`).join(' | '))

  const silentCard = cards.find((c) => c.state === 'running · silent')
  ok('a lane that is going nowhere says for how long, counted in the browser from `since`',
    silentCard !== undefined && /no output \d+m \d\ds/.test(silentCard.sub),
    silentCard ? silentCard.sub : 'absent')

  const selected = cards.filter((c) => c.selected === 'true')
  ok('the lane running THIS ticket is the selected one, and the only one',
    selected.length === 1 && selected[0].lane === silent,
    selected.map((c) => c.lane).join(' · ') || 'none')

  const needsCards = cards.filter((c) => c.alarm === 'true')
  ok('a card that needs a person takes a border the browser paints',
    needsCards.length > 0 && needsCards.every((c) => !transparentBorder(c.border)),
    needsCards.map((c) => `${c.lane} ${c.border}`).join(' | ') || 'none')

  // A ticket with no lane of its own STILL offers to start one, with other tickets' lanes live.
  // This is the case a naive filter breaks — the empty block used to be chosen by the size of the
  // global set — and it is asserted against the real server rather than only in jsdom.
  await page.goto(`${BASE}/#/tickets/TKT-155`, { waitUntil: 'networkidle' })
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="rail-empty"]', { timeout: 10000 }).catch(() => {})
  const emptyRail = (await page.textContent('[data-testid="rail-empty"]').catch(() => '')).trim()
  const emptyCards = await page.$$('[data-testid="lane-card"]')
  const startables = await page.$$('[data-testid="claim-lane"]')
  const railStart = await page.$('[data-testid="rail-empty"] [data-testid="claim-lane"]')
  const sessionStart = await page.$('[data-testid="composer"] [data-testid="claim-lane"]')
  ok('a ticket with no lane of its own still offers to start one, while others have lanes',
    /NO LANE ASSIGNED/.test(emptyRail) && emptyCards.length === 0 && startables.length === 1
      && railStart === null && sessionStart !== null && laneRows.length > 0,
    `${emptyCards.length} cards, ${laneRows.length} lanes in the repository, `
      + `${startables.length} control(s), session ${sessionStart ? 'present' : 'ABSENT'}, `
      + `rail ${railStart ? 'DUPLICATED' : 'absent'}`)

  // §4.1 — the counter is the SERVER's number, and absent rather than zero
  const needs = laneRows.filter((l) => l.needs_you).length
  const chipText = await page.textContent('[data-testid="needs-you-chip"]').catch(() => null)
  ok('the `needs you` chip is the count E9 reported',
    needs > 0
      ? chipText !== null && chipText.includes(`${needs} lane`)
      : chipText === null,
    `server says ${needs}, chip says ${chipText === null ? 'absent' : chipText.trim()}`)

  // §4.5 — three chips, one per axis, never merged
  const axes = await page.$$eval('[data-testid="state-chip"]',
    (els) => els.map((e) => ({ axis: e.getAttribute('data-axis'), text: e.textContent.trim() })))
  ok('the header carries three chips, one per axis, in order',
    axes.length === 3 && axes.map((a) => a.axis).join(',') === 'process,motion,evidence',
    axes.map((a) => a.text).join(' | '))

  const caption = (await page.textContent('[data-testid="stage-caption"]').catch(() => '')).trim()
  ok('the stage strip carries its caption, and the strip itself stays linear',
    /^stage \d+\/\d+( · round \d+ of \d+)? · \w+ sequence$/.test(caption), caption)

  // ── 8b · the background tab (WEB-FEAT-019 · §4.13, F24) ───────────────────────────────────
  //
  // The three events that reach out are driven by frames a browser cannot fabricate, so what is
  // checked here is the half that is a fact about the DOCUMENT: the title and the favicon are the
  // channel that always works, and they are quiet when nothing has happened and honest when the
  // socket is gone. The event rules themselves are `tests/notify.test.tsx`.
  await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="requirement-row"]', { timeout: 10000 }).catch(() => {})
  const restTitle = await page.title()
  ok('at rest, with a live socket, the title carries no prefix',
    restTitle === 'Factory', restTitle)
  const restIcon = await page.getAttribute('link[rel="icon"]', 'href').catch(() => null)
  ok('and the favicon is the plain icon, with no dot',
    typeof restIcon === 'string' && restIcon.startsWith('data:image/svg+xml')
    && !restIcon.includes('circle'),
    restIcon === null ? 'no icon link' : `${restIcon.slice(0, 48)}…`)

  // the socket really gone — the absence of notifications otherwise reads as `nothing is
  // happening`, which is exactly wrong
  offlineWindow = true
  await page.context().setOffline(true)
  await page.waitForFunction(() => document.title.includes('(offline)'), null,
    { timeout: 15000 }).catch(() => {})
  const offlineTitle = await page.title()
  ok('with the network cut the title reads (offline)',
    offlineTitle === '(offline) Factory', offlineTitle)
  const offlineChip = (await page.textContent('[data-testid="socket-chip"]').catch(() => '')).trim()
  ok('and the chip agrees with the title', /reconnecting/.test(offlineChip), offlineChip)

  await page.context().setOffline(false)
  await page.waitForFunction(() => !document.title.includes('(offline)'), null,
    { timeout: 20000 }).catch(() => {})
  const backTitle = await page.title()
  ok('and when it comes back the title loses the prefix', backTitle === 'Factory', backTitle)
  offlineWindow = false

  // ── 8c · roles, and the row that is not there (WEB-FEAT-033 · §4.5, E5, F31) ──────────────
  //
  // The fixture repository declares a role, so the manifest half of F31 is loaded and E2's
  // refusal (a `prompt_file` that is not there) is proved not to be firing. Nothing STARTS: a
  // role starts when a run reports its stage, and this gate never starts a run — see the note
  // below and `WEB-FIX-035`.
  const liveManifest = (await api('GET', '/api/manifest')).body
  ok('the fixture repository declares a role, and the tool carries it verbatim',
    liveManifest?.roles?.reviewer?.start_on_stage === 'GATE'
      && liveManifest?.roles?.reviewer?.lane === 'new',
    JSON.stringify(liveManifest?.roles ?? null))

  // The mandatory negative, against the REAL server: `roles` is ABSENT, not an empty array, until
  // a declared role has been reached. This is the shape every ticket in this repository has.
  const liveTicket = (await api('GET', '/api/tickets/TKT-142')).body
  ok('E5 omits `roles` until a role is reached — absent, not an empty array',
    liveTicket !== null && !('roles' in liveTicket),
    'roles' in (liveTicket ?? {}) ? JSON.stringify(liveTicket.roles) : 'absent')

  const rolesRow = await page.$('[data-testid="roles-row"]')
  ok('and the header draws no roles row at all for a ticket that has none',
    rolesRow === null, rolesRow === null ? 'absent' : 'an empty container is on the screen')

  // BLOCKED(WEB-FIX-035) — "the started role and a queued one both render, in both themes".
  // A role is created by `POST /api/progress`, which needs the `FACTORY_PROGRESS_TOKEN` that
  // E18 puts in the AGENT's environment and nowhere else. E18 spawns the manifest's
  // `agent.command`, which this gate must not start, and `POST /api/_test/lane-process` attaches
  // a process without ever creating a run. That is exactly the stand-in agent `WEB-FIX-035`
  // owns; when it lands, this is four lines here and one call to it.
  console.log('  BLOCKED(WEB-FIX-035)  a started role and a queued one, in both themes — '
    + 'driving one needs a run\'s progress token, which needs the stand-in agent 035 owns')

  // ── 8d · RUNS and DIFF (WEB-FEAT-016 · §4.10, §8 D3, F23) ────────────────────────────────
  //
  // Written by WEB-FEAT-016 and NOT RUN BY IT: it was one of five client lanes in a batch, and
  // five headless Chromes on one laptop make each other flaky. The integrator runs this.
  //
  // The two endpoints section 6b could not reach, now that there is a run and a lane to name.
  const runsBody = (await api('GET', '/api/tickets/TKT-139/runs')).body
  const firstRun = (runsBody?.runs ?? [])[0]
  if (firstRun) {
    const path = `/api/runs/${encodeURIComponent(firstRun.run_id)}/transcript`
    const answer = await api('GET', path)
    // E8's 404 is a specified answer, not a failure: a run whose log has been cleaned keeps its
    // row in E7 and says plainly that the transcript is gone, naming the path it looked for.
    // E8's node defers to this section by name, so this section says it judged it — the ledger
    // at the end of the run is what turns `checked_by` into an assertion (WEB-FIX-213).
    judged.add('E8')
    if (answer.status === 200) {
      ok(`E8 ${path} matches http.schema.json`,
        validate(SCHEMA.responses.E8, answer.body, 'E8', []).length === 0,
        validate(SCHEMA.responses.E8, answer.body, 'E8', []).slice(0, 3).join(' · ') || 'clean')
    } else {
      ok('E8 on a cleaned run is a 404 that names the transcript it looked for',
        answer.status === 404 && /transcript\.jsonl$/.test(answer.body?.error?.detail?.path ?? ''),
        `${answer.status} ${answer.body?.error?.detail?.path ?? ''}`)
    }
  } else {
    ok('the fixture repository records at least one run for E7 and E8 to be about', false, 'none')
  }

  const diffLane = silent ?? holding
  const diffPath = `/api/lanes/${encodeURIComponent(diffLane)}/diff`
  const diffAnswer = await api('GET', diffPath)
  judged.add('E10')
  ok(`E10 ${diffPath} matches http.schema.json`,
    diffAnswer.status === 200
      && validate(SCHEMA.responses.E10, diffAnswer.body, 'E10', []).length === 0,
    validate(SCHEMA.responses.E10, diffAnswer.body ?? {}, 'E10', []).slice(0, 3).join(' · ')
      || `HTTP ${diffAnswer.status}`)

  // The tab row now carries five, and the page still names none of them.
  await page.goto(`${BASE}/#/tickets/TKT-139/runs`, { waitUntil: 'networkidle' })
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="run-row"], [data-testid="runs-empty"]',
                             { timeout: 10000 }).catch(() => {})
  const tabIdsOnScreen = await page.$$eval('[role="tab"]', (els) => els.map((e) => e.getAttribute('data-tab')))
  ok('the tab row carries RUNS and DIFF', tabIdsOnScreen.includes('runs') && tabIdsOnScreen.includes('diff'),
    tabIdsOnScreen.join(' · '))

  // §4.10 — one row per run E7 served, newest first, or the empty sentence and not an error.
  const wireRuns = (await api('GET', '/api/tickets/TKT-139/runs')).body?.runs ?? []
  const painted = await page.$$eval('[data-testid="run-row"]', (els) => els.map((e) => ({
    n: e.querySelector('[data-testid="run-n"]')?.textContent?.trim() ?? '',
    started: e.querySelector('[data-testid="run-started"]')?.textContent?.trim() ?? '',
    outcome: e.querySelector('[data-testid="run-outcome"]')?.textContent?.trim() ?? '',
    colour: getComputedStyle(e.querySelector('[data-testid="run-outcome"]')).color,
    dur: e.querySelector('[data-testid="run-dur"]')?.textContent?.trim() ?? '',
  })))
  ok('the RUNS tab paints one row per run E7 served',
    painted.length === wireRuns.length, `${painted.length} of ${wireRuns.length}`)

  // WEB-FIX-091 — the two numbers ABOVE the table are about the table. A count taken from
  // anywhere else is a count that can disagree with the rows a person is looking at, and the
  // audit photographed exactly that: `0 runs · RUNS · 0` over a lane producing output.
  const countOnTab = (await page.textContent('[data-tab="runs"]').catch(() => '')).trim()
  const identity = (await page.textContent('[data-testid="ticket-metrics"]').catch(() => '')).trim()
  ok('the tab and the identity row count the rows under them',
    countOnTab === `RUNS · ${painted.length}`
      && identity.startsWith(`${painted.length} run`),
    `${countOnTab} · ${identity} · ${painted.length} rows`)

  // …and a run whose record carries a start time shows it. `—` is for a record that has none,
  // which no run the tool wrote ever has.
  const withStart = wireRuns.filter((r) => r.started_iso)
  ok('a run the wire gave a start time shows the time, not a dash',
    withStart.length > 0 && painted.slice(0, withStart.length)
      .every((r) => /^\d{2}:\d{2}:\d{2}$/.test(r.started)),
    `${withStart.length} of ${wireRuns.length} carry started_iso · painted `
      + painted.map((r) => r.started).join(' | '))
  if (wireRuns.length === 0) {
    const empty = (await page.textContent('[data-testid="runs-empty"]').catch(() => '')).trim()
    ok('a ticket with no runs says so, and is not an error',
      empty.includes('No runs recorded for this ticket.'), empty)
  } else {
    ok('every run reads as one of §4.10\'s outcomes and its duration is a clock',
      painted.every((r) => /^#\d+$/.test(r.n) && r.outcome !== '' && /\d+m \d\ds/.test(r.dur)),
      painted.map((r) => `${r.n} ${r.outcome} ${r.dur}`).join(' | '))

    // selecting a row is what the tab is for: it lands on SESSION, read-only, naming the run.
    // The wait is on a fact the panel produces — the banner's own element — and never on time
    // passing: a gate that reports a race as a missing panel is a gate nobody can read.
    await page.click('[data-testid="run-row"]')
    await page.waitForSelector('[data-testid="replay-banner"], [data-testid="session-empty"]',
                               { timeout: 10000 }).catch(() => {})
    const banner = (await page.textContent('[data-testid="replay-banner"]').catch(() => '')).trim()
    ok('selecting a run reads it back, in the SESSION tab, saying it is read only',
      /#\d/.test(banner) && /read only/.test(banner), banner || 'no banner')
    // WEB-FIX-354 — the panel FIRST, then what is inside it. `$$eval` over an absent composer
    // returns `[]`, so `0 enabled controls` used to be the green answer for a panel that was
    // never drawn: one row said the replay was missing and the row under it reported the missing
    // replay as safe. A promise about controls is a claim about a collection, so the collection
    // is in the same assertion — present, non-empty, and none of it able to send.
    const controls = await page.$$eval(
      '[data-testid="replay-composer"] textarea, [data-testid="replay-composer"] button',
      (els) => els.map((e) => (e.disabled ? 'disabled' : 'ENABLED')))
    ok('nothing on a replayed run can send anything',
      controls.length > 0 && controls.every((state) => state === 'disabled'),
      controls.length === 0
        ? 'no replay composer on screen — nothing to be read only'
        : `${controls.length} controls · ${controls.join(' · ')}`)
  }

  // §8 D3 / F23 — the diff of a lane that has only just been claimed: empty, and not an error.
  await page.goto(`${BASE}/#/tickets/TKT-142/diff`, { waitUntil: 'networkidle' })
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="diff-empty"], [data-testid="diff-totals"]',
                             { timeout: 10000 }).catch(() => {})
  const wireDiff = (await api('GET', diffPath)).body ?? { files: [], totals: { files: 0 } }
  if ((wireDiff.files ?? []).length === 0) {
    const empty = (await page.textContent('[data-testid="diff-empty"]').catch(() => '')).trim()
    ok('a lane that changed nothing says so, and is not an error',
      empty.includes('Nothing changed in this lane yet.'), empty)
  } else {
    const shown = await page.$$eval('[data-testid="diff-file"]', (els) => els.map((e) => ({
      path: e.querySelector('[data-testid="diff-path"]')?.textContent?.trim() ?? '',
      added: e.querySelector('[data-testid="diff-added"]')?.textContent?.trim() ?? '',
    })))
    ok('the DIFF tab paints every file E10 named, with its +n',
      shown.length === wireDiff.files.length && shown.every((f) => /^\+\d+$/.test(f.added)),
      shown.map((f) => `${f.path} ${f.added}`).join(' | '))
    await page.click('[data-testid="diff-open"]')
    await page.waitForTimeout(500)
    const patch = (await page.textContent('[data-testid="diff-patch"]').catch(() => '')).trim()
    ok('one file\'s patch opens in place, without leaving the ticket screen',
      patch.length > 0 && (await page.$('[data-testid="ticket-header"]')) !== null,
      `${patch.length} chars`)
  }
  // ── 8e · permissions: the policy, the granted list, and the word that reaches them ────────
  //
  // WEB-FEAT-018 · SPEC.md §4.12, §8 D4. The rule that is granted here is granted the way a
  // person grants one — a real ask through the tool's own hook, a real `always` at E23, a real
  // line written into `.factory/permissions.json` in the fixture — because a table painted from
  // a JSON fixture is a picture of a document rather than of the product. The fixture repository
  // is a temporary copy, so the file this writes is thrown away with it.
  //
  // NOT ASSERTED HERE: the inline request. It is a transcript ENTRY (§4.6) and the transcript is
  // WEB-FEAT-014's; the block and its reader ship in this ticket with vitest cover, and the one
  // line that renders it lands at integration. A gate assertion for a component nothing mounts
  // would be a gate assertion that cannot fail.
  //
  // WEB-FEAT-379 — a LABELLED BLOCK, and the body below is deliberately left at the indentation
  // it has always had. Re-indenting a section to add a guard is a diff nobody can review, and it
  // silently rewrites every multi-line template literal in it; `break <label>` costs two lines and
  // changes nothing else.
  permissionsSection: {
  if (!sectionInScope('the permissions screen (8e)', 'permissions')) break permissionsSection

  await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
  const permsHref = await page.getAttribute('.bar a:has-text("PERMISSIONS")', 'href')
    .catch(() => null)
  ok('the top bar reaches the permissions screen', permsHref === '#/permissions',
    String(permsHref))

  await page.goto(`${BASE}/#/permissions`, { waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="policy-control"]', { timeout: 10000 }).catch(() => {})
  const options = await page.$$eval('[data-testid="policy-option"]', (els) => els.map((e) => ({
    policy: e.getAttribute('data-policy'),
    checked: e.querySelector('input')?.checked ?? false,
    consequence: e.querySelector('[data-testid="policy-consequence"]')?.textContent?.trim() ?? '',
  })))
  ok('the policy offers three values, in SPEC.md §4.12\'s order',
    options.map((o) => o.policy).join(',') === 'ask_all,edit_free,never_ask',
    options.map((o) => o.policy).join(' · '))
  ok('and each one names its consequence in a line of its own',
    options.length === 3 && options.every((o) => o.consequence.length > 20)
    && new Set(options.map((o) => o.consequence)).size === 3,
    options.map((o) => `${o.policy}: "${o.consequence}"`).join(' | '))
  ok("the manifest's own permission_mode is the value in force",
    options.find((o) => o.checked)?.policy === 'edit_free',
    options.find((o) => o.checked)?.policy ?? 'none checked')
  const policyNote = (await page.textContent('[data-testid="policy-note"]').catch(() => '')).trim()
  ok('the control says out loud that a change applies to lanes started afterwards',
    /applies to lanes started afterwards/.test(policyNote), policyNote)

  // a rule declared in the manifest: listed, and NOT revocable from a UI
  const manifestRows = await page.$$eval('[data-testid="rule-row"][data-source="manifest"]',
    (els) => els.map((e) => ({
      pattern: e.querySelector('.perms-pattern')?.textContent?.trim() ?? '',
      revoke: e.querySelector('[data-testid="revoke"]') !== null,
      text: e.textContent.trim(),
      href: e.querySelector('a')?.getAttribute('href') ?? '',
    })))
  ok('a manifest rule is listed, says why it cannot be revoked, and links to the file',
    manifestRows.length === 2
    && manifestRows.every((r) => !r.revoke && /declared in the manifest/.test(r.text)
                                 && r.href.includes('.factory/factory.json')),
    manifestRows.map((r) => `${r.pattern} revoke=${r.revoke}`).join(' | ') || 'none')

  // now grant one for real: ask → the lane goes blocked with the request id → answer `always`
  void fetch(`${BASE}/api/_test/permission-ask`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ lane: silent, tool: 'Bash', input: { command: 'npm run build' } }),
  }).catch(() => {})   // it HOLDS its response until the decision arrives — never awaited here

  let requestId = null
  for (let i = 0; i < 60 && !requestId; i += 1) {
    await page.waitForTimeout(100)
    const rows = (await api('GET', '/api/lanes')).body?.lanes ?? []
    requestId = rows.map((l) => l.state?.blocked?.request_id).find(Boolean) ?? null
  }
  ok('an out-of-policy tool call blocks its lane on a request a person can answer',
    requestId !== null, String(requestId))
  const granted = await api('POST', '/api/permissions/decisions',
    { request_id: requestId, decision: 'always' })
  ok('`allow always` writes exactly one rule', granted.status === 200
    && granted.body?.decision === 'always' && Boolean(granted.body?.rule_id),
    JSON.stringify(granted.body))

  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="rule-row"][data-source="granted"]', { timeout: 10000 })
    .catch(() => {})
  const grantedRow = await page.$$eval('[data-testid="rule-row"][data-source="granted"]',
    (els) => els.map((e) => ({
      rule: e.getAttribute('data-rule'),
      pattern: e.querySelector('.perms-pattern')?.textContent?.trim() ?? '',
      when: e.querySelector('.perms-when')?.textContent?.trim() ?? '',
      matches: e.querySelector('.perms-matches')?.textContent?.trim() ?? '',
      revoke: e.querySelector('[data-testid="revoke"]') !== null,
    })))
  ok('the rule it wrote is the rule it showed, listed with its match count and a revoke',
    grantedRow.length === 1 && grantedRow[0].pattern === 'Bash(npm run build)'
    && grantedRow[0].revoke && /\d{4}-\d\d-\d\d/.test(grantedRow[0].when)
    && /match/.test(grantedRow[0].matches),
    JSON.stringify(grantedRow[0] ?? null))

  // revoking is one click, and the list changes because M12 said so — no reload anywhere here
  await tryClick(page, '[data-testid="rule-row"][data-source="granted"] [data-testid="revoke"]')
  const gone = await page.waitForFunction(
    () => document.querySelectorAll('[data-testid="rule-row"][data-source="granted"]').length === 0,
    { timeout: 8000 }).then(() => true).catch(() => false)
  ok('revoke removes the rule, live, off the broadcast rather than a refetch', gone)
  const afterRevoke = (await api('GET', '/api/permissions/rules')).body
  ok('and the tool agrees the rule is gone',
    (afterRevoke?.rules ?? []).every((r) => r.source === 'manifest'),
    JSON.stringify((afterRevoke?.rules ?? []).map((r) => r.pattern)))

  // the policy write, from a click. `ask_all` on purpose: a gate that leaves a fixture on
  // `never_ask` is a gate that made the tool less careful than it found it.
  await tryClick(page, '[data-testid="policy-input-ask_all"]')
  await page.waitForSelector('[data-testid="policy-applied"]', { timeout: 8000 }).catch(() => {})
  const appliedLine = (await page.textContent('[data-testid="policy-applied"]').catch(() => ''))
    .trim()
  ok('a policy change says which value, and that it applies to lanes started afterwards',
    /applies to lanes started afterwards/.test(appliedLine), appliedLine)
  const policyNow = (await api('GET', '/api/permissions/rules')).body
  ok('and the tool holds it, as a session value rather than a rewritten manifest',
    policyNow?.policy === 'ask_all' && policyNow?.policy_source === 'session',
    `${policyNow?.policy} / ${policyNow?.policy_source}`)
  }

  // ── 8f · the SESSION tab and the composer (WEB-FEAT-014 · SPEC.md §4.6, §4.7) ─────────────
  //
  // WRITTEN BUT NOT RUN on the ticket's own branch: five client lanes were built in parallel and
  // five headless Chromes on one laptop make each other flaky, so the browser gate runs once,
  // over the integrated union. Everything below is against the REAL server — entries are pushed
  // through the same `/api/_test/transcript` hook `SRV-FEAT-005` added, so what the column
  // renders came off the socket rather than out of a fixture file.
  //
  // WEB-FEAT-379 — 8f through 8k are ONE guarded region and not six, and the same labelled block
  // 8e uses. They share the lanes section 8 claimed, they hand them back to each other by name, and
  // 8k releases the run lane section 10 goes on to claim — so a scope that ran four of them and
  // skipped two would be a fixture nobody designed. The live surfaces are in the guard for that
  // last reason.
  sessionSections: {
  if (!sectionInScope('the SESSION tab, the composer, the transcript and the role lane (8f-8k)',
    'ticket-session', 'all-lanes', 'role-lane-rail', 'role-lane-all-lanes',
    ...LIVE_SURFACES.map(([name]) => name))) break sessionSections

  const push = (lane, run, entry) => api('POST', '/api/_test/transcript', { lane, run_id: run, entry })
  const readControl = () =>
    page.$$eval('[data-testid="composer-button"]', (els) => els.map((e) => ({
      label: e.textContent.trim(), mode: e.getAttribute('data-mode'),
      colour: getComputedStyle(e).color,
      x: Math.round(e.getBoundingClientRect().x), y: Math.round(e.getBoundingClientRect().y),
    })))
  const motionOf = async (name) => {
    const row = ((await api('GET', '/api/lanes')).body?.lanes ?? []).find((r) => r.lane === name)
    return `${name} is ${row?.state?.process}/${row?.state?.motion?.value}`
      + ` held=${row?.held_count ?? '?'}`
  }

  if (silent) {
    // ── WEB-FIX-037 · EVERY READING IS TAKEN IN THE STATE ITS ASSERTION NAMES ─────────────────
    //
    // The three assertions below are unchanged. What was wrong was WHEN they read the button.
    // The section read the control once, before it had made the lane quiet, and then asserted
    // `Send on a quiet lane` against that reading; it asserted `once the lane is producing` after
    // 104 transcript pushes, which do not make a lane produce — `/api/_test/transcript` appends to
    // the socket's sequence log and never touches `lastOutputMs`, and `lastOutputMs` is the only
    // input `lane::state::axesOf` derives `motion` from (`app/lane/store.cpp` stamps it when the
    // CHILD writes); and it asserted `mid-turn` on TKT-147's lane after its own `advance_s: 130`
    // had silenced every running lane, that one included. Three red assertions, one client that
    // agreed with `/api/lanes` at every single moment. So: mid-turn first, while the lanes really
    // are mid-turn, and the silence last, after the clock has actually made it.

    let stop = []
    if (producing) {
      await page.goto(`${BASE}/#/tickets/TKT-147/session`, { waitUntil: 'networkidle' })
      await page.reload({ waitUntil: 'networkidle' })
      await page.waitForSelector('[data-testid="composer-button"]', { timeout: 10000 })
        .catch(() => {})
      stop = await readControl()
      const lane = ((await api('GET', '/api/lanes')).body?.lanes ?? [])
        .find((row) => row.lane === producing)
      const producingControl = producingControlCheck(lane, stop)
      ok(producingControl.name, producingControl.passed, producingControl.detail)
    }

    await page.goto(`${BASE}/#/tickets/TKT-142/session`, { waitUntil: 'networkidle' })
    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="composer"]', { timeout: 10000 }).catch(() => {})

    // one entry of each of the four shapes a reader meets most, in order
    await push(silent, 'gate-run', { kind: 'prose', text: 'Reading the build task now.' })
    await push(silent, 'gate-run', {
      kind: 'tool', tool: 'Bash', summary: 'cd core && pytest tests/ -v', ok: true,
      duration_s: 4.81, input: 'cd core && pytest tests/ -v',
      result: '32 passed, 1 failed in 4.81s',
    })
    await push(silent, 'gate-run', {
      kind: 'file_edit', path: 'core/tasks/build.py', added: 18, removed: 6,
      diff: '@@ -40,6 +40,18 @@\n-    return True\n+    return is_fresh(artifact, sources)',
    })
    await push(silent, 'gate-run', { kind: 'turn', n: 4, duration_s: 192, cost_usd: 0.41 })
    await page.waitForTimeout(400)

    const kinds = await page.$$eval('[data-testid="entry"]',
      (els) => els.map((e) => e.getAttribute('data-kind')))
    // The four this section pushed are the LAST four, in the order they were pushed — and every
    // entry on the screen is a kind §4.6 declares.
    //
    // Until SRV-FIX-049 this read `kinds.join(',') === 'prose,tool,file_edit,turn'` and passed,
    // which was the defect wearing a green tick: the reload above asks for a snapshot, this lane
    // already held 8e's `permission_request` and `permission_decision`, and the snapshot poured
    // whole M5 frames into E8's `entries` so both of them rendered as NOTHING. The assertion was
    // measuring an empty snapshot and calling it seq order. It is now strictly stronger — the
    // four in order AND nothing on the screen that failed to draw.
    ok('the transcript renders what arrived on the socket, in seq order',
      kinds.slice(-4).join(',') === 'prose,tool,file_edit,turn'
        && kinds.length >= 4 && kinds.every((k) => k && k !== 'null'),
      kinds.join(' · ') || 'nothing rendered')

    // F12 — one line, and clicking it reveals INPUT and RESULT in the real browser
    await page.click('[data-kind="tool"] [data-testid="entry-line"]')
    await page.waitForTimeout(150)
    const opened = await page.textContent('[data-kind="tool"]').catch(() => '')
    ok('a tool line expands to INPUT and RESULT',
      /INPUT/.test(opened) && /RESULT/.test(opened) && /32 passed/.test(opened),
      opened.replace(/\s+/g, ' ').slice(0, 90))

    // F13 — the diff, and NOT INPUT/RESULT
    await page.click('[data-kind="file_edit"] [data-testid="entry-line"]')
    await page.waitForTimeout(150)
    const edit = await page.textContent('[data-kind="file_edit"]').catch(() => '')
    ok('a file_edit reads `Edit <path> · +18 −6` and expands to the diff, not to INPUT/RESULT',
      /core\/tasks\/build\.py/.test(edit) && /\+18 −6/.test(edit)
        && /is_fresh\(artifact, sources\)/.test(edit) && !/INPUT/.test(edit),
      edit.replace(/\s+/g, ' ').slice(0, 90))

    // THE NEGATIVE, in a real browser with a real caret: a half-typed message and a hundred
    // entries. This is the one the prototype failed.
    await page.click('[data-testid="composer-draft"]')
    await page.type('[data-testid="composer-draft"]', 'the gate is red on the third assertion')
    await page.evaluate(() => {
      const el = document.querySelector('[data-testid="composer-draft"]')
      el.setSelectionRange(20, 20)
    })
    for (let i = 0; i < 100; i += 1) {
      await push(silent, 'gate-run', { kind: 'prose', text: `line ${i}` })
    }
    await page.waitForTimeout(600)
    const after = await page.evaluate(() => {
      const el = document.querySelector('[data-testid="composer-draft"]')
      return { value: el.value, caret: el.selectionStart, focused: document.activeElement === el,
               entries: document.querySelectorAll('[data-testid="entry"]').length }
    })
    ok('a hundred entries do not touch the draft, in a real browser',
      after.value === 'the gate is red on the third assertion' && after.caret === 20
        && after.focused && after.entries >= 104,
      `"${after.value}" caret ${after.caret} focused ${after.focused} entries ${after.entries}`)

    const composerText = (await page.textContent('[data-testid="composer"]')) ?? ''
    ok('End session, Kill and `restart demo` are not in the composer (§8 D1, §8 D6)',
      !/End session|Kill|restart demo/.test(composerText),
      composerText.replace(/\s+/g, ' ').slice(0, 90))

    // §4.7's first state needs a lane that is QUIET, and this section must not assume the one
    // section 8 left behind: 8e pushes a real permission request through this same lane, and its
    // decision stamps activity, which puts `motion` back to `producing`. So make the silence
    // rather than inherit it — the clock is the tool's own, and 120s is §3.2's threshold. The wait
    // is the whole point: no reload, no refetch, one `M2 lane.state` frame off the socket.
    await api('POST', '/api/_test/clock', { advance_s: 130 })
    await page.waitForFunction(
      () => document.querySelector('[data-testid="composer-button"]')?.getAttribute('data-mode')
        === 'send', null, { timeout: 15000 }).catch(() => {})

    // The failure message carries the lane row the button is drawn from, because "it says Stop"
    // without the state it read is a fact with no cause attached.
    const quiet = await readControl()
    ok('the composer carries exactly ONE control, and it reads Send on a quiet lane',
      quiet.length === 1 && quiet[0].label === 'Send' && quiet[0].mode === 'send',
      `${quiet.map((c) => `${c.label} (${c.mode})`).join(' + ') || 'no control'}`
        + ` — ${await motionOf(silent)}`)

    // and the same control, in the same seat, in the crash colour on a lane that is producing.
    // Compared against `quiet` — the Send reading on a lane that really has fallen silent —
    // because §4.7's claim is that ONE control changes word and colour without moving. Comparing
    // two Stop readings would compare a colour with itself and pass for free.
    if (producing) {
      ok('mid-turn the SAME one control reads Stop, in the crash colour, in the same seat',
        stop.length === 1 && stop[0].mode === 'stop' && /^Stop$/.test(stop[0].label)
          && stop[0].colour !== quiet[0]?.colour
          && stop[0].x === quiet[0]?.x && stop[0].y === quiet[0]?.y,
        `${stop.map((s) => `${s.label} (${s.mode}) ${s.colour} @${s.x},${s.y}`).join(' · ')
          || 'none'} vs ${quiet.map((s) => `${s.label} (${s.mode}) ${s.colour} @${s.x},${s.y}`)
          .join(' · ') || 'none'}`)
    } else {
      skipped('mid-turn the SAME one control reads Stop, in the crash colour, in the same seat',
        'no lane was claimed for TKT-147, so §4.7 was never read on a producing lane')
    }
  } else {
    skipped('the SESSION tab and the composer (8f)',
      'no lane was claimed for TKT-142, so nothing in §4.6 or §4.7 was exercised')
  }

  // ══ 8g · WEB-FEAT-044 — a lane claimed and released BY A PERSON (E12, E13, §4.4, §4.7) ══════
  //
  // Every lane above was claimed by `fetch()` from this script, because until this ticket the
  // client could not make one. This section is the first time the BROWSER claims a lane: a click
  // on §4.7's control, the real E12, a real worktree and a real branch, and then E13 taking them
  // away again. A gate that only asserted the endpoints answer would be a gate for a screen with
  // no button on it.
  //
  // TKT-160 is the fixture's orphan ticket — workspace `core`, needs no exclusive resource — and
  // section 8 deliberately left it unclaimed, so the empty state here is real rather than arranged.
  //
  // The seat clicked is §4.7's, not §4.4's, and that is the specification's doing: §4.4's Empty is
  // "no lanes exist" (globally), and six exist by now. The rail's seat is covered by
  // `tests/claim.test.tsx::test_claims_with_the_tickets_own_workspace`, which mounts the whole app
  // against a server holding none.
  const dialogs = []
  page.on('dialog', (d) => { dialogs.push(d.message()); void d.accept() })

  // E6's 404 IS §4.9's empty state, for TKT-160 exactly as for TKT-142 — the RUNS tab reads the
  // report to decide which run §3.4 calls proven, and this ticket has none. Declared where the
  // surface is reached rather than in the list at the top of the file, so a concurrent lane
  // appending its own does not meet this diff.
  EXPECTED_404.push('/api/tickets/TKT-160/report')

  await page.goto(`${BASE}/#/tickets/TKT-160/session`, { waitUntil: 'networkidle' })
  await page.reload({ waitUntil: 'networkidle' })
  await page.waitForSelector('[data-testid="claim-lane"]', { timeout: 10000 }).catch(() => {})

  const composerSeat = '[data-testid="composer"] [data-testid="claim-lane"]'
  const seatText = (await page.textContent(composerSeat).catch(() => '')).trim()
  const idleText = (await page.textContent('[data-testid="composer"]').catch(() => '')).trim()
  ok('§4.7\'s empty state carries a claim control a person can reach',
    seatText === 'Start a lane' && /Start a lane to send a message\./.test(idleText),
    `"${seatText}" · "${idleText.replace(/\s+/g, ' ').slice(0, 60)}"`)

  const lanesBefore = ((await api('GET', '/api/lanes')).body?.lanes ?? []).length
  const claimedByHand = await tryClick(page, composerSeat)
  let mine = null
  for (let i = 0; i < 80 && !mine; i += 1) {
    await page.waitForTimeout(100)
    mine = ((await api('GET', '/api/lanes')).body?.lanes ?? [])
      .find((l) => l.ticket_id === 'TKT-160') ?? null
  }
  ok('a click claims a REAL lane, in the ticket\'s own workspace and the default role',
    claimedByHand && mine !== null && mine.workspace === 'core' && mine.role === 'run'
      && mine.branch === 'ticket/TKT-160',
    mine ? `${mine.lane} ${mine.workspace}/${mine.role} on ${mine.branch}` : 'no lane appeared')

  if (mine) {
    // E12's `201` is E9's own lane object, so the card is drawn from the RESPONSE. No reload here
    // on purpose — a reload would prove the endpoint and hide the thing the ticket is about.
    const shown = await page.waitForSelector(`[data-testid="lane-card"][data-lane="${mine.lane}"]`,
      { timeout: 8000 }).then(() => true).catch(() => false)
    ok('and the rail shows it with no reload and no refetch — E12\'s 201 IS the row', shown)
    const count = (await page.textContent('[data-testid="lane-count"]').catch(() => '')).trim()
    // CHANGED BY WEB-FEAT-050, deliberately. This asserted `lanesBefore + 1` — the REPOSITORY's
    // total — because §4.4's rail was global. It is now about the open ticket, so the number that
    // has to move is TKT-160's own: from none to one. Asserting the global total here would be
    // asserting the rail this ticket removed, and the second clause keeps that honest.
    ok('the rail\'s own count moved with it — this ticket\'s lanes, not the repository\'s',
      count === '1' && count !== String(lanesBefore + 1),
      `count reads "${count}"; TKT-160 now has 1 lane, the repository has ${lanesBefore + 1}`)

    // the worktree really is on disk — a lane is a directory and a branch, not a row in a table
    const diff = await api('GET', `/api/lanes/${mine.lane}/diff`)
    ok('the lane it made is a real worktree on a real branch',
      diff.status === 200 && diff.body?.branch === 'ticket/TKT-160',
      `${diff.status} ${diff.body?.branch ?? ''}`)

    // ── WEB-FEAT-045 — the SAME click started a run in it (E18) ─────────────────────────────
    //
    // §1's pre-decision: one click is claim + start, so nobody meets a claimed-but-empty lane. The
    // agent spawned here is the `/bin/sh` copy `run-gate.sh` puts on PATH as `claude` — a real
    // spawn, a real pid, a real run recorded, and nothing that costs a token. Its outcome is
    // whatever `sh` does with the CLI's argv, and this asserts that a run EXISTS and is the tool's
    // own record of it, not what the process did.
    let started = null
    for (let i = 0; i < 100 && !started; i += 1) {
      await page.waitForTimeout(100)
      started = ((await api('GET', '/api/tickets/TKT-160/runs')).body?.runs ?? [])[0] ?? null
    }
    ok('ONE click claimed the lane and started a run in it — E12 then E18, no second control',
      started !== null && typeof started.run_id === 'string' && started.n === 1,
      started ? `${started.run_id} · n=${started.n} · ${started.outcome}`
        : `no run recorded for TKT-160 — server.log: ${
          (await api('GET', `/api/lanes/${mine.lane}/diff`)).status}`)

    if (started) {
      // and the RUNS tab shows it. The hash change is a SAME-DOCUMENT navigation: no reload, and
      // the app keeps everything it had — the row is read by the tab that just mounted.
      // `M11` patching an ALREADY-OPEN table with no refetch at all is
      // `tests/start-run.test.tsx::test_the_run_appears_in_runs_without_a_refetch`, which can hold
      // the tab open across the start in a way a browser gate cannot reach from one control.
      await page.goto(`${BASE}/#/tickets/TKT-160/runs`, { waitUntil: 'networkidle' })
      await page.waitForSelector('[data-testid="run-row"]', { timeout: 10000 }).catch(() => {})
      const painted = await page.$$eval('[data-testid="run-row"]', (els) => els.map((e) => ({
        n: e.querySelector('[data-testid="run-n"]')?.textContent?.trim() ?? '',
        outcome: e.querySelector('[data-testid="run-outcome"]')?.textContent?.trim() ?? '',
      })))
      ok('the run the click started is on the RUNS tab, as a row a person can open',
        painted.length === 1 && painted[0].n === '#1' && painted[0].outcome !== '',
        painted.map((r) => `${r.n} ${r.outcome}`).join(' | ') || 'no rows')

      // WEB-FIX-091, on the state the audit photographed: a run that started AFTER this ticket's
      // document was read. The header may not still be reporting the count from before it.
      const liveTab = (await page.textContent('[data-tab="runs"]').catch(() => '')).trim()
      const liveId = (await page.textContent('[data-testid="ticket-metrics"]').catch(() => '')).trim()
      ok('a run that just started is counted by the header above it, not only listed',
        liveTab === `RUNS · ${painted.length}` && liveId.startsWith(`${painted.length} run`),
        `${liveTab} · ${liveId} · ${painted.length} rows`)

      await page.goto(`${BASE}/#/tickets/TKT-160/session`, { waitUntil: 'networkidle' })
      await page.waitForSelector('[data-testid="lane-card"]', { timeout: 10000 }).catch(() => {})
    }

    // ── E13, from the card. Two dialogs are possible and only the first is certain: the second
    //    appears exactly when a process is live, and whether one is depends on WEB-FEAT-045.
    const card = `[data-testid="lane-card"][data-lane="${mine.lane}"]`
    await page.hover(card).catch(() => {})
    dialogs.length = 0
    // A 409 is the SPECIFIED answer to releasing a live lane, so it must not count as a bad
    // response — the force dialog is what the client does with it.
    EXPECTED_404.push(`/api/lanes/${mine.lane}`)
    const asked = await tryClick(page, `${card} [data-testid="lane-release"]`)
    const gone = await page.waitForFunction(
      (selector) => document.querySelector(selector) === null, card,
      { timeout: 15000 }).then(() => true).catch(() => false)
    ok('release asks first, and the question says what survives',
      asked && dialogs[0] === 'Release lane? The branch is kept.',
      dialogs.join(' | ') || 'no dialog')
    ok('and on the answer the card is gone from the rail', gone)
    const after = ((await api('GET', '/api/lanes')).body?.lanes ?? [])
      .some((l) => l.ticket_id === 'TKT-160')
    ok('the tool agrees the lane is released, and kept the branch',
      after === false, after ? 'still claimed' : 'released')
    const branches = await api('GET', `/api/lanes/${mine.lane}/diff`)
    ok('E13\'s promise, checked against the tool: the lane is gone, not the work',
      branches.status === 404, `HTTP ${branches.status}`)

    // TKT-160 has no lane again, so §4.7's empty state and its control are on this surface for
    // section 9's screenshots and its contrast sweep — in BOTH themes, which is §1's own words.
    SURFACES.push(['ticket-no-lane', '#/tickets/TKT-160/session'])

    // WEB-FIX-088 — and this is the state the audit photographed: a ticket whose lane has been
    // RELEASED still has its runs, and §4.6's Empty is about runs, not about lanes. The two
    // strings may not co-occur, on the surface where the contradiction was found.
    await page.goto(`${BASE}/#/tickets/TKT-160/session`, { waitUntil: 'networkidle' })
    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="session-empty"], [data-testid="transcript"]',
                               { timeout: 10000 }).catch(() => {})
    const noLaneTab = (await page.textContent('[data-tab="runs"]').catch(() => '')).trim()
    const noLaneText = (await page.textContent('[data-testid="surface"]').catch(() => '')) ?? ''
    const ranHere = Number(/RUNS · (\d+)/.exec(noLaneTab)?.[1] ?? '0')
    ok('a ticket with runs and no lane is not told that nothing has run',
      ranHere > 0 && !noLaneText.includes('Nothing has run on this ticket yet.'),
      `${noLaneTab} · says-nothing-ran=${noLaneText.includes('Nothing has run on this ticket yet.')}`)
  } else {
    skipped('E13 · release a lane from its card', 'nothing was claimed for TKT-160 to release')
  }


  // ── the queued card, in both themes (§1's other gate criterion) ───────────────────────────
  //
  // Section 8 already asserted that a queued lane names its resource; what WEB-FEAT-044 added is
  // the DESCRIPTION beside it, which is the only place a person is told what `climate-chamber` is.
  // Read in both themes because §1 asks for both, and because a sub-line is the smallest type on
  // the smallest surface — the two places a colour goes wrong.
  if (queued) {
    const subs = {}
    for (const theme of SCOPE.themes) {
      await page.goto(`${BASE}/#/tickets/TKT-133`, { waitUntil: 'networkidle' })
      await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
      await page.reload({ waitUntil: 'networkidle' })
      await page.waitForSelector('[data-testid="lane-card"]', { timeout: 10000 }).catch(() => {})
      subs[theme] = await page.$eval(`[data-testid="lane-card"][data-lane="${queued}"]`,
        (el) => ({
          sub: el.querySelector('[data-testid="lane-sub"]')?.textContent?.trim() ?? '',
          colour: getComputedStyle(el.querySelector('[data-testid="lane-sub"]') ?? el).color,
          // the rail's, not the card's: a selected card whose tone is `none` takes a
          // TRANSPARENT tint by design, so the ground behind the words is the pane's
          ground: getComputedStyle(el.closest('.rail') ?? el).backgroundColor,
          applied: document.documentElement.getAttribute('data-theme'),
        })).catch(() => ({ sub: '', colour: '', ground: '', applied: null }))
    }
    ok(`a queued card says what it is waiting for, in words, in ${themeWords()}`,
      SCOPE.themes.every((t) => /queued for climate-chamber/.test(subs[t].sub)
        && /one chamber, one run at a time/.test(subs[t].sub)
        && /held by lane\/\d+/.test(subs[t].sub) && /#\d/.test(subs[t].sub)),
      SCOPE.themes.map((t) => `${t}: "${subs[t].sub}"`).join(' · '))
    // Each theme really applied, and the line is painted over a DIFFERENT ground in each. Not "in
    // a different colour": `--t3` is one value in both themes on purpose (`tokens.css`), and the
    // sub-line takes it — asserting the ink moved would be asserting the design is something else.
    // What has to be true is that the card behind it changed and the words survived it.
    // WEB-FEAT-379 — quantified over the themes this run swept rather than over the pair, and
    // the grounds are compared as a SET so the sentence stays *each theme over its own ground*
    // whether that is two themes or one.
    const grounds = new Set(SCOPE.themes.map((t) => subs[t].ground))
    ok('and each theme really applied, over its own ground, with the line still painted',
      SCOPE.themes.every((t) => subs[t].applied === t && subs[t].colour !== '')
        && grounds.size === SCOPE.themes.length,
      `${SCOPE.themes.map((t) => `${subs[t].applied}:${subs[t].ground}`).join(' vs ')}`
        + ` · ink ${subs[SCOPE.themes[0]].colour}`)
  } else {
    skipped(`a queued card says what it is waiting for, in ${themeWords()}`,
      'no lane was queued on climate-chamber in section 8')
  }

  // ── 8h · WEB-FEAT-046 · E19 + M10 — the workspace's own build and gate, from the header ────
  //
  // INTEGRATION NOTE: both lanes of this batch wrote a section they each called `8g`. 044/045's
  // kept the letter because it landed first; this one is `8h`. Nothing else about either changed
  // — they share `api`, `page`, `ok` and section 8's lanes and no local state of each other's,
  // which is what made the two appends reviewable instead of a hand-merge.
  //
  // Its own block, reaching into nothing above it but `api`, `page`, `ok`, `skipped` and the two
  // lanes section 8 claimed: WEB-FEAT-044 and 045 are editing this file in parallel and a section
  // that borrows another one's local state is a merge nobody can review.
  //
  // §1's end-to-end, in the browser, against the real server: a ticket with a lane, a click on
  // Gate, and the REAL command's output and exit code arriving on M10. `core.gate` is
  // `cd core && pytest tests/ -v` and this fixture has no `core` directory, so the shell fails and
  // says why — which is the whole point of E19: the tool runs the declared string and reports the
  // exit code, and has no opinion about either. Nothing but the shell is involved, so this check
  // does not depend on what is installed on the machine running the gate.
  //
  // The click needs a lane whose PROCESS SLOT IS FREE. `lane::Store::startProcess` refuses a lane
  // that already holds a live process — `409 wrong_state`, naming what is in the way — and both
  // lanes section 8 claimed in `core` are running the stand-in agent. So this claims one of its
  // own, for a ticket in `core` that section 8 left alone, rather than quietly asserting the
  // refusal and calling it the feature.
  if (silent && holding) {
    const declared = (await api('GET', '/api/manifest')).body?.workspaces ?? {}

    await page.goto(`${BASE}/#/tickets/TKT-142/session`, { waitUntil: 'networkidle' })
    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="commands"]', { timeout: 10000 }).catch(() => {})

    const read = () => page.$$eval('[data-testid="commands"] .cmd-button', (els) => els.map((e) => ({
      label: e.textContent.trim(), which: e.getAttribute('data-which'),
      title: e.getAttribute('title'), disabled: e.disabled,
    })))
    const buttons = await read()
    ok('the header offers the two commands `core` declares, as the words Build and Gate',
      buttons.length === 2 && buttons[0].label === 'Build' && buttons[1].label === 'Gate'
        && buttons.every((b) => !b.disabled),
      buttons.map((b) => `${b.label}${b.disabled ? ' (disabled)' : ''}`).join(' · ') || 'none')
    ok('and the tooltip is the string the MANIFEST declares, not one this client invented',
      buttons.length === 2 && buttons[0].title === declared.core?.build
        && buttons[1].title === declared.core?.gate,
      buttons.map((b) => `${b.which}="${b.title}"`).join(' · ') || 'none')

    const idle = await claim('TKT-160', 'core')
    if (idle) {
      await page.goto(`${BASE}/#/tickets/TKT-160/session`, { waitUntil: 'networkidle' })
      await page.reload({ waitUntil: 'networkidle' })
      await page.waitForSelector('[data-testid="command-gate"]', { timeout: 10000 }).catch(() => {})

      // The click and the reading are ONE MACROTASK in the page, on purpose. `disabled` is set
      // inside the React handler; React 18 flushes that in a MICROTASK, so the read has to yield
      // the microtask queue and no further — a `fetch` cannot resolve without a macrotask, so
      // neither the `202` nor an M10 exit from a command that fails in two milliseconds can land
      // in this window. A `page.click()` followed by a separate read is the same assertion with a
      // race added, and this is the one assertion in §1 that must not be able to pass by luck.
      const heldAtClick = await page.evaluate(async () => {
        document.querySelector('[data-testid="command-gate"]').click()
        for (let i = 0; i < 3; i += 1) await Promise.resolve()
        return Array.from(document.querySelectorAll('.cmd-button')).every((e) => e.disabled)
      })
      ok('clicking Gate holds BOTH controls at once — one lane runs one command',
        heldAtClick === true)

      // No poll and no timeout of the client's own: the exit code arrives on M10 or not at all.
      await page.waitForFunction(
        () => /exit \d+/.test(document.querySelector('[data-kind="command"]')?.textContent ?? ''),
        null, { timeout: 30000 }).catch(() => {})
      const line = await page.textContent('[data-kind="command"]').catch(() => '')
      ok('the command it declared ran, and its exit code landed on the entry',
        /gate/.test(line) && /exit \d+/.test(line),
        line.replace(/\s+/g, ' ').slice(0, 90) || 'no entry')

      await tryClick(page, '[data-kind="command"] [data-testid="entry-line"]')
      await page.waitForTimeout(150)
      const output = await page.textContent('[data-kind="command"]').catch(() => '')
      ok('and the output the command wrote is in the transcript, under OUTPUT',
        /OUTPUT/.test(output) && /core/.test(output.replace(/gate/g, '')),
        output.replace(/\s+/g, ' ').slice(0, 120) || 'no entry')

      const after = await read()
      ok('the exit is what gives the controls back',
        after.length === 2 && after.every((b) => !b.disabled),
        after.map((b) => `${b.label}${b.disabled ? ' (disabled)' : ''}`).join(' · ') || 'none')

      // ── WEB-FIX-072 · and it is still there after leaving the tab and coming back ──────────
      //
      // §1 asked for this in section 8i. It is HERE because 8i's lane holds a live process, so
      // `POST /api/lanes/{lane}/commands` answers `409 wrong_state` there — measured, and the
      // skip is in this ticket's round-1 gate log. This block is the only place in the gate where
      // a real command has really run, which is what the check needs.
      //
      // Nothing on the wire can replay a command: `M10` carries `seq: null`, it is outside the
      // run's sequence log, and the server keeps no copy of E19's output at all. 8i-b's journey
      // never covered it — the lane it walks has no `command` entry on screen — so a green gate
      // said nothing about this.
      //
      // The theme is set and the page RELOADED **before** the command is started, not after. A
      // reload is a new page: the socket module's store is memory, so a command's output cannot
      // survive one and no client-side fix could make it — the server keeps no copy to ask for.
      // The journey §1 names is leaving the TAB and coming back, and that is what this walks.
      for (const theme of themesFor('session-command-after-coming-back',
        'the command panel survives leaving the tab and coming back')) {
        await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
        await page.goto(`${BASE}/${'#/tickets/TKT-160/session'}`, { waitUntil: 'networkidle' })
        await page.reload({ waitUntil: 'networkidle' })
        await page.waitForSelector('[data-testid="command-gate"]', { timeout: 10000 })
          .catch(() => {})
        await tryClick(page, '[data-testid="command-gate"]')
        await page.waitForFunction(
          () => /exit \d+/.test(document.querySelector('[data-kind="command"]')?.textContent ?? ''),
          null, { timeout: 30000 }).catch(() => {})

        await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
        await page.waitForTimeout(400)
        await page.goto(`${BASE}/${'#/tickets/TKT-160/session'}`, { waitUntil: 'networkidle' })
        await page.waitForTimeout(1200)

        const back = await page.textContent('[data-kind="command"]').catch(() => '')
        await tryClick(page, '[data-kind="command"] [data-testid="entry-line"]')
        await page.waitForTimeout(200)
        const opened = await page.textContent('[data-kind="command"]').catch(() => '')
        // `OUTPUT` is the panel's LABEL — `<Panel label="OUTPUT">`, rendered for every expanded
        // command entry whatever `output` holds — so testing for it says the panel came back and
        // says nothing about what is in it (QA, F2). The stronger form was already in this block
        // fourteen lines up: the command declared for `core` writes `core` into its own output.
        ok(`the command panel survives leaving the tab and coming back (${theme})`,
          /gate/.test(back ?? '') && /exit \d+/.test(back ?? '')
            && /OUTPUT/.test(opened ?? '') && /core/.test((opened ?? '').replace(/gate/g, '')),
          (opened ?? 'NO COMMAND ENTRY').replace(/\s+/g, ' ').slice(0, 140))
        await shot(page, 'session-command-after-coming-back', theme)
      }
      await page.evaluate((t) => localStorage.setItem('factory.theme', t), SCOPE.themes[0])
      await page.reload({ waitUntil: 'networkidle' })

      // INTEGRATION: 8g released TKT-160 and registered `ticket-no-lane` as a §4.7-EMPTY surface
      // for section 9's screenshots and its contrast sweep. This block borrowed that same ticket
      // for its idle process slot, so it hands it back — a shot named for an empty state has to be
      // one, and the two sections were written without sight of each other.
      const handed = await api('DELETE', `/api/lanes/${idle}?force=true`)
      ok('and the lane this section borrowed is handed back, so 8g\'s empty surface stays empty',
        handed.status === 200, `HTTP ${handed.status}`)
    } else {
      skipped('E19 · running the declared command end to end',
        'no seat was free for a lane in `core` with an idle process slot')
    }

    // ── a workspace that declares only one of them offers only one of them. TKT-151's lane is in
    //    `edge`, which the fixture manifest declares with a gate and no build — so the note has a
    //    real manifest to be right about, and the key it names is the key E19 would name.
    await page.goto(`${BASE}/#/tickets/TKT-151/session`, { waitUntil: 'networkidle' })
    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="commands"]', { timeout: 10000 }).catch(() => {})
    const edge = await read()
    ok('a workspace with no `build` offers Gate alone — absent, not greyed out',
      edge.length === 1 && edge[0].which === 'gate' && edge[0].title === declared.edge?.gate,
      edge.map((b) => `${b.label}="${b.title}"`).join(' · ') || 'none')
    const note = (await page.textContent('[data-testid="commands-note"]').catch(() => '')) ?? ''
    ok('and a note names the manifest key that would have to exist',
      /workspaces\.edge\.build/.test(note), note.trim() || 'no note')
  } else {
    skipped('E19 · the workspace\'s build and gate commands (8h)',
      'section 8 claimed no lane for TKT-142 or TKT-151, so E19 had no worktree to run in')
  }

  // ── 8i · the transcript a snapshot draws (SRV-FIX-049 · SPEC.md §4.6, E8, §6.2) ───────────
  //
  // The user's report, in the user's own words: *"khi chuyển tab qua requirement quay lại thì mất
  // lỗi. send tin nhắn thì hiện lại lỗi."* — leave the ticket screen, come back, and the WHOLE
  // transcript is gone; send a message and it reappears.
  //
  // Running it in a browser found TWO independent causes for that one screen, and this section
  // is split along them because a single assertion covering both cannot say which is fixed:
  //
  //   1 · THE SNAPSHOT'S SHAPE (server, SRV-FIX-049). A lane snapshot's `entries` carried whole
  //       `M5` frames where E8 says entries, so every member had no `kind` and §4.6 renders an
  //       undeclared kind as nothing, deliberately. EVERY path that delivers a snapshot drew a
  //       blank transcript: first mount, reload, and the `replaces_tail` fallback — the last of
  //       which instructs the client to throw away a correct transcript first.
  //   2 · THE REMOUNT'S QUESTION (client, WEB-FIX-051). `services/socket.ts` keeps `lastSeq` per
  //       lane in a module-level map that outlives the component, so a surface that mounts fresh
  //       sends `resume {after_seq: <the seq the CONNECTION holds>}` where it needs `subscribe`.
  //       The server answers correctly — nothing was missed — and the surface, whose own state is
  //       empty, draws nothing. It only recovers when a later frame opens a gap and forces a
  //       resync, which is exactly "send a message and it comes back".
  //
  // 8i-a judges cause 1 and passes on this build. 8i-b judges the user's literal journey and is
  // RED, BLOCKED on WEB-FIX-051; it is left red rather than skipped, because WEB-FIX-037 made a
  // skipped check a failing one and a defect nobody can see in the log is a defect nobody fixes.
  if (silent) {
    const entryKinds = () => page.$$eval('[data-testid="entry"]',
      (els) => els.map((e) => e.getAttribute('data-kind')))

    // ── 8i-a · a snapshot is asked for, and what it carries is drawn ─────────────────────────
    // A reload is a new page: `socket.ts`'s lane map starts empty, so the surface sends
    // `subscribe` and the server answers `M1` with the lane's whole tail. That is the frame this
    // ticket is about, and this is it reaching a screen.
    await page.goto(`${BASE}/#/tickets/TKT-142/session`, { waitUntil: 'networkidle' })
    await page.reload({ waitUntil: 'networkidle' })
    await push(silent, 'gate-run', { kind: 'prose', text: 'The last thing the agent said.' })
    await page.waitForTimeout(400)
    const before = await entryKinds()

    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="entry"]', { timeout: 10000 }).catch(() => {})
    await page.waitForTimeout(400)
    const reloaded = await entryKinds()

    ok('a lane snapshot fills the transcript, and every entry in it draws',
      reloaded.length >= before.length && before.length > 0
        && reloaded.every((k) => k && k !== 'null'),
      `${before.length} entries live, ${reloaded.length} after a reload built them from the`
        + ` snapshot alone — kinds: ${[...new Set(reloaded)].join(' · ') || 'NOTHING RENDERED'}`)

    // the same list the server would hand E8 for that run, so the screen and the endpoint are
    // compared rather than each trusted on its own
    const lanes = (await api('GET', '/api/lanes')).body?.lanes ?? []
    const laneRow = lanes.find((r) => r.lane === silent)
    const replay = laneRow?.run_id
      ? (await api('GET', `/api/runs/${laneRow.run_id}/transcript?limit=1000`)).body
      : null

    for (const theme of themesFor('session-from-a-snapshot',
      "the snapshot's entries are on the screen in each theme")) {
      await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
      await page.reload({ waitUntil: 'networkidle' })
      await page.waitForSelector('[data-testid="entry"]', { timeout: 10000 }).catch(() => {})
      await page.mouse.move(0, 0)
      await page.waitForTimeout(400)
      const applied = await page.getAttribute('html', 'data-theme')
      const drawn = await entryKinds()
      await shot(page, 'session-from-a-snapshot', theme)
      ok(`the snapshot's entries are on the screen in the ${theme} theme`,
        applied === theme && drawn.length > 0 && drawn.every((k) => k && k !== 'null'),
        `theme ${applied}, ${drawn.length} entries drawn`
          + `${replay ? `, E8 holds ${replay.entries?.length ?? 0} for this run` : ''}`)
    }

    // ── 8i-b · the user's own journey: away to #/requirements and back ───────────────────────
    // BLOCKED(WEB-FIX-051). Nothing on the server can answer this: the connection really is
    // caught up and only the SURFACE knows it has just been rebuilt from nothing.
    await page.goto(`${BASE}/#/tickets/TKT-142/session`, { waitUntil: 'networkidle' })
    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="entry"]', { timeout: 10000 }).catch(() => {})
    await page.waitForTimeout(400)
    const onScreen = await entryKinds()

    await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="requirement-row"]', { timeout: 10000 })
      .catch(() => {})
    await page.goto(`${BASE}/#/tickets/TKT-142/session`, { waitUntil: 'networkidle' })
    await page.waitForTimeout(1500)
    const andBack = await entryKinds()

    // WEB-FIX-051 landed; the `BLOCKED` annotation is gone with it and this check is judged
    // like any other. It was left RED rather than skipped for a day, which is WEB-FIX-037's rule
    // working: a defect nobody can see in the log is a defect nobody fixes.
    ok('leaving the SESSION tab and coming back leaves the transcript on the screen',
      andBack.length >= onScreen.length && onScreen.length > 0
        && andBack.every((k) => k && k !== 'null'),
      `${onScreen.length} entries before leaving, ${andBack.length} after coming back`
        + ` — kinds after: ${[...new Set(andBack)].join(' · ') || 'NOTHING RENDERED'}`)

    // §1 asks for this check GREEN **with a screenshot in both themes**, and round 1 produced
    // neither the loop nor the shot: the two SESSION images that existed belonged to other checks
    // on other paths, and neither would have gone red on the pre-fix build (WEB-FIX-051 QA, F1).
    // The journey is re-walked per theme rather than the theme being flipped at the end, because
    // the evidence has to be of THIS journey — away and back — in the theme it is filed under.
    for (const theme of themesFor('session-after-coming-back',
      '...and it is still there after the same away-and-back, in each theme')) {
      await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
      await page.reload({ waitUntil: 'networkidle' })
      await page.waitForTimeout(400)
      await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(400)
      await page.goto(`${BASE}/#/tickets/TKT-142/session`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(1200)
      const kinds = await entryKinds()
      const applied = await page.getAttribute('html', 'data-theme')
      ok(`...and it is still there in the ${theme} theme, after the same away-and-back`,
        applied === theme && kinds.length > 0 && kinds.every((k) => k && k !== 'null'),
        `theme=${applied} · ${kinds.length} entries · `
          + `${[...new Set(kinds)].join(' · ') || 'NOTHING RENDERED'}`)
      await shot(page, 'session-after-coming-back', theme)
    }
    await page.evaluate((t) => localStorage.setItem('factory.theme', t), SCOPE.themes[0])
    await page.reload({ waitUntil: 'networkidle' })

    ok('and one live entry brings the whole transcript back, which is the reported workaround',
      await (async () => {
        await push(silent, 'gate-run', { kind: 'prose', text: 'One more, after coming back.' })
        await page.waitForTimeout(800)
        return (await entryKinds()).length > andBack.length
      })(), `${andBack.length} → ${(await entryKinds()).length} entries after one push`)

    // ── 8i-c · a SECOND run starts while the surface is away (WEB-FIX-073) ───────────────────
    //
    // What this check can and cannot say, stated here rather than discovered by the next reader:
    // it is a REGRESSION GUARD on the journey, not a discriminator for the defect. The server
    // hands one run over to the next on the strand (`Socket::handOver_` → `SequenceLog::beginRun`
    // → `replaces_tail`) and a surface that mounts fresh sends `subscribe`, which is answered with
    // a snapshot of the new run. So the SCREEN is corrected either way, which is what §1 of the
    // ticket says in its own words: *"what a person sees is a flash of the wrong run, not a
    // permanent loss."* The module's record is what was wrong, and `socket.test.ts::
    // a_new_run_is_not_a_replay` is where that is judged, with no surface involved.
    //
    // It is here because the journey it walks is the one a person actually walks, and because a
    // future change that breaks the handover breaks this and nothing else in the gate.
    const texts = () => page.$$eval('[data-testid="entry"]', (els) => els.map((e) => e.textContent))
    for (const theme of themesFor('session-second-run',
      'a run that started while the tab was away is the one on screen, in each theme')) {
      await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
      await page.reload({ waitUntil: 'networkidle' })
      await page.waitForTimeout(400)
      await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(400)

      // a second run in the same lane, numbering from 1 again, with nothing mounted to see it
      const mark = `RUN TWO ${theme} — started while the tab was away`
      await push(silent, `gate-run-2-${theme}`, { kind: 'prose', text: mark })
      await push(silent, `gate-run-2-${theme}`, { kind: 'prose', text: `${mark} (second entry)` })
      await page.waitForTimeout(400)

      await page.goto(`${BASE}/${'#/tickets/TKT-142/session'}`, { waitUntil: 'networkidle' })
      await page.waitForTimeout(1500)
      const shown = await texts()
      const carries = shown.some((t) => (t ?? '').includes(mark))
      const stale = shown.some((t) => (t ?? '').includes('One more, after coming back.'))
      ok(`a run that started while the tab was away is the one on screen (${theme})`,
        carries && !stale,
        `${shown.length} entries · new-run=${carries} · previous-run-still-drawn=${stale}`)
      await shot(page, 'session-second-run', theme)
    }
    await page.evaluate((t) => localStorage.setItem('factory.theme', t), SCOPE.themes[0])
    await page.reload({ waitUntil: 'networkidle' })
  } else {
    skipped('the transcript a snapshot draws (8i)',
      'no lane was claimed for TKT-142, so there was no transcript to lose')
  }

  // ── 8j · an empty Send into a lane with no live session (WEB-FIX-061 · §4.7, E18) ─────────
  //
  // §1's "the real thing", which round 1 never wrote: *"Drive a lane to a terminal state, press
  // Send with an empty composer, and assert a run starts — with a screenshot in both themes. A
  // vitest that mounts the composer alone cannot see whether the person could FIND the control."*
  // QA failed the criterion for its absence, and the two regressions it also found are exactly
  // the kind a mounted-in-isolation test cannot see: what the button SAYS about a queued lane,
  // and what a second press does.
  {
    const dead = await claim('TKT-155', 'core')
    if (dead) {
      // a terminal state, reached the way a real one is: a process that starts and exits
      await api('POST', '/api/_test/lane-process', { lane: dead, command: 'true' })
      await page.waitForTimeout(1200)
      // E7, `GET /api/tickets/{id}/runs` — the run HISTORY. `/api/lanes/{lane}/runs` is E18 and
      // is POST-only, so reading it here returned nothing both times and the check measured
      // nothing at all while looking like it had.
      const runsOf = async () => ((await api('GET', '/api/tickets/TKT-155/runs')).body?.runs ?? [])
      const before = (await runsOf()).length

      // A RELOAD, not a hash change. Everything before this section leaves the browser on another
      // ticket's SESSION tab, and `goto` to a different fragment of the same document is a
      // same-document navigation — `networkidle` resolves before the app has fetched anything for
      // the new ticket. Reloading makes the first paint of THIS ticket the thing being judged.
      await page.goto(`${BASE}/#/tickets/TKT-155/session`, { waitUntil: 'networkidle' })
      await page.reload({ waitUntil: 'networkidle' })
      const composerArrived = await page.waitForSelector('[data-testid="composer-button"]',
        { timeout: 8000 }).then(() => true).catch(() => false)
      ok('the SESSION tab of a lane with no live session still has a composer in it',
        composerArrived,
        composerArrived ? 'it is there' : `what is on the screen: ${
          (await page.$$eval('[data-testid]', (els) => [...new Set(els.map(
            (e) => e.getAttribute('data-testid')))].join(' · '))).slice(0, 300)}`)

      const hint = (await page.$eval('[data-testid="composer-hint"]',
        (e) => e.textContent.trim()).catch(() => '')).toLowerCase()
      ok('the composer TELLS a person that Send will start a run when nothing is running',
        hint.includes('start'), `the composer says "${hint}"`)

      const box = await page.$('[data-testid="composer-draft"]')
      const typed = box ? await page.$eval('[data-testid="composer-draft"]', (e) => e.value) : ''
      ok('and the box it would start from is empty', box !== null && typed === '',
        box === null ? 'there is no box on the screen at all' : `the draft is "${typed}"`)

      if (composerArrived) await page.click('[data-testid="composer-button"]')
      await page.waitForTimeout(1500)
      const after = (await runsOf()).length
      ok('an empty Send into a lane with no live session STARTS A RUN',
        after > before, `${before} run(s) before the click, ${after} after`)

      for (const theme of themesFor('empty-send-starts-a-run',
        'the way back is legible in each theme')) {
        await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
        await page.reload({ waitUntil: 'networkidle' })
        await page.waitForTimeout(500)
        const applied = await page.getAttribute('html', 'data-theme')
        const said = (await page.$eval('[data-testid="composer-hint"]',
          (e) => e.textContent.trim()).catch(() => '')).toLowerCase()
        ok(`the way back is legible in the ${theme} theme`,
          applied === theme && said.length > 0,
          `theme=${applied} · the composer says "${said}"`)
        await shot(page, 'empty-send-starts-a-run', theme)
      }
      await page.evaluate((t) => localStorage.setItem('factory.theme', t), SCOPE.themes[0])
    } else {
      skipped('an empty Send into a lane with no live session (8j)',
        'no lane could be claimed for TKT-155')
    }
  }

  // ── 8k · a role lane is drawn UNDER the lane it belongs to (WEB-FEAT-079 · §4.4, §4.14) ───
  //
  // It runs BEFORE 8z, which deletes every worktree under the lane root to prove a leftover
  // blocks nothing: after that section there is no lane on this repository to nest anything under.
  //
  // The lane it nests under is `silent`, claimed at the top of section 8 on TKT-142 in `core`.
  // The second one is claimed over E12 with `role: "qa"` — a free string there — because that is
  // what makes this section runnable TODAY: SRV-FEAT-065 is what will make one appear unprompted,
  // and a drawing that waited for it would be a drawing nobody could gate.
  //
  // Measured, not matched. Containment says the tree is the tree and an x-offset says a person can
  // SEE it: a rule that asserted a class name would stay green through a stylesheet that stopped
  // indenting anything, which is the shape of gate this repository's escape ledger is made of.
  const runLane = ((await api('GET', '/api/lanes')).body?.lanes ?? [])
    .find((l) => l.ticket_id === 'TKT-142' && l.role === 'run') ?? null
  // THE SURFACE IS OPENED FIRST, and the order is the product's rather than a convenience: `M17`
  // is delivered to the scopes subscribed AT THE MOMENT IT IS SENT. A page that navigates here
  // afterwards has missed it, and a hash change re-reads nothing — which is the client's
  // no-polling rule doing exactly what it says. This is also F31's own order: the person is
  // already looking at the ticket when the tool claims a lane on it.
  if (runLane) {
    await page.goto(`${BASE}/#/tickets/TKT-142/session`, { waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="lane-rail"]', { timeout: 10000 }).catch(() => {})
  }
  const role = runLane
    ? await api('POST', '/api/lanes', { ticket_id: 'TKT-142', workspace: 'core', role: 'qa' })
    : null
  if (!runLane || role?.status !== 201) {
    skipped('a role lane is drawn under the lane it belongs to (8k)',
      runLane ? `E12 refused the qa lane: ${role?.status} ${JSON.stringify(role?.body)}`
        : 'TKT-142 has no run lane left to nest under')
  } else {
    const qaLane = role.body.lane
    ok('E12 claims a second lane on one ticket in a role of its own, on its own branch',
      role.body.role === 'qa' && role.body.branch === 'ticket/TKT-142-qa'
        && role.body.lane !== runLane.lane,
      `${qaLane} ${role.body.role} on ${role.body.branch}`)

    // the shape of a nested card, measured on a real page — used for both surfaces below
    const nesting = async (name) => page.evaluate((lane) => {
      const card = document.querySelector(`[data-testid="lane-card"][data-lane="${lane}"]`)
      if (!card) return null
      const box = card.getBoundingClientRect()
      const holder = card.closest('[data-testid="lane-children"]')
      const group = card.closest('[data-testid="lane-group"]')
      return {
        parent: holder ? holder.getAttribute('data-parent') : null,
        x: box.x,
        width: box.width,
        role: card.getAttribute('data-role'),
        tag: card.querySelector('[data-testid="lane-role"]')?.textContent?.trim() ?? '',
        ticket: card.getAttribute('data-ticket'),
        group: group ? group.querySelector('[data-testid="group-name"]')?.textContent?.trim() : null,
      }
    }, name)

    // ── §4.4, the rail, WITH NO RELOAD ──────────────────────────────────────────────────────
    //
    // The lane was claimed out of band, so nothing about it has reached this browser: E12's `201`
    // is a row for whoever POSTed it, and this page did not. What reaches it is the run — the
    // lane starts a process, `M2` names a lane the set does not hold, and `services/lanes.tsx`
    // re-reads E9 ON THAT EVENT. That is F31's own journey: a role lane exists in order to run
    // something, and the frame that run produces is what puts it on the screen.
    const drawn = await page.waitForSelector(
      `[data-testid="lane-card"][data-lane="${qaLane}"]`, { timeout: 10000 })
      .then(() => true).catch(() => false)
    // A process in it, so the card the screenshots below show is a lane doing something rather
    // than an empty one. AFTER the assertion, so nothing about it can be what put it on screen.
    const spawned = await api('POST', '/api/_test/lane-process',
      { lane: qaLane, command: 'sleep 600' })
    // What the SERVER says about the lane the screen has or has not drawn — so a failure here
    // says which half is wrong instead of leaving both suspects on the table.
    const onWire = ((await api('GET', '/api/lanes')).body?.lanes ?? [])
      .find((l) => l.lane === qaLane) ?? null
    const named = received.filter((f) => f.includes(qaLane))
    const wireSays = `E12 ${role.status} · process ${spawned.status} · E9 says `
      + `${onWire ? `${onWire.state.process}/${onWire.state.motion?.value ?? '—'}` : 'no such lane'}`
      + ` · ${named.length} frame(s) named it: `
      + `${named.map((f) => JSON.parse(f).m).join(',') || 'none'}`
    // UN-SKIPPED BY SRV-FIX-080, and the reload beneath it deleted with it. `WEB-FEAT-079` wrote
    // this check against a defect it could not fix from the client: the lane set was the one
    // thing about a lane the socket never announced, so a lane claimed out of band reached an
    // open page only on a reload. `M17 lane.membership` is what it now waits for.
    ok('a lane claimed while this page was open reaches it with no reload', drawn, wireSays)

    const child = await nesting(qaLane)
    const parent = await nesting(runLane.lane)
    ok('the rail draws the qa lane INSIDE the run lane\'s own container',
      child?.parent === runLane.lane && parent?.parent === null,
      child ? `${qaLane} under ${child.parent} · ${runLane.lane} under ${parent?.parent}`
        : `the qa card never appeared — ${wireSays}`)
    ok('and a person can see it: the child card is indented past its parent',
      child !== null && parent !== null && child.x > parent.x,
      child && parent ? `child x=${child.x.toFixed(1)} vs parent x=${parent.x.toFixed(1)}`
        : 'not measured')
    ok('the card says which role it is, and keeps the ticket it belongs to',
      child?.role === 'qa' && child?.tag === 'qa' && child?.ticket === 'TKT-142'
        && parent?.role === 'run' && parent?.tag === '',
      `child ${child?.role}/"${child?.tag}"/${child?.ticket} · parent ${parent?.role}/"${parent?.tag}"`)
    const railCount = (await page.textContent('[data-testid="lane-count"]').catch(() => '')).trim()
    ok('nesting is presentation: the rail still counts both lanes of the ticket',
      railCount === '2', `the count reads "${railCount}"`)

    // ── §4.14, the same nesting inside the group both lanes belong to ────────────────────────
    await page.goto(`${BASE}/#/lanes`, { waitUntil: 'networkidle' })
    await page.waitForSelector('[data-testid="lane-card"]', { timeout: 10000 }).catch(() => {})
    const onSurface = await nesting(qaLane)
    const parentOnSurface = await nesting(runLane.lane)
    ok('§4.14 nests it under the same lane, inside the group that counts it',
      onSurface?.parent === runLane.lane && onSurface?.group === 'core'
        && parentOnSurface?.group === 'core',
      `${onSurface?.group}: ${qaLane} under ${onSurface?.parent}`)
    // Counted off E9 rather than written down here: how many lanes `core` holds depends on which
    // sections above claimed one, and a hard-coded number would go red for somebody else's reason.
    const inCore = ((await api('GET', '/api/lanes')).body?.lanes ?? [])
      .filter((l) => l.workspace === 'core').length
    const coreGroup = await page.evaluate(() => {
      const group = [...document.querySelectorAll('[data-testid="lane-group"]')].find(
        (el) => el.querySelector('[data-testid="group-name"]')?.textContent?.trim() === 'core')
      return group === undefined ? null : {
        count: group.querySelector('[data-testid="group-count"]')?.textContent?.trim() ?? '',
        lanes: [...group.querySelectorAll('[data-testid="lane-card"]')]
          .map((c) => c.getAttribute('data-lane')),
      }
    })
    ok('and the group counts it — a nested lane is not a hidden one',
      coreGroup?.count === `${inCore} lanes` && coreGroup.lanes.includes(qaLane),
      `core reads "${coreGroup?.count}" over ${inCore} lanes: ${coreGroup?.lanes.join(' ')}`)

    // ── the screenshot the user asked for, both themes, both surfaces ────────────────────────
    for (const theme of themesFor(['role-lane-rail', 'role-lane-all-lanes'],
      'the role lane is photographed on both surfaces, in each theme')) {
      for (const [name, route] of [['role-lane-rail', '#/tickets/TKT-142/session'],
                                   ['role-lane-all-lanes', '#/lanes']]
        .filter(([n]) => SCOPE.inScope(n))) {
        await page.goto(`${BASE}/${route}`, { waitUntil: 'networkidle' })
        await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
        await page.reload({ waitUntil: 'networkidle' })
        await page.waitForSelector(`[data-testid="lane-card"][data-lane="${qaLane}"]`,
          { timeout: 10000 }).catch(() => {})
        await page.mouse.move(0, 0)
        await shot(page, name, theme)
      }
    }
    const shotAfter = await nesting(qaLane)
    // WEB-FEAT-379 — the reload and the theme change this asserts survive are the loop above's, so
    // when that loop did not run there is nothing here to have survived anything. Skipped rather
    // than passed: the data would satisfy it either way, which is the whole shape of a check that
    // has not run.
    okScoped('role-lane-rail',
      'the nesting survives a reload and a theme change — it is the data, not a render state',
      shotAfter?.parent === runLane.lane,
      `after the sweep: ${qaLane} under ${shotAfter?.parent}`)

    // ── THE ORPHAN, on the real journey: E13 takes the run lane and the check keeps working ──
    //
    // §1's mandatory negative, driven against the server rather than a fixture. A nesting that
    // hid this card would hide a running check at the one moment it is all that is left.
    EXPECTED_404.push(`/api/lanes/${runLane.lane}`)
    const released = await api('DELETE', `/api/lanes/${runLane.lane}?force=true`)
    await page.goto(`${BASE}/#/tickets/TKT-142/session`, { waitUntil: 'networkidle' })
    await page.reload({ waitUntil: 'networkidle' })
    const still = await page.waitForSelector(
      `[data-testid="lane-card"][data-lane="${qaLane}"]`, { timeout: 10000 })
      .then(() => true).catch(() => false)
    const orphan = still ? await nesting(qaLane) : null
    ok('E13 releases the run lane and the qa lane is STILL on the screen, at top level',
      released.status < 400 && still && orphan?.parent === null,
      `release ${released.status} · ${still ? `under ${orphan?.parent}` : 'the card vanished'}`)
    ok('and it still says whose ticket it is, with nothing above it left to say so',
      orphan?.ticket === 'TKT-142' && orphan?.role === 'qa',
      `${orphan?.ticket} · ${orphan?.role}`)
    const afterCount = (await page.textContent('[data-testid="lane-count"]').catch(() => '')).trim()
    ok('the rail counts what is left, and it is not zero and not two', afterCount === '1',
      `the count reads "${afterCount}"`)
  }

  // ══ 8z · SRV-FIX-059 round 7 — a leftover blocks nothing ═════════════════════════════════
  //
  // Rounds 1-6 built a refusal, a consent flag, a traversal guard and a way-out control so that a
  // person could get out of a lane blocked by its own leftovers. Round 7 asked why the collision
  // happened at all: `nextNumber_` restarted at 1 in every process and never asked the disk, so
  // the tool walked into directories the previous process had left. The counter steps past them
  // now, and the whole trap is gone — so this section asserts the ABSENCE of the journey the
  // previous version of it drove.
  //
  // Kept as a gate check rather than deleted, because "nothing blocks" is exactly the claim a
  // browser is needed to believe: the unit suite can assert a `201`, only this can assert that a
  // person pressed the button and got a lane.
  {
    const laneRoot = join(FIXTURE, '.factory', 'worktrees', 'lane')
    for (const entry of (existsSync(laneRoot) ? readdirSync(laneRoot) : [])) {
      if (!statSync(join(laneRoot, entry)).isDirectory()) continue
      await api('DELETE', `/api/lanes/lane/${entry}?force=true&discard_ignored=true`)
    }
    const seed = await api('POST', '/api/lanes', { ticket_id: 'TKT-160', workspace: 'core' })
    if (seed.status !== 201 || !seed.body?.worktree) {
      skipped('SRV-FIX-059 · a leftover blocks nothing', `could not seed (HTTP ${seed.status})`)
    } else {
      // a lane that ran its build once, left behind by a restart: the shape six rounds fought over
      mkdirSync(join(seed.body.worktree, 'evidence'), { recursive: true })
      writeFileSync(join(seed.body.worktree, 'evidence', 'agent_app_1.log'),
                    'one board, one run, not reproducible\n')
      await api('POST', '/api/_test/forget-lane', { lane: seed.body.lane })
      const leftover = seed.body.worktree

      await page.goto(`${BASE}/#/tickets/TKT-160/session`, { waitUntil: 'networkidle' })
      await page.reload({ waitUntil: 'networkidle' })
      await tryClick(page, '[data-testid="composer"] [data-testid="claim-lane"]')

      let got = null
      for (let i = 0; i < 100 && !got; i += 1) {
        await page.waitForTimeout(100)
        got = ((await api('GET', '/api/lanes')).body?.lanes ?? [])
          .find((l) => l.ticket_id === 'TKT-160') ?? null
      }
      ok('a leftover on disk blocks no claim — the button just works',
        got !== null, got ? got.lane : 'still blocked')

      // ...and the leftover is STILL THERE. Nothing was deleted to make room, which is the whole
      // reason nothing can be lost making room.
      ok('and the leftover was stepped around, not deleted',
        existsSync(join(leftover, 'evidence', 'agent_app_1.log')), leftover)

      // no refusal reached the screen at all — the control that used to be needed is absent
      const control = await page.$$('[data-testid="claim-clear-leftover"], '
                                  + '[data-testid="claim-discard-ignored"]')
      ok('no way-out control is offered, because there is nothing to get out of',
        control.length === 0, `${control.length} present`)

      if (got) await api('DELETE', `/api/lanes/${got.lane}?force=true&discard_ignored=true`)
    }
  }

  }

  // ── 9 · every surface, both themes: screenshots and the contrast floor ────────────────────
  //
  // WEB-FEAT-379 — the rows this run's scope named, which with nothing named is every row. A scope
  // naming no static surface leaves this section nothing to measure, and a contrast floor asserted
  // over nothing is a check that has not run (`_TEMPLATE.md` §2), so it is reported out of scope
  // instead of green.
  const sweep = SURFACES.filter(([name]) => SCOPE.inScope(name))
  let worst = { ratio: Infinity }
  let tightest = { margin: Infinity }
  const tight = Object.fromEntries(SCOPE.themes.map((t) => [t, { margin: Infinity }]))
  const below = []
  const measured = new Map()
  for (const theme of sweep.length === 0 ? [] : SCOPE.themes) {
    await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
    await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
    // A goto that changes only the hash is a SAME-DOCUMENT navigation: the app keeps its React
    // state and writes its own theme straight back. The reload is what makes the stored value win.
    await page.reload({ waitUntil: 'networkidle' })
    for (const [name, route, prepare] of sweep) {
      await page.goto(`${BASE}/${route}`, { waitUntil: 'networkidle' })
      // A surface whose state has to be put there says so in its own row (WEB-FIX-337): the
      // tickets list is only itself with a query in the search box.
      if (prepare) await prepare(page)
      // and off the last thing that was clicked: a hovered row is not the resting screen, and
      // measuring one as if it were reports a colour the user never sees at rest
      await page.mouse.move(0, 0)
      await page.waitForTimeout(250)
      const applied = await page.getAttribute('html', 'data-theme')
      if (applied !== theme) {
        failures += 1
        console.log(`  FAIL  theme ${theme} did not apply on ${route} (was ${applied})`)
      }
      await shot(page, name, theme)
      for (const m of await measureContrast(page)) {
        const ratio = contrast(m.fg, m.bg)
        const floor = floorFor(m.size)
        const row = { ...m, theme, route, ratio, floor }
        if (ratio < worst.ratio) worst = row
        if (ratio - floor < tightest.margin) tightest = { ...row, margin: ratio - floor }
        if (ratio - floor < tight[theme].margin) tight[theme] = { ...row, margin: ratio - floor }
        if (ratio < floor) {
          // one line per distinct offender, not one per surface it appears on
          const key = `${theme} ${m.el} ${m.fg} ${m.bg}`
          if (!measured.has(key)) { measured.set(key, row); below.push(row) }
        }
      }
    }
  }
  // WEB-FEAT-379 — a contrast floor asserted over nothing is a check that has not run
  // (`_TEMPLATE.md` §2), so a scope naming no static surface reports it out of scope rather
  // than green over an empty measurement.
  if (sweep.length === 0) {
    outOfScope('every text node clears WCAG: 4.5:1 at 14px and up, 3.0:1 below — no exemptions',
      'the static sweep')
  } else {
    console.log(`\n  contrast · worst measured ${worst.ratio.toFixed(2)}:1 — ${worst.el} `
      + `"${worst.text}" ${worst.fg} on ${worst.bg} (${worst.theme}, ${worst.route})`)
    console.log(`  contrast · tightest against its own floor ${tightest.ratio.toFixed(2)}:1 vs `
      + `${tightest.floor.toFixed(1)}:1 at ${tightest.size}px — ${tightest.el} "${tightest.text}" `
      + `(${tightest.theme})`)
    // both themes, always — a single worst figure hides whichever theme happens to be second, and
    // the one this gate was built to catch was second in the dark
    for (const theme of SCOPE.themes) {
      const t = tight[theme]
      console.log(`  contrast · quietest ${theme} ${t.ratio.toFixed(2)}:1 vs ${t.floor.toFixed(1)}:1 `
        + `at ${t.size}px — ${t.el} "${t.text}" ${t.fg} on ${t.bg}`)
    }
    for (const b of below) {
      console.log(`  BELOW FLOOR  ${b.ratio.toFixed(2)}:1 < ${b.floor.toFixed(1)}:1  ${b.size}px  `
        + `${b.theme}  ${b.el}  "${b.text}"  ${b.fg} on ${b.bg}  (${b.route})`)
    }
    ok('every text node clears WCAG: 4.5:1 at 14px and up, 3.0:1 below — no exemptions',
      below.length === 0,
      below.length === 0
        ? `worst ${worst.ratio.toFixed(2)}:1, tightest margin ${tightest.margin.toFixed(2)}`
        : `${below.length} below floor, worst ${worst.ratio.toFixed(2)}:1 (${worst.el} "${worst.text}", ${worst.theme})`)
  }

  // ── 10 · the live sweep (WEB-FEAT-083): the process axis, photographed while it is true ──
  //
  // Every shot above is of the static fixture, and SRV-FIX-081's class of defect — three lanes
  // running while the board said nothing had started — is only VISIBLE in a frame taken while a
  // lane is running. One lane is claimed over E12 and reused for all four surfaces (§1's
  // pre-decision: four claims would race the fixture's single resource declaration for nothing),
  // and E9 is queried in the same block as every screenshot so the index row carries the state
  // the tool reported at that moment, not the state the setup hoped for. Judgment of the pixels
  // is SRV-FEAT-084's; what this section owes it is frames that were live when taken.
  {
    // WEB-FEAT-379 — nothing is claimed and no run is started when the scope named no live
    // surface: this whole section exists to photograph them.
    const liveSweep = LIVE_SURFACES.filter(([name]) => SCOPE.inScope(name))
    const live = liveSweep.length === 0 ? null : await claim('TKT-142', 'core')
    if (liveSweep.length === 0) {
      outOfScope('the live sweep (10)', 'every live surface')
    } else if (!live) {
      skipped('the live sweep (10)', 'no lane could be claimed for TKT-142')
    } else {
      // WEB-FIX-091 QA F1 — a REAL run (E18), not a bare process attached to the lane.
      //
      // This section is the only place the product is photographed while something is happening,
      // and until now what was happening was `/api/_test/lane-process`: a process the lane holds
      // that belongs to no run. Every surface here therefore showed a lane the tool itself called
      // `running` above a run history that was empty — and it was empty truthfully, which is why
      // no assertion could tell the fixture's shortcut from the defect the ticket was raised for.
      //
      // The stand-in agent holds while this file exists (see run-gate.sh), so the run stays in
      // flight for the whole sweep and every frame below is of a lane running a RUN.
      const hold = process.env.FACTORY_GATE_AGENT_HOLD
      if (hold) writeFileSync(hold, '')
      const startedLive = await api('POST', `/api/lanes/${encodeURIComponent(live)}/runs`,
        { prompt: 'hold the lane open for the live sweep' })
      const startProblems = validate(SCHEMA.responses.E18, startedLive.body, 'E18', [])
      judged.add('E18')
      ok('the live sweep runs a real run, not a process standing in for one',
        startedLive.status === SCHEMA.responses.E18.status && startProblems.length === 0,
        startProblems.slice(0, 3).join(' · ')
          || `E18 ${startedLive.status} ${JSON.stringify(startedLive.body ?? {}).slice(0, 160)}`)
      // …and it EXECUTED. Until SRV-FIX-137 every agent this gate ever spawned died at exec
      // (`bwrap: execvp …/claude: No such file or directory`, because the agent's directory was
      // bound before the sandbox's own `--tmpfs /tmp` covered it) and was recorded as a crashed
      // run. Nothing looked at a transcript, so a gate that never once ran an agent read green.
      const liveRunId = startedLive.body?.run_id
      let standinSaid = 'no run id'
      let stillRunning = false
      if (liveRunId) {
        await page.waitForTimeout(1500)
        const entries = (await api('GET', `/api/runs/${encodeURIComponent(liveRunId)}/transcript`))
          .body?.entries ?? []
        standinSaid = JSON.stringify(entries).slice(0, 300)
        const mine = ((await api('GET', '/api/tickets/TKT-142/runs')).body?.runs ?? [])
          .find((r) => r.run_id === liveRunId)
        stillRunning = mine?.outcome === 'running'
      }
      // Both halves, because either alone is vacuous: an empty transcript satisfies "no exec
      // error" whether the agent ran or never started, and a run still in flight a second and a
      // half after `exec` is the only positive evidence a gate can hold that a process exists.
      ok('the agent the tool spawned actually ran — still in flight, and no exec failure',
        stillRunning && !/execvp|bwrap:|No such file or directory/.test(standinSaid),
        `${stillRunning ? 'running' : 'not running'} · transcript ${standinSaid}`)
      const observed = []
      let runningAsserted = false
      for (const theme of SCOPE.themes) {
        await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
        await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
        await page.reload({ waitUntil: 'networkidle' })
        for (const [name, route] of liveSweep) {
          await page.goto(`${BASE}/${route}`, { waitUntil: 'networkidle' })
          await page.mouse.move(0, 0)
          await page.waitForTimeout(250)
          const row = ((await api('GET', '/api/lanes')).body?.lanes ?? [])
            .find((l) => l.lane === live)
          const laneState = row?.state?.process ?? ''
          observed.push(laneState)
          await shot(page, name, theme, 'live', laneState)

          // WEB-FIX-373 — `running` IS asserted, and here is the only place it can be. It is the
          // one delivery state that is a fact about the process table rather than about git
          // (`probe.cpp`: a lane the tool started whose agent is alive), so the static sweep
          // cannot reach it and section 7's derivation will never produce it. This lane holds
          // TKT-142 and this route is the requirement TKT-142 sits under, so the wire and the
          // screen are asked together: a regression that stops `running` ever being drawn is red
          // here rather than nowhere.
          if (name === 'requirement-detail-live' && theme === SCOPE.themes[0]) {
            runningAsserted = true
            const served = ((await api('GET', '/api/requirements/REQ-014')).body?.groups ?? [])
              .flatMap((g) => g.tickets ?? []).find((t) => t.id === 'TKT-142')?.delivery?.state
            const painted = await page.$$eval('[data-testid="delivery-badge"]',
              (els) => els.map((e) => e.getAttribute('data-state')))
            ok('`running` is drawn on the lane the tool started — the state no static frame reaches',
              served === 'running' && painted.includes('running'),
              `E4 says TKT-142 is ${served ?? 'absent'} · badges ${painted.join(' · ') || 'none'}`)
          }

          // WEB-FIX-091 round 2, from QA's F1 — asserted on THE surface §1 photographed, not on a
          // neighbour of it. This is the frame the whole ticket is named after: a lane the tool
          // itself reports as `running`, and the two numbers above the transcript claiming
          // nothing has ever run. `0 runs` over a running lane is the sentence, and it is false
          // whichever half is wrong.
          if (name === 'ticket-live' && theme === SCOPE.themes[0]) {
            const liveTab = (await page.textContent('[data-tab="runs"]').catch(() => '')).trim()
            const liveId = (await page.textContent('[data-testid="ticket-metrics"]')
              .catch(() => '')).trim()
            const wire = ((await api('GET', '/api/tickets/TKT-142/runs')).body?.runs ?? [])
            ok('the ticket the tool says is RUNNING does not report zero runs',
              laneState === 'running' && !liveId.startsWith('0 run') && liveTab !== 'RUNS · 0',
              `lane ${laneState} · ${liveTab} · ${liveId} · E7 rows ${wire.length}`)
          }
        }
      }
      // The count is DERIVED from the list and the themes, not written as a literal: `8` was
      // 4 surfaces × 2 themes, and adding a fifth surface turned an assertion about the lane into
      // an assertion about how many surfaces somebody had listed the last time it was edited.
      const wantFrames = liveSweep.length * SCOPE.themes.length
      ok('the lane held a live state for the whole sweep — E9 said running or queued at every frame',
        observed.length === wantFrames
          && observed.every((s) => s === 'running' || s === 'queued'),
        `${observed.length}/${wantFrames} · ${observed.join(' · ') || 'nothing observed'}`)
      // A check the scope left out is printed and counted, never silently passed — the ledger is
      // the whole of what makes a way to run less than everything safe (WEB-FEAT-379).
      if (!runningAsserted) {
        outOfScope('`running` is drawn on the lane the tool started (10)',
                   'requirement-detail-live')
      }
      if (hold) rmSync(hold, { force: true })
      await api('DELETE', `/api/lanes/${encodeURIComponent(live)}?force=true`)
    }
  }

  ok('the page threw nothing', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | '))
  ok('the page logged no error of its own', consoleErrors.length === 0,
    consoleErrors.slice(0, 3).join(' | '))
  ok('every request the client made was answered, bar the report it expects to be absent',
    badResponses.length === 0, badResponses.slice(0, 5).join(' | '))

  // ── 11 · shots: the evidence is asserted, not hoped (WEB-FEAT-083) ────────────────────────
  //
  // Until this section the screenshots were side artifacts: a `page.screenshot` that failed
  // after its `await` was a warning nobody reads, and a run could end 166/166 green with a shot
  // missing — the TEST-stage capture of WEB-FEAT-083 did exactly that. Every surface `shot()`
  // drove must now have a photograph on disk, from THIS run (the directory outlives runs, and a
  // stale PNG of the right name satisfied the existence check during that same capture), bigger
  // than zero bytes, with an index row; and a `-live` row whose `lane_state` is not a live one
  // is counted as missing, because a "live" frame of an idle repository is the static sweep
  // wearing the wrong name. The index is written before the verdict so a red run still leaves
  // its manifest behind for whoever comes to look.
  {
    const holes = []
    for (const d of driven) {
      const row = shotIndex.find((r) => r.surface === d.surface && r.theme === d.theme)
      const file = join(SHOTS, `${d.surface}-${d.theme}.png`)
      if (!row) holes.push({ ...d, why: 'the write never happened' })
      else if (!existsSync(file)) holes.push({ ...d, why: 'the index names a file that is not on disk' })
      else if (statSync(file).size === 0) holes.push({ ...d, why: 'a zero-byte file' })
      else if (statSync(file).mtimeMs < GATE_START) holes.push({ ...d, why: 'a stale file from an earlier run' })
      else if (d.state === 'live' && row.lane_state !== 'running' && row.lane_state !== 'queued') {
        holes.push({ ...d, why: `lane_state "${row.lane_state}" — the frame was not live` })
      }
      // WEB-FEAT-085 round 3 — a picture with no captured text is a row only its author can
      // check, which is the hole §1's rewritten criteria exist to close. Same treatment as a
      // missing PNG: it is a hole, not a warning.
      else if (!existsSync(join(SHOTS, `${d.surface}-${d.theme}.txt`))) {
        holes.push({ ...d, why: 'the surface was photographed and its text was not captured' })
      } else if (readFileSync(join(SHOTS, `${d.surface}-${d.theme}.txt`), 'utf8').trim() === '') {
        holes.push({ ...d, why: 'the captured text is empty — the surface rendered nothing to quote' })
      }
    }
    // WEB-FEAT-085 round 5 (QA round 4, finding 2) — §1 names this gate as its build target and
    // says it asserts "no orphan of either kind". Two of the three were here; this is the third.
    // Section 11 iterates `driven`, so a file on disk that no index row names was invisible to it,
    // and a stale .txt from a renamed surface is exactly what a future audit row would quote as
    // evidence while this gate stayed green over it.
    const named = new Set(shotIndex.flatMap((r) => [r.file, r.text]))
    const strays = readdirSync(SHOTS)
      .filter((f) => (f.endsWith('.png') || f.endsWith('.txt')) && !named.has(f))
    ok('no file in the shots directory is unnamed by the index',
      strays.length === 0,
      strays.length === 0 ? `${named.size} files, all named` : strays.slice(0, 5).join(' · '))

    writeFileSync(join(SHOTS, 'index.json'), `${JSON.stringify(shotIndex, null, 2)}\n`)
    const surfaces = new Set(driven.map((d) => d.surface)).size
    console.log(`\n  shots · surfaces ${surfaces} · files ${shotIndex.length} · missing ${holes.length}`)
    for (const h of holes) {
      console.log(`  MISSING SHOT  {surface: ${h.surface}, theme: ${h.theme}} — ${h.why}`)
    }
    ok('every surface the sweep drove has a fresh photograph of this run on disk',
      holes.length === 0,
      holes.length === 0 ? `${shotIndex.length} files, none missing`
        : holes.map((h) => `${h.surface}/${h.theme}`).join(' · '))
    const staticRows = shotIndex.filter((r) => r.state === 'static')
    const liveRows = shotIndex.filter((r) => r.state === 'live')
    // WEB-FEAT-379 — the floor is what the SCOPE asked for. `four` and `eight` were 21 × 2 and
    // 5 × 2 rounded down, which on a scoped run is a floor no run can clear; `Math.min` keeps an
    // unscoped run's numbers, and therefore its check names, exactly what they were.
    const wantSurfaces = Math.min(4, sweep.length)
    const wantLive = Math.min(8,
      LIVE_SURFACES.filter(([n]) => SCOPE.inScope(n)).length * SCOPE.themes.length)
    ok(`the index holds the static sweep — ${themeWords()}, at least ${wantSurfaces} surfaces`,
      new Set(staticRows.map((r) => r.theme)).size === SCOPE.themes.length
        && new Set(staticRows.map((r) => r.surface)).size >= wantSurfaces,
      `${new Set(staticRows.map((r) => r.surface)).size} surfaces × `
        + `${new Set(staticRows.map((r) => r.theme)).size} themes`)
    ok(`and at least ${wantLive === 8 ? 'eight' : wantLive} \`-live\` rows, each carrying the`
      + ' state E9 reported when it was taken',
      liveRows.length >= wantLive
        && liveRows.every((r) => r.lane_state === 'running' || r.lane_state === 'queued'),
      `${liveRows.length} live rows: `
        + `${[...new Set(liveRows.map((r) => r.lane_state))].join(' · ') || 'none'}`)
  }

  {
    const ownedCore = ((await api('GET', '/api/lanes')).body?.lanes ?? [])
      .filter((lane) => lane.workspace === 'core' && lane.role === 'run')
    let routingLane = ownedCore.find((lane) => lane.state.process !== 'running')
      ?? ownedCore[0]
    if (!routingLane) {
      const lane = await claim('TKT-160', 'core')
      routingLane = ((await api('GET', '/api/lanes')).body?.lanes ?? [])
        .find((row) => row.lane === lane)
    }
    if (routingLane?.state.process === 'running') {
      const killed = await api('POST', `/api/lanes/${routingLane.lane}/kill`, { grace_s: 1 })
      ok('routing coverage stops only its owned fixture process after the screenshots',
        killed.status === 200 || killed.status === 202,
        `${killed.status} ${JSON.stringify(killed.body)}`)
      const deadline = Date.now() + 15000
      do {
        await page.waitForTimeout(50)
        routingLane = ((await api('GET', '/api/lanes')).body?.lanes ?? [])
          .find((row) => row.lane === routingLane.lane)
      } while (routingLane?.state.process === 'running' && Date.now() < deadline)
    }
    if (!routingLane || routingLane.state.process === 'running') {
      skipped('E33 and E34 routing responses', 'no owned idle core lane after the screenshots')
    } else {
      const path = `/api/lanes/${routingLane.lane}/routing`
      const submitted = await api('POST', path, {
        request_id: 'browser-gate-routing',
        steps: [{ which: 'build', accepted_exit_codes: [0] }],
      })
      const submitProblems = validate(SCHEMA.responses.E33, submitted.body, 'E33', [])
      judged.add('E33')
      ok('E33 submits a declared command on a real claimed lane',
        submitted.status === SCHEMA.responses.E33.status && submitProblems.length === 0,
        submitProblems.slice(0, 3).join(' · ')
          || `${submitted.status} ${JSON.stringify(submitted.body).slice(0, 160)}`)
      if (submitted.status === SCHEMA.responses.E33.status) {
        let retained
        const deadline = Date.now() + 15000
        do {
          retained = await api('GET', `${path}/browser-gate-routing?include_output=true`)
          if (retained.status !== 200 || retained.body?.state !== 'running') break
          await page.waitForTimeout(50)
        } while (Date.now() < deadline)
        const stateProblems = validate(SCHEMA.responses.E34, retained.body, 'E34', [])
        judged.add('E34')
        ok('E34 serves the retained routing response from the real command',
          retained.status === SCHEMA.responses.E34.status && stateProblems.length === 0,
          stateProblems.slice(0, 3).join(' · ')
            || `${retained.status} ${JSON.stringify(retained.body).slice(0, 160)}`)
        const receipt = retained.body?.receipts?.[0]
        let capture = null
        let captureProblem = 'no complete receipt or fixture repository'
        if (FIXTURE && receipt?.complete && typeof receipt.capture_path === 'string') {
          const path = resolve(isAbsolute(receipt.capture_path)
            ? receipt.capture_path : join(FIXTURE, receipt.capture_path))
          const captures = resolve(FIXTURE, '.factory/runs/.routing') + sep
          if (path.startsWith(captures) && existsSync(path)) {
            capture = readFileSync(path)
            captureProblem = `${capture.length} bytes from ${receipt.capture_path}`
          } else {
            captureProblem = 'capture is missing or outside the routing runtime directory'
          }
        }
        ok('a real nonzero fixture build returns to main with its full capture',
          retained.body?.state === 'needs_main' && retained.body?.action === 'main'
            && retained.body?.reason === 'command_failed' && retained.body?.receipts?.length === 1
            && receipt?.which === 'build' && receipt?.command === 'make -C core'
            && receipt?.exit_code !== 0 && receipt?.accepted === false && receipt?.reused === false
            && capture !== null && capture.length > 0 && capture.length === receipt.bytes
            && createHash('sha256').update(capture).digest('hex') === receipt.capture_sha256
            && typeof receipt.output_base64 === 'string'
            && Buffer.from(receipt.output_base64, 'base64').equals(capture),
          `${retained.body?.state}/${retained.body?.reason} · exit ${receipt?.exit_code} · ${captureProblem}`)
      } else {
        skipped('E34 retained routing response', 'the real E33 submission did not create a request')
      }
    }
  }

  await browser.close()

  // ── the schema ledger · every node was asked for, by somebody (WEB-FIX-213 §3.4) ─────────
  //
  // The class this closes is *a contract file gains a member whose shape the sweep cannot ask
  // for*. `checked_by` lets a node say section 6b cannot reach it; without this check that
  // sentence is a comment, and the two nodes it exempts today could quietly become twelve.
  const { scopedOut, unjudged } = schemaCoverage(
    SCHEMA.responses, judged, deferredNodes, SCOPE, LIVE_SURFACES)
  for (const id of scopedOut) outOfScope(`${id} response in the live sweep (10)`, 'every live surface')
  ok('every node in http.schema.json was judged or explicitly skipped by scope',
    unjudged.length === 0,
    unjudged.length === 0
      ? `${judged.size} judged, ${scopedOut.size} scoped out, ${deferredNodes.size} deferred: `
        + [...deferredNodes.keys()].join(' · ')
      : `never judged: ${unjudged.join(' · ')}`)
  for (const [id, why] of deferredNodes) {
    if (scopedOut.has(id)) continue
    ok(`${id} says which section reaches it instead of section 6b`,
      typeof why === 'string' && why.length > 0 && judged.has(id), why || 'nothing')
  }

  console.log(`\n=== ${checks - failures}/${checks} checks passed, ${failures} failed`
    + `${skips ? `, ${skips} NOT RUN` : ''}, ${SCOPE.skipLine()}\n`)
  process.exit(failures === 0 ? 0 : 1)
}

main().catch((e) => { console.error('\nGATE CRASHED:', e); process.exit(2) })

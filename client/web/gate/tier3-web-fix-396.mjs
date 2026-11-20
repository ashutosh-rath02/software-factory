/**
 * WEB-FIX-396 · TIER 3 — drive §4.13's mandatory negative in the real client, against the real
 * server, on a REAL busy run, as a person.
 *
 * The case this ticket makes load-independent is §4.13's negative: a run producing hundreds of
 * transcript entries raises ZERO notifications while the tab is in the background. Nothing about
 * that claim moved, so tier 3's job is to show the claim is still true of the PRODUCT rather than
 * of jsdom — the built bundle, the C++ server on this repository, the live socket, the frames this
 * machine's own lanes are producing while the script watches.
 *
 * ONE THING HERE IS NOT REAL, and it is named rather than hidden: `visibilityState`. An automated
 * Chrome reports every tab visible — headless and headful, a second `newPage`, a `window.open`
 * tab, and with `--disable-backgrounding-occluded-windows` dropped, all measured, all `visible`.
 * So the signal the component listens to is synthesised the way the browser delivers it (the
 * getter, then a real `visibilitychange` on the real document) and everything downstream of it is
 * the product's.
 *
 * AND THE WATCH COUNTS WHAT ARRIVED. "No notification was raised" is satisfied by nothing arriving,
 * so the page's own WebSocket is instrumented — observed, not replaced — and a window in which no
 * frame reached the client is reported as a check that did not run.
 *
 *   node gate/tier3-web-fix-396.mjs http://localhost:7791 gate/shots-tier3
 */
import { chromium } from 'playwright-core'

const BASE = process.argv[2]
const SHOTS = process.argv[3]
const CHROME = process.env.CHROME_PATH || '/usr/bin/google-chrome'
const WATCH_MS = Number(process.env.TIER3_WATCH_MS || 60000)
// A ticket whose lane is RUNNING on this machine. The session surface subscribes to that lane's
// transcript, which is where the hundreds of entries §4.13's negative is about actually flow —
// `#/lanes` subscribes to the lane list and would watch a quiet socket and call it a busy run.
const TICKET = process.env.TIER3_TICKET || ''

const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })

const fails = []
const ok = (label, cond, detail = '') => {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${label}${detail ? ' · ' + detail : ''}`)
  if (!cond) fails.push(label)
}

// Observe the page's own socket and its own Notification constructor. Neither is replaced: the
// real WebSocket still connects to the real server, and the real Notification still runs.
await ctx.addInitScript(() => {
  window.__seen = { frames: 0, kinds: {}, notifications: [] }
  const Native = window.WebSocket
  window.WebSocket = function (...args) {
    const ws = new Native(...args)
    ws.addEventListener('message', (e) => {
      window.__seen.frames += 1
      try {
        const m = JSON.parse(String(e.data)).m
        window.__seen.kinds[m] = (window.__seen.kinds[m] || 0) + 1
      } catch { /* a frame the client cannot read is still a frame */ }
    })
    return ws
  }
  window.WebSocket.prototype = Native.prototype
  Object.assign(window.WebSocket, Native)
  if (window.Notification) {
    const RealNotification = window.Notification
    window.Notification = function (title, options) {
      window.__seen.notifications.push({ title, body: options && options.body })
      return new RealNotification(title, options)
    }
    Object.defineProperty(window.Notification, 'permission',
      { get: () => RealNotification.permission })
    window.Notification.requestPermission = (...a) => RealNotification.requestPermission(...a)
  }
})

if (!TICKET) {
  console.error('TIER3_TICKET is not set, and this script will not guess whose run it is watching.')
  process.exit(2)
}

const page = await ctx.newPage()
await page.goto(`${BASE}/#/tickets/${TICKET}/session`, { waitUntil: 'networkidle' })
await page.waitForTimeout(4000)

const surface = await page.evaluate(() => document.body.innerText)
ok(`the session surface is drawn by the real server, for ${TICKET}`, surface.includes(TICKET),
   surface.split('\n').filter(Boolean).slice(0, 3).join(' | '))
ok('with the tab in front, the title carries no count', !/^\(\d+\)/.test(await page.title()),
   await page.title())
await page.screenshot({ path: `${SHOTS}/tier3-396-rail-in-front.png` })

// The person switches away. The component's own signal, delivered on the real document.
await page.evaluate(() => {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
  document.dispatchEvent(new Event('visibilitychange'))
})
ok('the page now reads as a background tab', await page.evaluate(() => document.visibilityState) === 'hidden')

const before = await page.evaluate(() => ({ ...window.__seen, kinds: { ...window.__seen.kinds } }))
const titles = new Set()
const started = Date.now()
while (Date.now() - started < WATCH_MS) {
  titles.add(await page.title())
  await page.waitForTimeout(1500)
}
const after = await page.evaluate(() => ({ ...window.__seen, kinds: { ...window.__seen.kinds } }))

const arrived = after.frames - before.frames
const kinds = Object.entries(after.kinds)
  .map(([m, n]) => `${m}:${n - (before.kinds[m] || 0)}`)
  .filter((s) => !s.endsWith(':0'))
console.log(`  frames that reached the client while it was backgrounded: ${arrived} — ${kinds.join(' ')}`)
console.log(`  titles seen: ${[...titles].map((t) => JSON.stringify(t)).join(', ')}`)

ok('THE WATCH SWEPT SOMETHING — a busy run really was busy', arrived > 0,
   `${arrived} frames in ${WATCH_MS / 1000}s`)
// §4.13 reaches out for exactly three frames. If one of them arrives during the watch the product
// SHOULD interrupt, and a check that demanded silence would be red for the product working — so
// the window is classified rather than assumed quiet, and the assertion follows what arrived.
const reachOuts = (after.kinds['permission.request'] || 0) - (before.kinds['permission.request'] || 0)
const noisy = arrived - ((after.kinds['pong'] || 0) - (before.kinds['pong'] || 0))
console.log(`  frequent frames (everything but the heartbeat): ${noisy}`)
console.log(`  reach-out-eligible frames in the window: permission.request ${reachOuts}`)

ok('THE BUSY HALF OF THE WATCH SWEPT SOMETHING — frames other than the heartbeat arrived',
   noisy > 0, `${noisy} frames`)
if (reachOuts === 0) {
  ok('a busy run in the background raises ZERO notifications',
     after.notifications.length === 0, JSON.stringify(after.notifications))
  ok('and never prefixes the title with a count', ![...titles].some((t) => /^\(\d+\)/.test(t)),
     [...titles].join(' | '))
} else {
  ok('a permission request DID arrive, and the product interrupted for it',
     after.notifications.length >= 1 && [...titles].some((t) => /^\(\d+\)/.test(t)),
     `${reachOuts} reach-outs · ${JSON.stringify(after.notifications)} · ${[...titles].join(' | ')}`)
}
ok('and never says the socket went away', ![...titles].some((t) => t.startsWith('(offline)')))
ok('and leaves no unread dot on the favicon',
   !(await page.evaluate(() => document.querySelector('link[rel="icon"]')?.getAttribute('href') ?? ''))
     .includes('circle'))

await page.screenshot({ path: `${SHOTS}/tier3-396-rail-after-busy-run.png` })

// and the surface a person reads this ticket on, on the real repository
const ticket = await ctx.newPage()
await ticket.goto(`${BASE}/#/tickets/WEB-FIX-396`, { waitUntil: 'networkidle' })
await ticket.waitForTimeout(3000)
const body = await ticket.evaluate(() => document.body.innerText)
ok('the ticket surface answers for WEB-FIX-396', body.includes('WEB-FIX-396'),
   body.split('\n').filter(Boolean).slice(0, 3).join(' | '))
await ticket.screenshot({ path: `${SHOTS}/tier3-396-ticket.png`, fullPage: true })

console.log(fails.length ? `\n  TIER 3 FAILED: ${fails.join('; ')}` : '\n  TIER 3: every check passed')
await browser.close()
process.exit(fails.length ? 1 : 0)

/**
 * WEB-FIX-326 — tier 3, driven against THIS repository rather than the gate's fixture.
 *
 * The browser gate drives `gate/fixture-repo`, and §1's three controls were all found there. This
 * opens the same three on the repository a person actually uses, measures the ink against the
 * ground the page paints behind it — flattened in the page, exactly as `browser-gate.mjs` does,
 * so the two numbers are comparable — and photographs each surface.
 *
 *   FACTORY_HTTP_PORT=7887 FACTORY_WEB_PORT=7889 ./build.sh run "$PWD" --no-open
 *   TIER3_THEME=light node gate/tier3-web-fix-326.mjs http://127.0.0.1:7889 <out-dir> <ticket-id>
 *
 * The third control is REACHABLE ONLY WHERE THE MANIFEST DECLARES `agent.allowed_tools`: the
 * `declared in the manifest` row exists for a rule nobody may revoke, and this repository grants
 * none, so it reports ABSENT here and is measured by the gate on the fixture, which declares two.
 * A row that is not on the screen is reported, never skipped.
 */
import { chromium } from 'playwright-core'
import { writeFileSync, mkdirSync } from 'node:fs'

const BASE = process.argv[2]
const OUT = process.argv[3]
mkdirSync(OUT, { recursive: true })

const lum = (c) => {
  const m = c.match(/^rgb\((\d+), (\d+), (\d+)\)$/)
  const lin = (x) => (x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4)
  const [r, g, b] = m.slice(1, 4).map((v) => lin(Number(v) / 255))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)]
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

const measure = (page, selector) => page.evaluate((sel) => {
  const el = document.querySelector(sel)
  if (!el) return null
  const ctx = document.createElement('canvas').getContext('2d', { willReadFrequently: true })
  const px = () => { const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data; return { rgb: `rgb(${r}, ${g}, ${b})`, a } }
  const opaque = (c) => { ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = 'rgba(0,0,0,0)'; ctx.fillStyle = c; ctx.fillRect(0, 0, 1, 1); return px().a === 255 }
  const flatten = (c, over) => { ctx.fillStyle = over; ctx.fillRect(0, 0, 1, 1); ctx.fillStyle = c; ctx.fillRect(0, 0, 1, 1); return px().rgb }
  let ground = 'rgb(0, 0, 0)'
  for (let n = el; n; n = n.parentElement) {
    const c = getComputedStyle(n).backgroundColor
    if (opaque(c)) { ground = flatten(c, 'rgb(0, 0, 0)'); break }
  }
  const s = getComputedStyle(el)
  return { text: el.textContent.trim(), fg: flatten(s.color, ground), bg: ground, size: parseFloat(s.fontSize) }
}, selector)

const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox', '--font-render-hinting=none'] })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const log = []
const say = (s) => { console.log(s); log.push(s) }

const THEME = process.env.TIER3_THEME || 'light'
await page.goto(`${BASE}/#/`, { waitUntil: 'networkidle' })
await page.evaluate((t) => localStorage.setItem('factory.theme', t), THEME)
await page.reload({ waitUntil: 'networkidle' })
say(`theme applied: ${await page.getAttribute('html', 'data-theme')}`)

const ticket = process.argv[4]
const STOPS = [
  ['#/',                       'a.home-more',                'all requirements'],
  [`#/tickets/${ticket}/session`, 'button.empty-action, a.empty-action', 'Start a lane'],
  ['#/permissions',            '.perms-declared a',          'manifest'],
]

let below = 0
for (const [route, sel, what] of STOPS) {
  await page.goto(`${BASE}/${route}`, { waitUntil: 'networkidle' })
  await page.mouse.move(0, 0)
  await page.waitForTimeout(400)
  const name = route.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '') || 'home'
  await page.screenshot({ path: `${OUT}/tier3-${name}-${THEME}.png` })
  const m = await measure(page, sel)
  if (!m) { say(`  ABSENT  ${route}  ${sel}  (${what})`); below += 1; continue }
  const r = ratio(m.fg, m.bg)
  const floor = m.size >= 14 ? 4.5 : 3.0
  if (r < floor) below += 1
  say(`  ${r >= floor ? 'READS' : 'BELOW'}  ${r.toFixed(2)}:1 vs ${floor.toFixed(1)}:1  ${m.size}px  `
    + `${sel}  "${m.text}"  ${m.fg} on ${m.bg}  (${route})`)
}
say(`${below} below floor`)
await browser.close()
writeFileSync(`${OUT}/tier3-${THEME}.log`, log.join('\n') + '\n')
process.exit(below === 0 ? 0 : 1)

/**
 * WEB-FIX-329 — tier 3, driven against THIS repository rather than the gate's fixture.
 *
 * The corpus this ticket re-shoots is eight photographs of four surfaces, taken against
 * `gate/fixture-repo`: nine synthetic tickets, no lane, one owner. This opens the same four
 * surfaces on the repository a person actually uses — 380-odd tickets, a live lane rail, a real
 * owner derived from commits — and writes what each one drew, so the re-judged rows can be read
 * against a screen that is not the one they were written from.
 *
 *   FACTORY_HTTP_PORT=7801 FACTORY_WEB_PORT=7803 ./build.sh run "$PWD" --no-open
 *   node gate/tier3-web-fix-329.mjs http://127.0.0.1:7803 <out-dir> <ticket-id>
 */
import { chromium } from 'playwright-core'
import { writeFileSync, mkdirSync } from 'node:fs'

const BASE = process.argv[2]
const OUT = process.argv[3]
const TICKET = process.argv[4]
mkdirSync(OUT, { recursive: true })

const SURFACES = [
  ['all-lanes', '#/lanes'],
  ['ticket-session', `#/tickets/${TICKET}/session`],
  ['report-md', `#/tickets/${TICKET}/report`],
  ['diff', `#/tickets/${TICKET}/diff`],
]

const CHROME = process.env.CHROME_PATH || '/usr/bin/google-chrome'
const browser = await chromium.launch({
  executablePath: CHROME, headless: true, args: ['--no-sandbox', '--font-render-hinting=none'],
})
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })

for (const theme of ['dark', 'light']) {
  for (const [name, route] of SURFACES) {
    await page.goto(`${BASE}/${route}`, { waitUntil: 'networkidle' })
    // Through the CONTROL a person uses, never by setting the attribute: the chip in the top bar
    // draws the theme it believes is on, and a screenshot taken by writing `data-theme` behind its
    // back is a picture of a screen contradicting itself that nobody chose.
    for (let n = 0; n < 3; n += 1) {
      if (await page.getAttribute('html', 'data-theme') === theme) break
      await page.click('[data-testid="theme-toggle"]')
      await page.waitForTimeout(250)
    }
    const applied = await page.getAttribute('html', 'data-theme')
    if (applied !== theme) throw new Error(`asked for ${theme}, the page is ${applied}`)
    await page.waitForTimeout(400)
    const text = await page.evaluate(() => document.body.innerText)
    const nav = await page.$$eval('[data-testid="side-nav"] a, nav a', (els) =>
      els.map((e) => e.innerText.replace(/\n/g, ' ').trim()))
    writeFileSync(`${OUT}/${name}-${theme}.txt`, `${nav.join(' | ')}\n\n${text}\n`)
    await page.screenshot({ path: `${OUT}/${name}-${theme}.png`, fullPage: false })
    console.log(`  drew ${name}-${theme} · rail: ${nav.join(' · ') || 'NONE'}`)
  }
}
console.log(`  page errors: ${errors.length ? errors.join(' | ') : 'none'}`)
await browser.close()

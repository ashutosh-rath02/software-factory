/**
 * Tier 3 for WEB-FEAT-398 — drive #/metrics as a person, against the REAL repository.
 *
 * Not the gate's synthetic fixture: the base URL given here is a dev client proxying to the
 * factory instance that is running this lane, so every number on screen is this repository's
 * own history (WEB-FIX-248, WEB-FIX-261).
 */
import { chromium } from 'playwright-core'
import { writeFileSync } from 'node:fs'

const BASE = process.argv[2]
const OUT = process.argv[3]
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome' })
const said = []
const log = (s) => { said.push(s); console.log(s) }

for (const theme of ['dark', 'light']) {
  const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } })
  const asked = []
  page.on('request', (r) => { if (r.url().includes('/api/metrics')) asked.push(r.url()) })
  page.on('pageerror', (e) => log(`  PAGE ERROR: ${e.message}`))
  await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
  await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
  await page.reload({ waitUntil: 'networkidle' })

  log(`\n── ${theme} ── a person clicks Metrics in the rail`)
  await page.click('.sidenav-row:has(.sidenav-label:text-is("Metrics"))')
  await page.waitForSelector('[data-testid="metrics-page"]', { timeout: 15000 })
  await page.waitForSelector('[data-testid="metric-runs"]', { timeout: 15000 })
  log(`  the address is ${await page.evaluate(() => location.hash)}`)
  const read = async () => {
    const tiles = await page.$$eval('[data-testid^="metric-"][class="metric-tile"]', (els) =>
      els.map((e) => [
        e.querySelector('[data-testid="metric-label"]').textContent.trim(),
        e.querySelector('[data-testid="metric-value"]').textContent.trim(),
        e.querySelector('[data-testid="metric-n"]').textContent.trim(),
        e.querySelector('[data-testid="metric-second"]')?.textContent.trim() ?? '',
      ].filter(Boolean).join('  ')))
    const head = await page.$eval('[data-testid="metrics-days"]', (e) => e.textContent.trim())
    const stages = await page.$$eval('tbody tr[data-testid^="metric-stage-"]', (els) =>
      els.map((e) => [...e.querySelectorAll('td')].map((t) => t.textContent.trim()).join('  ')))
    return { head, tiles, stages }
  }
  for (const w of [30, 7, 90]) {
    if (w !== 30) {
      log(`\n  …and clicks "${w} days"`)
      await page.click(`[data-testid="metrics-window-${w}"]`)
      // Wait on a FACT the thing itself produces, never on time passing (SRV-FIX-034): the head
      // reads the window E32 ECHOED, so it saying `7 days` is the new answer having landed.
      await page.waitForFunction(
        (want) => document.querySelector('[data-testid="metrics-days"]')?.textContent?.trim()
          === want, `${w} days`, { timeout: 30000 })
    }
    const shown = await read()
    log(`  head: ${shown.head}`)
    for (const t of shown.tiles) log(`    ${t}`)
    for (const s of shown.stages) log(`    stage  ${s}`)
  }
  log(`\n  the windows the surface asked for, in order:`)
  for (const u of asked) log(`    ${u.replace(BASE, '')}`)
  await page.screenshot({ path: `${OUT}/tier3-metrics-${theme}.png`, fullPage: true })
  await page.close()
}
writeFileSync(`${OUT}/tier3-transcript.txt`, said.join('\n'))
await browser.close()

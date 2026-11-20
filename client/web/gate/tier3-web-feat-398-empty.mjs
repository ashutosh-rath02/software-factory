/** Tier 3, the empty state — a REAL repository (this lane's own worktree) with no run record. */
import { chromium } from 'playwright-core'
const BASE = process.argv[2]
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome' })
for (const theme of ['dark', 'light']) {
  const page = await browser.newPage({ viewport: { width: 1500, height: 700 } })
  await page.goto(`${BASE}/#/requirements`, { waitUntil: 'networkidle' })
  await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
  await page.reload({ waitUntil: 'networkidle' })
  await page.click('.sidenav-row:has(.sidenav-label:text-is("Metrics"))')
  await page.waitForSelector('[data-testid="metrics-page"]')
  await page.waitForSelector('[data-testid="metrics-empty"]', { timeout: 20000 })
  const e32 = await page.evaluate(async () => (await (await fetch('/api/metrics?days=30')).json()))
  console.log(`\n── ${theme} ── #/metrics on a repository with no run record`)
  console.log(`  E32 says runs=${e32.runs}, escapes_in_window=${e32.escapes_in_window}`)
  console.log(`  the screen says: "${await page.$eval('[data-testid="metrics-empty"]', (e) => e.textContent.trim())}"`)
  console.log(`  tiles on screen: ${(await page.$$('[class="metric-tile"]')).length}`)
  console.log(`  head figure on screen: ${(await page.$('[data-testid="metrics-days"]')) ? 'yes' : 'none'}`)
  console.log(`  the window control is still there: ${(await page.$$('[data-testid^="metrics-window-"]')).length} chips`)
  await page.screenshot({ path: `${process.argv[3]}/tier3-metrics-empty-${theme}.png` })
  await page.close()
}
await browser.close()

/**
 * WEB-FIX-336 — tier 3, driven against THIS repository rather than the gate's fixture.
 *
 * The browser gate drives `gate/fixture-repo`, a synthetic repository with a handful of tickets.
 * §1 is about a frame whose numbers must be the size this repository actually holds, so the only
 * screen that can answer it is the one pointed at this repository.
 *
 *   node gate/tier3-web-fix-336.mjs <base-url> <out-dir>
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from 'playwright-core'

const BASE = process.argv[2]
const OUT = process.argv[3]
const CHROME = process.env.CHROME_PATH || '/opt/google/chrome/chrome'
if (!BASE || !OUT) { console.error('usage: tier3-web-fix-336.mjs <base-url> <out-dir>'); process.exit(2) }
mkdirSync(OUT, { recursive: true })

const say = (...a) => console.log(...a)

const browser = await chromium.launch({ executablePath: CHROME, headless: true,
                                        args: ['--no-sandbox', '--font-render-hinting=none'] })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('pageerror', (e) => errors.push(String(e)))
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })

const read = async () => await page.evaluate(() => {
  const block = document.querySelector('[data-testid="tickets-by-epic"]')
  const result = document.querySelector('[data-testid="search-result"]')
  const nav = [...document.querySelectorAll('.sidenav-row')]
    .find((r) => r.querySelector('.sidenav-label')?.textContent === 'Tickets')
  return {
    pill: block?.querySelector('.block-head .pill')?.textContent ?? null,
    result: result?.textContent ?? null,
    navCount: nav?.querySelector('.sidenav-count')?.textContent ?? null,
    rows: document.querySelectorAll('[data-testid="epic-group"] tbody tr').length,
  }
})

for (const theme of ['light', 'dark']) {
  // A hash change on a mounted SPA is not a reload, and the previous theme's query would
  // survive into this one — the reading has to be of a screen somebody just opened.
  await page.goto('about:blank')
  await page.goto(`${BASE}/#/tickets`, { waitUntil: 'networkidle' })
  await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme)
  await page.waitForSelector('[data-testid="tickets-by-epic"] .block-head .pill')
  const before = await read()
  say(`[${theme}] no query      · header pill ${before.pill} · result line ${before.result}`
      + ` · rows on the page ${before.rows} · rail count ${before.navCount}`)
  await page.screenshot({ path: join(OUT, `tickets-${theme}.png`), fullPage: false })

  await page.fill('[data-testid="tickets-search"]', '-')
  await page.waitForSelector('[data-testid="search-result"]')
  await page.waitForTimeout(400)
  const dash = await read()
  say(`[${theme}] query "-"     · header pill ${dash.pill} · result line ${dash.result}`)
  await page.screenshot({ path: join(OUT, `tickets-search-dash-${theme}.png`), fullPage: false })

  await page.fill('[data-testid="tickets-search"]', 'WEB-FIX-336')
  await page.waitForTimeout(400)
  const one = await read()
  say(`[${theme}] query "WEB-FIX-336" · header pill ${one.pill} · result line ${one.result}`)
  await page.screenshot({ path: join(OUT, `tickets-search-one-${theme}.png`), fullPage: false })

  writeFileSync(join(OUT, `tier3-${theme}.json`),
                JSON.stringify({ before, dash, one }, null, 2) + '\n')
}

say(errors.length ? `page errors: ${errors.join(' | ')}` : 'the page threw and logged nothing')
await browser.close()

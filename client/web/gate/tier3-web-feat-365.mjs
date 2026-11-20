/**
 * WEB-FEAT-365 — tier 3, driven in a real Chrome against the REAL repository.
 *
 * `npm run gate` drives `gate/fixture-repo`, a synthetic repository, which is what `WEB-FIX-248`
 * and `WEB-FIX-261` are about. This drives the tool pointed at THIS repository, on the port
 * `./build.sh run` serves, the way somebody would who wanted the thing the ticket does — and it
 * photographs the top bar in both themes so the report shows the hint rather than claiming it.
 *
 * It runs the halves jsdom cannot: a real `contenteditable`, a real `Alt` modifier delivered by
 * Chrome's own input pipeline, and the real `<kbd>` at 9 px measured against the bar behind it.
 *
 *   node gate/tier3-web-feat-365.mjs <base-url> <shots-dir>
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from 'playwright-core'

const BASE = process.argv[2] ?? 'http://localhost:7779'
const SHOTS = process.argv[3] ?? 'shots'
const CHROME = process.env.CHROME_PATH || '/usr/bin/google-chrome'

let failures = 0
const ok = (what, pass, detail = '') => {
  console.log(`  ${pass ? 'PASS' : 'FAIL'}  ${what}${detail ? ` — ${detail}` : ''}`)
  if (!pass) failures += 1
}

const hash = (page) => page.evaluate(() => location.hash)
const setHash = async (page, to) => {
  await page.evaluate((h) => { location.hash = h }, to)
  await page.waitForTimeout(300)
}

/**
 * Focus outside every field, without clicking anything.
 *
 * `page.click('body')` lands in the middle of a real surface and can press whatever is drawn
 * there; this instance is pointed at the REAL repository and nothing here may act on it.
 */
const focusNothing = (page) => page.evaluate(() => {
  document.activeElement?.blur?.()
  document.body.tabIndex = -1
  document.body.focus()
})

/** Alt+N, through Chrome's own keyboard pipeline — not a synthetic event object. */
const altN = async (page) => {
  await page.keyboard.down('Alt')
  await page.keyboard.press('KeyN')
  await page.keyboard.up('Alt')
  await page.waitForTimeout(400)
}

mkdirSync(SHOTS, { recursive: true })

const browser = await chromium.launch({
  executablePath: CHROME, headless: true, args: ['--no-sandbox', '--font-render-hinting=none'],
})
const page = await browser.newPage({ viewport: { width: 1480, height: 900 } })

try {
  await page.goto(`${BASE}/#/`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)

  const lanes = await page.evaluate(async () =>
    (await (await fetch('/api/lanes')).json()))
  const needing = (lanes.lanes ?? []).filter((row) => row.needs_you)
  console.log(`\n  the repository this instance is pointed at holds ${(lanes.lanes ?? []).length} `
    + `lanes, ${needing.length} of which need a person`)
  for (const row of lanes.lanes ?? []) {
    console.log(`    ${row.lane}  ${row.ticket_id}  needs_you=${row.needs_you}  `
      + `${row.state?.process}`)
  }

  const chip = await page.$('[data-testid="needs-you-chip"]')

  if (chip) {
    // ── the hint is drawn, inside the chip, and it is what the module exports
    const hint = (await page.textContent('[data-testid="needs-you-key"]') ?? '').trim()
    ok('the chip carries a key hint', hint !== '', JSON.stringify(hint))
    const inside = await page.evaluate(() =>
      document.querySelector('[data-testid="needs-you-key"]')
        ?.closest('[data-testid="needs-you-chip"]') !== null)
    ok('the hint is inside the chip\'s own anchor', inside)

    const title = await chip.getAttribute('title')
    ok('the chip says the key on hover', (title ?? '').includes('alt+N'), title ?? '')

    // ── the ink and the size the design named, measured off the live element
    const drawn = await page.evaluate(() => {
      const el = document.querySelector('[data-testid="needs-you-key"]')
      const s = getComputedStyle(el)
      return { size: s.fontSize, colour: s.color, family: s.fontFamily }
    })
    ok('drawn at the vocabulary sheet\'s smallest size', drawn.size === '9px', drawn.size)
    console.log(`        ink ${drawn.colour} · family ${drawn.family}`)

    // ── the key goes where the chip goes
    const where = await chip.getAttribute('href')
    await setHash(page, '#/requirements')
    await focusNothing(page)
    await altN(page)
    ok('Alt+N arrives where the chip points', await hash(page) === where,
      `${await hash(page)} vs href ${where}`)

    // ── in a real contenteditable, the key TYPES. jsdom implements none, so this half of the
    //    mandatory negative can only be measured here.
    await setHash(page, '#/requirements')
    await page.evaluate(() => {
      const box = document.createElement('div')
      box.id = 'tier3-prose'
      box.setAttribute('contenteditable', 'true')
      box.style.cssText = 'position:fixed;bottom:0;left:0;width:200px;height:40px;z-index:9999'
      document.body.appendChild(box)
      box.focus()
    })
    const before = await hash(page)
    await altN(page)
    ok('in a real contenteditable the key does not navigate', await hash(page) === before,
      `${before} → ${await hash(page)}`)
    const typed = await page.evaluate(() => {
      const el = document.getElementById('tier3-prose')
      const text = el?.textContent ?? ''
      el?.remove()
      return text
    })
    console.log(`        the prose box holds ${JSON.stringify(typed)} after the press`)

    // ── in a real <input>, the same. The requirements filter is the client's own.
    await setHash(page, '#/requirements')
    await page.click('input.filter-input')
    const beforeInput = await hash(page)
    await altN(page)
    ok('in the requirements filter the key does not navigate',
      await hash(page) === beforeInput, `${beforeInput} → ${await hash(page)}`)

    // ── the control: from outside the field, on the same screen, it still goes
    await focusNothing(page)
    await altN(page)
    ok('and from outside the field it still goes', await hash(page) === where,
      `${await hash(page)} vs href ${where}`)
  } else {
    console.log('\n  no lane in this repository needs a person, so the chip is absent (§4.1\'s '
      + 'Empty rule) and this run drives the negative only')
    await setHash(page, '#/requirements')
    await focusNothing(page)
    const before = await hash(page)
    await altN(page)
    ok('with the chip absent, Alt+N leaves the address alone', await hash(page) === before,
      `${before} → ${await hash(page)}`)
  }

  // ── the photograph §1 asks for: the top bar, both themes
  for (const theme of ['dark', 'light']) {
    await page.evaluate((t) => localStorage.setItem('factory.theme', t), theme)
    await page.reload({ waitUntil: 'networkidle' })
    await page.waitForTimeout(600)
    await page.mouse.move(0, 0)
    const bar = await page.$('[data-testid="top-bar"]')
    await bar.screenshot({ path: join(SHOTS, `top-bar-${theme}.png`) })
    await page.screenshot({ path: join(SHOTS, `surface-${theme}.png`) })
    const text = (await bar.textContent()) ?? ''
    console.log(`  shot  top-bar-${theme}.png · the bar reads ${JSON.stringify(text.trim())}`)
  }

  writeFileSync(join(SHOTS, 'lanes.json'), JSON.stringify(lanes, null, 2))
} finally {
  await browser.close()
}

console.log(failures === 0 ? '\n── TIER 3 GREEN ──' : `\n── TIER 3: ${failures} failed ──`)
process.exit(failures === 0 ? 0 : 1)

import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * WEB-FIX-169 — the state palette must be legible AS A PALETTE.
 *
 * `theme.test.tsx` already proves every token equals the design's, in both themes. It cannot say
 * whether the design's own values are distinguishable from each other, and that is exactly what
 * went wrong: `--st-done` was `var(--color-neutral-600)` — the same ink as `--t3` body text — and
 * `--st-none` a near-grey beside it, so the two commonest states on a real repository were one
 * colour and the state column carried no information.
 *
 * **The metric is perceptual distance in OKLab, not hue.** §1 of the ticket asked for "hue ≥ 30°
 * apart" and that criterion is WRONG: it fails the correct palette, because `--st-none` is
 * deliberately low-chroma (0.055 against everything else's 0.108) and separates from its
 * neighbours by chroma and lightness rather than by hue — `none` at 265° and `wait` at 246° are
 * 19° apart and are not remotely confusable. Measured, worst pair over the whole palette:
 *
 *   the shipped values      ΔEok 0.0774   (run vs done — the pair the ticket worries about)
 *   the defect being fixed  ΔEok 0.0283   (done vs none — one colour, two meanings)
 *   `done` as a green 158   ΔEok 0.0000   (run vs done — identical)
 *
 * so the threshold is 0.05: above every legitimate pair, below both wrong answers.
 */

const HERE = dirname(fileURLToPath(import.meta.url))
const TOKENS = readFileSync(join(HERE, '../src/styles/tokens.css'), 'utf8')

function block(css: string, selector: string): Map<string, string> {
  const start = css.indexOf(selector)
  const open = css.indexOf('{', start)
  let depth = 0, end = open
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === '{') depth += 1
    else if (css[i] === '}') { depth -= 1; if (depth === 0) { end = i; break } }
  }
  const out = new Map<string, string>()
  for (const decl of css.slice(open + 1, end).replace(/\/\*[\s\S]*?\*\//g, '').split(';')) {
    const at = decl.indexOf(':')
    if (at < 0) continue
    const name = decl.slice(0, at).trim()
    if (name.startsWith('--')) out.set(name, decl.slice(at + 1).trim())
  }
  return out
}

const dark = block(TOKENS, ':root')
const light = new Map([...dark, ...block(TOKENS, '[data-theme="light"]')])

/** Follow `var(--x)` to a literal. */
function resolve(vars: Map<string, string>, name: string, depth = 0): string {
  const v = (vars.get(name) ?? '').trim()
  const m = /^var\(\s*(--[\w-]+)\s*\)$/.exec(v)
  if (m && depth < 8) return resolve(vars, m[1], depth + 1)
  return v
}

type RGB = [number, number, number]

function toRgb(value: string): RGB | null {
  const hex = /^#([0-9a-f]{6})$/i.exec(value)
  if (hex) {
    const h = hex[1]
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as RGB
  }
  const ok = /^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\)$/.exec(value)
  if (ok) return oklchToRgb(+ok[1], +ok[2], +ok[3])
  return null
}

function oklchToRgb(L: number, C: number, H: number): RGB {
  const h = (H * Math.PI) / 180
  const a = C * Math.cos(h), b = C * Math.sin(h)
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
  const s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
  const lin = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
  ]
  return lin.map((x) => {
    const v = x <= 0.0031308 ? 12.92 * x : 1.055 * Math.max(x, 0) ** (1 / 2.4) - 0.055
    return Math.min(1, Math.max(0, v))
  }) as RGB
}

function toOklab(c: RGB): RGB {
  const f = (x: number) => (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4)
  const [r, g, b] = c.map(f)
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  return [
    0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
  ]
}

const deltaE = (a: RGB, b: RGB) => {
  const [x, y, z] = toOklab(a), [p, q, r] = toOklab(b)
  return Math.hypot(x - p, y - q, z - r)
}

function luminance(c: RGB): number {
  const f = (x: number) => (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4)
  const [r, g, b] = c.map(f)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const contrast = (a: RGB, b: RGB) => {
  const [x, y] = [luminance(a), luminance(b)]
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

/** OKLab → L, chroma, hue°. Hue is meaningless at low chroma and callers must not ask for it. */
function toLch(c: RGB): { L: number; C: number; h: number } {
  const [L, a, b] = toOklab(c)
  return { L, C: Math.hypot(a, b), h: ((Math.atan2(b, a) * 180) / Math.PI + 360) % 360 }
}

const hueGap = (p: number, q: number) => {
  const d = Math.abs(p - q) % 360
  return d > 180 ? 360 - d : d
}

const STATE_TONES = ['--st-run', '--st-silent', '--st-crash', '--st-wait', '--st-claim',
                     '--st-done', '--st-none']
const THEMES: [string, Map<string, string>][] = [['dark', dark], ['light', light]]

/** The palette's worst pair, in one theme — the number the threshold is about. */
function worstPair(vars: Map<string, string>) {
  let worst = { d: Infinity, a: '', b: '' }
  for (let i = 0; i < STATE_TONES.length; i += 1) {
    for (let j = i + 1; j < STATE_TONES.length; j += 1) {
      const A = toRgb(resolve(vars, STATE_TONES[i]))
      const B = toRgb(resolve(vars, STATE_TONES[j]))
      expect(A, `${STATE_TONES[i]} is not a colour this test can read`).not.toBeNull()
      expect(B, `${STATE_TONES[j]} is not a colour this test can read`).not.toBeNull()
      const d = deltaE(A!, B!)
      if (d < worst.d) worst = { d, a: STATE_TONES[i], b: STATE_TONES[j] }
    }
  }
  return worst
}

describe('WEB-FIX-169 · the state palette is legible as a palette', () => {
  it('test_no_two_state_tones_are_confusable', () => {
    for (const [name, vars] of THEMES) {
      const worst = worstPair(vars)
      expect(worst.d, `${name}: ${worst.a} and ${worst.b} are ΔEok ${worst.d.toFixed(4)} apart — `
        + 'two states drawn in one colour say nothing').toBeGreaterThanOrEqual(0.05)
    }
  })


  /**
   * §1's criterion, delivered as §1 wrote it — `WEB-FIX-169`'s QA, finding F1.
   *
   * Round 1 replaced this with the perceptual rule above and argued in the report and the commit
   * message that §1 was "WRONG" because `--st-none` at 265° and `--st-wait` at 246° are 19° apart.
   * That reading dropped four words. §1 says **at the same lightness**, and those two are at
   * L 0.56 and L 0.68 — the clause never covered the pair used to discard it. Read as written the
   * criterion is satisfiable, and this palette satisfies it with room: every same-lightness pair is
   * at least 42° apart.
   *
   * The two rules are not redundant. `ΔEok ≥ 0.05` behaves like `hue ≥ 28°` at this palette's
   * chroma and lightness, so hues 186 and 187 pass it while §1's rule refuses them — a two-degree
   * band on the one axis §1 singles out as the confusion the palette cannot afford. QA measured
   * that band and this case closes it. Transcribed from its reproduction: `--st-done` at hue 186
   * must go RED here and does.
   *
   * An implementing session does not get to replace a §1 criterion it judges wrong. That is the
   * human's call, and §2 says the agent follows §1 verbatim.
   */
  it('test_every_same_lightness_pair_is_at_least_30_degrees_apart', () => {
    const SAME_L = 0.05          // --st-claim sits at L 0.66 against the others' 0.68
    const CHROMATIC = 0.04       // below this a tone is a grey and its hue is noise

    for (const [name, vars] of THEMES) {
      const tones = STATE_TONES
        .map((t) => ({ token: t, ...toLch(toRgb(resolve(vars, t))!) }))
        .filter((t) => t.C >= CHROMATIC)

      expect(tones.length, `${name}: a state tone with no chroma is a grey, and this palette `
        + 'may not contain one').toBe(STATE_TONES.length)

      for (let i = 0; i < tones.length; i += 1) {
        for (let j = i + 1; j < tones.length; j += 1) {
          if (Math.abs(tones[i].L - tones[j].L) > SAME_L) continue
          const gap = hueGap(tones[i].h, tones[j].h)
          expect(gap, `${name}: ${tones[i].token} (${tones[i].h.toFixed(1)}deg, L `
            + `${tones[i].L.toFixed(2)}) and ${tones[j].token} (${tones[j].h.toFixed(1)}deg, L `
            + `${tones[j].L.toFixed(2)}) are ${gap.toFixed(1)}deg apart at the same lightness`)
            .toBeGreaterThanOrEqual(30)
        }
      }
    }
  })

  /**
   * Negative (mandatory). The cheap wrong fix is to brighten `not started` until it is obviously
   * coloured — which makes the state NOBODY HAS BEGUN louder than the ticket titles beside it, and
   * every row on a fresh repository is that state. `WEB-FIX-026`'s defect, inverted.
   *
   * The rule it set stands: `--st-none` clears 3.0:1 on every ground a row can sit on, and stays
   * the quietest text on the screen — quieter than `--t3`, the next quietest.
   */
  it('test_not_started_stays_the_quietest_text_on_the_screen', () => {
    for (const [name, vars] of THEMES) {
      const none = toRgb(resolve(vars, '--st-none'))!
      const t3 = toRgb(resolve(vars, '--t3'))!
      const grounds = ['--c-panel', '--c-raise', '--c-canvas']
        .map((g) => toRgb(resolve(vars, g))!)

      for (const ground of grounds) {
        expect(contrast(none, ground),
          `${name}: --st-none is unreadable on one of its grounds`).toBeGreaterThanOrEqual(3.0)
      }
      const panel = grounds[0]
      expect(contrast(none, panel),
        `${name}: --st-none (${contrast(none, panel).toFixed(2)}:1) is louder than --t3 `
        + `(${contrast(t3, panel).toFixed(2)}:1) — the state nobody has begun is every row on a `
        + 'fresh repository, and it may not shout over the titles')
        .toBeLessThan(contrast(t3, panel))
    }
  })
})

/**
 * WEB-FIX-326 — a token with one value is a token with one THEME.
 *
 * The client's tokens come from two design sources and only one of them is themed. `factory.pen`
 * declares seventeen variables with a light value and a dark value each, and `gen:tokens` writes
 * them into both blocks. `nocturne.css` declares its own in a single `:root` — nocturne's dark
 * ground — so a nocturne token the client paints reaches the light theme by inheritance and keeps
 * the value that was chosen for a dark one.
 *
 * `--color-accent` is the ink for a link and for a quiet action. The browser gate measured what
 * that inheritance costs on a light panel and had been standing red about it:
 *
 *   BELOW FLOOR  2.62:1 < 3.0:1  12px    light  a.home-more          "all requirements →"
 *   BELOW FLOOR  2.81:1 < 3.0:1  10.5px  light  button.empty-action  "Start a lane"
 *   BELOW FLOOR  2.62:1 < 3.0:1  11.5px  light  a                    "manifest"
 *
 * Two cases, and they are not the same question. The first is about the accent and the floor it
 * has to clear. The second is about the CLASS the defect belongs to, and its set is derived from
 * the client's own stylesheets rather than typed here: a list somebody types is bounded by what
 * they already thought of, and the second member of this class — `--color-bg`, the page ground
 * under `.app` — was not in anybody's list until the query found it.
 */

/** The three surface fills the design paints behind text. `--c-line`/`--c-line2` are rules and
 *  borders, and nothing sets a text node's ground to one. */
const GROUNDS = ['--c-canvas', '--c-panel', '--c-raise']

/** Every custom property the client's own source references, as `var(--name`. */
function referencedByTheClient(): Set<string> {
  const roots = [join(HERE, '../src')]
  const files: string[] = []
  while (roots.length) {
    const dir = roots.pop()!
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name)
      if (entry.isDirectory()) roots.push(path)
      else if (/\.(css|ts|tsx)$/.test(entry.name) && entry.name !== 'tokens.css') files.push(path)
    }
  }
  const out = new Set<string>()
  for (const file of files) {
    for (const m of readFileSync(file, 'utf8').matchAll(/var\(\s*(--[\w-]+)/g)) out.add(m[1])
  }
  return out
}

describe('WEB-FIX-326 · the accent is ink, and it is ink in both themes', () => {
  /**
   * §1 criterion 1, at the token. The gate says it about three rendered elements; this says it
   * about the value all three read, so it can be watched red without a browser.
   *
   * The floor is 4.5:1 — WCAG's for normal text. The gate applies 3.0:1 to the three elements
   * above only because each happens to be drawn under 14px; `.md a` is body ink, and a link whose
   * legibility depends on staying small is not a link the design can promise anything about.
   */
  it('test_the_accent_clears_the_text_floor_on_every_ground_in_both_themes', () => {
    for (const [name, vars] of THEMES) {
      const value = resolve(vars, '--color-accent')
      const accent = toRgb(value)
      expect(accent, `${name}: --color-accent is not a colour this test can read`).not.toBeNull()

      for (const ground of GROUNDS) {
        const behind = toRgb(resolve(vars, ground))!
        const ratio = contrast(accent!, behind)
        expect(ratio, `${name}: --color-accent ${value} on ${ground} `
          + `${resolve(vars, ground)} is ${ratio.toFixed(2)}:1 — this is the ink the gate named `
          + 'in a.home-more "all requirements →", button.empty-action "Start a lane" and '
          + 'a "manifest"').toBeGreaterThanOrEqual(4.5)
      }
    }
  })

  /**
   * The class. A colour the client paints that is declared for the dark theme and never for the
   * light one is a colour the light theme draws in the dark theme's value — which is the defect,
   * stated without naming the token it was found through.
   */
  it('test_no_colour_the_client_paints_is_declared_for_one_theme_only', () => {
    const used = referencedByTheClient()
    const lightBlock = block(TOKENS, '[data-theme="light"]')

    const painted = [...dark.keys()].filter((t) => used.has(t) && toRgb(resolve(dark, t)) !== null)
    expect(painted.length, 'no colour token is referenced by the client at all — this case swept '
      + 'nothing, and a sweep of nothing is not a check').toBeGreaterThan(0)

    const darkOnly = painted.filter((t) => !lightBlock.has(t))
    expect(darkOnly, `declared in :root and nowhere else, so the light theme paints the dark `
      + `theme's value: ${darkOnly.join(', ')}`).toEqual([])
  })
})

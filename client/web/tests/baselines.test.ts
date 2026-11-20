import { describe, expect, it } from 'vitest'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'

/**
 * WEB-FEAT-085 round 2 — the invariants QA round 1 found nothing asserting.
 *
 * Transcribed from `factory/docs/reports/WEB-FEAT-085-qa.md`, findings 1 and 2, and from the
 * reproduction each carries. Round 1 committed fourteen baselines: two of them draw a lane card
 * the audit had already judged defective on a neighbouring surface (F9, F1), and five of them do
 * not reproduce across two runs of the gate. Both were visible in the artefacts and neither had a
 * test, so both survived a green vitest, a green browser gate and the audit's own eyes.
 *
 * These read the committed deliverable, not a mock. That is deliberate: the deliverable IS the
 * artefact under test for this ticket.
 */

const CLIENT = join(__dirname, '..')
const BASELINES = join(CLIENT, 'gate', 'baselines')
const SHOTS = join(CLIENT, 'gate', 'shots')
const AUDIT = join(CLIENT, '..', '..', 'docs', 'reports', 'WEB-FEAT-085-ui-audit.md')
/**
 * The judged corpus, COMMITTED — the evidence every row is checked against.
 *
 * QA round 4, finding 1: `gate/shots/` is gitignored, so on a fresh clone it is empty, and the
 * reproduction assertion filtered to files that exist and passed on zero comparisons. Every FIX
 * this audit filed cites a PNG by filename and none of them were in the repository; §1's stated
 * purpose — "a second party can falsify any row without re-judging it" — was unreachable by
 * anybody but the machine that wrote it. `factory/.gitignore:23-25` had already stated the
 * convention: "The run a gate was DECIDED on is copied into docs/reports/<ticket-id>/ and
 * committed." It was never applied. These checks read the committed corpus, so they mean the same
 * thing on any clone.
 */
const CORPUS = join(CLIENT, '..', '..', 'docs', 'reports', 'WEB-FEAT-085', 'corpus')

type Row = { n: number; surface: string; state: string; theme: string; coherent: boolean }

function auditRows(): Row[] {
  const rows: Row[] = []
  for (const line of readFileSync(AUDIT, 'utf8').split('\n')) {
    const m = /^\|\s*(\d+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*([^|]+?)\s*\|/.exec(line)
    if (m) rows.push({ n: +m[1], surface: m[2], state: m[3], theme: m[4], coherent: m[5] === 'coherent' })
  }
  return rows
}

const sha = (p: string) => createHash('sha256').update(readFileSync(p)).digest('hex')
const baselineFiles = () => (existsSync(BASELINES) ? readdirSync(BASELINES).filter((f) => f.endsWith('.png')) : [])
const fileFor = (r: Row) => `${r.surface}-${r.theme}.png`
const textFor = (f: string) => join(CORPUS, f.replace(/\.png$/, '.txt'))
const auditLine = (n: number) =>
  readFileSync(AUDIT, 'utf8').split('\n').find((l) => new RegExp(`^\\|\\s*${n}\\s*\\|`).test(l)) ?? ''
/** The backticked spans of one line, paired left to right — never the prose BETWEEN two of them. */
const codeSpans = (line: string) => [...line.matchAll(/`([^`]+)`/g)].map((m) => m[1])

/**
 * WEB-FIX-329 — the nav the whole corpus drifted for, read off the component that draws it.
 *
 * A literal list of twelve labels typed here would agree with whatever this file's author last
 * looked at, which is the first way a check lies. `SideNav.tsx`'s own `ITEMS` is the set.
 */
const navLabels = () =>
  [...readFileSync(join(CLIENT, 'src', 'components', 'SideNav.tsx'), 'utf8')
    .matchAll(/\{\s*label:\s*'([^']+)'/g)].map((m) => m[1])

/** The commit that last wrote `gate/baselines/`, asked of git rather than written down. */
export function lastWroteTheBaselines(): string {
  return execFileSync(
    'git',
    ['-C', CLIENT, 'log', '-1', '--format=%h %ad', '--date=short', '--', 'gate/baselines'],
    { encoding: 'utf8' },
  ).trim()
}

/**
 * WEB-FIX-329 — how a drift says what it is.
 *
 * Eight filenames on eight lines read as eight surfaces that each independently broke, and every
 * one of them is the same fact: the corpus predates the client. `SideNav.tsx` landed on 2026-09-04
 * and moved every surface right by the rail's width; the set was last written on 2026-09-02, so
 * all eight moved at once and no single ticket looks responsible. A message that cannot tell a
 * whole-corpus staleness from one surface changing is a message that answers the same way whatever
 * anybody does next, and the gate's exit code stops carrying information for every ticket after it.
 *
 * So: when EVERY baseline has drifted, one sentence about the corpus, naming the commit that last
 * wrote it and how long ago that was. When some have, the list — because then the files are the
 * fact. A corpus of ONE is the second case and not the first: there is no set for the drift to be
 * a fact about, and the only useful thing to say is which file moved.
 */
export function driftReport(drifted: string[], all: string[], lastWrite: string): string {
  if (drifted.length === 0) return ''
  if (all.length > 1 && drifted.length === all.length) {
    const [commit, date] = lastWrite.split(/\s+/)
    // Calendar days between two DATES, both taken in the same frame. `Date.now()` against a
    // midnight-UTC parse of git's local `%ad` reported the set written today as `-1 days ago`,
    // which the mandatory negative caught on its first real run.
    const now = new Date()
    const [y, m, d] = date.split('-').map(Number)
    const days = Math.round(
      (Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) - Date.UTC(y, m - 1, d))
      / 86_400_000)
    return `THE WHOLE CORPUS IS STALE — all ${all.length} baselines drifted, not one surface. `
      + `gate/baselines/ was last written in ${commit} (${date}), ${days} days ago, and the client `
      + `has moved since. Re-shoot the set and re-judge it in WEB-FEAT-085's UI audit: this is one `
      + `fact about the corpus, not ${all.length} unrelated surfaces.`
  }
  return `a later gate run produced different bytes: ${drifted.join(', ')}`
}

type Scope = { surfaces: string[]; themes: string[]; surfacesScoped: boolean; themesScoped: boolean }

/**
 * WEB-FIX-329 round 3 — which baselines the run ON DISK is allowed to be asked about.
 *
 * `gate/shots/` is one run's output. Unscoped it is the whole sweep and every baseline owes a
 * shot; scoped (`WEB-FEAT-379`) it is the few surfaces somebody asked for, and the six it did not
 * take are not six surfaces the gate failed to photograph. `scope.json`, which the sweep writes
 * beside the pictures, is the difference — and it is written precisely so that a subset by design
 * is distinguishable from a corpus with holes in it.
 *
 * `note` is never optional: a comparison of nothing must read as a comparison of nothing.
 */
export function corpusCheck(all: string[], photographed: string[], scope: Scope | null) {
  const took = new Set(photographed)
  const compare = all.filter((f) => took.has(f))
  const scoped = scope !== null && (scope.surfacesScoped || scope.themesScoped)
  const missing = scoped ? [] : all.filter((f) => !took.has(f))
  const note = scoped
    ? `  baselines · compared ${compare.length} of ${all.length} · the rest are outside this run's `
      + `scope (${scope!.surfaces.join(',') || 'none'} · ${scope!.themes.join(',') || 'none'}), `
      + `not missing from it`
    : `  baselines · compared ${compare.length} of ${all.length} files against the committed corpus`
  return { compare, missing, note, scoped }
}

/**
 * WEB-FIX-405 — why a reproduction verdict is not trustworthy, as plain sentences.
 *
 * `corpusCheck` answers WHICH files a run may be asked about. This answers the question one layer
 * up and the one this file has now been red about twice: whether the comparison that was actually
 * made says anything at all. A comparison of zero files agrees with every corpus in existence.
 */
export function nothingToCompare(compare: string[], note: string, scoped: boolean): string[] {
  if (compare.length > 0) return []
  // A run that swept everything and compared nothing is the hole: the corpus was stale for a
  // fortnight while this case reported green about it, because `gate/shots/` is gitignored and a
  // fresh checkout has none. There is nothing a whole sweep can mean by zero comparisons.
  if (!scoped) {
    return [
      `a whole gate run is on disk and not one baseline was compared against it — `
      + `"${note.trim()}". A check with nothing to compare is not a pass; it is a check that did `
      + 'not run.',
    ]
  }
  // A SCOPED run is a subset by design (`WEB-FEAT-379`), so zero comparisons is not a defect —
  // but it is not agreement either, and the one thing it may never be is silent. It is a check
  // the scope left out, and it has to say so with the number, exactly as the browser sweep's
  // `skipped(scope): n` does.
  return /\b0 of \d+\b/.test(note)
    ? []
    : [`a scoped run compared no baseline and its note does not say so: "${note.trim()}"`]
}

describe('WEB-FEAT-085 — the baseline set', () => {
  /**
   * WEB-FIX-329 extends this case with the second half of its criterion 2. Coherent is not the
   * whole of "judged": a row can be marked coherent about a photograph taken two days before the
   * side nav existed, and reads exactly like a row somebody looked at. So a baselined row must
   * quote something the RE-SHOT image put there — a nav label the component declares and this
   * surface's own captured text contains — and `ticket-session-*`, which gained `SRV-FIX-377`'s
   * owner chip in the same window, must name that too.
   */
  it('test_every_baseline_is_a_row_the_audit_judged_coherent', () => {
    const rows = auditRows()
    const coherent = new Set(rows.filter((r) => r.coherent).map(fileFor))
    const files = baselineFiles()
    const strays = files.filter((f) => !coherent.has(f))
    expect(strays, `baselined but not judged coherent: ${strays.join(', ')}`).toEqual([])

    const labels = navLabels()
    expect(labels.length, 'no nav label was derived from SideNav.tsx — the sweep is empty').toBeGreaterThan(0)
    expect(files.length, 'no baseline to judge — the sweep is empty').toBeGreaterThan(0)

    const silentAboutTheNav = files.filter((f) => {
      const row = rows.find((r) => fileFor(r) === f)
      if (!row) return false
      const quoted = codeSpans(auditLine(row.n))
      const rendered = existsSync(textFor(f)) ? readFileSync(textFor(f), 'utf8') : ''
      return !labels.some((l) => rendered.includes(l) && quoted.some((q) => q.includes(l)))
    })
    expect(
      silentAboutTheNav,
      `baselined since the side nav landed, with no row quoting a nav label their own surface `
        + `renders: ${silentAboutTheNav.join(', ')}`,
    ).toEqual([])

    const ticketSession = files.filter((f) => f.startsWith('ticket-session-'))
    expect(ticketSession.length, 'no ticket-session baseline to check the owner chip on').toBeGreaterThan(0)
    const silentAboutTheOwner = ticketSession.filter((f) => {
      const row = rows.find((r) => fileFor(r) === f)
      if (!row) return true
      const rendered = existsSync(textFor(f)) ? readFileSync(textFor(f), 'utf8') : ''
      return !codeSpans(auditLine(row.n)).some((q) => q.includes('owner') && rendered.includes(q))
    })
    expect(
      silentAboutTheOwner,
      `SRV-FIX-377 draws an owner chip on this surface and its row does not name it: `
        + `${silentAboutTheOwner.join(', ')}`,
    ).toEqual([])
  })

  /**
   * A coherent row is baselined unless the report says out loud why it cannot be, and names the
   * ticket that unblocks it. No silent caps: a surface quietly dropped from the set reads exactly
   * like a surface nobody thought about, which is how round 1 shipped five baselines that could
   * never match.
   */
  it('test_every_coherent_row_is_baselined_or_blocked_by_a_named_ticket', () => {
    const blocked = new Map<string, string>()
    for (const line of readFileSync(AUDIT, 'utf8').split('\n')) {
      const m = /^\s*-\s*`(\S+\.png)`\s*—\s*blocked by\s*`((?:FAC|SRV|WEB)-\S+?)`/.exec(line)
      if (m) blocked.set(m[1], m[2])
    }
    const unaccounted = auditRows()
      .filter((r) => r.coherent)
      .map(fileFor)
      .filter((f) => !existsSync(join(BASELINES, f)) && !blocked.has(f))
    expect(
      unaccounted,
      `judged coherent, not baselined, and not declared blocked by any ticket: ${unaccounted.join(', ')}`,
    ).toEqual([])
  })

  /** And a block must be real: a blocked file may not also be sitting in the set. */
  it('test_a_blocked_baseline_is_not_committed_anyway', () => {
    const blocked: string[] = []
    for (const line of readFileSync(AUDIT, 'utf8').split('\n')) {
      const m = /^\s*-\s*`(\S+\.png)`\s*—\s*blocked by\s*`(?:FAC|SRV|WEB)-\S+?`/.exec(line)
      if (m && existsSync(join(BASELINES, m[1]))) blocked.push(m[1])
    }
    expect(blocked, `declared blocked and committed anyway: ${blocked.join(', ')}`).toEqual([])
  })

  /**
   * QA finding 1. F9 names the lane card — `running · silent` with `no output 0m 00s`, against
   * §3.3's 120 s — and F1 names its `0/4` denominator. Two baselines draw lane cards, so the diff
   * gate would have protected both defects as approved pixels. The rule is the general one, not a
   * patch for these two files: a surface may not be baselined while an open finding names a
   * component that surface draws.
   */
  it('test_no_baseline_draws_a_component_an_open_finding_names', () => {
    const DRAWS_LANE_CARDS = ['all-lanes', 'role-lane-all-lanes', 'role-lane-rail', 'lane-rail-live']
    const offenders = baselineFiles().filter((f) =>
      DRAWS_LANE_CARDS.some((s) => f.startsWith(`${s}-`)) && !f.startsWith('all-lanes-'),
    )
    expect(
      offenders,
      `F9 and F1 name the lane card; these baselines draw one: ${offenders.join(', ')}. ` +
        `all-lanes is exempt only because its rows are the NO LANES empty block, which draws no card.`,
    ).toEqual([])
  })

  /**
   * QA finding 2, and the one that decides whether this deliverable is worth anything. A baseline
   * that cannot reproduce is not a baseline: WEB-FEAT-086 diffs against these, and a diff that is
   * red on its first execution trains everybody to stop looking.
   *
   * QA measured it by re-running the gate and diffing: `ticket-md-{dark,light}` moved 269 and 271
   * px inside `(391,202)-(434,212)`, which is the `mktemp` suffix in the rendered path line
   * (`/tmp/factory-gate-1hQXhB/…`); `role-lane-all-lanes-light` moved 39 px at `(56,161)-(62,168)`,
   * the elapsed clock on lane/01; `permissions-{dark,light}` moved 21-24 px of corner antialiasing.
   *
   * `gate/shots/` on disk is the newest run's output. Any baseline whose bytes differ from the shot
   * of the same name is a baseline the gate cannot reproduce, whatever the reason.
   */
  it('test_every_baseline_matches_the_committed_corpus_it_was_taken_from', () => {
    // No `.filter(existsSync)`. That filter is what made this pass on an empty directory for two
    // whole rounds: a baseline with nothing to compare against is the failure, not a row to skip.
    const absent = baselineFiles().filter((f) => !existsSync(join(CORPUS, f)))
    expect(absent, `baselined with no committed corpus file to check against: ${absent.join(', ')}`).toEqual([])
    const drifted = baselineFiles().filter((f) => sha(join(BASELINES, f)) !== sha(join(CORPUS, f)))
    expect(drifted, `baselines differing from the corpus they were taken from: ${drifted.join(', ')}`).toEqual([])
  })

  /**
   * The reproduction property itself, when a fresh gate run is on disk to check it against.
   *
   * WEB-FIX-329. This case passed on a clean checkout having compared NOTHING, for the whole
   * fortnight the corpus was stale. `gate/shots/` is gitignored, and `run-gate.sh` ran the client
   * suite BEFORE the browser sweep, so on a fresh clone the early return fired and seventeen green
   * cases said nothing about the one property this file exists for. It only ever went red on a
   * SECOND run in a tree still holding the first one's leftovers — which is why a two-day-old
   * whole-corpus drift was found by another ticket's QA session instead of by the gate on `main`.
   *
   * Two changes: `run-gate.sh` runs this file again after the sweep, which is the only moment a
   * fresh run exists; and when a run IS on disk, every baseline must have a shot to compare
   * against. A `.filter(existsSync)` that silently drops a row is the same defect one layer down.
   */
  it('test_a_fresh_gate_run_reproduces_the_committed_corpus', () => {
    if (!existsSync(join(SHOTS, 'index.json'))) return   // no run here; the check above still holds
    const all = baselineFiles()
    expect(all.length, 'a gate run is on disk and there was nothing to compare it against').toBeGreaterThan(0)
    const scope = existsSync(join(SHOTS, 'scope.json'))
      ? JSON.parse(readFileSync(join(SHOTS, 'scope.json'), 'utf8')) as Scope
      : null
    const { compare, missing, note } = corpusCheck(all, all.filter((f) => existsSync(join(SHOTS, f))), scope)
    expect(
      missing,
      `a gate run is on disk and did not photograph these baselined surfaces: ${missing.join(', ')}`,
    ).toEqual([])

    // WEB-FIX-405 A-NEG — a run on disk that compared NOTHING is refused here, through the same
    // predicate the negative below drives. Zero comparisons read as agreement is the defect this
    // whole file has now been red about twice.
    const wasScoped = scope !== null && (scope.surfacesScoped || scope.themesScoped)
    expect(nothingToCompare(compare, note, wasScoped), 'the reproduction check compared nothing')
      .toEqual([])
    if (compare.length === 0) {
      // Printed and counted, never passed: this run photographed no baselined surface, so it has
      // said nothing about the corpus and a reader has to be told that rather than shown a tick.
      console.log(`${note} · SKIP(scope): no baselined surface in this run’s scope`)
      return
    }

    const drifted = compare.filter((f) => sha(join(SHOTS, f)) !== sha(join(CORPUS, f)))
    console.log(`${note} · ${drifted.length} drifted`)
    // QA round 2's honest note, closed. `toEqual([])` prints vitest's own `Received Array [ …8
    // filenames… ]` UNDER the sentence, so a whole-corpus staleness still ended in eight rows —
    // the shape §1's criterion 3 exists to stop, one layer below the message. The value asserted
    // is the report itself, which is `''` exactly when nothing drifted, so the diff block carries
    // the sentence and nothing else.
    expect(driftReport(drifted, compare, lastWroteTheBaselines()), 'the corpus did not reproduce')
      .toEqual('')
  })

  /**
   * WEB-FIX-405 §1 cluster A2. The reproduction case is about the run that JUST happened, and
   * `run-gate.sh` decides whether one exists when it runs the case.
   *
   * `gate/shots/` is gitignored. The gate ran the whole client suite BEFORE the build, so on a
   * fresh checkout the case above took its early return and passed having compared nothing, and on
   * a SECOND run in the same tree it compared the FIRST run's leftovers — a verdict about what ran
   * before rather than about this run. `WEB-FIX-362` is *the gate answers differently on the same
   * tree depending on run order* and `WEB-FIX-374` is the same sentence from the corpus's side.
   *
   * Two properties, both read off the script that owns them:
   *   · the phase that runs before the build does not run this file at all;
   *   · the phase that runs after the sweep does.
   * And one about the pixels: the fixture the sweep photographs lives at a path that is the same
   * on every run of a checkout, because a `mktemp` suffix is rendered on `ticket-md` and
   * `report-md` (`docs/reports/WEB-FEAT-085/corpus/ticket-md-dark.txt:43`) and a path in the
   * pixels is a run that can never reproduce.
   */
  it('test_the_reproduction_case_does_not_depend_on_what_ran_before', () => {
    const script = readFileSync(join(CLIENT, 'gate', 'run-gate.sh'), 'utf8')

    const before = script.split('phase_begin vitest')[1]?.split('phase_end')[0] ?? ''
    expect(before, 'run-gate.sh no longer has a vitest phase').not.toEqual('')
    expect(before, 'the pre-build suite still runs the corpus comparison, so the gate’s verdict '
      + 'depends on what the previous run left in gate/shots/').toMatch(/baselines\.test\.ts/)
    expect(before, 'and it runs it rather than excluding it').toMatch(/--exclude/)

    const after = script.split('phase_begin baselines')[1]?.split('phase_end')[0] ?? ''
    expect(after, 'nothing runs the corpus comparison after the sweep, which is the only moment a '
      + 'fresh run exists to compare against').toMatch(/baselines\.test\.ts/)

    const work = /^WORK=.*$/m.exec(script)?.[0] ?? ''
    expect(work, 'run-gate.sh never sets WORK').not.toEqual('')
    expect(work, `the gate’s work directory carries a per-run suffix — ${work.trim()} — and that `
      + 'suffix is rendered into the pixels, so no two runs can ever produce the same bytes')
      .not.toMatch(/mktemp/)
  })

  /**
   * MANDATORY NEGATIVE for the case above (§1 cluster A-NEG). A reproduction check with nothing to
   * compare is RED, not a pass.
   *
   * Driven through the same predicate the real case uses, on fabricated inputs, because the real
   * corpus is green — and a check that can only run while the product is broken is a check nobody
   * runs. `WEB-FEAT-085` QA round 4 and `WEB-FIX-329` are the two rounds this exact hole shipped
   * through, one `.filter(existsSync)` apart.
   */
  it('test_a_reproduction_check_with_nothing_to_compare_is_not_a_pass', () => {
    const four = ['a-dark.png', 'a-light.png', 'b-dark.png', 'b-light.png']

    const whole = corpusCheck(four, [], null)
    expect(whole.missing, 'a whole sweep that photographed nothing reported no missing surface')
      .toEqual(four)
    expect(nothingToCompare(whole.compare, whole.note, false),
      'a WHOLE sweep’s zero comparisons passed as agreement').not.toEqual([])
    expect(nothingToCompare(whole.compare, whole.note, false).join(' ')).toMatch(/did not run/)

    // A SCOPED run is a subset by design, so zero comparisons is not a defect — and it is not
    // agreement either. The one thing it may never be is silent: the note carries the number.
    const scoped = corpusCheck(four, [],
      { surfaces: ['z'], themes: ['dark'], surfacesScoped: true, themesScoped: true })
    expect(scoped.missing, 'a scope owes nothing it was not asked for').toEqual([])
    expect(scoped.note, 'a scoped run that compared none must say so, with the number')
      .toMatch(/0 of 4/)
    expect(nothingToCompare(scoped.compare, scoped.note, true),
      'a scoped subset was reported as a defect').toEqual([])
    expect(nothingToCompare([], 'nothing to say', true),
      'a scoped run compared none, said nothing about it, and was accepted').not.toEqual([])

    // …and the predicate is not simply always red: one real comparison is a comparison.
    const one = corpusCheck(four, ['a-dark.png'],
      { surfaces: ['a'], themes: ['dark'], surfacesScoped: true, themesScoped: true })
    expect(nothingToCompare(one.compare, one.note, true),
      'a run that DID compare a baseline was refused').toEqual([])
  })

  /**
   * WEB-FIX-329 round 3, transcribed from a false red this ticket's own change produced.
   *
   * `WEB-FEAT-379` gave the gate a SCOPE, and `GATE`'s tier 2b is now a scoped run — a few
   * surfaces, one theme, four minutes instead of twelve. The scope reaches the browser sweep and
   * never the vitest stage, which is always the whole suite, so this case runs against a
   * `gate/shots/` holding two surfaces and reported the other six as surfaces the gate had failed
   * to photograph:
   *
   *   AssertionError: a gate run is on disk and did not photograph these baselined surfaces:
   *   all-lanes-dark.png, all-lanes-light.png, diff-dark.png, … (8)
   *
   * That is a gate saying no about something that is fine, one mechanism from a gate saying yes
   * about something that is not. `gate/shots/scope.json` is what the sweep already writes to tell
   * the two apart — `surfacesScoped` / `themesScoped` — so a subset BY DESIGN is distinguishable
   * from a corpus with holes in it, which is the sentence that file was added for.
   *
   * The rule: a whole sweep owes a shot for every baseline; a scoped run owes one for every
   * baseline IT PHOTOGRAPHED, and says out loud how many it left out. Never silently, and never
   * zero comparisons reported as agreement.
   */
  it('test_a_scoped_run_compares_what_it_photographed_and_says_what_it_did_not', () => {
    const eight = ['a-dark.png', 'a-light.png', 'b-dark.png', 'b-light.png']

    const whole = corpusCheck(eight, ['a-dark.png', 'a-light.png'], null)
    expect(whole.missing, 'a WHOLE sweep that skipped a baselined surface must still say so')
      .toEqual(['b-dark.png', 'b-light.png'])

    const scoped = corpusCheck(eight, ['a-dark.png'],
      { surfaces: ['a'], themes: ['dark'], surfacesScoped: true, themesScoped: true })
    expect(scoped.missing, 'a scoped run did not fail to photograph what it was not asked for')
      .toEqual([])
    expect(scoped.compare, 'a scoped run still compares the baselines it DID take')
      .toEqual(['a-dark.png'])
    expect(scoped.note, 'a scoped run must say how much of the corpus it could not speak for')
      .toMatch(/scope/)
    expect(scoped.note, 'and how many that was').toMatch(/1 of 4/)

    const nothing = corpusCheck(eight, [], { surfaces: ['z'], themes: ['dark'], surfacesScoped: true, themesScoped: true })
    expect(nothing.compare, 'a scope that photographed no baseline compares none').toEqual([])
    expect(nothing.note, 'and a comparison of nothing may never read as agreement')
      .toMatch(/0 of 4/)
  })

  /**
   * MANDATORY NEGATIVE (WEB-FIX-329 §1 criterion 3). A whole-corpus staleness and a single surface
   * changing are different events and the gate has to say which one it saw. Driven with a
   * fabricated drift set, because the real corpus is green and a check that can only run when the
   * product is broken is a check nobody runs.
   */
  it('test_a_wholly_stale_corpus_names_itself_as_stale', () => {
    const all = baselineFiles()
    expect(all.length, 'no baselines to drift — the sweep is empty').toBeGreaterThan(0)
    const lastWrite = lastWroteTheBaselines()
    expect(lastWrite, 'git was never asked which commit last wrote gate/baselines/')
      .toMatch(/^[0-9a-f]{7,40} \d{4}-\d{2}-\d{2}$/)
    const [commit] = lastWrite.split(' ')

    const whole = driftReport(all, all, lastWrite)
    expect(whole, 'a whole-corpus drift that does not say it is one').toMatch(/WHOLE CORPUS IS STALE/)
    expect(whole, 'the staleness names no commit').toContain(commit)
    expect(whole, 'the staleness does not say how long it has been stale').toMatch(/\d+ days ago/)
    const listed = all.filter((f) => whole.includes(f))
    expect(
      listed,
      `a corpus-wide staleness reported as ${all.length} unrelated rows: ${listed.join(', ')}`,
    ).toEqual([])

    const one = driftReport([all[0]], all, lastWrite)
    expect(one, 'one surface drifting must still name that surface').toContain(all[0])
    expect(one, 'one surface drifting is not evidence about the corpus').not.toMatch(/WHOLE CORPUS IS STALE/)
    expect(one, 'one surface drifting says nothing about when the set was written').not.toContain(commit)

    // QA round 2, M1 — transcribed from its reproduction, `driftReport(['a.png'], ['a.png'], …)`,
    // not from its sentence. A corpus of ONE is the case where every reduction looks like the
    // identity: `all` drifted and `one surface` drifted are the same set, so the branch that
    // decides between them cannot be exercised by the real eight-file corpus at all. A corpus of
    // one that drifts is one surface changing, and a person needs the filename, not a sentence
    // about a set with nothing to compare inside it.
    const alone = driftReport(['a.png'], ['a.png'], 'abc1234 2026-09-02')
    expect(alone, 'a corpus holding one file drifting is one surface, not a corpus-wide staleness')
      .not.toMatch(/WHOLE CORPUS IS STALE/)
    expect(alone, 'a corpus of one drifted and the message names no file').toContain('a.png')
    expect(alone, 'a corpus of one says nothing about when the set was written').not.toContain('abc1234')
  })

  /**
   * QA round 4, finding 1, transcribed from its reproduction rather than its name. Every FIX this
   * audit filed cites a PNG by filename; a second party must find them in the repository.
   */
  it('test_the_judged_corpus_is_committed_beside_the_report', () => {
    const rows = auditRows()
    const missing = rows.flatMap((r) => [`${r.surface}-${r.theme}.png`, `${r.surface}-${r.theme}.txt`])
      .filter((f) => !existsSync(join(CORPUS, f)))
    expect([...new Set(missing)],
      `cited as evidence and absent from the repository: ${[...new Set(missing)].slice(0, 6).join(', ')}`,
    ).toEqual([])
  })

  /** Negative (mandatory). The cheap fix for all of the above is an empty directory. */
  it('test_the_baseline_set_is_not_empty', () => {
    expect(baselineFiles().length).toBeGreaterThan(0)
  })

  /** Negative (mandatory). And the second cheap fix is to call every row a defect. */
  it('test_the_audit_still_judged_some_row_coherent', () => {
    expect(auditRows().filter((r) => r.coherent).length).toBeGreaterThan(0)
  })
})

/**
 * WEB-FEAT-085 round 3 — §1's rewritten criteria.
 *
 * The three blind analyses agreed that the original four criteria were relations among artefacts
 * this session authors, so all four could pass on a table describing an audit that never happened.
 * The question round 1 should have asked: "which of these goes red if no PNG is ever opened?" —
 * none. These are the replacement, and they are ONE convention asked at every row rather than a
 * fifth check beside the last four.
 *
 * The convention: a row must quote, in backticks, a string its own surface actually rendered and
 * that no other surface rendered. The gate persists each surface's rendered text beside its PNG so
 * the quote is checkable by a second party without re-judging the image.
 */
/**
 * The row-quote checker, as a FUNCTION OF THE TABLE'S TEXT rather than of a file on disk.
 *
 * QA round 3, finding 1: the mandatory negative read one `.txt` and asserted an invented English
 * sentence was absent from it. It built no table, constructed no row, and never called this logic —
 * so it could only ever go red if a future gate run made some surface render that exact sentence.
 * §1 had said "written by inserting one into a fixture copy of the table, never by trusting that
 * the real table is honest", and the test did the thing §1 forbade.
 *
 * Taking the table as a string is the whole fix: a fabricated row can now be spliced in and the
 * checker asked about it.
 */
export function rowsRejectedBy(tableText: string, shotsDir: string): string[] {
  const rows: Row[] = []
  for (const line of tableText.split('\n')) {
    const m = /^\|\s*(\d+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*([^|]+?)\s*\|/.exec(line)
    if (m) rows.push({ n: +m[1], surface: m[2], state: m[3], theme: m[4], coherent: m[5] === 'coherent' })
  }
  const lineOf = (n: number) =>
    tableText.split('\n').find((l) => new RegExp(`^\\|\\s*${n}\\s*\\|`).test(l)) ?? ''
  // The row's EVIDENCE is the string it marks as read off the surface — not every backtick in it.
  // QA round 3 finding 2 exposed why: rows 9 and 19 backtick `not_started` and `unknown` because
  // their FINDING is that the vocabulary sheet never renders them. Naming an absent value and
  // quoting a present one are opposite acts, and a rule that cannot tell them apart either forbids
  // the finding or excuses fabrication. An explicit marker separates them, and §1 now requires one
  // on every row rather than leaving it to whichever backtick a reader picks.
  const quotes = (line: string) => {
    const m = /Read off this surface: `([^`]+)`\./.exec(line)
    return m ? [m[1]] : []
  }

  const text = new Map<string, string>()
  for (const r of rows) {
    const f = join(shotsDir, `${r.surface}-${r.theme}.txt`)
    if (existsSync(f)) text.set(r.surface, (text.get(r.surface) ?? '') + readFileSync(f, 'utf8'))
  }
  const surfaces = text.size
  const rejected: string[] = []
  for (const r of rows) {
    const mine = text.get(r.surface) ?? ''
    const identifying = quotes(lineOf(r.n)).filter((q) =>
      mine.includes(q)
      && [...text.values()].filter((v) => v.includes(q)).length <= Math.floor(surfaces / 2))
    if (identifying.length === 0) rejected.push(`row ${r.n} (${r.surface}/${r.theme})`)
  }
  return rejected
}

describe('WEB-FEAT-085 — a row is an observation', () => {
  const table = () => readFileSync(AUDIT, 'utf8')
  const rowLine = (r: Row) =>
    table().split('\n').find((l) => new RegExp(`^\\|\\s*${r.n}\\s*\\|`).test(l)) ?? ''
  const txtFor = (surface: string, theme: string) => join(CORPUS, `${surface}-${theme}.txt`)

  it('test_the_gate_captured_the_text_of_every_surface_it_photographed', () => {
    const missing = auditRows()
      .filter((r) => !existsSync(txtFor(r.surface, r.theme)))
      .map((r) => `${r.surface}-${r.theme}.txt`)
    expect([...new Set(missing)], `photographed with no text captured: ${missing.join(', ')}`).toEqual([])
  })

  /** §1's criterion 1c, which round 3 never asserted (QA round 3). */
  it('test_no_orphan_of_either_kind_in_the_shots_directory', () => {
    const index = JSON.parse(readFileSync(join(CORPUS, 'index.json'), 'utf8')) as { file: string; text: string }[]
    const named = new Set(index.flatMap((e) => [e.file, e.text]))
    const onDisk = readdirSync(CORPUS).filter((f) => f.endsWith('.png') || f.endsWith('.txt'))
    const orphanFiles = onDisk.filter((f) => !named.has(f))
    const orphanRows = [...named].filter((f) => !existsSync(join(CORPUS, f)))
    expect(orphanFiles, `on disk, named by no index row: ${orphanFiles.join(', ')}`).toEqual([])
    expect(orphanRows, `named by the index, not on disk: ${orphanRows.join(', ')}`).toEqual([])
  })

  it('test_every_row_quotes_a_string_only_its_own_surface_rendered', () => {
    const bad = rowsRejectedBy(table(), CORPUS)
    expect(bad, `rows quoting nothing their own surface identifies: ${bad.join(', ')}`).toEqual([])
  })

  /**
   * Negative (mandatory), rewritten after QA round 3 rejected the previous one for being unable to
   * fail. Splices a fabricated row into a COPY of the real table and asserts the checker names it.
   * Transcribed from the probe in QA's finding 1, whose raw output was:
   *   REAL TABLE        : 46 rows parsed · 0 rejected
   *   FABRICATED ROW 47 : 47 rows parsed · 1 rejected → row 47 (vocabulary/dark)
   */
  it('test_a_fabricated_row_goes_red', () => {
    const lines = table().split('\n')
    const last = lines.map((l, i) => (/^\|\s*\d+\s*\|/.test(l) ? i : -1)).filter((i) => i >= 0).pop()!
    const fixture = [...lines]
    fixture.splice(last + 1, 0,
      '| 47 | vocabulary | static | dark | coherent | reads clearly. '
      + 'Read off this surface: `a sentence no surface has ever rendered`. |')
    expect(rowsRejectedBy(fixture.join('\n'), CORPUS)).toContain('row 47 (vocabulary/dark)')
    expect(rowsRejectedBy(table(), CORPUS), 'and the real table still passes').toEqual([])
  })

  /**
   * Negative (mandatory). §1's rule, per quote and not per row.
   *
   * QA round 3, finding 2: when this went red on rows 3, 9 and 19, I narrowed it to `every()` so a
   * row could pass on one good quote plus lifted junk. That is the failure mode the workflow names
   * outright — never re-word an assertion to make it pass. Restored: NO quote may be a SPEC string
   * absent from its own surface.
   */
  it('test_a_quote_copied_from_the_spec_is_not_evidence', () => {
    const spec = readFileSync(join(CLIENT, '..', '..', 'docs', 'product/SPEC.md'), 'utf8')
    const lifted = auditRows().flatMap((r) => {
      const mine = readFileSync(txtFor(r.surface, r.theme), 'utf8')
      const marked = /Read off this surface: `([^`]+)`\./.exec(rowLine(r))
      return (marked ? [marked[1]] : [])
        .filter((q) => spec.includes(q) && !mine.includes(q))
        .map((q) => `row ${r.n}: \`${q}\` is SPEC vocabulary its own surface never rendered`)
    })
    expect(lifted, lifted.join('; ')).toEqual([])
  })

  it('test_every_defect_row_names_a_finding_or_a_ticket', () => {
    const unnamed = auditRows().filter((r) => !r.coherent)
      .filter((r) => !/\(F\d+\)|F\d+\b|(?:FAC|SRV|WEB)-FIX-\d+/.test(rowLine(r)))
      .map((r) => r.n)
    expect(unnamed, `defect rows naming no finding: ${unnamed.join(', ')}`).toEqual([])
  })

  it('test_every_number_the_report_states_equals_what_its_table_holds', () => {
    const doc = table()
    const rows = auditRows()
    const index = JSON.parse(readFileSync(join(CORPUS, 'index.json'), 'utf8'))
    const split = /(\d+)\s+rows judged `defect`,\s*(\d+)\s+`coherent`/.exec(doc)
    expect(split, 'the report states no verdict split at all').not.toBeNull()
    expect([+split![1], +split![2]], 'stated split vs the table').toEqual(
      [rows.filter((r) => !r.coherent).length, rows.filter((r) => r.coherent).length])
    const counts = /rows: \*\*(\d+)\*\*\. Rows judged: \*\*(\d+)\*\*/.exec(doc)
    expect(counts, 'the report states no row count').not.toBeNull()
    expect([+counts![1], +counts![2]], 'stated counts vs index.json and the table').toEqual([index.length, rows.length])
  })
})

describe('WEB-FEAT-085 — every row marks its evidence', () => {
  /** §1, rewritten in round 4: the marker is mandatory, so no reader has to guess which backtick
   *  in a sentence was meant as evidence. */
  it('test_every_row_marks_the_string_it_read_off_the_surface', () => {
    const doc = readFileSync(AUDIT, 'utf8')
    const unmarked = auditRows().filter((r) => {
      const line = doc.split('\n').find((l) => new RegExp(`^\\|\\s*${r.n}\\s*\\|`).test(l)) ?? ''
      return !/Read off this surface: `[^`]+`\./.test(line)
    }).map((r) => r.n)
    expect(unmarked, `rows carrying no evidence marker: ${unmarked.join(', ')}`).toEqual([])
  })
})

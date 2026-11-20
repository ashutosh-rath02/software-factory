import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
// The gate's OWN resolver and its OWN tables, imported — not a copy of either, and not a regex
// over their source. `gate-surfaces.test.ts` is the pattern and its reason holds here: a test that
// re-types the set it is checking agrees with itself.
import { LIVE_SURFACES, SURFACES, THEMES, resolveScope } from '../gate/surfaces.mjs'

/**
 * WEB-FEAT-379 — the browser gate takes a scope.
 *
 * `GATE` is the largest stage of an implementing run (`FAC-EPIC-374` §1: median 22.1 minutes) and
 * one browser gate is ten to twelve of them, because there was no way to run less than everything:
 * `run-gate.sh` took a screenshot directory and nothing else, and the sweep walked all 21 rows of
 * `SURFACES` in both themes unconditionally.
 *
 * The hazard this file exists to keep visible is in `docs/operations/ESCAPES.md`, 2026-07-29: five client
 * lanes were told not to run the browser gate so five headless Chromes would not make each other
 * flaky, all five merged on a green vitest run, and four gate assertions were merged having never
 * once executed. **A way to run less than everything is that instruction with a nicer interface**
 * unless a check that did not run is visibly a check that did not run — which is what the ledger
 * on `Scope` is for, and why `skipped(scope): n` is asserted rather than described.
 */

const CLIENT = join(__dirname, '..')
const GATE = join(CLIENT, 'gate')
const REPO = join(CLIENT, '..', '..')

/** Every name a scope may select, derived from the gate's own tables. */
const DECLARED: string[] = [...SURFACES, ...LIVE_SURFACES].map(([name]) => String(name))

describe('WEB-FEAT-379 — a scope is resolved from the argument or the environment', () => {
  /**
   * §1 criterion 1. `npm run gate -- --surfaces tickets,ticket-md --themes dark` is two surfaces
   * in one theme and nothing else, and the run says so in a line a report can quote.
   *
   * Both spellings are driven and compared to each other, because §1 pre-decided they are
   * equivalent: a resolver that read only its argv would pass every assertion written about the
   * flags and leave `FACTORY_GATE_SURFACES` meaning nothing.
   */
  it('a_scope_resolves_to_exactly_the_named_surfaces_and_themes', () => {
    // The derivation says how much it swept. A `surfaces.mjs` whose tables stopped being read
    // would make every membership assertion below vacuously true.
    expect(DECLARED.length, `only ${DECLARED.length} surface names read out of surfaces.mjs`)
      .toBeGreaterThanOrEqual(20)
    expect(THEMES).toEqual(['dark', 'light'])

    const fromArgv = resolveScope({
      argv: ['node', 'browser-gate.mjs', 'http://127.0.0.1:1', 'shots', '/fixture',
             '--surfaces', 'tickets,ticket-md', '--themes', 'dark'],
    })
    expect(fromArgv.surfaces).toEqual(['tickets', 'ticket-md'])
    expect(fromArgv.themes).toEqual(['dark'])
    expect(fromArgv.surfacesScoped).toBe(true)
    expect(fromArgv.themesScoped).toBe(true)

    // …and the membership question the gate actually asks, on a name in the scope, a name in
    // `SURFACES` that is not, and a name in `LIVE_SURFACES` that is not. One in-scope answer alone
    // is satisfied by a predicate that returns true for everything.
    expect(fromArgv.inScope('tickets')).toBe(true)
    expect(fromArgv.inScope('ticket-md')).toBe(true)
    expect(fromArgv.inScope('runs')).toBe(false)
    expect(fromArgv.inScope('ticket-live')).toBe(false)

    // The line §1 quotes. Its two numbers are DERIVED from the same tables the resolver validates
    // against — a count written beside a collection is asserted with that collection, never
    // against the literal somebody typed when the line was added (WEB-FEAT-016 / WEB-FIX-091).
    expect(fromArgv.line()).toBe(
      `scope: 2 surfaces × 1 theme (of ${DECLARED.length} × ${THEMES.length})`
      + ' — tickets · ticket-md · dark')

    // The environment spelling is the same scope, asserted as an object rather than field by
    // field so a third field appearing on one path and not the other is red.
    const fromEnv = resolveScope({
      env: { FACTORY_GATE_SURFACES: 'tickets,ticket-md', FACTORY_GATE_THEMES: 'dark' },
    })
    expect(fromEnv.surfaces).toEqual(fromArgv.surfaces)
    expect(fromEnv.themes).toEqual(fromArgv.themes)
    expect(fromEnv.line()).toBe(fromArgv.line())

    // Whitespace and repetition are a person typing, not a different scope.
    expect(resolveScope({ argv: ['--surfaces', ' tickets , ticket-md ,tickets'] }).surfaces)
      .toEqual(['tickets', 'ticket-md'])
    // …and the order is the DECLARED order, not the order they were typed, so the sweep is
    // deterministic however the flag was written.
    expect(resolveScope({ argv: ['--surfaces', 'ticket-md,tickets'] }).surfaces)
      .toEqual(['tickets', 'ticket-md'])
    expect(resolveScope({ argv: ['--themes', 'light,dark'] }).themes).toEqual(['dark', 'light'])
  })

  /**
   * §1's pre-decision: *arguments win over the environment*. Asserted where the two DISAGREE and
   * per axis, because a fixture in which they hold the same value proves only that the resolver
   * read one of them and passes identically whichever one that is (`_TEMPLATE.md` §2,
   * `SRV-FEAT-065`).
   */
  it('an_argument_wins_over_the_environment_on_its_own_axis', () => {
    const both = resolveScope({
      argv: ['--surfaces', 'tickets', '--themes', 'light'],
      env: { FACTORY_GATE_SURFACES: 'runs', FACTORY_GATE_THEMES: 'dark' },
    })
    expect(both.surfaces).toEqual(['tickets'])
    expect(both.themes).toEqual(['light'])

    // …and one axis given on the command line does not silence the other axis's variable. The
    // two axes are independent, so the case that would pass on a resolver taking argv-or-env
    // wholesale is the one that has to be written.
    const mixed = resolveScope({
      argv: ['--surfaces', 'tickets'],
      env: { FACTORY_GATE_SURFACES: 'runs', FACTORY_GATE_THEMES: 'light' },
    })
    expect(mixed.surfaces).toEqual(['tickets'])
    expect(mixed.themes).toEqual(['light'])
    expect(mixed.surfacesScoped).toBe(true)
    expect(mixed.themesScoped).toBe(true)
  })

  /**
   * §1 criterion 2. No scope means the whole declared set — every row of `SURFACES` and
   * `LIVE_SURFACES`, both themes — and `inScope` answers **true to everything**, including the
   * surfaces the gate photographs that are in neither table (`session-second-run` and its seven
   * neighbours). That last clause is the whole of "unchanged": a resolver that answered `false`
   * for a name it has never heard of would silently stop photographing eight surfaces on a run
   * nobody scoped.
   */
  it('no_scope_is_the_whole_declared_set', () => {
    const all = resolveScope({})
    expect(all.surfaces).toEqual(DECLARED)
    expect(all.themes).toEqual(THEMES)
    expect(all.surfacesScoped).toBe(false)
    expect(all.themesScoped).toBe(false)
    expect(all.declared.surfaces).toEqual(DECLARED)
    expect(all.declared.themes).toEqual(THEMES)

    const outside = ['session-second-run', 'requirements-filtered-done', 'role-lane-rail']
    for (const name of [...DECLARED, ...outside]) {
      expect(all.inScope(name), `${name} is out of scope on a run nobody scoped`).toBe(true)
    }
    expect(all.skipped).toEqual([])
    expect(all.skipLine()).toBe('skipped(scope): 0')

    // An empty environment and an absent one are the same run.
    expect(resolveScope({ env: { FACTORY_GATE_SURFACES: '', FACTORY_GATE_THEMES: '' } }).surfaces)
      .toEqual(DECLARED)

    // …and the surfaces the resolver hands back really are the gate's tables, in their order.
    expect(all.surfaces.slice(0, SURFACES.length))
      .toEqual(SURFACES.map(([name]) => String(name)))
    expect(all.surfaces.slice(SURFACES.length))
      .toEqual(LIVE_SURFACES.map(([name]) => String(name)))
  })

  /**
   * §1 criterion 3. A check that needs a surface outside the scope is SKIPPED and COUNTED, never
   * passed and never silently absent — `docs/operations/ESCAPES.md`, 2026-07-29, is what a gate assertion
   * that did not run is worth.
   *
   * The ledger is exercised with more than one member and members that do not agree: a
   * one-element fixture makes a count indistinguishable from `first`, from `any` and from the
   * identity (`_TEMPLATE.md` §2, `SRV-FEAT-165`).
   */
  it('a_check_outside_the_scope_is_skipped_and_counted', () => {
    const scope = resolveScope({ argv: ['--surfaces', 'tickets', '--themes', 'dark'] })
    expect(scope.skipLine()).toBe('skipped(scope): 0')

    expect(scope.inScope('runs')).toBe(false)
    scope.skip('the runs tab is photographed', 'runs')
    expect(scope.skipLine()).toBe('skipped(scope): 1')

    expect(scope.inScope('session-second-run')).toBe(false)
    scope.skip('a run that started while the tab was away', 'session-second-run')
    scope.skip('the live sweep (10)', 'ticket-live')

    // The count and the collection it is drawn over, in one assertion.
    expect(scope.skipped).toEqual([
      { name: 'the runs tab is photographed', surface: 'runs' },
      { name: 'a run that started while the tab was away', surface: 'session-second-run' },
      { name: 'the live sweep (10)', surface: 'ticket-live' },
    ])
    expect(scope.skipLine()).toBe(`skipped(scope): ${scope.skipped.length}`)
    expect(scope.skipLine()).toBe('skipped(scope): 3')

    // …and a skip is not a failure. The gate's exit status is decided by the checks it ran, so
    // the ledger carries no verdict of its own — this is the assertion that would go red if a
    // later edit made a scope skip red, which is the shape that turns a scoped gate into a gate
    // nobody can use.
    expect(Object.keys(scope.skipped[0]).sort()).toEqual(['name', 'surface'])
  })

  /**
   * MANDATORY NEGATIVE (§1 criterion 6). An unknown surface or theme is refused BY NAME, with the
   * declared names listed, before anything is photographed.
   *
   * Driven with §1's own example command, `--surfaces tickets,ticket`, because there is no surface
   * called `ticket` — the declared names near it are `ticket-session`, `ticket-md` and
   * `ticket-live`. A resolver that accepted a name it did not know would sweep nothing under it
   * and the run would pass having photographed one surface instead of two, which is
   * `WEB-FIX-337`'s silence with a flag in front of it.
   */
  it('an_unknown_surface_or_theme_is_refused_by_name', () => {
    expect(DECLARED).not.toContain('ticket')

    const badSurface = () => resolveScope({ argv: ['--surfaces', 'tickets,ticket'] })
    expect(badSurface).toThrow(/unknown surface "ticket"/)
    // …and the message lists what a person may say instead. Every declared name, not a sample:
    // a refusal that names three of twenty-six is a refusal somebody has to read the source to act
    // on.
    let said = ''
    try { badSurface() } catch (e) { said = (e as Error).message }
    for (const name of DECLARED) expect(said, `${name} is missing from the refusal`).toContain(name)

    expect(() => resolveScope({ argv: ['--surfaces', 'nosuch'] }))
      .toThrow(/unknown surface "nosuch"/)
    expect(() => resolveScope({ argv: ['--themes', 'blue'] }))
      .toThrow(/unknown theme "blue" — declared: dark · light/)

    // The environment spelling refuses identically — a resolver that validated only its flags
    // would let `FACTORY_GATE_SURFACES=nosuch` through into a run that photographs nothing.
    expect(() => resolveScope({ env: { FACTORY_GATE_SURFACES: 'nosuch' } }))
      .toThrow(/unknown surface "nosuch"/)
    expect(() => resolveScope({ env: { FACTORY_GATE_THEMES: 'blue' } }))
      .toThrow(/unknown theme "blue"/)

    // A flag with nothing after it is a scope of NOTHING, which is a browser gate that
    // photographs nothing and exits 0. Refused for the same reason an unknown name is.
    expect(() => resolveScope({ argv: ['--surfaces', ''] })).toThrow(/--surfaces named no surface/)
    expect(() => resolveScope({ argv: ['--themes', ' , '] })).toThrow(/--themes named no theme/)
    expect(() => resolveScope({ argv: ['--surfaces'] })).toThrow(/--surfaces named no surface/)

    // …and the refusal is about names it does not know, not about every name: the scopes above
    // and the whole declared set still resolve. Without this the case passes on a resolver that
    // throws unconditionally.
    expect(resolveScope({ argv: ['--surfaces', DECLARED.join(',')] }).surfaces).toEqual(DECLARED)
    expect(resolveScope({ argv: ['--themes', 'dark,light'] }).themes).toEqual(THEMES)
  })
})

/**
 * The structural half. §1 pre-decided *"the nine theme literals in `browser-gate.mjs` become reads
 * of the resolved scope — one source, so a tenth literal cannot be added without going through
 * it."* That sentence is only a fact if the count is ZERO and something checks it; at nine it was
 * a convention, and a convention is what let the pair be written in nine places.
 */

/** Every `['dark', 'light']` written as an array literal in a source file, however spaced. */
export function themePairLiterals(source: string): string[] {
  return (source.match(/\[\s*(['"])(?:dark|light)\1\s*,\s*(['"])(?:dark|light)\2\s*\]/g) ?? [])
}

describe('WEB-FEAT-379 — the theme pair has one source', () => {
  it('the_gate_reads_its_themes_from_the_resolved_scope_and_not_from_a_literal', () => {
    const gate = readFileSync(join(GATE, 'browser-gate.mjs'), 'utf8')

    // The matcher says it can see one, against a string that is not the file under test. A regex
    // that had stopped matching would report zero literals in a file full of them and turn this
    // case green on exactly the defect it exists to refuse.
    expect(themePairLiterals("for (const theme of ['dark', 'light']) {")).toHaveLength(1)
    expect(themePairLiterals('const t = ["light","dark"]')).toHaveLength(1)
    expect(themePairLiterals("const t = ['dark']")).toEqual([])

    expect(themePairLiterals(gate),
      'browser-gate.mjs still writes the theme pair as a literal').toEqual([])

    // …and it reads the scope instead, in at least as many places as it used to hold literals.
    // Nine is the count §1 measured; a file that had simply deleted the loops would satisfy the
    // assertion above and fail this one.
    const reads = gate.match(/SCOPE\.themes/g) ?? []
    expect(reads.length, `browser-gate.mjs reads SCOPE.themes ${reads.length} times`)
      .toBeGreaterThanOrEqual(9)
  })
})

/**
 * §1 criterion 7 — the workflow says which gate is whose. §1 names `pytest tests/ -m repository`
 * as the evidence and this is the cheap half of it that runs in the client's own suite: the three
 * documents that tell a session what to run must name the arguments, and must put the scoped gate
 * at GATE and the unscoped one at VERIFY.
 */
describe('WEB-FEAT-379 — the workflow says which gate is whose', () => {
  it('the_documents_name_the_scoped_gate_at_GATE_and_the_whole_sweep_at_VERIFY', () => {
    const files = {
      'docs/technical/workflow/WORKFLOW.md': readFileSync(join(REPO, 'docs', 'technical/workflow/WORKFLOW.md'), 'utf8'),
      'docs/tickets/_TEMPLATE.md': readFileSync(
        join(REPO, 'docs', 'tickets', '_TEMPLATE.md'), 'utf8'),
      'client/web/gate/run-gate.sh': readFileSync(join(GATE, 'run-gate.sh'), 'utf8'),
    }
    for (const [name, text] of Object.entries(files)) {
      expect(text, `${name} does not show --surfaces`).toContain('--surfaces')
      expect(text, `${name} does not show --themes`).toContain('--themes')
    }
    // The two documents that assign a gate to a stage say which is whose, in one sentence each
    // that names the stage beside the invocation.
    for (const name of ['docs/technical/workflow/WORKFLOW.md', 'docs/tickets/_TEMPLATE.md'] as const) {
      const text = files[name]
      expect(text, `${name} does not put the scoped gate at tier 2b`)
        .toMatch(/tier 2b[\s\S]{0,600}--surfaces/i)
      expect(text, `${name} does not keep the whole sweep at VERIFY`)
        .toMatch(/VERIFY[\s\S]{0,900}npm run gate/)
    }
  })
})

import { describe, expect, it } from 'vitest'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync }
  from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
// The gate's OWN list, imported — not a copy of it, and not a regex over its source. A test that
// re-types the set it is checking agrees with itself; `sweep.mjs` was split out of the gate for
// exactly this reason and `gate/surfaces.mjs` follows it.
import { LIVE_SURFACES, NOT_PHOTOGRAPHED_YET, SURFACES } from '../gate/surfaces.mjs'
import { deliveryCheck, orchardCheck, standaloneIsLast }
  from '../gate/fixture-checks.mjs'

/**
 * WEB-FIX-337 — the one surface the browser gate never photographed.
 *
 * `browser-gate.mjs` visited `#/tickets/TKT-142/…` — the ticket DETAIL routes — and never
 * `#/tickets` itself, so `TicketsByEpic` (the SEARCH row, the PROVENANCE and STAGE chips, the epic
 * groups and the pager) had no picture in the gate corpus in either theme. The two rules that are
 * supposed to catch a screen nobody looked at both pass over it: the gate photographs what its list
 * names, and `baselines.test.ts` judges only what was photographed. A surface absent from the list
 * is not a red row anywhere — it is silence.
 *
 * A row is judged by WHAT IS INSIDE THE PICTURE and never by the filename — `docs/operations/ESCAPES.md`,
 * 2026-08-26: `role-lane-rail` was read as covering the ROLES row because two components share one
 * word in a shot name, and the row was in none of the 52 text captures.
 *
 * The corpus is produced by `gate/run-gate.sh` against `gate/fixture-repo`, so the fixture's own
 * spellings (`TKT-…` for a ticket, `REQ-…` for the group it is under) are known here for the same
 * reason `browser-gate.mjs` knows `TKT-142`.
 */

const CLIENT = join(__dirname, '..')
const SHOTS = join(CLIENT, 'gate', 'shots')
/**
 * The corpus COMMITTED beside the report — the arbiter on any clone.
 *
 * `gate/shots/` is gitignored, so a case that read only it would sweep nothing on a fresh checkout
 * and pass, which is `WEB-FEAT-085` QA round 4 finding 1 exactly. The committed corpus is always
 * there; the live shots directory is judged as well whenever a run has left one, and that is the
 * half §1's mandatory negative moves.
 */
const CORPUS = join(CLIENT, '..', '..', 'docs', 'reports', 'WEB-FIX-337', 'corpus')

type IndexRow = { surface: string; state: string; theme: string; file: string; text: string }

const THEMES = ['dark', 'light'] as const

/** What `TicketsByEpic` renders beside the search box, carrying the two counts it claims. */
const RESULT_LINE = /(\d+) of (\d+) · matches id, title, owner and lane/

export function readIndex(dir: string): IndexRow[] {
  return JSON.parse(readFileSync(join(dir, 'index.json'), 'utf8')) as IndexRow[]
}

/**
 * What is wrong with ONE text capture of the tickets list, as plain sentences.
 *
 * Split out of the file-level check so a negative can hand it a capture with one thing taken away,
 * rather than waiting for a future gate run to be broken in exactly the right way — `WEB-FEAT-085`
 * QA round 3 rejected a negative that could only ever fail if some surface later rendered an
 * invented sentence.
 */
export function problemsInTheCapture(txt: string, seen: string): string[] {
  const problems: string[] = []
  if (!/\bSEARCH\b/.test(seen)) problems.push(`${txt}: the SEARCH row is not in frame`)

  const line = RESULT_LINE.exec(seen)
  if (!line) {
    problems.push(`${txt}: no result line in frame — the shot was taken with an empty search box`)
    return problems
  }
  const matched = Number(line[1])
  const backlog = Number(line[2])
  if (matched === 0) {
    problems.push(`${txt}: the result line reads "${line[0]}" — nothing matched, so no filtered `
      + 'group is in frame')
  }
  if (matched >= backlog) {
    problems.push(`${txt}: the result line reads "${line[0]}" — the query filtered nothing away, so `
      + 'the two counts are one number and the shot cannot tell them apart')
  }
  if (!/\bREQ-\d+\b/.test(seen)) {
    problems.push(`${txt}: no epic group header in frame — the groups the query matched are not in `
      + 'the picture')
  }
  // The rows BELOW the line whose count is about them. A count and the collection it is drawn over
  // are one assertion, never two (`_TEMPLATE.md` §2, WEB-FEAT-016 / WEB-FIX-091).
  const below = seen.slice(seen.indexOf(line[0]) + line[0].length)
  const ids = [...new Set(below.match(/\bTKT-\d+\b/g) ?? [])]
  if (ids.length !== matched) {
    problems.push(`${txt}: the result line claims ${matched} and ${ids.length} ticket rows are in `
      + `frame (${ids.join(' ') || 'none'})`)
  }
  return problems
}

/**
 * Everything wrong with the tickets list's photographs in one shot corpus.
 *
 * A FUNCTION OF THE INDEX, not of a directory alone, so the mandatory negative can hand it an index
 * with the entry taken out instead of trusting a description of one.
 */
export function ticketsShotProblems(index: IndexRow[], dir: string): string[] {
  const problems: string[] = []
  for (const theme of THEMES) {
    const png = `tickets-${theme}.png`
    const txt = `tickets-${theme}.txt`
    const row = index.find((r) => r.surface === 'tickets' && r.theme === theme && r.state === 'static')
    if (!row) {
      problems.push(`${png}: the shot index has no static \`tickets\` row for the ${theme} theme — `
        + 'the sweep never drove `#/tickets`')
      continue
    }
    if (row.file !== png || row.text !== txt) {
      problems.push(`${png}: the index row names ${row.file} / ${row.text} instead`)
      continue
    }
    if (!existsSync(join(dir, png))) {
      problems.push(`${png}: named by the shot index and not on disk in ${dir}`)
      continue
    }
    if (statSync(join(dir, png)).size === 0) {
      problems.push(`${png}: a zero-byte file`)
      continue
    }
    if (!existsSync(join(dir, txt))) {
      problems.push(`${txt}: the surface was photographed and its text was not captured`)
      continue
    }
    problems.push(...problemsInTheCapture(txt, readFileSync(join(dir, txt), 'utf8')))
  }
  return problems
}

/**
 * Was the gate run on disk a WHOLE sweep? (WEB-FEAT-379.)
 *
 * `npm run gate -- --surfaces tickets --themes dark` photographs a subset ON PURPOSE, and a corpus
 * that is a subset by design is indistinguishable, from the directory alone, from a corpus with
 * holes in it — which is the one thing every case in this file exists to refuse. So the run writes
 * its own scope beside the shots and this reads it. A directory with no `scope.json` was written
 * before the scope existed, and is judged as the whole sweep, which is what it was.
 */
export function theRunOnDiskSweptEverything(dir: string): boolean {
  if (!existsSync(join(dir, 'index.json'))) return false
  const at = join(dir, 'scope.json')
  if (!existsSync(at)) return true
  const scope = JSON.parse(readFileSync(at, 'utf8')) as
    { surfacesScoped?: boolean; themesScoped?: boolean }
  return scope.surfacesScoped === false && scope.themesScoped === false
}

/** The shot corpora there are to judge: the committed one always, a live gate run when there is one. */
function corpora(): { name: string; dir: string }[] {
  const found = [{ name: 'the corpus committed with the report', dir: CORPUS }]
  if (theRunOnDiskSweptEverything(SHOTS)) {
    found.push({ name: 'the gate run on disk in client/web/gate/shots', dir: SHOTS })
  }
  return found
}

describe('WEB-FIX-337 — the gate photographs the tickets list', () => {
  it('test_the_shot_index_holds_the_tickets_list', () => {
    const problems = corpora().flatMap(({ name, dir }) =>
      (existsSync(join(dir, 'index.json'))
        ? ticketsShotProblems(readIndex(dir), dir)
        : [`no index.json in ${dir} — no gate run has written this corpus`]
      ).map((p) => `${name} · ${p}`))
    expect(problems, `\n  ${problems.join('\n  ')}\n`).toEqual([])
  })

  /**
   * The sweep says how much it swept. Without this, deleting the committed corpus turns the case
   * above green on a clone — `_TEMPLATE.md` §2, *a check that sweeps nothing has not run*.
   */
  it('test_the_committed_corpus_is_there_to_be_judged', () => {
    expect(existsSync(join(CORPUS, 'index.json')),
      `no shot index committed at ${CORPUS} — the case above judged nothing`).toBe(true)
    const themes = readIndex(CORPUS).filter((r) => r.surface === 'tickets').map((r) => r.theme)
    expect([...themes].sort()).toEqual(['dark', 'light'])
  })

  /**
   * NEGATIVE (mandatory). Take `#/tickets` out of the gate's shot list and the case above must go
   * red naming the absent file: a check that passes on a corpus with no tickets shot is checking
   * nothing. Spliced out of the REAL index, so the refusal is measured rather than described.
   */
  it('test_a_shot_index_without_the_tickets_entry_is_refused', () => {
    const index = readIndex(CORPUS)
    const dropped = index.filter((r) => r.surface !== 'tickets')
    expect(dropped.length, 'the real index carried no tickets row to drop').toBeLessThan(index.length)

    expect(ticketsShotProblems(dropped, CORPUS)).toEqual([
      'tickets-dark.png: the shot index has no static `tickets` row for the dark theme — '
        + 'the sweep never drove `#/tickets`',
      'tickets-light.png: the shot index has no static `tickets` row for the light theme — '
        + 'the sweep never drove `#/tickets`',
    ])
    expect(ticketsShotProblems(index, CORPUS), 'and the real index still passes').toEqual([])
  })

  /**
   * NEGATIVE (mandatory), the other cheap fix: photograph `#/tickets` with an EMPTY search box and
   * call the surface covered. The result line is the one thing `TicketsByEpic` does not draw then,
   * so the picture would be of the state §1 is not about.
   *
   * Built by taking the result line out of the real capture — not by writing an invented one.
   */
  it('test_a_shot_taken_with_an_empty_search_box_is_refused', () => {
    const real = readFileSync(join(CORPUS, 'tickets-dark.txt'), 'utf8')
    expect(problemsInTheCapture('tickets-dark.txt', real), 'the real capture passes').toEqual([])

    const line = RESULT_LINE.exec(real)!
    const emptyBox = real.replace(line[0], '')
    expect(problemsInTheCapture('tickets-dark.txt', emptyBox)).toEqual([
      'tickets-dark.txt: no result line in frame — the shot was taken with an empty search box',
    ])
  })
})

/**
 * The structural half of `WEB-FIX-337`'s escape analysis.
 *
 * The defect was never `#/tickets` in particular: it was that the set of surfaces the gate sweeps
 * is a hand-written list, and nothing anywhere compared it against the set of surfaces the product
 * offers. `_TEMPLATE.md` §2, the first way a check lies: *a set you typed is bounded by what you
 * already thought of.* So the rail's destinations are DERIVED from the rail, and every one of them
 * has to be either swept or written down as a debt against a real ticket.
 *
 * `docs/operations/ESCAPES.md` already owed this once — the 2026-08-26 row filed `WEB-FIX-259` over the ROLES
 * row nobody had photographed and owed `_TEMPLATE.md` a paragraph, which never landed. A paragraph
 * is what this is instead of.
 */
const NAV = join(CLIENT, 'src', 'components', 'SideNav.tsx')
const TICKET_ID = /^(?:FAC|SRV|WEB)-(?:FIX|FEAT|REF|EPIC)-\d+$/
/** This repository's own document tree — `factory/docs`, two directories above the client. */
const DOCS = join(CLIENT, '..', '..', 'docs')
const PAGES = join(CLIENT, 'src', 'pages')

/** Every destination the rail offers, read off the rail itself. */
export function railDestinations(source: string): string[] {
  return [...new Set([...source.matchAll(/hash:\s*'(#\/[^']*)'/g)].map((m) => m[1]))]
}

/** Every route the gate navigates to for a photograph, static and live. */
function sweptRoutes(): Set<string> {
  return new Set([...SURFACES, ...LIVE_SURFACES].map(([, route]) => route as string))
}

/**
 * Every ticket id this repository actually carries, read off the documents (WEB-FIX-344).
 *
 * `WEB-FIX-337` checked the SHAPE of the id a debt is owed to and never that a document carries
 * it, so `'#/wiki': 'WEB-FIX-999'` was accepted — which is `const LATER = new Set([…])` with more
 * syllables, and the whole value of `NOT_PHOTOGRAPHED_YET` over a private set is that the debt is
 * findable. Derived by reading each document's own `id:`, never from a filename and never from a
 * list here: `_TEMPLATE.md` §2, *a set you typed is bounded by what you already thought of*.
 */
export function ticketIdsOnDisk(docs = DOCS): Set<string> {
  const ids = new Set<string>()
  for (const dir of ['tickets', 'epics']) {
    const at = join(docs, dir)
    if (!existsSync(at)) continue
    for (const f of readdirSync(at)) {
      if (!f.endsWith('.md') || f.startsWith('_') || f === 'README.md') continue
      const id = /^id:[ \t]*(\S+)[ \t]*$/m.exec(readFileSync(join(at, f), 'utf8'))
      if (id) ids.add(id[1])
    }
  }
  return ids
}

/**
 * Everything wrong with the sweep judged against the rail; `[]` means every destination is
 * accounted for.
 *
 * A FUNCTION of the four sets, for the reason `ticketsShotProblems` is one: §1's mandatory
 * negative drives the REAL checker with a broken surface list rather than restating its
 * arithmetic in an assertion that can only agree with itself. Every line names the route it is
 * about, because a refusal that does not is a refusal nobody can act on.
 */
export function railProblems(input: {
  destinations: string[]
  swept: Set<string>
  owed: Record<string, string>
  tickets: Set<string>
}): string[] {
  const { destinations, swept, owed, tickets } = input
  const problems: string[] = []
  for (const d of destinations) {
    if (swept.has(d) || d in owed) continue
    problems.push(`${d} is on the rail, no gate run opens it, and nothing says which ticket will`)
  }
  for (const [route, ticket] of Object.entries(owed)) {
    if (!TICKET_ID.test(ticket)) {
      problems.push(`${route} is declared unphotographed against "${ticket}", which is not a `
        + 'ticket id')
      continue
    }
    // WEB-FIX-344 — and the id has to name a document somebody can open, not merely look like
    // one. The shape check alone accepts `WEB-FIX-999`, which is a debt nobody can pay or find.
    if (!tickets.has(ticket)) {
      problems.push(`${route} is declared unphotographed against ${ticket}, and no ticket carries `
        + 'that id')
      continue
    }
    // A stale debt is its own defect: it reads as work outstanding on a surface already covered,
    // and it is the row a later ticket would delete instead of the one it should.
    if (swept.has(route)) {
      problems.push(`${route} is declared unphotographed and swept anyway`)
      continue
    }
    // …and a debt against a destination the rail no longer offers.
    if (!destinations.includes(route)) {
      problems.push(`${route} is declared unphotographed and is not on the rail at all`)
    }
  }
  return problems
}

describe('WEB-FIX-337 — the sweep is judged against the product, not against itself', () => {
  it('test_every_destination_the_rail_offers_is_swept_or_owed_by_a_named_ticket', () => {
    const destinations = railDestinations(readFileSync(NAV, 'utf8'))
    // The sweep says how much it swept. A regex that stopped matching reads exactly like a rail
    // that stopped offering anything, and would turn every assertion below green.
    expect(destinations.length,
      `only ${destinations.length} destinations found in ${NAV} — the derivation has stopped `
      + 'seeing the rail').toBeGreaterThanOrEqual(10)

    const tickets = ticketIdsOnDisk()
    // …and so does the id set every debt is checked against. An empty one accepts all of them,
    // which is the check this replaced, reintroduced by a glob that stopped matching.
    expect(tickets.size,
      `only ${tickets.size} ticket ids read out of ${DOCS} — the derivation has stopped seeing `
      + 'the documents, and every debt would be accepted').toBeGreaterThan(100)

    const problems = railProblems({
      destinations, swept: sweptRoutes(), owed: NOT_PHOTOGRAPHED_YET, tickets,
    })
    expect(problems, `\n  ${problems.join('\n  ')}\n`).toEqual([])
  })

  /**
   * NEGATIVE (mandatory, and WEB-FIX-344 §1.4). The two cheap fixes for the case above are to
   * stop sweeping a surface, and to excuse it against something nobody can open. Both are driven
   * against the REAL rail and the REAL id set, so the checker is measured refusing rather than
   * described refusing — and the second is the one `WEB-FIX-337` accepted.
   */
  it('test_a_destination_that_is_neither_swept_nor_owed_is_refused', () => {
    const destinations = railDestinations(readFileSync(NAV, 'utf8'))
    const tickets = ticketIdsOnDisk()
    expect(destinations).toContain('#/wiki')

    const dropped = sweptRoutes()
    dropped.delete('#/wiki')
    expect(railProblems({ destinations, swept: dropped, owed: {}, tickets })).toEqual([
      '#/wiki is on the rail, no gate run opens it, and nothing says which ticket will',
    ])

    // …and the same destination excused by an id no document carries. It passes the SHAPE check
    // that was the whole of this rule until now, which is what makes it the cheap fix.
    expect(TICKET_ID.test('WEB-FIX-999'), 'the shape check alone still accepts it').toBe(true)
    expect(tickets.has('WEB-FIX-999'), 'and no document on disk carries it').toBe(false)
    expect(railProblems({
      destinations, swept: dropped, owed: { '#/wiki': 'WEB-FIX-999' }, tickets,
    })).toEqual([
      '#/wiki is declared unphotographed against WEB-FIX-999, and no ticket carries that id',
    ])

    // …while a debt owed to a ticket that DOES exist is accepted, so the refusal above is about
    // an id naming nothing rather than about every id.
    expect(tickets.has('WEB-FIX-344'), 'this ticket is a document on disk').toBe(true)
    expect(railProblems({
      destinations, swept: dropped, owed: { '#/wiki': 'WEB-FIX-344' }, tickets,
    })).toEqual([])

    // …and the tree as it stands still passes.
    expect(railProblems({
      destinations, swept: sweptRoutes(), owed: NOT_PHOTOGRAPHED_YET, tickets,
    }), 'and the real tree still passes').toEqual([])
  })

  /** NEGATIVE (mandatory). And a rail the derivation cannot read is not an empty rail. */
  it('test_a_rail_the_derivation_cannot_read_is_not_an_empty_rail', () => {
    expect(railDestinations('const ITEMS = [{ label: "Dashboard", route: "home" }]')).toEqual([])
    expect(railDestinations(readFileSync(NAV, 'utf8')).length).toBeGreaterThanOrEqual(10)
  })
})

/**
 * WEB-FIX-344 — the six destinations the sweep did not reach, and what makes a picture of one
 * count.
 *
 * With `NOT_PHOTOGRAPHED_YET` empty the case above says every destination is SWEPT, and that is a
 * statement about a list. `docs/operations/ESCAPES.md`, 2026-08-26, is what a list is worth on its own: a row
 * is judged by WHAT IS INSIDE THE PICTURE and never by the filename. So every destination's
 * capture is read here, and two things are asked of it — that it is not one of the sentences this
 * client draws when there is nothing to show, and that it carries lines no other surface draws.
 */

/**
 * Every sentence the client renders in place of content, derived from the pages themselves.
 *
 * The CATEGORY is stated rather than a list typed here: a `data-testid` ending in `-empty`,
 * `-refused`, `-absent` or `-warning` is, by this client's own convention, the paragraph a surface
 * draws when the collection is empty, when the manifest declares nothing, or when the document it
 * names is not there. `seams-unchecked` is none of those — it is standing prose that ships beside
 * real content — and it is out by the category rather than by name.
 */
export function emptyStateSentences(dir = PAGES): { testid: string; text: string }[] {
  const found: { testid: string; text: string }[] = []
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.tsx'))) {
    const src = readFileSync(join(dir, f), 'utf8')
    for (const m of src.matchAll(
      /data-testid="([a-z-]+-(?:empty|refused|absent|warning))"\s*>([\s\S]*?)<\/p>/g)) {
      const text = m[2]
        .replace(/<[^>]*>/g, ' ')        // <code>…</code> and friends
        .replace(/\{[^{}]*\}/g, ' ')     // {w.path}
        .replace(/[“”]/g, '"')
        .replace(/\s+/g, ' ')
        .trim()
      if (text.length >= 20) found.push({ testid: m[1], text })
    }
  }
  return found
}

/** One text capture as whitespace-normalised, non-empty lines. */
const linesOf = (txt: string) =>
  txt.split('\n').map((l) => l.replace(/\s+/g, ' ').trim()).filter(Boolean)

/**
 * The lines EVERY static capture in a corpus carries — the shell, derived rather than described.
 *
 * A picture that is the shell and nothing else has been taken and has shown nothing, which is the
 * half of §1's criterion 1 that "rather than an empty state" does not cover: a surface rendering
 * neither content nor an empty state is silence with a PNG beside it.
 */
export function shellLines(index: IndexRow[], dir: string): Set<string> {
  const captures = index
    .filter((r) => r.state === 'static' && existsSync(join(dir, r.text)))
    .map((r) => new Set(linesOf(readFileSync(join(dir, r.text), 'utf8'))))
  if (captures.length === 0) return new Set()
  return new Set([...captures[0]].filter((l) => captures.every((c) => c.has(l))))
}

/**
 * What is wrong with ONE capture of one destination, as plain sentences.
 *
 * Split out for `problemsInTheCapture`'s reason: a negative can hand it the real capture with a
 * refusal spliced into it, rather than waiting for a future gate run to be broken in exactly the
 * right way.
 */
export function problemsInTheDestinationCapture(
  txt: string, route: string, seen: string, shell: Set<string>,
  empties: { testid: string; text: string }[],
): string[] {
  const flat = seen.replace(/[“”]/g, '"').replace(/\s+/g, ' ')
  const hit = empties.find((e) => flat.includes(e.text))
  if (hit) {
    return [`${txt}: ${route} was photographed showing \`${hit.testid}\` — `
      + `"${hit.text.slice(0, 60)}…"`]
  }
  const own = linesOf(seen).filter((l) => !shell.has(l))
  if (own.length < 3) {
    return [`${txt}: ${route} drew ${own.length} lines no other surface draws — the picture is `
      + 'the shell and nothing of its own']
  }
  return []
}

/**
 * Everything wrong with one shot corpus's pictures of the rail's destinations.
 *
 * A FUNCTION OF THE INDEX, not of a directory alone, so the mandatory negative can hand it an
 * index with a destination's rows taken out instead of trusting a description of one.
 */
export function destinationShotProblems(
  index: IndexRow[], dir: string, routes: Map<string, string>,
  empties: { testid: string; text: string }[],
): string[] {
  const problems: string[] = []
  const shell = shellLines(index, dir)
  for (const [route, surface] of routes) {
    for (const theme of THEMES) {
      const png = `${surface}-${theme}.png`
      const txt = `${surface}-${theme}.txt`
      const row = index.find(
        (r) => r.surface === surface && r.theme === theme && r.state === 'static')
      if (!row) {
        problems.push(`${png}: the shot index has no static \`${surface}\` row for the ${theme} `
          + `theme — the sweep never drove ${route}`)
        continue
      }
      if (!existsSync(join(dir, png))) {
        problems.push(`${png}: named by the shot index and not on disk in ${dir}`)
        continue
      }
      if (statSync(join(dir, png)).size === 0) {
        problems.push(`${png}: a zero-byte file`)
        continue
      }
      if (!existsSync(join(dir, txt))) {
        problems.push(`${txt}: ${route} was photographed and its text was not captured`)
        continue
      }
      problems.push(...problemsInTheDestinationCapture(
        txt, route, readFileSync(join(dir, txt), 'utf8'), shell, empties))
    }
  }
  return problems
}

/** `route → the name the sweep photographs it under`, off the gate's own list. */
function railShots(): Map<string, string> {
  const names = new Map(SURFACES.map(([name, route]) => [route as string, name as string]))
  return new Map(railDestinations(readFileSync(NAV, 'utf8'))
    .filter((d) => names.has(d))
    .map((d) => [d, names.get(d) as string]))
}

/** The corpus committed beside THIS ticket's report — the arbiter on any clone. */
const CORPUS_344 = join(CLIENT, '..', '..', 'docs', 'reports', 'WEB-FIX-344', 'corpus')

/** The corpora there are to judge: the committed one always, a live gate run when there is one. */
function corpora344(): { name: string; dir: string }[] {
  const found = [{ name: 'the corpus committed with the report', dir: CORPUS_344 }]
  if (theRunOnDiskSweptEverything(SHOTS)) {
    found.push({ name: 'the gate run on disk in client/web/gate/shots', dir: SHOTS })
  }
  return found
}

/** The six §1 names, so the committed corpus is asserted to carry the ones this ticket opened. */
const OPENED_HERE = ['delivery', 'all-runs', 'wiki', 'escapes', 'seams', 'orchard']

describe('WEB-FIX-344 — every destination the rail offers is in the shot corpus', () => {
  it('test_every_rail_destination_is_photographed_with_its_own_content', () => {
    const destinations = railDestinations(readFileSync(NAV, 'utf8'))
    const routes = railShots()
    const empties = emptyStateSentences()
    // Both derivations say how much they swept. A rail the gate maps under no name, and an
    // empty-state derivation matching nothing, each turn every judgment below green while
    // examining nothing.
    expect([...routes.keys()].sort(),
      'the rail offers destinations no gate surface is named for').toEqual([...destinations].sort())
    expect(empties.length,
      `only ${empties.length} empty-state sentences read out of ${PAGES} — the derivation has `
      + 'stopped seeing them, and a photograph of a refusal would pass').toBeGreaterThanOrEqual(6)

    const problems = corpora344().flatMap(({ name, dir }) =>
      (existsSync(join(dir, 'index.json'))
        ? destinationShotProblems(readIndex(dir), dir, routes, empties)
        : [`no index.json in ${dir} — no gate run has written this corpus`]
      ).map((p) => `${name} · ${p}`))
    expect(problems, `\n  ${problems.join('\n  ')}\n`).toEqual([])
  })

  /**
   * The sweep says how much it swept. Without this, deleting the committed corpus turns the case
   * above green on a clone — `_TEMPLATE.md` §2, *a check that sweeps nothing has not run*.
   */
  it('test_the_committed_corpus_holds_the_six_destinations_this_ticket_opened', () => {
    expect(existsSync(join(CORPUS_344, 'index.json')),
      `no shot index committed at ${CORPUS_344} — the case above judged nothing`).toBe(true)
    const index = readIndex(CORPUS_344)
    for (const surface of OPENED_HERE) {
      const themes = index.filter((r) => r.surface === surface && r.state === 'static')
        .map((r) => r.theme).sort()
      expect(themes, `${surface} is not in the committed corpus in both themes`)
        .toEqual(['dark', 'light'])
    }
  })

  /**
   * NEGATIVE (mandatory, WEB-FIX-344 §1.4, the picture half). Take a destination's rows out of
   * the index and the case above must go red naming the route it stopped opening; photograph the
   * surface's REFUSAL instead of its content and it must go red too. Both are spliced out of the
   * real corpus, so each refusal is measured rather than described.
   */
  it('test_a_corpus_missing_a_destination_or_showing_its_refusal_is_refused', () => {
    const index = readIndex(CORPUS_344)
    const routes = railShots()
    const empties = emptyStateSentences()
    expect(destinationShotProblems(index, CORPUS_344, routes, empties),
      'the committed corpus passes').toEqual([])

    const dropped = index.filter((r) => r.surface !== 'wiki')
    expect(dropped.length, 'the real index carried no wiki row to drop').toBeLessThan(index.length)
    expect(destinationShotProblems(dropped, CORPUS_344, routes, empties)).toEqual([
      'wiki-dark.png: the shot index has no static `wiki` row for the dark theme — the sweep '
        + 'never drove #/wiki',
      'wiki-light.png: the shot index has no static `wiki` row for the light theme — the sweep '
        + 'never drove #/wiki',
    ])

    const refusal = empties.find((e) => e.testid === 'wiki-refused')
    expect(refusal, 'WikiPage no longer draws a `wiki-refused` paragraph').toBeDefined()
    const shell = shellLines(index, CORPUS_344)
    const real = readFileSync(join(CORPUS_344, 'wiki-dark.txt'), 'utf8')
    expect(problemsInTheDestinationCapture('wiki-dark.txt', '#/wiki', real, shell, empties),
      'the real capture passes').toEqual([])
    const refused = `${real}\n${(refusal as { text: string }).text}\n`
    expect(problemsInTheDestinationCapture('wiki-dark.txt', '#/wiki', refused, shell, empties))
      .toEqual([
        'wiki-dark.txt: #/wiki was photographed showing `wiki-refused` — '
          + `"${(refusal as { text: string }).text.slice(0, 60)}…"`,
      ])
  })
})

/**
 * WEB-FEAT-379 — the live corpus is judged only when the run that wrote it swept everything.
 *
 * The predicate is driven over the four states a shots directory can be in, because the one that
 * matters — a run scoped on ONE axis — is the state a boolean `wasScoped` would have got wrong,
 * and a fixture holding only the two extremes cannot tell the two spellings apart.
 */
describe('WEB-FEAT-379 — a scoped run is a subset by design, not a corpus with holes', () => {
  it('test_a_scoped_run_on_disk_is_not_judged_as_a_whole_sweep', () => {
    const dir = mkdtempSync(join(tmpdir(), 'web-feat-379-'))
    try {
      expect(theRunOnDiskSweptEverything(dir), 'no run at all is not a run to judge').toBe(false)

      writeFileSync(join(dir, 'index.json'), '[]')
      expect(theRunOnDiskSweptEverything(dir),
        'a run written before the scope existed swept everything').toBe(true)

      for (const [surfacesScoped, themesScoped, judged] of [
        [true, true, false], [true, false, false], [false, true, false], [false, false, true],
      ] as [boolean, boolean, boolean][]) {
        writeFileSync(join(dir, 'scope.json'), JSON.stringify({ surfacesScoped, themesScoped }))
        expect(theRunOnDiskSweptEverything(dir),
          `surfaces=${surfacesScoped} themes=${themesScoped}`).toBe(judged)
      }
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

// ── WEB-FIX-405 · cluster A ───────────────────────────────────────────────────────────────────

const REPO = join(CLIENT, '..', '..')
const SPEC = join(REPO, 'docs', 'product/SPEC.md')
const SCHEMA_PATH = join(REPO, 'docs', 'technical/contracts/http.schema.json')
const RUN_GATE = join(CLIENT, 'gate', 'run-gate.sh')

/**
 * The delivery states `SPEC.md` §3.5 tabulates, read off the specification rather than typed here.
 *
 * §3.5's table IS the product's enumeration — every other statement of it (`probe.cpp`'s `settle`
 * calls, the vocabulary sheet, `E30`'s axis) is downstream of it. A list written into this file
 * would agree with whatever its author last read, which is `_TEMPLATE.md` §2's first way a check
 * lies, and it is the exact shape `browser-gate.mjs:874` had.
 */
export function specDeliveryStates(specText: string): string[] {
  const section = specText.split('### 3.5 Delivery')[1]?.split('**The evidence')[0] ?? ''
  return [...section.matchAll(/^\|\s*`([a-z_]+)`\s*\|/gm)].map((m) => m[1])
}

/** The delivery sources of the browser gate — derived from `gate/`, never a list typed here. */
function gateSources(): string[] {
  return readdirSync(join(CLIENT, 'gate'))
    .filter((f) => f.endsWith('.mjs'))
    .map((f) => join(CLIENT, 'gate', f))
}

/**
 * Every literal array in `text` that names two or more of `states` — a typed delivery domain.
 *
 * Two, not one: `browser-gate.mjs` legitimately names a single state in a sentence it prints, and
 * a rule that refused that would forbid the live half's own `running` assertion. What may not
 * exist is a SET somebody wrote down, because a set is the thing that stops tracking the product.
 */
export function typedStateLists(text: string, states: string[]): string[] {
  // Comments first. The one file guaranteed to hold the defect verbatim is the file that explains
  // it: `delivery-states.mjs` opens by quoting `['done','running','stalled','unshipped','waiting']`
  // as the thing it replaced, and a sweep that cannot tell a record of the incident from the
  // incident either forbids the explanation or is satisfied by deleting it.
  const code = text
    .split('\n')
    .map((line) => line.replace(/^\s*(\*|\/\/|\/\*|#).*$/, ''))
    .join('\n')
  return [...code.matchAll(/\[[^[\]]*\]/g)]
    .map((m) => m[0])
    .filter((arr) => states.filter((s) => new RegExp(`['"\`]${s}['"\`]`).test(arr)).length >= 2)
}

describe('WEB-FIX-405 — the gate’s delivery domain is derived, and `running` lives in the live half', () => {
  /**
   * §1 cluster A1. Three facts, in one case because they are one sentence: the CONTRACT enumerates
   * the states, the gate's judge is bounded by that enumeration, and the one state no static frame
   * can reach is asserted where it can be.
   */
  it('the_delivery_states_the_gate_asserts_are_derived_not_typed', async () => {
    const spec = specDeliveryStates(readFileSync(SPEC, 'utf8'))
    expect(spec.length, '§3.5 tabulated no delivery state — the derivation swept nothing')
      .toBeGreaterThan(1)

    const schema = JSON.parse(readFileSync(SCHEMA_PATH, 'utf8'))
    expect(schema.$defs?.delivery?.properties?.state?.enum,
      'the contract lets `delivery.state` be any string at all, so there is no enumeration for the '
      + 'gate to derive its domain from').toEqual(spec)

    // The judge is bounded by the schema: a fixture or a vocabulary sheet naming a state the
    // contract does not enumerate is refused rather than silently widening the domain.
    const { judgeDeliveryBadges } = await import('../gate/delivery-states.mjs')
    const produced = [{ state: spec[0], source: 'a' }, { state: spec[1], source: 'b' }]
    const good = judgeDeliveryBadges({ produced, words: spec, drawn: [spec[0], spec[1]],
                                       domain: spec })
    expect(good.ok, good.line).toBe(true)

    const invented = judgeDeliveryBadges({
      produced: [...produced, { state: 'shipped_ish', source: 'c' }],
      words: [...spec, 'shipped_ish'], drawn: [spec[0], spec[1], 'shipped_ish'], domain: spec })
    expect(invented.ok, 'a state the contract does not enumerate was accepted').toBe(false)
    expect(invented.line).toContain('shipped_ish')

    expect(judgeDeliveryBadges({ produced, words: spec, drawn: [spec[0], spec[1]], domain: [] }).ok,
      'an empty enumeration is a sweep of nothing, and it passed').toBe(false)

    // …and no gate source holds a list of them. This is the defect itself: `browser-gate.mjs:874`
    // was `['done','running','stalled','unshipped','waiting'].every(…)`.
    const sources = gateSources()
    expect(sources.length, 'no gate source was swept').toBeGreaterThan(0)
    const typed = sources.flatMap((f) =>
      typedStateLists(readFileSync(f, 'utf8'), spec).map((a) => `${f.split('/').pop()}: ${a}`))
    expect(typed, `a delivery-state list is typed into the gate: ${typed.join(' | ')}`).toEqual([])

    // …and the sweep can still SEE one. Stripping comments is one edit away from stripping the
    // code too, and then this rule governs nothing while reporting the same green.
    expect(typedStateLists(`const d = ['${spec[0]}', '${spec[1]}']\n`, spec),
      'the rule no longer sees the exact line browser-gate.mjs:874 held').toHaveLength(1)
    expect(typedStateLists(` * it was ['${spec[0]}', '${spec[1]}']\n`, spec),
      'a comment recording the defect was read as the defect').toEqual([])

    // `running` is a fact about the process table, so it is asserted in section 10 — where a lane
    // the tool started is alive — and nowhere above it.
    const gate = readFileSync(join(CLIENT, 'gate', 'browser-gate.mjs'), 'utf8')
    const split = gate.indexOf('── 10 · the live sweep')
    expect(split, 'browser-gate.mjs no longer marks its live sweep').toBeGreaterThan(0)
    const live = gate.slice(split)
    expect(live, '`running` is not asserted in the live half, so it is asserted nowhere')
      .toMatch(/ok\(\s*['"`][^'"`]*running/)
  })

  /**
   * §1 cluster A-NEG. A red vitest phase used to end the run under `set -e`, so a gate whose unit
   * suite failed photographed nothing — and the corpus check, which only has a run to compare
   * against AFTER the sweep, never ran at all. `WEB-FIX-362` and `WEB-FIX-374` are the two tickets
   * about the shape.
   *
   * Driven in both directions on the script's TEXT, so the rule can fail: the real script must
   * report no problem, and a fabricated copy with the capture taken off the vitest phase must be
   * named.
   */
  it('test_the_shots_are_taken_even_when_the_suite_is_red', () => {
    const script = readFileSync(RUN_GATE, 'utf8')
    expect(problemsInTheGatesOrder(script), 'run-gate.sh can still be ended by a red phase')
      .toEqual([])

    const broken = script.replace(/ \|\| vitest_status=\$\?/, '')
    expect(broken, 'the fabrication changed nothing — the case cannot fail').not.toEqual(script)
    expect(problemsInTheGatesOrder(broken).join(' | '))
      .toMatch(/vitest/)
  })
})

/**
 * The phases that may still END the run, and why each one may.
 *
 * Stated as a CATEGORY with a reason per entry rather than as rows deleted until the rule passed:
 * these two produce the thing the sweep consumes, so a sweep behind either of them is not a
 * weaker verdict, it is no verdict. Everything else before the sweep is a VERDICT ABOUT the
 * product, and a verdict is recorded and carried to the exit — never used to cancel the pictures.
 */
const FATAL_BECAUSE_THE_SWEEP_CONSUMES_THEM: Record<string, string> = {
  build: 'the sweep drives the bundle this phase writes — there is nothing to photograph without it',
  server: 'the sweep talks to the server this phase starts, and to the fixture repository it serves',
}

/**
 * What is wrong with the ORDER `run-gate.sh` runs its phases in, as plain sentences.
 *
 * A function of the script's text so a fabricated copy can be handed to it — the shape
 * `baselines.test.ts::rowsRejectedBy` already uses, and the reason is the same: a check that can
 * only read the real file agrees with whatever the real file says.
 */
export function problemsInTheGatesOrder(script: string): string[] {
  const problems: string[] = []
  const phases = [...script.matchAll(/^[ \t]*phase_begin ([a-z]+)$/gm)].map((m) => m[1])
  if (phases.length < 5) return ['run-gate.sh declares fewer than five phases — nothing was swept']

  const sweepAt = phases.indexOf('sweep')
  if (sweepAt < 0) return ['run-gate.sh runs no sweep at all']

  // The exclusion list is checked against the script, so a renamed phase is a red row here rather
  // than a silently empty category.
  for (const name of Object.keys(FATAL_BECAUSE_THE_SWEEP_CONSUMES_THEM)) {
    if (!phases.includes(name)) problems.push(`the fatal-phase category names \`${name}\`, which run-gate.sh no longer runs`)
  }

  const mustRecord = phases.slice(0, sweepAt)
    .filter((p) => !(p in FATAL_BECAUSE_THE_SWEEP_CONSUMES_THEM))
  if (mustRecord.length === 0) problems.push('every phase before the sweep is exempt — the rule governs nothing')

  for (const phase of mustRecord) {
    const block = script.split(`phase_begin ${phase}`)[1]?.split('phase_end')[0] ?? ''
    if (!/\|\|\s*\w+_status=\$\?/.test(block)) {
      problems.push(
        `the ${phase} phase ends the run when it fails, so a red ${phase} means no sweep and no `
        + 'corpus check — its status must be recorded and carried to the exit instead')
    }
  }

  // …and a recorded status that nothing reads is worse than none: the run would go green on a
  // phase it had already judged red.
  for (const [, name] of script.matchAll(/\|\|\s*(\w+_status)=\$\?/g)) {
    if (script.split(name).length < 3) problems.push(`${name} is recorded and never read`)
  }

  // The sweep and the baselines phase are unconditional. A sweep behind `if [ $x_status = 0 ]` is
  // the same defect one line lower.
  const afterSweep = script.slice(script.indexOf('phase_begin sweep'))
  if (/if\s+\[\s*"?\$\{?\w+_status/.test(afterSweep)) {
    problems.push('a phase after the sweep is guarded by an earlier phase’s status')
  }
  return problems
}

describe('WEB-FIX-415 — scoped fixture collections and ordering', () => {
  const fixture = () => JSON.parse(readFileSync(
    join(CLIENT, 'gate/fixture-repo/.factory/factory.json'), 'utf8'))

  it('test_standalone_order_uses_wrapped_requirement_rows', () => {
    const root = document.createElement('div')
    root.innerHTML = '<div data-testid="requirement-node"><a data-testid="requirement-row">REQ-014</a></div>'
      + '<div data-testid="requirement-node"><a data-testid="requirement-row" data-standalone="true">Standalone</a></div>'
    expect(standaloneIsLast(root), 'after the real requirements, never among them').toBe(true)
    root.prepend(root.lastElementChild!)
    expect(standaloneIsLast(root), 'standalone must not precede real requirements').toBe(false)
    root.replaceChildren()
    expect(standaloneIsLast(root), 'an empty requirement list proves no ordering').toBe(false)
  })

  it('test_scoped_fixture_declares_owned_repository_documents', () => {
    const manifest = fixture()
    const names = Object.keys(manifest.repos ?? {})
    expect(names, 'Orchard needs an actual named repository').toEqual(['fixture'])
    expect(manifest.docs, 'top-level docs and repos are mutually exclusive').toBeUndefined()
    const repo = manifest.repos.fixture
    expect(repo.path).toBe('.')
    expect(repo.docs).toEqual({ epics: 'docs/requirements', tickets: 'docs/tickets',
      reports: 'docs/reports', wiki: 'docs/wiki', escapes: 'docs/ESCAPES.md' })
    const workspaces = Object.values(manifest.workspaces) as { repo?: string }[]
    expect(workspaces.length).toBeGreaterThan(0)
    expect(workspaces.every((workspace) => workspace.repo === 'fixture')).toBe(true)
    expect(manifest.workspaces.ledger, 'the unknown-workspace ticket remains a negative').toBeUndefined()
  })

  it('test_scoped_fixture_collections_are_non_empty', () => {
    const manifest = fixture()
    const names = Object.keys(manifest.repos ?? {})
    const backlog = { total: 9 }
    const counts = names.map(() => '9 tickets')
    const orchard = orchardCheck(manifest, backlog, names, counts, String(names.length))
    expect(orchard.passed, orchard.name).toBe(true)
    expect(names.length).toBeGreaterThan(0)
    for (const name of names) {
      const repos = { ...manifest.repos }
      delete repos[name]
      const missing = orchardCheck({ ...manifest, repos }, backlog, names, counts, String(names.length))
      expect(missing.passed, missing.name).toBe(false)
    }
    for (let i = 0; i < names.length; i += 1) {
      const missing = orchardCheck(manifest, backlog, names.filter((_, index) => index !== i),
        counts.filter((_, index) => index !== i), String(names.length))
      expect(missing.passed, missing.name).toBe(false)
    }
    expect(orchardCheck(manifest, backlog, names, [], String(names.length)).passed,
      'a repository without its count is incomplete').toBe(false)
    expect(orchardCheck({ repos: {} }, { total: 0 }, [], [], '0').passed,
      'empty server and screen collections must not pass together').toBe(false)

    const home = { needs_you: { rows: [{ ticket_id: 'TKT-142' }] },
      unpushed: { rows: [{ ticket_id: 'TKT-147' }] } }
    const ledger = { total: 2 }
    const drawn = ['TKT-142', 'TKT-147']
    const delivery = deliveryCheck(home, ledger, drawn, '2')
    expect(delivery.passed, delivery.name).toBe(true)
    for (const group of ['needs_you', 'unpushed'] as const) {
      const missing = deliveryCheck({ ...home, [group]: { rows: [] } }, ledger, drawn, '2')
      expect(missing.passed, missing.name).toBe(false)
    }
    for (let i = 0; i < drawn.length; i += 1) {
      const missing = deliveryCheck(home, ledger, drawn.filter((_, index) => index !== i), '2')
      expect(missing.passed, missing.name).toBe(false)
    }
    expect(deliveryCheck({ needs_you: { rows: [] }, unpushed: { rows: [] } }, ledger, [], '2').passed,
      'empty server and screen collections must not pass together').toBe(false)
    expect(deliveryCheck(home, ledger, [...drawn].reverse(), '2').passed).toBe(false)
  })

  it('test_live_gate_calls_the_regression_checked_predicates', () => {
    const source = readFileSync(join(CLIENT, 'gate/browser-gate.mjs'), 'utf8')
    for (const name of ['deliveryCheck', 'orchardCheck']) {
      expect(source, `the live gate must call ${name}`).toContain(`${name}(`)
    }
    expect(source).toMatch(/\$eval\('\[data-testid="rows"\]', standaloneIsLast\)/)
    for (const name of ['delivery', 'orchard']) {
      expect(source, `the live gate must use ${name}'s verdict`)
        .toContain(`ok(${name}.name, ${name}.passed, ${name}.detail)`)
    }
  })
})

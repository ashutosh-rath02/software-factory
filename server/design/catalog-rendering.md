# `app/catalog` — what a document becomes on the wire

The decisions behind `render.{h,cpp}` and `document.{h,cpp}`, moved out of the code by
`SRV-FEAT-229`. Several are the *why* behind a specific escape, so deleting them would have lost
the reasoning that fix was bought with.

## `document` — one document, reduced

**Five keys are structural** — `id`, `parent`, `workspace`, `status`, `stages` — and they are the
only place this module is allowed an opinion. Every other frontmatter key is carried in `fields`
untouched and reaches a screen only because the manifest's `display.ticket_fields` asked for it by
name. That is the whole mechanism by which a repository's own vocabulary is displayed by a tool
that knows nothing about it.

**`repo` is a fact about WHERE the file is, never what it says** (`SRV-FIX-181`). It is an index
into the manifest's `repos`, not frontmatter, and is never parsed from one — a document that
declared its own repository could disagree with the directory holding it. A ticket's `parent`
names a requirement in ITS repo, so two repositories may each hold an `EPIC-001` without either
counting the other's tickets.

**Parsing never throws and never refuses.** A document it cannot make sense of comes back with
`parseError` set, its body intact and its id taken from the file's name. Frontmatter opened and
never closed yields the whole file as the body rather than nothing: a document that cannot be
parsed can still be read, and that is the point.

`splitList` accepts `a b c`, `[a, b, c]` and a `- item` block, because a repository writes what it
writes. `value   # a note` drops the note; a quoted value keeps everything.

## `render` — the holes

`catalog` reads the target repository's documents and **nothing else**. What became of a ticket's
work is a fact about git and the process table; what a run cost is a fact about the run directory;
what stage it reached is a fact about what a run reported. None of those are documents.

So the facts that are not documents arrive as **lookups injected by `api`**, not as
dependencies:

| hole | the fact it carries | whose fact it is |
|---|---|---|
| `Delivery` | what became of the work | `delivery` — git, the process table |
| `Runs` | what a ticket's runs cost | `agent` — the run directory |
| `Stage` | what a run reported (E22) | `lane` |
| `Roles` | what the tool started, or holds a queue place for (F31) | `lane` |
| `Outcome` | what became of the newest run | `agent` |
| `Author` | who committed on the ticket's branch | `delivery` — git |

`Lane` and `Unpushed` are two more of the same shape, carried by the requirement and home
renderers.

A `catalog` that shelled out to git would be the same boundary failure as a `catalog` that
hardcoded a directory name, one layer down (`docs/technical/ARCHITECTURE.md`).

**They are lookups and not values so `api` can build each from ONE pass** and hand every row the
same answer. E5 and E4's rows then read one index and cannot disagree about the same ticket —
which is `docs/product/SPEC.md` F27: three surfaces, one number, one source.

**The two ticket renderers give none of their lookups a default, and that is a refusal the
compiler makes rather than a rule a reader has to remember.** `SRV-FIX-377` added `Author` to
`renderTicket` as `const AuthorLookup& authors = {}`; `api/socket.cpp` served the same document
from `subscribe {ticket:<id>}` and omitted it, so the socket's snapshot — which
`docs/technical/contracts/socket.schema.json` declares to be E5's shape — carried every member E5 has except `owner`,
and an absent `owner` reads as *nobody wrote this*. It compiled, it ran, and no test could see it,
because the parameter that must not be forgotten had been declared optional in the commit that
made forgetting it a defect (`SRV-FIX-380`). A caller of `renderTicket` or `renderTickets` now
names every lookup or says `{}` out loud. `renderRequirements` and `renderRequirement` keep their
defaults: an omitted `Runs`, `Stage`, `Outcome` or `Lane` is *visible* — a screen draws `0 runs`
or an empty stage row — where an omitted `Author` deletes the member.

`catalog` still owns what the documents say: the stage `total` and `source` are its, because the
sequence comes out of the ticket or the manifest; the three numbers beside them came off the wire.
The report's filename is likewise this module's own convention and so this module's to supply.

## The process axis is read, not inferred

`meta.json`'s `outcome` is the verb this tool **wrote** when it reaped the process, and §3.2's axis
has a value for each one — so rendering it is a lookup in a vocabulary, not an opinion about what
happened.

- `SRV-FIX-096` — a run that died unreaped renders `crashed`, the family §3.2 already has room for.
  The distinction from a run that crashed *with* an exit code is kept on the record itself
  (`settled_from`, `exit_code: null`) rather than spent on a fifth process word.
- `SRV-FIX-064` — **anything unrecognised is not "nothing ever happened"**. That inversion rendered
  two runs of 6354 s and $2.65 as `not_started`.

**Precedence is the ticket's decision:** a live lane beats the last outcome, which beats
`not_started`. A claimed lane that has run nothing answers `not_started` from its own record and
deliberately falls through to the outcome — a lane's existence must not shadow what became of the
ticket's last run (`SRV-FIX-081`). §4.5's "no lane yet" note is then true exactly when there is no
lane.

## Two orderings, and they rank different things

**Most-live**, for a requirement row a person is scanning: a burning lane outranks one waiting on a
person, which outranks one resting, which outranks one still queued. **Only live words rank** —
outcomes of finished runs stay on their own rows and never climb onto the requirement's.

**Most-urgent**, for triage: `stalled` and `unshipped` first, because they are the two that need
somebody. `unknown` is last, because *"I cannot tell you"* is what a row falls back to and must
never be promoted to.

**`done` is the one state a requirement may not inherit from a single child.** It means the work is
on the base branch, and one ticket of six landing is not that. `SRV-FIX-166` is the instance:
`?filter=done` kept an epic whose other five tickets had never started, because the reduction
ranked `done` ahead of `not_started` and took the lowest. Every *other* state is still the most
urgent of the children.

## Two small ones with teeth

**`jstr` is deliberately not named `quoted`.** `<iomanip>` declares `std::quoted`, whose
non-const-lvalue overload wins by ADL for any `std::string` member of a non-const object — and it
escapes quotes but not newlines, so a `body_markdown` goes onto the wire as invalid JSON. It did,
once.

**Cost is formatted to read back as the cost that went in.** `ostream`'s default six significant
digits rounds a run's cost away, and F27 asks three surfaces to show the same number.

**The synthetic group** for tickets no requirement claims has one reserved id (`WEB-FEAT-041`). A
repository that declares a requirement with that id owns it: the document wins and nothing
synthetic is emitted, so the tool never puts two rows under one id.

## `bad_filter` passes `"null"` for its remedy, and the argument is not decoration

`renderRequirements` refuses an undeclared filter with `errorJson(..., "filter", filter, "null")`.
That fifth argument is the whole point: `remedy::member("")` emits NOTHING, so an error built
without it carries no `remedy` member at all.

§11 exempts `bad_filter` — the filter is one of five values the surface draws one control per, so
a sixth string is not a request this client can build and no action offered to this client would
correct the caller. **An exemption is a CLAIM, and the body has to carry it.** An omitted member is
indistinguishable from a code nobody has decided about yet, which is exactly the state this code
was in: it is a string argument to `errorJson` rather than an `errorCode` assignment, so §11
check 12 never saw it, and for weeks `test_spec.py` and `test_errors.py` asserted opposite things
about it — one requiring every code in a table, the other requiring this one in neither. A green
run was impossible and both reds became scenery.

`test_errors.py::test_every_refusal_check_12_reaches_is_governed_by_section_11` holds it now, and
dropping the `"null"` turns it red with *"`bad_filter` is exempt in §11, so its body carries
`remedy: null`"*.

**Both emission sites, or neither.** `bad_filter` is produced twice — `renderRequirements` for the
requirements filter and `badFilterJson` for the tickets index's `stage` and `provenance` — and one
code must have one shape. Fixing only the first left the two refusals with different key sets, and
`test_tickets_index.py::test_a_filter_value_the_repository_does_not_declare_is_refused` caught it
by comparing them to each other rather than to a literal. A shape asserted against a sibling is why
that drift could not survive one commit.

## `surveyIds` reads the epics too, or the reserved prefix is the unallocatable one

It read `tickets().documents` and every git ref's copy of that directory, and never the epics
directory. The consequence was the exact inverse of the scheme this repository runs on:

    POST /api/tickets/ids {"prefix":"FAC-FIX"}   -> FAC-FIX-364   granted
    POST /api/tickets/ids {"prefix":"FAC-EPIC"}  -> 422 unknown_prefix

`FAC-` is the epic prefix and reaches nothing else, so the one series the scheme reserves was the
one series with no ids — you could not file an epic — while a retired ticket prefix was still
being handed out, reconstructed from branches that have not merged yet.

Reading the requirements listing fixes the reserved half. The retired half fixes itself: the `FAC`
ticket series lives on unmerged refs by design — `idsOnEveryRef` exists so a lane cut at claim time
cannot reissue a number another lane already took — and it goes when those refs do.

## `E28` reads the ledger's FIRST table and nothing else

`renderEscapes` takes the header of the first markdown table in each declared `docs.escapes` file,
verbatim, and its rows in the file's order. It does not sort, rank, date or score: the file's order
is the repository's statement about its own history, and a tool that reordered it would be making
a claim of its own.

A cell carries `ticket_id` when its text contains an id the catalog resolves. The client links it
and parses nothing — a cell naming an id nobody wrote carries text and no `ticket_id`, which is
what stops a surface inventing a link to a ticket that does not exist.

**It lives in `render.cpp` and not in a `_escapes.cpp` of its own.** The api layer's rule is one
action per `_<action>.cpp`; the catalog's renderers are all here, sharing one anonymous namespace
that the convention ratchet has baselined. A new file meant a NEW anonymous namespace, which the
gate refuses — correctly, since the helpers it needs (`jstr`, `remedy::member`) are already here.

### What this endpoint found in the repository's own ledger

The file was broken in two ways at once, and both were invisible until something parsed it.

  a blank line at line 33      splits a markdown table in two. Every renderer, GitHub included,
                               would have shown two tables; the endpoint saw 18 rows.
  138 rows below `## Patterns` appended after the prose section with NO header and no separator,
                               so they were not in a table at all.

184 rows, one header, one table now. The rows have been arriving in the wrong half of the file for
weeks — every branch appends one, and nothing read the file back.

## `E27` serves the pages whole, and knows none of their frontmatter

`renderWiki` reads every `*.md` in each declared `docs.wiki`, skipping `_*` and dotfiles the way
`isDocument` does. `id` is the filename without its extension — the repository's own word and the
only name this surface has for a page. `title` is the frontmatter's, falling back to the filename.

**Every other frontmatter key is passed through untouched**, in the order the document wrote them,
as `{label, value}`. The tool renders them and understands none of them, exactly as
`display.ticket_fields` reaches `E5`'s `extras`. A key this code recognised would be a key the
target repository could not rename.

A document whose frontmatter will not parse stays in the list with `parse_error` set and
`body_markdown` intact. One unreadable file may not blank a screen (§5), and the page is still the
thing the reader came for.

## Two facts git does not carry, and where each is read (`SRV-FEAT-001`)
`E30` grew a per-ticket array so the client can compute `docs/product/SPEC.md` §3.2b's nine cells from one read.
Four of the six axes those predicates need already existed — `lane`, `process` and `role` come off
the lane holding the ticket, `delivery` off git. The other two are written by a person into a
document, and §3.5b is the specification; this is why the code looks the way it does.
**The verdict is a pair, and neither half would do alone.** `hasVerdict()` answers only *does a
verdict document exist beside this ticket's report*, and `render` combines that with the document's
own `status:` claim: `pass` or `fail` when both hold, `null` otherwise. Reading the claim alone
would put a `QA failed` cell on a word the implementing session could have written about itself —
the whole reason §3.5 refuses to trust `status:`. Reading a word out of the verdict document
instead would put a repository's prose inside the tool, which is `S1` going the way `docs.wiki`
exists to stop. **The tool reads nothing out of the verdict document; its existence is the whole
contribution**, exactly as a report's existence is the whole of `claimed` in §3.4.
`beside_()` is `reportPathFor` generalised over the suffix, because `-qa` is now the second
`<ticket-id>-` name the tool knows. A THIRD is the point at which these become a manifest key
rather than constants: two is a pattern, three is a directory layout the tool taught itself.
**The ledger is matched, never parsed.** `ledgerNames()` reads the file `docs.escapes` names —
optional, no default, resolved in `manifest` beside the three directories — takes the FIRST
markdown table in it, and returns every id-shaped token in its cells. `render` then intersects that
set with the ids it already holds. The tool therefore never learns which column means what, which
is the sentence `E28` is written on; a token naming a ticket nobody has matches nothing, and a
repository that declares no ledger produces an empty set rather than a special case. The first
table only, because a document may hold others and the ledger is the one it opens with.
`ledgerNames()` is a read per request, like every other document read here (`S2`). It is a file of
a few hundred rows and an index over it would be the second source of truth this module is built
not to have.

## `owner` — the same lookup, and the seam that lets a test drop it
`ownerOf` reads `authors ? authors(id) : std::string()`, so **an absent `AuthorLookup` and one that
answers `""` are the same code path**: both fall through to an `owner` frontmatter key, and then to
no `owner` member at all. `api::authorLookup(snapshot)` builds one lookup and `E5` and `E29` are
handed the same one, which is why no repository, branch layout or probe state can make one endpoint
name an author while the other does not. The asymmetry `SRV-FIX-377` fixed lived in C++ argument
lists and nowhere else.
That is a problem for the check rather than for the product. `SRV-FIX-377`'s mandatory negative was
named *the agreement case fails when E5 drops the lookup* and could only be produced with an editor
and a rebuild, so it was written to assert something else and stayed green in the condition its own
name described (`docs/operations/ESCAPES.md`, 2026-09-04).
`SRV-FIX-379` gives the suite the experiment instead. `_ticket.cpp` renders `E5` with an empty
`AuthorLookup` when **both** `FACTORY_TEST_HOOKS` and `FACTORY_TEST_TICKET_WITHOUT_AUTHORS` are set
and non-empty — the repository's existing test-hook gate plus a knob of its own — and logs a warning
when it does. Either variable alone changes nothing, so the response a person reaches is the
response they always got, and `test_owner_agreement.py` asserts that on a server started with the
knob and no hooks. Nothing else in the ticket path reads either name, and no wire shape moved: with
the seam on, `E5` answers exactly as it answers for a ticket nobody has committed to.

## `E30.tickets` — the axes §3.2b's cells are computed from (`SRV-FEAT-363`)

One row per ticket: `lane · role · state · delivery · status · verdict · escaped`. The server never
serves the **cell** — that is §3.2b constraint 3, and the client computes the nine from these axes.
The array is built from the `views` `renderHome` already has, so it costs no second scan.

### `role` came from the wrong place twice

It was written as a literal `null`, and criterion 2 — which asserts the row's **member set** — could
not tell a constant from a served fact. Three of the nine cells read `role`, so `agent running`,
`awaiting QA` and `QA running` were unreachable while every case stayed green.

The second attempt read `roleStates`, which tracks only **auto-started** roles: an ordinary claim has
none there, so a lane a person opened still answered `null`. The answer is the lane record's own
`role`, carried on `catalog::LaneState` — the field `E9` already answers with, so the two endpoints
cannot disagree about the same lane at the same instant.

**And §3.2b named a role the tool never emits.** The table said `role == agent`; `_claim-lane.cpp:65`
defaults an unnamed claim to `run` and every other role is one the manifest declares. The SPEC was
corrected rather than the word bent in code — a target repository names its own roles, so the
vocabulary cannot live in the server.

### `escaped` needs BOTH facts, and they come from different places

§1's word is *a **passed** ticket*. Two wrong answers preceded the right one, and both were theories
about ids rather than readings of the document:

1. a regex, `[A-Z]+(-[A-Z]+)*-[0-9]+` — this repository's id shape and not a rule. The fixture's
   `TKT-BURNT` ends in no number and was silently missed.
2. a search of the whole file — which matched `TKT-CLEAN` in a **second** table below prose. §3.5b
   makes the ledger the **first** markdown table.

It now walks the table the way `renderEscapes` walks it and matches ids with the same `idNamedIn`,
so two readers of one file cannot disagree. And the ticket's own `status` must claim `pass`:
matching an id anywhere marked **188 of 378** tickets escaped on the real repository, **75 of which
never claimed pass** — including `WEB-FIX-329`, named once in a cell's prose as the ticket that will
*fix* a baseline. A ticket that never passed cannot have escaped a gate, because no gate ever said
yes about it.

### `verdict` is §3.5b's pair, executed

A `<id>-qa.md` beside the report **and** the ticket's own `status:` claiming `pass` or `fail`.
Neither half alone: the tool reads no word out of the document — its existence is the whole
contribution, exactly as `claimed` reads none out of the report — so a session marking its own work
passed still answers `null`.

## A requirement's `parent:`, and why the array stays flat (`SRV-FEAT-547`)

`document.cpp` has always read `parent` off **every** document, requirements included, and
`render` has always thrown a requirement's away. It now follows it: a requirement's `parent:` names
another requirement, resolved **by id across every declared repository**, which is the one shape
that could not be built out of a path (`S1`) and needs no index to answer (`S2`).

**The tree is links, not nesting, and that is the decision with consequences.** `requirements`
carries every requirement exactly as it did; `parent` and `children` say where each one sits, and a
surface resolves a `children` id against the same array. The alternative — an array of roots with
their children nested inside them — was refused for three reasons, in order of weight:

1. a nested child is a SECOND copy of a row that is already in the body, and two copies of one
   requirement can disagree about its counts. That is `WEB-FIX-091`'s shape, one level up;
2. `q` and `filter` narrow this list, and a nested body makes *"the row matched but its parent did
   not"* a case with no answer. Flat, the filter keeps doing exactly what it did, and a row whose
   `parent` names a row the response does not carry is drawn at the top — which is a thing a
   surface can do without knowing anything new;
3. it is what the client asked for. `WEB-FEAT-411`'s own negative is *an E3 row whose `children`
   names an id absent from the body*, which is only a sentence about a flat array.

**Two numbers, because one word cannot mean two things.** `ticket_count` is the roll-up over the
requirement and every descendant; `own_ticket_count` is what `ticket_count` meant before. Every
other aggregate on the row — `state`, `delivery`, `needs_you`, `workspaces` — is over the rolled-up
set, so `?filter=needs` and `?filter=done` weigh a requirement by everything beneath it. That is
`SRV-FIX-166`'s rule at the next level up: `done` is still *every ticket under it landed*, and the
set that "under it" names is now the whole subtree.

`E4`'s `children[]` is one level deep and reuses `writeGroups` — E4's own `groups` shape — for each
child's tickets. Reused rather than declared again: `test_requirement_page_carries_child_requirements`
asserts the entry against `GET /api/requirements/<child>` itself, so the two cannot drift. `E4`'s own
`state` and `delivery` are rolled up as well, because §4.2's row and §4.3's header are two surfaces
drawing one requirement and `F27` is that they may not disagree. `needs_you` is deliberately NOT
rolled up: it is `{count,total}` over the `groups` that page draws, and a total over tickets the page
does not carry is a claim about a collection nobody can see.

**The walk is three-colour and iterative, and both halves are load-bearing.** Iterative, because the
depth is whatever the documents say and a recursive resolve would put a repository's document tree on
the C stack. Three-colour, because a `parent:` cycle is a thing a person can write: `A→B→A` and
`C→C` are found once each, every member has its link cleared so it is drawn at the top, and one
`parent_cycle` warning names the members in the order the walk met them. A requirement hanging off a
cycle member keeps its link and still nests under it — the cycle is broken, not the subtree.

An unresolvable `parent:` is `unknown_parent` with the id and the value it wrote, `200`, and the
requirement at the top level. **`standalone` needs no special case**: the synthetic bucket is not in
the requirement listing, so a requirement naming it fails to resolve like any other absent id — and
a repository that declares a real requirement with that id owns it, which is the rule
`WEB-FEAT-041` already wrote for the ticket bucket.

`E4` had no `warnings` member before this and has one now, built by the same `writeWarnings` `E3`
uses, so `invalid_manifest` and `path_missing` reach it too. A requirement page that could not say
why a directory was missing was the only document surface with no way to.

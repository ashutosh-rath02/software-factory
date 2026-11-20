# `tools/docgate.py` — the document gate answered as a difference

`SRV-FEAT-479`, 2026-09-09. `FAC-EPIC-374`'s parent measurement counted `doc_gate=5.9` launches
per implementing run and `2.6` per QA run, 52 s each — and the runtime was the smaller cost. The
larger one is that the verdict was never in the exit status.

## Why the exit status of the gate is not a verdict

`pytest tests/ -m repository` was red on `main` on the day this was written, for six reasons no
open ticket owned. So `docs/technical/workflow/WORKFLOW.md` told every session to run it twice — once on the branch,
once on the merge-base — and compare the two failing sets **by reading**. Both halves of that
instruction are load-bearing and neither was automated: `grep -rn merge-base .claude/scripts/
server/tools/` found one hit, a permission string.

Read by a person, the comparison has a known failure mode and this repository has already paid for
it. Half these cases *enumerate the tickets they refuse*, so a case that is already red on both
sides grows a name — the branch's own — **without the list of case names changing at all**.
`WEB-FIX-328` reached `awaiting-qa` with the gate red naming `WEB-FIX-328`; `WEB-FIX-334` merged
the same way (`docs/operations/ESCAPES.md`, 2026-09-05). The set is the case names AND the ids, and the ids
are where the round's own name appears.

## The set

`parse(text) -> {node id: frozenset(ticket ids)} | None`, and `delta(base, head)`. Both pure, both
taking a string, so both are asserted directly against captures of real runs rather than against
anything this repository invented for the purpose — `docs/reports/SRV-FIX-453/` is a real pair
that added nothing and `docs/reports/WEB-FIX-370/` a real pair that added a name.

Two things the captures taught that a spec would not have:

- **Node ids are normalised at `tests/`.** One committed capture writes
  `server/tests/test_catalog.py::x` and the other `tests/test_catalog.py::x`, because the two runs
  had different working directories. Compared unnormalised, every case in such a pair is *both*
  new and gone, and the answer is noise.
- **Both shapes pytest prints are read**, `FAILED <node>` from the short summary and
  `<node> FAILED` from the `-v` progress line, unioned. The two committed pairs disagree about
  which is present, and a parser written against either one alone is right about half the corpus.

A case's ids are every `(SRV|WEB|FAC)-(FIX|FEAT|EPIC)-\d+` in its own block of the `FAILURES`
section, plus its line of the short summary. A case with no block — a collection error — has an
empty id set rather than an absent one, so it can still be reported as a NEW CASE.

`delta` reports only what `head` has and `base` does not. A case that went GREEN and an id that
disappeared are not things this branch added, and a script that reported them would be handing
back the noise it exists to remove.

## Exit 2, and why it is not exit 1

Three codes: `0` added nothing, `1` added something, `2` could not answer. The third is the whole
of the mandatory negative. `docs/operations/ESCAPES.md` 2026-08-27 is a gate that appended `GATE_EXIT=0`
eleven seconds before the run it was reporting on finished, and a reader doing exactly what the
script told them to do saw green. So a run whose output carries no pytest summary line is not a
failing set with fewer members — it is not a failing set at all, `parse` returns `None`, and the
script says which side did not finish and writes **nothing**. A log that does not exist cannot be
quoted as a verdict.

A branch with no merge-base — no `origin/main`, no `main`, or an unrelated history — is the same
answer for the same reason: the question was never asked, and *not asked* must not be reachable
from *asked and answered green*.

The summary line is not the whole of *did it finish*, and the third shape was found by reading the
parser rather than by any criterion. A run whose collection collapses prints a summary line
anyway — `185 deselected, 6 warnings, 24 errors in 5.14s` — with no `FAILED` line in it, so the
summary-line guard alone reads it as an EMPTY failing set and reports `added: 0`, exit 0, on a
tree where the gate cannot even be collected. `!!! Interrupted: N errors during collection !!!` is
the line that says the cases never ran, and it is the second half of the guard.
`tests/fixtures/docgate/collection-error-2026-09-09.log` is a real capture of that, taken by
breaking one import; the header of `test_docgate.py`'s `CAPTURES` says how to take it again.

## S7 — where the merge-base run happens

The gate clones the source into a private scratch directory with a separate Git directory, then
checks out the committed merge-base detached. No worktree registration is added to the source.
The scratch directory is removed on every exit; the operator's checkout, branch and refs are not
moved or changed. The source's exact local `main` ref is recreated only in the scratch clone, so
history checks read the same shared history on both sides of the comparison.

The linked Git context matters to existing repository assertions: a check that discovers an
enclosing repository through the common Git directory must discover the same enclosing documents
on both sides. The scratch clone carries a snapshot of those declared documents. An archive loses
that context and can make an unchanged failure look newly introduced by using a historical
evidence fallback only on the baseline. The comparison preserves those assertions; it does not
correct enclosing-repository files or substitute weaker baseline assertions.

A confined caller whose common Git directory is read-only can still clone into private scratch
without registering a worktree. If the clone or checkout fails, the gate answers exit 2 with no
comparison verdict or evidence pair.

The `repo` element in the path is not decoration. The suite resolves the repository it is checking
by walking up from `server/`, and the one committed merge-base capture that worked ran at
`rootdir: /tmp/factory/base370/repo/server`. `SRV-FIX-354` owns that resolution; this script does
not fix it, runs where it resolves today, and **prints both server directories it ran from** so a
report can say where rather than assert it.

## S2 — the two logs

With `--ticket <id>`, and only then, the raw output of each side is written to

    docs/reports/<id>/<id>-document-gate-<date>.log
    docs/reports/<id>/<id>-document-gate-merge-base-<date>.log

which is the pair layout `WEB-FIX-370` and `SRV-FIX-453` already committed by hand. Nothing else
under `docs/` is touched, and without `--ticket` nothing under `docs/` is written at all — the
script is usable from any checkout without leaving a trace in a repository it was only asked a
question about. `docs/reports/` as a whole carries 34 different spellings of that filename today,
which is what a ritual nobody automated looks like on disk.

## The rule this gate carries — `SRV-FEAT-498`

> **A ledger row's named case is an eval, and a process change is gated on the whole set.**

`docs/operations/ESCAPES.md` had 221 rows on 2026-09-11 and 117 of them named the case that closed the
incident. Nothing read that column after the row was written, so an eval an escape paid for could
be renamed, moved or deleted and the row went on claiming it — the same shape as the 2026-09-08
row, *the rule was written in three places and enforced in none*.

`test_escapes_are_evals.py::test_every_case_the_ledger_names_is_collected` reads the first
markdown table of the file the manifest declares at `docs.escapes` and requires every `test_*` a
row's **Change landed** cell names to be reachable: collected by `pytest tests/ --collect-only -q`
under `server/tests/`, or a vitest case name under the client. One exclusion, and only one — a row
whose cell names a ticket that is still `todo` or `in-progress` has filed its case and not landed
it, and that ticket id is printed per excluded row so the debt is visible on every run. No
ceiling, and no row excluded for its prose.

### What this script adds to that

**`process_paths(root)`** is the set a change to which changes how every future round is run, and
it is derived from the repository rather than typed:

| source | why |
|---|---|
| every file under `.claude/` | the skills, scripts and settings a session is actually run from |
| the backticked paths on `_TEMPLATE.md` §2's `FIRST read:` sentence | §2 sends every session to them before it does anything |
| `_TEMPLATE.md` | §2 itself — the standing process a ticket follows verbatim |
| `.factory/factory.json` | the manifest the two rows above are located through |

§2 is found the way `test_catalog.py::_section_three` finds §3, `^## 2\b` to the next `^## `, and
the sentence is `FIRST read:` up to the first backtick-then-period. A template that loses that
sentence yields no named documents, and `test_docgate.py::test_a_process_change_is_named_by_the_gate`
refuses that on this repository — an empty derivation must not be a green tick over a gate that
quietly stopped covering `CLAUDE.md`.

`git diff --name-only <merge-base> <HEAD>` against that set prints, before any verdict:

    process change: 2 files
        .claude/skills/run-ticket/SKILL.md
        CLAUDE.md

or `process change: none`. **The naming is conditional; the eval is not.** The eval carries
`@pytest.mark.repository`, so `pytest tests/ -m repository` — the command both sides of this
comparison run — reaches it on every branch. A gate that ran only when somebody had edited the
right file would be a gate that stopped running the day somebody edited a fifth kind of file.

### Why a `NEW CASE` now prints its own assertion lines

The failing SET said WHICH case, never WHY, so the operator opened a log to find out what a node
id meant. `assertion_lines()` reads the `E   …` lines out of that case's own block of the head run's
`FAILURES` section — the case's message, nothing this script composed — and prints them under the
`NEW CASE` line. It is what makes the refusal in `SRV-FEAT-498` criterion 3 legible: the ledger
row's date and the identifier that went missing are in the eval's message, so they reach the
gate's output without this script learning what a ledger row is.

### Where the eval reads its ledger from — `SRV-FIX-520`

The eval kept a root rule of its own, copied out of `test_catalog.py`: *the checkout, unless its
PARENT keeps a manifest, in which case the parent is the monorepo and this is one repository
inside it.* The rule is right. What was copied with it was one half — the rule — and not the other
— the **enumeration** that makes the rule usable. `test_catalog.py` never reads the root's manifest
alone; it sweeps `manifests_of_this_monorepo(root)`, which is why its 27 cases behind the same door
were green on the developer's checkout while the eval was red there.

Read one manifest at the resolved root and the answer depends on where the checkout sits. Inside a
lane the checkout is `.factory/worktrees/lane/NN`, its parent is `lane/`, no manifest is there and
the parent rule never fires — so GATE, QA and the handover check of `SRV-FEAT-498` all ran green,
and the case was red ten minutes after landing in the one place the document gate is run after a
merge. *A criterion that distinguishes two possible sources asserts them where the sources
disagree*, and the checkout and the monorepo root coincide in every lane.

So the eval has no root rule of its own:

    from test_catalog import MANIFEST_REL, _repo_root, manifests_of_this_monorepo

and three consequences follow from enumerating rather than reading one file.

- **The sweep is the union over every ledger any manifest in the monorepo declares.** A manifest
  that declares no `docs.escapes` contributes nothing and is not an error — the monorepo holding
  this repository declares tickets and reports and no ledger. Every line the judgement produces —
  an offender, an exclusion, a row it could not read — names the ledger it came from, because a
  row with no file beside it is a row nobody can find once there is more than one file.
- **No manifest anywhere is an error, and it is an `assert`, not a traceback.**
  `manifests_of_this_monorepo` would raise `FileNotFoundError` on a placement with nothing above
  it; `manifests_here` refuses first, saying `no manifest found`. An empty sweep is never a pass,
  and neither is a stack trace nobody can read a verdict out of.
- **The client's cases are swept per manifest base, not under the root.** On the developer's
  checkout the root is the monorepo and `client/web` is one level down, so a sweep of the root
  alone finds no vitest case and every vitest name the ledger carries becomes an offender.

`test_every_case_the_ledger_names_is_collected` prints what it decided on — the ledgers it read
and its counts — at the start of a line, so a placement can be asked which ledger it found rather
than only whether it liked it.

### What a copy of "this repository" is — `SRV-FIX-525`

The placement cases above build each placement by copying the checkout the suite is running in.
The first version copied the directory and subtracted a set of paths somebody had typed:

    NOT_COPIED = {".git", "docs/reports", "client/web/node_modules", "server/.cmake_build",
                  ".factory/worktrees", ".factory/state", "client/web/gate/shots"}

The set names `.factory/worktrees` and `.factory/state`. It does not name `.factory/runs`, where
every run the tool has ever made leaves a `stdin` FIFO; `shutil.copytree` finishes its walk and
then raises on one. A lane worktree has no `.factory/runs` of its own, so the three cases were
green in `SRV-FIX-520`'s GATE, its QA and its handover check, and red the first time they ran
where the tool had ever been pointed — twelve minutes after that round landed, for the same reason
and in the same placement it existed to fix.

**A test that copies "this repository" copies what git tracks, and the list comes from the index:**
`git ls-files -z` in the source, then the working tree's own bytes for each path it names. **A
typed exclusion list is the first way a check lies** — `docs/tickets/_TEMPLATE.md` §2, *a set you
typed is bounded by what you already thought of* — and this one was bounded by a machine with no
runs on it.

Every entry the typed set held is excluded by the derivation instead, and by the rule that put it
there rather than by a memory of it:

- **`.factory/` is what the tool writes and is not one of this repository's documents**
  (`docs/technical/MODEL.md` §2, `S2`), and `.gitignore:28` is that sentence as policy — `.factory/*` with
  three `!` exceptions, so the tool's runs, worktrees, state and `ids.json` are untracked by
  policy and the copy obeys the policy rather than restating it.
- `.cache/`, `node_modules/`, `.cmake_build*/`, `client/web/gate/shots/`, `__pycache__/`, `.pytest-tmp*/` are
  ignored build and test droppings, and `.git` is not an index entry at all.
- `docs/reports` was excluded for weight and is now copied: 115 MB of committed gate logs, which
  measures at 0.45 s and 135 MB per placement against a whole-module wall clock of 44 s. The weight
  is real; it is not worth a set that can be wrong.

Three consequences worth stating, because each is a choice:

- **The working tree, not `HEAD`.** `git archive HEAD | tar -x` would also derive from git and
  would show a placement the branch as it was last committed, not as it is being worked. A path in
  the index and absent from the working tree is skipped, so a deletion travels too.
- **A file nobody ran `git add` on does not reach a placement.** That is the property
  `test_the_copy_follows_the_index_not_the_directory` pins, and it is why a new test file must be
  added to the index before the placement cases will see it.
- **The placement with no manifest is MADE without one** — `placement(..., manifest=False)` — and
  no longer built with one and then stripped by `shutil.rmtree(lone / ".factory")`, which was the
  same defect one line further down: it removed a directory by name and took `.factory/prompts`
  with it.

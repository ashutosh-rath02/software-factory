# The handover check — the decisions behind `tools/handover.py`

`SRV-FEAT-480`. The source carries no comments; the reasoning is here.

## What it is for

QA fails 37% of the verdicts it writes, and the first blocker of a FAIL is more often about the
**shape of the handover** than about the product: a criterion naming a case nobody wrote, a
mandatory negative that is green against the implementation it was supposed to be red against, a
criterion that was never executed at all. Six recent verdicts open on exactly that.

Each of those is a fact a script can read before a session is spent on it. This program reads §1
and the logs committed under `docs/reports/<id>/` and answers three questions per case:

1. does it exist,
2. is there a committed log in which it is **red**,
3. is there one in which it is **green**.

## Why the LOGS and not the report

`docs/tickets/_TEMPLATE.md` §2 already says *validate before you hand over*, and it says it to the
session. A session reading its own report is the check `docs/operations/ESCAPES.md` is a ledger of: the
narrative and the artefact are written by the same author in the same hour, so they agree with
each other by construction.

A committed log does not. It was produced by a program, not by an account of one, and the KEEP THE
LOG rule already requires it to be there. Reading it costs a second and turns "I watched it fail"
from a sentence into a fact somebody else can check.

## The result must be on the same line as the name

This is the whole of the mandatory negative, and it is the difference between this program and a
grep. A ticket that never ran anything can still put the case name in front of a reader:

    $ python3 -m pytest tests/test_x.py::test_y -v          ← a command line
    tests/test_x.py::test_y                                 ← a `--collect-only` listing

Both name the case. Neither says what happened. A file-level match — *this log holds the name, and
this log holds the word FAILED* — accepts both, and accepts a log where a hundred other cases
failed and this one never ran. So `RED` and `GREEN` are decided per LINE, and the name is matched
with word boundaries so `test_foo` is not found inside `test_foo_bar`.

## And the marker must be THIS case's result, not a word on its line

Same line is necessary and it is not sufficient, which is what the round-1 QA verdict found (B1
and B2). A line's words are not all about what the run did:

    tests/test_cases.py::test_a_verdict_is_recorded[FAIL] PASSED     ← FAIL is a PARAMETER
    FAILED tests/t.py::test_x - AssertionError: assert 'FAIL' == 'PASS'   ← PASS is the COMPARISON
    ✓ 4.14 the gate says FAIL when a lane is gone > a_card_…         ← FAIL is the SUITE TITLE

Every one of those is a single-result line that a substring match reads as both colours, and both
directions are the direction a gate must never fail in: the first hands over a case nobody ever
watched fail, the second hands over one that never passed. The third is the same defect in the
client's spelling and it is why the rule here is **grammatical rather than proximity-based** — the
first fix for B1 and B2 asked what text sat BETWEEN the marker and the case name, which is a suite
title on a vitest line and therefore prose a person writes.

The two runners each put the result in one place, so the program reads it there and nowhere else:

* **immediately after the case's address**, separated only by spaces — `<nodeid> PASSED`,
  `<nodeid> FAILED`; the parameter list `[FAIL]` is part of the address and is skipped with it;
* otherwise **as the leading token of the line** — `FAILED <nodeid> - …`, `FAIL  <file> > …`, and
  vitest's `✓`/`×`/`✗`.

Anything else on the line is data the case was about. `test_no_result_line_in_the_committed_corpus_
is_read_as_both_colours_or_as_neither` holds this against every `*.log` this repository has
committed — 1347 logs, 53513 lines that are a real result of one of the two runners, matched by
those runners' own grammars — and asserts BOTH directions: no line reads as two results, and no
line stops reading as the one it is. The second half is not decoration. Overshooting here produces
a false `not green` about a case somebody did watch fail, and a false refusal teaches the next
session to ignore the tool.

## …and the case it is the result of is an ADDRESS, not a name

Round 3 found the same class open in two more shapes, both of which handed over a case nobody had
watched fail:

    FAILED tests/test_handover.py::test_rows - AssertionError: row 1 does not say `never red`:
      1  server/tests/test_cases.py::test_the_first_case  yes  —  green.log  never red

The leading token belongs to `test_rows`. `test_the_first_case` is quoted inside its failure
message, because an assertion in `test_handover.py` prints the rendered row it is complaining
about — so this file manufactures exactly the lines it must not misread. The rule that closes it
is ordering: **the leading token is this case's result only when no OTHER case address precedes
it on the line.** A short summary line `FAILED <nodeid> - …` names its own case first and keeps
its marker; a case quoted inside somebody else's failure message gets nothing.

    somewhere/else/test_other.py::test_the_first_case FAILED

The second shape is the path. `Pre-decided` writes a case as `path::name` and the resolver has
always honoured the path — two files may each define `test_the_first_case` and they are different
cases — while the log side matched the NAME alone, so another file's red run and green run
answered the whole handover. A mention that sits inside a `file::name` address is this case's
mention only when that file matches the criterion's, compared the way `resolve` compares: by path
parts from the right, so `tests/test_cases.py` in a log written from `server/` still matches
`server/tests/test_cases.py` on the `Evidence:` line.

Where a runner prints no address there is nothing to bind to, and the program does not invent one:
vitest's verbose line is `× 4.14 all lanes > a_card_navigates_to_its_ticket`, which names the case
and not the file. A same-named case in a different `.tsx` would still be read as this one's. That
is the reporter's grammar rather than a choice made here, and it is written down as a limit below
rather than papered over with a guess.

## Existence is asked of the tools, not of a regex

`.py` is resolved by asking pytest to collect the file. A regex over the source agrees with a
`def test_x` that pytest never collects — wrong file name, a collection error one import above it,
a class the file no longer defines — and existence is precisely the question of whether the
runner can reach it.

`.ts`/`.tsx` has no collector that can be run here (the factory's own machine carries node 12 and
no npm), so it is a regex for `it(`, `test(`, `it.each(...)(` or `describe(` and the quoted title.
The asymmetry is deliberate and it is a known limit, not a preference: if a client collector
becomes runnable, this is the half to replace.

Both are `--collect-only`-shaped questions, so the program still writes nothing. That is **S2**:
the documents on disk are the only state, and this reads them.

## Two shapes of §1, and two spellings of a case — both read off the corpus

Driving the first implementation by hand over real tickets is what produced both of these, and
neither was visible from the ticket that specified the program.

**The criteria block has two shapes.** `_TEMPLATE.md` writes it as a bullet —
`- **Pass criteria** — one block per workspace touched` — with the criteria indented underneath;
`SRV-FEAT-480` and its neighbours write it as a `### Pass criteria` heading with the criteria at
column zero. The corpus uses both, roughly evenly. A parser that reads one of them answers
*nothing to check* about half the backlog, which is the one verdict this program may never give
wrongly. So the block is found by either marker, and the criteria level is taken as the MINIMUM
indentation of the numbered lines inside it rather than assumed to be zero.

Tickets older than the `Evidence:` convention — `SRV-FEAT-001` and its neighbours — carry
unnumbered bullets and name no case. They exit 2, and that is the right answer there.

**A case has two spellings.** `test_land.py::<case>` is written as often as
`server/tests/test_land.py::<case>`. The literal path is tried first, from the root and from
`server/`; if nothing is there, the file is looked for by name under the root, pruning `.git`,
`node_modules`, build trees and `worktrees`. A false `missing` is worse than no check at all: it
refuses a handover that was correct, and it teaches the next session to ignore the tool.

The prune list is applied to the path RELATIVE to the root. A lane worktree lives at
`.factory/worktrees/lane/<n>`, so an absolute-path prune matched every candidate in a lane and the
fallback resolved nothing — in exactly the checkout every ticket agent runs in.

## Manual evidence is listed and does not block

Not every criterion is a case, and it should not have to be. The dogfood criterion every ticket in
this repository carries — *drive it in the web client as a person* — is evidenced by a transcript,
and criterion 8 of this ticket's own §1 is evidenced by the report. A gate that refused those
would refuse every ticket and be switched off inside a week.

So they print as `manual`, with their text, and they are **counted**. The count is the point: QA
reads `manual: 2` and knows exactly how much of this handover no program has checked.

## Exit 2 is not exit 0

A ticket with no `Evidence:` line at all is not a ticket that passed. Nothing was asked, so nothing
was answered, and the one thing this program may never do is let *no question* read as *a good
answer*. `0` means every case is complete; `1` means at least one is not; `2` means the question
could not be put.

## A capture is a document, and a document is the same on every machine

`test_handover.py` runs the real pytest to produce its fixture logs, which is what makes them
evidence rather than lines a session typed. It also makes them a rendering: pytest truncates its
short summary to the width of the terminal it believes it has, so

    FAILED tests/test_cases.py::test_the_first_case - AssertionError: assert 'FAIL' == 'PASS'

is that line on a wide terminal and

    FAILED tests/test_cases.py::test_the_first_case - AssertionError: assert 'FAI...

at 80 columns — which is `run-gate.sh`, every redirected run, and every CI invocation, because a
pipe is not a terminal and 80 is the fallback. The mandatory negative for *the word PASS inside a
failure message is not a pass* then stopped carrying the word PASS, and the case that guards it
went red on the machine every gate here runs on while passing on the author's. That is QA round
2's blocker, and the ticket's own subject one level up: a handover answered from a log that only
its author's terminal can reproduce.

Every capture this file takes therefore pins `COLUMNS` (`CAPTURE_COLUMNS`), and
`test_no_case_in_this_file_answers_differently_at_eighty_columns` runs every case the file
collects at 80 and at `CAPTURE_COLUMNS` and requires the two runs to agree AND to be green. The
sweep is pytest's own collection of this file, so a case added later is covered without anybody
remembering to add it, and the case names the offender rather than being told which one it is.

## What it does not check

- **That the log is honest.** A hand-written file named `*.log` satisfies it. The defence is that
  the logs are committed and QA re-runs every criterion anyway; this program moves the cheap half
  earlier, it does not replace the verdict.
- **That the green run is the current code.** A case green in a log from five commits ago still
  counts. Freshness is a different question and it needs the sha, which the logs do not carry
  today.
- **That two files with the same basename are told apart when the log carries only the basename.**
  `same_file` compares path parts from the right over the SHORTER of the two spellings, so a log
  written from a different root — `test_cases.py::test_x` against an `Evidence:` line reading
  `server/tests/test_cases.py::test_x` — still matches, which is what real captures look like and
  what `test_a_case_spelled_without_its_directory_still_resolves` exists to protect. The price is
  that a log naming only the basename cannot distinguish two `test_cases.py` in different
  directories. Refusing those would over-refuse every log this repository actually commits, and a
  false refusal is the failure mode that teaches the next session to ignore the tool.
- **That a vitest line is about the file the criterion names.** vitest's verbose reporter prints
  the case and its suite, not its file, so the address binding above has nothing to work with on
  that line and the name alone decides it. Two `.tsx` files defining the same case name would be
  read as one. pytest lines carry the address and are bound to it.
- **The static half.** `SRV-FIX-434` adds case existence to the document gate, over every ticket at
  once. That stays where it is: this program is what a session runs about ITS ticket, at the
  moment of handover, and it is the half that can ask the logs.

## Capturing a client case green

vitest's default reporter prints a per-case `✓` only for a slow test; a green client suite
otherwise reports one line per FILE. A client case evidenced by a log therefore needs the run
captured with `--reporter=verbose`, or its row reads `not green` and says so truthfully — no log
committed here shows it passing.

## Two ways the check said yes (`SRV-FIX-494`, `SRV-FIX-496`)

Both reach the same place — `manual`, the one status that by `SRV-FEAT-480` §1 criterion 5 does not
block — and both are about a criterion that names no case the check can look for.

**A ticket that names none at all.** The tool already refused a ticket with no `Evidence:` line —
*the question cannot be answered*, exit 2. A ticket whose every `Evidence:` line is a sentence is
the same condition reached the other way, and it exited 0 over a reports directory holding zero
logs. The rule is read from the counts the tool already computes: `complete + missing + never_red +
not_green == 0` means nothing was looked for, so the answer is exit 2 and the message names the
criteria that carry no case — what to write, not what to delete. `manual` beside a real case is
unchanged.

**A node id broken across two lines.** `evidence()` joined the criterion's words with single
spaces, so a wrapped `…test_x.py::` / `test_gone` became a pair the pattern cannot cross, and the
handover passed naming a case that exists nowhere. The break is closed before the whitespace is
collapsed, in both spellings: a `::` that ends a line, and a path that ends one with the `::` on
the next. Prose is untouched — a sentence naming a real path with no `::` is still prose, and a fix
that widened the pattern until every mention of a `.py` file became a case would refuse the
dogfood criterion every ticket here carries.

**A criterion may be labelled by its cluster.** A ticket that folds many tickets writes `A1.`,
`A-NEG.`, `B2.` where a single-subject ticket writes `1.`, and the item pattern read only digits —
so `handover.py` answered *nothing to check* about the two such tickets in this repository, which
is a refusal, but a refusal about the wrong thing. The label is read as written and printed as
written; it is a name, never a number.

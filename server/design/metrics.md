# Metrics — the playbook's numbers, computed when asked

`docs/product/SPEC.md` §4.21 and §5 `E32`. `FAC-EPIC-375` §1.4. The module answers one question: **how is this
repository's process doing over the last N days** — first-pass QA rate, rounds per ticket, minutes
per stage, hours from the first stage to the landing, escapes filed, and tickets whose §1 was
edited after work started.

Everything it reads already exists. `app/metrics` holds no state, opens no file and starts no
process: it is a reduction over values `api/_metrics.cpp` hands it, and `api` is the only module
that composes (`docs/technical/ARCHITECTURE.md`). That is the whole reason the module is a pure function — a
metric that kept an index would be a second answer about the repository, and `docs/technical/MODEL.md` `S2` says
there is only one.

## Why the script is the arbiter

`docs/reports/FAC-EPIC-374/measure-rounds.py` computed these numbers first, by hand, once per
epic. It is a 60-line script somebody ran and pasted into an epic, and it is the only recorded
definition of *active minutes* and *stage minutes* this repository has ever had. A C++
re-implementation checked against a test the same session wrote would agree with itself and with
nothing else — `_TEMPLATE.md` §2's second way a check lies, and the reason `SRV-FEAT-009`'s eight
green criteria described a session that could only ever have one turn.

So the script is a **recorded arbiter**: `test_metrics.py` builds a fixture of run records, runs
the script over it as a subprocess, parses its stdout, and requires the endpoint's numbers to
equal the script's field by field. The script is never edited to agree with the endpoint. If the
two disagree, the endpoint is wrong until a ticket says otherwise in this file.

Two definitions are copied from it verbatim, so neither can drift without this paragraph being
edited:

- **Active seconds** are the sum of a run's `turn` durations — the agent working. Never wall
  clock: a session keeps its seat after its turn ends, and the difference between the two on this
  repository's own history is a factor of three.
- **Stage seconds** are the gap between one `stage` frame and the next in the same run. The last
  stage a run reported runs to that run's last working entry, where *working* excludes `turn` and
  `silence` — the script's own two exclusions, for the script's own reason: a turn boundary is
  bookkeeping and a silence is the absence of work, so neither is evidence that the session was
  still in the stage.

Three of the script's filters are copied with it and are worth naming, because each one is a
place the two could silently diverge:

- **`role` is `run` or `qa` in the script; here it is *declared* or *not*.** The script names the
  two roles this repository happens to have. The tool may not (`S1`), so `stage_minutes` and
  `active_minutes` reduce the runs **nobody declared a role for** — `E31`'s `kind: run` — which is
  the same set for any repository whose only role is a reviewing one. `test_metrics.py`'s fixture
  declares exactly the roles the script knows, and says so, because a fixture with a third role
  would make the two populations differ for a reason that is not a defect.

  **A record's `role` field is not that question, and the difference cost this ticket a round.**
  `agent::history::write` puts `"role":"run"` in the meta of an ordinary run lane, so *the key is
  absent* and *this is not a role run* are different facts and the second is the one that matters.
  `E31` already had the rule — an undeclared name resolves to the fallback `run` — as a private
  static beside its handler; `E32` wrote it a second time as `!role.empty()` and was `null` for
  every series over three real runs while every case in `test_metrics.py` was green, because the
  fixture planted the key absent. The rule is now `api::runKind` / `api::isRoleRun` in
  `app/api/lane-view.cpp`, called by both, and the fixture writes `role: "run"` the way the tool
  writes it. One definition, and a fixture built from what the product produces rather than from
  what the test found convenient.
- **`active > 0`.** A run that recorded no turn duration recorded no work, and the script drops it
  from both series. So does this.
- **`--min-s`.** The script drops runs shorter than ten minutes by default; the endpoint has no
  such knob and drops none, because a window is a question about everything that ran in it. The
  test runs the script with `--min-s 1` so its filter is provably inert, and the fixture's runs
  are all longer than that. A comparison taken with the arbiter filtering something the endpoint
  keeps would be a comparison of two different populations.

## Why every rate is `null` on nothing measured

`0` and `null` are different facts and a screen cannot recover one from the other. `0` means
*measured, and none qualified*; `null` means *nothing was measured*.

`first_pass_qa_rate: 0` says *tickets reached QA in this window and none of them passed first
time* — a process in trouble. `first_pass_qa_rate: null` says *no ticket reached QA*, which is a
repository that has been quiet. Rendering both as `0%` puts a red number on a screen about a month
in which nothing happened, and the person reading it cannot tell which of the two they are looking
at without opening the run directory themselves.

So every rate — `first_pass_qa_rate`, `rounds_per_ticket.median` and `.mean`,
`active_minutes.median` and `.mean`, `spec_to_land_hours.median`, `rework_after_build.share` — is
`null` when its denominator is zero and a number when it is not. Counts (`runs`,
`tickets_with_verdict`, `escapes_in_window`, `rework_after_build.tickets`, every `n`) stay `0`,
because a count of nothing really is nothing.

`stage_minutes` carries the same rule as a shape rather than a value: a stage nobody reported has
**no row**, rather than a row with `n: 0` and two nulls. A row is a claim about a collection, and
a claim about a collection with no members is the empty-state defect `WEB-FIX-088` cost.

## The denominator is served beside the rate

`tickets_with_verdict` is not decoration and it is not a second count. It is
`first_pass_qa_rate`'s own denominator — the tickets a verdict was RUN for, those with at least
one role run inside the window — and it is in the response so that §4.21 can draw the rate beside
the population it is a share of.

This repository has paid for the alternative twice. `WEB-FIX-091` drew `RUNS · 4` above one row,
because the count came from one collection and the rows from another; `WEB-FIX-002` drew *"No
requirements in this repository."* under a condition computed entirely from tickets. Both are two
correct facts forming a false sentence. A rate whose denominator is not on the wire is that shape
waiting for a surface.

A ticket whose role run left **no verdict document** is in the denominator and not in the
numerator. *Nobody wrote the verdict down* is an outcome of a QA round, not the absence of one,
and dropping it would make the rate improve every time a session died before it could write.

## What a landing is, and why it is not `docs/`

`spec_to_land_hours` ends at the commit `test_main_history.py`'s derivation finds: on the base
branch, one commit per ticket, subject prefixed with the ticket id, touching **at least one path
outside the documents**. That test spells the category as the literal `docs/`; the tool may not
(`S1`), so the exclusion here is *every path the manifest declared as documents* — each
repository's `docs.epics`, `docs.tickets`, `docs.reports`, `docs.wiki` and `docs.escapes`.

For this repository the two are the same set, because `.factory/factory.json` declares
`docs.wiki: "docs"`. For a repository that declares narrower document roots the tool's category is
narrower than the test's, and the difference is a commit touching a file under `docs/` that no
declared root covers. That is stated rather than hidden: the tool's word is the manifest's, and if
a repository wants a directory treated as documents it declares it.

The window bounds each series by its own clock — the runs by `started_iso`, the ledger rows by the
date in the row, the landings by the commit date — because those are three different questions and
a single cutoff over one of them would silently answer the other two about the wrong month.

## Why it is on a worker

The walk reads every run's `transcript.jsonl` and asks git for the base branch's commits. Both
wait, and `core::Engine` runs four threads on one `io_context` — blocking one of them stalls
everything queued behind it (`SRV-FIX-108`, `docs/technical/ARCHITECTURE.md`). `E32` therefore does what `E31`
does: it takes the delivery snapshot for the base branch through `delivery::Probe`, then posts the
whole computation to `core::Worker` and responds from there. `test_metrics.py` asserts `E1`
answers inside 200 ms while `E32` is walking 500 runs, which is the only form of that rule a test
can hold.

The per-ticket git call — *was this ticket's own document committed after its first run started* —
is made once per ticket in the window and not once per ticket in the repository, because the
denominator is the same set `rounds_per_ticket` reduces. On this repository that is tens of calls
rather than hundreds, and all of them are on the worker.

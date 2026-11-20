# `app/permissions` — the policy, the request that blocks a run, and the granted list

Decisions behind `app/permissions/{store,rules}.{h,cpp}`, moved out of the code by
`SRV-FEAT-230`.

## Three pieces, none of which works alone

`docs/product/SPEC.md` §8 D4: `Allow always` is one click, ten clicks make a policy nobody can state, and **a
policy a person cannot state is indistinguishable from `never_ask`**. So the standing policy, the
request, and the list of what was granted are one feature.

## What this module may not know

It does not know what a lane is and does not know the agent CLI's wire format. Parsing a permission
request out of a run's output belongs to `agent/transcript` (`docs/product/SPEC.md` §10). **This module owns the
DECISION**, and the join between the two is one function — `ask`. `api` composes: it hands the
frames to the socket and the blocked set to the lane renderer.

`Ask::argument` is the one string a rule is about, already extracted by `rules.h`'s `argumentOf` —
so this component never sees the CLI's input object.

## `expires_at` is null, and there is no timer in the file

A request that times out into a denial produces **a run that failed for a reason nobody saw**. The
absence of a timer is the mechanism, not an oversight.

## A denial is an answer, not a kill

`docs/product/SPEC.md` E23. The agent is told it was refused and its run continues.

## The request names the rule it would write, and the one that nearly matched

The whole reason the feature exists: an agent once waited on an approval nobody could see, for a
command a rule was supposed to cover and did not. So the request says **which rule would be
written** if you answer `always`, and **which rule nearly matched and why it did not** — a rule
that nearly matched and a rule that is missing are different problems with different fixes, and a
request that cannot tell them apart leaves the person clicking `always` again.

`always` writes the string the request SHOWED. It does not recompute it from the input: the two
could differ, and the one the person read is the one they agreed to.

The nearest miss reports the **earliest** point of divergence, because that is the requirement the
argument actually tripped over rather than one further down a branch it never reached. A rule for a
different tool is never the nearest miss. Where two are equally close, the more recently granted
wins, because that is the one a person is more likely to remember writing.

## The freeze — the fourth input to the decision

`SRV-FEAT-499`. A repository may declare, in its manifest and nowhere else, which line of a
ticket's §1 names what evidences a criterion and after which stage that evidence stops being
freely editable. Once a run has REPORTED that stage, a call touching one of those paths is
**asked**.

**The freeze is a fourth input to the decision, after the policy and before any grant, and it is
`asked` and never `deny` because the person who wrote the criterion is the one who may change
it.** `decideNow_` reads the four in that order: the policy this run captured, then the freeze,
then the manifest's rules, then what has been granted. Sitting above the grants is the whole
mechanism — `always` writes its rule, `E11` lists it, and the next touch of the same file is
asked again, so **there is no `always` for a frozen path**. Sitting above the policy is what makes
the answer the same under `never_ask` as under `ask_all`: a repository that trusts its agent with
everything else still decides this one by hand.

`deny` would be the wrong answer twice over. A refusal is not a question, and the run continues
either way — what a person wants here is the chance to say *yes, rename it* or *no, that is the
test you were supposed to make pass*, per file, on the record.

### Kept per RUN, never per lane

`frozenByRun_` is keyed by the run id and erased in `noteRun` beside `policyByRun_`. A lane name
is designed to be destroyed and reused (`SRV-FIX-247`, `SRV-FIX-256`); a run id is not. A freeze
keyed by lane would arm the next occupant of that name against a ticket it has never seen.

The consequence is deliberate and worth stating: a SECOND run in the same lane — a resume, a
restart after a crash — starts unfrozen and stays unfrozen until it reports the stage itself. The
tool enforces what it was TOLD, and nothing else; a freeze inferred from a report some earlier
process made would be the tool deciding a fact about work it did not watch.

### Which calls it reaches, and how a path is matched

The argument is the one `rules.h`'s `argumentOf` already extracts, so a `Bash` command naming the
file is reached for the same reason a `Write` at it is — the path is in the string either way, and
this module goes on never seeing the CLI's input object.

It applies to every tool EXCEPT a closed set whose whole purpose is to read. That direction is
deliberate: a tool nobody listed is frozen, which is the answer this module gives everywhere else
when it is unsure. A list of tools that DO write would have the opposite failure — the next
writing tool the CLI grows would edit a red test and nobody would hear about it.

The match is normalised path containment: the argument contains the path as a whole path-component
sequence. The character before must be a boundary and the character after must not continue a
filename, so `server/tests/test_x.py` is found inside an absolute path to it and inside a `sed`
command that names it, and is **not** found in `server/tests/test_x_helpers.py` or in a `.bak`
beside it.

### The `202` means it is in force

`E22` answers only once `permissions::Store::freeze` has completed. Responding first and freezing
afterwards would make *report the stage, then ask* a race, and a criterion about it a coin flip.

## It fails closed

A corrupt `.factory/permissions.json`, an unparseable pattern, any doubt at all, means **ask**.
Nothing that could be salvaged is salvaged: half a policy read out of a broken file is a permission
nobody granted. The file is written whole, stable order, one field per line, trailing newline — so
no reader, including the tool's own next start, ever sees half a policy.

An unrecognised `permission_mode` is not a policy. It produces a warning and `ask_all`, rather than
a guess.

## `edit_free` is the file tools and nothing else

§4.12's middle option in its own words: *"edit files freely but ask before commands."* A `Bash` is a
command however gently it is phrased.

## A run that ends VOIDS its pending requests

Without it, an answer arriving for a dead run would write a standing permission nobody meant to
grant, and answering into a dead run would be a silent success instead of `410 gone`.

## The four events append (SRV-FEAT-230)

`onRequest` / `onResolved` / `onRules` / `onEntry` hold a `std::vector` and append. They used to
**replace** — `requestHandler_ = std::move(handler)` — so a second subscriber silently evicted the
first, with no compile error and no runtime error, and one screen that stopped updating.

That this had not yet bitten was luck about registration counts, not a property anything enforced:
`catalog::Store::onChange` is registered twice today and works only because it happens to be the
append kind. `api::Manager::wireLaneState_` carried a comment routing around it — the `blocked`
edges were wired in `wirePermissions_` rather than beside the other lane-state edges, *because*
these four held one handler each.

`SRV-FEAT-228` proposed a general event registry for this and was declined. The content of it was
four lines.

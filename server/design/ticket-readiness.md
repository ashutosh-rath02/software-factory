# Repository readiness and recorded causes

`SRV-FEAT-562` separates runtime startup from repository knowledge. The optional manifest
`start_preflight` command runs at the confined boundary before an agent starts. The repository
chooses its checks; Factory owns reservation, timeout, cleanup and the refusal response.

`tools/ticket_readiness.py` keeps a version-1 JSON registry. A cause has a stable reviewed ID,
workspace and profile selectors, owning fix tickets, deterministic probe argv, incident references
and evidence references. Repeating a cause unions the incident and evidence references. A changed
probe, scope or owner is a conflicting definition that requires triage. Locked atomic updates keep
concurrent incidents from overwriting one another.

`record` adds an open cause. `dedup` previews that operation. `check` executes applicable probes
again, writing complete stdout and stderr captures. Open causes block even when their current probe
passes; a cheap probe cannot approve the fix. A named repair ticket bypasses only its own open
cause. No applicable causes is an explicit empty result, not a claim that a probe passed. Missing
or malformed input and unavailable or timed-out probes are errors. Probes use their own process
groups; timeout kills the group and reaps its leader rather than leaving children running.

`resolve` requires typed RED, GREEN, negative and QA receipts naming the same cause and candidate
artifact hash. RED and negative receipts may measure deliberate reversions of that candidate.
Every receipt names a nonempty complete capture; the registry retains its hash and the receipt's
hash. The QA receipt attests an independent session, but this command does not establish that
session's independence or replace the Factory verdict and landing checks. Resolved causes keep
running their probes; recording a new incident reopens the cause and preserves prior resolutions.

Exit 0 means ready, an explicit unrelated scope, or a successful registry operation. Exit 1 means
blocked. Exit 2 means the question could not be answered. The JSON result distinguishes these
states. Provider calls and full browser gates belong in the ticket's validation, not start probes.

The example registry at `docs/operations/readiness/workflow-causes.example.json` records the missing confined Codex
helper. It is deliberately an example: the active manifest has no preflight declaration yet, and
older worktrees do not acquire the new tool or registry from this checkout's uncommitted edits.
Rollout must make the declared command and its registry reachable in each target worktree before
enabling the start check. Fix scheduling, independent QA, landing and resuming dependents remain
separate workflow actions.

Shared learning uses one canonical registry owned by the coordinating checkout. Declare its exact
file as a workspace toolchain path and let the preflight read that absolute path. The existing
read-only file binding makes the latest record available on the next start even when the lane's
branch has an older registry. It gives the lane neither write access to that registry nor access
to adjacent files. The host coordinator performs `record` and `resolve`; a lane supplies its cause
and evidence for that operation. Record which policy version was deployed, and keep the tool
version compatible with the registry. Binding a whole checkout or developer directory to share
one file would remove the boundary this workflow relies on.

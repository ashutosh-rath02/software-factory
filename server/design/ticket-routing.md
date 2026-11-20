# Local ticket routing — contract frozen 2026-10-04

SRV-FEAT-565 implements the local JEV replacement as an explicitly requested sequence of declared
workspace commands in one existing lane. It does not install JEV, call a model for routine decisions,
start helper roles, infer acceptance, edit ticket/report documents, or move repository refs. Model
profiles, enforced helper isolation, full-session advisory execution and the complete automatic
agent tree remain separate work. This is a local command controller, not a global ticket scheduler.

## HTTP contract

E33: `POST /api/lanes/{lane}/routing`. A lane such as `lane/01` uses the same segmented-path support
as E19. Strict JSON body:

```json
{
  "request_id": "build-gate-1",
  "steps": [
    {"which": "build", "accepted_exit_codes": [0], "reuse": true},
    {"which": "gate", "accepted_exit_codes": [0], "require_decision": true}
  ]
}
```

`request_id` is 1–64 ASCII letters, digits, underscores or hyphens. There are 1–16 steps. Each
`which` names an existing command field declared by this lane's workspace. Accepted exit codes
are a nonempty unique set of integers in 0–255. `reuse` and `require_decision` are optional booleans,
both defaulting false. Unknown body/step keys and duplicate JSON members are refused. Main chooses
the command order and exit policy; the controller never invents a stage, tool or role name.

A new request returns 202; an identical retained request returns 200 with its current state.
Same ID with different steps returns 409 `routing_conflict`. Another active routing request for
that lane returns 409 `routing_busy`. A live process or queued resource returns the existing
409 `wrong_state`/`resource_busy` without ending it. Missing lane is 404 `not_found`. A malformed
body or unsupported command is 400 `bad_request`/`bad_which`; an undeclared command is 404
`not_declared`. Unreadable source or evidence returns 409 `routing_unresolved`. No command starts
until the whole plan, source identity and durable dispatch intent are valid.

Transient routing/command/resource busy and missing-target refusals offer the existing explicit
`retry` remedy. It reissues the caller's request after its holder finishes or the missing target
is established; it does not end the holder, free its resource or replay anything automatically.
Undeclared commands offer `reload_manifest`. Malformed callers, immutable plan conflicts and
unresolved source/evidence/claim diagnostics retain null with §11 governance. Their correction
requires main/operator judgment; a remedy cannot overwrite retained history or bless uncertain work.

E34: `GET /api/lanes/{lane}/routing/{request_id}` reads the retained job; 200 when it exists, 404
`not_found` otherwise. `?include_output=true` includes each capture as `output_base64` without
clipping; other values are 400 `bad_request`. The GET never dispatches work. Missing/damaged
receipts/captures make the job unresolved instead of advertising reusable success.

Inspection and duplicate delivery require the retained ticket, workspace and incarnation to match
the current lane claim in every state. A mismatch returns 409 `routing_unresolved` without exposing
captures. A failed request remains inspectable in its original claim, including across restart.
Well-formed records from older claims still load as history for validation of a new unique request;
claim matching is an inspection boundary, not a rejection of the lane's historical journal set.

Both success bodies use this shape:

```json
{
  "request_id": "build-gate-1",
  "lane": "lane/01",
  "ticket_id": "TICKET-1",
  "source": "sha256-of-git-visible-inputs-and-effective-manifest",
  "state": "running",
  "action": "wait",
  "reason": "command_running",
  "next_index": 0,
  "receipts": [],
  "checkpoint": null
}
```

States are `running`, `completed`, `needs_main`, `interrupted`; actions are `wait`, `main`, `advisor`.
Completing the command sequence returns `completed/main/commands_completed`: main still reviews
and follows independent QA. An accepted step with `require_decision` returns
`needs_main/main/decision_required` and starts no later step. Unexpected exit returns
`needs_main/main/command_failed`; two consecutive actual failures with identical source, command,
exit code and full capture digest create one stable `repeated_error` checkpoint and action `advisor`.
This records a request for advice, not an advisor invocation. Repeated reads/deliveries retain the
same checkpoint ID and create neither a new command nor another checkpoint. No automatic retry.

Each receipt contains `index`, `which`, `command`, `command_id`, `source`, `source_after`,
`exit_code`, `capture_path`, `capture_sha256`, `bytes`, `accepted`, `reused`, `complete` and optional
`output_base64`. A complete zero-byte capture is valid. A checkpoint contains `id`, `kind`, `cause`
and `receipt_count`, binding its cutoff to the retained completed receipts.
If terminal inputs cannot be observed, `source_after` is an empty string rather than a fabricated
digest. That representation is valid only on the final nonreused receipt of
`needs_main/main/source_unresolved`; the complete capture and actual exit remain available to main
once current inputs can be read. It cannot be an earlier, running, completed or reused receipt.
Repairing the current inputs allows inspection and a new unique request, including after restart,
without replaying the unresolved attempt or treating it as cacheable success.
Existing M10 command frames stream routed executions through the normal lane/ticket subscriptions;
there is no new socket frame or frontend polling.

## Evidence and effects

`manifest` owns the joined runtime path `routingDir` under its existing runs directory:
`.factory/runs/.routing`. The routing component owns its journals/captures and one `core::Queue`;
filesystem and Git work run off that queue through workers. `api` composes lane observations,
read-only source fingerprinting, existing E19 execution and socket publication. No sibling module
reads another's internal storage. SHA-256 uses OpenSSL Crypto.

The source identity binds lane/ticket/workspace/branch incarnation, HEAD, sorted complete Git index
stage entries (mode, object id, stage and path), the sorted Git-visible tracked and nonignored
untracked paths with file type, executable bits and exact bytes, the
instance's effective manifest, and the current manifest file bytes. Git commands disable optional
locks and external diff/text conversion; source fingerprinting never refreshes the index.
A missing tracked file has a distinct deletion marker. Symlink declarations are represented by
their literal target bytes. Ignored build/runtime output and external environment state are outside
this identity. Reuse is explicitly opted in only for commands whose deciding inputs are covered;
hardware, time-dependent gates and external services should keep reuse disabled.

Recheck inputs immediately before dispatch and after a terminal result. A changed source stops the
sequence with `needs_main/main/source_changed`. Reuse requires a fully validated complete accepted
receipt, matching source, exact command and exit policy, and a readable capture with matching hash
and length. Missing, corrupt or contradictory evidence stops; it is never repaired into success.
A reused receipt does not execute a process. Operator checkout state and refs remain untouched.

Persist intent before executing a command, serialize each lane's admission, and retain complete
stdout/stderr. New processes use lane confinement and existing resource ownership. An ordinary
command remains a working resource holder for automatic role handoff from launch reservation
through actual terminal exit, even after a prior agent turn ended or its output closes. Explicit
operator force-release retains the existing lane release lifecycle. A terminal
capture and receipt are persisted before advancing. Never use output text, reported progress or
an early PASS line as a terminal result. No clipped capture qualifies as complete.

On restart, a persisted nonterminal dispatch becomes `interrupted/main/interrupted`; the same
request is not replayed. A new request requires main to reconcile the prior attempt and the lane
to be free. Old callbacks are bound to the current request and step and cannot advance another.
Retained completed jobs replay across restart only if current source and evidence still agree.
No claimed exactly-once guarantee spans a crash between dispatch and durable completion.

## Read-only input adapter

The API calls the source fingerprint on its component queue's worker offload and supplies a
read-only Git runner there. The helper enumerates only verified HEAD/ref and Git-visible paths;
it never runs status, refreshes the index or executes a repository command. Literal symlink targets
are inputs, external targets are not followed, and unsupported gitlinks or redirected source
ancestors refuse reuse before dispatch. This explicit worker boundary keeps blocking Git work
visible to the convention gate without expanding its baseline.

Each scan hashes the complete sorted `ls-files --stage` entries, so changing only a staged blob,
executable mode or merge stage invalidates prior evidence even when working bytes and HEAD stay
equal. The final Git pass must retain the same HEAD, symbolic ref, path set and index entries.
After that pass, the helper walks every observed path again from the open worktree descriptor,
refusing symlink ancestors and requiring the same file identity, type, mode, size, modification and
change timestamps, or continued tracked deletion. This catches earlier-file changes discovered
after later files and Git entries have been read. These checks establish observed scan consistency;
they do not create an atomic filesystem snapshot or prevent changes after observation. Dispatch
and terminal-result checks still perform their separate full scans.

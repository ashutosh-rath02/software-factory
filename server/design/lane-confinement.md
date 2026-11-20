# `lane/confine` — the boundary, and the two namespaces it lives in

## Child environment exclusions

A launch may pass explicit environment names to remove as well as values to set. Confinement
translates exclusions to `bwrap --unsetenv` for that child, after the normal environment overrides.
This prevents a legacy tree-off agent from inheriting the server's selected helper membership or
decider while leaving other inherited values intact.

## Explicit executable companions (SRV-FIX-560)

Absolute declared commands use their basename for the projected executable alias. When an explicit
argv begins with a declared host executable hidden by the namespace, confinement substitutes that
alias. A reachable original executable keeps its original path and package-relative lookup.

An executable file explicitly declared in the workspace toolchain and located beside a declared
agent binary is also projected read-only into `/factory/bin` by basename. This preserves the
relative executable lookup used by packaged agents when commands select their projected alias.
Directory declarations do not enumerate or project their contents. Undeclared adjacent files
and credential directories acquire no additional bindings. Existing projected names take
precedence, so a companion cannot replace another declared agent.

The installed Codex CLI looks for `codex-code-mode-host` beside its executable. The active
manifest declares that exact companion file; the engine contains no provider-specific companion
name. The [official app-server documentation](https://learn.chatgpt.com/docs/app-server) describes
the default local Code Mode host. The installed CLI and actual confined RED/GREEN captures,
rather than a configuration guess, determine the local executable requirement.

`SRV-FEAT-002`. A lane is a `bwrap` mount namespace: the worktree and four git directories bound
read-write, `/usr` and `/etc` read-only, everything else absent. What is written down here is the
part that is not visible in the argv — why the policy holds two kinds of path, and why a boundary
also has to be *reachable*.

## Two namespaces in one `Policy`

Every path in a `Policy` is one of two things and they are never interchangeable:

- **host paths** — the worktree, the git directories, the lane's agent-configuration directory,
  the read-only binds, the declared toolchain's `host` side. These exist before the lane and are
  bound into it. `Policy::writable` is the host ones a lane may write.
- **in-sandbox paths** — the home and scratch directory the confinement *synthesises*, its
  private runtime socket directory, and `/factory/agent-config`. They have no host side at all;
  they are `--tmpfs` and `--bind` destinations. `Policy::writableInside` is those.

They were one list until round 3 of `SRV-FEAT-002`, and both comparisons made against that list
were therefore comparing across namespaces. On a developer workstation the two never collide, so
nothing said so. When the factory is pointed at itself from inside one of its own lanes — which is
where every ticket agent now runs — they always do, and the ancestor guard refused every lane with
*"writable path /tmp/factory is above the repository root"*.

## A private runtime socket mount for nested sandboxes (SRV-FIX-560)

The installed native Codex Linux sandbox uses `/tmp/codex-daemon-<uid>` for its socket bootstrap;
its [upstream implementation](https://github.com/openai/codex/blob/main/codex-rs/uds/src/daemon_directory.rs)
fixes this location independently of `HOME`, `TMPDIR`, `CODEX_HOME`, and command settings. The
Codex launch adapter declares the concrete path as a runtime requirement. The manager and store
carry that generic runtime-directory list to confinement; `lane/confine` contains no driver
branch or provider path. It accepts only normalized direct children of `/tmp` that do not overlap
the worktree, Git, scratch, agent configuration, toolchain, or read-only host binds.

Before the outer confinement remounts `/tmp` read-only, it creates each accepted directory in its
already private empty `/tmp` tmpfs. After the remount, it mounts an independent tmpfs on that exact
path with mode `0700`. The rest of `/tmp` stays read-only. The socket directory has no host bind
or shared bytes; each lane gets its own mount namespace. Other agent drivers receive no Codex
runtime mount. The invented scratch directory remains a separate tmpfs.

The exact no-model native startup command returned a filesystem error before this mount and exited
zero afterward. A second native read-only probe read Git and the cited policy while a write to the
lane worktree failed with `Read-only file system`; an unbound host-temp sentinel was absent and
source bytes were unchanged. The full isolation floor caught and rejected a broader writable
`/tmp` variant because it allowed writes into shadow directories representing the hidden main
checkout. The narrow mount preserves those refusals. Captures are under
`docs/reports/FAC-EPIC-378-closeout/l2-gate-final/review-runtime-*.json`.

## The synthesised paths are invented, not named

`HOME_INSIDE` and `SCRATCH_INSIDE` are the *first candidates*, not the answers. `invent()` walks
`/home/lane`, `/home/lane2`, … until it finds one that neither contains nor is contained by any
host path this policy has to carry in — and, for the home, is not the home the process building the
policy is itself using.

The reason is not tidiness. A `--tmpfs` over a directory that already exists does not remove it, it
*shadows* it: the lane gets a fresh writable directory at that path, and a write the lane aims
there succeeds instead of failing. `SRV-FEAT-002`'s standard is that a refusal which succeeds
somewhere harmless is not a refusal, so the path has to be one nobody else owns. The same rule
stops `--tmpfs <scratch>` mounting over a worktree that happens to live under it.

`FACTORY_LANE_HOME` is set inside every lane for the same reason. `developerHome()` answers *which
home a lane may not be given*; with no `HOME` in the environment it used to fall back to the passwd
database, which knows nothing about lanes and names the workstation's home — so the guard that
refuses a writable directory inside the developer's home failed open for a factory running inside a
lane. The confinement therefore tells the processes it starts what home it invented for them.

## A bound toolchain is not a reachable one

`PATH` is built by the confinement, never inherited. Three sources, in order:

1. `/factory/bin`, holding one projection for every declared agent command, under its declared name.
   When the resolved executable is already visible at its original path through an existing
   read-only directory mount, the projection is a symlink. This preserves executable-adjacent
   helpers and resources without adding any bind or exposing any additional directory. A bind of
   just the executable would relocate `/proc/self/exe` to `/factory/bin` and break that lookup.
   Otherwise the projection remains a read-only bind of that exact file: an unselected profile's
   sibling files are not made visible merely to make its command resolvable.

   Reachability is about the final namespace, not only an earlier bind. Original paths under
   `/tmp`, `/proc`, `/dev`, the synthetic home/configuration, the worktree or Git metadata, or a
   differently mapped toolchain are not used as symlink targets. Later mounts can hide or change
   those paths. Existing `/factory/bin` targets also retain file projection, preventing self-links
   or projection chains when a confined caller supplies the binaries. Declared symlinks are
   resolved before this decision; a link out of a mounted bundle does not expose its target's
   directory. The aliases themselves remain on the read-only namespace root.

   The selected profile alone receives its generated configuration and borrowed credential file;
   making the other declared executables resolvable does not expose their credentials. The
   binaries' directories had been read-bound since `SRV-FEAT-039`, but only the selected CLI was
   projected by name, so a nested factory refused a valid unselected profile with
   `agent_binary_missing` before it could claim a lane.
2. every declared toolchain directory that holds an executable, plus its `bin/` if it has one. The
   *declaration* is the reason it is on `PATH`. Deriving `PATH` only from the inherited one works
   one level down and drops the whole toolchain two levels down, because a lane's `PATH` has no
   home directory on it to translate.
3. the inherited `PATH`, with entries under the developer's home rewritten under the lane's home,
   and every entry that does not exist inside the namespace dropped. This is what carries a deep
   toolchain the developer's shell already names — `~/.nvm/versions/node/<v>/bin` and its like.

Then the system directories, always.

Everything else is dropped, and that is the load-bearing half: the cheap way to make a gate run
inside a lane is to hand it the developer's `PATH` and home back, which would pass every
reachability case in `test_isolation.py` and undo the ticket.
`test_a_lane_cannot_reach_a_program_its_workspace_did_not_declare` is the case that decides it.

## What a refusal is

`inspect()` reads the child's own stderr, because that is the only place the tool learns that a
process was refused — a command whose stderr is captured is a refusal nobody can report. A line
carrying `EROFS`, `EACCES` or `ENOENT` and a path outside every writable list, host and
in-sandbox, becomes an `M9 isolation` frame naming the lane, the operation and the path tried.

## The developer's home is not `$HOME` when the tool is itself inside a lane (`SRV-FIX-466`)

`developerHomeImpl` read `$HOME` first. That is right on a workstation and wrong in the one place
every ticket is actually worked: inside a lane `$HOME` is `/home/lane`, the writable tmpfs this
module mounts, so `realHome` — and therefore `hiddenHome`, which decides whether a refusal is
reported at all — pointed at the sandbox rather than at the home the sandbox exists to keep out of
reach. `'/home/lane' != '/home/lane'` was the whole defect in one assertion, and with it the
isolation floor `CLAUDE.md` makes unconditional could not give a verdict where tickets are run.

So `$HOME` is consulted only when it is not one of this module's own inventions: if it equals
`HOME_INSIDE` or `$FACTORY_LANE_HOME`, the process is in a lane and the passwd entry for the uid is
the answer. `/etc` is carried in read-only and the uid is not remapped, so that entry reads the
same inside a lane and outside one — which is the property the floor needs and `$HOME` does not
have.

The homes a lane may be GIVEN are a separate question, and the same change exposed it: `invent`
avoids every path the policy has to carry in, and it now also avoids the home, the lane home and
the temp directory **this process** is using. A nested lane must not be handed the home its parent
is standing in, or the child's writable tmpfs lands on top of the parent's home and every write the
child aims outside itself succeeds.

## A toolchain may not name a credential (`SRV-FIX-438`)

A `toolchain` entry is READ-BOUND into every lane for its workspace. `~/.config/gh` was one of
three such entries in this repository's own manifest, so every lane could `gh auth status`, push a
branch, delete one, or open a pull request as the operator — and the boundary the tool exists to
hold was removed by one manifest line that reads like a convenience.

The denied set is one named table, `CREDENTIAL_DIRECTORIES`, beside `isTooBroadForABind`, and the
rule is `SRV-FIX-078`'s: containment in **both** directions, asked of the canonical path. A path
that IS a member, is UNDER one, or CONTAINS one is refused `422 toolchain_holds_a_credential` with
the JSON pointer that failed. `SRV-FIX-078` is the reason the rule is stated that way rather than
as equality: it guarded a whole home and asserted the status code for the two spellings its author
had in mind, and `/home` was neither of them.

Identity is not a credential. `~/.gitconfig` stays, because a lane's commits must carry the
operator's name and passing `user.name`/`user.email` through the environment is a second mechanism
for something that already works. `agent.credentials_dir` also stays — `SRV-FEAT-039` exists
because a lane with no agent credential cannot run at all — and it is checked by its own rule one
function away.

## A lane's branch ref is its own (`SRV-FIX-449`)

`refs/heads/ticket` and `logs/refs/heads/ticket` were bound read-WRITE: one directory, shared,
holding every lane's branch. Measured from `lane/11` with four other lanes running, one
`git update-ref` from an unrelated lane discarded a branch fifteen thousand seconds into its round,
silently, and the lane that did it is not the one that would be blamed.

**The obvious narrowing does not work, and it was measured rather than argued.** `git update-ref`
writes `<ref>.lock` in the ref's OWN directory and renames it over the ref, so binding a lane's own
ref read-write inside a read-only directory refuses the lane's own commit:

```
fatal: cannot lock ref 'HEAD': Unable to create '…/refs/heads/ticket/MINE.lock':
Read-only file system
```

So it is the other way round. The namespace directory stays writable — the lane's own lock lands
there — and **every OTHER lane's ref file is bound read-only over itself**, reflog included. A
rename or an unlink over a bind mount is `EBUSY`, which is the kernel refusing rather than the tool
asking nicely, and it holds for `git update-ref`, `git update-ref -d`, `git branch -f`, `rm`, and a
redirect straight at the file.

The set is taken when the policy is built, from the refs that are on disk then. A lane claimed
afterwards creates a ref this lane's namespace does not cover; that is a narrowing of a hole that
was total, not a closure of it, and the case that names it is the one to write when it matters.
`refs/heads` itself is untouched and stays read-only — `main` lives there and `SRV-FEAT-002`'s
fourth criterion rests on it.

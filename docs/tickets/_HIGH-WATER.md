# Id high-water mark — SUPERSEDED, kept for the incident below

**This file no longer states a mark, and nothing may read one from it.**
`POST /api/tickets/ids` is the only answer:

```
.claude/scripts/api.sh POST /api/tickets/ids '{"prefix":"SRV-FIX"}'
```

It stated `349` until 2026-09-03, by which time the tool was issuing `365` — the file was wrong by
sixteen and nothing could tell. That is the same defect as the incident it was written to record,
one turn later: a number a human maintains, believed by sessions that cannot verify it.

The `FAC-*` prefix in the table below is also historical. `FAC-` is now the EPIC prefix; the
component series are `SRV-` and `WEB-`, and each carries its own counter.

## The mark is not derivable from one branch, and this file was

On 2026-09-02 five ids were issued twice or three times in a single night, all by sessions that had
read this file and believed it:

| id | issued by | and also by |
|---|---|---|
| `318` | `SRV-FEAT-311`'s QA | `SRV-FEAT-313`'s QA, and `SRV-FEAT-307`'s branch |
| `319` | `SRV-FEAT-313`'s QA | `SRV-FEAT-315`'s QA, and `SRV-FEAT-307`'s branch |
| `320` | `SRV-FEAT-313`'s QA | `SRV-FEAT-315`'s QA, and the `ui/*` branches |
| `321` | `SRV-FEAT-315`'s QA | the `ui/*` branches |
| `341` | `SRV-FIX-298`'s round 5 | `SRV-FEAT-315`'s QA, minutes apart |

The earlier version of this file explained the first collision as concurrency — *"a number handed
out by reading a file is a number two readers get"* — which is true and is not the whole of it. It
then said the highest id ever issued was `320`, while unmerged branches already carried ids to
`337`. **A file read on a branch reports the branch, and ids are issued on branches.** Every session
above was reading honestly and getting an answer that was true of its own worktree.

**So the mark cannot be taken from this file alone.** Before issuing an id, ask the repository:

```bash
for r in $(git for-each-ref --format='%(refname:short)' refs/heads); do
    git ls-tree -r --name-only "$r" -- factory/docs/tickets/ 2>/dev/null
done | grep -oE 'FAC-(FIX|FEAT)-[0-9]+' | grep -oE '[0-9]+$' | sort -n | tail -1
```

That reads every branch, merged or not, and is the only answer that does not depend on which one you
are standing on. This file remains the portable record for the case the command cannot cover — ids
reserved but not yet written to a file, and ids whose files were archived — and it is raised to `349`
because that is the id `SRV-FIX-347`'s QA session issued on 2026-09-02. The command returned `348`
that day (`ticket/FAC-FIX-348`, unmerged), so `349` was the first free number, and this line records
it for the next reader who is standing on a branch that cannot see either.

## Why this file exists

The tool's own record of what it has handed out is `.factory/ids.json`, and that file is
**gitignored** (`factory/.gitignore:24` — `.factory/*`). It is machine-local. On any other clone it
is absent, and `app/catalog/ids.cpp:57` then says what happens:

> `ids: <path> is not readable as a ledger, so the sequence restarts from the highest id on disk`

So on a fresh clone the sequence is derived from the ticket files, and the ticket files are the only
thing carrying the mark across machines. That is fine while every id ever issued still has a file —
and it stops being fine the moment a set of tickets is archived, moved or deleted. The highest id on
disk would drop, the tool would re-issue numbers that `ESCAPES.md`, the reports, every
`escaped_from:` chain and every commit prefix already point at, and the collision would be silent
until a second machine cloned. That is `SRV-FIX-188` (`409 ambiguous_ticket_id`) at the scale of the
whole backlog.

**This file is the portable copy of the mark.** It is committed, it is not a ticket, and it is read
by people rather than by the tool. Raise the number here whenever a block is reserved or a set of
tickets is archived, so the mark survives independently of which files happen to be on disk.

On 2026-09-01, 177 tickets were closed by decision (`docs/BACKLOG-CLOSE-2026-09-01.md`). They were
**left on disk** rather than deleted, precisely so this mark stays true by two independent means.

## The real fix, not done here

The ledger being machine-local is a tool defect: a repository's id sequence is a fact about the
repository, and it is currently stored where only one machine can see it. Either `.factory/ids.json`
is committed, or the mark moves into a file the tool writes outside `.factory/`. Both change what
`SPEC.md` §2 says the tool may write, so it is a specification change and not a patch — it is not
made here, and this file is the stopgap that makes the current arrangement safe to rely on.

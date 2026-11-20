# TKT-139 — Record the source hash into the build manifest — PASS

Run: `run-2026-07-27-0003` · Date: 2026-07-27

## Stages
SPEC  — one field, named, with the reason it is a hash and not an mtime.
TEST  — `test_manifest_carries_source_hash` written first, red.
IMPL  — `core/tasks/build.py`, +18 −6.
BUILD — green.
GATE  — `2 passed in 5.19s`.

## Assertions

| # | Assertion | Result | Evidence |
|---|-----------|--------|----------|
| 1 | The manifest carries `source_hash` | PASS | `2 passed in 5.19s` |
| 2 | A cache hit with changed sources rebuilds | PASS | `test_stale_guard PASSED` |

This report names the run it was produced in, so the evidence axis reads `claimed · proof`.

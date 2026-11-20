---
id: TKT-142
title: Guard against flashing a stale build artifact
parent: REQ-014
workspace: core
status: todo
contract: artifact-v2
owner: core team
stages: [SPEC, TEST, IMPL, BUILD, FLASH, GATE, QA, DELIVER]
---

# TKT-142 — Guard against flashing a stale build artifact

The flash stage writes whatever is in `build/`. If the build stage short-circuits on a cache hit,
the flashed image can predate the source it claims to be.

## ACCEPTANCE

1. The build stage decides freshness by hashing tracked sources, not by comparing mtimes.
2. The flash stage refuses to run against an artifact whose recorded source hash does not match
   the working tree.
3. A regression test covers the stale-cache path and fails on the current implementation.

## A note on rendering

This paragraph contains <b>embedded HTML</b> and a <script>window.__pwned = 1</script> tag, on
purpose. The tool renders documents written by agents, so what it does with HTML is a real
question: it escapes it. You are reading the file's bytes, not executing them.

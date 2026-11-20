---
id: TKT-128
title: Reject unsigned manifests at ingest
parent: REQ-011
workspace: web
status: done
contract: ingest-v1
owner: web team
stages: [SPEC, IMPL, TEST, DELIVER]
---

# TKT-128 — Reject unsigned manifests at ingest

Ingest accepts any manifest it can parse. It should accept only one it can verify.

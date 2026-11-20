---
id: TKT-139
title: Record the source hash into the build manifest
parent: REQ-014
workspace: core
status: done
contract: artifact-v2
owner: core team
stages: [SPEC, TEST, IMPL, BUILD, FLASH, GATE, QA, DELIVER]
---

# TKT-139 — Record the source hash into the build manifest

The build writes `manifest.json` beside the artifact. It gains one field: the hash of the tracked
sources the artifact was built from, so any later stage can check the claim independently.

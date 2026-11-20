---
id: TKT-147
title: Propagate the manifest hash to the deploy step
parent: REQ-014
workspace: edge
status: in progress
contract: artifact-v2
owner: edge team
stages: [SPEC, IMPL, BUILD, GATE, DELIVER]
---

# TKT-147 — Propagate the manifest hash to the deploy step

The deploy step reads the artifact and never opens its manifest, so the hash recorded by TKT-139
stops at the boundary. Five stages here, not eight — which is exactly why progress is only
comparable inside a group.

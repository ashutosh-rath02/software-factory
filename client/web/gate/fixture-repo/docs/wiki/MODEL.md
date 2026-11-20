---
title: The model
status: agreed
created: 2026-07-19
---

# The model

## 1. What this repository is

One firmware image, one board, one bench. Everything below follows from that: there is a single
physical climate chamber, a single flashing rig, and no amount of parallelism buys a second one.

## 2. Seams

A seam is where this repository's assumption meets somebody else's. Each one is a sentence that
must stay true, and the place it is enforced.

### S1 — the artifact is reproducible from the tracked sources alone

Two builds of the same commit produce the same bytes. Enforced by `core/build/manifest.py`, which
writes the source hash beside the artifact, and by `TKT-139`.

### S2 — a lane writes only inside its own checkout

The bench factory gives each run a checkout of its own and nothing above it. Enforced by the
sandbox the runner starts, never by the agent's good behaviour.

### S3 — the chamber is held, never shared

`climate-chamber` is declared exclusive in `.factory/factory.json`. A second run waits; it does
not get a second chamber.

### S4 — a release is signed before it leaves the bench

Nothing reaches a device without a signature this repository can check offline. `TKT-170` is where
that is being built and `REQ-031` is what it owes.

## 3. What is deliberately out

Fleet rollout, over-the-air staging and device telemetry all belong to the service repository. This
one stops at a signed artifact on the bench.

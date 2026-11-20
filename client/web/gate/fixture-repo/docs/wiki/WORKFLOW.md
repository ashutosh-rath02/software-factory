---
title: How work moves
status: agreed
---

# How work moves

Four stages, and the manifest declares them: `SPEC`, `IMPL`, `GATE`, `DELIVER`.

A ticket is claimed into a lane, driven to `GATE`, and delivered only when its report cites the run
that produced it — `REQ-011`. A report that cites no run is `claimed · no proof`, and the board
says so rather than counting it as done.

## The bench is the bottleneck

`edge` needs the chamber and the chamber is one. Work that does not need it is scheduled around
work that does, which is why `core` and `web` carry their own gate commands.

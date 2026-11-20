---
id: REQ-014
title: Never ship an artifact the working tree cannot reproduce
status: in progress
created: 2026-07-20
---

# REQ-014 — Never ship an artifact the working tree cannot reproduce

Four tickets across three workspaces. Each workspace runs its own stage sequence, so progress is
only comparable within a group.

An artifact that cannot be reproduced from the tree it claims to come from is an artifact nobody
can debug: the failure is downstream of a lie, and every hour spent on it is spent on the wrong
question.

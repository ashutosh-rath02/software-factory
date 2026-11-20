---
title: Flashing a board by hand
owner: bench
---

# Flashing a board by hand

For the times the rig is down and somebody has to do it with a cable.

1. Put the board in DFU: hold BOOT, tap RESET, release BOOT.
2. Check the artifact's manifest hash against the source hash you expect. `TKT-133` is the ticket
   that made the flasher refuse a mismatch; do it by eye when you are bypassing the flasher.
3. Flash, then power-cycle and read the banner. The banner prints the same hash.

If the banner disagrees with the manifest, stop. That is the condition `REQ-014` exists for.

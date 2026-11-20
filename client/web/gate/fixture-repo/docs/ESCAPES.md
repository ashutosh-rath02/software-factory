# Escapes — defects that got past a green gate

A defect found after its ticket passed means a gate said yes when it should have said no. The bug
is the small half; the check that let it through is the real defect.

## Ledger

| Date | Escape | Escaped from | Found by | Root cause of the MISS | Change landed |
|------|--------|--------------|----------|------------------------|---------------|
| 2026-07-24 | The flasher accepted an artifact whose manifest hash did not match the image beside it, so a board came off the bench running sources nobody could name. | TKT-133 | the bench, reading a banner that disagreed with the manifest | The gate compared the manifest against itself: both the expected hash and the measured one were read out of `manifest.json`, so a mismatch could not be expressed. | TKT-139 records the source hash from the tracked sources, and the flasher reads the image. |
| 2026-07-26 | A release was signed with a key the offline checker does not carry, so every device refused it after it shipped. | TKT-170 | REQ-031's first field trial | Signing was asserted by "the signing step exited zero". Nothing verified the signature the way a device does, which is the only reader that matters. | The gate now verifies with the device's own offline checker before a release leaves the bench. |
| 2026-08-02 | Two runs held the climate chamber at once and both reported a clean thermal sweep over the other's readings. | REQ-009 | a chamber log with two runs interleaved in it | Exclusivity was declared in the manifest and asserted by one run at a time in the test suite. One caller cannot tell a lock from a race. | The chamber case runs two claims released together, and the second one has to wait. |

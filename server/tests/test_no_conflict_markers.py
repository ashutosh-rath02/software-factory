"""A commit carrying git conflict markers reached `main`, and nothing refused it.

On 2026-09-05 `server/tests/test_owner_agreement.py` was committed to `main` with six conflict
markers in it. The file did not parse — `SyntaxError: invalid syntax` at line 1 of the marker — so
every case in it was uncollectable, and `docs/operations/ESCAPES.md` carried a duplicated pair of rows inside
markers of its own. A `git add -A` over a working tree left conflicted by an aborted merge is all
it took.

This repository's whole method is gates, and it had no gate for the one artefact that is never
intentional. A conflict marker is not a judgement call: there is no context in which
`<<<<<<< HEAD` at the start of a line is the content somebody meant to commit.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MARKER = ("<<<<<<< ", "=======", ">>>>>>> ")


def tracked_text_files():
    """DERIVED from git, not a list of directories somebody remembered to add."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True)
    for rel in out.stdout.split("\0"):
        if not rel:
            continue
        path = REPO / rel
        if not path.is_file():
            continue
        try:
            yield rel, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue          # a binary file cannot carry a marker a merge wrote


def test_the_sweep_reaches_the_repository():
    """MANDATORY NEGATIVE for the case below: it is only as good as the set it walks, and a
    sweep that finds no files passes for the wrong reason."""
    seen = {rel for rel, _text in tracked_text_files()}
    assert len(seen) > 200, f"the sweep found {len(seen)} files, which is not this repository"
    for must in ("server/tests/test_owner_agreement.py", "docs/operations/ESCAPES.md", "CLAUDE.md"):
        assert must in seen, f"{must} is tracked and the sweep did not reach it"


def test_no_tracked_file_carries_a_conflict_marker():
    offenders = []
    for rel, text in tracked_text_files():
        if rel == Path(__file__).relative_to(REPO).as_posix():
            continue          # this file names the markers in order to forbid them
        for n, line in enumerate(text.split("\n"), 1):
            if line.startswith(MARKER[0]) or line.startswith(MARKER[2]) or line == MARKER[1]:
                # `=======` alone is a markdown rule under a heading, so it only counts when a
                # real marker opens above it in the same file.
                if line == MARKER[1] and not any(
                        l.startswith(MARKER[0]) for l in text.split("\n")):
                    continue
                offenders.append(f"{rel}:{n}: {line[:60]}")
    assert not offenders, (
        "these tracked files carry git conflict markers; a merge was committed unresolved:\n  "
        + "\n  ".join(offenders))

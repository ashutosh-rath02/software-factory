"""Keep BOTH sides of a `docs/operations/ESCAPES.md` conflict, dropping exact duplicates.

Every branch appends a row to the same table, so this file conflicts on almost every integration
and `CLAUDE.md` says to keep both sides always. Duplicates are dropped because a row that landed on
main and on the branch is one row, and keeping it twice grows the ledger with nothing.
"""
import pathlib

p = pathlib.Path("docs/operations/ESCAPES.md")
if not p.exists():
    raise SystemExit(0)
lines = p.read_text().split("\n")
out, i, dup = [], 0, 0
while i < len(lines):
    if lines[i].startswith("<<<<<<< "):
        m = next(n for n in range(i, len(lines)) if lines[n] == "=======")
        j = next(n for n in range(m, len(lines)) if lines[n].startswith(">>>>>>> "))
        seen, merged = set(), []
        for row in lines[i + 1:m] + lines[m + 1:j]:
            k = row.strip()
            if k and k in seen:
                dup += 1
                continue
            if k:
                seen.add(k)
            merged.append(row)
        out += merged
        i = j + 1
    else:
        out.append(lines[i])
        i += 1
p.write_text("\n".join(out))
print(f"ESCAPES: both sides kept, {dup} exact duplicates dropped")

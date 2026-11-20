#!/usr/bin/env python3
"""Assert the .pen draws every identity the design's inventory declares.

The failure this catches is the one a pixel diff and a geometry probe both sail past: an
element that was simply never drawn. You cannot measure or diff what is not there.

The sets come from `extract.py`, which reads the design's own `const` blocks — NOT from
whoever drew the .pen. That is the whole point: a set the drawing agent wrote is a set
bounded by that agent's imagination, and it will agree with the drawing every time.
"""
import json, sys, pathlib

def asserted(inv, name):
    """Only the structural sets are a pass/fail. See `_structural` in the inventory."""
    return name in inv.get("structural", [])

def pen_strings(path):
    doc = json.loads(pathlib.Path(path).read_text())
    out = set()
    def walk(n):
        if isinstance(n, dict):
            for k in ("content", "name"):
                if isinstance(n.get(k), str):
                    for line in n[k].split("\n"):
                        out.add(line.strip())
            for v in n.values(): walk(v)
        elif isinstance(n, list):
            for v in n: walk(v)
    walk(doc)
    return out

def main(pen, invs):
    have = pen_strings(pen)
    total = missing = 0
    for f in invs:
        inv = json.loads(pathlib.Path(f).read_text())
        print(f"── {inv['source']}")
        for name, values in sorted(inv["sets"].items()):
            if not asserted(inv, name): continue
            absent = [v for v in values if v not in have]
            total += len(values); missing += len(absent)
            mark = "ok  " if not absent else "MISS"
            print(f"  {mark} {name:12} {len(values)-len(absent):3}/{len(values):<3}"
                  + ("  missing: " + " · ".join(absent[:6]) + (" …" if len(absent) > 6 else "") if absent else ""))
        for kind in ("icons", "button_variants"):
            values = inv.get(kind, [])
            absent = [v for v in values if v not in have]
            total += len(values); missing += len(absent)
            print(f"  {'ok  ' if not absent else 'MISS'} {kind:12} {len(values)-len(absent):3}/{len(values):<3}"
                  + ("  missing: " + " · ".join(absent[:8]) if absent else ""))
    print(f"\n{total - missing}/{total} identities drawn · {missing} missing")
    return 1 if missing else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))

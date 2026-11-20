#!/usr/bin/env python3
"""Which architectural rules over the tool's own sources does a ticket's own gate open?

`SRV-FIX-447` §3, question 5. A case that reads this repository's `.h`/`.cpp` files is asserting
a rule about EVERY module, and it is filed in the test file of ONE. The tier rules run a module's
test file only for a ticket that touched that module, so such a rule has no reader until somebody
runs the full suite: `test_manifest.py::test_no_module_but_manifest_knows_a_path` was red on
`main` for four days that way.

`@pytest.mark.repository` is the answer, because `pytest tests/ -m repository` runs on every
ticket — the commit that sets `status: awaiting-qa` is a document change.

    $ server/tools/tier-blind-architecture-checks.py          # from server/
"""
import ast
import pathlib
import sys


def reads_the_tools_own_sources(src):
    return ".h" in src and ".cpp" in src and any(
        k in src for k in ("rglob", "glob", "iterdir", "walk"))


def main():
    tests = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "tests")
    rows = []
    for f in sorted(tests.glob("test_*.py")):
        tree = ast.parse(f.read_text())
        module_marked = any(
            isinstance(n, ast.Assign)
            and any(getattr(t, "id", "") == "pytestmark" for t in n.targets)
            and "repository" in ast.unparse(n.value)
            for n in tree.body)
        helpers = {n.name for n in tree.body
                   if isinstance(n, ast.FunctionDef) and not n.name.startswith("test_")
                   and reads_the_tools_own_sources(ast.unparse(n))}
        for n in tree.body:
            if not isinstance(n, ast.FunctionDef) or not n.name.startswith("test_"):
                continue
            src = ast.unparse(n)
            if not (reads_the_tools_own_sources(src) or any(h + "(" in src for h in helpers)):
                continue
            marked = module_marked or any(
                "repository" in ast.unparse(d) for d in n.decorator_list)
            rows.append((marked, f"{f.name}::{n.name}"))

    if not rows:
        print("the sweep found no architectural rule at all — check the derivation before "
              "believing it", file=sys.stderr)
        return 2
    for marked, name in rows:
        print(f"{'every ticket        ' if marked else 'ONLY ITS OWN MODULE '} {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Navigation regression tests use isolated temporary Git repositories."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("doc_inventory", Path(__file__).with_name("doc_inventory.py"))
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


class MarkdownNavigationTests(unittest.TestCase):
    def test_duplicate_reference_definition_keeps_first_rendered_target(self):
        content = "[Page][Target]\n\n[target]: missing.md\n[TARGET]: valid.md\n"
        self.assertEqual(inventory.markdown_links(content), [("missing.md", 1)])

    def test_rendered_links_exclude_examples_and_unused_references(self):
        content = """---
title: '[metadata](missing.md)'
---
# Navigation

`[inline example](missing.md)`

```markdown
[fenced example](missing.md)
```

    [indented example](missing.md)

<!-- [comment](missing.md) -->
\\[escaped example](missing.md)
[angle](<folder/file name.md> "title")
[balanced](folder/file(one).md#section)
[full reference][Known]
[Known][]
[Known]
![image](screen.png)
[![nested image](nested.png)](document.md)
<a href="linked.md">Link</a>

[Known]: reference.md
[Unused]: missing.md
"""
        self.assertEqual(
            [target for target, _ in inventory.markdown_links(content)],
            ["folder/file name.md", "folder/file(one).md#section", "reference.md", "reference.md", "reference.md", "screen.png", "document.md", "nested.png", "linked.md"],
        )

    def test_heading_targets_include_code_unicode_duplicate_and_html_ids(self):
        content = """# API `POST` — café
## Same
## Same
## Same-1
Setext heading
--------------
<a id="explicit"></a>

```markdown
## Example only
```
"""
        self.assertEqual(
            inventory.markdown_anchors(content),
            {"api-post--café", "same", "same-1", "same-1-1", "setext-heading", "explicit"},
        )


class RepositoryNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="factory-doc-navigation-")
        self.root = Path(self.temporary.name).resolve()
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, path, content):
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            destination.write_bytes(content)
        else:
            destination.write_text(content, encoding="utf-8")
        return destination

    def refresh(self):
        outputs, count = inventory.render(self.root)
        for path, content in outputs.items():
            self.write(path, content)
        return count

    def navigation_sources(self):
        self.write("docs/INDEX.md", "# Index\n[Inventory](DOCUMENTS.md)\n")
        self.write("docs/tools/doc_inventory.py", "")
        self.write("docs/tools/legacy_links.json", '{"format":1,"sources":{}}')
        self.write("docs/archive/SNAPSHOT-LINKS.md", "# Snapshot links")

    def command(self, *arguments):
        return subprocess.run([sys.executable, inventory.__file__, "--root", str(self.root), *arguments],
                              capture_output=True, text=True, timeout=30)

    def test_all_evidence_and_component_assets_are_indexed_once(self):
        self.write(".gitignore", "docs/private/\nnode_modules/\n")
        self.write("docs/INDEX.md", "# Index\n[Inventory](DOCUMENTS.md)\n")
        self.write("docs/raw capture.log", "not Markdown [example](missing.md)")
        self.write("docs/screenshot.png", b"\x89PNG\xff")
        self.write("docs/private/secret.md", "private")
        self.write("client/web/design/render.py", "print('design tool')")
        self.write("component/README.md", "# Component")
        self.write("node_modules/library/README.md", "ignored")
        self.write("component/runtime.cpp", "not documentation")
        paths = inventory.document_paths(self.root)
        self.assertEqual(set(map(str, paths)), set(map(str, inventory.GENERATED_TITLES)) | {"docs/INDEX.md", "docs/raw capture.log", "docs/screenshot.png", "client/web/design/render.py", "component/README.md"})
        self.assertEqual(len(paths), len(set(paths)))
        self.refresh()
        text = (self.root / inventory.CATALOGS["technical"]).read_text()
        self.assertIn("(../raw%20capture.log)", text)
        catalogs = "\n".join((self.root / path).read_text() for path in inventory.CATALOGS.values())
        for path in paths:
            self.assertEqual(catalogs.count(f"| [{inventory.table_text(path)}]"), 1)
        # Inventory prose names real repository tools and the audit page.
        self.write("docs/tools/doc_inventory.py", "")
        self.write("docs/tools/legacy_links.json", '{"format":1,"sources":{}}')
        self.write("docs/archive/SNAPSHOT-LINKS.md", "# Snapshot links")
        self.refresh()
        issues, unreachable, _ = inventory.navigation_audit(self.root)
        self.assertEqual(issues, [])
        self.assertEqual(unreachable, [])
        previous = inventory.render(self.root)[0]
        self.write("docs/new-evidence.stdout", "new capture")
        self.assertNotEqual(inventory.render(self.root)[0], previous)
        (self.root / "docs/new-evidence.stdout").unlink()
        self.assertEqual(inventory.render(self.root)[0], previous)

    def test_missing_target_heading_and_unreachable_files_are_distinct(self):
        self.write("docs/INDEX.md", "# Index\n[Page](page.md#present)\n[Missing](gone.md)\n[Wrong heading](page.md#absent)\n")
        self.write("docs/page.md", "# Present\n")
        self.write("docs/unlinked.md", "# Not linked\n")
        issues, unreachable, _ = inventory.navigation_audit(self.root)
        self.assertEqual({(target, reason) for _, _, target, reason in issues}, {("gone.md", "missing target"), ("page.md#absent", "missing heading anchor")})
        self.assertEqual(set(map(str, unreachable)), set(map(str, inventory.GENERATED_TITLES)) | {"docs/unlinked.md"})

    def test_first_generation_is_complete_idempotent_and_preserves_heading_links(self):
        self.navigation_sources()
        self.write("docs/INDEX.md", "# Index\n[Inventory](DOCUMENTS.md)\n[Old report heading](DOCUMENTS.md#reports)\n")
        self.write("docs/reports/example.log", "raw evidence\n")
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        first, count = inventory.render(self.root)
        self.assertEqual(set(first), set(inventory.GENERATED_TITLES))
        self.assertEqual(count, len(inventory.document_paths(self.root)))
        self.assertEqual(first, {path: (self.root / path).read_text() for path in first})
        self.assertLess(len(first[inventory.OUTPUT].splitlines()), 150)
        self.assertTrue({inventory.heading_slug(label) for label, _ in inventory.GROUPS.values()}
                        <= inventory.markdown_anchors(first[inventory.OUTPUT]))
        self.assertEqual(self.command().returncode, 0)
        self.assertEqual(first, inventory.render(self.root)[0])
        checked = self.command("--check")
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_check_refuses_missing_and_stale_category_pages_without_writing(self):
        self.navigation_sources()
        self.refresh()
        catalog = self.root / inventory.CATALOGS["reports"]
        catalog.write_text("# Stale report catalog\n")
        checked = self.command("--check")
        self.assertEqual(checked.returncode, 1, checked.stderr)
        self.assertIn(str(inventory.CATALOGS["reports"]), checked.stderr)
        self.assertEqual(catalog.read_text(), "# Stale report catalog\n")
        catalog.unlink()
        checked = self.command("--check")
        self.assertEqual(checked.returncode, 1, checked.stderr)
        self.assertFalse(catalog.exists())
        self.assertEqual(self.command().returncode, 0)
        self.assertEqual(self.command("--check").returncode, 0)

    def test_catalog_symlink_escape_is_refused_before_any_output_is_written(self):
        self.write("docs/INDEX.md", "# Index\n")
        with tempfile.TemporaryDirectory(prefix="factory-doc-outside-") as outside:
            (self.root / "docs/catalog").symlink_to(outside, target_is_directory=True)
            result = self.command()
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("Path leaves repository root", result.stderr)
            self.assertFalse((self.root / inventory.OUTPUT).exists())
            self.assertEqual(list(Path(outside).iterdir()), [])

    def test_documentation_root_rejects_sources_and_lock_files(self):
        for name in ("README.md", "INDEX.md", "DOCUMENTS.md"):
            self.write("docs/" + name, "# Navigation\n")
        self.write("docs/technical/ARCHITECTURE.md", "# Architecture\n")
        self.assertEqual(inventory.layout_audit(self.root), [])
        self.write("docs/ARCHITECTURE.md", "# Misplaced source\n")
        self.write("docs/workflow-causes.json.lock", b"")
        errors = inventory.layout_audit(self.root)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("docs/ARCHITECTURE.md" in error for error in errors))
        self.assertTrue(any("docs/workflow-causes.json.lock" in error for error in errors))

    def test_filesystem_escape_and_symlink_escape_are_refused(self):
        self.write("docs/INDEX.md", "# Index\n[Escape](../../outside.md)\n")
        issues, _, _ = inventory.navigation_audit(self.root)
        self.assertIn("Path leaves repository root", issues[0][3])
        (self.root / "docs/escape.md").symlink_to(Path(self.temporary.name).parent / "outside.md")
        with self.assertRaisesRegex(ValueError, "Path leaves repository root"):
            inventory.document_paths(self.root)

    def test_exact_frozen_debt_is_hashed_and_does_not_bless_new_links(self):
        source = inventory.FROZEN_HISTORY_ROOT + "worktree-01/docs/reports/old.md"
        original = "# Frozen\n[Original capture](not-copied.log)\n"
        path = self.write(source, original)
        issue = (source, 2, "not-copied.log", "missing target")
        recovered = self.write("docs/archive/capture.log", "exact original capture\n")
        companion = self.write("docs/archive/SNAPSHOT-LINKS.md", "# Snapshot links\n## Snapshot 001\n[Capture](capture.log)\n")
        ledger = {"format": 1, "sources": {source: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "links": [{"line": 2, "target": "not-copied.log", "reason": "missing target", "resolved_path": "docs/archive/capture.log", "target_kind": "file", "target_sha256": hashlib.sha256(recovered.read_bytes()).hexdigest(), "companion_path": "docs/archive/SNAPSHOT-LINKS.md", "companion_anchor": "snapshot-001"}]}}}
        self.write(str(inventory.LEGACY_LINKS), json.dumps(ledger))
        self.assertEqual(inventory.legacy_link_audit(self.root, [issue]), ([], 1, []))
        new_issue = (source, 3, "new-missing.log", "missing target")
        unexpected, count, errors = inventory.legacy_link_audit(self.root, [issue, new_issue])
        self.assertEqual(unexpected, [new_issue])
        self.assertEqual(count, 1)
        self.assertEqual(errors, [])
        path.write_text(original + "Changed historical bytes\n")
        self.assertIn("Frozen history source changed", inventory.legacy_link_audit(self.root, [issue])[2][0])
        path.write_text(original)
        self.assertIn("Stale frozen-history link exception", inventory.legacy_link_audit(self.root, [])[2][0])
        recovered.write_text("changed original capture\n")
        self.assertIn("companion changed or is missing", inventory.legacy_link_audit(self.root, [issue])[2][0])
        recovered.write_text("exact original capture\n")
        companion.write_text("# Snapshot links\n## Snapshot 001\n")
        self.assertIn("click-through companion", inventory.legacy_link_audit(self.root, [issue])[2][0])
        companion.write_text("# Snapshot links\n## Snapshot 001\nNo mapped target.\n## Snapshot 002\n[Unrelated capture](capture.log)\n")
        self.assertIn("click-through companion", inventory.legacy_link_audit(self.root, [issue])[2][0])

    def test_frozen_directory_targets_pin_membership_and_bytes(self):
        source = inventory.FROZEN_HISTORY_ROOT + "worktree-01/docs/reports/old.md"
        path = self.write(source, "# Frozen\n[Original directory](not-copied/)\n")
        self.write("docs/archive/captures/one.log", "original capture\n")
        digest, files = inventory.directory_fingerprint(self.root, Path("docs/archive/captures"))
        self.write("docs/archive/SNAPSHOT-LINKS.md", "# Snapshot links\n## Snapshot 001\n[Captures](captures/)\n")
        issue = (source, 2, "not-copied/", "missing target")
        ledger = {"format": 1, "sources": {source: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "links": [{"line": 2, "target": "not-copied/", "reason": "missing target", "resolved_path": "docs/archive/captures", "target_kind": "directory", "target_sha256": digest, "directory_files": files, "companion_path": "docs/archive/SNAPSHOT-LINKS.md", "companion_anchor": "snapshot-001"}]}}}
        self.write(str(inventory.LEGACY_LINKS), json.dumps(ledger))
        self.assertEqual(inventory.legacy_link_audit(self.root, [issue]), ([], 1, []))
        self.write("docs/archive/captures/new.log", "new capture\n")
        errors = inventory.legacy_link_audit(self.root, [issue])[2]
        self.assertTrue(any("directory membership changed" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

# Documentation navigation tools

[Documentation](../INDEX.md) / [Technical](../technical/README.md) / Navigation tools

[doc_inventory.py](doc_inventory.py) builds the exhaustive [inventory](../DOCUMENTS.md) from Git's
tracked and non-ignored untracked file list. The compact entry point links to generated category
catalogs under `docs/catalog/`. It does not read private ignored configuration or maintain runtime
product state.

## Scope

- Every existing file under `docs/`, including Markdown, schemas, tools, logs, captures and images.
- Repository Markdown, MDX, reStructuredText and AsciiDoc documentation.
- Component-owned files under `client/web/design/` and `server/design/`.

Only `README.md`, `INDEX.md` and `DOCUMENTS.md` belong at the documentation root. Keep schemas
under `technical/contracts/`, shared workflow under `technical/workflow/` and readiness state under
`operations/readiness/`. The check rejects misplaced root sources and lock files.

Each file appears once in one category catalog. Archived records, fixtures, vendored references,
templates and agent adapters are identified separately from current guidance. Raw captures and
binary assets use filenames as titles; ticket statuses are documentation metadata, not release
evidence.

## Generate and check

From the repository root:

```bash
python3 docs/tools/doc_inventory.py
python3 docs/tools/doc_inventory.py --check
```

Generation updates `docs/DOCUMENTS.md` and the category pages under `docs/catalog/`. Edit the
indexed sources rather than these generated pages. The read-only check rejects missing or stale
inventory pages, unreachable files, misplaced files at the documentation root, and unresolved
local Markdown links or heading fragments. Links in fenced code,
inline code, metadata and examples are not navigation. External URLs are not fetched. Other
documentation formats and evidence assets are inventoried rather than parsed as Markdown.

## Preserve captured evidence

Some historical worktree reports were copied without their original sibling files. Their original
bytes remain intact. The [snapshot link map](../archive/SNAPSHOT-LINKS.md) connects those references
to files verified against the source Git objects; differing versions are recovered into the archive.

[legacy_links.json](legacy_links.json) records each original source hash, line, target and verified
destination. The check rejects changed captures, altered or missing destinations, stale mappings
and newly broken links. A new defect cannot inherit an existing historical mapping.

If a mapped current document must change, first retain its exact `git_source_object` bytes in
`docs/archive/2026/snapshot-targets/`. Verify the retained bytes against `target_sha256`, then update
the ledger's `resolved_path` and the companion link to that archived copy. Keep the original source
hash, line, target and Git provenance intact. Current product work can then change without
relabeling a newer document as historical evidence. Directory mappings also retain their exact
member list and file hashes.

## Regression coverage

[test_doc_inventory.py](test_doc_inventory.py) exercises isolated temporary repositories: inventory
coverage and freshness, rendered link syntax, heading targets, missing/unreachable files, root
confinement and frozen snapshot provenance. These checks validate documentation navigation; existing
repository gates still govern shared process documents.

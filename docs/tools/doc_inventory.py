#!/usr/bin/env python3
"""Generate the complete documentation inventory or check navigation offline."""

import argparse
from bisect import bisect_right
import html
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import unicodedata
from urllib.parse import quote, unquote, urlsplit


OUTPUT = PurePosixPath("docs/DOCUMENTS.md")
CATALOG_ROOT = PurePosixPath("docs/catalog")
DOCUMENT_EXTENSIONS = {".md", ".mdx", ".rst", ".adoc"}
MARKDOWN_EXTENSIONS = {".md", ".mdx"}
COMPONENT_DOCUMENT_ROOTS = ("client/web/design/", "server/design/")
LEGACY_LINKS = PurePosixPath("docs/tools/legacy_links.json")
FROZEN_HISTORY_ROOT = "docs/reports/FAC-EPIC-378-closeout/worktree-consolidation-2026-10-03/history/"
ROOT_DOCUMENTS = {"README.md", "INDEX.md", "DOCUMENTS.md"}
DESIGN_ASSETS = {
    "client/web/design/factory.pen": "UI wireframes and screen design",
    "client/web/design/nocturne.css": "Nocturne theme reference",
    "client/web/design/tokens.json": "UI design tokens",
}
GROUPS = {
    "overview": ("Overview", "Purpose, scope, architecture overview, roadmap and reading entry points."),
    "technical": ("Technical", "Implementation references and canonical interface schemas."),
    "product": ("Product", "Product requirements, specifications, stories, features and business cases."),
    "operations": ("Operations", "Deployment, runbooks, incidents, backup and monitoring."),
    "samples": ("Samples and templates", "Reusable forms, examples and sample documents."),
    "design": ("Design", "UI, wireframes, flows, diagrams and module design references."),
    "notes": ("Notes", "Drafts, quick notes, meeting records and material awaiting classification."),
    "epics": ("Epics", "Product acceptance and requirement records; consult their evidence before claiming completion."),
    "tickets": ("Tickets", "Implementation work records; their declared status is not a release verdict."),
    "reports": ("Reports", "Validation, QA and closeout reports; results apply to the candidates they identify."),
    "workflow": ("Shared workflow", "Provider-neutral working agreements, review rules and escape records."),
    "adapters": ("Agent adapters", "Provider and component entry points, prompts and skills; shared workflow remains authoritative."),
    "fixtures": ("Fixtures", "Synthetic documents used by tests; these do not describe the Factory product backlog."),
    "vendored": ("Vendored references", "Documents accompanying copied dependencies; these do not define Factory product scope."),
    "history": ("History", "Archived snapshots and historical copies; these are not current product or workflow authority."),
}
CATALOGS = {
    key: CATALOG_ROOT / (re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") + ".md")
    for key, (label, _) in GROUPS.items()
}
GENERATED_TITLES = {
    OUTPUT: "Documentation inventory",
    CATALOG_ROOT / "README.md": "Documentation catalogs",
    **{CATALOGS[key]: f"{label} catalog" for key, (label, _) in GROUPS.items()},
}


def git(root, *arguments):
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-C", str(root), *arguments],
        stderr=subprocess.PIPE,
    )


def within_root(root, path):
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path leaves repository root: {path}")
    return resolved


def qualifying(path):
    if str(path).startswith("docs/") or str(path).startswith(COMPONENT_DOCUMENT_ROOTS):
        return True
    if path.suffix.lower() in DOCUMENT_EXTENSIONS:
        return True
    if path.parent == PurePosixPath("docs") and path.name.endswith(".schema.json"):
        return True
    return str(path) in DESIGN_ASSETS or (
        path.parent == PurePosixPath("client/web/design/ref")
        and path.name.endswith(".inventory.json")
    )


def document_paths(root):
    candidates = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    paths = {PurePosixPath(os.fsdecode(path)) for path in candidates.split(b"\0") if path}
    selected = set()
    for path in paths:
        if qualifying(path):
            local = within_root(root, root / path)
            if local.is_file():
                selected.add(path)
    selected.update(GENERATED_TITLES)
    return sorted(selected, key=str)


def group_for(path):
    parts = {part.lower() for part in path.parts}
    name = str(path)
    if name.startswith(str(CATALOG_ROOT) + "/"):
        return "overview"
    if parts & {"history", "archive", "archives"} or path.name.startswith("BACKLOG-CLOSE-"):
        return "history"
    if parts & {"fixture", "fixtures", "fixture-repo"}:
        return "fixtures"
    if name.startswith("server/core/") or parts & {"vendor", "vendored", "third_party", "third-party"}:
        return "vendored"
    if path.name in {"AGENTS.md", "CLAUDE.md"} or path.parts[0] in {".claude", ".codex", ".agents"} or name.startswith(".factory/prompts/"):
        return "adapters"
    if parts & {"samples", "sample", "templates", "template"} or path.stem.upper().startswith("_TEMPLATE"):
        return "samples"
    if name.startswith("docs/epics/"):
        return "epics"
    if name.startswith("docs/tickets/"):
        return "tickets"
    if name.startswith("docs/reports/"):
        return "reports"
    if name.startswith("docs/technical/workflow/"):
        return "workflow"
    if "design" in parts:
        return "design"
    for group in ("overview", "product", "technical", "operations", "notes"):
        if group in parts:
            return group
    if name in {"README.md", "docs/README.md", "docs/INDEX.md", "docs/DOCUMENTS.md"}:
        return "overview"
    return "technical"


def scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    return value.split(" #", 1)[0].rstrip()


def metadata(root, path):
    if path in GENERATED_TITLES:
        return GENERATED_TITLES[path], "—"
    if str(path) in DESIGN_ASSETS:
        return DESIGN_ASSETS[str(path)], "—"
    if path.suffix.lower() not in DOCUMENT_EXTENSIONS:
        if path.parent == PurePosixPath("docs/technical/contracts") and path.name.endswith(".schema.json"):
            data = json.loads(within_root(root, root / path).read_text(encoding="utf-8"))
            return str(data.get("title") or path.stem), "—"
        return path.name, "—"
    content = within_root(root, root / path).read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    fields = {}
    if lines and lines[0].lstrip("\ufeff").strip() == "---":
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() in {"---", "..."}), len(lines))
        for line in lines[1:end]:
            match = re.match(r"^(title|status):\s*(.*?)\s*$", line)
            if match and match[2] not in {"|", ">", "|-", ">-"}:
                fields[match[1]] = scalar(match[2])
        lines = lines[end + 1:]
    title = fields.get("title")
    if not title:
        for i, line in enumerate(lines):
            heading = re.match(r"^(?:#{1,6}|={1,6})\s+(.+?)\s*#*\s*$", line)
            if heading:
                title = heading[1]
                break
            if line.strip() and i + 1 < len(lines) and re.fullmatch(r"[=\-~^]{3,}\s*", lines[i + 1]):
                title = line.strip()
                break
    return title or path.stem, fields.get("status") or "—"


def table_text(value):
    return html.escape(" ".join(str(value).split()), quote=False).replace("|", "&#124;").replace("[", "&#91;").replace("]", "&#93;")


def visible_markdown(content):
    """Mask code and metadata, retaining offsets for useful failure locations.

    This scanner covers repository navigation syntax rather than rendering HTML.
    Masked regions keep newlines so line numbers remain those of the source.
    """
    lines = content.splitlines(keepends=True)
    masked = []
    fence = None
    frontmatter = bool(lines and lines[0].lstrip("\ufeff").strip() == "---")
    for number, line in enumerate(lines):
        if frontmatter:
            masked.append(re.sub(r"[^\r\n]", " ", line))
            if number and line.strip() in {"---", "..."}:
                frontmatter = False
            continue
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            masked.append(re.sub(r"[^\r\n]", " ", line))
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not line[marker.end():].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
            masked.append(re.sub(r"[^\r\n]", " ", line))
        elif line.startswith("    ") or line.startswith("\t"):
            # Nested list prose is retained; indented standalone examples are not links.
            if masked and masked[-1].strip():
                masked.append(line)
            else:
                masked.append(re.sub(r"[^\r\n]", " ", line))
        else:
            masked.append(line)
    text = "".join(masked)
    text = re.sub(r"<!--.*?-->", lambda m: re.sub(r"[^\r\n]", " ", m[0]), text, flags=re.S)
    # Matching equal-length backtick runs is what makes a code span close.
    text = re.sub(r"(`+)(?!`)(.*?)(?<!`)\1(?!`)", lambda m: re.sub(r"[^\r\n]", " ", m[0]), text, flags=re.S)
    return text


def unescape_markdown(value):
    return html.unescape(re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])", r"\1", value))


def destination(text, start, stop=None):
    """Read an angle-bracket or balanced bare Markdown link destination."""
    stop = len(text) if stop is None else stop
    while start < stop and text[start].isspace():
        start += 1
    if start >= stop:
        return "", start
    if text[start] == "<":
        end = start + 1
        while end < stop:
            if text[end] == "\\":
                end += 2
            elif text[end] == ">":
                return unescape_markdown(text[start + 1:end]), end + 1
            elif text[end] in "\r\n":
                return None, end
            else:
                end += 1
        return None, end
    end, depth = start, 0
    while end < stop:
        character = text[end]
        if character == "\\":
            end += 2
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            if not depth:
                break
            depth -= 1
        elif character.isspace():
            break
        end += 1
    return unescape_markdown(text[start:end]), end


class HtmlNavigation(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.anchors = set()

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if "id" in attributes:
            self.anchors.add(attributes["id"])
        if tag == "a" and attributes.get("name"):
            self.anchors.add(attributes["name"])
        key = "href" if tag == "a" else "src" if tag in {"img", "source"} else None
        if key and attributes.get(key):
            self.links.append((attributes[key], self.getpos()[0]))


def markdown_links(content):
    """Return rendered inline, reference, image and HTML links (target, line)."""
    text = visible_markdown(content)
    newlines = [match.start() for match in re.finditer("\n", text)]
    references = {}
    reference_spans = []
    for match in re.finditer(r"(?m)^ {0,3}\[([^\]\n]+)\]:[ \t]*(.*)$", text):
        value, _ = destination(match[2], 0)
        if value is not None:
            references.setdefault(" ".join(unescape_markdown(match[1]).split()).casefold(), value)
            reference_spans.append((match.start(), match.end()))
    for start, end in reversed(reference_spans):
        text = text[:start] + re.sub(r"[^\r\n]", " ", text[start:end]) + text[end:]
    links = []
    position = 0
    while position < len(text):
        if text[position] == "\\":
            position += 2
            continue
        if text[position] != "[":
            position += 1
            continue
        end, depth = position + 1, 1
        while end < len(text) and depth:
            if text[end] == "\\":
                end += 2
                continue
            if text[end] == "[":
                depth += 1
            elif text[end] == "]":
                depth -= 1
            end += 1
        if depth:
            position += 1
            continue
        label = text[position + 1:end - 1]
        value = None
        after = end
        if end < len(text) and text[end] == "(":
            value, after = destination(text, end + 1)
            # The remaining text may contain a quoted title, then must close.
            closing = re.match(r"\s*(?:\"[^\"]*\"|'[^']*'|\([^)]*\))?\s*\)", text[after:])
            if closing:
                after += closing.end()
            else:
                value = None
        else:
            second = re.match(r"[ \t]*(?:\n[ \t]*)?\[([^\]\n]*)\]", text[end:])
            reference = second[1] or label if second else label
            key = " ".join(unescape_markdown(reference).split()).casefold()
            value = references.get(key)
            if second:
                after = end + second.end()
        if value is not None:
            base_line = bisect_right(newlines, position)
            links.append((value, base_line + 1))
            # An image nested in a link still has its own resource target.
            if "![" in label:
                links.extend((target, line + base_line) for target, line in markdown_links(label))
        position = max(position + 1, after)
    parser = HtmlNavigation()
    parser.feed(text)
    links.extend(parser.links)
    return links


def heading_slug(value):
    value = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape_markdown(value).lower()
    value = "".join(character for character in value if character in "-_ " or unicodedata.category(character)[0] in {"L", "N"})
    return value.strip().replace(" ", "-")


def markdown_anchors(content):
    # Code spans contribute visible text to headings, unlike fenced examples.
    text = visible_markdown(content.replace("`", ""))
    # Removing backticks must not turn fenced code into headings.
    fenced_text = visible_markdown(content)
    lines = text.splitlines()
    fenced_lines = fenced_text.splitlines()
    anchors, seen = set(), {}
    for number, line in enumerate(lines):
        if number >= len(fenced_lines) or not fenced_lines[number].strip():
            continue
        heading = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        title = heading[1] if heading else None
        if not title and number + 1 < len(lines) and re.fullmatch(r" {0,3}(?:=+|-+)\s*", lines[number + 1]):
            title = line.strip()
        if title:
            slug = heading_slug(title)
            suffix = seen.get(slug, 0)
            candidate = f"{slug}-{suffix}" if suffix else slug
            while candidate in anchors:
                suffix += 1
                candidate = f"{slug}-{suffix}"
            seen[slug] = suffix + 1
            anchors.add(candidate)
    parser = HtmlNavigation()
    parser.feed(fenced_text)
    return anchors | parser.anchors


def heading_sections(content):
    """Return ATX-heading bodies through the next equal or higher-level heading."""
    text = visible_markdown(content)
    headings, used, counters = [], set(), {}
    for match in re.finditer(r"(?m)^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", text):
        # Code spans remain visible heading text even though navigation masks them.
        title = content[match.start(2):match.end(2)]
        base = heading_slug(title)
        suffix = counters.get(base, 0)
        anchor = f"{base}-{suffix}" if suffix else base
        while anchor in used:
            suffix += 1
            anchor = f"{base}-{suffix}"
        counters[base] = suffix + 1
        used.add(anchor)
        headings.append((anchor, len(match[1]), match.start(), match.end()))
    sections = {}
    for index, (anchor, level, _, start) in enumerate(headings):
        end = next((other_start for _, other_level, other_start, _ in headings[index + 1:] if other_level <= level), len(content))
        sections[anchor] = content[start:end]
    return sections


def local_destination(root, source, target):
    if not target or target.startswith("//") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target):
        return None
    parsed = urlsplit(target)
    relative = unquote(parsed.path)
    if not relative:
        path = root / source
    elif relative.startswith("/"):
        path = root / relative.lstrip("/")
    else:
        path = root / source.parent / relative
    resolved = within_root(root, path)
    return PurePosixPath(resolved.relative_to(root).as_posix()), unquote(parsed.fragment)


def navigation_audit(root, paths=None):
    """Check local navigation and find files inaccessible from docs/INDEX.md."""
    paths = document_paths(root) if paths is None else paths
    contents = {path: within_root(root, root / path).read_text(encoding="utf-8", errors="replace") for path in paths if path.suffix.lower() in MARKDOWN_EXTENSIONS and (root / path).is_file()}
    anchors = {path: markdown_anchors(content) for path, content in contents.items()}
    graph = {path: set() for path in paths}
    issues = []
    total_links = 0
    for source, content in contents.items():
        for target, line in markdown_links(content):
            try:
                resolved = local_destination(root, source, target)
            except (ValueError, OSError) as error:
                issues.append((str(source), line, target, str(error)))
                continue
            if resolved is None:
                continue
            total_links += 1
            path, fragment = resolved
            local = root / path
            if not local.exists():
                issues.append((str(source), line, target, "missing target"))
                continue
            if path in graph:
                graph[source].add(path)
            if fragment and path in anchors and fragment not in anchors[path]:
                line_anchor = re.fullmatch(r"L(\d+)(?:-L(\d+))?", fragment)
                if line_anchor and 0 < int(line_anchor[1]) <= len(contents[path].splitlines()) and (not line_anchor[2] or int(line_anchor[2]) <= len(contents[path].splitlines())):
                    continue
                issues.append((str(source), line, target, "missing heading anchor"))
    start = PurePosixPath("docs/INDEX.md")
    reachable, pending = set(), [start]
    while pending:
        path = pending.pop()
        if path not in reachable:
            reachable.add(path)
            pending.extend(graph.get(path, set()) - reachable)
    unreachable = sorted(set(paths) - reachable, key=str)
    return issues, unreachable, total_links


def file_sha256(root, path):
    return hashlib.sha256(within_root(root, root / path).read_bytes()).hexdigest()


def layout_audit(root):
    """Keep the documentation root limited to its three navigation entry points."""
    directory = within_root(root, root / "docs")
    return sorted(
        f"Unexpected docs root file; move it to its owning category: docs/{path.name}"
        for path in directory.iterdir()
        if path.is_file() and path.name not in ROOT_DOCUMENTS
    )


def directory_fingerprint(root, path):
    directory = within_root(root, root / path)
    files = []
    for candidate in sorted(directory.rglob("*")):
        if candidate.is_file():
            within_root(root, candidate)
            files.append({"path": candidate.relative_to(directory).as_posix(), "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest()})
    files.sort(key=lambda entry: entry["path"])
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), files


def legacy_link_audit(root, issues):
    """Ratchet frozen literals and verify their exact companion destinations."""
    ledger_path = within_root(root, root / LEGACY_LINKS)
    if not ledger_path.is_file():
        return issues, 0, []
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if not isinstance(ledger, dict) or ledger.get("format") != 1 or not isinstance(ledger.get("sources"), dict):
        raise ValueError("Unsupported frozen-history link ledger format")
    allowed = set()
    ledger_errors = []
    targets = {}
    companions = {}
    for source, entry in ledger["sources"].items():
        if not isinstance(entry, dict) or not isinstance(entry.get("links"), list) or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
            raise ValueError(f"Malformed frozen history source: {source}")
        if not source.startswith(FROZEN_HISTORY_ROOT) or ".." in PurePosixPath(source).parts:
            raise ValueError(f"Legacy link exception is outside the frozen worktree snapshots: {source}")
        source_path = within_root(root, root / source)
        if not source_path.is_file() or file_sha256(root, PurePosixPath(source)) != entry.get("sha256"):
            ledger_errors.append(f"Frozen history source changed or is missing: {source}")
        for link in entry.get("links", []):
            if not isinstance(link, dict) or not isinstance(link.get("line"), int) or link["line"] < 1 or not isinstance(link.get("target"), str) or not isinstance(link.get("reason"), str):
                raise ValueError(f"Malformed frozen reference in {source}")
            issue = (source, link["line"], link["target"], link["reason"])
            if issue in allowed:
                raise ValueError(f"Duplicate legacy link exception: {source}:{link['line']}")
            allowed.add(issue)
            resolved_path = link.get("resolved_path")
            if not resolved_path:
                ledger_errors.append(f"Frozen reference has no companion destination: {source}:{link['line']}")
                continue
            resolved = PurePosixPath(resolved_path)
            if resolved.is_absolute() or ".." in resolved.parts:
                raise ValueError(f"Frozen reference destination is not a repository-relative path: {resolved_path}")
            destination_path = within_root(root, root / resolved)
            kind = link.get("target_kind")
            cache_key = (resolved_path, kind)
            if cache_key not in targets:
                if kind == "file" and destination_path.is_file():
                    targets[cache_key] = (file_sha256(root, resolved), None)
                elif kind == "directory" and destination_path.is_dir():
                    targets[cache_key] = directory_fingerprint(root, resolved)
                else:
                    targets[cache_key] = (None, None)
            digest, directory_files = targets[cache_key]
            if digest != link.get("target_sha256") or digest is None:
                ledger_errors.append(f"Frozen reference companion changed or is missing: {source}:{link['line']} → {resolved_path}")
            if kind == "directory" and directory_files != link.get("directory_files"):
                ledger_errors.append(f"Frozen reference directory membership changed: {resolved_path}")
            companion_path = link.get("companion_path")
            if companion_path != "docs/archive/SNAPSHOT-LINKS.md":
                ledger_errors.append(f"Frozen reference has no declared snapshot companion: {source}:{link['line']}")
                continue
            companion = PurePosixPath(companion_path)
            if companion_path not in companions:
                companion_file = within_root(root, root / companion)
                if not companion_file.is_file():
                    companions[companion_path] = {}
                else:
                    content = companion_file.read_text(encoding="utf-8")
                    sections = {}
                    for anchor, section in heading_sections(content).items():
                        destinations = set()
                        for target, _ in markdown_links(section):
                            destination_value = local_destination(root, companion, target)
                            if destination_value:
                                destinations.add(str(destination_value[0]))
                        sections[anchor] = destinations
                    companions[companion_path] = sections
            section_destinations = companions[companion_path].get(link.get("companion_anchor"), set())
            if resolved_path not in section_destinations:
                ledger_errors.append(f"Frozen reference is missing from its click-through companion: {source}:{link['line']}")
    current = set(issues)
    for source, line, target, _ in sorted(allowed - current):
        ledger_errors.append(f"Stale frozen-history link exception: {source}:{line}: {target}")
    unexpected = [issue for issue in issues if issue not in allowed]
    return unexpected, len(current & allowed), ledger_errors


def render(root):
    groups = {key: [] for key in GROUPS}
    for path in document_paths(root):
        title, status = metadata(root, path)
        group = group_for(path)
        relative = os.path.relpath(str(path), str(CATALOGS[group].parent))
        link = f"[{table_text(path)}]({quote(relative, safe='/')})"
        groups[group].append(f"| {link} | {table_text(title)} | {table_text(status)} |")
    total = sum(len(rows) for rows in groups.values())
    lines = [
        "# Documentation inventory",
        "",
        "Generated by [doc_inventory.py](tools/doc_inventory.py). Start with the [documentation index](INDEX.md) for reading order and product context.",
        "",
        "Scope: every existing Git-tracked or non-ignored untracked file under `docs/`, including screenshots, command captures, schemas, examples and documentation tools; every file under `client/web/design/` and `server/design/`; and every `.md`, `.mdx`, `.rst` and `.adoc` file elsewhere in this repository. Each qualifying file appears exactly once in the generated category catalogs; this entry point and all catalog pages are included. Ignored private configuration and ignored dependency/build output are excluded. Tracked vendored documentation remains included.",
        "",
        "Paths, titles and frontmatter `status` values describe documentation. Counts and declared statuses do not establish product completion, release readiness or the version currently running. Historical copies, test fixtures and vendored references are listed separately from current product documents. Files without frontmatter status show `—`.",
        "",
        "Regenerate with `python3 docs/tools/doc_inventory.py`; check freshness, file coverage, local Markdown links, heading targets and reachability from the index with `python3 docs/tools/doc_inventory.py --check`. Output is deterministic and contains no timestamp or content hash.",
        "",
        "Use the [catalog directory](catalog/README.md) to browse individual categories. The category links below open their complete file lists; existing heading links to this entry point remain available.",
        "",
        "The check rejects unresolved local links in current documents. Frozen worktree snapshots retain their original bytes and relative link literals; their exact references have verified destinations in the [snapshot link companion](archive/SNAPSHOT-LINKS.md), with source and destination hashes in [legacy_links.json](tools/legacy_links.json). Each check reports these mapped references and rejects new defects, changed snapshots or changed companion destinations. HTTP links are listed but are not fetched; RST/AsciiDoc and raw evidence contents are inventoried without parsing their internal links.",
        "",
        f"**Total: {total} documentation files and evidence assets.**",
        "",
        "| Category | Files |",
        "|---|---:|",
    ]
    for key, (label, _) in GROUPS.items():
        relative = os.path.relpath(str(CATALOGS[key]), str(OUTPUT.parent))
        lines.append(f"| [{label}]({relative}) | {len(groups[key])} |")
    outputs = {}
    catalog_lines = [
        "# Documentation catalogs", "",
        "[Documentation index](../INDEX.md) / [Complete inventory](../DOCUMENTS.md) / Catalogs", "",
        "Generated by [doc_inventory.py](../tools/doc_inventory.py). Each category contains the complete list of its documents and evidence assets. Use the [documentation index](../INDEX.md) for reading order and product context.", "",
        f"**Total: {total} documentation files and evidence assets.**", "",
        "| Category | Files |", "|---|---:|",
    ]
    for key, (label, description) in GROUPS.items():
        catalog = CATALOGS[key]
        lines.extend(["", f"## {label}", "", description, "",
                      f"Open the [{label.lower()} catalog]({os.path.relpath(str(catalog), str(OUTPUT.parent))}) ({len(groups[key])} files)."])
        catalog_lines.append(f"| [{label}]({catalog.name}) | {len(groups[key])} |")
        page = [
            f"# {label} catalog", "",
            f"[Documentation index](../INDEX.md) / [Complete inventory](../DOCUMENTS.md) / [Catalogs](README.md) / {label}", "",
            "Generated by [doc_inventory.py](../tools/doc_inventory.py).", "",
            description, "",
            f"**Files: {len(groups[key])}.** Titles and declared statuses are documentation metadata; consult the owning source and evidence for current product state.", "",
        ]
        if groups[key]:
            page.extend(["| Repository path | Title | Declared status |", "|---|---|---|", *groups[key]])
        else:
            page.append("No qualifying files in this category.")
        outputs[catalog] = "\n".join(page) + "\n"
    outputs[CATALOG_ROOT / "README.md"] = "\n".join(catalog_lines) + "\n"
    outputs[OUTPUT] = "\n".join(lines) + "\n"
    return outputs, total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check inventory freshness, local links, headings and index reachability; write nothing")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2], help="Git repository root; outputs are confined to docs/DOCUMENTS.md and docs/catalog/")
    args = parser.parse_args()
    try:
        root = args.root.resolve(strict=True)
        git_root = Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
        if git_root != root:
            raise ValueError("--root must name the Git repository root")
        expected, total = render(root)
        outputs = {path: within_root(root, root / path) for path in expected}
        if args.check:
            stale = [path for path, output in outputs.items()
                     if not output.is_file() or output.read_text(encoding="utf-8") != expected[path]]
            if stale:
                for path in stale:
                    print(f"Documentation inventory is stale: {path}; run python3 docs/tools/doc_inventory.py", file=sys.stderr)
                return 1
            issues, unreachable, links = navigation_audit(root)
            issues, legacy_count, ledger_errors = legacy_link_audit(root, issues)
            layout_errors = layout_audit(root)
            for source, line, target, reason in issues:
                print(f"{source}:{line}: {reason}: {target}", file=sys.stderr)
            for path in unreachable:
                print(f"Unreachable from docs/INDEX.md: {path}", file=sys.stderr)
            for error in ledger_errors:
                print(error, file=sys.stderr)
            for error in layout_errors:
                print(error, file=sys.stderr)
            if issues or unreachable or ledger_errors or layout_errors:
                print(f"Documentation navigation failed: {len(issues)} local link defects, {len(unreachable)} unreachable files, {len(ledger_errors)} frozen-reference integrity defects.", file=sys.stderr)
                return 1
            print(f"Documentation inventory is current ({total} files).")
            print("Documentation root is clean (README.md, INDEX.md, DOCUMENTS.md only).")
            print(f"Current documentation navigation is valid ({links} local links audited; every indexed file is reachable from docs/INDEX.md).")
            print(f"Frozen history: {legacy_count} exact snapshot references mapped to verified destinations; see docs/archive/SNAPSHOT-LINKS.md. Original snapshot link literals are preserved.")
        else:
            for path, output in outputs.items():
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(expected[path], encoding="utf-8")
            print(f"Wrote docs/DOCUMENTS.md and {len(expected) - 1} catalog pages ({total} files).")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Documentation inventory failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

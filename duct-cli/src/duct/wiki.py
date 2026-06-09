"""Workspace wiki — durable knowledge curated by the wiki subagents.

The wiki lives at ``{workspace_root}/toolkit/wiki/`` and contains flat markdown
entries plus an ``INDEX.md`` table of contents. Sessions consult the wiki
via the ``wiki-reader`` subagent and contribute through the
``wiki-contributor`` subagent — both are Claude Code subagents materialised at
``{workspace_root}/.claude/agents/``. This module provides the read-side helpers
used by the ``duct wiki`` CLI and a launcher for the ``wiki-maintainer``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from duct import paths
from duct.markdown import parse_frontmatter

INDEX_FILENAME = paths.WIKI_INDEX_FILENAME
ENTRY_TYPES = ("lesson", "convention", "domain", "env")


@dataclass(frozen=True)
class WikiEntry:
    name: str
    type: str
    description: str
    tags: tuple[str, ...]
    path: Path


def wiki_dir(root: Path) -> Path:
    return paths.wiki_dir(root)


def index_path(root: Path) -> Path:
    return paths.wiki_index(root)


def _warn(message: str) -> None:
    sys.stderr.write(f"warning: {message}\n")


def _parse_entry_file(path: Path) -> WikiEntry | None:
    """Parse a single wiki entry. Skip the index file. Warn on malformed input."""
    if path.name == INDEX_FILENAME:
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        _warn(f"could not read wiki entry {path}: {exc}")
        return None

    meta, _ = parse_frontmatter(content)
    if not meta:
        _warn(f"wiki entry {path} has no frontmatter; skipping")
        return None

    name = meta.get("name", "").strip()
    if not name:
        _warn(f"wiki entry {path} is missing 'name'; skipping")
        return None

    entry_type = meta.get("type", "").strip()
    if entry_type not in ENTRY_TYPES:
        _warn(f"wiki entry {path} has invalid type {entry_type!r}; skipping")
        return None

    tags_raw = meta.get("tags", "").strip()
    tags = tuple(t.strip() for t in tags_raw.split(",") if t.strip()) if tags_raw else ()

    return WikiEntry(
        name=name,
        type=entry_type,
        description=meta.get("description", "").strip(),
        tags=tags,
        path=path,
    )


def list_entries(root: Path) -> list[WikiEntry]:
    """Return all wiki entries, sorted by (type, name). Skips INDEX.md."""
    directory = wiki_dir(root)
    if not directory.is_dir():
        return []

    entries: list[WikiEntry] = []
    for path in sorted(directory.glob("*.md")):
        entry = _parse_entry_file(path)
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=lambda e: (e.type, e.name))


def read_entry(root: Path, name: str) -> WikiEntry | None:
    """Return the entry with *name*, or None when not found."""
    for entry in list_entries(root):
        if entry.name == name:
            return entry
    return None


def spawn_maintainer(root: Path) -> int:
    """Launch a Claude session that invokes the wiki-maintainer subagent.

    Runs the ``claude`` CLI in *root* so it discovers the subagent
    definition under ``.claude/agents/``. The subagent does the actual
    work; this wrapper just kicks off the session. Returns the
    subprocess exit code.
    """
    claude = shutil.which("claude")
    if claude is None:
        raise FileNotFoundError(
            "`claude` CLI not found on PATH. Install Claude Code or add it to PATH."
        )

    prompt = (
        "Invoke the `wiki-maintainer` subagent via the Task tool to dedupe, "
        "prune, and consolidate the workspace wiki at ./toolkit/wiki/. Surface its "
        "summary at the end."
    )
    cmd = [claude, "-p", prompt]
    result = subprocess.run(cmd, cwd=str(root), check=False)
    return result.returncode

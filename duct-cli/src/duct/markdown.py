"""Markdown utilities for reading, writing, and parsing duct workspace files."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

TICKET_KEY_PATTERN = re.compile(r"[A-Z]+-\d+")

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)", re.DOTALL)

MANAGED_BLOCK_START = "<!-- duct:managed:start -->"
MANAGED_BLOCK_END = "<!-- duct:managed:end -->"


def generate_frontmatter(source: str = "sync", synced_at: str | None = None) -> str:
    """Produce a YAML frontmatter block with source and syncedAt fields."""
    if synced_at is None:
        synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"---\nsource: {source}\nsyncedAt: {synced_at}\n---\n"


def parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Split markdown content into (frontmatter_dict, body).

    Returns ({}, content) when there is no frontmatter block.
    """
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}, content

    raw, body = m.group(1), m.group(2)
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, body


def atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via a temporary file and os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


class ManagedBlockReseeded(Exception):
    """Raised when an existing file lacked a complete managed block.

    The original file is renamed to a timestamped ``.bak`` and a fresh seed is
    written in its place. Callers typically catch this to surface a warning.
    """

    def __init__(self, original: Path, backup: Path):
        self.original = original
        self.backup = backup
        super().__init__(f"{original} backed up to {backup} (incomplete managed block)")


def update_managed_block(
    path: Path,
    managed: str,
    *,
    seed_tail: str = "",
) -> Literal["created", "updated"]:
    """Write *managed* into the duct-managed region of *path*, preserving user content.

    *managed* must contain the start and end marker lines; the helper does not add
    them. The newline that terminates the end-marker line belongs to the managed
    block: a trailing newline is appended to *managed* if absent, and one newline
    immediately after the existing end marker is consumed when splicing in the tail.

    Returns ``"created"`` when the file did not previously exist (in which case
    *seed_tail* is appended), or ``"updated"`` when both markers were found and the
    enclosed region was replaced. If the file exists but lacks a complete managed
    block, it is renamed to a timestamped ``.bak`` and a fresh seed is written; this
    raises :class:`ManagedBlockReseeded` after the rewrite completes.
    """
    if not managed.endswith("\n"):
        managed = managed + "\n"

    if not path.exists():
        atomic_write(path, managed + seed_tail)
        return "created"

    existing = path.read_text(encoding="utf-8")
    start = existing.find(MANAGED_BLOCK_START)
    end = existing.find(MANAGED_BLOCK_END, start + 1) if start != -1 else -1

    if start == -1 or end == -1:
        backup = _backup_path(path)
        os.replace(path, backup)
        atomic_write(path, managed + seed_tail)
        raise ManagedBlockReseeded(original=path, backup=backup)

    prefix = existing[:start]
    tail_start = end + len(MANAGED_BLOCK_END)
    if tail_start < len(existing) and existing[tail_start] == "\n":
        tail_start += 1
    tail = existing[tail_start:]
    atomic_write(path, prefix + managed + tail)
    return "updated"


def _backup_path(path: Path) -> Path:
    """Return ``{path}.{YYYYmmddTHHMMSSZ}.bak`` for the current UTC time."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.{stamp}.bak")


def write_if_changed(path: Path, content: str) -> bool:
    """Write *content* only when it differs from the file on disk.

    Returns True if the file was (re)written, False if it already matched.
    """
    try:
        existing = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = None

    if existing == content:
        return False

    atomic_write(path, content)
    return True


def extract_table(body: str) -> list[dict[str, str]]:
    """Parse the first markdown table found in *body* into a list of row dicts.

    Each dict maps column header -> cell value (both stripped of whitespace).
    The separator row (containing dashes) is skipped automatically.
    """
    lines = [ln for ln in body.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 2:
        return []

    def _cells(line: str) -> list[str]:
        # Split on |, drop the empty first/last from leading/trailing pipes.
        parts = line.split("|")
        return [p.strip() for p in parts[1:-1]]

    headers = _cells(lines[0])
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        cells = _cells(line)
        # Skip separator rows (all cells are dashes / colons).
        if all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue
        row = {h: (cells[i] if i < len(cells) else "") for i, h in enumerate(headers)}
        rows.append(row)
    return rows

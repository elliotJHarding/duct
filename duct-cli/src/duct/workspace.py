"""Workspace directory layout helpers for duct.

Ticket directories live directly under the workspace root, named
``{KEY}-{slug}`` where KEY is a Jira-style ticket key (e.g. ERSC-1278).

A "ticket directory" contains an ``orchestrator/`` subdirectory.
Epic metadata lives in ``{root}/epics/`` as markdown files, and each ticket's
``orchestrator/`` directory optionally symlinks ``EPIC.md`` to its parent epic file.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from duct.markdown import TICKET_KEY_PATTERN, generate_frontmatter

if TYPE_CHECKING:
    from duct.config import SandboxConfig

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

def slug(text: str) -> str:
    """Convert *text* to a lowercase URL-style slug (a-z, 0-9, hyphens)."""
    s = _SLUG_STRIP_RE.sub("-", text.lower())
    return s.strip("-")


def branch_name(key: str, summary: str, issue_type: str) -> str:
    """Build a branch name like ``feature/ERSC-1278-case-file-updates``.

    Uses ``bugfix/`` for PS- project tickets or Bug issue types, ``feature/``
    for everything else.  Truncated to 80 characters.
    """
    prefix = "bugfix" if key.startswith("PS-") or issue_type.lower() == "bug" else "feature"
    return f"{prefix}/{key.upper()}-{slug(summary)}"[:80]


def read_issue_type(ticket_dir: Path) -> str:
    """Read the issue type from ``orchestrator/TICKET.md``.

    Returns the type string (e.g. ``"Story"``, ``"Bug"``), or an empty string
    if the file is missing or the field is not found.
    """
    ticket_md = ticket_dir / "orchestrator" / "TICKET.md"
    if not ticket_md.exists():
        return ""
    for line in ticket_md.read_text().splitlines():
        if line.strip().startswith("| Type |"):
            parts = line.split("|")
            if len(parts) >= 3:
                return parts[2].strip()
    return ""


def ticket_dir_name(key: str, summary: str) -> str:
    """Return the canonical directory name for a ticket.

    Format: ``{KEY}-{slugified-summary}``, truncated so the total length
    stays under 80 characters.
    """
    prefix = f"{key}-"
    max_slug = 80 - len(prefix)
    s = slug(summary)
    if len(s) > max_slug:
        s = s[:max_slug].rstrip("-")
    return f"{prefix}{s}"


# ---------------------------------------------------------------------------
# Directory resolution
# ---------------------------------------------------------------------------

def _key_from_dirname(name: str) -> str | None:
    """Extract a ticket key from the start of *name*, or return None."""
    m = re.match(rf"^({TICKET_KEY_PATTERN.pattern})-", name)
    return m.group(1) if m else None


def _is_ticket_dir(path: Path) -> bool:
    """True when *path* looks like a leaf ticket directory."""
    return path.is_dir() and (path / "orchestrator").is_dir()


def resolve_ticket_dir(root: Path, key: str) -> Path | None:
    """Find an existing ticket directory for *key* under *root*.

    Only scans the root level (flat layout).
    Returns the path if found, otherwise None.
    """
    prefix = f"{key}-"
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name.startswith(prefix) and _is_ticket_dir(child):
            return child
    return None


# ---------------------------------------------------------------------------
# Directory creation / mutation
# ---------------------------------------------------------------------------

def ensure_ticket_dir(
    root: Path,
    key: str,
    summary: str,
) -> Path:
    """Create a ticket directory directly under *root* and return its path.

    All tickets are placed at the root level (flat layout).  If the ticket
    already exists with a different name (summary changed), it is renamed.
    """
    existing = resolve_ticket_dir(root, key)
    dirname = ticket_dir_name(key, summary)
    target = root / dirname

    if existing and existing != target:
        shutil.move(str(existing), str(target))
    elif not existing:
        target.mkdir(parents=True, exist_ok=True)

    # Always ensure the orchestrator subdirectory exists.
    (target / "orchestrator").mkdir(exist_ok=True)
    return target


def ensure_epic_link(
    root: Path,
    ticket_dir: Path,
    epic_key: str,
    epic_summary: str | None = None,
) -> Path:
    """Create the epic metadata file and symlink EPIC.md in the orchestrator dir.

    - Creates ``{root}/epics/{EPIC_KEY}-{slug}.md`` if it doesn't exist.
    - Creates or updates ``{ticket_dir}/orchestrator/EPIC.md`` as a relative
      symlink to the epic file.

    Returns the path to the epic metadata file.
    """
    epics_dir = root / "epics"
    epics_dir.mkdir(exist_ok=True)

    epic_filename = ticket_dir_name(epic_key, epic_summary or epic_key) + ".md"
    epic_file = epics_dir / epic_filename

    if not epic_file.exists():
        content = generate_frontmatter(source="sync")
        content += f"\n# {epic_key}: {epic_summary or epic_key}\n"
        epic_file.write_text(content)

    orch_dir = ticket_dir / "orchestrator"
    orch_dir.mkdir(exist_ok=True)
    link_path = orch_dir / "EPIC.md"
    rel_target = os.path.relpath(epic_file, orch_dir)

    if link_path.is_symlink():
        current_target = os.readlink(link_path)
        if current_target != rel_target:
            link_path.unlink()
            link_path.symlink_to(rel_target)
    elif link_path.exists():
        # A regular file — replace with symlink.
        link_path.unlink()
        link_path.symlink_to(rel_target)
    else:
        link_path.symlink_to(rel_target)

    return epic_file


def orchestrator_dir(ticket_dir: Path) -> Path:
    """Return the ``orchestrator/`` subdirectory inside *ticket_dir*, creating it if needed."""
    d = ticket_dir / "orchestrator"
    d.mkdir(exist_ok=True)
    return d


def find_repo_dirs(ticket_dir: Path) -> list[Path]:
    """Return immediate child directories of *ticket_dir* that are git repos.

    A child counts as a repo when it contains a ``.git`` entry. The
    ``orchestrator/`` subdirectory is excluded. Result is sorted alphabetically.
    """
    if not ticket_dir.is_dir():
        return []
    return [
        child for child in sorted(ticket_dir.iterdir())
        if child.is_dir()
        and child.name != "orchestrator"
        and (child / ".git").exists()
    ]


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

def enumerate_ticket_dirs(root: Path) -> list[tuple[str, Path]]:
    """Scan *root* for all ticket directories (flat layout).

    Returns a list of ``(ticket_key, path)`` pairs.  Only scans the root
    level — tickets are never nested.
    """
    results: list[tuple[str, Path]] = []
    if not root.is_dir():
        return results

    for child in sorted(root.iterdir()):
        if child.name.startswith(".") or not child.is_dir():
            continue
        key = _key_from_dirname(child.name)
        if key and _is_ticket_dir(child):
            results.append((key, child))

    return results


# ---------------------------------------------------------------------------
# Archive / restore
# ---------------------------------------------------------------------------

def archive_ticket(root: Path, key: str) -> Path | None:
    """Move the ticket directory for *key* into ``root/.archive/``.

    Returns the archive path, or None if no matching ticket dir was found.
    """
    src = resolve_ticket_dir(root, key)
    if src is None:
        return None
    archive = root / ".archive"
    archive.mkdir(exist_ok=True)
    dest = archive / src.name
    # shutil.move into an existing directory nests the source inside it, which
    # would bury an already-archived copy. De-duplicate the destination name so
    # the existing archive is preserved untouched.
    if dest.exists():
        suffix = 1
        while (candidate := archive / f"{src.name}.{suffix}").exists():
            suffix += 1
        dest = candidate
    shutil.move(str(src), str(dest))
    return dest


def restore_ticket(root: Path, key: str) -> Path | None:
    """Move a ticket directory from ``.archive`` back into the workspace.

    Restores to the workspace root (flat layout).  Returns the restored path,
    or None if nothing was found in the archive.
    """
    archive = root / ".archive"
    if not archive.is_dir():
        return None

    prefix = f"{key}-"
    src: Path | None = None
    for child in sorted(archive.iterdir()):
        if child.is_dir() and child.name.startswith(prefix):
            src = child
            break
    if src is None:
        return None

    dest = root / src.name
    shutil.move(str(src), str(dest))
    return dest


# ---------------------------------------------------------------------------
# Worktree creation
# ---------------------------------------------------------------------------


def _fetch_base_branch(repo_path: Path, base_branch: str) -> str:
    """Fetch ``base_branch`` from origin and return the ref to branch from.

    Returns ``origin/<base_branch>`` when the fetch succeeds so the new
    worktree starts from the latest upstream commit. Falls back to the local
    ``<base_branch>`` ref (with a stderr warning) when there is no ``origin``
    remote, no network, or the branch is not present on origin.
    """
    result = subprocess.run(
        ["git", "fetch", "origin", base_branch],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 0:
        return f"origin/{base_branch}"

    detail = (result.stderr or result.stdout).strip()
    print(
        f"warning: could not fetch origin/{base_branch} "
        f"({detail or 'unknown error'}); branching from local {base_branch}",
        file=sys.stderr,
    )
    return base_branch


def create_worktree(
    ticket_dir: Path,
    repo_path: Path,
    repo_name: str,
    base_branch: str,
    feature_branch: str,
    sandbox: "SandboxConfig | None" = None,
) -> Path:
    """Create a git worktree for a ticket.

    Attempts to create ``feature_branch`` from ``base_branch``. If the branch
    already exists, falls back to checking it out into the worktree.

    Writes sandbox settings into the worktree when ``sandbox`` is enabled.

    Returns the worktree path. Raises ``RuntimeError`` on git failure.
    """
    worktree_path = ticket_dir / repo_name

    source_ref = _fetch_base_branch(repo_path, base_branch)

    result = subprocess.run(
        [
            "git", "worktree", "add",
            str(worktree_path), "-b", feature_branch,
            source_ref, "--no-track",
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        # Branch may already exist — try without -b
        result = subprocess.run(
            ["git", "worktree", "add", str(worktree_path), feature_branch],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    else:
        # Record what we forked from so the TUI can later show only this
        # branch's commits (git log <base>..HEAD). --no-track means there is
        # no upstream to recover this from afterwards.
        subprocess.run(
            ["git", "config", f"branch.{feature_branch}.duct-base", source_ref],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )

    if sandbox is not None and sandbox.enabled:
        from duct.sandbox import write_settings
        write_settings(worktree_path, sandbox)

    return worktree_path

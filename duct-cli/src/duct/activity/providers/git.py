"""Local git commits by the current user across workspace repos.

Walks `cfg.repo_paths` plus every repo worktree under each ticket directory,
filters commits by author email and by ``[since, until)``. Captures commits
that may not yet be pushed to GitHub.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from duct.activity.base import infer_ticket_key
from duct.config import WorkspaceConfig
from duct.models import ActivityEvent
from duct.workspace import enumerate_ticket_dirs


class GitActivityProvider:
    name = "git"

    def __init__(self, author_email: str | None = None):
        """``author_email`` — git commit author filter.

        When unset, resolves from ``git config user.email`` at fetch time,
        which matches the user's global-or-local git identity.
        """
        self._explicit_email = author_email

    def fetch(
        self,
        since: datetime,
        until: datetime,
        cfg: WorkspaceConfig,
    ) -> Iterator[ActivityEvent]:
        author = self._explicit_email or _git_user_email()
        if not author:
            return

        known_keys = {key for key, _ in enumerate_ticket_dirs(cfg.root)}
        for repo in _discover_repos(cfg):
            yield from self._commits_in(repo, author, since, until, known_keys)

    def _commits_in(
        self,
        repo: Path,
        author: str,
        since: datetime,
        until: datetime,
        known_keys: set[str],
    ) -> Iterator[ActivityEvent]:
        # %x00-separated fields keep parsing unambiguous vs arbitrary commit text.
        fmt = "%H%x00%aI%x00%an%x00%ae%x00%s%x00%D"
        try:
            result = subprocess.run(
                [
                    "git",
                    "log",
                    "--all",
                    "--no-merges",
                    f"--author={author}",
                    f"--since={_iso(since)}",
                    f"--until={_iso(until)}",
                    f"--pretty=format:{fmt}",
                ],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (subprocess.SubprocessError, OSError):
            return
        if result.returncode != 0:
            return

        for line in result.stdout.splitlines():
            parts = line.split("\x00")
            if len(parts) < 6:
                continue
            sha, iso_ts, name, email, subject, refs = parts[:6]
            ts = _normalise_ts(iso_ts)
            if not _in_window(ts, since, until):
                continue
            branch = _first_branch(refs)
            ticket = infer_ticket_key(f"{subject} {branch}", known_keys)
            yield ActivityEvent(
                event_id=f"git:{sha}",
                timestamp=ts,
                source=self.name,
                event_type="commit",
                actor=email or name or "unknown",
                summary=f"{repo.name}: {sha[:7]} — {subject[:140]}",
                ticket_key=ticket,
                url=None,
                detail={
                    "repo": str(repo),
                    "repo_name": repo.name,
                    "branch": branch,
                    "refs": refs,
                    "sha": sha,
                    "subject": subject,
                    "author_name": name,
                },
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git_user_email() -> str | None:
    try:
        result = subprocess.run(
            ["git", "config", "--get", "user.email"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    email = result.stdout.strip()
    return email or None


def _discover_repos(cfg: WorkspaceConfig) -> list[Path]:
    """Enumerate git repositories relevant to the workspace.

    Scans both the configured top-level `repo_paths` and the per-ticket
    worktree repos living under each ticket directory, so we pick up
    feature-branch checkouts regardless of whether they're siblings of
    the workspace root or nested within tickets.
    """
    seen: dict[Path, None] = {}

    def add(path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved not in seen and (resolved / ".git").exists():
            seen[resolved] = None

    def walk(path: Path, depth: int) -> None:
        if not path.is_dir():
            return
        if (path / ".git").exists():
            add(path)
            return  # don't descend into nested submodules
        if depth <= 0:
            return
        try:
            children = list(path.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_dir() and not child.name.startswith("."):
                walk(child, depth - 1)

    for base in cfg.repo_paths:
        walk(base.expanduser(), depth=2)

    for _, ticket_dir in enumerate_ticket_dirs(cfg.root):
        for child in ticket_dir.iterdir():
            if child.is_dir() and child.name != "orchestrator":
                add(child)

    return list(seen.keys())


def _first_branch(refs: str) -> str:
    """Pull a branch name out of ``git log --pretty=%D`` style refs output."""
    for raw in refs.split(","):
        ref = raw.strip()
        if not ref:
            continue
        if ref.startswith("HEAD -> "):
            return ref[len("HEAD -> "):].strip()
        if ref.startswith("HEAD"):
            continue
        if ref.startswith("tag: "):
            continue
        return ref
    return ""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalise_ts(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ts


def _in_window(ts: str, since: datetime, until: datetime) -> bool:
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return since <= dt < until

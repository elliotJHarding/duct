"""_branch_commits — show only the commits made on a worktree's own branch.

The base a branch forked from is recovered from (in priority) the persisted
``branch.<name>.duct-base`` config, the upstream, or the reflog fork point;
``_branch_commits`` then lists ``<base>..HEAD`` using whichever candidate gives
the tightest range. These tests drive the real git plumbing on a temp repo.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from duct.api import _branch_commits

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "t@t.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "t@t.com",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, env=_ENV,
    ).stdout.strip()


def _commit(repo: Path, message: str) -> None:
    (repo / message.replace(" ", "_")).write_text(message)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def _repo_on_branch(tmp_path: Path) -> Path:
    """A repo with `main` (2 commits) and a `feature` branch (+2 commits)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "checkout", "-b", "main")
    _commit(repo, "base one")
    _commit(repo, "base two")
    # Explicit start point, mirroring `git worktree add -b feature <base>` —
    # this is what records "branch: Created from main" in the reflog.
    _git(repo, "checkout", "-b", "feature", "main")
    _commit(repo, "feature one")
    _commit(repo, "feature two")
    return repo


def test_lists_only_branch_commits_via_persisted_base(tmp_path: Path):
    repo = _repo_on_branch(tmp_path)
    _git(repo, "config", "branch.feature.duct-base", "main")

    commits = _branch_commits(repo, "feature")

    subjects = " ".join(commits)
    assert "feature one" in subjects
    assert "feature two" in subjects
    assert "base one" not in subjects  # inherited history is excluded
    assert len(commits) == 2


def test_recovers_base_from_reflog_without_config(tmp_path: Path):
    repo = _repo_on_branch(tmp_path)  # reflog records "Created from main"

    commits = _branch_commits(repo, "feature")

    assert len(commits) == 2
    assert all("base" not in c for c in commits)


def test_prefers_tightest_base_over_a_stale_one(tmp_path: Path):
    """A stale recorded base over-reports; the tighter candidate must win."""
    repo = _repo_on_branch(tmp_path)
    # Persisted base is a stale ref at the first commit (old-base..HEAD == 3),
    # while the reflog fork point `main` is tighter (main..HEAD == 2). The
    # tightest candidate (2) must be chosen.
    first = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    _git(repo, "branch", "old-base", first)
    _git(repo, "config", "branch.feature.duct-base", "old-base")

    commits = _branch_commits(repo, "feature")

    assert len(commits) == 2  # main..HEAD, not old-base..HEAD (which is 3)


def test_falls_back_to_recent_when_no_base(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "checkout", "-b", "main")
    _commit(repo, "only one")

    # `main` has no fork point; expire the reflog so nothing resolves.
    _git(repo, "reflog", "expire", "--expire=all", "--all")
    commits = _branch_commits(repo, "main")

    assert len(commits) == 1
    assert "only one" in commits[0]

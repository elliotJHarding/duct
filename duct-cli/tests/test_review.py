"""Tests for duct.review (deep-review helpers)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from duct.config import WorkspaceConfig
from duct.models import PullRequest
from duct.review import open_in_intellij, prepare_local_review


def _pr(repo: str = "acme/web", branch: str = "feature-x", number: int = 1) -> PullRequest:
    return PullRequest(
        number=number,
        title="t",
        repo=repo,
        state="open",
        author="a",
        is_draft=False,
        review_status="pending",
        ci_status="passing",
        url="https://github.com/acme/web/pull/1",
        created_at="2026-03-20T10:00:00Z",
        updated_at="2026-03-20T10:00:00Z",
        branch=branch,
    )


class _FakeRunner:
    """Scripted subprocess.run stand-in that records calls."""

    def __init__(self, responses: list[tuple[int, str]] | None = None) -> None:
        # Responses are keyed by the git subcommand (first arg after 'git').
        # If no responses supplied, every call succeeds with empty output.
        self.responses = responses or []
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if self.responses:
            returncode, output = self.responses.pop(0)
        else:
            returncode, output = 0, ""
        return subprocess.CompletedProcess(cmd, returncode, stdout=output, stderr="")


def test_prepare_local_review_uses_existing_repo(tmp_path: Path) -> None:
    repo_dir = tmp_path / "web"
    repo_dir.mkdir()
    cfg = WorkspaceConfig(repo_paths=[tmp_path])

    runner = _FakeRunner([
        (0, ""),  # git fetch
        (0, ""),  # git status --porcelain (clean)
        (0, ""),  # git rev-parse (branch exists locally)
        (0, ""),  # git checkout
        (0, ""),  # git pull --ff-only
    ])

    with (
        patch("duct.review.find_repo", return_value=repo_dir),
        patch("duct.review.clone_repo") as mock_clone,
        patch("duct.review.subprocess.run", side_effect=runner),
    ):
        result = prepare_local_review(cfg, _pr())

    assert result == repo_dir
    mock_clone.assert_not_called()
    cmds = [c[1] for c in runner.calls]  # git <subcmd>
    assert cmds == ["fetch", "status", "rev-parse", "checkout", "pull"]


def test_prepare_local_review_clones_when_missing(tmp_path: Path) -> None:
    cfg = WorkspaceConfig(repo_paths=[tmp_path])
    cloned_dir = tmp_path / "web"

    runner = _FakeRunner([
        (0, ""),  # fetch
        (0, ""),  # status (clean)
        (0, ""),  # rev-parse (local branch exists)
        (0, ""),  # checkout
        (0, ""),  # pull
    ])

    with (
        patch("duct.review.find_repo", return_value=None),
        patch("duct.review.clone_repo", return_value=cloned_dir) as mock_clone,
        patch("duct.review.subprocess.run", side_effect=runner),
    ):
        result = prepare_local_review(cfg, _pr())

    assert result == cloned_dir
    mock_clone.assert_called_once_with("acme/web", tmp_path)


def test_prepare_local_review_rejects_dirty_tree(tmp_path: Path) -> None:
    repo_dir = tmp_path / "web"
    repo_dir.mkdir()
    cfg = WorkspaceConfig(repo_paths=[tmp_path])

    runner = _FakeRunner([
        (0, ""),                # fetch
        (0, " M file.py\n"),    # status reports changes
    ])

    with (
        patch("duct.review.find_repo", return_value=repo_dir),
        patch("duct.review.subprocess.run", side_effect=runner),
    ):
        with pytest.raises(RuntimeError, match="uncommitted changes"):
            prepare_local_review(cfg, _pr())


def test_prepare_local_review_creates_tracking_branch(tmp_path: Path) -> None:
    repo_dir = tmp_path / "web"
    repo_dir.mkdir()
    cfg = WorkspaceConfig(repo_paths=[tmp_path])

    runner = _FakeRunner([
        (0, ""),   # fetch
        (0, ""),   # status (clean)
        (1, ""),   # rev-parse fails → local branch missing
        (0, ""),   # checkout -b
        (0, ""),   # pull
    ])

    with (
        patch("duct.review.find_repo", return_value=repo_dir),
        patch("duct.review.subprocess.run", side_effect=runner),
    ):
        prepare_local_review(cfg, _pr())

    checkout = runner.calls[3]
    assert checkout[1:] == ["checkout", "-b", "feature-x", "origin/feature-x"]


def test_prepare_local_review_rejects_missing_branch(tmp_path: Path) -> None:
    cfg = WorkspaceConfig(repo_paths=[tmp_path])
    with pytest.raises(RuntimeError, match="no head branch"):
        prepare_local_review(cfg, _pr(branch=""))


def test_prepare_local_review_rejects_bad_repo_slug(tmp_path: Path) -> None:
    cfg = WorkspaceConfig(repo_paths=[tmp_path])
    with pytest.raises(RuntimeError, match="no usable repo slug"):
        prepare_local_review(cfg, _pr(repo=""))


def test_open_in_intellij_invokes_idea(tmp_path: Path) -> None:
    with patch("duct.review.subprocess.run") as mock_run:
        open_in_intellij(tmp_path)
    mock_run.assert_called_once_with(
        ["idea", str(tmp_path)], check=True, timeout=10,
    )


def test_open_in_intellij_surfaces_missing_binary(tmp_path: Path) -> None:
    with patch("duct.review.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(RuntimeError, match="idea"):
            open_in_intellij(tmp_path)

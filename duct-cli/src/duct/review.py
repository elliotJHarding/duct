"""Helpers for the 'deep review' PR action.

Given a PullRequest, ensures a local clone exists, checks out the PR's head
branch, and opens the repo directory in IntelliJ. Intended for reviewers who
want to use IntelliJ's GitHub plugin rather than GitHub's web UI.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from duct.cli.workspace_cmd import clone_repo, find_repo
from duct.config import WorkspaceConfig
from duct.models import PullRequest


def prepare_local_review(cfg: WorkspaceConfig, pr: PullRequest) -> Path:
    """Ensure the PR's repo is cloned locally and its head branch is checked out.

    Returns the path to the repo. Raises ``RuntimeError`` on any failure with
    a message suitable for surfacing to the user.
    """
    if not pr.repo or "/" not in pr.repo:
        raise RuntimeError(f"PR has no usable repo slug: {pr.repo!r}")
    if not pr.branch:
        raise RuntimeError(f"PR #{pr.number} has no head branch recorded")

    repo_name = pr.repo.split("/", 1)[1]
    repo_path = find_repo(cfg, repo_name)

    if repo_path is None:
        if not cfg.repo_paths:
            raise RuntimeError(
                "Cannot clone: no repoPaths configured. "
                "Add one with: duct config add-repo-path <dir>"
            )
        dest_parent = cfg.repo_paths[0]
        if not dest_parent.is_dir():
            raise RuntimeError(
                f"Cannot clone: configured repoPath does not exist: {dest_parent}"
            )
        repo_path = clone_repo(pr.repo, dest_parent)

    _checkout_branch(repo_path, pr.branch)
    return repo_path


def _checkout_branch(repo_path: Path, branch: str) -> None:
    _run_git(repo_path, ["fetch", "origin", branch], timeout=60)

    status = _run_git(repo_path, ["status", "--porcelain"], timeout=10)
    if status.stdout.strip():
        raise RuntimeError(
            f"Working tree at {repo_path} has uncommitted changes. "
            f"Commit, stash, or discard them before switching to {branch}."
        )

    local_exists = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    ).returncode == 0

    if local_exists:
        _run_git(repo_path, ["checkout", branch], timeout=30)
    else:
        _run_git(
            repo_path,
            ["checkout", "-b", branch, f"origin/{branch}"],
            timeout=30,
        )

    # Best-effort fast-forward; reviewer can sort out divergence in IntelliJ.
    subprocess.run(
        ["git", "pull", "--ff-only"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _run_git(cwd: Path, args: list[str], *, timeout: int) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result


def open_in_intellij(path: Path) -> None:
    """Launch IntelliJ IDEA on ``path`` via the ``idea`` CLI."""
    try:
        subprocess.run(["idea", str(path)], check=True, timeout=10)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "IntelliJ 'idea' command not found on PATH. "
            "Install via JetBrains Toolbox > IDEA > Settings > "
            "Create Command-line Launcher."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"idea exited with status {exc.returncode}") from exc

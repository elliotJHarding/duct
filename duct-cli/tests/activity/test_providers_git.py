"""Tests for GitActivityProvider using real local git repositories."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from duct.activity.providers.git import GitActivityProvider
from duct.config import WorkspaceConfig


def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> None:
    base_env = {
        "GIT_AUTHOR_NAME": "Alice",
        "GIT_AUTHOR_EMAIL": "alice@example.com",
        "GIT_COMMITTER_NAME": "Alice",
        "GIT_COMMITTER_EMAIL": "alice@example.com",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    if env:
        base_env.update(env)
    subprocess.run(cmd, cwd=str(cwd), check=True, env=base_env, capture_output=True)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-b", "main"], path)
    _run(["git", "config", "user.email", "alice@example.com"], path)
    _run(["git", "config", "user.name", "Alice"], path)


def _commit(path: Path, message: str, filename: str = "README.md") -> None:
    (path / filename).write_text(f"{message}\n", encoding="utf-8")
    _run(["git", "add", filename], path)
    _run(["git", "commit", "-m", message], path)


class TestGitProvider:
    def test_emits_commits_by_author_in_window(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "ERSC-1278: first commit")
        _commit(repo, "FOO-99: second commit")

        cfg = WorkspaceConfig(root=tmp_path, repo_paths=[tmp_path])
        provider = GitActivityProvider(author_email="alice@example.com")
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        until = datetime.now(timezone.utc)
        events = list(provider.fetch(since, until, cfg))

        assert len(events) == 2
        subjects = [e.detail["subject"] for e in events]
        assert "ERSC-1278: first commit" in subjects
        assert "FOO-99: second commit" in subjects
        assert all(e.source == "git" for e in events)
        assert all(e.event_type == "commit" for e in events)

    def test_skips_other_authors(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "mine")
        _run(
            ["git", "commit", "--allow-empty", "-m", "theirs"],
            repo,
            env={
                "GIT_AUTHOR_NAME": "Bob",
                "GIT_AUTHOR_EMAIL": "bob@example.com",
                "GIT_COMMITTER_NAME": "Bob",
                "GIT_COMMITTER_EMAIL": "bob@example.com",
            },
        )

        cfg = WorkspaceConfig(root=tmp_path, repo_paths=[tmp_path])
        provider = GitActivityProvider(author_email="alice@example.com")
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        until = datetime.now(timezone.utc)
        events = list(provider.fetch(since, until, cfg))

        subjects = [e.detail["subject"] for e in events]
        assert "mine" in subjects
        assert "theirs" not in subjects

    def test_event_id_is_commit_sha(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "only commit")
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True
        )
        sha = result.stdout.strip()

        cfg = WorkspaceConfig(root=tmp_path, repo_paths=[tmp_path])
        provider = GitActivityProvider(author_email="alice@example.com")
        events = list(
            provider.fetch(
                datetime(2020, 1, 1, tzinfo=timezone.utc),
                datetime.now(timezone.utc),
                cfg,
            )
        )
        assert len(events) == 1
        assert events[0].event_id == f"git:{sha}"

    def test_ticket_inference_from_commit_message(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        _commit(repo, "ERSC-1278: implement foo")

        # Create a matching ticket dir so the known_keys set includes it.
        ticket_dir = tmp_path / "ERSC-1278-implement-foo"
        (ticket_dir / "orchestrator").mkdir(parents=True)

        cfg = WorkspaceConfig(root=tmp_path, repo_paths=[tmp_path])
        provider = GitActivityProvider(author_email="alice@example.com")
        events = list(
            provider.fetch(
                datetime(2020, 1, 1, tzinfo=timezone.utc),
                datetime.now(timezone.utc),
                cfg,
            )
        )
        assert len(events) == 1
        assert events[0].ticket_key == "ERSC-1278"

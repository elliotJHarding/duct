"""Tests for duct.sync.workspace_sync."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from duct.sync.workspace_sync import WorkspaceSync


def _make_git_repo(path: Path) -> None:
    """Create a minimal git repo with one commit."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    (path / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "t@t.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "t@t.com",
        },
    )


def _make_ticket_dir(root: Path, name: str) -> Path:
    """Create a ticket directory with an orchestrator/ subdirectory."""
    ticket = root / name
    ticket.mkdir(parents=True, exist_ok=True)
    (ticket / "orchestrator").mkdir(exist_ok=True)
    return ticket


# ---------------------------------------------------------------------------
# _find_repos
# ---------------------------------------------------------------------------

class TestFindRepos:
    def test_finds_git_repos(self, tmp_path: Path):
        ticket = _make_ticket_dir(tmp_path, "ERSC-100-task")
        _make_git_repo(ticket / "my-service")
        _make_git_repo(ticket / "another-repo")

        ws = WorkspaceSync()
        repos = ws._find_repos(ticket)

        names = [r["name"] for r in repos]
        assert "my-service" in names
        assert "another-repo" in names

    def test_ignores_orchestrator_dir(self, tmp_path: Path):
        ticket = _make_ticket_dir(tmp_path, "ERSC-101-task")
        _make_git_repo(ticket / "my-service")

        ws = WorkspaceSync()
        repos = ws._find_repos(ticket)

        names = [r["name"] for r in repos]
        assert "orchestrator" not in names

    def test_ignores_non_git_dirs(self, tmp_path: Path):
        ticket = _make_ticket_dir(tmp_path, "ERSC-102-task")
        (ticket / "not-a-repo").mkdir()
        _make_git_repo(ticket / "real-repo")

        ws = WorkspaceSync()
        repos = ws._find_repos(ticket)

        assert len(repos) == 1
        assert repos[0]["name"] == "real-repo"

    def test_returns_empty_when_no_repos(self, tmp_path: Path):
        ticket = _make_ticket_dir(tmp_path, "ERSC-103-task")

        ws = WorkspaceSync()
        repos = ws._find_repos(ticket)

        assert repos == []


# ---------------------------------------------------------------------------
# _repo_info
# ---------------------------------------------------------------------------

class TestRepoInfo:
    def test_extracts_branch(self, tmp_path: Path):
        repo_path = tmp_path / "repo"
        _make_git_repo(repo_path)

        ws = WorkspaceSync()
        info = ws._repo_info(repo_path)

        # Default branch is usually main or master depending on git config
        assert info["branch"] in ("main", "master")

    def test_clean_status(self, tmp_path: Path):
        repo_path = tmp_path / "repo"
        _make_git_repo(repo_path)

        ws = WorkspaceSync()
        info = ws._repo_info(repo_path)

        assert info["dirty"] is False
        assert info["changes"] == 0

    def test_dirty_status(self, tmp_path: Path):
        repo_path = tmp_path / "repo"
        _make_git_repo(repo_path)
        (repo_path / "new_file.txt").write_text("uncommitted")

        ws = WorkspaceSync()
        info = ws._repo_info(repo_path)

        assert info["dirty"] is True
        assert info["changes"] > 0

    def test_recent_commits(self, tmp_path: Path):
        repo_path = tmp_path / "repo"
        _make_git_repo(repo_path)

        ws = WorkspaceSync()
        info = ws._repo_info(repo_path)

        assert len(info["recent_commits"]) >= 1
        assert "init" in info["recent_commits"][0]


# ---------------------------------------------------------------------------
# _write_workspace_md
# ---------------------------------------------------------------------------

class TestWriteWorkspaceMd:
    def test_writes_expected_format(self, tmp_path: Path):
        ticket = _make_ticket_dir(tmp_path, "ERSC-200-task")
        repos = [
            {
                "name": "my-service",
                "path": "/some/path/my-service",
                "branch": "feature/thing",
                "dirty": True,
                "changes": 3,
                "recent_commits": ["abc1234 fix bug", "def5678 add feature"],
            },
            {
                "name": "lib",
                "path": "/some/path/lib",
                "branch": "main",
                "dirty": False,
                "changes": 0,
                "recent_commits": [],
            },
        ]

        ws = WorkspaceSync()
        ws._write_workspace_md(repos, ticket)

        md_path = ticket / "orchestrator" / "WORKSPACE.md"
        assert md_path.exists()

        content = md_path.read_text()
        assert "# Workspace" in content
        assert "## Repos" in content
        assert "### my-service" in content
        assert "feature/thing" in content
        assert "dirty" in content
        assert "(3 changes)" in content
        assert "abc1234 fix bug" in content
        assert "### lib" in content
        assert "clean" in content

    def test_has_frontmatter(self, tmp_path: Path):
        ticket = _make_ticket_dir(tmp_path, "ERSC-201-task")
        repos = [
            {
                "name": "repo",
                "path": "/p/repo",
                "branch": "main",
                "dirty": False,
                "changes": 0,
                "recent_commits": [],
            },
        ]

        ws = WorkspaceSync()
        ws._write_workspace_md(repos, ticket)

        content = (ticket / "orchestrator" / "WORKSPACE.md").read_text()
        assert content.startswith("---\n")
        assert "source: sync" in content
        assert "syncedAt:" in content


# ---------------------------------------------------------------------------
# Full sync
# ---------------------------------------------------------------------------

class TestFullSync:
    def test_sync_with_git_repo(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()
        ticket = _make_ticket_dir(root, "ERSC-300-task")
        _make_git_repo(ticket / "my-service")

        ws = WorkspaceSync()
        result = ws.sync(root)

        assert result.source == "workspace"
        assert result.tickets_synced == 1
        assert result.errors == []
        assert (ticket / "orchestrator" / "WORKSPACE.md").exists()

    def test_sync_skips_tickets_without_repos(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()
        _make_ticket_dir(root, "ERSC-301-no-repos")

        ws = WorkspaceSync()
        result = ws.sync(root)

        assert result.tickets_synced == 0

    def test_sync_empty_workspace(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()

        ws = WorkspaceSync()
        result = ws.sync(root)

        assert result.tickets_synced == 0
        assert result.errors == []

    def test_sync_multiple_tickets(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()

        t1 = _make_ticket_dir(root, "ERSC-400-first")
        _make_git_repo(t1 / "svc-a")

        t2 = _make_ticket_dir(root, "ERSC-401-second")
        _make_git_repo(t2 / "svc-b")

        ws = WorkspaceSync()
        result = ws.sync(root)

        assert result.tickets_synced == 2
        assert (t1 / "orchestrator" / "WORKSPACE.md").exists()
        assert (t2 / "orchestrator" / "WORKSPACE.md").exists()

    def test_template_propagates_to_every_ticket_dir(self, tmp_path: Path):
        """settings.template.json at root is merged into every ticket workspace."""
        root = tmp_path / "workspace"
        root.mkdir()

        template = {"env": {"CLAUDE_CODE_ENABLE_TELEMETRY": "1"}}
        (root / "settings.template.json").write_text(json.dumps(template))

        with_repo = _make_ticket_dir(root, "ERSC-500-with-repo")
        _make_git_repo(with_repo / "svc")
        without_repo = _make_ticket_dir(root, "ERSC-501-without-repo")

        result = WorkspaceSync().sync(root)

        assert result.errors == []
        assert result.tickets_synced == 2

        for ticket in (with_repo, without_repo):
            settings_path = ticket / ".claude" / "settings.json"
            assert settings_path.exists(), f"missing settings for {ticket.name}"
            assert json.loads(settings_path.read_text()) == template

        # Root workspace settings file is NOT created by this sync step —
        # telemetry is deliberately scoped to work sessions.
        assert not (root / ".claude" / "settings.json").exists()

    def test_no_template_leaves_ticket_settings_untouched(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()
        ticket = _make_ticket_dir(root, "ERSC-502-task")
        _make_git_repo(ticket / "svc")

        WorkspaceSync().sync(root)

        assert not (ticket / ".claude" / "settings.json").exists()

"""Tests for duct.workspace utilities."""

import os
import subprocess
from pathlib import Path

from duct.workspace import (
    archive_ticket,
    branch_name,
    create_worktree,
    ensure_epic_link,
    ensure_ticket_dir,
    enumerate_ticket_dirs,
    find_repo_dirs,
    orchestrator_dir,
    read_issue_type,
    resolve_ticket_dir,
    restore_ticket,
    slug,
    ticket_dir_name,
)

# ---------------------------------------------------------------------------
# slug()
# ---------------------------------------------------------------------------

class TestSlug:
    def test_basic(self):
        assert slug("Fix Auth Middleware") == "fix-auth-middleware"

    def test_special_characters(self):
        assert slug("Hello, World! (v2)") == "hello-world-v2"

    def test_already_clean(self):
        assert slug("already-clean") == "already-clean"

    def test_leading_trailing_stripped(self):
        assert slug("  --hello--  ") == "hello"

    def test_collapses_multiple_hyphens(self):
        assert slug("a   b") == "a-b"


# ---------------------------------------------------------------------------
# branch_name()
# ---------------------------------------------------------------------------

class TestBranchName:
    def test_feature_branch(self):
        assert branch_name("ERSC-1278", "case file updates", "Story") == "feature/ERSC-1278-case-file-updates"

    def test_ps_project_is_bugfix(self):
        assert branch_name("PS-412", "null pointer on submit", "Task") == "bugfix/PS-412-null-pointer-on-submit"

    def test_bug_type_is_bugfix(self):
        assert branch_name("AZIE-100", "login crash", "Bug") == "bugfix/AZIE-100-login-crash"

    def test_key_uppercased(self):
        result = branch_name("ersc-50", "some title", "Story")
        assert result.startswith("feature/ERSC-50-")

    def test_truncation(self):
        long_title = "a very long title that goes on and on " * 5
        result = branch_name("ERSC-1278", long_title, "Story")
        assert len(result) <= 80


# ---------------------------------------------------------------------------
# read_issue_type()
# ---------------------------------------------------------------------------

class TestReadIssueType:
    def test_reads_type(self, tmp_path: Path):
        ticket_dir = tmp_path / "ERSC-100-test"
        (ticket_dir / "orchestrator").mkdir(parents=True)
        (ticket_dir / "orchestrator" / "TICKET.md").write_text(
            "| Field | Value |\n|-------|-------|\n| Status | Open |\n| Type | Bug |\n"
        )
        assert read_issue_type(ticket_dir) == "Bug"

    def test_missing_file(self, tmp_path: Path):
        ticket_dir = tmp_path / "ERSC-200-test"
        (ticket_dir / "orchestrator").mkdir(parents=True)
        assert read_issue_type(ticket_dir) == ""

    def test_no_type_row(self, tmp_path: Path):
        ticket_dir = tmp_path / "ERSC-300-test"
        (ticket_dir / "orchestrator").mkdir(parents=True)
        (ticket_dir / "orchestrator" / "TICKET.md").write_text("# Ticket\nNo table here.\n")
        assert read_issue_type(ticket_dir) == ""


# ---------------------------------------------------------------------------
# ticket_dir_name()
# ---------------------------------------------------------------------------

class TestTicketDirName:
    def test_format(self):
        result = ticket_dir_name("ERSC-1278", "Fix auth middleware")
        assert result == "ERSC-1278-fix-auth-middleware"

    def test_truncation(self):
        long_summary = "a" * 200
        name = ticket_dir_name("ERSC-1", long_summary)
        assert len(name) <= 80
        assert name.startswith("ERSC-1-")


# ---------------------------------------------------------------------------
# resolve_ticket_dir()
# ---------------------------------------------------------------------------

class TestResolveTicketDir:
    def test_finds_at_root(self, tmp_workspace: Path):
        d = tmp_workspace / "ERSC-100-some-task"
        d.mkdir()
        (d / "orchestrator").mkdir()
        assert resolve_ticket_dir(tmp_workspace, "ERSC-100") == d

    def test_returns_none_when_missing(self, tmp_workspace: Path):
        assert resolve_ticket_dir(tmp_workspace, "ERSC-999") is None


# ---------------------------------------------------------------------------
# ensure_ticket_dir()
# ---------------------------------------------------------------------------

class TestEnsureTicketDir:
    def test_creates_new_dir(self, tmp_workspace: Path):
        path = ensure_ticket_dir(tmp_workspace, "ERSC-200", "New feature")
        assert path.exists()
        assert (path / "orchestrator").is_dir()
        assert path.name == "ERSC-200-new-feature"
        assert path.parent == tmp_workspace

    def test_renames_when_summary_changes(self, tmp_workspace: Path):
        original = ensure_ticket_dir(tmp_workspace, "ERSC-300", "Old name")
        assert original.parent == tmp_workspace

        renamed = ensure_ticket_dir(tmp_workspace, "ERSC-300", "New name")
        assert not original.exists()
        assert renamed.exists()
        assert renamed.name == "ERSC-300-new-name"
        assert renamed.parent == tmp_workspace


# ---------------------------------------------------------------------------
# ensure_epic_link()
# ---------------------------------------------------------------------------

class TestEnsureEpicLink:
    def test_creates_epic_file_and_symlink(self, tmp_workspace: Path):
        ticket_dir = ensure_ticket_dir(tmp_workspace, "ERSC-201", "Sub task")
        epic_file = ensure_epic_link(
            tmp_workspace, ticket_dir, "ERSC-100", "Platform epic",
        )

        # Epic file exists in epics/ dir.
        assert epic_file.exists()
        assert epic_file.parent.name == "epics"
        assert "ERSC-100" in epic_file.name

        # Symlink exists inside orchestrator/ and resolves to the epic file.
        link = ticket_dir / "orchestrator" / "EPIC.md"
        assert link.is_symlink()
        assert link.resolve() == epic_file.resolve()

        # Symlink is relative.
        target = os.readlink(link)
        assert not os.path.isabs(target)

        # Epic file has frontmatter and heading.
        content = epic_file.read_text()
        assert "source: sync" in content
        assert "# ERSC-100: Platform epic" in content

    def test_updates_symlink_when_epic_changes(self, tmp_workspace: Path):
        ticket_dir = ensure_ticket_dir(tmp_workspace, "ERSC-300", "My task")

        # Link to first epic.
        ensure_epic_link(tmp_workspace, ticket_dir, "ERSC-50", "First epic")
        link = ticket_dir / "orchestrator" / "EPIC.md"
        first_target = os.readlink(link)

        # Link to second epic — symlink should update.
        ensure_epic_link(tmp_workspace, ticket_dir, "ERSC-60", "Second epic")
        second_target = os.readlink(link)
        assert first_target != second_target
        assert "ERSC-60" in second_target

    def test_does_not_overwrite_existing_epic_file(self, tmp_workspace: Path):
        ticket_dir = ensure_ticket_dir(tmp_workspace, "ERSC-400", "Task")
        epic_file = ensure_epic_link(
            tmp_workspace, ticket_dir, "ERSC-10", "My epic",
        )
        original_content = epic_file.read_text()

        # Call again — should not overwrite the file.
        ensure_epic_link(tmp_workspace, ticket_dir, "ERSC-10", "My epic")
        assert epic_file.read_text() == original_content


# ---------------------------------------------------------------------------
# orchestrator_dir()
# ---------------------------------------------------------------------------

class TestOrchestratorDir:
    def test_creates_and_returns(self, tmp_workspace: Path):
        ticket = tmp_workspace / "ERSC-400-test"
        ticket.mkdir()
        result = orchestrator_dir(ticket)
        assert result == ticket / "orchestrator"
        assert result.is_dir()


# ---------------------------------------------------------------------------
# enumerate_ticket_dirs()
# ---------------------------------------------------------------------------

class TestEnumerateTicketDirs:
    def test_finds_root_level(self, tmp_workspace: Path):
        d = tmp_workspace / "ERSC-500-task"
        d.mkdir()
        (d / "orchestrator").mkdir()
        results = enumerate_ticket_dirs(tmp_workspace)
        assert ("ERSC-500", d) in results

    def test_skips_epics_dir(self, tmp_workspace: Path):
        """The epics/ directory should not produce results."""
        epics = tmp_workspace / "epics"
        epics.mkdir()
        d = tmp_workspace / "ERSC-500-task"
        d.mkdir()
        (d / "orchestrator").mkdir()
        results = enumerate_ticket_dirs(tmp_workspace)
        assert len(results) == 1
        assert ("ERSC-500", d) in results

    def test_empty_workspace(self, tmp_workspace: Path):
        assert enumerate_ticket_dirs(tmp_workspace) == []


# ---------------------------------------------------------------------------
# find_repo_dirs()
# ---------------------------------------------------------------------------

class TestFindRepoDirs:
    def _make_repo(self, parent: Path, name: str) -> Path:
        repo = parent / name
        repo.mkdir()
        (repo / ".git").mkdir()
        return repo

    def test_returns_git_subdirs_alphabetically(self, tmp_path: Path):
        ticket = tmp_path / "ERSC-1-task"
        ticket.mkdir()
        self._make_repo(ticket, "policy")
        self._make_repo(ticket, "claims")
        assert [p.name for p in find_repo_dirs(ticket)] == ["claims", "policy"]

    def test_excludes_orchestrator_even_if_git(self, tmp_path: Path):
        ticket = tmp_path / "ERSC-2-task"
        ticket.mkdir()
        orch = ticket / "orchestrator"
        orch.mkdir()
        (orch / ".git").mkdir()
        self._make_repo(ticket, "claims")
        assert [p.name for p in find_repo_dirs(ticket)] == ["claims"]

    def test_excludes_non_git_subdirs(self, tmp_path: Path):
        ticket = tmp_path / "ERSC-3-task"
        ticket.mkdir()
        (ticket / "notes").mkdir()
        self._make_repo(ticket, "claims")
        assert [p.name for p in find_repo_dirs(ticket)] == ["claims"]

    def test_empty_when_no_repos(self, tmp_path: Path):
        ticket = tmp_path / "ERSC-4-task"
        ticket.mkdir()
        (ticket / "orchestrator").mkdir()
        assert find_repo_dirs(ticket) == []

    def test_returns_empty_when_ticket_dir_missing(self, tmp_path: Path):
        assert find_repo_dirs(tmp_path / "missing") == []

    def test_skips_dotdirs(self, tmp_workspace: Path):
        hidden = tmp_workspace / ".archive"
        hidden.mkdir()
        d = hidden / "ERSC-600-old"
        d.mkdir()
        (d / "orchestrator").mkdir()
        assert enumerate_ticket_dirs(tmp_workspace) == []


# ---------------------------------------------------------------------------
# archive / restore
# ---------------------------------------------------------------------------

class TestArchiveTicket:
    def test_moves_to_archive(self, tmp_workspace: Path):
        d = tmp_workspace / "ERSC-700-task"
        d.mkdir()
        (d / "orchestrator").mkdir()
        result = archive_ticket(tmp_workspace, "ERSC-700")
        assert result is not None
        assert result.parent.name == ".archive"
        assert not d.exists()

    def test_returns_none_when_missing(self, tmp_workspace: Path):
        assert archive_ticket(tmp_workspace, "ERSC-999") is None


class TestRestoreTicket:
    def test_restores_to_root(self, tmp_workspace: Path):
        # Set up archive.
        archive = tmp_workspace / ".archive"
        archive.mkdir()
        d = archive / "ERSC-800-task"
        d.mkdir()
        (d / "orchestrator").mkdir()

        result = restore_ticket(tmp_workspace, "ERSC-800")
        assert result is not None
        assert result.parent == tmp_workspace
        assert result.is_dir()

    def test_returns_none_when_no_archive(self, tmp_workspace: Path):
        assert restore_ticket(tmp_workspace, "ERSC-999") is None


# ---------------------------------------------------------------------------
# create_worktree()
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> str:
    env = {
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@example.com",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@example.com",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _make_repo_with_origin(tmp_path: Path) -> tuple[Path, Path]:
    """Build a working repo with an ``origin`` bare remote.

    Returns (repo_path, origin_path). The bare ``origin`` carries an extra
    commit on ``main`` that the local clone has not pulled yet, so the local
    ``main`` ref is one commit behind ``origin/main``.
    """
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(["init", "-b", "main"], seed)
    (seed / "README.md").write_text("v1\n")
    _git(["add", "README.md"], seed)
    _git(["commit", "-m", "v1"], seed)

    origin = tmp_path / "origin.git"
    _git(["clone", "--bare", str(seed), str(origin)], tmp_path)

    repo = tmp_path / "repo"
    _git(["clone", str(origin), str(repo)], tmp_path)

    # Advance origin/main past the local clone via a second working copy.
    other = tmp_path / "other"
    _git(["clone", str(origin), str(other)], tmp_path)
    (other / "README.md").write_text("v2\n")
    _git(["add", "README.md"], other)
    _git(["commit", "-m", "v2"], other)
    _git(["push", "origin", "main"], other)

    return repo, origin


class TestCreateWorktree:
    def test_branches_from_origin_when_local_is_stale(self, tmp_path: Path):
        repo, origin = _make_repo_with_origin(tmp_path)
        local_main = _git(["rev-parse", "main"], repo)
        origin_main = _git(["rev-parse", "main"], origin)
        assert local_main != origin_main, "fixture should leave local main behind"

        ticket_dir = tmp_path / "workspace" / "ERSC-1-task"
        ticket_dir.mkdir(parents=True)

        worktree = create_worktree(
            ticket_dir=ticket_dir,
            repo_path=repo,
            repo_name="repo",
            base_branch="main",
            feature_branch="feature/ERSC-1",
        )

        head = _git(["rev-parse", "HEAD"], worktree)
        assert head == origin_main
        assert head != local_main

    def test_falls_back_when_no_origin_remote(self, tmp_path: Path, capsys):
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(["init", "-b", "main"], repo)
        (repo / "README.md").write_text("only\n")
        _git(["add", "README.md"], repo)
        _git(["commit", "-m", "only"], repo)
        local_main = _git(["rev-parse", "main"], repo)

        ticket_dir = tmp_path / "workspace" / "ERSC-2-task"
        ticket_dir.mkdir(parents=True)

        worktree = create_worktree(
            ticket_dir=ticket_dir,
            repo_path=repo,
            repo_name="repo",
            base_branch="main",
            feature_branch="feature/ERSC-2",
        )

        assert _git(["rev-parse", "HEAD"], worktree) == local_main
        assert "could not fetch origin/main" in capsys.readouterr().err

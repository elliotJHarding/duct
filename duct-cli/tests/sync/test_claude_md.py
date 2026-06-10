"""Tests for duct.sync.claude_md."""

from __future__ import annotations

from pathlib import Path

from duct.markdown import MANAGED_BLOCK_END, MANAGED_BLOCK_START
from duct.sync.claude_md import ClaudeMdSync


def _make_ticket(root: Path, name: str) -> Path:
    ticket = root / name
    (ticket / "orchestrator").mkdir(parents=True)
    return ticket


def _make_repo(parent: Path, name: str) -> Path:
    repo = parent / name
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


def _read_claude_md(ticket: Path) -> str:
    return (ticket / "CLAUDE.md").read_text()


class TestFreshWrite:
    def test_creates_managed_block_with_imports(self, tmp_path: Path):
        ticket = _make_ticket(tmp_path, "ERSC-1-task")

        result = ClaudeMdSync().sync(tmp_path)

        content = _read_claude_md(ticket)
        assert MANAGED_BLOCK_START in content
        assert MANAGED_BLOCK_END in content
        assert "@orchestrator/TICKET.md" in content
        assert "# duct ticket workspace" in content
        assert "Sync-managed context" in content
        assert "@../toolkit/WORKFLOW.md" in content
        assert "preserved across syncs" in content
        assert result.tickets_synced == 1
        assert result.errors == []

    def test_omits_wiki_wiring_by_default(self, tmp_path: Path):
        """The wiki is opt-in — no wiki import, section, or mention without it."""
        ticket = _make_ticket(tmp_path, "ERSC-9-task")

        ClaudeMdSync().sync(tmp_path)

        content = _read_claude_md(ticket)
        assert "@../toolkit/wiki/INDEX.md" not in content
        assert "## Wiki" not in content
        assert "toolkit/wiki" not in content


class TestWikiEnabled:
    def test_includes_wiki_import_section_and_branch_hint(self, tmp_path: Path):
        ticket = _make_ticket(tmp_path, "ERSC-10-task")

        ClaudeMdSync(wiki_enabled=True).sync(tmp_path)

        content = _read_claude_md(ticket)
        assert "@../toolkit/wiki/INDEX.md" in content
        assert "## Wiki" in content
        assert "wiki-reader" in content
        assert "consult `../toolkit/wiki/` for client-specific" in content

    def test_omits_optional_sections_when_empty(self, tmp_path: Path):
        _make_ticket(tmp_path, "ERSC-2-task")

        ClaudeMdSync().sync(tmp_path)

        content = _read_claude_md(tmp_path / "ERSC-2-task")
        assert "Working notes" not in content
        assert "Repos in this ticket workspace" not in content

    def test_code_and_repos_section_always_rendered(self, tmp_path: Path):
        """Tells the agent how to discover and add repos, even when none are cloned."""
        _make_ticket(tmp_path, "ERSC-3-task")

        ClaudeMdSync().sync(tmp_path)

        content = _read_claude_md(tmp_path / "ERSC-3-task")
        assert "## Code & repos" in content
        assert "duct list-repos" in content
        assert "duct list-branches" in content
        assert "duct add-repo" in content
        assert "--clone-from" in content

    def test_code_and_repos_section_warns_against_default_branch(self, tmp_path: Path):
        """The base-branch guidance directs the agent to fixVersion first, not the default."""
        _make_ticket(tmp_path, "ERSC-4-task")

        ClaudeMdSync().sync(tmp_path)

        content = _read_claude_md(tmp_path / "ERSC-4-task")
        assert "fixVersion" in content
        # The section explicitly warns off the default branch as a first choice.
        assert "Do not assume the default" in content
        # Sibling-repo alignment via WORKSPACE.md remains part of the guidance.
        assert "WORKSPACE.md" in content


class TestWorkingNotes:
    def test_lists_user_artifacts_alphabetically(self, tmp_path: Path):
        ticket = _make_ticket(tmp_path, "ERSC-10-task")
        (ticket / "orchestrator" / "SPEC.md").write_text("# spec")
        (ticket / "orchestrator" / "AC.md").write_text("# ac")
        (ticket / "orchestrator" / "progress-2026-05-07.md").write_text("# progress")

        ClaudeMdSync().sync(tmp_path)

        content = _read_claude_md(ticket)
        ac_idx = content.find("`AC.md`")
        spec_idx = content.find("`SPEC.md`")
        progress_idx = content.find("`progress-2026-05-07.md`")
        # ASCII sort: uppercase before lowercase, so AC < SPEC < progress
        assert 0 < ac_idx < spec_idx < progress_idx

    def test_excludes_known_sync_artifacts(self, tmp_path: Path):
        ticket = _make_ticket(tmp_path, "ERSC-11-task")
        (ticket / "orchestrator" / "TICKET.md").write_text("# ticket")
        (ticket / "orchestrator" / "WORKSPACE.md").write_text("# ws")
        (ticket / "orchestrator" / "AC.md").write_text("# ac")

        ClaudeMdSync().sync(tmp_path)

        content = _read_claude_md(ticket)
        notes_section = content.split("Working notes / artifacts")[1]
        assert "`AC.md`" in notes_section
        # Known artifacts must not appear under the working-notes heading.
        notes_lines = notes_section.split("Repos in this")[0]
        assert "- `TICKET.md`" not in notes_lines
        assert "- `WORKSPACE.md`" not in notes_lines


class TestRepos:
    def test_lists_cloned_repos(self, tmp_path: Path):
        ticket = _make_ticket(tmp_path, "ERSC-20-task")
        _make_repo(ticket, "policy")
        _make_repo(ticket, "claims")

        ClaudeMdSync().sync(tmp_path)

        content = _read_claude_md(ticket)
        assert "Repos in this ticket workspace" in content
        claims_idx = content.find("`claims/`")
        policy_idx = content.find("`policy/`")
        assert 0 < claims_idx < policy_idx

    def test_omits_repos_section_when_none(self, tmp_path: Path):
        _make_ticket(tmp_path, "ERSC-21-task")

        ClaudeMdSync().sync(tmp_path)

        content = _read_claude_md(tmp_path / "ERSC-21-task")
        assert "Repos in this ticket workspace" not in content


class TestEditPreservation:
    def test_user_notes_below_end_marker_preserved(self, tmp_path: Path):
        ticket = _make_ticket(tmp_path, "ERSC-30-task")

        # First sync seeds the file.
        ClaudeMdSync().sync(tmp_path)
        claude_md = ticket / "CLAUDE.md"
        claude_md.write_text(claude_md.read_text() + "\n## My notes\n\nWIP.\n")

        # Add a working note so the managed block changes.
        (ticket / "orchestrator" / "AC.md").write_text("# ac")
        ClaudeMdSync().sync(tmp_path)

        content = _read_claude_md(ticket)
        assert "## My notes\n\nWIP.\n" in content
        assert "`AC.md`" in content


class TestReseedOnMissingMarker:
    def test_existing_file_without_markers_is_backed_up(self, tmp_path: Path):
        ticket = _make_ticket(tmp_path, "ERSC-40-task")
        (ticket / "CLAUDE.md").write_text("# manual override\n\nno markers\n")

        result = ClaudeMdSync().sync(tmp_path)

        # Original moved to a timestamped .bak; fresh seed in CLAUDE.md.
        backups = list(ticket.glob("CLAUDE.md.*.bak"))
        assert len(backups) == 1
        assert "manual override" in backups[0].read_text()
        assert MANAGED_BLOCK_START in _read_claude_md(ticket)

        # Warning surfaced but the ticket still counts as synced.
        assert result.tickets_synced == 1
        assert any("backed up" in e for e in result.errors)


class TestDefensive:
    def test_skips_non_ticket_dirs(self, tmp_path: Path):
        # workspace root with no ticket dirs at all
        result = ClaudeMdSync().sync(tmp_path)
        assert result.tickets_synced == 0
        assert result.errors == []

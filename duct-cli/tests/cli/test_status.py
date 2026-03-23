"""Tests for the duct status command."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from click.testing import CliRunner

from duct.cli.main import cli
from duct.cli.status_cmd import (
    _check_dirty_repos,
    _count_active_sessions,
    _count_prs,
    _parse_ticket_md,
    _sync_age,
)


def _init_workspace(root: Path) -> None:
    (root / "config.yaml").write_text("workspace:\n  root: .\n")


def _make_ticket(root: Path, key: str, status: str = "In Progress") -> Path:
    """Create a ticket directory with a minimal TICKET.md."""
    ticket_dir = root / f"{key}-feature"
    orch = ticket_dir / "orchestrator"
    orch.mkdir(parents=True)
    content = f"---\nsource: sync\nsyncedAt: 2025-01-01T00:00:00Z\n---\n# {key}: Test Feature\n\n| Field | Value |\n|-------|-------|\n| Status | {status} |\n| Category | Task |\n"
    (orch / "TICKET.md").write_text(content)
    return ticket_dir


# ---------------------------------------------------------------------------
# _parse_ticket_md
# ---------------------------------------------------------------------------


def test_parse_ticket_md_basic():
    content = "---\nsource: sync\n---\n# PROJ-123: My Feature\n\n| Field | Value |\n|-------|-------|\n| Status | In Progress |\n| Category | Story |\n"
    info = _parse_ticket_md(content)

    assert info["key"] == "PROJ-123"
    assert info["summary"] == "My Feature"
    assert info["status"] == "In Progress"
    assert info["category"] == "Story"


def test_parse_ticket_md_empty():
    info = _parse_ticket_md("")
    assert info == {}


# ---------------------------------------------------------------------------
# _count_prs
# ---------------------------------------------------------------------------


def test_count_prs_no_file(tmp_path: Path):
    count, ci = _count_prs(tmp_path)
    assert count == 0
    assert ci == ""


def test_count_prs_with_data(tmp_path: Path):
    orch = tmp_path / "orchestrator"
    orch.mkdir()
    content = (
        "# Pull Requests\n\n"
        "## #10 Fix the thing\n**CI**: passing\n\n"
        "## #11 Another PR\n**CI**: failing\n"
    )
    (orch / "PULL_REQUESTS.md").write_text(content)

    count, ci = _count_prs(tmp_path)

    assert count == 2
    assert ci == "failing"


def test_count_prs_all_passing(tmp_path: Path):
    orch = tmp_path / "orchestrator"
    orch.mkdir()
    content = "## #10 PR one\n**CI**: passing\n\n## #11 PR two\n**CI**: passing\n"
    (orch / "PULL_REQUESTS.md").write_text(content)

    count, ci = _count_prs(tmp_path)

    assert count == 2
    assert ci == "passing"


# ---------------------------------------------------------------------------
# _count_active_sessions
# ---------------------------------------------------------------------------


def test_count_active_sessions(tmp_path: Path):
    orch = tmp_path / "orchestrator"
    orch.mkdir()
    content = "# Claude Sessions\n\n## Active\n\n### PID 1234\nDoing stuff\n\n### PID 5678\nDoing more\n\n## Terminated\n\n### PID 9999\nDone\n"
    (orch / "CLAUDE_SESSIONS.md").write_text(content)

    assert _count_active_sessions(tmp_path) == 2


def test_count_active_sessions_no_file(tmp_path: Path):
    assert _count_active_sessions(tmp_path) == 0


# ---------------------------------------------------------------------------
# _check_dirty_repos
# ---------------------------------------------------------------------------


def test_check_dirty_repos_from_workspace_md(tmp_path: Path):
    orch = tmp_path / "orchestrator"
    orch.mkdir()
    content = "# Workspace\n\n## repo-a\n**Status**: dirty\n\n## repo-b\n**Status**: clean\n\n## repo-c\n**Status**: dirty\n"
    (orch / "WORKSPACE.md").write_text(content)

    assert _check_dirty_repos(tmp_path) == 2


# ---------------------------------------------------------------------------
# _sync_age
# ---------------------------------------------------------------------------


def test_sync_age_recent(tmp_path: Path):
    orch = tmp_path / "orchestrator"
    orch.mkdir()
    now = datetime.now(timezone.utc)
    synced_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    content = f"---\nsource: sync\nsyncedAt: {synced_at}\n---\n# PROJ-1: Test\n"
    (orch / "TICKET.md").write_text(content)

    result = _sync_age(tmp_path)

    # Should be a small number of seconds
    assert result.endswith("s") or result.endswith("m")


def test_sync_age_missing(tmp_path: Path):
    assert _sync_age(tmp_path) == "none"


# ---------------------------------------------------------------------------
# CLI: status
# ---------------------------------------------------------------------------


def test_status_default_filter(tmp_path: Path):
    _init_workspace(tmp_path)
    _make_ticket(tmp_path, "PROJ-1", status="In Progress")
    _make_ticket(tmp_path, "PROJ-2", status="Done")
    _make_ticket(tmp_path, "PROJ-3", status="Testing Failed")

    runner = CliRunner()
    result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "status"])

    assert result.exit_code == 0, result.output
    assert "PROJ-1" in result.output
    assert "PROJ-2" not in result.output
    assert "PROJ-3" in result.output


def test_status_all_excludes_done(tmp_path: Path):
    _init_workspace(tmp_path)
    _make_ticket(tmp_path, "PROJ-1", status="To Do")
    _make_ticket(tmp_path, "PROJ-2", status="Done")

    runner = CliRunner()
    result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "status", "--all"])

    assert result.exit_code == 0, result.output
    assert "PROJ-1" in result.output
    assert "PROJ-2" not in result.output


def test_status_closed_shows_everything(tmp_path: Path):
    _init_workspace(tmp_path)
    _make_ticket(tmp_path, "PROJ-1", status="In Progress")
    _make_ticket(tmp_path, "PROJ-2", status="Done")

    runner = CliRunner()
    result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "status", "--closed"])

    assert result.exit_code == 0, result.output
    assert "PROJ-1" in result.output
    assert "PROJ-2" in result.output


def test_status_uses_configured_focus_statuses(tmp_path: Path):
    _init_workspace(tmp_path)
    import yaml

    config_data = {
        "workspace": {"root": str(tmp_path)},
        "status": {"focusStatuses": ["To Do"]},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config_data))

    _make_ticket(tmp_path, "PROJ-1", status="To Do")
    _make_ticket(tmp_path, "PROJ-2", status="In Progress")

    runner = CliRunner()
    result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "status"])

    assert result.exit_code == 0, result.output
    assert "PROJ-1" in result.output
    assert "PROJ-2" not in result.output


def test_status_no_tickets(tmp_path: Path):
    _init_workspace(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "status"])

    assert result.exit_code == 0, result.output
    assert "No tracked tickets" in result.output

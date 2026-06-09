"""Tests for the duct ticket command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from duct.cli.main import cli

TICKET_MD = """\
---
source: sync
syncedAt: 2025-01-15T10:00:00Z
---
# ERSC-100: Fix authentication middleware

| Field | Value |
| --- | --- |
| Status | In Progress |
| Category | Bug |
| Priority | High |
| Assignee | alice |
"""


def _make_ticket(root: Path, key: str, slug: str, ticket_md: str | None = None) -> Path:
    """Create a ticket directory with an orchestrator/ subdir and optional TICKET.md."""
    d = root / f"{key}-{slug}"
    orch = d / "orchestrator"
    orch.mkdir(parents=True)
    if ticket_md:
        (orch / "TICKET.md").write_text(ticket_md)
    return d


def _init_workspace(root: Path, jira_domain: str | None = "mycompany.atlassian.net") -> None:
    """Write a minimal config.yaml so find_workspace_root succeeds."""
    lines = "workspace:\n  root: .\n"
    if jira_domain:
        lines += f"jira:\n  domain: {jira_domain}\n"
    (root / "config.yaml").write_text(lines)


class TestTicketList:
    def test_populated_workspace(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD)

        runner = CliRunner()
        result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "ticket", "list"])

        assert result.exit_code == 0, result.output
        assert "ERSC-100" in result.output
        assert "Fix authentication middleware" in result.output
        assert "In Progress" in result.output

    def test_empty_workspace(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)

        runner = CliRunner()
        result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "ticket", "list"])

        assert result.exit_code == 0, result.output
        assert "No tracked tickets" in result.output

    def test_json_output(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "--workspace-root", str(tmp_path), "ticket", "list"]
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["key"] == "ERSC-100"
        assert data[0]["status"] == "In Progress"

    def test_ticket_without_ticket_md(self, tmp_path: Path) -> None:
        """A ticket dir without TICKET.md should still appear with the key."""
        _init_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-200", "no-md")

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "--workspace-root", str(tmp_path), "ticket", "list"]
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert len(data) == 1
        assert data[0]["key"] == "ERSC-200"
        assert data[0]["summary"] == ""


class TestTicketShow:
    def test_show_existing(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        td = _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD)
        # Add an extra artifact.
        (td / "orchestrator" / "SYNC-SNAPSHOT.md").write_text("snapshot")

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "ticket", "status", "ERSC-100"]
        )

        assert result.exit_code == 0, result.output
        assert "Fix authentication middleware" in result.output
        assert "Artifacts" in result.output
        assert "TICKET.md" in result.output
        assert "SYNC-SNAPSHOT.md" in result.output

    def test_show_missing(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "ticket", "status", "ERSC-999"]
        )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_show_json(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        td = _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD)
        # Add a repo worktree dir.
        (td / "my-repo").mkdir()

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "--workspace-root", str(tmp_path), "ticket", "status", "ERSC-100"]
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["key"] == "ERSC-100"
        assert "TICKET.md" in data["artifacts"]
        assert "my-repo" in data["repos"]

    def test_show_with_repo_worktrees(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        td = _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD)
        (td / "frontend").mkdir()
        (td / "backend").mkdir()

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "ticket", "status", "ERSC-100"]
        )

        assert result.exit_code == 0, result.output
        assert "Repo worktrees" in result.output
        assert "frontend" in result.output
        assert "backend" in result.output


class TestTicketOpen:
    def test_open_existing(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD)

        runner = CliRunner()
        with patch("click.launch") as mock_launch:
            result = runner.invoke(
                cli, ["--workspace-root", str(tmp_path), "ticket", "open", "ERSC-100"]
            )

        assert result.exit_code == 0, result.output
        mock_launch.assert_called_once_with(
            "https://mycompany.atlassian.net/browse/ERSC-100"
        )

    def test_open_missing_ticket(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "ticket", "open", "ERSC-999"]
        )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_open_missing_domain(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path, jira_domain=None)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "ticket", "open", "ERSC-100"]
        )

        assert result.exit_code != 0
        assert "jira.domain" in result.output.lower()

    def test_open_json(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD)

        runner = CliRunner()
        with patch("click.launch") as mock_launch:
            result = runner.invoke(
                cli,
                ["--json", "--workspace-root", str(tmp_path), "ticket", "open", "ERSC-100"],
            )

        assert result.exit_code == 0, result.output
        mock_launch.assert_not_called()
        data = json.loads(result.output.strip())
        assert data["key"] == "ERSC-100"
        assert data["url"] == "https://mycompany.atlassian.net/browse/ERSC-100"

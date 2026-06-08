"""Tests for the duct pr command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from duct.cli.main import cli

TICKET_MD = """\
---
source: sync
syncedAt: 2026-03-27T10:00:00Z
---
# ERSC-100: Fix authentication middleware

| Field | Value |
| --- | --- |
| Status | In Progress |
| Category | Bug |
| Priority | High |
| Assignee | alice |
"""

PR_MD_SINGLE = """\
---
source: sync
syncedAt: 2026-03-27T10:00:00Z
---

# Pull Requests

## #42 - ERSC-100: Fix authentication middleware

- **Repo**: org/backend
- **State**: open
- **Author**: @alice
- **Review**: APPROVED
- **CI**: passing
- **Created**: 2026-03-20T10:00:00Z
- **Updated**: 2026-03-27T10:00:00Z
- [View on GitHub](https://github.com/org/backend/pull/42)

### Reviewers

- @bob: APPROVED
"""

PR_MD_MULTI = """\
---
source: sync
syncedAt: 2026-03-27T10:00:00Z
---

# Pull Requests

## #42 - ERSC-100: Fix authentication middleware

- **Repo**: org/backend
- **State**: open
- **Author**: @alice
- **Review**: APPROVED
- **CI**: passing
- **Created**: 2026-03-20T10:00:00Z
- **Updated**: 2026-03-27T10:00:00Z
- [View on GitHub](https://github.com/org/backend/pull/42)

## #15 - ERSC-100: Refactor components

- **Repo**: org/frontend
- **State**: merged
- **Author**: @alice
- **Review**: APPROVED
- **CI**: passing
- **Created**: 2026-03-10T10:00:00Z
- **Updated**: 2026-03-15T10:00:00Z
- [View on GitHub](https://github.com/org/frontend/pull/15)
"""

REVIEW_PRS_MD = """\
---
source: sync
syncedAt: 2026-03-27T10:00:00Z
---

# Pull Requests

## #31 - Team-requested change

- **Repo**: org/api
- **State**: open
- **Author**: @bob
- **Review**: pending
- **CI**: passing
- **Mergeable**: CONFLICTING
- **Created**: 2026-03-20T10:00:00Z
- **Updated**: 2026-03-27T10:00:00Z
- **Requested Teams**: @org/claims-dev
- **Needs Review**: true
- [View on GitHub](https://github.com/org/api/pull/31)
"""

CLOSED_TICKET_MD = """\
---
source: sync
syncedAt: 2026-03-27T10:00:00Z
---
# ERSC-200: Old ticket

| Field | Value |
| --- | --- |
| Status | Done |
| Category | Feature |
| Priority | Low |
| Assignee | alice |
"""


def _setup_workspace(root: Path, jira_domain: str | None = "co.atlassian.net") -> None:
    lines = "workspace:\n  root: .\nstatus:\n  focusStatuses:\n    - in progress\n  terminalStatuses:\n    - done\n    - closed\n"
    if jira_domain:
        lines += f"jira:\n  domain: {jira_domain}\n"
    (root / "config.yaml").write_text(lines)


def _make_ticket(root: Path, key: str, slug: str, ticket_md: str, pr_md: str | None = None) -> Path:
    d = root / f"{key}-{slug}"
    orch = d / "orchestrator"
    orch.mkdir(parents=True)
    (orch / "TICKET.md").write_text(ticket_md)
    if pr_md:
        (orch / "PULL_REQUESTS.md").write_text(pr_md)
    return d


class TestPrList:
    def test_single_ticket_by_key(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD, PR_MD_SINGLE)

        runner = CliRunner()
        result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "pr", "list", "ERSC-100"])

        assert result.exit_code == 0, result.output
        assert "#42" in result.output or "42" in result.output
        assert "approved" in result.output.lower()

    def test_single_ticket_no_ticket_column(self, tmp_path: Path) -> None:
        """When listing a single ticket, the Ticket column should be omitted."""
        _setup_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD, PR_MD_SINGLE)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "--workspace-root", str(tmp_path), "pr", "list", "ERSC-100"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["number"] == 42

    def test_all_active_tickets(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD, PR_MD_SINGLE)

        runner = CliRunner()
        result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "pr", "list"])

        assert result.exit_code == 0, result.output
        assert "42" in result.output

    def test_filters_by_focus_status(self, tmp_path: Path) -> None:
        """Tickets with terminal status should not appear by default."""
        _setup_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD, PR_MD_SINGLE)
        _make_ticket(tmp_path, "ERSC-200", "old", CLOSED_TICKET_MD, PR_MD_SINGLE)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "--workspace-root", str(tmp_path), "pr", "list"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        tickets = {entry["ticket"] for entry in data}
        assert "ERSC-100" in tickets
        assert "ERSC-200" not in tickets

    def test_closed_flag_includes_terminal(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD, PR_MD_SINGLE)
        _make_ticket(tmp_path, "ERSC-200", "old", CLOSED_TICKET_MD, PR_MD_SINGLE)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "--workspace-root", str(tmp_path), "pr", "list", "--closed"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        tickets = {entry["ticket"] for entry in data}
        assert "ERSC-100" in tickets
        assert "ERSC-200" in tickets

    def test_state_filter(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD, PR_MD_MULTI)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "--workspace-root", str(tmp_path), "pr", "list", "ERSC-100", "--state", "merged"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert len(data) == 1
        assert data[0]["state"] == "merged"

    def test_no_prs(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD)

        runner = CliRunner()
        result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "pr", "list", "ERSC-100"])
        assert result.exit_code == 0, result.output
        assert "No pull requests" in result.output

    def test_ticket_not_found(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)

        runner = CliRunner()
        result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "pr", "list", "ERSC-999"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


class TestPrReview:
    def test_lists_review_prs(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)
        (tmp_path / ".review_prs.md").write_text(REVIEW_PRS_MD)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--json", "--workspace-root", str(tmp_path), "pr", "review"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert len(data) == 1
        entry = data[0]
        assert entry["number"] == 31
        assert entry["author"] == "bob"
        assert entry["why"] == "@org/claims-dev"
        assert entry["mergeable"] == "CONFLICTING"

    def test_empty_when_no_review_file(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "pr", "review"]
        )
        assert result.exit_code == 0, result.output
        assert "No pull requests awaiting your review" in result.output

    def test_conflict_flagged_in_status(self) -> None:
        from duct.cli.pr_cmd import _review_status

        assert "conflicts" in _review_status("open", "CONFLICTING")
        assert "conflicts" not in _review_status("open", "MERGEABLE")


class TestPrDeepReview:
    def test_invokes_review_helpers(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)
        (tmp_path / ".review_prs.md").write_text(REVIEW_PRS_MD)

        runner = CliRunner()
        with patch("duct.review.prepare_local_review", return_value=Path("/repo/api")) as prep, \
             patch("duct.review.open_in_intellij") as opener:
            result = runner.invoke(
                cli, ["--workspace-root", str(tmp_path), "pr", "deep-review", "31"]
            )

        assert result.exit_code == 0, result.output
        prep.assert_called_once()
        assert prep.call_args.args[1].number == 31
        opener.assert_called_once_with(Path("/repo/api"))

    def test_surfaces_runtime_error(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)
        (tmp_path / ".review_prs.md").write_text(REVIEW_PRS_MD)

        runner = CliRunner()
        with patch(
            "duct.review.prepare_local_review",
            side_effect=RuntimeError("no repoPaths configured"),
        ):
            result = runner.invoke(
                cli, ["--workspace-root", str(tmp_path), "pr", "deep-review", "31"]
            )
        assert result.exit_code != 0
        assert "no repoPaths configured" in result.output

    def test_not_found(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "pr", "deep-review", "999"]
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()


class TestPrOpen:
    def test_opens_browser(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD, PR_MD_SINGLE)

        runner = CliRunner()
        with patch("click.launch") as mock_launch:
            result = runner.invoke(
                cli, ["--workspace-root", str(tmp_path), "pr", "open", "42"]
            )

        assert result.exit_code == 0, result.output
        mock_launch.assert_called_once_with("https://github.com/org/backend/pull/42")

    def test_opens_review_queue_pr(self, tmp_path: Path) -> None:
        """A PR only in the review queue (no tracked ticket) is still openable."""
        _setup_workspace(tmp_path)
        (tmp_path / ".review_prs.md").write_text(REVIEW_PRS_MD)

        runner = CliRunner()
        with patch("click.launch") as mock_launch:
            result = runner.invoke(
                cli, ["--workspace-root", str(tmp_path), "pr", "open", "31"]
            )
        assert result.exit_code == 0, result.output
        mock_launch.assert_called_once_with("https://github.com/org/api/pull/31")

    def test_json_mode(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD, PR_MD_SINGLE)

        runner = CliRunner()
        with patch("click.launch") as mock_launch:
            result = runner.invoke(
                cli, ["--json", "--workspace-root", str(tmp_path), "pr", "open", "42"]
            )

        assert result.exit_code == 0, result.output
        mock_launch.assert_not_called()
        data = json.loads(result.output.strip())
        assert data["number"] == 42
        assert "github.com" in data["url"]

    def test_not_found(self, tmp_path: Path) -> None:
        _setup_workspace(tmp_path)
        _make_ticket(tmp_path, "ERSC-100", "fix-auth", TICKET_MD, PR_MD_SINGLE)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "pr", "open", "999"]
        )
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

"""get_ticket_index — fast, metadata-only ticket list for the Ctrl+K switcher."""

from __future__ import annotations

from pathlib import Path

from duct.api import get_ticket_index
from duct.sync.jira import _write_identity_cache


def _write_ticket(root: Path, key: str, *, status: str, category: str,
                  summary: str = "", assignee_id: str = "") -> None:
    orch = root / f"{key}-slug" / "orchestrator"
    orch.mkdir(parents=True)
    rows = [
        "| Field | Value |",
        "|-------|-------|",
        f"| Status | {status} |",
        f"| Category | {category} |",
    ]
    if assignee_id:
        rows.append(f"| Assignee ID | {assignee_id} |")
    header = f"# {key}: {summary}\n\n" if summary else ""
    (orch / "TICKET.md").write_text(header + "\n".join(rows) + "\n")


def test_returns_metadata_and_excludes_terminal(tmp_path):
    _write_ticket(tmp_path, "DEV-1", status="In Progress",
                  category="Active Development", summary="Build the thing")
    _write_ticket(tmp_path, "DONE-1", status="Done", category="Done",
                  summary="Finished")

    entries = get_ticket_index(tmp_path, filter_mode="all")

    keys = [e.key for e in entries]
    assert keys == ["DEV-1"]  # terminal Done excluded
    dev = entries[0]
    assert dev.summary == "Build the thing"
    assert dev.status == "In Progress"
    assert dev.category == "Active Development"
    # Enriched collections are left empty — this loader does no git/session work.
    assert dev.repos == [] and dev.prs == [] and dev.sessions == []


def test_assigned_to_me_resolves_against_identity_cache(tmp_path):
    _write_identity_cache(tmp_path, "me-123")
    _write_ticket(tmp_path, "MINE-1", status="In Progress",
                  category="Active Development", assignee_id="me-123")
    _write_ticket(tmp_path, "MATE-1", status="In Progress",
                  category="Active Development", assignee_id="someone-else")

    by_key = {e.key: e for e in get_ticket_index(tmp_path)}

    assert by_key["MINE-1"].assigned_to_me is True
    assert by_key["MATE-1"].assigned_to_me is False
    # Not-assigned-to-me sorts last.
    assert [e.key for e in get_ticket_index(tmp_path)] == ["MINE-1", "MATE-1"]

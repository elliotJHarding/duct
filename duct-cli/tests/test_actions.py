"""Tests for action storage (ticket-scoped + workspace-scoped)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import yaml

from duct import paths
from duct.actions import (
    get_actions,
    get_all_actions,
    get_workspace_actions,
    resolve_action,
)


def _write_workspace_actions(root: Path, actions: list[dict]) -> Path:
    path = paths.workspace_actions_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"actions": actions}, sort_keys=False))
    return path


def _write_ticket_actions(root: Path, key: str, actions: list[dict]) -> Path:
    ticket_dir = root / f"{key}-feature"
    (ticket_dir / "orchestrator").mkdir(parents=True, exist_ok=True)
    path = ticket_dir / "orchestrator" / "actions.yaml"
    path.write_text(yaml.dump({"actions": actions}, sort_keys=False))
    return path


class TestWorkspaceActions:
    def test_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        assert get_workspace_actions(tmp_path) == []

    def test_loads_workspace_actions(self, tmp_path: Path) -> None:
        _write_workspace_actions(tmp_path, [
            {
                "id": "wf-1",
                "type": "improve_workflow",
                "description": "Reference draft-ac",
                "status": "pending",
                "detail": {"prompt": "Update WORKFLOW.md..."},
                "created_at": "2026-04-14T10:00:00Z",
            },
        ])

        actions = get_workspace_actions(tmp_path)

        assert len(actions) == 1
        assert actions[0].type == "improve_workflow"
        assert actions[0].detail["prompt"] == "Update WORKFLOW.md..."

    def test_resolve_workspace_action_flips_status(self, tmp_path: Path) -> None:
        path = _write_workspace_actions(tmp_path, [
            {
                "id": "wf-1", "type": "improve_workflow",
                "description": "x", "status": "pending",
                "detail": {"prompt": "p"},
                "created_at": "2026-04-14T10:00:00Z",
            },
        ])

        resolve_action(tmp_path, "", "wf-1", approved=True)

        raw = yaml.safe_load(path.read_text())
        assert raw["actions"][0]["status"] == "approved"
        assert raw["actions"][0]["resolved_at"]

    def test_resolve_workspace_rejection(self, tmp_path: Path) -> None:
        _write_workspace_actions(tmp_path, [
            {
                "id": "wf-1", "type": "improve_workflow",
                "description": "x", "status": "pending",
                "detail": {}, "created_at": "",
            },
        ])

        resolve_action(tmp_path, "", "wf-1", approved=False)

        actions = get_workspace_actions(tmp_path)
        assert actions[0].status == "rejected"

    def test_resolve_rejection_with_feedback_persists_reason(self, tmp_path: Path) -> None:
        _write_workspace_actions(tmp_path, [
            {
                "id": "wf-1", "type": "improve_workflow",
                "description": "x", "status": "pending",
                "detail": {}, "created_at": "",
            },
        ])

        resolve_action(
            tmp_path, "", "wf-1", approved=False,
            feedback="Already handled in ERSC-1258",
        )

        actions = get_workspace_actions(tmp_path)
        assert actions[0].status == "rejected"
        assert actions[0].feedback == "Already handled in ERSC-1258"

    def test_resolve_without_feedback_leaves_field_absent(self, tmp_path: Path) -> None:
        _write_workspace_actions(tmp_path, [
            {
                "id": "wf-1", "type": "improve_workflow",
                "description": "x", "status": "pending",
                "detail": {}, "created_at": "",
            },
        ])

        resolve_action(tmp_path, "", "wf-1", approved=False)

        actions = get_workspace_actions(tmp_path)
        assert actions[0].feedback is None

    def test_load_preserves_existing_feedback(self, tmp_path: Path) -> None:
        _write_workspace_actions(tmp_path, [
            {
                "id": "wf-1", "type": "improve_workflow",
                "description": "x", "status": "rejected",
                "detail": {}, "created_at": "",
                "resolved_at": "2026-04-20T10:00:00Z",
                "feedback": "Not the right time",
            },
        ])

        actions = get_workspace_actions(tmp_path)

        assert len(actions) == 1
        assert actions[0].feedback == "Not the right time"

    def test_withdrawn_status_round_trips(self, tmp_path: Path) -> None:
        """Orchestrator-authored withdrawn actions are loaded verbatim."""
        _write_workspace_actions(tmp_path, [
            {
                "id": "wf-1", "type": "improve_workflow",
                "description": "x", "status": "withdrawn",
                "detail": {"withdrawal_reason": "Ticket closed"},
                "created_at": "",
                "resolved_at": "2026-04-20T10:00:00Z",
            },
        ])

        actions = get_workspace_actions(tmp_path)

        assert actions[0].status == "withdrawn"
        assert actions[0].detail["withdrawal_reason"] == "Ticket closed"

    def test_malformed_yaml_returns_empty(self, tmp_path: Path) -> None:
        path = paths.workspace_actions_file(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not: yaml: [unbalanced")

        assert get_workspace_actions(tmp_path) == []

    def test_tolerates_bare_list_top_level(self, tmp_path: Path) -> None:
        """Action files written as a bare YAML list (no top-level `actions:` key)
        are accepted and migrated to the canonical dict form on next resolve.

        The orchestrator prompt has historically shown per-entry YAML shapes
        without the wrapping `actions:` key, so LLM-authored files sometimes
        land as bare lists. Accepting them avoids a TUI-wide crash.
        """
        path = paths.workspace_actions_file(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent("""\
            - id: wf-1
              type: improve_workflow
              description: x
              status: pending
              detail: {prompt: p}
              created_at: "2026-04-14T10:00:00Z"
        """))

        actions = get_workspace_actions(tmp_path)
        assert len(actions) == 1
        assert actions[0].id == "wf-1"

        resolve_action(tmp_path, "", "wf-1", approved=True)

        raw = yaml.safe_load(path.read_text())
        assert isinstance(raw, dict)
        assert raw["actions"][0]["status"] == "approved"


class TestGetAllActions:
    def test_merges_workspace_and_ticket_actions(self, tmp_path: Path) -> None:
        _write_workspace_actions(tmp_path, [
            {
                "id": "wf-1", "type": "improve_workflow",
                "description": "improve workflow", "status": "pending",
                "detail": {}, "created_at": "2026-04-14T11:00:00Z",
            },
        ])
        _write_ticket_actions(tmp_path, "PROJ-1", [
            {
                "id": "t-1", "type": "prompt",
                "description": "draft AC", "status": "pending",
                "detail": {"agent": "draft-ac"},
                "created_at": "2026-04-14T10:00:00Z",
            },
        ])

        results = get_all_actions(tmp_path)

        keys = [k for k, _ in results]
        ids = [a.id for _, a in results]
        # Both actions surface; workspace key is empty string
        assert "" in keys
        assert "PROJ-1" in keys
        assert set(ids) == {"wf-1", "t-1"}
        # Pending sort by created_at desc → workspace one (newer) first
        assert ids[0] == "wf-1"

    def test_pending_sorted_before_resolved(self, tmp_path: Path) -> None:
        _write_workspace_actions(tmp_path, [
            {
                "id": "old", "type": "improve_workflow",
                "description": "done", "status": "approved",
                "detail": {}, "created_at": "2026-04-13T10:00:00Z",
                "resolved_at": "2026-04-13T11:00:00Z",
            },
            {
                "id": "new", "type": "improve_workflow",
                "description": "todo", "status": "pending",
                "detail": {}, "created_at": "2026-04-14T10:00:00Z",
            },
        ])

        results = get_all_actions(tmp_path)
        ids = [a.id for _, a in results]

        assert ids == ["new", "old"]


class TestTicketActionsStillWork:
    """Regression: existing ticket-scoped flow keeps working unchanged."""

    def test_get_actions_reads_ticket_file(self, tmp_path: Path) -> None:
        _write_ticket_actions(tmp_path, "PROJ-1", [
            {
                "id": "t-1", "type": "concrete",
                "description": "do thing", "status": "pending",
                "detail": {"action": "launch_session"},
                "created_at": "",
            },
        ])

        actions = get_actions(tmp_path, "PROJ-1")

        assert len(actions) == 1
        assert actions[0].id == "t-1"

    def test_resolve_ticket_action(self, tmp_path: Path) -> None:
        path = _write_ticket_actions(tmp_path, "PROJ-1", [
            {
                "id": "t-1", "type": "concrete",
                "description": "x", "status": "pending",
                "detail": {}, "created_at": "",
            },
        ])

        resolve_action(tmp_path, "PROJ-1", "t-1", approved=True)

        raw = yaml.safe_load(path.read_text())
        assert raw["actions"][0]["status"] == "approved"

"""Action management for duct orchestration.

Actions can be ticket-scoped (`{ticket}/orchestrator/actions.yaml`) or
workspace-scoped (`.actions.yaml` at the workspace root, for cross-cutting
work like workflow self-improvement). Both files use the same schema.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

from duct.models import Action
from duct.workspace import enumerate_ticket_dirs, resolve_ticket_dir

WORKSPACE_ACTIONS_FILENAME = ".actions.yaml"


def _items_from_raw(raw: object) -> list[dict]:
    if isinstance(raw, list):
        return [i for i in raw if isinstance(i, dict)]
    if isinstance(raw, dict):
        items = raw.get("actions", [])
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    return []


def _as_iso(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value) if value else ""


def _load_actions_file(actions_file: Path) -> list[Action]:
    if not actions_file.exists():
        return []
    try:
        raw = yaml.safe_load(actions_file.read_text())
    except yaml.YAMLError:
        return []
    return [
        Action(
            id=item.get("id", ""),
            type=item.get("type", ""),
            description=item.get("description", ""),
            status=item.get("status", "pending"),
            detail=item.get("detail", {}),
            created_at=_as_iso(item.get("created_at")),
            resolved_at=_as_iso(item.get("resolved_at")) or None,
            feedback=item.get("feedback"),
        )
        for item in _items_from_raw(raw)
    ]


def _resolve_in_file(
    actions_file: Path,
    action_id: str,
    approved: bool,
    feedback: str | None = None,
) -> None:
    if not actions_file.exists():
        return
    try:
        raw = yaml.safe_load(actions_file.read_text())
    except yaml.YAMLError:
        return

    items = _items_from_raw(raw)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for item in items:
        if item.get("id") == action_id:
            item["status"] = "approved" if approved else "rejected"
            item["resolved_at"] = now
            if feedback:
                item["feedback"] = feedback
            break

    actions_file.write_text(yaml.dump({"actions": items}, default_flow_style=False, sort_keys=False))


def get_actions(root: Path, key: str, ticket_dir: Path | None = None) -> list[Action]:
    """Pending and recent actions for a ticket."""
    if ticket_dir is None:
        ticket_dir = resolve_ticket_dir(root, key)
    if not ticket_dir:
        return []
    return _load_actions_file(ticket_dir / "orchestrator" / "actions.yaml")


def get_workspace_actions(root: Path) -> list[Action]:
    """Pending and recent workspace-level actions (not tied to a ticket)."""
    return _load_actions_file(root / WORKSPACE_ACTIONS_FILENAME)


def get_all_actions(root: Path) -> list[tuple[str, Action]]:
    """All actions across tickets and the workspace.

    Returns (ticket_key, Action) pairs. Workspace-level actions use an empty
    ticket_key. Pending first, then by created_at desc.
    """
    results: list[tuple[str, Action]] = []
    for action in get_workspace_actions(root):
        results.append(("", action))
    for key, path in enumerate_ticket_dirs(root):
        for action in get_actions(root, key, ticket_dir=path):
            results.append((key, action))

    pending = [r for r in results if r[1].status == "pending"]
    resolved = [r for r in results if r[1].status != "pending"]
    pending.sort(key=lambda ka: ka[1].created_at or "", reverse=True)
    resolved.sort(key=lambda ka: ka[1].created_at or "", reverse=True)
    return pending + resolved


def resolve_action(
    root: Path,
    key: str,
    action_id: str,
    approved: bool,
    feedback: str | None = None,
) -> None:
    """Approve or reject a pending action.

    An empty `key` targets the workspace-level actions file; otherwise the
    ticket's ``orchestrator/actions.yaml`` is used. ``feedback`` is persisted
    on rejections so the orchestrator can see why on its next run.
    """
    if not key:
        _resolve_in_file(root / WORKSPACE_ACTIONS_FILENAME, action_id, approved, feedback)
        return

    ticket_dir = resolve_ticket_dir(root, key)
    if not ticket_dir:
        return
    _resolve_in_file(ticket_dir / "orchestrator" / "actions.yaml", action_id, approved, feedback)

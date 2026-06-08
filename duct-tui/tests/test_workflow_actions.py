"""Tests for workflow-improvement action handling on the Conduct tab."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from duct.actions import WORKSPACE_ACTIONS_FILENAME
from duct.models import Action

from duct_tui.widgets.action_render import _row_label


def _write_workspace_action(root: Path, action: dict) -> None:
    (root / WORKSPACE_ACTIONS_FILENAME).write_text(
        yaml.dump({"actions": [action]}, sort_keys=False),
    )


def test_row_label_uses_workflow_badge_for_empty_key():
    label, style = _row_label("")
    assert label == "[workflow]"
    assert "magenta" in style


def test_row_label_uses_ticket_key_when_present():
    label, style = _row_label("PROJ-1")
    assert label == "PROJ-1"
    assert "cyan" in style


def test_approving_improve_workflow_launches_session(workspace_root):
    """OrchestratorTab._resolve_action should post a RootSessionLaunch
    carrying the workflow-improvement preamble + prompt when approved."""
    from duct_tui.widgets.orchestrator_tab import OrchestratorTab

    _write_workspace_action(workspace_root, {
        "id": "wf-1",
        "type": "improve_workflow",
        "description": "Reference draft-ac in WORKFLOW.md",
        "status": "pending",
        "detail": {"prompt": "Update WORKFLOW.md to reference agents/draft-ac.md."},
        "created_at": "2026-04-14T10:00:00Z",
    })

    tab = OrchestratorTab.__new__(OrchestratorTab)
    tab.post_message = MagicMock()

    data = MagicMock()
    data.root = workspace_root

    app = MagicMock()
    app.data = data
    app.call_from_thread = lambda fn, *args, **kwargs: fn(*args, **kwargs)

    with patch.object(OrchestratorTab, "app", new=app):
        # Skip the @work decorator wrapping by calling the underlying fn
        OrchestratorTab._resolve_action.__wrapped__(tab, "", "wf-1", True)

    data.resolve_action.assert_called_once_with("", "wf-1", True, None)
    tab.post_message.assert_called_once()
    posted = tab.post_message.call_args.args[0]
    assert isinstance(posted, OrchestratorTab.RootSessionLaunch)
    assert "improving the workflow" in posted.prompt
    assert "Update WORKFLOW.md" in posted.prompt


def test_approving_concrete_launch_session_posts_ticket_launch(workspace_root):
    """A concrete ``launch_session`` action approved on the Conduct tab posts a
    TicketSessionLaunch so DuctApp's handler can spawn a WezTerm pane. The
    bare-Popen path on data.do_launch_session inherits the TUI's tty and
    must never be used from inside the TUI."""
    from duct_tui.widgets.orchestrator_tab import OrchestratorTab

    ticket_dir = workspace_root / "PROJ-1-feature"
    (ticket_dir / "orchestrator").mkdir(parents=True)
    (ticket_dir / "orchestrator" / "actions.yaml").write_text(yaml.dump({
        "actions": [{
            "id": "t-1", "type": "concrete",
            "description": "do thing", "status": "pending",
            "detail": {"action": "launch_session"}, "created_at": "",
        }],
    }))

    tab = OrchestratorTab.__new__(OrchestratorTab)
    tab.post_message = MagicMock()

    data = MagicMock()
    data.root = workspace_root

    app = MagicMock()
    app.data = data
    app.call_from_thread = lambda fn, *args, **kwargs: fn(*args, **kwargs)
    app.notify = lambda *a, **kw: None

    with patch.object(OrchestratorTab, "app", new=app):
        OrchestratorTab._resolve_action.__wrapped__(tab, "PROJ-1", "t-1", True)

    data.resolve_action.assert_called_once_with("PROJ-1", "t-1", True, None)
    data.do_launch_session.assert_not_called()
    tab.post_message.assert_called_once()
    posted = tab.post_message.call_args.args[0]
    assert isinstance(posted, OrchestratorTab.TicketSessionLaunch)
    assert posted.ticket_key == "PROJ-1"


def test_approving_workflow_action_with_empty_prompt_warns(workspace_root):
    from duct_tui.widgets.orchestrator_tab import OrchestratorTab

    _write_workspace_action(workspace_root, {
        "id": "wf-1", "type": "improve_workflow",
        "description": "noop", "status": "pending",
        "detail": {"prompt": ""}, "created_at": "",
    })

    tab = OrchestratorTab.__new__(OrchestratorTab)
    tab.post_message = MagicMock()
    data = MagicMock()
    data.root = workspace_root

    app = MagicMock()
    app.data = data
    app.call_from_thread = lambda fn, *args, **kwargs: fn(*args, **kwargs)
    severities: list[str] = []
    app.notify = lambda msg, **kwargs: severities.append(kwargs.get("severity", "info"))

    with patch.object(OrchestratorTab, "app", new=app):
        OrchestratorTab._resolve_action.__wrapped__(tab, "", "wf-1", True)

    tab.post_message.assert_not_called()
    assert "warning" in severities


def test_approving_jira_comment_posts_comment(workspace_root):
    from duct_tui.widgets.orchestrator_tab import OrchestratorTab

    ticket_dir = workspace_root / "PROJ-1-feature"
    (ticket_dir / "orchestrator").mkdir(parents=True)
    (ticket_dir / "orchestrator" / "actions.yaml").write_text(yaml.dump({
        "actions": [{
            "id": "jc-1", "type": "jira_comment",
            "description": "Post progress update", "status": "pending",
            "detail": {"ticket": "PROJ-1", "body": "Work complete."},
            "created_at": "2026-04-17T10:00:00Z",
        }],
    }))

    tab = OrchestratorTab.__new__(OrchestratorTab)
    data = MagicMock()
    data.root = workspace_root

    notifications: list[str] = []
    app = MagicMock()
    app.data = data
    app.call_from_thread = lambda fn, *args, **kwargs: fn(*args, **kwargs)
    app.notify = lambda msg, **kwargs: notifications.append(msg)

    with patch.object(OrchestratorTab, "app", new=app):
        OrchestratorTab._resolve_action.__wrapped__(tab, "PROJ-1", "jc-1", True)

    data.resolve_action.assert_called_once_with("PROJ-1", "jc-1", True, None)
    data.do_post_jira_comment.assert_called_once_with("PROJ-1", "Work complete.")
    assert any("Comment posted" in n for n in notifications)


def test_approving_workspace_prompt_action_launches_root_session(workspace_root):
    """An approved workspace-scoped prompt action with an agent should load
    the agent body and post a RootSessionLaunch carrying that body."""
    from duct_tui.widgets.orchestrator_tab import OrchestratorTab

    _write_workspace_action(workspace_root, {
        "id": "p-1",
        "type": "prompt",
        "description": "Draft today's standup",
        "status": "pending",
        "detail": {"agent": "draft-standup-update"},
        "created_at": "2026-04-22T09:54:00Z",
    })

    tab = OrchestratorTab.__new__(OrchestratorTab)
    tab.post_message = MagicMock()

    data = MagicMock()
    data.root = workspace_root
    data.load_agent_body.return_value = "AGENT BODY"

    app = MagicMock()
    app.data = data
    app.call_from_thread = lambda fn, *args, **kwargs: fn(*args, **kwargs)
    app.notify = lambda *a, **kw: None

    with patch.object(OrchestratorTab, "app", new=app):
        OrchestratorTab._resolve_action.__wrapped__(tab, "", "p-1", True)

    data.load_agent_body.assert_called_once_with("draft-standup-update")
    tab.post_message.assert_called_once()
    posted = tab.post_message.call_args.args[0]
    assert isinstance(posted, OrchestratorTab.RootSessionLaunch)
    assert posted.prompt == "AGENT BODY"


def test_approving_jira_comment_with_empty_body_warns(workspace_root):
    from duct_tui.widgets.orchestrator_tab import OrchestratorTab

    ticket_dir = workspace_root / "PROJ-2-feature"
    (ticket_dir / "orchestrator").mkdir(parents=True)
    (ticket_dir / "orchestrator" / "actions.yaml").write_text(yaml.dump({
        "actions": [{
            "id": "jc-2", "type": "jira_comment",
            "description": "Empty comment", "status": "pending",
            "detail": {"ticket": "PROJ-2", "body": ""},
            "created_at": "",
        }],
    }))

    tab = OrchestratorTab.__new__(OrchestratorTab)
    data = MagicMock()
    data.root = workspace_root

    severities: list[str] = []
    app = MagicMock()
    app.data = data
    app.call_from_thread = lambda fn, *args, **kwargs: fn(*args, **kwargs)
    app.notify = lambda msg, **kwargs: severities.append(kwargs.get("severity", "info"))

    with patch.object(OrchestratorTab, "app", new=app):
        OrchestratorTab._resolve_action.__wrapped__(tab, "PROJ-2", "jc-2", True)

    data.do_post_jira_comment.assert_not_called()
    assert "warning" in severities

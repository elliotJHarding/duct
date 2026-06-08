"""Tests for execute_approved_action.

The helper runs from a worker thread so it routes every UI touch through
``app.call_from_thread``. The fakes here turn ``call_from_thread`` into a
direct call so the assertions describe behaviour, not threading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from duct.models import Action
from duct_tui.widgets.action_execute import execute_approved_action
from duct_tui.widgets.orchestrator_tab import OrchestratorTab


_RootLaunch = OrchestratorTab.RootSessionLaunch
_TicketLaunch = OrchestratorTab.TicketSessionLaunch


@dataclass
class _Notification:
    args: tuple
    kwargs: dict


@dataclass
class _FakeData:
    """DataManager stand-in: records every method call."""
    agents: dict[str, str] = field(default_factory=dict)
    launch_session_calls: list[tuple] = field(default_factory=list)
    jira_comment_calls: list[tuple] = field(default_factory=list)
    jira_comment_should_raise: Exception | None = None

    def load_agent_body(self, name: str) -> str | None:
        return self.agents.get(name)

    def do_launch_session(
        self, key: str | None, repo: str | None, prompt: str | None,
    ) -> None:
        self.launch_session_calls.append((key, repo, prompt))

    def do_post_jira_comment(self, key: str, body: str) -> None:
        if self.jira_comment_should_raise is not None:
            raise self.jira_comment_should_raise
        self.jira_comment_calls.append((key, body))


@dataclass
class _FakeApp:
    """Minimal App fake. ``call_from_thread`` is a direct call."""
    data: _FakeData = field(default_factory=_FakeData)
    notifications: list[_Notification] = field(default_factory=list)
    refresh_called: int = 0

    def notify(self, *args: Any, **kwargs: Any) -> None:
        self.notifications.append(_Notification(args, kwargs))

    def request_session_refresh(self, *args: Any, **kwargs: Any) -> None:
        self.refresh_called += 1

    def call_from_thread(self, fn, *args: Any, **kwargs: Any) -> None:
        fn(*args, **kwargs)


@dataclass
class _FakeHost:
    """Widget stand-in: records posted messages."""
    posted: list[Any] = field(default_factory=list)

    def post_message(self, msg: Any) -> None:
        self.posted.append(msg)


def _action(action_type: str, *, detail: dict | None = None) -> Action:
    return Action(
        id="a-1",
        type=action_type,
        description="test action",
        status="pending",
        detail=detail or {},
        created_at="2026-04-10T10:00:00Z",
    )


# ---------------------------------------------------------------- prompt + ticket


def _ticket_msgs(host: "_FakeHost") -> "list":
    return [m for m in host.posted if isinstance(m, _TicketLaunch)]


def _root_msgs(host: "_FakeHost") -> "list":
    return [m for m in host.posted if isinstance(m, _RootLaunch)]


class TestPromptTicketScoped:
    """Conduct-tab approval of a ticket-scoped prompt should post the
    WezTerm spawn-and-dock message, never call ``do_launch_session``
    (which inherits the TUI's tty and tanks performance — see app.py
    ``on_orchestrator_tab_ticket_session_launch``)."""

    def test_prompt_with_agent_and_ticket_posts_launch(self):
        app = _FakeApp(data=_FakeData(agents={"reviewer": "reviewer body"}))
        host = _FakeHost()
        action = _action("prompt", detail={"agent": "reviewer", "ticket": "PROJ-1"})

        execute_approved_action(app, host, action, ticket_key=None)

        # No bare-Popen path
        assert app.data.launch_session_calls == []
        msgs = _ticket_msgs(host)
        assert len(msgs) == 1
        assert msgs[0].ticket_key == "PROJ-1"
        assert msgs[0].prompt == "reviewer body"
        assert msgs[0].repo is None

    def test_prompt_with_free_form_and_ticket_posts_launch(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action(
            "prompt", detail={"prompt": "do the thing", "ticket": "PROJ-2"},
        )

        execute_approved_action(app, host, action, ticket_key=None)

        assert app.data.launch_session_calls == []
        msgs = _ticket_msgs(host)
        assert len(msgs) == 1
        assert msgs[0].ticket_key == "PROJ-2"
        assert msgs[0].prompt == "do the thing"

    def test_ticket_key_from_caller_used_when_detail_has_none(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action("prompt", detail={"prompt": "body"})

        execute_approved_action(app, host, action, ticket_key="PROJ-3")

        msgs = _ticket_msgs(host)
        assert len(msgs) == 1
        assert msgs[0].ticket_key == "PROJ-3"

    def test_detail_ticket_wins_over_caller_ticket_key(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action("prompt", detail={"prompt": "body", "ticket": "PROJ-A"})

        execute_approved_action(app, host, action, ticket_key="PROJ-B")

        msgs = _ticket_msgs(host)
        assert len(msgs) == 1
        assert msgs[0].ticket_key == "PROJ-A"

    def test_repo_in_detail_passed_to_message(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action(
            "prompt",
            detail={"prompt": "body", "ticket": "PROJ-1", "repo": "service-a"},
        )

        execute_approved_action(app, host, action, ticket_key=None)

        msgs = _ticket_msgs(host)
        assert len(msgs) == 1
        assert msgs[0].repo == "service-a"


# ---------------------------------------------------------------- prompt workspace


class TestPromptWorkspaceScoped:
    def test_prompt_without_ticket_posts_root_session_launch(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action("prompt", detail={"prompt": "workspace body"})

        execute_approved_action(app, host, action, ticket_key=None)

        assert app.data.launch_session_calls == []
        msgs = _root_msgs(host)
        assert len(msgs) == 1
        assert msgs[0].prompt == "workspace body"
        assert _ticket_msgs(host) == []

    def test_agent_resolution_failure_emits_error_notify(self):
        app = _FakeApp()  # data.agents is empty
        host = _FakeHost()
        action = _action("prompt", detail={"agent": "missing", "ticket": "PROJ-1"})

        execute_approved_action(app, host, action, ticket_key=None)

        assert app.data.launch_session_calls == []
        assert host.posted == []
        assert any(
            "missing" in str(n.args) and n.kwargs.get("severity") == "error"
            for n in app.notifications
        )

    def test_empty_prompt_emits_warning(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action("prompt", detail={"prompt": "   "})

        execute_approved_action(app, host, action, ticket_key=None)

        assert app.data.launch_session_calls == []
        assert host.posted == []
        assert any(
            n.kwargs.get("severity") == "warning" for n in app.notifications
        )


# ---------------------------------------------------------------- jira_comment


class TestJiraComment:
    def test_jira_comment_posts_to_target(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action(
            "jira_comment", detail={"ticket": "PROJ-7", "body": "status update"},
        )

        execute_approved_action(app, host, action, ticket_key=None)

        assert app.data.jira_comment_calls == [("PROJ-7", "status update")]
        assert any(
            "PROJ-7" in str(n.args) and not n.kwargs.get("severity")
            for n in app.notifications
        )

    def test_jira_comment_uses_caller_ticket_when_detail_missing(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action("jira_comment", detail={"body": "ping"})

        execute_approved_action(app, host, action, ticket_key="PROJ-9")

        assert app.data.jira_comment_calls == [("PROJ-9", "ping")]

    def test_jira_comment_error_emits_error_notify(self):
        app = _FakeApp(
            data=_FakeData(jira_comment_should_raise=RuntimeError("boom")),
        )
        host = _FakeHost()
        action = _action(
            "jira_comment", detail={"ticket": "PROJ-1", "body": "x"},
        )

        execute_approved_action(app, host, action, ticket_key=None)

        assert any(
            n.kwargs.get("severity") == "error" and "boom" in str(n.args)
            for n in app.notifications
        )

    def test_empty_body_emits_warning(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action("jira_comment", detail={"ticket": "PROJ-1", "body": "  "})

        execute_approved_action(app, host, action, ticket_key=None)

        assert app.data.jira_comment_calls == []
        assert any(
            n.kwargs.get("severity") == "warning" for n in app.notifications
        )


# ---------------------------------------------------------------- improve_workflow


class TestImproveWorkflow:
    def test_improve_workflow_posts_root_launch_with_preamble(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action("improve_workflow", detail={"prompt": "tighten heuristic X"})

        execute_approved_action(app, host, action, ticket_key=None)

        assert len(host.posted) == 1
        msg = host.posted[0]
        assert isinstance(msg, OrchestratorTab.RootSessionLaunch)
        assert msg.prompt is not None
        assert "WORKFLOW.md" in msg.prompt
        assert msg.prompt.endswith("tighten heuristic X")

    def test_empty_prompt_emits_warning(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action("improve_workflow", detail={"prompt": ""})

        execute_approved_action(app, host, action, ticket_key=None)

        assert host.posted == []
        assert any(
            n.kwargs.get("severity") == "warning" for n in app.notifications
        )


# ---------------------------------------------------------------- concrete


class TestConcrete:
    def test_concrete_launch_session_posts_ticket_launch(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action(
            "concrete",
            detail={
                "action": "launch_session",
                "ticket_key": "PROJ-1",
                "repo": "repo-a",
                "prompt": "body",
            },
        )

        execute_approved_action(app, host, action, ticket_key=None)

        assert app.data.launch_session_calls == []
        msgs = _ticket_msgs(host)
        assert len(msgs) == 1
        assert (msgs[0].ticket_key, msgs[0].repo, msgs[0].prompt) == (
            "PROJ-1", "repo-a", "body",
        )

    def test_concrete_falls_back_to_ticket_key_when_detail_missing(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action("concrete", detail={"action": "launch_session"})

        execute_approved_action(app, host, action, ticket_key="PROJ-2")

        msgs = _ticket_msgs(host)
        assert len(msgs) == 1
        assert msgs[0].ticket_key == "PROJ-2"

    def test_concrete_without_any_ticket_warns(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action("concrete", detail={"action": "launch_session"})

        execute_approved_action(app, host, action, ticket_key=None)

        assert host.posted == []
        assert any(
            n.kwargs.get("severity") == "warning" for n in app.notifications
        )

    def test_unknown_concrete_subtype_warns(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action("concrete", detail={"action": "frobnicate"})

        execute_approved_action(app, host, action, ticket_key="PROJ-1")

        assert app.data.launch_session_calls == []
        assert host.posted == []
        assert any(
            n.kwargs.get("severity") == "warning" for n in app.notifications
        )


# ---------------------------------------------------------------- unknown


class TestUnknownType:
    def test_unknown_action_type_warns(self):
        app = _FakeApp()
        host = _FakeHost()
        action = _action("plant_a_tree", detail={})

        execute_approved_action(app, host, action, ticket_key=None)

        assert app.data.launch_session_calls == []
        assert host.posted == []
        assert any(
            n.kwargs.get("severity") == "warning" for n in app.notifications
        )

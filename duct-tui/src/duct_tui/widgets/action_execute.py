"""Single dispatch for approved actions.

Both the ticket-tab and the conduct-tab approval paths route through
``execute_approved_action`` so the case analysis lives in one place —
preventing future drift of the kind that produced the "ticket-scoped
prompts get warned at instead of launched" bug.

The helper is called from worker threads (the resolve workers in each
tab are ``@work(thread=True)``), so every UI touch goes through
``app.call_from_thread``. Blocking work (``do_launch_session``,
``do_post_jira_comment``) is called directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from duct.models import Action

if TYPE_CHECKING:
    from textual.app import App
    from textual.widget import Widget


_WORKFLOW_PREAMBLE = (
    "You are improving the workflow for this duct workspace. "
    "Read WORKFLOW.md and scan agents/ and the root directory "
    "to understand the current conventions before making changes.\n\n"
    "Improvement to apply:\n\n"
)


def execute_approved_action(
    app: "App",
    host: "Widget",
    action: Action,
    ticket_key: str | None,
    background: bool = False,
) -> None:
    """Run the side-effect of approving ``action``.

    ``host`` is the widget initiating the dispatch (ticket-tab or
    conduct-tab); it is used as the poster of ``RootSessionLaunch`` so
    the message bubbles to DuctApp's handler. ``ticket_key`` is the
    implicit scope of the caller — non-None for the ticket-tab, None for
    the conduct-tab's workspace slice. The action's own
    ``detail.ticket`` always takes precedence. ``background`` propagates
    to the launch message so DuctApp can spawn without docking or
    switching the active TUI tab.
    """
    detail = action.detail or {}
    detail_ticket = (detail.get("ticket") or "").strip() or None
    target_ticket = detail_ticket or ticket_key

    if action.type == "concrete":
        _run_concrete(app, host, detail, target_ticket, background)
        return
    if action.type == "prompt":
        _run_prompt(app, host, detail, target_ticket, background)
        return
    if action.type == "jira_comment":
        _run_jira_comment(app, detail, target_ticket)
        return
    if action.type == "improve_workflow":
        _run_improve_workflow(app, host, detail, background)
        return

    app.call_from_thread(
        app.notify,
        f"Unknown action type: {action.type!r}",
        severity="warning",
    )


def _run_concrete(
    app: "App", host: "Widget", detail: dict, target_ticket: str | None,
    background: bool,
) -> None:
    if detail.get("action") != "launch_session":
        app.call_from_thread(
            app.notify,
            f"Unknown concrete action subtype: {detail.get('action')!r}",
            severity="warning",
        )
        return
    ticket_key = detail.get("ticket_key") or target_ticket
    if not ticket_key:
        app.call_from_thread(
            app.notify,
            "concrete launch_session action has no ticket_key",
            severity="warning",
        )
        return
    _post_ticket_session_launch(
        app, host, ticket_key, detail.get("repo"), detail.get("prompt"),
        background,
    )


def _run_prompt(
    app: "App", host: "Widget", detail: dict, target_ticket: str | None,
    background: bool,
) -> None:
    body = _resolve_prompt_body(app, detail)
    if body is None:
        return
    if target_ticket:
        _post_ticket_session_launch(
            app, host, target_ticket, detail.get("repo"), body, background,
        )
    else:
        _post_root_session_launch(app, host, body, background)


def _run_jira_comment(app: "App", detail: dict, target_ticket: str | None) -> None:
    body = (detail.get("body") or "").strip()
    if not body or not target_ticket:
        app.call_from_thread(
            app.notify,
            "Jira comment action has no body or ticket",
            severity="warning",
        )
        return
    try:
        app.data.do_post_jira_comment(target_ticket, body)
        app.call_from_thread(app.notify, f"Comment posted to {target_ticket}")
    except Exception as exc:
        app.call_from_thread(
            app.notify,
            f"Failed to post comment: {exc}",
            severity="error",
        )


def _run_improve_workflow(
    app: "App", host: "Widget", detail: dict, background: bool,
) -> None:
    prompt = (detail.get("prompt") or "").strip()
    if not prompt:
        app.call_from_thread(
            app.notify,
            "Workflow action has no prompt to launch",
            severity="warning",
        )
        return
    _post_root_session_launch(app, host, _WORKFLOW_PREAMBLE + prompt, background)


def _resolve_prompt_body(app: "App", detail: dict) -> str | None:
    """Return the prompt body for a ``type: prompt`` action, or None on failure.

    Notifies the user on failure so silent fallthrough is impossible.
    """
    agent_name = (detail.get("agent") or "").strip() or None
    if agent_name:
        body = app.data.load_agent_body(agent_name)
        if not body:
            app.call_from_thread(
                app.notify,
                f"Agent '{agent_name}' not found in agents/",
                severity="error",
            )
            return None
        return body
    body = (detail.get("prompt") or "").strip() or None
    if not body:
        app.call_from_thread(
            app.notify,
            "Prompt action has no agent or prompt to launch",
            severity="warning",
        )
        return None
    return body


def _post_root_session_launch(
    app: "App", host: "Widget", prompt: str, background: bool,
) -> None:
    """Post the workspace-level launch message via ``host`` so it bubbles to DuctApp."""
    from duct_tui.widgets.orchestrator_tab import OrchestratorTab
    app.call_from_thread(
        host.post_message,
        OrchestratorTab.RootSessionLaunch(prompt=prompt, background=background),
    )


def _post_ticket_session_launch(
    app: "App",
    host: "Widget",
    ticket_key: str,
    repo: str | None,
    prompt: str | None,
    background: bool,
) -> None:
    """Post the ticket-scoped launch message via ``host``.

    DuctApp's ``on_orchestrator_tab_ticket_session_launch`` handler spawns
    a fresh WezTerm pane scoped to the ticket and docks it (foreground) or
    leaves it free (``background=True``). This is the only safe path from
    inside the TUI — a bare ``subprocess.Popen`` would inherit the TUI's
    tty and fight it for stdin/stdout.
    """
    from duct_tui.widgets.orchestrator_tab import OrchestratorTab
    app.call_from_thread(
        host.post_message,
        OrchestratorTab.TicketSessionLaunch(
            ticket_key=ticket_key, repo=repo, prompt=prompt, background=background,
        ),
    )

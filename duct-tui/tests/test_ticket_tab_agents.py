"""Tests for agent-launching behaviour on the ticket tab.

Action-execution semantics are tested against the shared helper in
``tests/widgets/test_action_execute.py``. This file keeps only the
ticket-tab-specific concerns: opening the launch-agent modal and the
``LaunchAgentConfig`` dataclass.
"""

from __future__ import annotations

from textwrap import dedent
from unittest.mock import patch

import pytest

from duct.models import Ticket, TicketDetail
from duct_tui.app import DuctApp
from duct_tui.modals.launch_agent import LaunchAgentModal, LaunchAgentConfig


def _write_agent(root, name: str, body: str) -> None:
    agents_dir = root / "agents"
    agents_dir.mkdir(exist_ok=True)
    (agents_dir / f"{name}.md").write_text(dedent(body).lstrip())


@pytest.fixture
def ticket_detail():
    return TicketDetail(
        ticket=Ticket(
            key="TEST-1", summary="test ticket", status="in progress",
            category="bug", priority="P2", issue_type="Bug",
            assignee="me", url="",
        ),
        artifacts=[],
        repos=[],
        prs=[],
        sessions=[],
        actions=[],
    )


async def _open_ticket_tab(app, pilot, detail):
    with patch.object(app.data, "load_ticket_detail", return_value=detail):
        app.sessions = detail.sessions
        app.screen._open_ticket_tab("TEST-1")
        await pilot.pause(delay=0.3)


@pytest.mark.asyncio
async def test_action_launch_agent_opens_modal(
    workspace_root, mock_terminal_adapter, ticket_detail,
):
    """Calling action_launch_agent on a TicketTab pushes the LaunchAgentModal."""
    from duct_tui.widgets.ticket_tab import TicketTab

    _write_agent(workspace_root, "draft-ac", """
        ---
        name: draft-ac
        description: Draft AC
        ---

        body
    """)

    app = DuctApp(root=workspace_root)
    app._terminal_adapter = mock_terminal_adapter
    app._tui_pane_id = 99

    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await _open_ticket_tab(app, pilot, ticket_detail)

        tab = app.screen.query_one("#ticket-TEST-1 TicketTab", TicketTab)
        tab.action_launch_agent()
        await pilot.pause(delay=0.2)

        assert isinstance(app.screen_stack[-1], LaunchAgentModal)


def test_launch_agent_config_passes_repo():
    cfg = LaunchAgentConfig(ticket_key="TEST-1", agent_name="a", repo="r")
    assert cfg.ticket_key == "TEST-1"
    assert cfg.agent_name == "a"
    assert cfg.repo == "r"

"""Tests for ticket-tab session interactions: preview, launch, focus."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from duct_tui.app import DuctApp, _SessionState


@pytest.fixture
def ticket_detail(mock_sessions):
    from duct.models import Ticket, TicketDetail
    return TicketDetail(
        ticket=Ticket(
            key="TEST-1", summary="test ticket", status="in progress",
            category="bug", priority="P2", issue_type="Bug",
            assignee="me", url="",
        ),
        artifacts=[],
        repos=[],
        prs=[],
        sessions=mock_sessions,
        actions=[],
    )


async def _open_ticket_tab(app, pilot, detail):
    with patch.object(app.data, "load_ticket_detail", return_value=detail):
        app.sessions = detail.sessions
        app.screen._open_ticket_tab("TEST-1")
        await pilot.pause(delay=0.3)


def _get_summary_pane(app):
    from duct_tui.widgets.ticket_summary_pane import TicketSummaryPane
    return app.screen.query_one("#ticket-TEST-1 TicketSummaryPane", TicketSummaryPane)


@pytest.mark.asyncio
async def test_ticket_session_highlight_activates_preview(
    workspace_root, mock_terminal_adapter, mock_sessions, ticket_detail,
):
    """Highlighting a session row in the summary pane shows the preview."""
    from duct_tui.widgets.session_preview import SessionPreview

    app = DuctApp(root=workspace_root)
    app._terminal_adapter = mock_terminal_adapter
    app._tui_pane_id = 99

    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await _open_ticket_tab(app, pilot, ticket_detail)

        pane = _get_summary_pane(app)
        # Find the first session row in the option list
        session_idx = next(
            i for i in range(pane.option_count)
            if (pane.get_option_at_index(i).id or "").startswith("session:")
            and pane.get_option_at_index(i).id != "session:__launch__"
        )
        pane.focus()
        pane.highlighted = session_idx
        await pilot.pause(delay=0.3)

        assert app._session_state == _SessionState.PREVIEWING
        preview = app.screen.query_one(SessionPreview)
        assert preview.display is True


@pytest.mark.asyncio
async def test_ticket_launch_row_pushes_modal(
    workspace_root, mock_terminal_adapter, mock_sessions, ticket_detail,
):
    """Highlighting the launch row and pressing Enter opens LaunchSessionModal."""
    from duct_tui.modals.launch_session import LaunchSessionModal

    app = DuctApp(root=workspace_root)
    app._terminal_adapter = mock_terminal_adapter
    app._tui_pane_id = 99

    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await _open_ticket_tab(app, pilot, ticket_detail)

        pane = _get_summary_pane(app)
        launch_idx = next(
            i for i in range(pane.option_count)
            if pane.get_option_at_index(i).id == "session:__launch__"
        )
        pane.focus()
        pane.highlighted = launch_idx
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause(delay=0.2)

        assert isinstance(app.screen_stack[-1], LaunchSessionModal)


@pytest.mark.asyncio
async def test_ticket_L_shortcut_pushes_launch_modal(
    workspace_root, mock_terminal_adapter, mock_sessions, ticket_detail,
):
    """Pressing L anywhere in the summary pane launches (ticket-scoped)."""
    from duct_tui.modals.launch_session import LaunchSessionModal

    app = DuctApp(root=workspace_root)
    app._terminal_adapter = mock_terminal_adapter
    app._tui_pane_id = 99

    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await _open_ticket_tab(app, pilot, ticket_detail)

        pane = _get_summary_pane(app)
        pane.focus()
        await pilot.pause()
        await pilot.press("L")
        await pilot.pause(delay=0.2)

        assert isinstance(app.screen_stack[-1], LaunchSessionModal)


@pytest.mark.asyncio
async def test_periodic_refresh_does_not_resurrect_preview_on_other_tab(
    workspace_root, mock_terminal_adapter, mock_sessions, ticket_detail,
):
    """A TicketTab data refresh must not re-show its session preview when
    the user has switched to another tab.

    Repro: open a ticket tab, highlight a session row (PREVIEWING), switch to
    another tab (BROWSING + preview hidden + no `.session-split`), then trigger
    the 10-second TicketTab refresh — previously the restored highlight fired
    SectionChanged → _select_session → _fetch_preview, popping the stale
    preview onto the new tab.
    """
    from duct_tui.widgets.session_preview import SessionPreview
    from duct_tui.widgets.ticket_tab import TicketTab

    app = DuctApp(root=workspace_root)
    app._terminal_adapter = mock_terminal_adapter
    app._tui_pane_id = 99

    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await _open_ticket_tab(app, pilot, ticket_detail)

        pane = _get_summary_pane(app)
        session_idx = next(
            i for i in range(pane.option_count)
            if (pane.get_option_at_index(i).id or "").startswith("session:")
            and pane.get_option_at_index(i).id != "session:__launch__"
        )
        pane.focus()
        pane.highlighted = session_idx
        await pilot.pause(delay=0.3)
        assert app._session_state == _SessionState.PREVIEWING

        # Switch away from the ticket tab. The TicketTab widget stays mounted;
        # only the active TabPane changes.
        app.screen._switch_to_pane(
            app.screen.query_one("TabbedContent").get_pane("overview"),
        )
        await pilot.pause(delay=0.2)
        assert app._session_state == _SessionState.BROWSING
        preview = app.screen.query_one(SessionPreview)
        assert preview.display is False
        main = app.screen.query_one("#main-layout")
        assert "session-split" not in main.classes

        # Simulate the 10-second TicketTab refresh while the user is on
        # another tab. _apply_data re-runs through TicketSummaryPane.update_data,
        # which restores the previously-highlighted session row.
        ticket_tab = app.screen.query_one("#ticket-TEST-1 TicketTab", TicketTab)
        ticket_tab._apply_data(ticket_detail)
        await pilot.pause(delay=0.4)

        assert app._session_state == _SessionState.BROWSING, (
            "Periodic refresh resurrected the session preview on the wrong tab"
        )
        assert preview.display is False
        assert "session-split" not in main.classes


@pytest.mark.asyncio
async def test_ticket_session_enter_docks(
    workspace_root, mock_terminal_adapter, mock_sessions, ticket_detail,
):
    """Pressing Enter on a session row triggers the dock flow."""
    app = DuctApp(root=workspace_root)
    app._terminal_adapter = mock_terminal_adapter
    app._tui_pane_id = 99

    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await _open_ticket_tab(app, pilot, ticket_detail)

        pane = _get_summary_pane(app)
        session_idx = next(
            i for i in range(pane.option_count)
            if (pane.get_option_at_index(i).id or "").startswith("session:")
            and pane.get_option_at_index(i).id != "session:__launch__"
        )
        pane.focus()
        pane.highlighted = session_idx
        await pilot.pause(delay=0.2)

        await pilot.press("enter")
        await pilot.pause(delay=0.3)

        assert app._session_state == _SessionState.DOCKED

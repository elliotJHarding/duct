"""Ctrl+K ticket switcher: bucketing, key filtering, and open-on-Enter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import Static

from duct.models import TicketOverview
from duct_tui.app import DuctApp
from duct_tui.modals.ticket_switcher import TicketSwitcherModal
from duct_tui.widgets.ticket_tab import TicketTab


def _overview(key, status, category, *, assigned_to_me=True):
    return TicketOverview(
        key=key,
        summary=f"summary for {key}",
        status=status,
        category=category,
        priority="Major",
        path=Path("/tmp") / key,
        artifacts=[],
        assigned_to_me=assigned_to_me,
    )


# One ticket per column, including an in-progress ticket owned by someone else
# that must land under "Not assigned" rather than "In Progress".
_OVERVIEWS = [
    _overview("DEV-1", "In Progress", "Active Development"),
    _overview("DEP-1", "Ready to Deploy", "Awaiting Action"),
    _overview("TODO-1", "To Do", "Pre-Development"),
    _overview("ODD-1", "Some Unknown Status", "Other"),
    _overview("MATE-1", "In Progress", "Active Development", assigned_to_me=False),
]


def _body(app, col_key):
    return app.screen.query_one(f"#switcher-body-{col_key}", Static).content.plain


async def _open_switcher(app, pilot):
    with patch.object(
        app.data, "load_ticket_index", return_value=_OVERVIEWS,
    ):
        await pilot.press("ctrl+k")
        await pilot.pause(delay=0.2)


@pytest.mark.asyncio
async def test_columns_bucket_by_phase_with_unassigned_precedence(workspace_root):
    app = DuctApp(root=workspace_root)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await _open_switcher(app, pilot)

        assert isinstance(app.screen, TicketSwitcherModal)
        assert "DEV-1" in _body(app, "active")
        assert "DEP-1" in _body(app, "post")
        assert "TODO-1" in _body(app, "pre")
        assert "ODD-1" in _body(app, "other")
        # Owned-by-someone-else outranks its In Progress status.
        assert "MATE-1" in _body(app, "na")
        assert "MATE-1" not in _body(app, "active")


@pytest.mark.asyncio
async def test_typing_filters_keys_and_uppercases(workspace_root):
    app = DuctApp(root=workspace_root)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await _open_switcher(app, pilot)

        await pilot.press("d", "e", "v")
        await pilot.pause()

        assert app.screen.query_one("#switcher-search").value == "DEV"
        assert "DEV-1" in _body(app, "active")
        # Non-matching keys are filtered out of their columns.
        assert "TODO-1" not in _body(app, "pre")


@pytest.mark.asyncio
async def test_tab_cycles_highlight_and_enter_opens_it(workspace_root):
    app = DuctApp(root=workspace_root)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await _open_switcher(app, pilot)

        # First match (DEV-1) is highlighted by default.
        assert app.screen._selected_key() == "DEV-1"
        assert "▸" in _body(app, "active")

        # Tab advances through the match set in overview order.
        await pilot.press("tab")
        await pilot.pause()
        assert app.screen._selected_key() == "DEP-1"

        with patch.object(app.data, "load_ticket_detail", return_value=None):
            await pilot.press("enter")
            await pilot.pause(delay=0.3)

        tab = app.screen.query_one("#ticket-detail TicketTab", TicketTab)
        assert tab._ticket_key == "DEP-1"


@pytest.mark.asyncio
async def test_inline_ghost_tracks_tab_selection(workspace_root):
    app = DuctApp(root=workspace_root)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await _open_switcher(app, pilot)

        await pilot.press("d")  # matches DEV-1 and DEP-1
        await pilot.pause()
        inp = app.screen.query_one("#switcher-search")
        assert inp.value == "D"
        assert inp._suggestion == "DEV-1"  # ghost = first match

        await pilot.press("tab")
        await pilot.pause()
        assert inp._suggestion == "DEP-1"  # ghost follows the highlight


@pytest.mark.asyncio
async def test_enter_opens_first_match_in_ticket_tab(workspace_root):
    app = DuctApp(root=workspace_root)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        await _open_switcher(app, pilot)

        with patch.object(app.data, "load_ticket_detail", return_value=None):
            await pilot.press("d", "e", "p")  # prefix matches only DEP-1
            await pilot.press("enter")
            await pilot.pause(delay=0.3)

        # Switcher dismissed; the ticket renders in the persistent Ticket tab.
        assert not isinstance(app.screen, TicketSwitcherModal)
        tab = app.screen.query_one("#ticket-detail TicketTab", TicketTab)
        assert tab._ticket_key == "DEP-1"

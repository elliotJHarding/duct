"""Tests for session browsing/preview/dock UX state machine.

Verifies the three-state model:
  BROWSING → (j/k) → PREVIEWING → (l/Enter) → DOCKED → (focus back) → PREVIEWING
  PREVIEWING → (Escape) → BROWSING
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from duct_tui.app import DuctApp, _SessionState
from duct_tui.widgets.session_preview import SessionPreview


def _make_app(workspace_root, mock_terminal_adapter, mock_sessions):
    """Create a DuctApp wired up with mocked dependencies."""
    app = DuctApp(root=workspace_root)
    app._terminal_adapter = mock_terminal_adapter
    app._tui_pane_id = 99

    # Patch data methods that would hit real filesystem/subprocesses
    original_init = app.data if hasattr(app, "data") else None

    def _inject_sessions(app_ref, sessions):
        """Inject sessions after the app mounts and data is initialised."""
        app_ref.sessions = sessions
        # Update session panels so the option list is populated
        try:
            screen = app_ref.screen
            from duct_tui.widgets.session_panel import SessionPanel
            for panel in screen.query(SessionPanel):
                panel.update_sessions(sessions)
        except Exception:
            pass

    app._test_inject_sessions = lambda: _inject_sessions(app, sessions=mock_sessions)
    return app


def _get_preview(app) -> SessionPreview | None:
    try:
        return app.screen.query_one(SessionPreview)
    except Exception:
        return None


@pytest.mark.asyncio
async def test_initial_state_is_browsing(workspace_root, mock_terminal_adapter, mock_sessions):
    """Sessions tab starts in BROWSING — preview hidden."""
    app = _make_app(workspace_root, mock_terminal_adapter, mock_sessions)

    async with app.run_test(size=(120, 40)) as pilot:
        app._test_inject_sessions()
        await pilot.pause()

        assert app._session_state == _SessionState.BROWSING
        preview = _get_preview(app)
        assert preview is not None
        assert preview.display is False


@pytest.mark.asyncio
async def test_j_shows_preview(workspace_root, mock_terminal_adapter, mock_sessions):
    """Pressing j transitions BROWSING → PREVIEWING."""
    app = _make_app(workspace_root, mock_terminal_adapter, mock_sessions)

    async with app.run_test(size=(120, 40)) as pilot:
        app._test_inject_sessions()
        await pilot.pause()

        # Navigate to sessions tab
        await pilot.press("tab")
        await pilot.pause()

        # Focus the session panel and press j
        from duct_tui.widgets.session_panel import SessionPanel
        for panel in app.screen.query(SessionPanel):
            panel.focus()
            break
        await pilot.press("j")
        # Wait for the preview worker to complete
        await pilot.pause(delay=0.3)

        assert app._session_state == _SessionState.PREVIEWING
        preview = _get_preview(app)
        assert preview is not None
        assert preview.display is True


@pytest.mark.asyncio
async def test_escape_hides_preview(workspace_root, mock_terminal_adapter, mock_sessions):
    """Escape transitions PREVIEWING → BROWSING (hides preview)."""
    app = _make_app(workspace_root, mock_terminal_adapter, mock_sessions)

    async with app.run_test(size=(120, 40)) as pilot:
        app._test_inject_sessions()
        await pilot.pause()

        # Get into PREVIEWING state — switch tabs BEFORE focusing the SessionPanel
        # so action_next_tab's post-switch focus doesn't steal focus from it.
        await pilot.press("tab")
        await pilot.pause()
        from duct_tui.widgets.session_panel import SessionPanel
        for panel in app.screen.query(SessionPanel):
            panel.focus()
            break
        await pilot.press("j")
        await pilot.pause(delay=0.3)
        assert app._session_state == _SessionState.PREVIEWING

        # Press Escape
        await pilot.press("escape")
        await pilot.pause()

        assert app._session_state == _SessionState.BROWSING
        preview = _get_preview(app)
        assert preview is not None
        assert preview.display is False


@pytest.mark.asyncio
async def test_l_docks_and_hides_preview(workspace_root, mock_terminal_adapter, mock_sessions):
    """l transitions PREVIEWING → DOCKED (hides preview, docks pane)."""
    app = _make_app(workspace_root, mock_terminal_adapter, mock_sessions)

    async with app.run_test(size=(120, 40)) as pilot:
        app._test_inject_sessions()
        await pilot.pause()

        # Get into PREVIEWING state — switch tabs before focusing the SessionPanel
        # so action_next_tab's post-switch focus doesn't steal focus from it.
        await pilot.press("tab")
        await pilot.pause()
        from duct_tui.widgets.session_panel import SessionPanel
        for panel in app.screen.query(SessionPanel):
            panel.focus()
            break
        await pilot.press("j")
        await pilot.pause(delay=0.3)
        assert app._session_state == _SessionState.PREVIEWING

        # Press l to dock
        await pilot.press("l")
        await pilot.pause(delay=0.3)

        assert app._session_state == _SessionState.DOCKED
        preview = _get_preview(app)
        assert preview is not None
        assert preview.display is False
        assert app._docked_pane_id is not None


@pytest.mark.asyncio
async def test_app_focus_undocks(workspace_root, mock_terminal_adapter, mock_sessions):
    """Focus returning to TUI transitions DOCKED → PREVIEWING with preview visible."""
    app = _make_app(workspace_root, mock_terminal_adapter, mock_sessions)

    async with app.run_test(size=(120, 40)) as pilot:
        app._test_inject_sessions()
        await pilot.pause()

        # Get into DOCKED state — switch tabs before focusing the SessionPanel
        # so action_next_tab's post-switch focus doesn't steal focus from it.
        await pilot.press("tab")
        await pilot.pause()
        from duct_tui.widgets.session_panel import SessionPanel
        for panel in app.screen.query(SessionPanel):
            panel.focus()
            break
        await pilot.press("j")
        await pilot.pause(delay=0.3)
        await pilot.press("l")
        await pilot.pause(delay=0.3)
        assert app._session_state == _SessionState.DOCKED
        assert app._docked_pane_id is not None

        # Simulate focus returning to TUI
        app.on_app_focus()
        await pilot.pause(delay=0.3)

        assert app._session_state == _SessionState.PREVIEWING
        assert app._docked_pane_id is None
        mock_terminal_adapter.undock_pane.assert_called()
        preview = _get_preview(app)
        assert preview is not None
        assert preview.display is True


@pytest.mark.asyncio
async def test_app_focus_during_dock_ignored(workspace_root, mock_terminal_adapter, mock_sessions):
    """AppFocus firing before dock completes should not trigger undock."""
    app = _make_app(workspace_root, mock_terminal_adapter, mock_sessions)

    async with app.run_test(size=(120, 40)) as pilot:
        app._test_inject_sessions()
        await pilot.pause()

        # Set state to DOCKED but leave _docked_pane_id as None
        # (simulates the window between state change and dock completion)
        app._session_state = _SessionState.DOCKED
        app._docked_pane_id = None

        # AppFocus fires — should be ignored because pane_id is not set yet
        app.on_app_focus()
        await pilot.pause()

        assert app._session_state == _SessionState.DOCKED
        mock_terminal_adapter.undock_pane.assert_not_called()


@pytest.mark.asyncio
async def test_tab_away_resets_to_browsing(workspace_root, mock_terminal_adapter, mock_sessions):
    """Switching tabs hides preview and returns to BROWSING."""
    app = _make_app(workspace_root, mock_terminal_adapter, mock_sessions)

    async with app.run_test(size=(120, 40)) as pilot:
        app._test_inject_sessions()
        await pilot.pause()

        # Get into PREVIEWING state on sessions tab — switch tabs before focusing
        # the SessionPanel so action_next_tab's post-switch focus doesn't steal it.
        await pilot.press("tab")
        await pilot.pause()
        from duct_tui.widgets.session_panel import SessionPanel
        for panel in app.screen.query(SessionPanel):
            panel.focus()
            break
        await pilot.press("j")
        await pilot.pause(delay=0.3)
        assert app._session_state == _SessionState.PREVIEWING

        # Switch to another tab
        await pilot.press("tab")
        await pilot.pause()

        assert app._session_state == _SessionState.BROWSING
        preview = _get_preview(app)
        assert preview is not None
        assert preview.display is False


@pytest.mark.asyncio
async def test_j_while_docked_is_ignored(workspace_root, mock_terminal_adapter, mock_sessions):
    """j/k while DOCKED should not show preview or change state."""
    app = _make_app(workspace_root, mock_terminal_adapter, mock_sessions)

    async with app.run_test(size=(120, 40)) as pilot:
        app._test_inject_sessions()
        await pilot.pause()

        # Get into DOCKED state — switch tabs before focusing the SessionPanel
        # so action_next_tab's post-switch focus doesn't steal focus from it.
        await pilot.press("tab")
        await pilot.pause()
        from duct_tui.widgets.session_panel import SessionPanel
        for panel in app.screen.query(SessionPanel):
            panel.focus()
            break
        await pilot.press("j")
        await pilot.pause(delay=0.3)
        await pilot.press("l")
        await pilot.pause(delay=0.3)
        assert app._session_state == _SessionState.DOCKED

        # Simulate a SessionSelect arriving (from an in-flight highlight)
        from duct_tui.widgets.session_panel import SessionPanel as SP
        app.on_session_panel_session_select(SP.SessionSelect(1002))
        await pilot.pause(delay=0.3)

        # Should still be docked, preview still hidden
        assert app._session_state == _SessionState.DOCKED
        preview = _get_preview(app)
        assert preview.display is False

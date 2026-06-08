"""Test fixtures for duct-tui."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from duct.models import SessionInfo


@pytest.fixture
def workspace_root():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_sessions():
    """Two mock sessions with PIDs for testing preview/dock flows."""
    return [
        SessionInfo(
            session_id="sess-1",
            ticket_key="TEST-1",
            pid=1001,
            status="working",
            mode="",
            topic="First session",
            cwd="/tmp/test1",
            started_at="2026-01-01T00:00:00",
            last_activity="2026-01-01T01:00:00",
        ),
        SessionInfo(
            session_id="sess-2",
            ticket_key="TEST-2",
            pid=1002,
            status="ready",
            mode="",
            topic="Second session",
            cwd="/tmp/test2",
            started_at="2026-01-01T00:00:00",
            last_activity="2026-01-01T01:00:00",
        ),
    ]


@pytest.fixture
def mock_terminal_adapter():
    adapter = MagicMock()
    adapter.name = "wezterm"
    adapter.get_own_pane_id.return_value = 99
    adapter.find_pane_for_pid.side_effect = lambda pid: pid + 5000
    adapter.dock_pane.return_value = True
    adapter.undock_pane.return_value = True
    adapter.activate_pane.return_value = True
    adapter.get_pane_text.return_value = "\033[32mtest output\033[0m\nline 2\n"
    return adapter

"""Tests for SyncStatusBar rendering and age formatting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from duct.models import SourceStatus
from duct_tui.widgets.sync_status import (
    _SOURCE_ORDER,
    SyncStatusBar,
    _format_age,
)


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def test_format_age_buckets():
    assert _format_age(None) == "—"
    assert _format_age("not-an-iso-date") == "—"
    assert _format_age(_iso(0)).endswith("s")
    assert _format_age(_iso(30)) == "30s"
    assert _format_age(_iso(90)) == "1m"
    assert _format_age(_iso(3700)) == "1h"
    assert _format_age(_iso(90000)) == "1d"


def test_source_order_covers_all_sources():
    expected = {"jira", "github", "workspace", "sessions", "ci"}
    assert set(_SOURCE_ORDER) == expected


@pytest.mark.asyncio
async def test_sync_status_bar_renders_per_source():
    from textual.app import App

    statuses = [
        SourceStatus(name="jira", last_synced=_iso(120), stale=False, interval_seconds=600),
        SourceStatus(name="github", last_synced=_iso(30), stale=False, interval_seconds=600),
        SourceStatus(name="workspace", last_synced=None, stale=True, interval_seconds=60),
        SourceStatus(name="sessions", last_synced=_iso(10), stale=False, interval_seconds=30),
        SourceStatus(name="ci", last_synced=_iso(3600), stale=True, interval_seconds=900),
    ]

    class _Harness(App):
        def compose(self):
            yield SyncStatusBar()

    app = _Harness()
    async with app.run_test(size=(120, 10)) as pilot:
        bar = app.query_one(SyncStatusBar)
        bar.update_statuses(statuses)
        await pilot.pause()
        # Render produces a Rich Table grid — inspect the rendered cells.
        grid = bar.render()
        row = list(grid.columns)
        left_cell = row[0]._cells[0]
        right_cell = row[1]._cells[0]
        assert left_cell == "duct"
        # Right cell contains each source age in order
        assert "2m" in right_cell   # jira
        assert "30s" in right_cell  # github
        assert "—" in right_cell    # workspace (never synced)
        assert "10s" in right_cell  # sessions
        assert "1h" in right_cell   # ci


@pytest.mark.asyncio
async def test_sync_status_bar_shows_syncing_state():
    from textual.app import App

    statuses = [
        SourceStatus(name="jira", last_synced=_iso(120), stale=False, interval_seconds=600),
    ]

    class _Harness(App):
        def compose(self):
            yield SyncStatusBar()

    app = _Harness()
    async with app.run_test(size=(120, 10)) as pilot:
        bar = app.query_one(SyncStatusBar)
        bar.update_statuses(statuses, syncing=frozenset({"jira"}))
        await pilot.pause()
        grid = bar.render()
        right_cell = list(grid.columns)[1]._cells[0]
        assert "syncing" in right_cell
        assert "2m" not in right_cell

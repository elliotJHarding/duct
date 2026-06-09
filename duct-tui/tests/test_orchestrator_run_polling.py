"""Conduct tab picks up daemon-written runs/actions without a restart."""

from __future__ import annotations

import pytest
from textual.widgets import TabbedContent

from duct import paths
from duct_tui.app import DuctApp
from duct_tui.widgets.orchestrator_tab import AllActionsPanel, OrchestratorTab


def _write_run(root, stamp: str) -> None:
    runs = paths.runs_dir(root)
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"{stamp}.md").write_text(
        f"---\ntimestamp: {stamp}\nexit_code: 0\n---\n\n"
        "# Orchestrator run\n\n## Conclusion\n> Verified.\n\n## Timeline\n- Read\n",
    )


@pytest.mark.asyncio
async def test_poll_runs_picks_up_new_run_without_remount(workspace_root):
    _write_run(workspace_root, "2026-06-01T09-00-00")

    app = DuctApp(root=workspace_root)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        app.screen.query_one(TabbedContent).active = "orchestrator-tab"
        await pilot.pause()
        tab = app.screen.query_one(OrchestratorTab)

        before = len(tab._runs_by_tab_id)
        assert before == 1

        # Daemon writes a second run out-of-process.
        _write_run(workspace_root, "2026-06-01T10-00-00")
        tab._poll_runs()
        await pilot.pause()

        assert len(tab._runs_by_tab_id) == 2, tab._runs_by_tab_id
        assert "run-20260601T100000" in tab._runs_by_tab_id


@pytest.mark.asyncio
async def test_poll_runs_skips_actions_while_composing_rejection(workspace_root):
    _write_run(workspace_root, "2026-06-01T09-00-00")

    app = DuctApp(root=workspace_root)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        app.screen.query_one(TabbedContent).active = "orchestrator-tab"
        await pilot.pause()
        tab = app.screen.query_one(OrchestratorTab)
        panel = app.screen.query_one("#all-actions", AllActionsPanel)

        # Simulate an open reject composer.
        panel._rejecting = ("TEST-1", "action-123")
        assert panel.is_composing

        refreshed = []
        tab._refresh_actions = lambda: refreshed.append(True)  # type: ignore[assignment]
        tab._poll_runs()
        await pilot.pause()

        assert refreshed == [], "actions must not refresh while composing a rejection"

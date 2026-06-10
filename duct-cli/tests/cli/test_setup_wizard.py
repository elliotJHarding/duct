"""Pilot tests for the Textual setup wizard.

Run the app headlessly via ``run_test()`` and assert on navigation:
fresh start, resume at the first incomplete phase, jump menu when duct
is already configured, and tutorial completion marking.

Tests are sync functions driving an asyncio loop directly, so no async
pytest plugin is needed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from duct.cli.setup_wizard.app import SetupApp
from duct.cli.setup_wizard.path_input import PathInput, complete_dir_path, contract_home
from duct.cli.setup_wizard.phases import JiraPhase, JumpMenuPhase, WelcomePhase, WorkspacePhase
from duct.cli.setup_wizard.tutorial import CommandsTourChapter
from duct.credentials import Credentials, save_credentials
from duct.global_state import load_state


def _bootstrap_workspace(tmp_path: Path) -> Path:
    from duct.cli.init_cmd import bootstrap_workspace
    from duct.global_state import set_workspace_path

    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)
    set_workspace_path(workspace)
    return workspace


@pytest.fixture(autouse=True)
def _no_network_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Phases probe live APIs on mount — keep tests offline and instant."""
    monkeypatch.setattr(
        "duct.cli.setup_core.jira_user", lambda *_a: (True, "Test User"),
    )
    monkeypatch.setattr("duct.cli.setup_core.github_user", lambda _t: (True, "octocat", []))
    monkeypatch.setattr("duct.cli.setup_core.jql_count", lambda *_a: 0)
    monkeypatch.setattr("duct.cli.setup_core.jql_preview", lambda *_a, **_kw: ([], ""))
    monkeypatch.setattr("duct.credentials.resolve_gh_token", lambda: "")
    monkeypatch.setattr(
        "duct.cli.setup_wizard.phases.resolve_gh_token_with_source", lambda: ("", ""),
    )
    monkeypatch.setattr(
        "duct.cli.workspace_cmd.list_repo_candidates", lambda cfg, refresh=False: [],
    )


def test_fresh_start_opens_welcome_and_advances() -> None:
    async def scenario() -> None:
        app = SetupApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.current_index == 0
            assert app.query(WelcomePhase)
            await pilot.click("#continue")
            await pilot.pause()
            assert app.query(WorkspacePhase)

    asyncio.run(scenario())


def test_resumes_at_first_incomplete_phase(tmp_path: Path) -> None:
    """Workspace exists but Jira creds are missing — wizard lands on Jira."""
    _bootstrap_workspace(tmp_path)

    async def scenario() -> None:
        app = SetupApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query(JiraPhase)

    asyncio.run(scenario())


def test_ready_state_opens_jump_menu(tmp_path: Path) -> None:
    _bootstrap_workspace(tmp_path)
    save_credentials(Credentials(jira_email="x@y.com", jira_token="tok"))

    async def scenario() -> None:
        app = SetupApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query(JumpMenuPhase)

    asyncio.run(scenario())


def test_jump_mode_returns_to_menu_after_phase(tmp_path: Path) -> None:
    _bootstrap_workspace(tmp_path)
    save_credentials(Credentials(jira_email="x@y.com", jira_token="tok"))

    async def scenario() -> None:
        app = SetupApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.jump_to("welcome")
            await pilot.pause()
            assert app.query(WelcomePhase)
            await pilot.click("#continue")
            await pilot.pause()
            assert app.query(JumpMenuPhase)

    asyncio.run(scenario())


def test_wiki_phase_sits_between_tools_and_completion() -> None:
    from duct.cli.setup_wizard.phases import (
        SETUP_PHASES,
        CompletionPhase,
        ToolsPhase,
        WikiPhase,
    )

    index = SETUP_PHASES.index(WikiPhase)
    assert SETUP_PHASES[index - 1] is ToolsPhase
    assert SETUP_PHASES[index + 1] is CompletionPhase


def test_wiki_phase_persists_choice(tmp_path: Path) -> None:
    from textual.widgets import Checkbox

    from duct.cli.setup_wizard.phases import WikiPhase
    from duct.config import load_config

    workspace = _bootstrap_workspace(tmp_path)
    save_credentials(Credentials(jira_email="x@y.com", jira_token="tok"))

    async def scenario() -> None:
        app = SetupApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.jump_to("wiki")
            await pilot.pause()
            assert app.query(WikiPhase)
            checkbox = app.query_one("#wiki-enabled", Checkbox)
            assert checkbox.value is False  # opt-in default
            checkbox.value = True
            await pilot.click("#continue")
            await pilot.pause()

    asyncio.run(scenario())
    assert load_config(workspace).wiki.enabled is True
    from duct import paths

    assert paths.wiki_index(workspace).exists()


def test_toolkit_anatomy_lists_real_entries(tmp_path: Path) -> None:
    from duct import paths
    from duct.cli.setup_wizard.tutorial import toolkit_anatomy

    workspace = _bootstrap_workspace(tmp_path)
    listing = toolkit_anatomy(paths.toolkit_dir(workspace))

    assert "config.yaml" in listing
    assert "WORKFLOW.md" in listing
    assert ".git/" in listing  # the shareable-repo hook
    # Wiki is off by default — shown as a pending row, not a real entry.
    assert "wiki disabled" in listing


def test_finishing_last_chapter_marks_tutorial_completed(tmp_path: Path) -> None:
    _bootstrap_workspace(tmp_path)
    save_credentials(Credentials(jira_email="x@y.com", jira_token="tok"))

    async def scenario() -> None:
        app = SetupApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.jump_to("tour-commands", jump=False)
            await pilot.pause()
            assert app.query(CommandsTourChapter)
            await pilot.click("#continue")
            await pilot.pause()
        assert app.return_code == 0

    asyncio.run(scenario())
    assert load_state().tutorial_completed is True


def test_sync_phase_marks_source_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-source status cells must update as the coordinator reports.

    Regression: the status column was addressed by its label, which is not
    a column key, so the first on_start callback crashed the phase.
    """
    _bootstrap_workspace(tmp_path)
    save_credentials(Credentials(jira_email="x@y.com", jira_token="tok"))

    class FakeSource:
        name = "jira"

    class FakeResult:
        source = "jira"
        tickets_synced = 3
        duration_seconds = 0.1
        errors: list[str] = []

    class FakeCoordinator:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run(self, sources, force, on_start, on_result):
            on_start("jira")
            on_result(FakeResult())
            return [FakeResult()]

    monkeypatch.setattr("duct.sync.base.SyncCoordinator", FakeCoordinator)
    monkeypatch.setattr(
        "duct.cli.setup_core.build_sync_sources",
        lambda cfg: ([FakeSource()], [("github", "no token")]),
    )
    monkeypatch.setattr(
        "duct.cli.sync_cmd._refresh_repo_completion_cache", lambda *args: None,
    )

    async def scenario() -> None:
        from textual.widgets import DataTable

        app = SetupApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.jump_to("sync", jump=False)
            await pilot.pause(0.5)
            table = app.query_one("#sync-table", DataTable)
            jira_row = table.get_row("jira")
            assert "3 tickets" in str(jira_row[1])
            github_row = table.get_row("github")
            assert "skipped" in str(github_row[1])

    asyncio.run(scenario())


def test_complete_dir_path(tmp_path: Path) -> None:
    (tmp_path / "alpha-one").mkdir()
    (tmp_path / "alpha-two").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / ".hidden").mkdir()

    # Unique match completes fully and appends the trailing slash.
    assert complete_dir_path(str(tmp_path / "b")) == (str(tmp_path / "beta") + "/", [])

    # Ambiguous match extends to the common prefix and lists candidates.
    new_value, names = complete_dir_path(str(tmp_path / "a"))
    assert new_value == str(tmp_path / "alpha-")
    assert names == ["alpha-one", "alpha-two"]

    assert complete_dir_path(str(tmp_path / "zzz")) == (None, [])

    # Hidden directories stay out of candidates until the dot is typed.
    _, names = complete_dir_path(str(tmp_path) + "/")
    assert ".hidden" not in names
    assert complete_dir_path(str(tmp_path) + "/.") == (str(tmp_path / ".hidden") + "/", [])


def test_contract_home() -> None:
    home = Path.home()
    assert contract_home(home) == "~"
    assert contract_home(home / "a" / "b") == "~/a/b"
    assert contract_home(Path("/somewhere/else")) == "/somewhere/else"


def test_workspace_path_tab_completion_keeps_focus(tmp_path: Path) -> None:
    (tmp_path / "projects").mkdir()

    async def scenario() -> None:
        app = SetupApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#continue")
            await pilot.pause()
            path_input = app.query_one("#workspace-path", PathInput)
            path_input.focus()
            path_input.value = str(tmp_path / "pro")
            path_input.cursor_position = len(path_input.value)
            await pilot.press("tab")
            await pilot.pause()
            assert path_input.value == str(tmp_path / "projects") + "/"
            assert app.focused is path_input
            # No candidates at all: tab still stays put (like a shell beep).
            path_input.value = str(tmp_path / "zzz-nothing")
            await pilot.press("tab")
            await pilot.pause()
            assert app.focused is path_input

    asyncio.run(scenario())


def test_skip_binding_only_on_skippable_phases() -> None:
    async def scenario() -> None:
        app = SetupApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # Welcome is not skippable.
            assert app.check_action("skip", ()) is False
            app.jump_to("github", jump=False)
            await pilot.pause()
            assert app.check_action("skip", ()) is True

    asyncio.run(scenario())

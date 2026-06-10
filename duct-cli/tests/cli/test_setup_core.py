"""Tests for the UI-agnostic setup logic shared by both setup front-ends."""

from __future__ import annotations

from pathlib import Path

import pytest

from duct.cli import setup_core
from duct.config import load_config
from duct.credentials import Credentials, save_credentials
from duct.global_state import load_state, mark_tutorial_completed

# ---------------------------------------------------------------------------
# Probes (network mocked via pytest-httpx).
# ---------------------------------------------------------------------------


def test_jira_user_success(httpx_mock) -> None:
    httpx_mock.add_response(
        url="https://acme.atlassian.net/rest/api/3/myself",
        json={"displayName": "Test User"},
    )
    ok, detail = setup_core.jira_user("acme.atlassian.net", "x@y.com", "tok")
    assert ok is True
    assert detail == "Test User"


def test_jira_user_bad_credentials(httpx_mock) -> None:
    httpx_mock.add_response(
        url="https://acme.atlassian.net/rest/api/3/myself", status_code=401,
    )
    ok, detail = setup_core.jira_user("acme.atlassian.net", "x@y.com", "bad")
    assert ok is False
    assert "401" in detail


def test_jql_preview_returns_issue_rows(httpx_mock) -> None:
    httpx_mock.add_response(
        json={
            "issues": [
                {
                    "key": "PROJ-1",
                    "fields": {
                        "summary": "Fix the thing",
                        "status": {"name": "In Progress"},
                        "updated": "2026-06-09T10:00:00.000+0000",
                    },
                },
            ],
        },
    )
    issues, error = setup_core.jql_preview("acme.atlassian.net", "x@y.com", "tok", "jql")
    assert error == ""
    assert issues == [
        setup_core.JqlIssue("PROJ-1", "Fix the thing", "In Progress", "2026-06-09"),
    ]


def test_jql_preview_paginates_until_total_reached(httpx_mock) -> None:
    def issue(n: int) -> dict:
        return {
            "key": f"PROJ-{n}",
            "fields": {
                "summary": f"Issue {n}",
                "status": {"name": "To Do"},
                "updated": "2026-06-09T10:00:00.000+0000",
            },
        }

    httpx_mock.add_response(
        json={"issues": [issue(n) for n in range(50)], "total": 60},
    )
    httpx_mock.add_response(
        json={"issues": [issue(n) for n in range(50, 60)], "total": 60},
    )
    issues, error = setup_core.jql_preview("acme.atlassian.net", "x@y.com", "tok", "jql")
    assert error == ""
    assert issues is not None and len(issues) == 60
    assert issues[0].key == "PROJ-0" and issues[-1].key == "PROJ-59"

    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    assert "startAt=50" in str(requests[1].url)


def test_jql_preview_surfaces_jira_error_message(httpx_mock) -> None:
    httpx_mock.add_response(
        status_code=400,
        json={"errorMessages": ["The value 'Donee' does not exist for the field 'status'."]},
    )
    issues, error = setup_core.jql_preview("acme.atlassian.net", "x@y.com", "tok", "bad jql")
    assert issues is None
    assert "Donee" in error


def test_org_repo_count_sums_public_and_private(httpx_mock) -> None:
    httpx_mock.add_response(
        url="https://api.github.com/orgs/acme",
        json={"public_repos": 3, "total_private_repos": 7},
    )
    assert setup_core.org_repo_count("tok", "acme") == 10


# ---------------------------------------------------------------------------
# Completeness predicates.
# ---------------------------------------------------------------------------


def _bootstrap(tmp_path: Path) -> Path:
    from duct.cli.init_cmd import bootstrap_workspace
    from duct.global_state import set_workspace_path

    workspace = tmp_path / "workspace"
    bootstrap_workspace(workspace)
    set_workspace_path(workspace)
    return workspace


def test_default_workspace_honours_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert setup_core.default_workspace() == Path.home() / "workspace" / "duct"
    monkeypatch.setenv("DUCT_DEFAULT_WORKSPACE", str(tmp_path / "sandbox"))
    assert setup_core.default_workspace() == tmp_path / "sandbox"


def test_state_is_ready_needs_workspace_and_jira_credentials(tmp_path: Path) -> None:
    assert setup_core.state_is_ready() is False
    workspace = _bootstrap(tmp_path)
    assert setup_core.state_is_ready() is False
    save_credentials(Credentials(jira_email="x@y.com", jira_token="tok"))
    assert setup_core.state_is_ready() is True
    assert setup_core.workspace_root() == workspace


def test_first_sync_done_flips_when_sync_state_written(tmp_path: Path) -> None:
    from duct import paths

    workspace = _bootstrap(tmp_path)
    assert setup_core.first_sync_done(workspace) is False
    sync_state = paths.sync_state_file(workspace)
    sync_state.parent.mkdir(parents=True, exist_ok=True)
    sync_state.write_text("jira: 1\n")
    assert setup_core.first_sync_done(workspace) is True


# ---------------------------------------------------------------------------
# Config writers.
# ---------------------------------------------------------------------------


def test_update_config_persists_replaced_fields(tmp_path: Path) -> None:
    workspace = _bootstrap(tmp_path)
    setup_core.update_config(
        workspace, jira_domain="acme.atlassian.net", github_orgs=("acme",),
    )
    cfg = load_config(workspace)
    assert cfg.jira_domain == "acme.atlassian.net"
    assert cfg.github_orgs == ("acme",)
    # Untouched fields keep their defaults.
    assert cfg.jira_jql


def test_set_notifications_round_trips(tmp_path: Path) -> None:
    workspace = _bootstrap(tmp_path)
    setup_core.set_notifications(workspace, True, ("done", "waiting"))
    cfg = load_config(workspace)
    assert cfg.notifications.enabled is True
    assert cfg.notifications.event_kinds == ("done", "waiting")


def test_set_wiki_enable_creates_scaffolding_and_wiring(tmp_path: Path) -> None:
    from duct import paths

    workspace = _bootstrap(tmp_path)
    ticket = workspace / "PROJ-1-demo" / "orchestrator"
    ticket.mkdir(parents=True)

    setup_core.set_wiki(workspace, True)

    assert load_config(workspace).wiki.enabled is True
    assert paths.wiki_index(workspace).exists()
    shim = paths.root_claude_md(workspace).read_text(encoding="utf-8")
    assert "@../toolkit/wiki/INDEX.md" in shim
    for name in ("wiki-reader", "wiki-contributor", "wiki-maintainer"):
        assert (paths.subagents_dir(workspace) / f"{name}.md").exists()
        assert (paths.root_claude_agents_dir(workspace) / f"{name}.md").exists()
    ticket_claude = (workspace / "PROJ-1-demo" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "@../toolkit/wiki/INDEX.md" in ticket_claude


def test_set_wiki_disable_keeps_toolkit_files_but_strips_wiring(tmp_path: Path) -> None:
    from duct import paths

    workspace = _bootstrap(tmp_path)
    ticket = workspace / "PROJ-2-demo" / "orchestrator"
    ticket.mkdir(parents=True)
    setup_core.set_wiki(workspace, True)
    entry = paths.wiki_dir(workspace) / "a-lesson.md"
    entry.write_text("---\nname: a-lesson\ntype: lesson\n---\nbody\n")

    setup_core.set_wiki(workspace, False)

    assert load_config(workspace).wiki.enabled is False
    # Knowledge stays on disk; only the generated wiring goes.
    assert entry.exists()
    assert paths.wiki_index(workspace).exists()
    assert (paths.subagents_dir(workspace) / "wiki-reader.md").exists()
    shim = paths.root_claude_md(workspace).read_text(encoding="utf-8")
    assert "wiki" not in shim.lower()
    for name in ("wiki-reader", "wiki-contributor", "wiki-maintainer"):
        assert not (paths.root_claude_agents_dir(workspace) / f"{name}.md").exists()
    ticket_claude = (workspace / "PROJ-2-demo" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "toolkit/wiki" not in ticket_claude


def test_set_wiki_reenable_restores_only_missing_pieces(tmp_path: Path) -> None:
    from duct import paths

    workspace = _bootstrap(tmp_path)
    setup_core.set_wiki(workspace, True)
    index = paths.wiki_index(workspace)
    index.write_text(index.read_text() + "| a-lesson | lesson | kept |\n")
    (paths.subagents_dir(workspace) / "wiki-reader.md").unlink()
    setup_core.set_wiki(workspace, False)

    setup_core.set_wiki(workspace, True)

    assert (paths.subagents_dir(workspace) / "wiki-reader.md").exists()
    assert "a-lesson" in index.read_text()  # populated index never clobbered


# ---------------------------------------------------------------------------
# Shell completion.
# ---------------------------------------------------------------------------


def test_shell_completion_status_and_enable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    status = setup_core.shell_completion_status()
    assert status is not None
    assert status.shell_name == "zsh"
    assert status.enabled is False

    setup_core.enable_shell_completion(status)
    assert status.enabled is True
    assert "_DUCT_COMPLETE" in (tmp_path / ".zshrc").read_text()


def test_shell_completion_unknown_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/unknown-shell")
    assert setup_core.shell_completion_status() is None


# ---------------------------------------------------------------------------
# Tutorial marker.
# ---------------------------------------------------------------------------


def test_tutorial_marker_persists_alongside_workspace_path(tmp_path: Path) -> None:
    workspace = _bootstrap(tmp_path)
    assert load_state().tutorial_completed is False
    mark_tutorial_completed()
    state = load_state()
    assert state.tutorial_completed is True
    # Marking the tutorial must not clobber the workspace pointer.
    assert state.workspace_path == workspace.resolve()

"""Tests for duct.config module."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import yaml

from duct.config import (
    AutoOrchestrateConfig,
    OrchestratorConfig,
    SessionConfig,
    SessionStatusConfig,
    StatusConfig,
    SyncIntervals,
    WorkspaceConfig,
    find_workspace_root,
    gh_token,
    jira_email,
    jira_token,
    load_config,
    save_config,
)
from duct.exceptions import AuthError, ConfigError

# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_with_valid_yaml(tmp_workspace: Path) -> None:
    config_data = {
        "workspace": {"root": str(tmp_workspace)},
        "jira": {
            "domain": "acme.atlassian.net",
            "jql": "project = ACME",
        },
        "repoPaths": ["/tmp/repos"],
        "syncIntervals": {
            "jira": 600,
            "github": 600,
            "sessions": 60,
            "workspace": 120,
            "ci": 300,
        },
    }
    (tmp_workspace / "config.yaml").write_text(yaml.dump(config_data))

    cfg = load_config(tmp_workspace)

    assert cfg.root == tmp_workspace
    assert cfg.jira_domain == "acme.atlassian.net"
    assert cfg.jira_jql == "project = ACME"
    assert cfg.repo_paths == [Path("/tmp/repos")]
    assert cfg.sync_intervals.jira == 600
    assert cfg.sync_intervals.sessions == 60


def test_load_config_missing_file_returns_defaults(tmp_workspace: Path) -> None:
    cfg = load_config(tmp_workspace)

    assert cfg.root == tmp_workspace
    assert cfg.jira_domain == ""
    assert "assignee = currentUser()" in cfg.jira_jql
    assert cfg.sync_intervals == SyncIntervals()


def test_load_config_ignores_legacy_trust_section(tmp_workspace: Path) -> None:
    """A config.yaml with a trust: section from an older version loads without error."""
    config_data = {
        "workspace": {"root": str(tmp_workspace)},
        "jira": {"domain": "acme.atlassian.net"},
        "trust": {
            "writeArtifact": "auto",
            "gitCommit": "deny",
        },
    }
    (tmp_workspace / "config.yaml").write_text(yaml.dump(config_data))

    cfg = load_config(tmp_workspace)

    assert cfg.jira_domain == "acme.atlassian.net"
    assert not hasattr(cfg, "trust")


# ---------------------------------------------------------------------------
# save_config round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_round_trip(tmp_workspace: Path) -> None:
    original = WorkspaceConfig(
        root=tmp_workspace,
        jira_jql="project = TEST",
        jira_domain="test.atlassian.net",
        repo_paths=[Path("/a"), Path("/b")],
        sync_intervals=SyncIntervals(jira=100, workspace=200),
    )

    save_config(original, tmp_workspace)
    loaded = load_config(tmp_workspace)

    assert loaded.root == original.root
    assert loaded.jira_domain == original.jira_domain
    assert loaded.jira_jql == original.jira_jql
    assert loaded.repo_paths == original.repo_paths
    assert loaded.sync_intervals.jira == 100
    assert loaded.sync_intervals.workspace == 200


def test_save_config_omits_trust(tmp_workspace: Path) -> None:
    cfg = WorkspaceConfig(root=tmp_workspace)
    save_config(cfg, tmp_workspace)

    raw = yaml.safe_load((tmp_workspace / "config.yaml").read_text())
    assert "trust" not in raw


def test_notifications_defaults_when_absent(tmp_workspace: Path) -> None:
    (tmp_workspace / "config.yaml").write_text("workspace:\n  root: " + str(tmp_workspace) + "\n")
    cfg = load_config(tmp_workspace)

    assert cfg.notifications.enabled is False
    assert cfg.notifications.session_poll_seconds == 4
    assert cfg.notifications.overview_poll_seconds == 45
    assert cfg.notifications.event_kinds == ("done", "waiting", "pending-action", "orchestrator")
    assert cfg.notifications.sender_bundle_id == ""


def test_notifications_round_trip(tmp_workspace: Path) -> None:
    from duct.config import NotificationsConfig

    original = WorkspaceConfig(
        root=tmp_workspace,
        notifications=NotificationsConfig(
            enabled=True,
            session_poll_seconds=2,
            overview_poll_seconds=30,
            event_kinds=("done", "waiting"),
            sender_bundle_id="com.github.wez.wezterm",
        ),
    )
    save_config(original, tmp_workspace)
    loaded = load_config(tmp_workspace)

    assert loaded.notifications.enabled is True
    assert loaded.notifications.session_poll_seconds == 2
    assert loaded.notifications.overview_poll_seconds == 30
    assert loaded.notifications.event_kinds == ("done", "waiting")
    assert loaded.notifications.sender_bundle_id == "com.github.wez.wezterm"


# ---------------------------------------------------------------------------
# StatusConfig
# ---------------------------------------------------------------------------


def test_load_config_with_status_section(tmp_workspace: Path) -> None:
    config_data = {
        "workspace": {"root": str(tmp_workspace)},
        "status": {
            "focusStatuses": ["To Do", "In Review"],
            "terminalStatuses": ["Resolved"],
        },
    }
    (tmp_workspace / "config.yaml").write_text(yaml.dump(config_data))

    cfg = load_config(tmp_workspace)

    assert cfg.status.focus_statuses == ("to do", "in review")
    assert cfg.status.terminal_statuses == ("resolved",)


def test_load_config_without_status_uses_defaults(tmp_workspace: Path) -> None:
    cfg = load_config(tmp_workspace)
    assert cfg.status == StatusConfig()


def test_save_and_load_status_round_trip(tmp_workspace: Path) -> None:
    original = WorkspaceConfig(
        root=tmp_workspace,
        status=StatusConfig(
            focus_statuses=("blocked", "waiting"),
            terminal_statuses=("archived",),
        ),
    )
    save_config(original, tmp_workspace)
    loaded = load_config(tmp_workspace)

    assert loaded.status.focus_statuses == original.status.focus_statuses
    assert loaded.status.terminal_statuses == original.status.terminal_statuses


# ---------------------------------------------------------------------------
# SessionStatusConfig
# ---------------------------------------------------------------------------


def test_load_config_with_session_status_section(tmp_workspace: Path) -> None:
    config_data = {
        "workspace": {"root": str(tmp_workspace)},
        "sessionStatus": {
            "doneWindowSeconds": 60,
            "staleAfterSeconds": 7200,
        },
    }
    (tmp_workspace / "config.yaml").write_text(yaml.dump(config_data))

    cfg = load_config(tmp_workspace)

    assert cfg.session_status.done_window_seconds == 60
    assert cfg.session_status.stale_after_seconds == 7200


def test_load_config_without_session_status_uses_defaults(tmp_workspace: Path) -> None:
    cfg = load_config(tmp_workspace)
    assert cfg.session_status == SessionStatusConfig()
    assert cfg.session_status.done_window_seconds == 900
    assert cfg.session_status.stale_after_seconds == 14400


def test_save_and_load_session_status_round_trip(tmp_workspace: Path) -> None:
    original = WorkspaceConfig(
        root=tmp_workspace,
        session_status=SessionStatusConfig(
            done_window_seconds=120,
            stale_after_seconds=3600,
        ),
    )
    save_config(original, tmp_workspace)
    loaded = load_config(tmp_workspace)

    assert loaded.session_status.done_window_seconds == 120
    assert loaded.session_status.stale_after_seconds == 3600


def test_load_config_with_non_integer_session_status_raises(tmp_workspace: Path) -> None:
    config_data = {
        "workspace": {"root": str(tmp_workspace)},
        "sessionStatus": {"doneWindowSeconds": "soon"},
    }
    (tmp_workspace / "config.yaml").write_text(yaml.dump(config_data))

    with pytest.raises(ConfigError, match="doneWindowSeconds"):
        load_config(tmp_workspace)


# ---------------------------------------------------------------------------
# SessionConfig
# ---------------------------------------------------------------------------


def test_load_config_with_session_extra_args(tmp_workspace: Path) -> None:
    config_data = {
        "workspace": {"root": str(tmp_workspace)},
        "session": {
            "extraArgs": ["--model", "sonnet", "--verbose"],
        },
    }
    (tmp_workspace / "config.yaml").write_text(yaml.dump(config_data))

    cfg = load_config(tmp_workspace)

    assert cfg.session.extra_args == ("--model", "sonnet", "--verbose")


def test_load_config_without_session_uses_defaults(tmp_workspace: Path) -> None:
    cfg = load_config(tmp_workspace)
    assert cfg.session == SessionConfig()
    assert cfg.session.extra_args == ()


def test_save_and_load_session_round_trip(tmp_workspace: Path) -> None:
    original = WorkspaceConfig(
        root=tmp_workspace,
        session=SessionConfig(extra_args=("--fast", "--model", "haiku")),
    )
    save_config(original, tmp_workspace)
    loaded = load_config(tmp_workspace)

    assert loaded.session.extra_args == original.session.extra_args


# ---------------------------------------------------------------------------
# OrchestratorConfig
# ---------------------------------------------------------------------------


def test_load_config_with_orchestrator_fork_model(tmp_workspace: Path) -> None:
    config_data = {
        "workspace": {"root": str(tmp_workspace)},
        "orchestrator": {"forkModel": "haiku"},
    }
    (tmp_workspace / "config.yaml").write_text(yaml.dump(config_data))

    cfg = load_config(tmp_workspace)

    assert cfg.orchestrator.fork_model == "haiku"


def test_load_config_without_orchestrator_defaults_to_sonnet(tmp_workspace: Path) -> None:
    cfg = load_config(tmp_workspace)
    assert cfg.orchestrator == OrchestratorConfig()
    assert cfg.orchestrator.fork_model == "sonnet"


def test_save_and_load_orchestrator_round_trip(tmp_workspace: Path) -> None:
    original = WorkspaceConfig(
        root=tmp_workspace,
        orchestrator=OrchestratorConfig(fork_model="haiku"),
    )
    save_config(original, tmp_workspace)
    loaded = load_config(tmp_workspace)

    assert loaded.orchestrator.fork_model == "haiku"


# ---------------------------------------------------------------------------
# AutoOrchestrateConfig
# ---------------------------------------------------------------------------


def test_load_config_without_auto_orchestrate_uses_defaults(tmp_workspace: Path) -> None:
    cfg = load_config(tmp_workspace)
    assert cfg.auto_orchestrate == AutoOrchestrateConfig()
    assert cfg.auto_orchestrate.enabled is False
    assert cfg.auto_orchestrate.weekdays == (0, 1, 2, 3, 4)


def test_load_config_with_auto_orchestrate_weekday_strings(tmp_workspace: Path) -> None:
    config_data = {
        "workspace": {"root": str(tmp_workspace)},
        "autoOrchestrate": {
            "enabled": True,
            "weekdays": ["mon", "wed", "Fri"],
            "startHour": 9,
            "endHour": 17,
            "syncFirst": True,
        },
    }
    (tmp_workspace / "config.yaml").write_text(yaml.dump(config_data))

    cfg = load_config(tmp_workspace)

    assert cfg.auto_orchestrate.enabled is True
    assert cfg.auto_orchestrate.weekdays == (0, 2, 4)
    assert cfg.auto_orchestrate.start_hour == 9
    assert cfg.auto_orchestrate.end_hour == 17
    assert cfg.auto_orchestrate.sync_first is True


def test_load_config_with_auto_orchestrate_int_weekdays(tmp_workspace: Path) -> None:
    config_data = {
        "workspace": {"root": str(tmp_workspace)},
        "autoOrchestrate": {"weekdays": [5, 6]},
    }
    (tmp_workspace / "config.yaml").write_text(yaml.dump(config_data))

    cfg = load_config(tmp_workspace)
    assert cfg.auto_orchestrate.weekdays == (5, 6)


def test_load_config_auto_orchestrate_invalid_hour_raises(tmp_workspace: Path) -> None:
    config_data = {
        "workspace": {"root": str(tmp_workspace)},
        "autoOrchestrate": {"startHour": 25},
    }
    (tmp_workspace / "config.yaml").write_text(yaml.dump(config_data))

    with pytest.raises(ConfigError, match="startHour"):
        load_config(tmp_workspace)


def test_load_config_auto_orchestrate_invalid_weekday_raises(tmp_workspace: Path) -> None:
    config_data = {
        "workspace": {"root": str(tmp_workspace)},
        "autoOrchestrate": {"weekdays": ["funday"]},
    }
    (tmp_workspace / "config.yaml").write_text(yaml.dump(config_data))

    with pytest.raises(ConfigError, match="weekday"):
        load_config(tmp_workspace)


def test_load_config_auto_orchestrate_start_after_end_raises(tmp_workspace: Path) -> None:
    config_data = {
        "workspace": {"root": str(tmp_workspace)},
        "autoOrchestrate": {"startHour": 18, "endHour": 9},
    }
    (tmp_workspace / "config.yaml").write_text(yaml.dump(config_data))

    with pytest.raises(ConfigError, match="startHour"):
        load_config(tmp_workspace)


def test_auto_orchestrate_next_fire_disabled_returns_none() -> None:
    cfg = AutoOrchestrateConfig(enabled=False)
    assert cfg.next_fire_time(datetime(2026, 4, 29, 14, 30)) is None


def test_auto_orchestrate_next_fire_inside_window_advances_one_hour() -> None:
    cfg = AutoOrchestrateConfig(
        enabled=True, start_hour=9, end_hour=17, weekdays=(0, 1, 2, 3, 4),
    )
    # Wednesday 2026-04-29 13:30 -> next fire is 14:00 same day.
    nxt = cfg.next_fire_time(datetime(2026, 4, 29, 13, 30))
    assert nxt == datetime(2026, 4, 29, 14, 0)


def test_auto_orchestrate_next_fire_at_end_of_window_skips_to_next_day() -> None:
    cfg = AutoOrchestrateConfig(
        enabled=True, start_hour=9, end_hour=17, weekdays=(0, 1, 2, 3, 4),
    )
    # Wednesday 17:30 -> next fire is Thursday 09:00.
    nxt = cfg.next_fire_time(datetime(2026, 4, 29, 17, 30))
    assert nxt == datetime(2026, 4, 30, 9, 0)


def test_auto_orchestrate_next_fire_skips_weekend() -> None:
    cfg = AutoOrchestrateConfig(
        enabled=True, start_hour=9, end_hour=17, weekdays=(0, 1, 2, 3, 4),
    )
    # Friday 2026-05-01 17:30 -> Monday 2026-05-04 09:00.
    nxt = cfg.next_fire_time(datetime(2026, 5, 1, 17, 30))
    assert nxt == datetime(2026, 5, 4, 9, 0)


def test_save_and_load_auto_orchestrate_round_trip(tmp_workspace: Path) -> None:
    original = WorkspaceConfig(
        root=tmp_workspace,
        auto_orchestrate=AutoOrchestrateConfig(
            enabled=True,
            weekdays=(0, 1, 2, 3, 4),
            start_hour=9,
            end_hour=17,
            sync_first=True,
        ),
    )
    save_config(original, tmp_workspace)
    loaded = load_config(tmp_workspace)

    assert loaded.auto_orchestrate == original.auto_orchestrate


# ---------------------------------------------------------------------------
# find_workspace_root
# ---------------------------------------------------------------------------


def test_find_workspace_root_walks_up(tmp_path: Path) -> None:
    root = tmp_path / "a" / "b"
    root.mkdir(parents=True)
    # Place config.yaml at tmp_path level
    (tmp_path / "config.yaml").write_text("workspace: {}\n")

    found = find_workspace_root(start=root)
    assert found == tmp_path


def test_find_workspace_root_raises_when_not_found(tmp_path: Path) -> None:
    # tmp_path has no config.yaml and neither do its parents (within the test)
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    with pytest.raises(ConfigError, match="No config.yaml found"):
        find_workspace_root(start=isolated)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def test_jira_email_raises_when_missing() -> None:
    # Keychain is empty (conftest) and there is no env fallback for Jira.
    with pytest.raises(AuthError, match="Jira email is not set"):
        jira_email()


def test_jira_email_returns_value() -> None:
    from duct.credentials import update_credentials

    update_credentials(jira_email="dev@example.com")
    assert jira_email() == "dev@example.com"


def test_jira_email_ignores_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The env fallback was the source of the daemon's silent skip; Jira now
    # resolves from the keychain only.
    monkeypatch.setenv("JIRA_EMAIL", "shell@example.com")
    with pytest.raises(AuthError, match="Jira email is not set"):
        jira_email()


def test_jira_token_raises_when_missing() -> None:
    with pytest.raises(AuthError, match="Jira token is not set"):
        jira_token()


def test_jira_token_returns_value() -> None:
    from duct.credentials import update_credentials

    update_credentials(jira_token="secret-token")
    assert jira_token() == "secret-token"


def test_gh_token_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(AuthError, match="No GitHub token found"):
        gh_token()


def test_gh_token_falls_back_to_gh_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    import subprocess
    from unittest.mock import MagicMock

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "gho_fake_token_from_cli\n"

    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)

    assert gh_token() == "gho_fake_token_from_cli"


def test_gh_token_returns_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "gh-tok")
    assert gh_token() == "gh-tok"


def test_gh_token_falls_back_to_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "github-tok")
    assert gh_token() == "github-tok"

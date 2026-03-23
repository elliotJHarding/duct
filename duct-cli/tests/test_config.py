"""Tests for duct.config module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from duct.config import (
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
# Auth env-var helpers
# ---------------------------------------------------------------------------


def test_jira_email_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    with pytest.raises(AuthError, match="JIRA_EMAIL"):
        jira_email()


def test_jira_email_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_EMAIL", "dev@example.com")
    assert jira_email() == "dev@example.com"


def test_jira_token_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIRA_TOKEN", raising=False)
    with pytest.raises(AuthError, match="JIRA_TOKEN"):
        jira_token()


def test_jira_token_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_TOKEN", "secret-token")
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

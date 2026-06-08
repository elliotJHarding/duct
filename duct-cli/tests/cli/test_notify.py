"""Tests for the duct notify command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from duct.cli.main import cli
from duct.config import NotificationsConfig, WorkspaceConfig, save_config


class _FakeNotifier:
    """Stand-in for MacNotifier that records notify() calls instead of shelling out."""

    def __init__(self, *, enabled: bool, icon: str | None = None) -> None:
        self.enabled = enabled
        self.calls: list[dict] = []

    def notify(self, title: str, body: str, **kwargs) -> bool:
        self.calls.append({"title": title, "body": body, **kwargs})
        return True


def _init_workspace(root: Path, **kwargs) -> None:
    save_config(WorkspaceConfig(root=root, **kwargs), root)


def _feed(root: Path) -> list[dict]:
    path = root / ".duct" / "notifications.jsonl"
    return [json.loads(line) for line in path.read_text().strip().splitlines()]


def _invoke(root: Path, *args) -> tuple:
    """Run `duct notify`, returning (result, captured_notifier)."""
    captured: dict = {}

    def _factory(*, enabled: bool, icon: str | None = None) -> _FakeNotifier:
        captured["notifier"] = _FakeNotifier(enabled=enabled)
        return captured["notifier"]

    runner = CliRunner()
    with patch("duct.cli.notify_cmd.MacNotifier", side_effect=_factory):
        result = runner.invoke(
            cli, ["--workspace-root", str(root), "notify", *args]
        )
    return result, captured.get("notifier")


def test_notify_records_feed_and_fires_through_notifier(tmp_path: Path) -> None:
    _init_workspace(
        tmp_path,
        jira_domain="ex.atlassian.net",
        notifications=NotificationsConfig(enabled=True, sender_bundle_id="com.example"),
    )

    result, notifier = _invoke(
        tmp_path, "--title", "Shipped slice", "--body", "Committed WIP", "--ticket", "ABC-1"
    )

    assert result.exit_code == 0
    entries = _feed(tmp_path)
    assert len(entries) == 1
    assert entries[0]["kind"] == "orchestrator-action"
    assert entries[0]["title"] == "Shipped slice"
    assert entries[0]["body"] == "Committed WIP"
    # Group carries a per-call unique suffix so distinct pushes never evict each
    # other in Notification Center; the ticket prefix stays for readability.
    assert entries[0]["group"].startswith("orchestrator-action:ABC-1:")
    # --ticket derives the click-to-open Jira URL from configured domain.
    assert entries[0]["open_url"] == "https://ex.atlassian.net/browse/ABC-1"

    # Fired through the same mechanism the daemon uses, honouring config.
    assert notifier.enabled is True
    assert notifier.calls[0]["title"] == "Shipped slice"
    assert notifier.calls[0]["sender"] == "com.example"
    assert notifier.calls[0]["open_url"] == "https://ex.atlassian.net/browse/ABC-1"


def test_notify_without_ticket_is_workspace_scoped(tmp_path: Path) -> None:
    _init_workspace(tmp_path, jira_domain="ex.atlassian.net")

    result, _ = _invoke(tmp_path, "--title", "Workspace notice", "--body", "details")

    assert result.exit_code == 0
    entry = _feed(tmp_path)[0]
    assert entry["group"].startswith("orchestrator-action:workspace:")
    assert entry["open_url"] is None


def test_repeated_notifies_get_distinct_groups(tmp_path: Path) -> None:
    # Regression: two distinct pushes must not share a -group, or the second
    # would evict the first from Notification Center (losing it).
    _init_workspace(tmp_path)

    _invoke(tmp_path, "--title", "Standup ready", "--body", "draft")
    _invoke(tmp_path, "--title", "PRs to review", "--body", "list")

    groups = [e["group"] for e in _feed(tmp_path)]
    assert len(groups) == 2
    assert groups[0] != groups[1]


def test_explicit_url_overrides_ticket_derivation(tmp_path: Path) -> None:
    _init_workspace(tmp_path, jira_domain="ex.atlassian.net")

    result, _ = _invoke(
        tmp_path,
        "--title", "PR ready",
        "--body", "review it",
        "--ticket", "ABC-1",
        "--url", "https://github.com/org/repo/pull/7",
    )

    assert result.exit_code == 0
    assert _feed(tmp_path)[0]["open_url"] == "https://github.com/org/repo/pull/7"


def test_notify_records_feed_even_when_notifications_disabled(tmp_path: Path) -> None:
    # Default config has notifications disabled — the OS popup no-ops but the
    # feed must still record so the TUI has a visible entry.
    _init_workspace(tmp_path)

    result, notifier = _invoke(tmp_path, "--title", "Did X", "--body", "details")

    assert result.exit_code == 0
    assert notifier.enabled is False
    assert len(_feed(tmp_path)) == 1

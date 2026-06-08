"""Tests for duct.activity.coordinator: error isolation, state tracking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from duct.activity.coordinator import ActivityCoordinator
from duct.activity.store import load_state
from duct.config import WorkspaceConfig
from duct.models import ActivityEvent


class _FakeProvider:
    def __init__(self, name: str, events=None, raises: Exception | None = None):
        self.name = name
        self._events = events or []
        self._raises = raises
        self.calls = 0

    def fetch(self, since, until, cfg):
        self.calls += 1
        if self._raises:
            raise self._raises
        return iter(self._events)


def _cfg(root: Path) -> WorkspaceConfig:
    return WorkspaceConfig(root=root)


def _event(eid: str, ts: str, source: str = "git") -> ActivityEvent:
    return ActivityEvent(
        event_id=eid,
        timestamp=ts,
        source=source,
        event_type="test",
        actor="me",
        summary=eid,
    )


class TestGather:
    def test_appends_events_from_providers(self, tmp_path: Path):
        coord = ActivityCoordinator(tmp_path, _cfg(tmp_path))
        p = _FakeProvider(
            "git",
            events=[
                _event("git:1", "2026-04-21T09:00:00Z"),
                _event("git:2", "2026-04-21T10:00:00Z"),
            ],
        )
        since = datetime(2026, 4, 21, 0, tzinfo=timezone.utc)
        until = datetime(2026, 4, 22, 0, tzinfo=timezone.utc)
        results = coord.gather([p], since, until)
        assert len(results) == 1
        assert results[0].events_fetched == 2
        assert results[0].events_new == 2
        assert results[0].errors == []

    def test_one_provider_raising_does_not_block_peers(self, tmp_path: Path):
        coord = ActivityCoordinator(tmp_path, _cfg(tmp_path))
        bad = _FakeProvider("jira", raises=RuntimeError("boom"))
        good = _FakeProvider("github", events=[_event("gh:1", "2026-04-21T09:00:00Z")])
        since = datetime(2026, 4, 21, 0, tzinfo=timezone.utc)
        until = datetime(2026, 4, 22, 0, tzinfo=timezone.utc)

        results = coord.gather([bad, good], since, until)
        by_name = {r.name: r for r in results}
        assert by_name["jira"].errors  # non-empty
        assert by_name["jira"].events_new == 0
        assert by_name["github"].events_new == 1
        assert by_name["github"].errors == []

    def test_state_advances_only_on_success(self, tmp_path: Path):
        coord = ActivityCoordinator(tmp_path, _cfg(tmp_path))
        bad = _FakeProvider("jira", raises=RuntimeError("boom"))
        good = _FakeProvider("github", events=[_event("gh:1", "2026-04-21T09:00:00Z")])
        until = datetime(2026, 4, 22, 0, tzinfo=timezone.utc)
        coord.gather([bad, good], until - timedelta(days=1), until)
        state = load_state(tmp_path)
        assert "jira" not in state
        assert state["github"] == "2026-04-22T00:00:00Z"

    def test_dedup_runs_across_invocations(self, tmp_path: Path):
        coord = ActivityCoordinator(tmp_path, _cfg(tmp_path))
        events = [_event("git:1", "2026-04-21T09:00:00Z")]
        p = _FakeProvider("git", events=events)
        since = datetime(2026, 4, 21, 0, tzinfo=timezone.utc)
        until = datetime(2026, 4, 22, 0, tzinfo=timezone.utc)
        first = coord.gather([p], since, until)
        assert first[0].events_new == 1

        second = coord.gather([_FakeProvider("git", events=events)], since, until)
        assert second[0].events_fetched == 1
        assert second[0].events_new == 0


class TestDefaultSince:
    def test_no_state_falls_back_to_24h_ago(self, tmp_path: Path):
        coord = ActivityCoordinator(tmp_path, _cfg(tmp_path))
        now = datetime.now(timezone.utc)
        since = coord.default_since(["jira"])
        delta_hours = (now - since).total_seconds() / 3600
        assert 23.0 < delta_hours < 25.0

    def test_uses_minimum_across_requested_providers(self, tmp_path: Path):
        coord = ActivityCoordinator(tmp_path, _cfg(tmp_path))
        from duct.activity.store import save_state

        save_state(
            tmp_path,
            {
                "jira": "2026-04-21T10:00:00Z",
                "github": "2026-04-21T12:00:00Z",
                "git": "2026-04-21T08:00:00Z",
            },
        )
        # Requesting jira + github should floor at 10:00, minus 1h overlap.
        since = coord.default_since(["jira", "github"], overlap_seconds=3600)
        assert since == datetime(2026, 4, 21, 9, tzinfo=timezone.utc)

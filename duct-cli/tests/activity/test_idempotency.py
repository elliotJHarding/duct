"""End-to-end idempotency check: re-running gather over an overlapping
window must not duplicate events in the JSONL store, even if individual
providers re-emit the same events."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from duct.activity.coordinator import ActivityCoordinator
from duct.activity.store import activity_dir
from duct.config import WorkspaceConfig
from duct.models import ActivityEvent


class _ReplayProvider:
    """Yields the same events on every fetch — simulates a provider that
    consistently reports the same logical events across re-runs."""

    def __init__(self, name: str, events: list[ActivityEvent]):
        self.name = name
        self._events = events
        self.fetch_calls = 0

    def fetch(self, since, until, cfg):
        self.fetch_calls += 1
        return iter(self._events)


def _event(source: str, eid: str, ts: str) -> ActivityEvent:
    return ActivityEvent(
        event_id=eid,
        timestamp=ts,
        source=source,
        event_type="test",
        actor="me",
        summary=f"{source}/{eid}",
    )


def _all_events_in_store(root: Path) -> list[dict]:
    adir = activity_dir(root)
    if not adir.is_dir():
        return []
    out = []
    for path in sorted(adir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


class TestGatherIdempotency:
    def test_second_gather_same_window_yields_zero_new_events(self, tmp_path: Path):
        cfg = WorkspaceConfig(root=tmp_path)
        coord = ActivityCoordinator(tmp_path, cfg)

        providers = [
            _ReplayProvider("jira", [
                _event("jira", "jira:FOO-1:comment:c1", "2026-04-21T09:00:00Z"),
                _event("jira", "jira:FOO-1:history:h1:status", "2026-04-21T10:00:00Z"),
            ]),
            _ReplayProvider("github", [
                _event("github", "github:1001:review", "2026-04-21T11:00:00Z"),
            ]),
            _ReplayProvider("git", [
                _event("git", "git:abc123", "2026-04-21T12:00:00Z"),
            ]),
            _ReplayProvider("claude", [
                _event("claude", "claude:sess-42", "2026-04-21T13:00:00Z"),
            ]),
            _ReplayProvider("outlook", [
                _event("outlook", "outlook:ABCDEF", "2026-04-21T14:00:00Z"),
            ]),
            _ReplayProvider("outlook_pdf", [
                _event("outlook_pdf", "outlook_pdf:deadbeef1234", "2026-04-21T15:00:00Z"),
            ]),
        ]
        since = datetime(2026, 4, 21, tzinfo=timezone.utc)
        until = datetime(2026, 4, 22, tzinfo=timezone.utc)

        first = coord.gather(providers, since, until)
        assert sum(r.events_new for r in first) == 7

        second = coord.gather(providers, since, until)
        assert sum(r.events_new for r in second) == 0
        assert sum(r.events_fetched for r in second) == 7

        stored = _all_events_in_store(tmp_path)
        assert len(stored) == 7
        assert len({e["event_id"] for e in stored}) == 7

    def test_second_gather_wider_window_yields_zero_new_events(self, tmp_path: Path):
        """Re-gathering with a wider ``since`` must not duplicate events
        that were already captured in the narrower run. Regression check
        for the Claude timestamp-clamping bug."""
        cfg = WorkspaceConfig(root=tmp_path)
        coord = ActivityCoordinator(tmp_path, cfg)

        providers = [
            _ReplayProvider("claude", [
                _event("claude", "claude:sess-1", "2026-04-19T10:00:00Z"),
            ])
        ]

        narrow = coord.gather(
            providers,
            datetime(2026, 4, 19, 9, tzinfo=timezone.utc),
            datetime(2026, 4, 20, tzinfo=timezone.utc),
        )
        assert narrow[0].events_new == 1

        wide = coord.gather(
            providers,
            datetime(2026, 4, 17, tzinfo=timezone.utc),
            datetime(2026, 4, 22, tzinfo=timezone.utc),
        )
        assert wide[0].events_new == 0

        stored = _all_events_in_store(tmp_path)
        assert len(stored) == 1

    def test_provider_failure_on_rerun_preserves_prior_events(self, tmp_path: Path):
        """A provider erroring on run N must not invalidate events from
        run N-1 — the store is append-only and per-provider failures are
        isolated."""
        cfg = WorkspaceConfig(root=tmp_path)
        coord = ActivityCoordinator(tmp_path, cfg)

        ok_events = [_event("git", "git:sha1", "2026-04-21T09:00:00Z")]
        coord.gather([_ReplayProvider("git", ok_events)], *_window())

        class _Failing:
            name = "git"

            def fetch(self, since, until, cfg):
                raise RuntimeError("boom")

        results = coord.gather([_Failing()], *_window())
        assert results[0].errors

        stored = _all_events_in_store(tmp_path)
        assert [s["event_id"] for s in stored] == ["git:sha1"]


def _window():
    return (
        datetime(2026, 4, 21, tzinfo=timezone.utc),
        datetime(2026, 4, 22, tzinfo=timezone.utc),
    )

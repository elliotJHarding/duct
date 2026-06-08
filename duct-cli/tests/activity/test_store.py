"""Tests for duct.activity.store — JSONL dedup + date bucketing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from duct.activity.store import (
    activity_dir,
    append_events,
    iter_events,
    load_state,
    save_state,
)
from duct.models import ActivityEvent


def _event(eid: str, ts: str, **kw) -> ActivityEvent:
    defaults = {
        "event_id": eid,
        "timestamp": ts,
        "source": kw.get("source", "git"),
        "event_type": kw.get("event_type", "commit"),
        "actor": kw.get("actor", "alice"),
        "summary": kw.get("summary", "test"),
        "ticket_key": kw.get("ticket_key"),
        "url": kw.get("url"),
        "duration_seconds": kw.get("duration_seconds"),
        "detail": kw.get("detail", {}),
    }
    return ActivityEvent(**defaults)


class TestAppendEvents:
    def test_writes_to_day_file(self, tmp_path: Path):
        append_events(tmp_path, [_event("a:1", "2026-04-21T09:00:00Z")])
        files = sorted(p.name for p in activity_dir(tmp_path).glob("*.jsonl"))
        assert files == ["2026-04-21.jsonl"]

    def test_buckets_events_by_utc_day(self, tmp_path: Path):
        append_events(
            tmp_path,
            [
                _event("a:1", "2026-04-20T23:30:00Z"),
                _event("a:2", "2026-04-21T00:30:00Z"),
            ],
        )
        files = sorted(p.name for p in activity_dir(tmp_path).glob("*.jsonl"))
        assert files == ["2026-04-20.jsonl", "2026-04-21.jsonl"]

    def test_dedupes_by_event_id(self, tmp_path: Path):
        e1 = _event("a:1", "2026-04-21T09:00:00Z", summary="first")
        assert append_events(tmp_path, [e1]) == 1
        # Same event_id, different summary — should NOT be written again.
        assert append_events(tmp_path, [_event("a:1", "2026-04-21T09:00:00Z", summary="second")]) == 0
        # Event file still contains the original summary.
        path = activity_dir(tmp_path) / "2026-04-21.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(records) == 1
        assert records[0]["summary"] == "first"

    def test_new_events_appended_alongside_existing(self, tmp_path: Path):
        append_events(tmp_path, [_event("a:1", "2026-04-21T09:00:00Z")])
        new = append_events(
            tmp_path,
            [
                _event("a:1", "2026-04-21T09:00:00Z"),
                _event("a:2", "2026-04-21T10:00:00Z"),
            ],
        )
        assert new == 1
        records = [
            json.loads(line)
            for line in (activity_dir(tmp_path) / "2026-04-21.jsonl").read_text().splitlines()
        ]
        assert [r["event_id"] for r in records] == ["a:1", "a:2"]


class TestIterEvents:
    def test_filters_by_window(self, tmp_path: Path):
        append_events(
            tmp_path,
            [
                _event("a:before", "2026-04-21T08:59:59Z"),
                _event("a:inside", "2026-04-21T09:00:00Z"),
                _event("a:boundary_end", "2026-04-21T11:00:00Z"),  # exclusive
                _event("a:after", "2026-04-21T11:00:01Z"),
            ],
        )
        start = datetime(2026, 4, 21, 9, tzinfo=timezone.utc)
        end = datetime(2026, 4, 21, 11, tzinfo=timezone.utc)
        out = list(iter_events(tmp_path, start, end))
        ids = [e.event_id for e in out]
        assert ids == ["a:inside"]

    def test_sorted_ascending(self, tmp_path: Path):
        append_events(
            tmp_path,
            [
                _event("a:2", "2026-04-21T10:00:00Z"),
                _event("a:1", "2026-04-21T09:00:00Z"),
                _event("a:3", "2026-04-21T11:00:00Z"),
            ],
        )
        out = list(
            iter_events(
                tmp_path,
                datetime(2026, 4, 21, tzinfo=timezone.utc),
                datetime(2026, 4, 22, tzinfo=timezone.utc),
            )
        )
        assert [e.event_id for e in out] == ["a:1", "a:2", "a:3"]

    def test_walks_multiple_day_files(self, tmp_path: Path):
        append_events(
            tmp_path,
            [
                _event("a:mon", "2026-04-20T09:00:00Z"),
                _event("a:tue", "2026-04-21T09:00:00Z"),
                _event("a:wed", "2026-04-22T09:00:00Z"),
            ],
        )
        out = list(
            iter_events(
                tmp_path,
                datetime(2026, 4, 20, tzinfo=timezone.utc),
                datetime(2026, 4, 23, tzinfo=timezone.utc),
            )
        )
        assert [e.event_id for e in out] == ["a:mon", "a:tue", "a:wed"]


class TestState:
    def test_round_trip(self, tmp_path: Path):
        assert load_state(tmp_path) == {}
        save_state(tmp_path, {"jira": "2026-04-21T10:00:00Z", "github": "2026-04-21T09:30:00Z"})
        assert load_state(tmp_path) == {
            "jira": "2026-04-21T10:00:00Z",
            "github": "2026-04-21T09:30:00Z",
        }

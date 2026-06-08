"""Tests for OutlookActivityProvider — JXA subprocess output translation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from duct.activity.providers.outlook import (
    OutlookActivityProvider,
    _expand_recurring_master,
    _translate,
)
from duct.config import WorkspaceConfig


class _FakeResult:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _weekly_master(
    *,
    master_id: str = "master-1",
    subject: str = "Daily standup",
    start: str = "2025-09-10T09:00:00Z",
    end: str = "2025-09-10T09:15:00Z",
    series_until: str | None = "2026-12-31T00:00:00Z",
    weekdays: tuple[str, ...] = ("monday", "tuesday", "wednesday", "thursday", "friday"),
) -> dict:
    end_info = (
        {"endType": "end date type", "data": series_until}
        if series_until
        else {"endType": "no end type"}
    )
    return {
        "id": master_id,
        "calendar": "Work",
        "subject": subject,
        "location": "",
        "start": start,
        "end": end,
        "allDay": False,
        "attendees": [],
        "contentPreview": "",
        "recurrence": {
            "recurrenceType": "weekly",
            "occurrenceInterval": 1,
            "daysOfWeek": {day: (day in weekdays) for day in (
                "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
            )},
            "endDate": end_info,
        },
    }


class TestOutlookProvider:
    def test_no_osascript_returns_empty(self, tmp_path: Path):
        # Force the fallback: both the explicit arg and shutil.which() must
        # miss, so the provider short-circuits without spawning osascript.
        with patch("duct.activity.providers.outlook.shutil.which", return_value=None):
            provider = OutlookActivityProvider(osascript_path=None)
            events = list(
                provider.fetch(
                    datetime(2026, 4, 21, tzinfo=timezone.utc),
                    datetime(2026, 4, 22, tzinfo=timezone.utc),
                    WorkspaceConfig(root=tmp_path),
                )
            )
        assert events == []

    def test_parses_jxa_output(self, tmp_path: Path):
        payload = {
            "events": [
                {
                    "id": "abc",
                    "calendar": "Work",
                    "subject": "ERSC-1278 sync",
                    "location": "",
                    "start": "2026-04-21T10:00:00Z",
                    "end": "2026-04-21T10:30:00Z",
                    "allDay": False,
                    "attendees": [{"name": "Bob", "email": "bob@example.com"}],
                    "contentPreview": "",
                }
            ]
        }
        provider = OutlookActivityProvider(osascript_path="/usr/bin/osascript")
        with patch("subprocess.run", return_value=_FakeResult(json.dumps(payload))):
            events = list(
                provider.fetch(
                    datetime(2026, 4, 21, tzinfo=timezone.utc),
                    datetime(2026, 4, 22, tzinfo=timezone.utc),
                    WorkspaceConfig(root=tmp_path),
                )
            )
        assert len(events) == 1
        e = events[0]
        assert e.event_id == "outlook:abc"
        assert e.ticket_key == "ERSC-1278"
        assert e.duration_seconds == 30 * 60
        assert e.event_type == "meeting"

    def test_detects_teams_meeting_via_location(self, tmp_path: Path):
        raw = {
            "id": "t1",
            "calendar": "",
            "subject": "1:1",
            "location": "Microsoft Teams Meeting https://teams.microsoft.com/foo",
            "start": "2026-04-21T10:00:00Z",
            "end": "2026-04-21T10:30:00Z",
            "allDay": False,
            "attendees": [],
            "contentPreview": "",
        }
        events = list(_translate(raw, known_keys=set()))
        assert events[0].event_type == "teams_meeting"
        assert events[0].detail["is_teams"] is True

    def test_outlook_missing_payload_returns_empty(self, tmp_path: Path):
        provider = OutlookActivityProvider(osascript_path="/usr/bin/osascript")
        with patch(
            "subprocess.run",
            return_value=_FakeResult(json.dumps({"error": "Outlook not available"})),
        ):
            events = list(
                provider.fetch(
                    datetime(2026, 4, 21, tzinfo=timezone.utc),
                    datetime(2026, 4, 22, tzinfo=timezone.utc),
                    WorkspaceConfig(root=tmp_path),
                )
            )
        assert events == []


class TestRecurringExpansion:
    """``_expand_recurring_master`` produces one dict per occurrence."""

    def test_expands_weekly_across_two_weeks(self):
        # Mon–Fri standup: 2 weeks = 10 occurrences.
        master = _weekly_master()
        since = datetime(2026, 4, 6, tzinfo=timezone.utc)   # Mon
        until = datetime(2026, 4, 20, tzinfo=timezone.utc)  # Mon (exclusive-ish)
        occurrences = list(_expand_recurring_master(master, since, until))
        assert len(occurrences) == 10
        # Time-of-day preserved from the master's startTime (09:00Z), not
        # the recurrence startDate (midnight).
        for occ in occurrences:
            assert occ["start"].endswith("T09:00:00Z")
        # Occurrence ID disambiguates for dedup.
        ids = {occ["id"] for occ in occurrences}
        assert len(ids) == 10
        assert all(i.startswith("master-1:") for i in ids)

    def test_occurrences_feed_translate_cleanly(self):
        """Expanded dicts flow through _translate without losing fields."""
        master = _weekly_master(subject="ERSC-9999 standup")
        since = datetime(2026, 4, 6, tzinfo=timezone.utc)
        until = datetime(2026, 4, 13, tzinfo=timezone.utc)
        events = [
            e
            for occ in _expand_recurring_master(master, since, until)
            for e in _translate(occ, known_keys={"ERSC-9999"})
        ]
        assert len(events) == 5
        assert all(e.ticket_key == "ERSC-9999" for e in events)
        assert all(e.duration_seconds == 15 * 60 for e in events)
        # event_ids are unique (the store's dedup key).
        assert len({e.event_id for e in events}) == 5

    def test_series_end_date_terminates_expansion(self):
        # Series ends on 2026-04-10 (a Friday). Window goes further.
        master = _weekly_master(series_until="2026-04-10T00:00:00Z")
        since = datetime(2026, 4, 6, tzinfo=timezone.utc)
        until = datetime(2026, 4, 20, tzinfo=timezone.utc)
        occurrences = list(_expand_recurring_master(master, since, until))
        # Expect only the Mon-Fri of the first week (buffered +24h includes
        # the final Fri but nothing past the series end).
        assert len(occurrences) == 5

    def test_open_ended_series_works(self):
        master = _weekly_master(series_until=None)
        since = datetime(2026, 4, 6, tzinfo=timezone.utc)
        until = datetime(2026, 4, 13, tzinfo=timezone.utc)
        occurrences = list(_expand_recurring_master(master, since, until))
        assert len(occurrences) == 5

    def test_malformed_recurrence_yields_nothing(self):
        master = {
            "id": "x",
            "subject": "Broken",
            "start": "2026-04-06T09:00:00Z",
            "end": "2026-04-06T09:30:00Z",
            "recurrence": {"recurrenceType": "weekly"},  # no daysOfWeek / interval
        }
        # Weekly with no daysOfWeek falls back to dtstart's weekday — that's
        # fine and dateutil handles it — but missing recurrenceType should
        # produce zero occurrences.
        since = datetime(2026, 4, 6, tzinfo=timezone.utc)
        until = datetime(2026, 4, 13, tzinfo=timezone.utc)
        occs = list(_expand_recurring_master(master, since, until))
        # Fallback to dtstart weekday means 1 occurrence (the Mon itself).
        assert len(occs) == 1

    def test_unknown_recurrence_type_yields_nothing(self):
        master = {
            "id": "x",
            "subject": "Mystery",
            "start": "2026-04-06T09:00:00Z",
            "end": "2026-04-06T09:30:00Z",
            "recurrence": {"recurrenceType": "lunar eclipse"},
        }
        since = datetime(2026, 4, 6, tzinfo=timezone.utc)
        until = datetime(2026, 4, 13, tzinfo=timezone.utc)
        assert list(_expand_recurring_master(master, since, until)) == []

    def test_count_terminated_series(self):
        master = _weekly_master(series_until=None)
        master["recurrence"]["endDate"] = {"endType": "end numbered type", "data": 3}
        # Series is dtstart=2025-09-10 (Wed) + next occurrences Thu, Fri —
        # capped to 3 occurrences total. Our query window is far later.
        since = datetime(2025, 9, 1, tzinfo=timezone.utc)
        until = datetime(2026, 1, 1, tzinfo=timezone.utc)
        occurrences = list(_expand_recurring_master(master, since, until))
        assert len(occurrences) == 3

    def test_daily_recurrence(self):
        master = {
            "id": "d1",
            "calendar": "Work",
            "subject": "Daily reflection",
            "location": "",
            "start": "2026-04-06T08:00:00Z",
            "end": "2026-04-06T08:15:00Z",
            "allDay": False,
            "attendees": [],
            "contentPreview": "",
            "recurrence": {
                "recurrenceType": "daily",
                "occurrenceInterval": 1,
                "endDate": {"endType": "no end type"},
            },
        }
        since = datetime(2026, 4, 6, tzinfo=timezone.utc)
        until = datetime(2026, 4, 13, tzinfo=timezone.utc)
        occurrences = list(_expand_recurring_master(master, since, until))
        # Master start is 08:00Z; next occurrence after 2026-04-13T00:00Z
        # (the ``until``) would be 2026-04-13T08:00Z, past the window.
        assert len(occurrences) == 7  # 6,7,8,9,10,11,12 @ 08:00

    def test_absolute_yearly(self):
        master = {
            "id": "y1",
            "calendar": "Holidays",
            "subject": "Battle of the Boyne",
            "location": "",
            "start": "2012-07-12T00:00:00Z",
            "end": "2012-07-13T00:00:00Z",
            "allDay": True,
            "attendees": [],
            "contentPreview": "",
            "recurrence": {
                "recurrenceType": "absolute yearly",
                "occurrenceInterval": 1,
                "dayOfMonth": 12,
                "monthNumber": 7,
                "endDate": {"endType": "no end type"},
            },
        }
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        until = datetime(2027, 1, 1, tzinfo=timezone.utc)
        occurrences = list(_expand_recurring_master(master, since, until))
        assert len(occurrences) == 1
        assert occurrences[0]["start"].startswith("2026-07-12")

    def test_skips_exception_dates(self):
        """Rescheduled/cancelled occurrences suppress the original-slot ghost."""
        master = _weekly_master()
        # Mark Wednesday 2026-04-08 as an exception (e.g. rescheduled).
        master["recurrence"]["exceptions"] = ["2026-04-08T09:00:00Z"]
        since = datetime(2026, 4, 6, tzinfo=timezone.utc)
        until = datetime(2026, 4, 13, tzinfo=timezone.utc)
        occurrences = list(_expand_recurring_master(master, since, until))
        dates = [occ["start"][:10] for occ in occurrences]
        assert "2026-04-08" not in dates
        assert len(occurrences) == 4  # Mon, Tue, Thu, Fri

    def test_exceptions_with_timezone_variants_still_match(self):
        """Exception ISOs with trailing 'Z' and '+00:00' both suppress ghosts."""
        master = _weekly_master()
        master["recurrence"]["exceptions"] = ["2026-04-08T09:00:00+00:00"]
        since = datetime(2026, 4, 6, tzinfo=timezone.utc)
        until = datetime(2026, 4, 13, tzinfo=timezone.utc)
        occurrences = list(_expand_recurring_master(master, since, until))
        assert len(occurrences) == 4

    def test_exception_matching_ignores_hour_of_day(self):
        """DST drift: rrule emits fixed-UTC-hour occurrences, but Outlook's
        exception recurrenceIds respect local wall-clock time. Matching by
        date (not full datetime) keeps suppression working across DST."""
        master = _weekly_master()
        # Simulates the real case where rrule computes 14:00Z but the
        # Outlook-recorded exception is at 13:00Z (BST).
        master["recurrence"]["exceptions"] = ["2026-04-08T13:00:00Z"]
        since = datetime(2026, 4, 6, tzinfo=timezone.utc)
        until = datetime(2026, 4, 13, tzinfo=timezone.utc)
        occurrences = list(_expand_recurring_master(master, since, until))
        dates = [occ["start"][:10] for occ in occurrences]
        assert "2026-04-08" not in dates
        assert len(occurrences) == 4

    def test_relative_monthly(self):
        # Third Tuesday of every month.
        master = {
            "id": "rm1",
            "calendar": "Work",
            "subject": "Staff meeting",
            "location": "",
            "start": "2026-01-20T10:00:00Z",  # Tue
            "end": "2026-01-20T11:00:00Z",
            "allDay": False,
            "attendees": [],
            "contentPreview": "",
            "recurrence": {
                "recurrenceType": "relative monthly",
                "occurrenceInterval": 1,
                "ordinal": 3,
                "daysOfWeek": {
                    "monday": False, "tuesday": True, "wednesday": False,
                    "thursday": False, "friday": False, "saturday": False, "sunday": False,
                },
                "endDate": {"endType": "no end type"},
            },
        }
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        until = datetime(2026, 7, 1, tzinfo=timezone.utc)
        occurrences = list(_expand_recurring_master(master, since, until))
        # Six months → six occurrences, each a Tuesday.
        assert len(occurrences) == 6
        dates = [occ["start"][:10] for occ in occurrences]
        # Third Tuesdays of Jan–Jun 2026.
        assert dates == [
            "2026-01-20", "2026-02-17", "2026-03-17",
            "2026-04-21", "2026-05-19", "2026-06-16",
        ]


class TestProviderWithMasters:
    def test_singles_and_masters_both_appear(self, tmp_path: Path):
        """End-to-end: payload with one single + one master yields both."""
        payload = {
            "events": [
                {
                    "id": "one-off",
                    "calendar": "Work",
                    "subject": "Ad-hoc",
                    "location": "",
                    "start": "2026-04-08T14:00:00Z",
                    "end": "2026-04-08T14:30:00Z",
                    "allDay": False,
                    "attendees": [],
                    "contentPreview": "",
                }
            ],
            "recurring_masters": [_weekly_master()],
        }
        provider = OutlookActivityProvider(osascript_path="/usr/bin/osascript")
        with patch("subprocess.run", return_value=_FakeResult(json.dumps(payload))):
            events = list(
                provider.fetch(
                    datetime(2026, 4, 6, tzinfo=timezone.utc),
                    datetime(2026, 4, 13, tzinfo=timezone.utc),
                    WorkspaceConfig(root=tmp_path),
                )
            )
        # 1 single + 5 weekday standups.
        assert len(events) == 6
        assert any(e.event_id == "outlook:one-off" for e in events)
        recurring_ids = [e.event_id for e in events if e.event_id.startswith("outlook:master-1:")]
        assert len(recurring_ids) == 5
        assert len(set(recurring_ids)) == 5  # all distinct

    def test_recurrence_horizon_caps_expansion(self, tmp_path: Path):
        """``outlook_recurrence_max_days`` clamps a large ``until``."""
        from duct.config import ActivityConfig
        cfg = WorkspaceConfig(
            root=tmp_path,
            activity=ActivityConfig(outlook_recurrence_max_days=7),
        )
        payload = {"events": [], "recurring_masters": [_weekly_master()]}
        provider = OutlookActivityProvider(osascript_path="/usr/bin/osascript")
        with patch("subprocess.run", return_value=_FakeResult(json.dumps(payload))):
            events = list(
                provider.fetch(
                    datetime(2026, 4, 6, tzinfo=timezone.utc),
                    datetime(2027, 1, 1, tzinfo=timezone.utc),  # huge window
                    cfg,
                )
            )
        # Clamped to since + 7 days → only the first week's Mon-Fri.
        assert len(events) == 5

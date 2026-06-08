"""Tests for OutlookPdfActivityProvider — text parsing of pdftotext output."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from duct.activity.providers.outlook_pdf import (
    OutlookPdfActivityProvider,
    _parse_events,
)
from duct.config import WorkspaceConfig


# Sample pdftotext -layout output modelled on the real format:
#   - Month header
#   - Date header
#   - Event title on its own line
#   - ``Wed DD/MM/YYYY HH:MM - HH:MM`` time line
#   - Location / Organiser / attendee fields
#   - Optional separator underscore line; sometimes omitted between cancelled events
_FIXTURE = """
April 2026
Calendar


Wednesday, April 01, 2026

  Integration standup
  Wed 01/04/2026 09:15 - 09:30

  Location: UKC - Putney Bridge
  Organiser: Piotr Klimczak
  Required Attendees: Piotr Klimczak <piotr.klimczak@iceinsuretech.com>; Rhys Farrant
  <rhys.farrant@iceinsuretech.com>; Elliot Harding <elliot.harding@iceinsuretech.com>;
  Optional Attendees: Hedley Proctor <hedley.proctor@iceinsuretech.com>;

  ________________________________________________________________________________

  Join Microsoft Teams Meeting

  ________________________________________________________________________________

  Canceled: Claims - Stand up
  Wed 01/04/2026 09:30 - 09:45

  Location: Microsoft Teams Meeting
  Organiser: Helen Bradley
  Required Attendees: Helen Bradley <helen.bradley@ice-tech.com>; Elliot Harding <elliot.harding@ice-tech.com>;

Thursday, April 02, 2026

  ERSC-1504 Review
  Thu 02/04/2026 14:00 - 15:00

  Location: Microsoft Teams Meeting
  Organiser: Elliot Harding
  Required Attendees: Elliot Harding <elliot.harding@iceinsuretech.com>;
"""


class TestParsing:
    def test_three_events_from_fixture(self):
        events = list(_parse_events(_FIXTURE))
        titles = [e["title"] for e in events]
        assert titles == ["Integration standup", "Claims - Stand up", "ERSC-1504 Review"]

    def test_canceled_marker_stripped_from_title(self):
        events = list(_parse_events(_FIXTURE))
        claims = events[1]
        assert claims["title"] == "Claims - Stand up"
        assert claims["canceled"] is True

    def test_non_canceled_events_not_flagged(self):
        events = list(_parse_events(_FIXTURE))
        assert events[0]["canceled"] is False
        assert events[2]["canceled"] is False

    def test_fields_do_not_bleed_across_missing_separator(self):
        # There is no underline separator between the cancelled event's
        # trailing attendees and the next event's date header. The parser
        # must still cleanly scope each event's fields — confirmed by the
        # organiser being single-valued, not concatenated.
        events = list(_parse_events(_FIXTURE))
        assert events[1]["organiser"] == "Helen Bradley"
        assert events[2]["organiser"] == "Elliot Harding"

    def test_attendees_continuation_lines_joined(self):
        events = list(_parse_events(_FIXTURE))
        integration = events[0]
        assert "Piotr Klimczak" in integration["required"]
        assert "Rhys Farrant" in integration["required"]
        assert "Elliot Harding" in integration["required"]


class TestFetch:
    def test_fetch_emits_events_from_pdftotext_output(self, tmp_path: Path):
        pdf = tmp_path / "cal.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF")  # placeholder; we mock pdftotext

        class _Result:
            returncode = 0
            stdout = _FIXTURE
            stderr = ""

        provider = OutlookPdfActivityProvider(
            pdf_path=str(pdf), pdftotext="/usr/bin/pdftotext"
        )
        with patch("subprocess.run", return_value=_Result()):
            events = list(
                provider.fetch(
                    datetime(2026, 4, 1, tzinfo=timezone.utc),
                    datetime(2026, 4, 3, tzinfo=timezone.utc),
                    WorkspaceConfig(root=tmp_path),
                )
            )
        # Only 1st of April events fall strictly inside [Apr 1, Apr 3) UTC once
        # BST (+01:00) is applied — 09:15 local = 08:15 UTC on Apr 1, still
        # inside the window.
        subjects = [e.detail["subject"] for e in events]
        assert "Integration standup" in subjects
        assert "Claims - Stand up" in subjects
        # Apr 2 14:00 local = 13:00 UTC, so ERSC review is in the window too.
        assert "ERSC-1504 Review" in subjects

    def test_window_exclusion(self, tmp_path: Path):
        pdf = tmp_path / "cal.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF")

        class _Result:
            returncode = 0
            stdout = _FIXTURE
            stderr = ""

        provider = OutlookPdfActivityProvider(
            pdf_path=str(pdf), pdftotext="/usr/bin/pdftotext"
        )
        with patch("subprocess.run", return_value=_Result()):
            events = list(
                provider.fetch(
                    datetime(2026, 4, 2, 20, tzinfo=timezone.utc),
                    datetime(2026, 4, 3, tzinfo=timezone.utc),
                    WorkspaceConfig(root=tmp_path),
                )
            )
        assert events == []

    def test_ticket_inference_from_subject(self, tmp_path: Path):
        pdf = tmp_path / "cal.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF")

        class _Result:
            returncode = 0
            stdout = _FIXTURE
            stderr = ""

        # Create an ERSC-1504 ticket so ticket_key resolves.
        (tmp_path / "ERSC-1504-review" / "orchestrator").mkdir(parents=True)

        provider = OutlookPdfActivityProvider(
            pdf_path=str(pdf), pdftotext="/usr/bin/pdftotext"
        )
        with patch("subprocess.run", return_value=_Result()):
            events = list(
                provider.fetch(
                    datetime(2026, 4, 1, tzinfo=timezone.utc),
                    datetime(2026, 4, 3, tzinfo=timezone.utc),
                    WorkspaceConfig(root=tmp_path),
                )
            )
        ersc = [e for e in events if e.detail["subject"] == "ERSC-1504 Review"]
        assert ersc and ersc[0].ticket_key == "ERSC-1504"

    def test_teams_detection_from_location(self, tmp_path: Path):
        pdf = tmp_path / "cal.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF")

        class _Result:
            returncode = 0
            stdout = _FIXTURE
            stderr = ""

        provider = OutlookPdfActivityProvider(
            pdf_path=str(pdf), pdftotext="/usr/bin/pdftotext"
        )
        with patch("subprocess.run", return_value=_Result()):
            events = list(
                provider.fetch(
                    datetime(2026, 4, 1, tzinfo=timezone.utc),
                    datetime(2026, 4, 3, tzinfo=timezone.utc),
                    WorkspaceConfig(root=tmp_path),
                )
            )
        ersc = [e for e in events if e.detail["subject"] == "ERSC-1504 Review"]
        assert ersc and ersc[0].event_type == "teams_meeting"

        putney = [e for e in events if e.detail["subject"] == "Integration standup"]
        assert putney and putney[0].event_type == "meeting"

    def test_canceled_event_type(self, tmp_path: Path):
        pdf = tmp_path / "cal.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF")

        class _Result:
            returncode = 0
            stdout = _FIXTURE
            stderr = ""

        provider = OutlookPdfActivityProvider(
            pdf_path=str(pdf), pdftotext="/usr/bin/pdftotext"
        )
        with patch("subprocess.run", return_value=_Result()):
            events = list(
                provider.fetch(
                    datetime(2026, 4, 1, tzinfo=timezone.utc),
                    datetime(2026, 4, 3, tzinfo=timezone.utc),
                    WorkspaceConfig(root=tmp_path),
                )
            )
        claims = [e for e in events if "Claims - Stand up" in e.detail["subject"]]
        assert claims and claims[0].event_type == "meeting_canceled"
        assert claims[0].detail["canceled"] is True
        assert claims[0].summary.startswith("[canceled] ")

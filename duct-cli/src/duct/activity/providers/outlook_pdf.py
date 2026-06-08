"""Outlook agenda PDF activity provider.

Parses an Outlook-for-Mac agenda export (File → Print → Save as PDF) via
``pdftotext -layout``. This path exists because the AppleScript and
classic-sqlite routes don't expose past meetings for Exchange-synced
accounts, and Graph API access typically requires tenant admin approval.

The PDF export is a reliable zero-approval fallback: every event appears
as a fixed-shape block with title, local-time range, organiser, and
attendees. Subject and attendees are **not** redacted (unlike Outlook's
OSA telemetry logs), which makes the output useful for standups and
timesheet entries, not just time-blocks.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from duct.activity.base import infer_ticket_key
from duct.config import WorkspaceConfig
from duct.exceptions import SyncError
from duct.models import ActivityEvent
from duct.workspace import enumerate_ticket_dirs

# Event time line, e.g. ``Wed 01/04/2026 09:15 - 09:30``. The recurrence
# symbol (♻ / ↻) sometimes follows, preceded by arbitrary whitespace.
_TIME_RE = re.compile(
    r"\b(?P<dow>Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?P<day>\d{2})/(?P<month>\d{2})/(?P<year>\d{4})\s+"
    r"(?P<start>\d{2}:\d{2})\s*-\s*(?P<end>\d{2}:\d{2})"
)

_DATE_HEADER_RE = re.compile(
    r"^\s*(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"\d{1,2},\s+\d{4}\s*$"
)

_SEPARATOR_RE = re.compile(r"^\s*_{10,}\s*$")

_FIELDS = ("Location", "Organiser", "Organizer", "Required Attendees", "Optional Attendees")


class OutlookPdfActivityProvider:
    name = "outlook_pdf"

    def __init__(self, pdf_path: str, pdftotext: str | None = None):
        self._pdf_path = Path(pdf_path).expanduser() if pdf_path else None
        self._pdftotext = pdftotext or shutil.which("pdftotext")

    def fetch(
        self,
        since: datetime,
        until: datetime,
        cfg: WorkspaceConfig,
    ) -> Iterator[ActivityEvent]:
        if not self._pdf_path:
            return
        if not self._pdf_path.exists():
            raise SyncError(f"outlook_pdf: file not found: {self._pdf_path}")
        if not self._pdftotext:
            raise SyncError("outlook_pdf: pdftotext binary not on PATH (brew install poppler)")

        text = self._extract_text()
        known_keys = {key for key, _ in enumerate_ticket_dirs(cfg.root)}
        for raw in _parse_events(text):
            start, end = _resolve_local_datetimes(raw)
            if start is None:
                continue
            if end < since or start >= until:
                continue
            yield _to_event(raw, start, end, known_keys)

    def _extract_text(self) -> str:
        try:
            result = subprocess.run(
                [self._pdftotext, "-layout", str(self._pdf_path), "-"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise SyncError(f"pdftotext failed: {exc}") from exc
        if result.returncode != 0:
            raise SyncError(f"pdftotext exited {result.returncode}: {result.stderr[:200]}")
        return result.stdout


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_events(text: str) -> Iterator[dict]:
    """Yield raw event dicts keyed by field name.

    Splits the text on event time lines (``Wed DD/MM/YYYY HH:MM - HH:MM``)
    rather than on the underline separators, because Outlook's PDF export
    sometimes omits the trailing separator between events — which would
    otherwise bleed the next event's fields into the current one.
    """
    lines = text.splitlines()

    # Locate every time-line and use those as event boundaries.
    time_line_indices: list[int] = []
    time_matches: list = []
    for i, line in enumerate(lines):
        m = _TIME_RE.search(line)
        if m:
            time_line_indices.append(i)
            time_matches.append(m)

    for idx, (start, match) in enumerate(zip(time_line_indices, time_matches)):
        end = time_line_indices[idx + 1] if idx + 1 < len(time_line_indices) else len(lines)
        event = _parse_event_range(lines, start, end, match, prior_end=time_line_indices[idx - 1] if idx > 0 else -1)
        if event:
            yield event


def _parse_event_range(
    lines: list[str],
    time_idx: int,
    next_time_idx: int,
    time_match,
    prior_end: int,
) -> dict | None:
    """Extract fields for the event whose time line is at ``time_idx``.

    Title is the nearest non-empty, non-date-header line above the time
    line, scanning backwards but not past the previous event's time line.
    Fields are the labelled lines strictly between ``time_idx`` and
    ``next_time_idx``.
    """
    # Title lookup — scan upwards but stop at the previous event's time line.
    title = ""
    lower_bound = prior_end + 1 if prior_end >= 0 else 0
    for line in reversed(lines[lower_bound:time_idx]):
        stripped = line.strip()
        if not stripped:
            continue
        if _DATE_HEADER_RE.match(stripped):
            continue
        if _SEPARATOR_RE.match(line):
            continue
        if stripped == "Calendar":
            continue
        if re.match(r"^[A-Z][a-z]+ \d{4}$", stripped):
            continue
        title = stripped
        break

    canceled = False
    if title.lower().startswith(("canceled:", "cancelled:")):
        canceled = True
        title = title.split(":", 1)[1].strip()

    fields: dict[str, list[str]] = {}
    current_label: str | None = None
    for line in lines[time_idx + 1:next_time_idx]:
        stripped = line.strip()
        if not stripped:
            current_label = None
            continue
        if _SEPARATOR_RE.match(line):
            current_label = None
            continue

        matched_label = None
        for label in _FIELDS:
            prefix = f"{label}:"
            if stripped.startswith(prefix):
                matched_label = label
                rest = stripped[len(prefix):].strip()
                fields.setdefault(label, []).append(rest)
                break
        if matched_label:
            current_label = matched_label
            continue

        if current_label:
            fields[current_label].append(stripped)

    return {
        "title": title,
        "canceled": canceled,
        "date": time_match.group("day") + "/" + time_match.group("month") + "/" + time_match.group("year"),
        "start": time_match.group("start"),
        "end": time_match.group("end"),
        "location": " ".join(fields.get("Location", [])).strip(),
        "organiser": " ".join(fields.get("Organiser", fields.get("Organizer", []))).strip(),
        "required": " ".join(fields.get("Required Attendees", [])).strip(),
        "optional": " ".join(fields.get("Optional Attendees", [])).strip(),
    }


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


def _resolve_local_datetimes(raw: dict) -> tuple[datetime | None, datetime | None]:
    """Parse the event's dd/mm/yyyy + HH:MM times as local-tz, return UTC."""
    try:
        day, month, year = raw["date"].split("/")
        start_local = datetime(
            int(year), int(month), int(day),
            int(raw["start"].split(":")[0]), int(raw["start"].split(":")[1]),
        )
        end_local = datetime(
            int(year), int(month), int(day),
            int(raw["end"].split(":")[0]), int(raw["end"].split(":")[1]),
        )
    except (ValueError, KeyError):
        return None, None
    if end_local < start_local:
        # Event crosses midnight — bump the end date forward by a day. The
        # PDF's end-time only shows HH:MM so a late-night → morning meeting
        # would otherwise compute a negative duration.
        from datetime import timedelta

        end_local = end_local + timedelta(days=1)
    local_tz = datetime.now().astimezone().tzinfo
    start_utc = start_local.replace(tzinfo=local_tz).astimezone(timezone.utc)
    end_utc = end_local.replace(tzinfo=local_tz).astimezone(timezone.utc)
    return start_utc, end_utc


_EMAIL_RE = re.compile(r"<([^>]+@[^>]+)>")


def _parse_attendees(raw_text: str) -> list[dict[str, str]]:
    """Split an attendees string into ``[{name, email}]`` entries.

    Attendees are separated by ``;``; each entry is usually ``Name
    <email@host>``. Entries without angle-bracket emails are kept as
    name-only.
    """
    attendees: list[dict[str, str]] = []
    for chunk in raw_text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _EMAIL_RE.search(chunk)
        if m:
            email = m.group(1).strip()
            name = chunk[:m.start()].strip()
            attendees.append({"name": name, "email": email})
        else:
            attendees.append({"name": chunk, "email": ""})
    return attendees


def _to_event(raw: dict, start: datetime, end: datetime, known_keys: set[str]) -> ActivityEvent:
    subject = raw["title"]
    location = raw["location"]
    organiser = raw["organiser"]
    required = _parse_attendees(raw["required"])
    optional = _parse_attendees(raw["optional"])
    attendee_count = len(required) + len(optional)

    is_teams = "teams meeting" in location.lower() or "teams.microsoft.com" in location.lower()
    if raw["canceled"]:
        event_type = "meeting_canceled"
    elif is_teams:
        event_type = "teams_meeting"
    else:
        event_type = "meeting"

    ticket = infer_ticket_key(f"{subject} {location}", known_keys)
    duration = max(0, int((end - start).total_seconds()))
    start_iso = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Stable id: the PDF has no UIDs, so hash start + normalised subject.
    # Re-exports of the same export window dedupe cleanly; a renamed event
    # becomes a new entry (acceptable — we'd rather not miss the rename).
    digest = hashlib.sha1(f"{start_iso}|{subject.strip().lower()}".encode()).hexdigest()[:16]

    attendee_summary = ", ".join(a["name"] or a["email"] for a in required[:4])
    if len(required) > 4:
        attendee_summary += f", +{len(required) - 4} more"
    summary = f"Meeting: {subject[:140]}"
    if attendee_summary:
        summary += f" (w/ {attendee_summary})"
    if raw["canceled"]:
        summary = "[canceled] " + summary

    return ActivityEvent(
        event_id=f"outlook_pdf:{digest}",
        timestamp=start_iso,
        source="outlook_pdf",
        event_type=event_type,
        actor="self",
        summary=summary,
        ticket_key=ticket,
        url=None,
        duration_seconds=duration,
        detail={
            "subject": subject,
            "location": location,
            "organiser": organiser,
            "start": start_iso,
            "end": end_iso,
            "is_teams": is_teams,
            "canceled": raw["canceled"],
            "required_attendees": required,
            "optional_attendees": optional,
            "attendee_count": attendee_count,
        },
    )

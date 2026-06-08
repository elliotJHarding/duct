"""Outlook calendar events via AppleScript / JXA.

Runs ``osascript -l JavaScript`` against Microsoft Outlook on macOS to list
calendar events in the requested window.

Two kinds of events are returned by the JXA script:

* ``events`` — one-off meetings and materialised recurring occurrences
  (accepted/declined/modified exceptions that Outlook has spawned as
  standalone records). Passed straight through to ``_translate``.
* ``recurring_masters`` — series masters with their recurrence record.
  Python expands these via ``dateutil.rrule``, filtered to the requested
  window, then feeds each occurrence through the same ``_translate``.

The JXA layer deliberately does not use ``whose`` to filter series masters
— a master's ``startTime`` is the first occurrence (often years ago), so a
``startTime >= since`` predicate silently drops every recurring meeting.

Returns empty when Outlook isn't installed, osascript isn't on PATH, or
the JXA call errors — the failure is logged through the normal coordinator
error path but doesn't bring down the whole gather.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from dateutil.rrule import DAILY, MONTHLY, WEEKLY, YEARLY, rrule, weekday

from duct.activity.base import infer_ticket_key
from duct.config import WorkspaceConfig
from duct.exceptions import SyncError
from duct.models import ActivityEvent
from duct.workspace import enumerate_ticket_dirs

_JXA_SCRIPT = r"""
function run(argv) {
  var sinceMs = parseInt(argv[0], 10);
  var untilMs = parseInt(argv[1], 10);
  var sinceDate = new Date(sinceMs);
  var untilDate = new Date(untilMs);

  var Outlook;
  try {
    Outlook = Application("Microsoft Outlook");
  } catch (e) {
    return JSON.stringify({ error: "Microsoft Outlook not available: " + e.message });
  }

  var calendars;
  try {
    calendars = Outlook.calendars();
  } catch (e) {
    return JSON.stringify({ error: "Unable to read calendars: " + e.message });
  }

  function readAttendees(ev) {
    var out = [];
    try {
      var raw = ev.attendees();
      for (var k = 0; k < raw.length; k++) {
        try {
          var a = raw[k];
          out.push({
            name: a.name ? a.name() : "",
            email: a.emailAddress ? a.emailAddress() : "",
          });
        } catch (e) {}
      }
    } catch (e) {}
    return out;
  }

  function readBase(ev, calName) {
    var id = String(ev.id());
    var subject = ""; try { subject = ev.subject() || ""; } catch (e) {}
    var location = ""; try { location = ev.location() || ""; } catch (e) {}
    var startTime = ev.startTime();
    var endTime = ev.endTime();
    var allDay = false; try { allDay = !!ev.allDayFlag(); } catch (e) {}
    var contentPreview = "";
    try { contentPreview = (ev.content() || "").substring(0, 300); } catch (e) {}
    return {
      id: id,
      calendar: calName,
      subject: subject,
      location: location,
      start: startTime.toISOString(),
      end: endTime.toISOString(),
      allDay: allDay,
      attendees: readAttendees(ev),
      contentPreview: contentPreview,
    };
  }

  function readRecurrence(rec) {
    // Outlook recurrence record shape (probed against Outlook 16.108):
    //   recurrenceType: "daily" | "weekly" | "absolute monthly" |
    //                   "relative monthly" | "absolute yearly" | "relative yearly"
    //   occurrenceInterval: int
    //   daysOfWeek: { monday: bool, tuesday: bool, ... } (weekly / relative variants)
    //   ordinal: 1..5 (relative variants; 5 == "last")
    //   dayOfMonth: int (absolute variants)
    //   monthNumber: int (absolute yearly, relative yearly)
    //   endDate: { endType: "no end type" }
    //          | { endType: "end date type", data: <ISO> }
    //          | { endType: "end numbered type", data: <int count> }
    var out = {};
    try { out.recurrenceType = String(rec.recurrenceType); } catch (e) {}
    try { out.occurrenceInterval = rec.occurrenceInterval; } catch (e) {}
    try { out.ordinal = rec.ordinal; } catch (e) {}
    try { out.dayOfMonth = rec.dayOfMonth; } catch (e) {}
    try { out.monthNumber = rec.monthNumber; } catch (e) {}
    try {
      var dow = rec.daysOfWeek;
      if (dow && typeof dow === 'object') {
        out.daysOfWeek = {
          monday: !!dow.monday, tuesday: !!dow.tuesday,
          wednesday: !!dow.wednesday, thursday: !!dow.thursday,
          friday: !!dow.friday, saturday: !!dow.saturday,
          sunday: !!dow.sunday,
        };
      }
    } catch (e) {}
    try {
      var ed = rec.endDate;
      if (ed && typeof ed === 'object') {
        var info = {};
        try { info.endType = String(ed.endType); } catch (e) {}
        try {
          var data = ed.data;
          if (data !== undefined && data !== null) {
            if (data && data.toISOString) info.data = data.toISOString();
            else info.data = data;
          }
        } catch (e) {}
        out.endDate = info;
      }
    } catch (e) {}
    return out;
  }

  var singles = [];
  var masters = [];
  // Modified / cancelled occurrences are stored as standalone events whose
  // ``master`` property points back to the series and whose ``recurrenceId``
  // is the *original* slot they replace. Collecting (master_id, original_iso)
  // lets us suppress the ghost occurrence Python would otherwise generate
  // from the master's recurrence rule.
  var exceptionsByMaster = {};

  for (var i = 0; i < calendars.length; i++) {
    var cal = calendars[i];
    var calName = ""; try { calName = cal.name(); } catch (e) {}

    var evs;
    try { evs = cal.calendarEvents(); } catch (e) { continue; }

    for (var j = 0; j < evs.length; j++) {
      var ev = evs[j];
      var rec = null;
      try { rec = ev.recurrence(); } catch (e) { rec = null; }
      try {
        if (rec) {
          // Series master: emit regardless of startTime — Python expands.
          var base = readBase(ev, calName);
          base.recurrence = readRecurrence(rec);
          masters.push(base);
        } else {
          // Single / materialised: filter by the requested window in JS.
          var s = ev.startTime();
          var e = ev.endTime();
          var inWindow = !(s >= untilDate || e <= sinceDate);
          if (inWindow) singles.push(readBase(ev, calName));

          // Regardless of window, record this as an exception to its master
          // so ghost occurrences get suppressed downstream.
          try {
            if (ev.isOccurrence()) {
              var m = ev.master();
              var rid = ev.recurrenceId();
              if (m && rid) {
                var mid = String(m.id());
                if (!exceptionsByMaster[mid]) exceptionsByMaster[mid] = [];
                exceptionsByMaster[mid].push(rid.toISOString());
              }
            }
          } catch (exErr) {}
        }
      } catch (err) {
        // Skip individual events that fail to read.
      }
    }
  }

  // Attach each master's exception dates before returning.
  for (var k = 0; k < masters.length; k++) {
    var mid = masters[k].id;
    if (exceptionsByMaster[mid]) {
      masters[k].recurrence.exceptions = exceptionsByMaster[mid];
    }
  }

  return JSON.stringify({ events: singles, recurring_masters: masters });
}
"""


_WEEKDAY_ORDER = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY_INDEX = {name: idx for idx, name in enumerate(_WEEKDAY_ORDER)}


class OutlookActivityProvider:
    name = "outlook"

    def __init__(self, osascript_path: str | None = None):
        self._osascript = osascript_path or shutil.which("osascript")

    def fetch(
        self,
        since: datetime,
        until: datetime,
        cfg: WorkspaceConfig,
    ) -> Iterator[ActivityEvent]:
        if not self._osascript:
            # No macOS / no osascript → nothing to do, not an error condition.
            return

        since_ms = int(since.astimezone(timezone.utc).timestamp() * 1000)
        until_ms = int(until.astimezone(timezone.utc).timestamp() * 1000)

        try:
            result = subprocess.run(
                [
                    self._osascript,
                    "-l",
                    "JavaScript",
                    "-e",
                    _JXA_SCRIPT,
                    str(since_ms),
                    str(until_ms),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise SyncError(f"osascript failed: {exc}") from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if "not allowed to send keystrokes" in stderr.lower() or "not authorized" in stderr.lower():
                raise SyncError(
                    "Outlook automation access denied — grant Terminal/iTerm "
                    "access in System Settings › Privacy › Automation › Outlook"
                )
            raise SyncError(f"osascript exited {result.returncode}: {stderr[:200]}")

        raw_out = (result.stdout or "").strip()
        if not raw_out:
            return
        try:
            payload = json.loads(raw_out)
        except json.JSONDecodeError as exc:
            raise SyncError(f"could not parse Outlook JSON: {exc}") from exc

        if "error" in payload:
            # Outlook not installed / not running — treat as no events.
            return

        known_keys = {key for key, _ in enumerate_ticket_dirs(cfg.root)}

        # Clamp the recurrence-expansion horizon — an accidentally large
        # ``until`` shouldn't produce years of daily-standup rows.
        cap_days = getattr(cfg.activity, "outlook_recurrence_max_days", 60)
        expansion_until = min(until, since + timedelta(days=max(1, cap_days)))

        for raw in payload.get("events", []) or []:
            yield from _translate(raw, known_keys)
        for master in payload.get("recurring_masters", []) or []:
            for occurrence in _expand_recurring_master(master, since, expansion_until):
                yield from _translate(occurrence, known_keys)


def _expand_recurring_master(
    master: dict, since: datetime, until: datetime
) -> Iterator[dict]:
    """Yield per-occurrence dicts shaped like singles, for ``_translate``."""
    start_dt = _parse(master.get("start", ""))
    end_dt = _parse(master.get("end", ""))
    if not start_dt:
        return
    duration = (end_dt - start_dt) if end_dt else timedelta(0)

    rec = master.get("recurrence") or {}
    rule = _build_rrule(rec, start_dt)
    if rule is None:
        return

    # Materialised exceptions (modified or cancelled occurrences) carry the
    # original slot's timestamp in their recurrenceId. Match by date only —
    # Outlook recurrence patterns don't go sub-daily, and the UTC hour
    # drifts around DST transitions (rrule produces fixed-UTC-hour output
    # while Outlook's recurrenceIds respect local wall-clock time), so
    # matching on full datetime would miss suppressions in BST months.
    exception_dates: set = set()
    for iso in rec.get("exceptions") or []:
        dt = _parse(iso)
        if dt:
            exception_dates.add(dt.date())

    master_id = master.get("id", "")
    for occ_start in rule.between(since, until, inc=True):
        if occ_start.tzinfo is None:
            occ_start = occ_start.replace(tzinfo=timezone.utc)
        if occ_start.astimezone(timezone.utc).date() in exception_dates:
            continue
        occ_end = occ_start + duration
        occ_iso = occ_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        yield {
            "id": f"{master_id}:{occ_iso}",
            "calendar": master.get("calendar", ""),
            "subject": master.get("subject", ""),
            "location": master.get("location", ""),
            "start": occ_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": occ_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "allDay": bool(master.get("allDay", False)),
            "attendees": master.get("attendees", []) or [],
            "contentPreview": master.get("contentPreview", ""),
        }


def _build_rrule(rec: dict, dtstart: datetime) -> rrule | None:
    """Translate Outlook's recurrence record to a ``dateutil.rrule``.

    Returns None when the record is missing / malformed / references a
    recurrence type we don't handle — caller treats that as "no occurrences".
    """
    r_type = (rec.get("recurrenceType") or "").strip().lower()
    interval = _coerce_int(rec.get("occurrenceInterval"), default=1) or 1

    end_info = rec.get("endDate") or {}
    until = None
    count = None
    end_type = (end_info.get("endType") or "").strip().lower()
    if end_type == "end date type":
        until_dt = _parse(end_info.get("data"))
        # Outlook's "until" is a midnight date; give the last day a 24h buffer
        # so same-day occurrences at non-midnight times aren't clipped.
        if until_dt is not None:
            until = until_dt + timedelta(days=1)
    elif end_type == "end numbered type":
        count = _coerce_int(end_info.get("data")) or None
    # "no end type" (or anything unrecognised) → open-ended.

    kwargs: dict = {"dtstart": dtstart, "interval": interval}
    if until is not None:
        kwargs["until"] = until
    if count is not None:
        kwargs["count"] = count

    try:
        if r_type == "daily":
            return rrule(DAILY, **kwargs)
        if r_type == "weekly":
            byweekday = _active_weekdays(rec.get("daysOfWeek") or {})
            if byweekday:
                kwargs["byweekday"] = byweekday
            return rrule(WEEKLY, **kwargs)
        if r_type == "absolute monthly":
            bymonthday = _coerce_int(rec.get("dayOfMonth"))
            if bymonthday is None:
                return None
            kwargs["bymonthday"] = bymonthday
            return rrule(MONTHLY, **kwargs)
        if r_type == "relative monthly":
            pos = _ordinal_to_position(rec.get("ordinal"))
            active = _weekdays_with_position(rec.get("daysOfWeek") or {}, pos)
            if not active:
                return None
            kwargs["byweekday"] = active
            return rrule(MONTHLY, **kwargs)
        if r_type == "absolute yearly":
            bymonth = _coerce_int(rec.get("monthNumber"))
            bymonthday = _coerce_int(rec.get("dayOfMonth"))
            if bymonth is None or bymonthday is None:
                return None
            kwargs["bymonth"] = bymonth
            kwargs["bymonthday"] = bymonthday
            return rrule(YEARLY, **kwargs)
        if r_type == "relative yearly":
            bymonth = _coerce_int(rec.get("monthNumber"))
            pos = _ordinal_to_position(rec.get("ordinal"))
            active = _weekdays_with_position(rec.get("daysOfWeek") or {}, pos)
            if bymonth is None or not active:
                return None
            kwargs["bymonth"] = bymonth
            kwargs["byweekday"] = active
            return rrule(YEARLY, **kwargs)
    except (ValueError, TypeError):
        return None
    return None


def _active_weekdays(days: dict) -> list[weekday]:
    return [weekday(_WEEKDAY_INDEX[name]) for name in _WEEKDAY_ORDER if days.get(name)]


def _weekdays_with_position(days: dict, pos: int) -> list[weekday]:
    return [weekday(_WEEKDAY_INDEX[name], pos) for name in _WEEKDAY_ORDER if days.get(name)]


def _ordinal_to_position(ordinal) -> int:
    """Outlook exposes 1..5; 5 conventionally means 'last'."""
    n = _coerce_int(ordinal, default=1) or 1
    return -1 if n >= 5 else n


def _coerce_int(value, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _translate(raw: dict, known_keys: set[str]) -> Iterator[ActivityEvent]:
    start = _normalise_ts(raw.get("start", ""))
    end_dt = _parse(raw.get("end", ""))
    start_dt = _parse(raw.get("start", ""))
    if not start or not start_dt:
        return
    duration = None
    if end_dt and start_dt:
        duration = max(0, int((end_dt - start_dt).total_seconds()))

    subject = raw.get("subject", "") or ""
    location = raw.get("location", "") or ""
    content = raw.get("contentPreview", "") or ""
    attendees = raw.get("attendees", []) or []
    attendee_summary = ", ".join(
        a.get("name") or a.get("email") or "" for a in attendees if a
    )[:200]

    ticket = infer_ticket_key(f"{subject} {location} {content}", known_keys)

    is_teams = (
        "teams.microsoft.com" in location.lower()
        or "teams.microsoft.com" in content.lower()
        or "microsoft teams meeting" in location.lower()
    )
    event_type = "teams_meeting" if is_teams else "meeting"

    yield ActivityEvent(
        event_id=f"outlook:{raw.get('id', '')}",
        timestamp=start,
        source="outlook",
        event_type=event_type,
        actor="self",
        summary=f"Meeting: {subject[:140]}" + (f" ({attendee_summary})" if attendee_summary else ""),
        ticket_key=ticket,
        url=None,
        duration_seconds=duration,
        detail={
            "subject": subject,
            "calendar": raw.get("calendar", ""),
            "location": location,
            "start": start,
            "end": _normalise_ts(raw.get("end", "")),
            "all_day": bool(raw.get("allDay", False)),
            "attendees": attendees,
            "is_teams": is_teams,
            "content_preview": content,
        },
    )


def _parse(ts) -> datetime | None:
    if not ts:
        return None
    if isinstance(ts, datetime):
        dt = ts
    else:
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalise_ts(ts: str) -> str:
    dt = _parse(ts)
    if not dt:
        return ts
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

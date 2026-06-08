"""JSONL store for activity events.

One file per UTC date at ``{workspace}/.activity/YYYY-MM-DD.jsonl``.
Append-only with event_id-based dedup. Crash-safe via atomic rewrite on
append (read existing → union with new → atomic_write).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import yaml

from duct.markdown import atomic_write
from duct.models import ActivityEvent

_DIR_NAME = ".activity"
_STATE_FILENAME = ".state.yaml"

_EVENT_FIELDS = {f.name for f in fields(ActivityEvent)}


def activity_dir(root: Path) -> Path:
    return root / _DIR_NAME


def _day_path(root: Path, day: date) -> Path:
    return activity_dir(root) / f"{day.isoformat()}.jsonl"


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting a trailing ``Z``."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def _event_day(event: ActivityEvent) -> date:
    try:
        return _parse_iso(event.timestamp).date()
    except ValueError:
        # Fall back to today so malformed timestamps don't silently vanish.
        return datetime.now(timezone.utc).date()


def _read_day(path: Path) -> list[ActivityEvent]:
    if not path.exists():
        return []
    events: list[ActivityEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Drop unexpected keys so older payloads still deserialise when we
        # add fields later.
        kwargs = {k: v for k, v in raw.items() if k in _EVENT_FIELDS}
        try:
            events.append(ActivityEvent(**kwargs))
        except TypeError:
            continue
    return events


def _write_day(path: Path, events: Iterable[ActivityEvent]) -> None:
    lines = [json.dumps(asdict(e), sort_keys=True) for e in events]
    atomic_write(path, "\n".join(lines) + ("\n" if lines else ""))


def append_events(root: Path, events: Iterable[ActivityEvent]) -> int:
    """Append *events* to their per-day JSONL files, dedup by event_id.

    Returns the number of *new* events written (events whose event_id was
    not already present in the target day-file).
    """
    by_day: dict[date, list[ActivityEvent]] = {}
    for event in events:
        by_day.setdefault(_event_day(event), []).append(event)

    new_count = 0
    for day, day_events in by_day.items():
        path = _day_path(root, day)
        existing = _read_day(path)
        existing_ids = {e.event_id for e in existing}
        # Preserve insertion order, which roughly mirrors arrival order; the
        # reporter sorts on timestamp anyway.
        merged = list(existing)
        for e in day_events:
            if e.event_id in existing_ids:
                continue
            existing_ids.add(e.event_id)
            merged.append(e)
            new_count += 1
        if new_count:
            _write_day(path, merged)
        else:
            # Still create a zero-length file on first call so downstream
            # tooling sees a consistent directory layout.
            if not path.exists():
                _write_day(path, merged)
    return new_count


def iter_events(
    root: Path,
    since: datetime,
    until: datetime,
) -> Iterator[ActivityEvent]:
    """Yield events whose timestamp falls in ``[since, until)``, sorted ascending.

    Walks the per-day JSONL files covering the window (inclusive on both
    dates to capture any day the range touches).
    """
    start_day = since.astimezone(timezone.utc).date()
    end_day = until.astimezone(timezone.utc).date()

    collected: list[ActivityEvent] = []
    current = start_day
    while current <= end_day:
        for event in _read_day(_day_path(root, current)):
            try:
                ts = _parse_iso(event.timestamp)
            except ValueError:
                continue
            if since <= ts < until:
                collected.append(event)
        current += timedelta(days=1)
    collected.sort(key=lambda e: e.timestamp)
    yield from collected


# ---------------------------------------------------------------------------
# Per-provider state (for "since last run" defaulting).
# ---------------------------------------------------------------------------


def _state_path(root: Path) -> Path:
    return activity_dir(root) / _STATE_FILENAME


def load_state(root: Path) -> dict[str, str]:
    """Return per-provider ``gathered_through`` ISO timestamps."""
    path = _state_path(root)
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    providers = raw.get("providers", {})
    return {str(k): str(v) for k, v in providers.items() if v}


def save_state(root: Path, providers: dict[str, str]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump({"providers": providers}, default_flow_style=False, sort_keys=True),
        encoding="utf-8",
    )

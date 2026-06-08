"""Lightweight always-on timing instrumentation.

Records spans into ``~/.duct/perf.jsonl`` as one JSON line per span. The
log is capped at ``_MAX_ROWS`` and rolled in place when the cap is hit.

Usage:

    from duct import perf

    with perf.Timer("load.tickets", n=len(tickets)):
        ...

    duration_ms = perf.record("custom_op", 12.3, source="github")

    for entry in perf.recent("load.tickets", limit=20):
        print(entry["ms"])

The module is deliberately dependency-free and best-effort: a failure to
write the log never raises into the caller.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_LOG_PATH = Path.home() / ".duct" / "perf.jsonl"
_MAX_ROWS = 5000
_ROLL_TARGET = 4000  # keep this many when rolling
_lock = threading.Lock()
_disabled = threading.local()


def _is_disabled() -> bool:
    return getattr(_disabled, "value", False)


@contextmanager
def disabled() -> Iterator[None]:
    """Suppress writes to the perf log within this thread (used by tests)."""
    prev = getattr(_disabled, "value", False)
    _disabled.value = True
    try:
        yield
    finally:
        _disabled.value = prev


def record(name: str, duration_ms: float, **metadata: Any) -> None:
    """Append a single span to the perf log."""
    if _is_disabled():
        return
    entry = {"ts": time.time(), "name": name, "ms": round(duration_ms, 3)}
    if metadata:
        entry["meta"] = metadata
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with _LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str))
                f.write("\n")
            _maybe_roll()
    except Exception:
        pass


@contextmanager
def Timer(name: str, **metadata: Any) -> Iterator[dict]:
    """Context manager that records the wall-clock duration of the block.

    The yielded dict is mutable — callers can attach extra metadata that
    will be merged into the recorded entry on exit.
    """
    extras: dict[str, Any] = {}
    start = time.monotonic()
    try:
        yield extras
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000.0
        record(name, elapsed_ms, **{**metadata, **extras})


def recent(name: str | None = None, limit: int = 200) -> list[dict]:
    """Return the most recent entries, newest first. Filters by name if given."""
    if not _LOG_PATH.exists():
        return []
    try:
        lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if name is not None and entry.get("name") != name:
            continue
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def summarise(entries: Iterable[dict]) -> list[dict]:
    """Aggregate entries by ``name`` returning count, p50, p95, max, sum."""
    buckets: dict[str, list[float]] = {}
    for e in entries:
        name = e.get("name")
        ms = e.get("ms")
        if not isinstance(name, str) or not isinstance(ms, (int, float)):
            continue
        buckets.setdefault(name, []).append(float(ms))
    summary = []
    for name, values in buckets.items():
        values.sort()
        n = len(values)
        summary.append({
            "name": name,
            "count": n,
            "p50": values[n // 2],
            "p95": values[min(n - 1, max(0, int(n * 0.95)))],
            "max": values[-1],
            "sum": sum(values),
        })
    summary.sort(key=lambda s: s["sum"], reverse=True)
    return summary


def clear() -> None:
    """Delete the log file. Used by tests."""
    try:
        _LOG_PATH.unlink()
    except FileNotFoundError:
        pass


def _maybe_roll() -> None:
    """Trim the log when it grows past ``_MAX_ROWS``."""
    try:
        size_check = _LOG_PATH.stat().st_size
    except OSError:
        return
    # Cheap pre-check: if the file is plausibly under the row cap, skip.
    if size_check < _MAX_ROWS * 200:
        return
    try:
        lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= _MAX_ROWS:
        return
    keep = lines[-_ROLL_TARGET:]
    tmp = _LOG_PATH.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
    os.replace(tmp, _LOG_PATH)

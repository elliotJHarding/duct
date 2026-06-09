"""Sync framework: SyncSource protocol and SyncCoordinator."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

import yaml

from duct import paths
from duct.models import SyncResult


@runtime_checkable
class SyncSource(Protocol):
    """Protocol that all sync sources implement."""

    @property
    def name(self) -> str:
        """Unique identifier for this source (e.g. 'jira', 'github')."""
        ...

    def sync(self, root: Path) -> SyncResult:
        """Run a sync cycle, writing snapshot files under root."""
        ...


@dataclass(frozen=True)
class SourceStatus:
    """Staleness info for a single sync source."""

    name: str
    last_sync: float  # epoch timestamp, 0 = never
    interval: int  # seconds
    is_stale: bool

    @property
    def last_sync_iso(self) -> str:
        if self.last_sync == 0:
            return "never"
        return datetime.fromtimestamp(self.last_sync, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    @property
    def age_seconds(self) -> float:
        if self.last_sync == 0:
            return float("inf")
        return time.time() - self.last_sync

    @property
    def age_human(self) -> str:
        if self.last_sync == 0:
            return "never"
        age = self.age_seconds
        if age < 60:
            return f"{int(age)}s ago"
        if age < 3600:
            return f"{int(age / 60)}m ago"
        return f"{age / 3600:.1f}h ago"


class SyncCoordinator:
    """Tracks staleness per source and orchestrates sync runs."""

    def __init__(self, root: Path, intervals: dict[str, int] | None = None):
        self._root = root
        self._intervals = intervals or {}
        self._state_path = paths.sync_state_file(root)

    def _load_state(self) -> dict[str, float]:
        if self._state_path.exists():
            raw = yaml.safe_load(self._state_path.read_text()) or {}
            return {k: float(v) for k, v in raw.items()}
        return {}

    def _save_state(self, state: dict[str, float]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(yaml.dump(state, default_flow_style=False))

    def is_stale(self, source_name: str) -> bool:
        state = self._load_state()
        last_sync = state.get(source_name, 0.0)
        interval = self._intervals.get(source_name, 0)
        return (time.time() - last_sync) >= interval

    def source_status(self, source_name: str) -> SourceStatus:
        """Return staleness info for a single source."""
        state = self._load_state()
        last_sync = state.get(source_name, 0.0)
        interval = self._intervals.get(source_name, 0)
        stale = (time.time() - last_sync) >= interval
        return SourceStatus(
            name=source_name, last_sync=last_sync, interval=interval, is_stale=stale
        )

    def all_source_statuses(self) -> list[SourceStatus]:
        """Return staleness info for all configured sources."""
        return [self.source_status(name) for name in sorted(self._intervals)]

    def run(
        self,
        sources: list[SyncSource],
        force: bool = False,
        on_result: Callable[[SyncResult], None] | None = None,
        on_start: Callable[[str], None] | None = None,
    ) -> list[SyncResult]:
        results = []
        state = self._load_state()
        for source in sources:
            if not force and not self.is_stale(source.name):
                continue
            if on_start:
                on_start(source.name)
            started = time.time()
            try:
                result = source.sync(self._root)
            except Exception as exc:
                # A raising source must not advance its timestamp; surface the
                # failure as a SyncResult so callers can report it alongside
                # other sources instead of aborting the whole run.
                result = SyncResult(
                    source=source.name,
                    tickets_synced=0,
                    duration_seconds=time.time() - started,
                    errors=[f"{type(exc).__name__}: {exc}"],
                )
            results.append(result)
            if not result.errors:
                state[source.name] = time.time()
            if on_result:
                on_result(result)
        self._save_state(state)
        return results

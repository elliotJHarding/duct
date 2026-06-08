"""ActivityCoordinator: orchestrates providers and persists events to the store."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from duct.activity.base import ActivityProvider, ProviderResult
from duct.activity.store import append_events, load_state, save_state
from duct.config import WorkspaceConfig
from duct.models import ActivityEvent


class ActivityCoordinator:
    """Runs a set of providers over a time window, appends events to the store."""

    def __init__(self, root: Path, cfg: WorkspaceConfig):
        self._root = root
        self._cfg = cfg

    def gather(
        self,
        providers: list[ActivityProvider],
        since: datetime,
        until: datetime,
        on_start: Callable[[str], None] | None = None,
        on_result: Callable[[ProviderResult], None] | None = None,
    ) -> list[ProviderResult]:
        """Fetch from each provider and append new events to the store."""
        state = load_state(self._root)
        results: list[ProviderResult] = []

        for provider in providers:
            if on_start:
                on_start(provider.name)
            started = time.time()
            errors: list[str] = []
            fetched: list[ActivityEvent] = []
            try:
                for event in provider.fetch(since, until, self._cfg):
                    fetched.append(event)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

            new_count = 0
            if fetched:
                try:
                    new_count = append_events(self._root, fetched)
                except Exception as exc:
                    errors.append(f"append failed: {type(exc).__name__}: {exc}")

            result = ProviderResult(
                name=provider.name,
                events_fetched=len(fetched),
                events_new=new_count,
                duration_seconds=time.time() - started,
                errors=errors,
            )
            results.append(result)

            if not errors:
                # Advance the high-water mark so the next `gather` without
                # --since skips work we've already captured. Writers should
                # still provide a small overlap window on re-runs.
                state[provider.name] = _iso(until)

            if on_result:
                on_result(result)

        save_state(self._root, state)
        return results

    def default_since(self, provider_names: list[str], overlap_seconds: int = 3600) -> datetime:
        """Pick a sensible default ``since`` from stored per-provider state.

        Takes the minimum ``gathered_through`` across requested providers so
        we refetch any gap where one provider lagged; subtracts *overlap* to
        catch late-arriving events near the previous boundary. Falls back to
        24h ago when no state exists yet.
        """
        state = load_state(self._root)
        per_provider: list[datetime] = []
        for name in provider_names:
            raw = state.get(name)
            if raw:
                try:
                    per_provider.append(_parse_iso(raw))
                except ValueError:
                    continue
        if not per_provider:
            return datetime.now(timezone.utc).replace(microsecond=0) - _hours(24)
        floor = min(per_provider)
        return floor - _hours(overlap_seconds / 3600)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def _hours(h: float):
    from datetime import timedelta

    return timedelta(hours=h)

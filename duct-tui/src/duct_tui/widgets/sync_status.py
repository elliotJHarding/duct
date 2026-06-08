"""SyncStatusBar -- displays per-source last-sync ages in the top bar."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.table import Table
from textual.app import RenderResult
from textual.reactive import reactive
from textual.widget import Widget

from duct.models import SourceStatus
from duct_tui.icons import Icons, get_icons


# Render order matches the flow of a typical sync cycle: upstream tickets
# first, then local workspace state, then sessions and CI derived from them.
_SOURCE_ORDER = ("jira", "github", "workspace", "sessions", "ci")


def _format_age(iso: str | None) -> str:
    """Compact relative age from an ISO 8601 UTC timestamp."""
    if not iso:
        return "—"
    try:
        # Accept both with and without trailing Z
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age < 0:
        return "0s"
    if age < 60:
        return f"{int(age)}s"
    if age < 3600:
        return f"{int(age / 60)}m"
    if age < 86400:
        return f"{int(age / 3600)}h"
    return f"{int(age / 86400)}d"


def _source_icon(icons: Icons, name: str) -> str:
    return getattr(icons, f"source_{name}", name[:1].upper())


# A heartbeat older than this means the daemon isn't ticking (it writes one
# every session-poll interval, default 4s). Generous to absorb a slow tick.
_DAEMON_STALE_SECONDS = 90


class SyncStatusBar(Widget):
    statuses: reactive[list[SourceStatus]] = reactive(list, layout=False)
    syncing: reactive[frozenset[str]] = reactive(frozenset, layout=False)
    # Seconds since the daemon's last heartbeat; None = no daemon detected.
    daemon_age: reactive[float | None] = reactive(None, layout=False)

    def render(self) -> RenderResult:
        icons: Icons = getattr(self.app, "icons", get_icons())
        by_name = {s.name: s for s in self.statuses}
        parts: list[str] = []
        for name in _SOURCE_ORDER:
            icon = _source_icon(icons, name)
            if name in self.syncing:
                age = "syncing…"
            else:
                status = by_name.get(name)
                age = _format_age(status.last_synced) if status else "—"
            parts.append(f"{icon} {age}")
        parts.append(self._daemon_segment())
        grid = Table.grid(expand=True, padding=0)
        grid.add_column(justify="left", no_wrap=True)
        grid.add_column(justify="right", no_wrap=True)
        grid.add_row("duct", "  ".join(parts))
        return grid

    def _daemon_segment(self) -> str:
        age = self.daemon_age
        if age is None or age > _DAEMON_STALE_SECONDS:
            return "[dim]⏻ daemon off[/dim]"
        return "[green]⏻ daemon[/green]"

    def update_statuses(
        self,
        statuses: list[SourceStatus],
        syncing: frozenset[str] = frozenset(),
    ) -> None:
        self.statuses = list(statuses)
        self.syncing = syncing

    def update_daemon_status(self, age_seconds: float | None) -> None:
        self.daemon_age = age_seconds

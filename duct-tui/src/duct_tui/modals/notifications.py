"""Notification feed overlay — tails the daemon's .duct/notifications.jsonl."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Static

from duct.notifications import feed_path

_MAX_ENTRIES = 40

_KIND_LABEL = {
    "done": "done",
    "waiting": "waiting",
    "pending-action": "action",
    "orchestrator": "orchestrator",
}


def _format_age(iso: str) -> str:
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age < 60:
        return f"{int(age)}s ago"
    if age < 3600:
        return f"{int(age / 60)}m ago"
    if age < 86400:
        return f"{int(age / 3600)}h ago"
    return f"{int(age / 86400)}d ago"


def _read_feed(root) -> list[dict]:
    path = feed_path(root)
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return []
    entries: list[dict] = []
    for line in lines[-_MAX_ENTRIES:]:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    entries.reverse()  # newest first
    return entries


def _render(root) -> str:
    entries = _read_feed(root)
    if not entries:
        return (
            "[bold]Notifications[/bold]\n\n"
            "[dim]No notifications yet.\n"
            "The duct daemon records them here — install it with "
            "`duct daemon install`.[/dim]"
        )
    rows = ["[bold]Notifications[/bold]\n"]
    for e in entries:
        kind = _KIND_LABEL.get(e.get("kind", ""), e.get("kind", ""))
        age = _format_age(e.get("at", ""))
        title = e.get("title", "")
        body = e.get("body", "")
        rows.append(f"[dim]{age:>8}[/dim]  [cyan]{kind:<12}[/cyan] [bold]{title}[/bold]")
        if body:
            rows.append(f"          [dim]{body}[/dim]")
    return "\n".join(rows)


class NotificationsModal(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_feed", "Close"),
        Binding("N", "dismiss_feed", "Close"),
    ]

    def compose(self) -> ComposeResult:
        root = self.app.data.root
        yield Static(_render(root), id="notifications-content")

    def action_dismiss_feed(self) -> None:
        self.dismiss(None)

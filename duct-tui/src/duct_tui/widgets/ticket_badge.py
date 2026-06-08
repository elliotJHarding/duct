"""Shared ticket-key badge rendering.

A single canonical style for ticket keys across the TUI so they read as a
distinct, clickable-looking identifier wherever they appear — session cards,
ticket cards, PR lists, etc.
"""

from __future__ import annotations

from rich.text import Text


# Soft reverse-style badge: bright fg on a muted mid-grey background, with
# single-space padding either side so the key reads as a pill.
_BADGE_STYLE = "bold bright_white on #3a3a3a"


def render_ticket_badge(key: str) -> Text:
    """Return the canonical ticket-key badge as a Rich `Text`.

    Single-space padding is baked in; callers don't need to add their own.
    """
    return Text(f" {key} ", style=_BADGE_STYLE)

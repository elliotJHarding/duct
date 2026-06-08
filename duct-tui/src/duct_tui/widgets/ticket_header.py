"""TicketHeader -- ticket identity bar."""

from __future__ import annotations

from textual.widget import Widget
from textual.app import RenderResult

from duct.models import Ticket


class TicketHeader(Widget):
    def __init__(self, ticket: Ticket, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ticket = ticket

    def render(self) -> RenderResult:
        t = self._ticket
        parts = [f"{t.key}: {t.summary}"]
        meta = []
        if t.status:
            meta.append(t.status)
        if t.priority:
            meta.append(f"Priority: {t.priority}")
        if t.issue_type:
            meta.append(t.issue_type)
        if meta:
            parts.append("  ".join(meta))
        return "\n".join(parts)

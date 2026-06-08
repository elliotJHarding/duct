"""TicketFilterBar — filter mode selector for overview."""

from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.app import RenderResult

from rich.text import Text


_MODES = ["focus", "all", "closed"]
_MODE_LABELS = {"focus": "Focused", "all": "All", "closed": "Closed"}


class TicketFilterBar(Widget):

    mode = reactive("focus")

    class FilterChanged(Message):
        def __init__(self, mode: str) -> None:
            super().__init__()
            self.mode = mode

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._counts: dict[str, int] = {}

    def set_counts(self, counts: dict[str, int]) -> None:
        self._counts = counts
        self.refresh()

    def render(self) -> RenderResult:
        t = Text()
        t.append("  ")
        for i, m in enumerate(_MODES):
            if i > 0:
                t.append("  |  ", style="dim")
            label = _MODE_LABELS[m]
            count = self._counts.get(m, "")
            count_str = f" ({count})" if count != "" else ""
            if m == self.mode:
                t.append(f"{label}{count_str}", style="bold")
            else:
                t.append(f"{label}{count_str}", style="dim")
        return t

    def action_cycle_filter(self) -> None:
        idx = _MODES.index(self.mode)
        self.mode = _MODES[(idx + 1) % len(_MODES)]
        self.post_message(self.FilterChanged(self.mode))

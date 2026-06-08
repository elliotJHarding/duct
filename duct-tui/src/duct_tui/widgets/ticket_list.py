"""TicketList -- ticket list for overview (sorted by status group then activity)."""

from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from duct.models import TicketSummary
from duct_tui.widgets.vim_mixin import VimListMixin


class TicketList(VimListMixin, OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("enter", "select", "Open"),
        *VimListMixin.VIM_BINDINGS,
    ]

    class TicketSelected(Message):
        def __init__(self, ticket_key: str) -> None:
            super().__init__()
            self.ticket_key = ticket_key

    def __init__(self, **kwargs) -> None:
        super().__init__(id=kwargs.pop("id", "ticket-list"), **kwargs)
        self._ticket_keys: list[str] = []
        self.border_title = "Tickets"

    def _vim_goto_first(self) -> None:
        if self.option_count:
            self.highlighted = 0

    def _vim_goto_last(self) -> None:
        if self.option_count:
            self.highlighted = self.option_count - 1

    def update_tickets(self, tickets: list[TicketSummary]) -> None:
        self.clear_options()
        self._ticket_keys = []
        for t in tickets:
            session_indicator = f"● {t.active_sessions}" if t.active_sessions else "○ 0"
            ci = ""
            if t.ci_status == "passing":
                ci = " CI:✓"
            elif t.ci_status == "failing":
                ci = " CI:✗"

            label = (
                f"[{t.key}]  {t.summary[:40]}\n"
                f"  {t.status}  PR:{t.pr_count}{ci}  {session_indicator} sessions"
            )
            if t.pending_action_count:
                label += f"\n  ⚠ {t.pending_action_count} pending action{'s' if t.pending_action_count > 1 else ''}"

            self.add_option(Option(label, id=t.key))
            self._ticket_keys.append(t.key)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        idx = self.highlighted
        if idx is not None and idx < len(self._ticket_keys):
            self.post_message(self.TicketSelected(self._ticket_keys[idx]))

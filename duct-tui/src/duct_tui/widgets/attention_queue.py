"""AttentionQueue -- items needing human action."""

from __future__ import annotations

from textual.binding import Binding
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from duct.models import SessionInfo, TicketSummary


class AttentionQueue(OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("enter", "select", "Open"),
    ]

    class ItemSelected(Message):
        def __init__(self, ticket_key: str) -> None:
            super().__init__()
            self.ticket_key = ticket_key

    def __init__(self, **kwargs) -> None:
        super().__init__(id=kwargs.pop("id", "attention-queue"), **kwargs)
        self._item_keys: list[str] = []
        self.border_title = "Attention"

    def update_from_state(
        self, tickets: list[TicketSummary], sessions: list[SessionInfo]
    ) -> None:
        self.clear_options()
        self._item_keys = []

        # Tickets with pending actions
        for t in tickets:
            if t.pending_action_count:
                label = f"⚠ {t.key}: {t.pending_action_count} action{'s' if t.pending_action_count > 1 else ''} pending"
                self.add_option(Option(label, id=f"action-{t.key}"))
                self._item_keys.append(t.key)

        # Tickets with failing CI
        for t in tickets:
            if t.ci_status == "failing":
                label = f"⚠ {t.key}: CI failing"
                self.add_option(Option(label, id=f"ci-{t.key}"))
                self._item_keys.append(t.key)

        # Sessions waiting for human input
        for s in sessions:
            if s.status == "waiting" and s.ticket_key:
                label = f"⚠ {s.ticket_key}: Session waiting for input"
                self.add_option(Option(label, id=f"wait-{s.session_id}"))
                self._item_keys.append(s.ticket_key)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        idx = self.highlighted
        if idx is not None and idx < len(self._item_keys):
            self.post_message(self.ItemSelected(self._item_keys[idx]))

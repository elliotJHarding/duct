"""ActionPanel -- pending actions with approve/reject-with-feedback."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from duct.models import Action
from duct_tui.icons import Icons, UNICODE
from duct_tui.widgets.action_render import render_action_row, render_section_header
from duct_tui.widgets.vim_mixin import VimListMixin


class _ActionList(VimListMixin, OptionList):
    """Inner option list holding the action rows."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("y", "approve", "Approve"),
        Binding("n", "reject", "Reject"),
        *VimListMixin.VIM_BINDINGS,
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._actions: list[Action] = []
        self._entries: list[tuple[str, Action] | None] = []
        self._sep_seq: int = 0
        self._skip_direction: int = 1
        self._icons: Icons = UNICODE

    def on_mount(self) -> None:
        self._icons = getattr(self.app, "icons", UNICODE)

    def update_actions(self, actions: list[Action], ticket_key: str) -> None:
        self._actions = actions
        self._entries = []
        self._sep_seq = 0
        self.clear_options()

        icons = self._icons
        pending = [a for a in actions if a.status == "pending"]
        resolved = [a for a in actions if a.status != "pending"]

        if not pending and not resolved:
            return

        if pending:
            self._add_section_header("Pending", len(pending))
            self._add_separator()
            for i, a in enumerate(pending):
                if i > 0:
                    self._add_separator()
                text = render_action_row(a, icons)
                self.add_option(Option(text, id=a.id))
                self._entries.append((ticket_key, a))

        if resolved:
            if pending:
                self._add_separator()
            self._add_section_header("Resolved", len(resolved))
            self._add_separator()
            for i, a in enumerate(resolved):
                if i > 0:
                    self._add_separator()
                text = render_action_row(a, icons)
                self.add_option(Option(text, id=a.id, disabled=True))
                self._entries.append(None)

    def _add_section_header(self, label: str, count: int) -> None:
        self._sep_seq += 1
        self.add_option(
            Option(render_section_header(label, count),
                   id=f"header-{self._sep_seq}", disabled=True),
        )
        self._entries.append(None)

    def _add_separator(self) -> None:
        from rich.text import Text
        self._sep_seq += 1
        self.add_option(Option(Text(""), id=f"sep-{self._sep_seq}", disabled=True))
        self._entries.append(None)

    # ------------------------------------------------------------------ nav

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted,
    ) -> None:
        if event.option is None:
            return
        option_id = event.option.id or ""
        if not (option_id.startswith("header-") or option_id.startswith("sep-")):
            return
        idx = self.highlighted
        if idx is None:
            return
        target = idx + self._skip_direction
        if target < 0 or target >= self.option_count:
            return
        self.highlighted = target

    def action_cursor_down(self) -> None:
        self._skip_direction = 1
        super().action_cursor_down()

    def action_cursor_up(self) -> None:
        self._skip_direction = -1
        super().action_cursor_up()

    def _vim_goto_first(self) -> None:
        if self.option_count:
            self._skip_direction = 1
            self.highlighted = 0

    def _vim_goto_last(self) -> None:
        if self.option_count:
            self._skip_direction = -1
            self.highlighted = self.option_count - 1

    # ------------------------------------------------------------------ resolve

    def _get_selected_entry(self) -> tuple[str, Action] | None:
        idx = self.highlighted
        if idx is None or idx >= len(self._entries):
            return None
        return self._entries[idx]

    def action_approve(self) -> None:
        entry = self._get_selected_entry()
        if entry:
            self.post_message(ActionPanel._Approve(entry[1].id))

    def action_reject(self) -> None:
        entry = self._get_selected_entry()
        if entry:
            self.post_message(ActionPanel._StartReject(entry[1].id))


class ActionPanel(Widget):
    """Per-ticket action panel: option list plus an inline reject-feedback input."""

    class ActionResolved(Message):
        def __init__(
            self,
            action_id: str,
            approved: bool,
            feedback: str | None = None,
        ) -> None:
            super().__init__()
            self.action_id = action_id
            self.approved = approved
            self.feedback = feedback

    class _Approve(Message):
        def __init__(self, action_id: str) -> None:
            super().__init__()
            self.action_id = action_id

    class _StartReject(Message):
        """Inner: list bubbles this up to open the feedback composer."""
        def __init__(self, action_id: str) -> None:
            super().__init__()
            self.action_id = action_id

    def __init__(self, ticket_key: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ticket_key = ticket_key
        self._rejecting: str | None = None
        self.border_title = "Actions"

    def compose(self) -> ComposeResult:
        yield _ActionList(id="action-list")
        with Vertical(id="action-feedback-area", classes="hidden"):
            yield Input(placeholder="Reason for rejecting (Enter to skip)...",
                        id="action-feedback-input")
            yield Static("[dim]Enter[/] reject  [dim]Escape[/] cancel",
                         id="action-feedback-hint")
        yield Static(
            r"[dim]\[y] approve  \[n] reject  \[j/k] move[/dim]",
            id="action-footer-hint",
        )

    def update_actions(self, actions: list[Action]) -> None:
        try:
            self.query_one("#action-list", _ActionList).update_actions(actions, self._ticket_key)
        except Exception:
            pass

    def on_action_panel__approve(self, event: _Approve) -> None:
        event.stop()
        self.post_message(self.ActionResolved(event.action_id, approved=True))

    def on_action_panel__start_reject(self, event: _StartReject) -> None:
        event.stop()
        self._rejecting = event.action_id
        inp = self.query_one("#action-feedback-input", Input)
        inp.value = ""
        self.query_one("#action-list").display = False
        self.query_one("#action-feedback-area").remove_class("hidden")
        inp.focus()

    def _close_input(self) -> None:
        self._rejecting = None
        self.query_one("#action-feedback-area").add_class("hidden")
        self.query_one("#action-list").display = True
        self.query_one("#action-list").focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "action-feedback-input" or self._rejecting is None:
            return
        event.stop()
        feedback = event.value.strip() or None
        action_id = self._rejecting
        self._close_input()
        self.post_message(self.ActionResolved(action_id, approved=False, feedback=feedback))

    def key_escape(self) -> None:
        if self._rejecting is not None:
            self._close_input()

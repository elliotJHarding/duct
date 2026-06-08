"""Generic confirmation modal."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [
        ("y", "confirm", "Yes"),
        ("n", "deny", "No"),
        ("escape", "deny", "Cancel"),
    ]

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._message = message

    def compose(self) -> ComposeResult:
        yield Label(self._message, id="confirm-message")
        with Horizontal(id="confirm-buttons"):
            yield Button("Yes", variant="primary", id="yes-btn")
            yield Button("No", id="no-btn")

    @on(Button.Pressed, "#yes-btn")
    def handle_yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no-btn")
    def handle_no(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)

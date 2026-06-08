"""Launch session modal."""

from __future__ import annotations

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select


@dataclass
class LaunchConfig:
    ticket_key: str
    repo: str | None
    prompt: str | None


class LaunchSessionModal(ModalScreen[LaunchConfig | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, ticket_key: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ticket_key = ticket_key

    def compose(self) -> ComposeResult:
        with Vertical(id="launch-modal"):
            yield Label(f"Launch Session -- {self._ticket_key}", id="modal-title")
            yield Label("Repo:")
            yield Select(options=self._get_repo_options(), id="repo-select", allow_blank=True)
            yield Label("Prompt (optional):")
            yield Input(placeholder="Enter prompt...", id="prompt-input")
            with Vertical(id="modal-buttons"):
                yield Button("Launch", variant="primary", id="launch-btn")
                yield Button("Cancel", id="cancel-btn")

    def _get_repo_options(self) -> list[tuple[str, str]]:
        try:
            detail = self.app.data.load_ticket_detail(self._ticket_key)
            if detail:
                return [(r.name, r.name) for r in detail.repos]
        except Exception:
            pass
        return []

    @on(Button.Pressed, "#launch-btn")
    def handle_launch(self) -> None:
        repo_select = self.query_one("#repo-select", Select)
        prompt_input = self.query_one("#prompt-input", Input)
        repo = None if repo_select.is_blank() else str(repo_select.value)
        prompt = prompt_input.value or None
        self.dismiss(LaunchConfig(self._ticket_key, repo, prompt))

    @on(Button.Pressed, "#cancel-btn")
    def handle_cancel(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

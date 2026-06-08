"""Launch agent modal."""

from __future__ import annotations

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select


@dataclass
class LaunchAgentConfig:
    ticket_key: str
    agent_name: str
    repo: str | None


class LaunchAgentModal(ModalScreen[LaunchAgentConfig | None]):
    """Pick an agent and optional repo to launch a session with the agent body."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, ticket_key: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ticket_key = ticket_key

    def compose(self) -> ComposeResult:
        with Vertical(id="launch-modal"):
            yield Label(f"Launch Agent -- {self._ticket_key}", id="modal-title")
            yield Label("Agent:")
            yield Select(
                options=self._get_agent_options(),
                id="agent-select",
                allow_blank=True,
                prompt="Choose an agent",
            )
            yield Label("Repo (optional):")
            yield Select(
                options=self._get_repo_options(),
                id="repo-select",
                allow_blank=True,
            )
            with Vertical(id="modal-buttons"):
                yield Button("Launch", variant="primary", id="launch-btn")
                yield Button("Cancel", id="cancel-btn")

    def _get_agent_options(self) -> list[tuple[str, str]]:
        try:
            agents = self.app.data.list_agents()
            return [
                (f"{a.name} — {a.description}" if a.description else a.name, a.name)
                for a in agents
            ]
        except Exception:
            return []

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
        agent_select = self.query_one("#agent-select", Select)
        repo_select = self.query_one("#repo-select", Select)
        if agent_select.is_blank():
            self.app.notify("Pick an agent first", severity="warning")
            return
        repo = None if repo_select.is_blank() else str(repo_select.value)
        self.dismiss(LaunchAgentConfig(
            ticket_key=self._ticket_key,
            agent_name=str(agent_select.value),
            repo=repo,
        ))

    @on(Button.Pressed, "#cancel-btn")
    def handle_cancel(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

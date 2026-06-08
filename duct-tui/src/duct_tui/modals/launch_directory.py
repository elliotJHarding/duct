"""Launch session in an arbitrary directory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.widgets import Button, Input, Label


@dataclass
class LaunchDirectoryConfig:
    cwd: Path
    prompt: str | None


class DirSuggester(Suggester):
    """Suggest existing directories under the current input prefix."""

    def __init__(self) -> None:
        super().__init__(use_cache=False, case_sensitive=True)

    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None
        try:
            expanded = Path(value).expanduser()
            # Split into parent dir + prefix on the last path segment
            if value.endswith("/"):
                parent = expanded
                prefix = ""
            else:
                parent = expanded.parent
                prefix = expanded.name
            if not parent.is_dir():
                return None
            # Preserve the literal prefix the user typed (e.g. ~ vs resolved home)
            typed_parent = value if value.endswith("/") else value[: len(value) - len(prefix)]
            for child in sorted(parent.iterdir()):
                if child.is_dir() and child.name.startswith(prefix):
                    return f"{typed_parent}{child.name}/"
        except Exception:
            return None
        return None


class LaunchDirectoryModal(ModalScreen[LaunchDirectoryConfig | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, default_cwd: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._default_cwd = str(default_cwd)

    def compose(self) -> ComposeResult:
        with Vertical(id="launch-modal"):
            yield Label("Launch Session -- pick a directory", id="modal-title")
            yield Label("Directory:")
            yield Input(
                value=self._default_cwd,
                placeholder="~/workspace/...",
                suggester=DirSuggester(),
                id="cwd-input",
            )
            yield Label("Prompt (optional):")
            yield Input(placeholder="Enter prompt...", id="prompt-input")
            with Vertical(id="modal-buttons"):
                yield Button("Launch", variant="primary", id="launch-btn")
                yield Button("Cancel", id="cancel-btn")

    @on(Button.Pressed, "#launch-btn")
    def handle_launch(self) -> None:
        cwd_text = self.query_one("#cwd-input", Input).value.strip()
        prompt = self.query_one("#prompt-input", Input).value.strip() or None
        if not cwd_text:
            self.notify("Directory is required", severity="error")
            return
        cwd = Path(cwd_text).expanduser().resolve()
        if not cwd.is_dir():
            self.notify(f"Not a directory: {cwd}", severity="error")
            return
        self.dismiss(LaunchDirectoryConfig(cwd, prompt))

    @on(Button.Pressed, "#cancel-btn")
    def handle_cancel(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

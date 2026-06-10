"""The setup wizard application shell.

Owns the phase sequence, the progress rail, and shared state (workspace
root + config). Phases post :class:`Phase.Advance` / :class:`Phase.GoBack`
and the app moves through the sequence; when duct is already configured
the app opens on a jump menu instead of the linear walk.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.theme import Theme
from textual.widgets import Footer, Static

from duct.cli import setup_core
from duct.cli.setup_wizard.base import Phase
from duct.config import WorkspaceConfig, load_config
from duct.global_state import load_state

_DUCT_ACCENT = "#bb9af7"


def _wizard_theme() -> Theme:
    return Theme(
        name="duct-setup",
        primary=_DUCT_ACCENT,
        secondary="#7aa2f7",
        accent=_DUCT_ACCENT,
        foreground="#c0caf5",
        background="#1a1b26",
        surface="#24283b",
        panel="#414868",
        success="#9ece6a",
        warning="#e0af68",
        error="#f7768e",
        dark=True,
    )


class ProgressRail(Static):
    """Left-hand list of phases with completion markers."""

    def render(self) -> str:
        app = self.app
        assert isinstance(app, SetupApp)
        lines: list[str] = []
        current_group = None
        for index, phase_cls in enumerate(app.phase_classes):
            if phase_cls.group != current_group:
                current_group = phase_cls.group
                if lines:
                    lines.append("")
                lines.append(f"[bold $text-muted]{current_group.upper()}[/]")
            label = phase_cls.rail_label or phase_cls.title
            if index == app.current_index:
                lines.append(f"[bold $primary]● {label}[/]")
            elif index in app.visited or phase_cls.is_complete(app):
                lines.append(f"[$success]✓[/] {label}")
            else:
                lines.append(f"[$text-muted]○ {label}[/]")
        return "\n".join(lines)


class SetupApp(App):
    """Full-screen guided setup + workflow tutorial."""

    TITLE = "duct setup"
    CSS_PATH = "wizard.tcss"
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("ctrl+s", "skip", "Skip phase"),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self) -> None:
        super().__init__()
        from duct.cli.setup_wizard.phases import SETUP_PHASES
        from duct.cli.setup_wizard.tutorial import TUTORIAL_PHASES

        self.phase_classes: list[type[Phase]] = [*SETUP_PHASES, *TUTORIAL_PHASES]
        self.current_index: int = 0
        self.visited: set[int] = set()
        self.jump_mode: bool = False
        # Pre-warmed (candidates, pick, branches) for the add-repo chapter.
        self.repo_preview: tuple | None = None
        self.root: Path | None = setup_core.workspace_root()
        self.cfg: WorkspaceConfig | None = (
            load_config(self.root) if self.root is not None else None
        )

    # ------------------------------------------------------------------
    # Shared state.
    # ------------------------------------------------------------------

    def refresh_state(self) -> None:
        """Re-read workspace root + config after a phase wrote something."""
        self.root = setup_core.workspace_root()
        self.cfg = load_config(self.root) if self.root is not None else None

    def warm_repo_cache(self) -> None:
        """Fetch the add-repo chapter's data in the background.

        A cold load takes many seconds (GitHub org listing + a git fetch
        for branches), so it starts as soon as the GitHub/repo-paths config
        lands and again at sync — by the time the chapter shows, it's warm.
        """
        cfg = self.cfg
        if cfg is None or self.repo_preview is not None:
            return

        def _warm() -> None:
            from duct.cli.setup_wizard.tutorial import load_repo_data

            try:
                data = load_repo_data(cfg)
            except Exception:
                return
            self.repo_preview = data

        self.run_worker(_warm, thread=True, exclusive=True, group="repo-warm")

    # ------------------------------------------------------------------
    # Composition.
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Horizontal(id="wizard-body"):
            with Vertical(id="rail-pane"):
                yield Static("duct", id="rail-brand")
                yield ProgressRail(id="rail")
            yield VerticalScroll(id="phase-pane")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(_wizard_theme())
        self.theme = "duct-setup"
        if setup_core.state_is_ready():
            self.show_menu()
        else:
            self.show_phase(self._first_incomplete_index())

    def _first_incomplete_index(self) -> int:
        for index, phase_cls in enumerate(self.phase_classes):
            if not phase_cls.is_complete(self):
                return index
        return 0

    # ------------------------------------------------------------------
    # Navigation.
    # ------------------------------------------------------------------

    async def _swap_phase(self, widget: Phase) -> None:
        pane = self.query_one("#phase-pane", VerticalScroll)
        await pane.remove_children()
        await pane.mount(widget)
        pane.scroll_home(animate=False)
        widget.focus()

    def show_phase(self, index: int, jump: bool = False) -> None:
        self.jump_mode = jump
        self.current_index = max(0, min(index, len(self.phase_classes) - 1))
        phase_cls = self.phase_classes[self.current_index]
        self.run_worker(
            self._swap_phase(phase_cls()), exclusive=True, group="phase-swap",
        )
        self.query_one("#rail", ProgressRail).refresh()
        self.refresh_bindings()

    def show_menu(self) -> None:
        from duct.cli.setup_wizard.phases import JumpMenuPhase

        self.jump_mode = True
        self.current_index = -1
        self.run_worker(
            self._swap_phase(JumpMenuPhase()), exclusive=True, group="phase-swap",
        )
        self.query_one("#rail", ProgressRail).refresh()
        self.refresh_bindings()

    def jump_to(self, phase_id: str, jump: bool = True) -> None:
        for index, phase_cls in enumerate(self.phase_classes):
            if phase_cls.phase_id == phase_id:
                self.show_phase(index, jump=jump)
                return

    def start_tutorial(self) -> None:
        from duct.cli.setup_wizard.tutorial import TUTORIAL_PHASES

        self.jump_to(TUTORIAL_PHASES[0].phase_id, jump=False)

    def on_phase_advance(self, event: Phase.Advance) -> None:
        event.stop()
        completed_cls = self._current_phase_cls()
        if 0 <= self.current_index < len(self.phase_classes):
            self.visited.add(self.current_index)
        self.refresh_state()
        if completed_cls is not None and completed_cls.phase_id in ("github", "repo-paths"):
            # Repo config just changed — drop any stale preview and re-warm.
            self.repo_preview = None
            self.warm_repo_cache()
        if self.jump_mode:
            self.show_menu()
        elif self.current_index + 1 < len(self.phase_classes):
            self.show_phase(self.current_index + 1)
        else:
            self.finish()

    def on_phase_go_back(self, event: Phase.GoBack) -> None:
        event.stop()
        if self.jump_mode:
            self.show_menu()
        elif self.current_index > 0:
            self.show_phase(self.current_index - 1)

    def finish(self) -> None:
        """Get-started walked to the end — record it and leave the wizard."""
        from duct.global_state import mark_tutorial_completed

        mark_tutorial_completed()
        self.exit(0)

    # ------------------------------------------------------------------
    # Actions / bindings.
    # ------------------------------------------------------------------

    def _current_phase_cls(self) -> type[Phase] | None:
        if 0 <= self.current_index < len(self.phase_classes):
            return self.phase_classes[self.current_index]
        return None

    def action_back(self) -> None:
        if self.jump_mode and self.current_index == -1:
            self.exit(0)
            return
        if self.jump_mode:
            self.show_menu()
        elif self.current_index > 0:
            self.show_phase(self.current_index - 1)

    def action_skip(self) -> None:
        phase_cls = self._current_phase_cls()
        if phase_cls is not None and phase_cls.skippable:
            self.on_phase_advance(Phase.Advance())

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "skip":
            phase_cls = self._current_phase_cls()
            return bool(phase_cls is not None and phase_cls.skippable)
        if action == "back":
            return self.jump_mode or self.current_index > 0
        return True


def run_wizard() -> int:
    """Run the wizard and return a process exit code."""
    app = SetupApp()
    app.run()
    return app.return_code or 0


def tutorial_completed() -> bool:
    return load_state().tutorial_completed

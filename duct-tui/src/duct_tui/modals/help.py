"""Help overlay showing keybindings."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static
from textual.binding import Binding


HELP_TEXT = """\
[bold]Navigation[/bold]
  h/l     Left / right
  j/k     Down / up (within lists)
  J/K     Down / up (between panels)
  gg      Jump to first item
  G       Jump to last item
  Enter   Select / open

[bold]Tabs[/bold]
  \]       Next tab
  \[       Previous tab
  1-5     Jump to tab by position
  w       Clear ticket tab

[bold]Actions[/bold]
  y       Approve action
  n       Reject action
  l       Launch session
  x       Stop session
  r       Add repo
  f       Cycle filter

[bold]Conduct[/bold]
  r       Run orchestrator
  c       Launch root session + dock

[bold]Global[/bold]
  Ctrl+K  Find / open a ticket
  a       Next attention item
  s       Sync
  S       Force sync
  N       Notifications feed
  ?       Help
  q       Quit
"""


class HelpModal(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_help", "Close"),
        Binding("question_mark", "dismiss_help", "Close"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(HELP_TEXT, id="help-content")

    def action_dismiss_help(self) -> None:
        self.dismiss(None)

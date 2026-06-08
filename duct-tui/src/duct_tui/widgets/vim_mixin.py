"""VimListMixin -- gg/G jump-to-first/last for list widgets."""

from __future__ import annotations

from textual.binding import Binding
from textual.events import Key
from textual.timer import Timer


class VimListMixin:
    """Mixin providing gg (first) and G (last) vim motions for list widgets.

    Subclasses must implement _vim_goto_first() and _vim_goto_last().
    Place this mixin BEFORE the base widget class in the MRO so on_key
    intercepts before the widget's own key handling.
    """

    VIM_BINDINGS = [
        Binding("G", "vim_goto_last", "Last", show=False),
    ]

    _g_pending: bool = False
    _g_timer: Timer | None = None

    def on_key(self, event: Key) -> None:
        if event.key == "g":
            if self._g_pending:
                self._g_pending = False
                if self._g_timer:
                    self._g_timer.stop()
                self._vim_goto_first()
                event.stop()
                event.prevent_default()
            else:
                self._g_pending = True
                self._g_timer = self.set_timer(0.3, self._reset_g)
                event.stop()
                event.prevent_default()
        else:
            self._g_pending = False

    def _reset_g(self) -> None:
        self._g_pending = False

    def action_vim_goto_last(self) -> None:
        self._vim_goto_last()

    def _vim_goto_first(self) -> None:
        raise NotImplementedError

    def _vim_goto_last(self) -> None:
        raise NotImplementedError

"""SessionPreview -- terminal snapshot displayed alongside the session list."""

from __future__ import annotations

import re

from rich.text import Text
from textual import events
from textual.message import Message
from textual.widgets import RichLog


# WezTerm uses colon-separated SGR parameters (ISO 8613-6) but Rich only
# parses semicolon-separated. Convert within CSI sequences, dropping the
# optional color space ID that appears as an empty field (the :: in 38:2::R:G:B).
_CSI_RE = re.compile(r"\033\[([0-9:;]+)m")


def _fix_sgr_colons(text: str) -> str:
    def _replace(m: re.Match) -> str:
        params = m.group(1).replace(":", ";")
        # Collapse ;; (empty color space ID) to ;
        while ";;" in params:
            params = params.replace(";;", ";")
        return f"\033[{params}m"
    return _CSI_RE.sub(_replace, text)


class SessionPreview(RichLog):
    """Displays captured terminal output for the highlighted session."""

    DEFAULT_CSS = """
    SessionPreview {
        scrollbar-size: 0 0;
    }
    """

    class Activate(Message):
        """Posted when the preview is clicked — user wants to dock the session."""

    def __init__(self, **kwargs) -> None:
        super().__init__(wrap=False, markup=False, **kwargs)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Activate())

    def update_content(self, text: str) -> None:
        """Replace content with new ANSI-escaped terminal text."""
        self.clear()
        self.write(Text.from_ansi(_fix_sgr_colons(text)))

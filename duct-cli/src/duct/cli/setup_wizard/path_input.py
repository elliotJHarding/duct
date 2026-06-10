"""Directory-path entry widgets for the wizard.

``PathInput`` gives an Input shell-style tab completion (complete the
unique match, extend to the common prefix and list candidates when
ambiguous), and ``DirectoryPicker`` is a folders-only DirectoryTree for
browsing to a location instead of typing it.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from textual import events
from textual.message import Message
from textual.widgets import DirectoryTree, Input


def complete_dir_path(value: str) -> tuple[str | None, list[str]]:
    """Shell-style directory completion for a typed path.

    Returns ``(new_value, candidates)``. ``new_value`` is None when the
    completion adds nothing; ``candidates`` holds the directory names that
    still match when the result is ambiguous. The head of the typed string
    (everything up to the last ``/``) is kept verbatim so ``~`` survives.
    """
    slash = value.rfind("/")
    head, prefix = value[: slash + 1], value[slash + 1 :]
    base = Path(head).expanduser() if head else Path(".")
    try:
        names = sorted(
            entry.name
            for entry in base.iterdir()
            if entry.is_dir()
            and entry.name.startswith(prefix)
            # Hidden dirs only complete once the user types the dot.
            and (prefix.startswith(".") or not entry.name.startswith("."))
        )
    except OSError:
        return None, []
    if not names:
        return None, []
    if len(names) == 1:
        return head + names[0] + "/", []
    common = os.path.commonprefix(names)
    new_value = head + common if len(common) > len(prefix) else None
    return new_value, names


def contract_home(path: Path) -> str:
    """Render a path with the home directory shortened to ``~``."""
    home = Path.home()
    if path == home:
        return "~"
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


class PathInput(Input):
    """Input with terminal-style tab completion for directory paths."""

    class Candidates(Message):
        """Completion was ambiguous (or resolved) — candidate names to show."""

        ALLOW_SELECTOR_MATCH: ClassVar[set[str]] = {"control"}

        def __init__(self, path_input: PathInput, names: list[str]) -> None:
            super().__init__()
            self.names = names
            self._path_input = path_input

        @property
        def control(self) -> PathInput:
            return self._path_input

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "tab":
            # Tab always completes, like a shell — it never moves focus,
            # even when nothing matches (a shell would just beep).
            event.stop()
            event.prevent_default()
            new_value, names = complete_dir_path(self.value)
            if new_value is not None:
                self.value = new_value
                self.cursor_position = len(new_value)
            self.post_message(self.Candidates(self, names))
            return
        await super()._on_key(event)


class DirectoryPicker(DirectoryTree):
    """DirectoryTree restricted to visible folders."""

    # Chevrons instead of the default emoji icons, matching the wizard's
    # unicode-symbol styling.
    ICON_NODE = "▸ "
    ICON_NODE_EXPANDED = "▾ "

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [p for p in paths if p.is_dir() and not p.name.startswith(".")]

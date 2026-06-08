"""FuzzyCombobox -- visible Input with a live-filtered OptionList below.

Stock ``Select`` hides the typed query and only does prefix matching. This
widget shows the query, does subsequence matching against each option's
searchable text, and exposes ``Selected``/``Cancelled`` messages so callers
can chain focus to the next form field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option


@dataclass(frozen=True)
class ComboOption:
    """One selectable row in a FuzzyCombobox.

    ``value`` is returned in ``Selected`` messages; ``label`` is the primary
    text users type against; ``secondary`` is dim trailing text (e.g. a repo
    slug) that is also searchable so ``ice-insuretech/aa-mocks`` finds it.
    """

    value: str
    label: str
    secondary: str = ""

    @property
    def haystack(self) -> str:
        return f"{self.label} {self.secondary}".lower()


def _subsequence_match(query: str, haystack: str) -> tuple[bool, int, list[int]]:
    """Return (matched, span, positions_in_haystack) for a subsequence match.

    ``positions`` indexes into the original (non-lowered) haystack so callers
    can highlight the matched characters. ``span`` is the distance between
    the first and last matched character -- tighter matches rank higher.
    """
    if not query:
        return True, 0, []
    q = query.lower()
    h = haystack.lower()
    positions: list[int] = []
    qi = 0
    for hi, ch in enumerate(h):
        if ch == q[qi]:
            positions.append(hi)
            qi += 1
            if qi == len(q):
                break
    if qi < len(q):
        return False, 0, []
    return True, positions[-1] - positions[0], positions


def _highlight(text: str, positions: list[int], *, start: int = 0) -> Text:
    """Render ``text`` with ``positions`` (haystack-absolute) bolded."""
    rich_text = Text(text)
    for pos in positions:
        rel = pos - start
        if 0 <= rel < len(text):
            rich_text.stylize("bold reverse", rel, rel + 1)
    return rich_text


class FuzzyCombobox(Widget):
    """Input + OptionList composite with subsequence fuzzy filtering."""

    DEFAULT_CSS = """
    FuzzyCombobox {
        height: auto;
        layout: vertical;
    }
    FuzzyCombobox > Input {
        margin: 0;
    }
    FuzzyCombobox > OptionList {
        max-height: 10;
        height: auto;
        border: none;
        padding: 0;
    }
    FuzzyCombobox.-empty > OptionList {
        display: none;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    class Selected(Message):
        """Posted when the user picks an option (Enter / click)."""

        def __init__(self, combobox: "FuzzyCombobox", value: str) -> None:
            super().__init__()
            self.combobox = combobox
            self.value = value

        @property
        def control(self) -> "FuzzyCombobox":
            return self.combobox

    class Cancelled(Message):
        """Posted when the user hits Escape from the input or the list."""

        def __init__(self, combobox: "FuzzyCombobox") -> None:
            super().__init__()
            self.combobox = combobox

        @property
        def control(self) -> "FuzzyCombobox":
            return self.combobox

    def __init__(
        self,
        *,
        placeholder: str = "",
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self._placeholder = placeholder
        self._options: list[ComboOption] = []
        self._filtered: list[ComboOption] = []

    def compose(self) -> ComposeResult:
        yield Input(placeholder=self._placeholder, id="combo-input")
        yield OptionList(id="combo-list")

    # -- Public API --

    def set_options(self, options: Iterable[ComboOption]) -> None:
        self._options = list(options)
        self._apply_filter(self._input.value if self.is_mounted else "")

    def set_value(self, value: str) -> None:
        """Set the visible text; does not emit Selected."""
        if self.is_mounted:
            self._input.value = value

    def clear(self) -> None:
        self._options = []
        if self.is_mounted:
            self._input.value = ""
            self._option_list.clear_options()
            self.add_class("-empty")

    @property
    def value(self) -> str:
        return self._input.value if self.is_mounted else ""

    def focus_input(self) -> None:
        if self.is_mounted:
            self._input.focus()

    # -- Internals --

    @property
    def _input(self) -> Input:
        return self.query_one("#combo-input", Input)

    @property
    def _option_list(self) -> OptionList:
        return self.query_one("#combo-list", OptionList)

    def on_mount(self) -> None:
        self._apply_filter("")

    def _apply_filter(self, query: str) -> None:
        scored: list[tuple[int, int, ComboOption, list[int]]] = []
        for idx, opt in enumerate(self._options):
            ok, span, positions = _subsequence_match(query, opt.haystack)
            if ok:
                scored.append((span, idx, opt, positions))
        scored.sort(key=lambda t: (t[0], t[1]))
        self._filtered = [opt for _, _, opt, _ in scored]

        olist = self._option_list
        olist.clear_options()
        for span, _idx, opt, positions in scored:
            label_len = len(opt.label)
            label_text = _highlight(
                opt.label,
                [p for p in positions if p < label_len],
            )
            row = Text()
            row.append_text(label_text)
            if opt.secondary:
                row.append("  ")
                sec_positions = [p - label_len - 1 for p in positions if p > label_len]
                sec_text = _highlight(opt.secondary, sec_positions)
                sec_text.stylize("dim")
                row.append_text(sec_text)
            olist.add_option(Option(row, id=opt.value))

        if self._filtered:
            self.remove_class("-empty")
            olist.highlighted = 0
        else:
            self.add_class("-empty")

    # -- Event handlers --

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "combo-input":
            return
        event.stop()
        self._apply_filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "combo-input":
            return
        event.stop()
        self._commit_highlighted()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        event.stop()
        if event.option.id:
            self._emit_selected(event.option.id)

    def on_key(self, event: events.Key) -> None:
        focused = self.screen.focused if self.is_mounted else None
        if focused is self._input:
            if event.key == "down":
                if self._filtered:
                    self._option_list.focus()
                    self._option_list.highlighted = 0
                    event.stop()
                    event.prevent_default()
            elif event.key == "tab":
                # Let Tab propagate so the parent form can advance, but only
                # if we've already got a value. Otherwise allow the default
                # focus cycle -- user can always Shift-Tab back.
                return
        elif focused is self._option_list:
            if event.key == "up" and self._option_list.highlighted == 0:
                self._input.focus()
                event.stop()
                event.prevent_default()
            elif event.key in ("escape",):
                self.post_message(self.Cancelled(self))
                event.stop()

    def action_cancel(self) -> None:
        self.post_message(self.Cancelled(self))

    def _commit_highlighted(self) -> None:
        olist = self._option_list
        if not self._filtered:
            return
        idx = olist.highlighted if olist.highlighted is not None else 0
        if idx < 0 or idx >= len(self._filtered):
            idx = 0
        self._emit_selected(self._filtered[idx].value)

    def _emit_selected(self, value: str) -> None:
        for opt in self._options:
            if opt.value == value:
                self._input.value = opt.label
                break
        self.post_message(self.Selected(self, value))

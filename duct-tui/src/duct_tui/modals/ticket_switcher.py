"""TicketSwitcherModal -- global Ctrl+K ticket finder.

A full-screen overlay that loads every non-terminal ticket and groups it into
status columns (In Progress / Post development / Pre Development / everything
else / Not assigned). A search box at the top filters by ticket key with the
first match ghosted inline; Enter opens that match. Navigation is type-only —
there is no in-overlay cursor.
"""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from duct_tui.phases import PHASE_COLORS, phase_for_category
from duct_tui.widgets.ticket_badge import render_ticket_badge

# (column key, header, the phase whose colour styles the card). The column key
# doubles as the CSS class suffix (`switcher-col-active`, … `switcher-col-na`).
_COLUMNS: list[tuple[str, str, str]] = [
    ("active", "In Progress", "active"),
    ("post", "Post development", "post"),
    ("pre", "Pre Development", "pre"),
    ("na", "Not assigned", ""),
    ("other", "", "other"),          # unknown-status catch-all, far right, no heading
]

# Width used to truncate titles before the column has been laid out (size 0).
_FALLBACK_WIDTH = 30


def _column_for(overview) -> str:
    """Which column an overview belongs to. Unassigned takes precedence."""
    if not getattr(overview, "assigned_to_me", True):
        return "na"
    return phase_for_category(overview.category)


class TicketSwitcherModal(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("tab", "next_match", "Next match", show=False, priority=True),
        Binding("shift+tab", "prev_match", "Prev match", show=False, priority=True),
    ]

    # Selection highlight + cycle marker.
    _MARKER = "▸"        # ▸
    _ACCENT = "#bb9af7"       # Duct brand lavender (matches theme accent)
    _HILITE_BG = "#3b4261"    # Filled-card background for the selected ticket

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._overviews: list = []  # phase-sorted; defines match/cycle order
        self._matches: list = []    # current prefix matches, in cycle order
        self._selected: int = 0     # index into _matches

    def compose(self) -> ComposeResult:
        with Vertical(id="switcher"):
            yield Input(
                placeholder="Type a ticket key…",
                id="switcher-search",
            )
            with Horizontal(id="switcher-columns"):
                for col_key, header, _phase in _COLUMNS:
                    with Vertical(classes=f"switcher-col switcher-col-{col_key}"):
                        yield Static(header, classes="switcher-col-title")
                        with VerticalScroll(classes="switcher-rows"):
                            yield Static("", id=f"switcher-body-{col_key}")

    def on_mount(self) -> None:
        self.query_one("#switcher-search", Input).focus()
        self._load()

    @work(thread=True)
    def _load(self) -> None:
        # Metadata-only load (TICKET.md reads, no git/sessions/PRs) so the
        # columns populate near-instantly even on large workspaces.
        overviews = self.app.data.load_ticket_index(filter_mode="all")
        self.app.call_from_thread(self._apply, overviews)

    def _apply(self, overviews) -> None:
        self._overviews = overviews
        self._refilter("")

    def on_input_changed(self, event: Input.Changed) -> None:
        # Force uppercase so the ghost completion and prefix filter stay aligned
        # with the canonical uppercase ticket keys.
        upper = event.value.upper()
        if upper != event.value:
            event.input.value = upper  # re-fires Changed; the branch below runs then
            return
        self._refilter(upper)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(self._selected_key())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_next_match(self) -> None:
        if self._matches:
            self._selected = (self._selected + 1) % len(self._matches)
            self._repaint()

    def action_prev_match(self) -> None:
        if self._matches:
            self._selected = (self._selected - 1) % len(self._matches)
            self._repaint()

    def _refilter(self, prefix: str) -> None:
        """Recompute the matching set and reset the highlight to the first match."""
        self._matches = [
            o for o in self._overviews if not prefix or o.key.startswith(prefix)
        ]
        self._selected = 0
        self._repaint()

    def _selected_key(self) -> str | None:
        if not self._matches:
            return None
        return self._matches[self._selected].key

    def _repaint(self) -> None:
        """Repaint every column body from the current match set.

        Each ticket is two lines — the canonical key badge followed by the
        title, then the status — mirroring how keys read elsewhere in the TUI.
        The currently-selected match (Enter target, cycled by Tab) is drawn as
        a filled card spanning the full column width. Titles truncate to the
        column's real width.
        """
        selected = self._selected_key()
        buckets: dict[str, list] = {col_key: [] for col_key, _h, _p in _COLUMNS}
        for o in self._matches:
            buckets[_column_for(o)].append(o)

        for col_key, _header, phase in _COLUMNS:
            color = PHASE_COLORS.get(phase, "")
            dim = col_key == "na"
            widget = self.query_one(f"#switcher-body-{col_key}", Static)
            width = widget.size.width or _FALLBACK_WIDTH
            body = Text()
            rows = buckets[col_key]
            if not rows:
                body.append("—", style="dim")
            for o in rows:
                chosen = o.key == selected
                base = f"on {self._HILITE_BG}" if chosen else ""

                # Line 1: gutter + key badge + title (truncated to remaining
                # width). The selected row's lines carry a background and are
                # padded to the full width so they read as a filled card.
                line1 = Text(style=base)
                line1.append(
                    f"{self._MARKER} " if chosen else "  ",
                    style=f"{self._ACCENT} {base}" if chosen else "",
                )
                line1.append_text(render_ticket_badge(o.key))
                title = o.summary or ""
                title_avail = width - 2 - (len(o.key) + 2) - 1
                if len(title) > title_avail:
                    title = title[: max(0, title_avail - 1)] + "…"
                if title:
                    line1.append(" ")
                    line1.append(title, style="bold" if chosen else ("dim" if dim else ""))
                if chosen:
                    self._pad(line1, width)
                body.append_text(line1)
                body.append("\n")

                # Line 2: status, aligned under the badge past the gutter.
                if o.status:
                    line2 = Text(style=base)
                    line2.append("  ")
                    line2.append(
                        o.status,
                        style="bold" if chosen else ("dim" if dim else (color or "dim")),
                    )
                    if chosen:
                        self._pad(line2, width)
                    body.append_text(line2)
                    body.append("\n")
                body.append("\n")
            widget.update(body)

        self._update_ghost()

    @staticmethod
    def _pad(line: Text, width: int) -> None:
        """Pad a line with trailing spaces (inheriting its base style) so a
        background fill spans the full column width."""
        gap = width - line.cell_len
        if gap > 0:
            line.append(" " * gap)

    def _update_ghost(self) -> None:
        """Inline-ghost the highlighted key so the completion tracks the
        Tab selection (not just the first match). Textual renders the part of
        ``_suggestion`` beyond the typed value; we set it directly because the
        built-in Suggester only refreshes on value changes, never on Tab.
        """
        inp = self.query_one("#switcher-search", Input)
        key = self._selected_key()
        inp._suggestion = key if (key and inp.value) else ""

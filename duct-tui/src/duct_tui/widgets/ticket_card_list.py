"""TicketCardList — horizontally scrolling list of ticket cards."""

from __future__ import annotations

import textwrap

from textual.binding import Binding
from textual.containers import HorizontalScroll
from textual.message import Message
from textual.widgets import Static

from duct_tui.icons import Icons, get_icons
from duct_tui.phases import phase_for_category
from duct_tui.widgets.ticket_card import (
    CARD_CONTENT_WIDTH,
    SectionHeights,
    TicketCard,
    card_pr_line_count,
)
from duct_tui.widgets.vim_mixin import VimListMixin


_LOADING_PLACEHOLDER_ID = "ticket-card-list-loading"


class TicketCardList(VimListMixin, HorizontalScroll):
    BINDINGS = [
        Binding("l", "focus_next_card", "Next", show=False),
        Binding("h", "focus_prev_card", "Prev", show=False),
        *VimListMixin.VIM_BINDINGS,
    ]

    class TicketSelected(Message):
        def __init__(self, ticket_key: str) -> None:
            super().__init__()
            self.ticket_key = ticket_key

    def __init__(self, **kwargs) -> None:
        super().__init__(id=kwargs.pop("id", "ticket-card-list"), **kwargs)
        self._overviews = []
        self._initial_load = True
        self._loading_shown = False

    @property
    def _icons(self) -> Icons:
        app = self.app
        return getattr(app, "icons", get_icons())

    def show_loading(self, message: str = "Loading workspace…") -> None:
        """Mount a placeholder so the user gets immediate feedback while
        the ticket-overview phase of ``load_initial`` is still running.
        Replaced by real cards as soon as `update_tickets` lands."""
        if self._loading_shown or self._overviews:
            return
        self._loading_shown = True
        try:
            self.remove_children()
            self.mount(Static(message, id=_LOADING_PLACEHOLDER_ID))
        except Exception:
            pass

    def _clear_loading(self) -> None:
        if not self._loading_shown:
            return
        self._loading_shown = False
        try:
            self.query_one(f"#{_LOADING_PLACEHOLDER_ID}", Static).remove()
        except Exception:
            pass

    def update_tickets(self, overviews) -> None:
        # Preserve the loading placeholder while overviews is still empty
        # — the FullScreen calls `_update_widgets` on mount before any
        # data has loaded, and we don't want that empty pass to wipe the
        # placeholder we just showed.
        if overviews:
            self._clear_loading()
        elif self._loading_shown:
            return
        heights = self._compute_section_heights(overviews)
        icons = self._icons
        new_keys = [o.key for o in overviews]
        old_keys = [o.key for o in self._overviews]
        if new_keys == old_keys and not self._initial_load:
            self._overviews = overviews
            for overview in overviews:
                try:
                    card = self.query_one(f"#card-{overview.key}", TicketCard)
                    new_phase = f"phase-{phase_for_category(overview.category)}"
                    for cls in ("phase-active", "phase-post", "phase-pre", "phase-other"):
                        if cls != new_phase:
                            card.remove_class(cls)
                    card.add_class(new_phase)
                    if getattr(overview, "assigned_to_me", True):
                        card.remove_class("not-mine")
                    else:
                        card.add_class("not-mine")
                    card.update_overview(overview, heights, icons)
                except Exception:
                    pass
            return
        self._overviews = overviews
        self.remove_children()
        for overview in overviews:
            classes = [f"phase-{phase_for_category(overview.category)}"]
            if not getattr(overview, "assigned_to_me", True):
                classes.append("not-mine")
            card = TicketCard(overview, section_heights=heights, icons=icons, id=f"card-{overview.key}", classes=" ".join(classes))
            self.mount(card)
        if overviews and self._initial_load:
            self._initial_load = False
            self.call_after_refresh(self._focus_first_card)

    @staticmethod
    def _compute_section_heights(overviews) -> SectionHeights:
        if not overviews:
            return SectionHeights()

        max_summary = 1
        max_artifacts = 0
        max_repos = 0
        max_prs = 0
        max_sessions = 0
        max_tasks = 0
        max_pending = 0

        for o in overviews:
            wrapped = textwrap.wrap(o.summary, width=CARD_CONTENT_WIDTH) or [""]
            max_summary = max(max_summary, min(len(wrapped), 3))

            # Artifacts render two-per-row, sessions are two lines each
            # (matches render_session_card from the sessions/ticket tabs).
            # PRs and repos keep the existing budgets.
            if o.artifacts:
                max_artifacts = max(max_artifacts, (len(o.artifacts) + 1) // 2)
            if o.repos:
                max_repos = max(max_repos, len(o.repos))
            if o.prs:
                max_prs = max(max_prs, card_pr_line_count(o.prs))
            if o.sessions:
                max_sessions = max(max_sessions, len(o.sessions) * 2)
            if o.tasks:
                todo_count = sum(1 for t in o.tasks if t.status == "todo")
                max_tasks = max(max_tasks, todo_count + 1)  # +1 for progress counter
            if o.pending_actions:
                max_pending = max(max_pending, len(o.pending_actions))

        return SectionHeights(
            summary=max_summary,
            artifacts=max_artifacts,
            repos=max_repos,
            prs=max_prs,
            sessions=max_sessions,
            tasks=max_tasks,
            pending=max_pending,
        )

    def _focus_first_card(self) -> None:
        # Focusing a TicketCard drags the ancestor Overview TabPane into view
        # and flips the active tab back to it, so skip the auto-focus if the
        # user has navigated elsewhere while data loaded.
        from textual.widgets import TabbedContent
        try:
            tabs = self.screen.query_one(TabbedContent)
        except Exception:
            return
        if tabs.active != "overview":
            return
        cards = self.query(TicketCard)
        if cards:
            cards.first().focus()

    def on_ticket_card_selected(self, event: TicketCard.Selected) -> None:
        event.stop()
        self.post_message(self.TicketSelected(event.ticket_key))

    def _get_cards(self) -> list[TicketCard]:
        return list(self.query(TicketCard))

    def _focused_index(self) -> int:
        cards = self._get_cards()
        for i, card in enumerate(cards):
            if card.has_focus:
                return i
        return -1

    def action_focus_next_card(self) -> None:
        cards = self._get_cards()
        if not cards:
            return
        idx = self._focused_index()
        next_idx = (idx + 1) % len(cards)
        cards[next_idx].focus()
        cards[next_idx].scroll_visible()

    def action_focus_prev_card(self) -> None:
        cards = self._get_cards()
        if not cards:
            return
        idx = self._focused_index()
        prev_idx = (idx - 1) % len(cards)
        cards[prev_idx].focus()
        cards[prev_idx].scroll_visible()

    def _vim_goto_first(self) -> None:
        cards = self._get_cards()
        if cards:
            cards[0].focus()
            cards[0].scroll_visible()

    def _vim_goto_last(self) -> None:
        cards = self._get_cards()
        if cards:
            cards[-1].focus()
            cards[-1].scroll_visible()

    def focus_next_attention(self) -> None:
        """Cycle focus to next card needing attention."""
        cards = self._get_cards()
        if not cards:
            return
        start = self._focused_index() + 1
        for i in range(len(cards)):
            idx = (start + i) % len(cards)
            o = cards[idx]._overview
            if o.pending_actions or any(s.status == "waiting" for s in o.sessions):
                cards[idx].focus()
                cards[idx].scroll_visible()
                return
        # Also check for failing CI in PRs
        for i in range(len(cards)):
            idx = (start + i) % len(cards)
            o = cards[idx]._overview
            if any(pr.ci_status in ("failing", "failure", "✗") for pr in o.prs):
                cards[idx].focus()
                cards[idx].scroll_visible()
                return

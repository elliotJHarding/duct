"""PRPanel -- pull request cards with CI, review and comment detail."""

from __future__ import annotations

import webbrowser

from textual.binding import Binding
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from duct.models import PullRequest
from duct.pr import pr_action_reasons
from duct_tui.icons import UNICODE, Icons
from duct_tui.widgets.pr_render import (
    format_relative,
    render_collapsed_pr_row,
    render_pr_card,
)
from duct_tui.widgets.vim_mixin import VimListMixin


class PRPanel(VimListMixin, OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        *VimListMixin.VIM_BINDINGS,
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.border_title = "Pull Requests"
        self._icons: Icons = UNICODE
        self._prs_by_id: dict[str, PullRequest] = {}

    def on_mount(self) -> None:
        self._icons = getattr(self.app, "icons", UNICODE)

    def _vim_goto_first(self) -> None:
        if self.option_count:
            self.highlighted = 0

    def _vim_goto_last(self) -> None:
        if self.option_count:
            self.highlighted = self.option_count - 1

    def update_prs(self, prs: list[PullRequest]) -> None:
        self.clear_options()
        self._prs_by_id = {str(pr.number): pr for pr in prs}
        # Open PRs render as full cards; done PRs (merged/closed) collapse to a
        # single aligned line at the bottom.
        open_prs = [pr for pr in prs if pr.state not in ("merged", "closed")]
        done_prs = [pr for pr in prs if pr.state in ("merged", "closed")]

        for pr in open_prs:
            if self.option_count:
                self.add_option(None)
            card = render_pr_card(
                pr,
                self._icons,
                relative_time_str=format_relative(pr.updated_at),
                created_time_str=format_relative(pr.created_at),
                action_reasons=tuple(pr_action_reasons(pr)),
            )
            self.add_option(Option(card, id=str(pr.number)))

        if open_prs and done_prs:
            self.add_option(None)

        for pr in done_prs:
            self.add_option(Option(
                render_collapsed_pr_row(
                    pr, self._icons,
                    relative_time_str=format_relative(pr.updated_at),
                ),
                id=str(pr.number),
            ))

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        pr = self._prs_by_id.get(event.option_id or "")
        if pr is None:
            return
        if not pr.url:
            self.app.notify("PR has no URL to open.", severity="warning")
            return
        try:
            webbrowser.open(pr.url)
        except Exception:
            pass

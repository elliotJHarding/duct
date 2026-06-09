"""PRPanel -- pull request list with CI status."""

from __future__ import annotations

from textual.binding import Binding
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from duct.models import PullRequest
from duct_tui.icons import UNICODE, Icons
from duct_tui.widgets.pr_render import format_relative, render_collapsed_pr_row
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
        # Open PRs keep the multi-line label; done PRs (merged/closed) collapse
        # to a single aligned line at the bottom.
        open_prs = [pr for pr in prs if pr.state not in ("merged", "closed")]
        done_prs = [pr for pr in prs if pr.state in ("merged", "closed")]

        for pr in open_prs:
            ci = ""
            if pr.ci_status in ("passing", "success"):
                ci = " CI:✓"
            elif pr.ci_status in ("failing", "failure"):
                ci = " CI:✗"
            elif pr.ci_status:
                ci = f" CI:{pr.ci_status}"

            review = ""
            if pr.review_status:
                review = f"  {pr.review_status}"

            label = (
                f"#{pr.number} {pr.title[:40]}\n"
                f"  {pr.state}{ci}{review}"
            )
            if pr.reviewers:
                reviewers_str = ", ".join(f"@{r.login}: {r.state}" for r in pr.reviewers)
                label += f"\n  {reviewers_str}"

            self.add_option(Option(label, id=str(pr.number)))

        for pr in done_prs:
            self.add_option(Option(
                render_collapsed_pr_row(
                    pr, self._icons,
                    relative_time_str=format_relative(pr.updated_at),
                ),
                id=str(pr.number),
            ))

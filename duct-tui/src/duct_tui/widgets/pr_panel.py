"""PRPanel -- pull request list with CI status."""

from __future__ import annotations

from textual.binding import Binding
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from duct.models import PullRequest
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

    def _vim_goto_first(self) -> None:
        if self.option_count:
            self.highlighted = 0

    def _vim_goto_last(self) -> None:
        if self.option_count:
            self.highlighted = self.option_count - 1

    def update_prs(self, prs: list[PullRequest]) -> None:
        self.clear_options()
        for pr in prs:
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

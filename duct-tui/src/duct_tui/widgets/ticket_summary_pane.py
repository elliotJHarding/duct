"""TicketSummaryPane -- left navigation pane mirroring the overview card layout.

Each section from the overview card becomes a selectable item. Highlighting
an item switches the right panel to show richer detail for that section.
"""

from __future__ import annotations

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from duct.models import SessionInfo, TicketDetail
from duct_tui.icons import Icons, UNICODE
from duct_tui.widgets.pr_render import format_relative, render_collapsed_pr_line
from duct_tui.widgets.ticket_badge import render_ticket_badge
from duct_tui.widgets.session_panel import (
    _SPINNER_FRAMES,
    SessionPanel,
    render_launch_row,
    render_session_card,
)
from duct_tui.widgets.vim_mixin import VimListMixin


_LAUNCH_ITEM_ID = "session:__launch__"


class TicketSummaryPane(VimListMixin, OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("L", "launch_session", "Launch", show=False),
        Binding("enter", "focus_session", "Focus", show=False),
        Binding("l", "focus_session", "Focus", show=False),
        *VimListMixin.VIM_BINDINGS,
    ]

    class SectionChanged(Message):
        def __init__(self, section: str, item_id: str) -> None:
            super().__init__()
            self.section = section
            self.item_id = item_id

    class FocusRightRequested(Message):
        """Posted when l/Enter is pressed on a non-session row so the parent can
        move focus to the right-hand detail pane."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.border_title = "Summary"
        self._skip_direction: int = 1
        self._sep_count: int = 0
        self._icons: Icons = UNICODE
        self._spinner_frame: int = 0
        self._session_options: list[tuple[int, SessionInfo]] = []
        # Track the option id we last fired SectionChanged for. A programmatic
        # `self.highlighted = idx` (e.g. from _restore_highlight after the 10s
        # data refresh) re-fires OptionHighlighted asynchronously — too late
        # for any synchronous suppression flag to catch. Comparing IDs is the
        # only reliable way to ignore a re-fire of the same row.
        self._last_reported_option_id: str | None = None

    def on_mount(self) -> None:
        self._icons = getattr(self.app, "icons", UNICODE)
        # Spinner ticks at 4Hz rather than 10Hz. Each tick calls
        # replace_option_prompt_at_index, which wipes OptionList's render cache
        # for *every* option — at 10Hz the multi-line PR / workspace / session
        # options were being re-rendered constantly and keyboard nav felt laggy.
        self.set_interval(0.25, self._tick_spinner)

    def _tick_spinner(self) -> None:
        # Don't churn the cache when the pane isn't actually on screen
        # (e.g. the user is on a different tab).
        if not self.display or not self.is_mounted:
            return
        working = [
            (idx, s) for idx, s in self._session_options if s.status == "working"
        ]
        if not working:
            return
        self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
        for idx, s in working:
            if idx < self.option_count:
                self.replace_option_prompt_at_index(idx, self._render_session(s))

    def _separator(self) -> Option:
        self._sep_count += 1
        return Option("\u2500" * 30, id=f"sep:{self._sep_count}", disabled=True)

    def update_data(self, detail: TicketDetail, artifacts: list[str]) -> None:
        previous_id = self._highlighted_option_id()
        self.clear_options()
        self._sep_count = 0
        self._session_options = []
        t = detail.ticket

        # -- Ticket: key badge + summary + at-a-glance metadata --
        # 2-3 dim lines below the summary so the top row carries more context
        # than just key + title.
        ticket_text = Text()
        ticket_text.append_text(render_ticket_badge(t.key))
        ticket_text.append(f"\n{t.summary}")
        status_parts = [p for p in (t.status, t.category) if p]
        if status_parts:
            ticket_text.append(f"\n{' / '.join(status_parts)}", style="dim italic")
        meta_parts: list[str] = []
        if t.assignee:
            meta_parts.append(t.assignee)
        if t.issue_type:
            meta_parts.append(t.issue_type)
        if t.priority:
            meta_parts.append(t.priority)
        if meta_parts:
            sep = " \u00b7 "
            ticket_text.append("\n" + sep.join(meta_parts), style="dim")
        self.add_option(Option(ticket_text, id="ticket:detail"))

        # -- Artifacts: one item per artifact --
        if artifacts:
            self.add_option(self._separator())
            for name in artifacts:
                self.add_option(Option(f"\u00b7 {name}", id=f"artifact:{name}"))

        # -- Workspace: tree of repo names, or empty-state hint --
        self.add_option(self._separator())
        workspace_text = Text()
        if detail.repos:
            last = len(detail.repos) - 1
            for i, repo in enumerate(detail.repos):
                if i > 0:
                    workspace_text.append("\n")
                prefix = "\u2514\u2500 " if i == last else "\u251c\u2500 "
                workspace_text.append(prefix, style="bright_black")
                color = "yellow" if repo.dirty else "green"
                workspace_text.append(repo.name, style=color)
                if repo.dirty:
                    workspace_text.append(f" \u25b3", style="yellow")
        else:
            workspace_text.append("No repos", style="dim italic")
            workspace_text.append(" \u2014 press ", style="dim")
            workspace_text.append("r", style="bold")
            workspace_text.append(" to add", style="dim")
        self.add_option(Option(workspace_text, id="workspace:overview"))

        # -- PRs: open PRs (2-line) first, then collapsed done PRs (1-line) --
        if detail.prs:
            self.add_option(self._separator())
            open_prs = [pr for pr in detail.prs if pr.state not in ("merged", "closed")]
            done_prs = [pr for pr in detail.prs if pr.state in ("merged", "closed")]
            pr_text = Text()
            for i, pr in enumerate(open_prs):
                if i > 0:
                    pr_text.append("\n\n")
                pr_text.append(f"#{pr.number}", style="bold")
                if pr.repo:
                    # Strip the org prefix — always the same, just clutter.
                    pr_text.append(f" {pr.repo.rsplit('/', 1)[-1]}")
                # Status line (this loop only renders open/draft PRs)
                pr_text.append("\n")
                pr_text.append(f"\u25cb {pr.state}", style="green")
                if pr.ci_status in ("passing", "success"):
                    pr_text.append(f"  \u2713 CI", style="green")
                elif pr.ci_status in ("failing", "failure"):
                    pr_text.append(f"  \u2717 CI", style="red")
                if pr.review_status:
                    review_color = "green" if "approved" in pr.review_status.lower() else "red" if "change" in pr.review_status.lower() else "bright_black"
                    review_icon = "\u2713" if "approved" in pr.review_status.lower() else "\u2717" if "change" in pr.review_status.lower() else "\u25cb"
                    pr_text.append(f"  {review_icon} {pr.review_status}", style=review_color)
            for j, pr in enumerate(done_prs):
                if j == 0:
                    if open_prs:
                        pr_text.append("\n\n")  # blank line between groups
                else:
                    pr_text.append("\n")  # done rows packed together
                pr_text.append_text(render_collapsed_pr_line(
                    pr, self._icons,
                    relative_time_str=format_relative(pr.updated_at),
                    title_width=20,
                ))
            self.add_option(Option(pr_text, id="pr:overview"))

        # -- Sessions: one item per active session, plus a launch row at the end --
        active_sessions = [s for s in detail.sessions if s.status != "terminated"]
        self.add_option(self._separator())
        for s in active_sessions:
            self.add_option(Option(self._render_session(s), id=f"session:{s.session_id}"))
            # Track index so the spinner tick can target just these rows
            self._session_options.append((self.option_count - 1, s))
        self.add_option(Option(render_launch_row(self._icons), id=_LAUNCH_ITEM_ID))

        # -- Tasks (always shown so user can navigate to TaskPanel to add) --
        self.add_option(self._separator())
        task_text = Text()
        if detail.tasks:
            todo = [t for t in detail.tasks if t.status == "todo"]
            done = [t for t in detail.tasks if t.status == "done"]
            for i, task in enumerate(todo):
                if i > 0:
                    task_text.append("\n")
                task_text.append(f"{self._icons.task_todo} ", style="bright_black")
                task_text.append(task.description)
            for i, task in enumerate(done):
                if todo or i > 0:
                    task_text.append("\n")
                task_text.append(f"{self._icons.task_done} ", style="green")
                task_text.append(task.description, style="dim strike")
            task_text.append(f"\n({len(done)}/{len(detail.tasks)} done)", style="dim")
        else:
            task_text.append("No tasks", style="dim italic")
        self.add_option(Option(task_text, id="task:tasks"))

        # -- Actions --
        pending = [a for a in detail.actions if a.status == "pending"]
        if pending:
            self.add_option(self._separator())
            action_text = Text()
            for i, a in enumerate(pending):
                if i > 0:
                    action_text.append("\n")
                action_text.append("\u2192 ", style="yellow")
                action_text.append(a.description)
            self.add_option(Option(action_text, id="action:actions"))

        self._restore_highlight(previous_id)

    def _restore_highlight(self, option_id: str | None) -> None:
        """Re-highlight the row with the given id, if it still exists.

        Keeps the cursor in place across periodic data refreshes so navigation
        isn't interrupted every 10s. The handler suppresses SectionChanged
        for the same row by comparing against `_last_reported_option_id`.
        """
        if option_id is None:
            return
        for idx in range(self.option_count):
            if self.get_option_at_index(idx).id == option_id:
                self.highlighted = idx
                return

    def _render_session(self, s: SessionInfo) -> Text:
        # show_ticket=False: every session here belongs to the current ticket,
        # so the badge would just duplicate the pane's context.
        return render_session_card(
            s,
            icons=self._icons,
            spinner_frame=self._spinner_frame,
            docked_pid=None,
            topic_width=28,
            show_ticket=False,
        )

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        if event.option is None:
            return

        option_id = event.option.id or ""

        # Auto-skip separators
        if option_id.startswith("sep:"):
            idx = self.highlighted
            if idx is not None:
                target = idx + self._skip_direction
                if 0 <= target < self.option_count:
                    self.highlighted = target
            return

        # Launch row doesn't trigger a section change; it waits for L / Enter
        if option_id == _LAUNCH_ITEM_ID:
            return

        # A programmatic re-highlight (from _restore_highlight after a periodic
        # data refresh) re-fires this event for the same row. Treat that as a
        # no-op so we don't resurrect a session preview the user has navigated
        # away from.
        if option_id == self._last_reported_option_id:
            return

        if ":" in option_id:
            section, item_id = option_id.split(":", 1)
            self._last_reported_option_id = option_id
            self.post_message(self.SectionChanged(section, item_id))

    def _highlighted_session(self) -> SessionInfo | None:
        """Return the SessionInfo for the currently highlighted row, or None."""
        idx = self.highlighted
        if idx is None:
            return None
        for opt_idx, s in self._session_options:
            if opt_idx == idx:
                return s
        return None

    def _highlighted_option_id(self) -> str | None:
        idx = self.highlighted
        if idx is None or idx >= self.option_count:
            return None
        return self.get_option_at_index(idx).id

    def action_launch_session(self) -> None:
        """Trigger a new session launch from a ticket context."""
        self.post_message(SessionPanel.SessionLaunch())

    def action_focus_session(self) -> None:
        """Enter / l dispatches based on the highlighted row:

        - launch row -> SessionLaunch
        - active session -> SessionFocus (docks the session pane)
        - anything else -> FocusRightRequested (parent moves focus to the
          right-hand detail pane)
        """
        if self._highlighted_option_id() == _LAUNCH_ITEM_ID:
            self.post_message(SessionPanel.SessionLaunch())
            return
        session = self._highlighted_session()
        if session and session.pid and session.status != "terminated":
            self.post_message(SessionPanel.SessionFocus(session.pid))
            return
        self.post_message(self.FocusRightRequested())

    def action_cursor_down(self) -> None:
        self._skip_direction = 1
        super().action_cursor_down()

    def action_cursor_up(self) -> None:
        self._skip_direction = -1
        super().action_cursor_up()

    def _vim_goto_first(self) -> None:
        if self.option_count:
            self._skip_direction = 1
            self.highlighted = 0

    def _vim_goto_last(self) -> None:
        if self.option_count:
            self._skip_direction = -1
            self.highlighted = self.option_count - 1

"""TicketCard — rich ticket summary card for overview."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.app import RenderResult

from rich.text import Text

from duct_tui.icons import Icons, UNICODE
from duct_tui.phases import PHASE_COLORS, get_phase_icon, phase_for_category
from duct_tui.widgets.pr_render import pr_state_display, review_display
from duct_tui.widgets.session_panel import render_session_card
from duct_tui.widgets.ticket_badge import render_ticket_badge

CARD_CONTENT_WIDTH = 48  # 56 (width) - 2 (border) - 6 (padding 3+3)
SEPARATOR = "\u2500" * CARD_CONTENT_WIDTH


def _is_done(pr) -> bool:
    return pr.state in ("merged", "closed")


def card_pr_line_count(prs) -> int:
    """Lines the PR section occupies: open PRs take 2 lines (with a blank
    between), done PRs collapse to 1 line packed together, plus one blank line
    separating the open block from the done block. Shared with the height
    normaliser so every card reserves the same space."""
    open_prs = [p for p in prs if not _is_done(p)]
    done_prs = [p for p in prs if _is_done(p)]
    lines = 0
    if open_prs:
        lines += len(open_prs) * 2 + (len(open_prs) - 1)
    if done_prs:
        if open_prs:
            lines += 1
        lines += len(done_prs)
    return lines

# Artifacts render as two columns; each cell gets half the card width minus
# the icon + single trailing space, with one extra space between cells.
_ARTIFACT_CELL_WIDTH = (CARD_CONTENT_WIDTH - 1) // 2 - 2


def _truncate(value: str, width: int) -> str:
    """Truncate `value` to `width` cells, replacing the tail with an ellipsis."""
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width == 1:
        return "\u2026"
    return value[: width - 1] + "\u2026"


@dataclass(frozen=True)
class SectionHeights:
    """Normalized section heights (in lines) across all visible cards."""

    summary: int = 1
    artifacts: int = 0
    repos: int = 0
    prs: int = 0
    sessions: int = 0
    tasks: int = 0
    pending: int = 0


class TicketCard(Widget, can_focus=True):
    BINDINGS = [
        Binding("enter", "select", "Open", show=False),
    ]

    class Selected(Message):
        def __init__(self, ticket_key: str) -> None:
            super().__init__()
            self.ticket_key = ticket_key

    def __init__(
        self,
        overview,
        *,
        section_heights: SectionHeights | None = None,
        icons: Icons | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._overview = overview
        self._heights = section_heights or SectionHeights()
        self._icons = icons or UNICODE

    def render(self) -> RenderResult:
        o = self._overview
        h = self._heights
        ic = self._icons
        t = Text()

        # Header: phase icon + key badge + status + assignee (always 1 line).
        # Priority is conveyed by sort order — we drop the numeric #N here to
        # free space for the status and assignee, which read at a glance.
        phase = phase_for_category(o.category)
        phase_icon = get_phase_icon(ic, phase)
        color = PHASE_COLORS.get(phase, "")
        if color:
            t.append(f"{phase_icon} ", style=color)
        t.append_text(render_ticket_badge(o.key))
        assignee = getattr(o, "assignee", "") or ""
        if o.status:
            t.append(f"  \u00b7 {o.status}", style="dim")
        if assignee and assignee.lower() != "unassigned":
            t.append(f"  \u00b7 {assignee}", style="dim")
        t.append("\n")

        # Summary (padded to h.summary lines)
        summary_lines = textwrap.wrap(o.summary, width=CARD_CONTENT_WIDTH) or [""]
        if len(summary_lines) > h.summary:
            summary_lines = summary_lines[: h.summary]
            last = summary_lines[-1]
            if len(last) > CARD_CONTENT_WIDTH - 1:
                last = last[: CARD_CONTENT_WIDTH - 1]
            summary_lines[-1] = last.rstrip() + "\u2026"
        for line in summary_lines:
            t.append(line + "\n")
        for _ in range(h.summary - len(summary_lines)):
            t.append("\n")

        # Category line (status is now on the header row, so this shows the
        # workflow phase category only).
        if o.category:
            t.append(o.category, style="dim italic")
        t.append("\n")

        # Blank line between header block and detail sections
        t.append("\n")

        # Detail sections with separators between active sections
        active_sections = []
        if h.artifacts > 0:
            active_sections.append("artifacts")
        if h.repos > 0:
            active_sections.append("repos")
        if h.prs > 0:
            active_sections.append("prs")
        if h.sessions > 0:
            active_sections.append("sessions")
        if h.tasks > 0:
            active_sections.append("tasks")
        if h.pending > 0:
            active_sections.append("pending")

        for i, section in enumerate(active_sections):
            if i > 0:
                t.append(SEPARATOR, style="bright_black")
                t.append("\n")

            if section == "artifacts":
                self._render_artifacts(t, o, h, ic)
            elif section == "repos":
                self._render_repos(t, o, h, ic)
            elif section == "prs":
                self._render_prs(t, o, h, ic)
            elif section == "sessions":
                self._render_sessions(t, o, h, ic)
            elif section == "tasks":
                self._render_tasks(t, o, h, ic)
            elif section == "pending":
                self._render_pending(t, o, h, ic)

        return t

    def _render_artifacts(self, t: Text, o, h, ic: Icons) -> None:
        # Two-column layout: each cell is icon + space + truncated name,
        # padded to a fixed width so the right column lines up.
        names = list(o.artifacts)
        rows = (len(names) + 1) // 2
        for r in range(rows):
            left = names[2 * r]
            left_cell = f"{ic.artifact} {_truncate(left, _ARTIFACT_CELL_WIDTH)}"
            t.append(left_cell.ljust(_ARTIFACT_CELL_WIDTH + 2))
            right_idx = 2 * r + 1
            if right_idx < len(names):
                right = names[right_idx]
                t.append(f" {ic.artifact} {_truncate(right, _ARTIFACT_CELL_WIDTH)}")
            t.append("\n")
        for _ in range(h.artifacts - rows):
            t.append("\n")

    def _render_repos(self, t: Text, o, h, ic: Icons) -> None:
        last = len(o.repos) - 1
        for i, repo in enumerate(o.repos):
            prefix = "\u2514\u2500 " if i == last else "\u251c\u2500 "
            t.append(prefix, style="bright_black")
            color = "yellow" if repo.dirty else "green"
            t.append(repo.name, style=color)
            if repo.dirty:
                t.append(f" {ic.dirty}", style="yellow")
            t.append("\n")
        for _ in range(h.repos - len(o.repos)):
            t.append("\n")

    def _render_prs(self, t: Text, o, h, ic: Icons) -> None:
        # Open PRs render as before (2 lines); done PRs (merged/closed) collapse
        # to a single packed line at the bottom — no group heading.
        open_prs = [pr for pr in o.prs if not _is_done(pr)]
        done_prs = [pr for pr in o.prs if _is_done(pr)]
        actual = 0
        for i, pr in enumerate(open_prs):
            if i > 0:
                t.append("\n")
                actual += 1
            # Line 1: icon + number + repo (org prefix stripped — same for
            # every ticket so it just clutters the column)
            pr_prefix = f"{ic.pr} " if ic.pr else ""
            t.append(f"{pr_prefix}#{pr.number}", style="bold")
            if pr.repo:
                t.append(f" {pr.repo.rsplit('/', 1)[-1]}", style="")
            t.append("\n")
            actual += 1

            # Line 2: state + CI + review (with icons)
            status_line = Text()
            state_icon, state_label, state_color = pr_state_display(pr, ic)
            status_line.append(f"{state_icon} {state_label}", style=state_color)

            if pr.ci_status in ("passing", "success"):
                status_line.append(f"  {ic.ci_pass} CI", style="green")
            elif pr.ci_status in ("failing", "failure"):
                status_line.append(f"  {ic.ci_fail} CI", style="red")

            if pr.review_status:
                review_icon, review_color = review_display(pr.review_status, ic)
                status_line.append(f"  {review_icon} {pr.review_status}", style=review_color)

            if len(status_line) > CARD_CONTENT_WIDTH:
                status_line.truncate(CARD_CONTENT_WIDTH - 1)
                status_line.append("\u2026")
            t.append_text(status_line)
            t.append("\n")
            actual += 1

        if done_prs and open_prs:
            t.append("\n")
            actual += 1
        # PRSummary (overview's PR type) has no title/timestamp, so the
        # collapsed line shows the repo — the only at-a-glance identifier here.
        # Fixed-width number column keeps the repos lined up across rows.
        for pr in done_prs:
            state_icon, _label, state_color = pr_state_display(pr, ic)
            line = Text()
            line.append(f"{state_icon} ", style=state_color)
            line.append(f"#{pr.number}".ljust(6), style=state_color)
            line.append("  ")
            repo_short = pr.repo.rsplit("/", 1)[-1] if pr.repo else ""
            line.append(_truncate(repo_short, CARD_CONTENT_WIDTH - 10), style="dim")
            t.append_text(line)
            t.append("\n")
            actual += 1

        for _ in range(h.prs - actual):
            t.append("\n")

    def _render_sessions(self, t: Text, o, h, ic: Icons) -> None:
        # Delegate to render_session_card so the overview matches the Sessions
        # tab and Ticket-tab Summary pane row-for-row. Each card takes 2 lines:
        #   line 1: <mode-icon>  <topic>
        #   line 2: <status-icon>  <status>  ·  <rel>  ·  <cwd>
        # show_ticket=False because the overview card is already scoped to one
        # ticket — rendering the badge again would just waste the row.
        actual = 0
        for session in o.sessions:
            card = render_session_card(
                session,
                icons=ic,
                spinner_frame=0,
                docked_pid=None,
                topic_width=CARD_CONTENT_WIDTH - 4,
                show_ticket=False,
            )
            t.append_text(card)
            actual += 2
        for _ in range(h.sessions - actual):
            t.append("\n")

    def _render_tasks(self, t: Text, o, h, ic: Icons) -> None:
        todo = [task for task in o.tasks if task.status == "todo"]
        done_count = sum(1 for task in o.tasks if task.status == "done")
        total = len(o.tasks)
        actual = 0
        for task in todo:
            line = Text()
            line.append(f"{ic.task_todo} ", style="bright_black")
            line.append(task.description)
            if len(line) > CARD_CONTENT_WIDTH:
                line.truncate(CARD_CONTENT_WIDTH - 1)
                line.append("\u2026")
            t.append_text(line)
            t.append("\n")
            actual += 1
        t.append(f"  ({done_count}/{total} done)", style="dim")
        t.append("\n")
        actual += 1
        for _ in range(h.tasks - actual):
            t.append("\n")

    def _render_pending(self, t: Text, o, h, ic: Icons) -> None:
        actual = 0
        for action in o.pending_actions:
            line = Text()
            line.append("\u2192 ", style="yellow")
            line.append(action.description)
            if len(line) > CARD_CONTENT_WIDTH:
                line.truncate(CARD_CONTENT_WIDTH - 1)
                line.append("\u2026")
            t.append_text(line)
            t.append("\n")
            actual += 1
        for _ in range(h.pending - actual):
            t.append("\n")

    def update_overview(self, overview, section_heights: SectionHeights | None = None, icons: Icons | None = None) -> None:
        self._overview = overview
        if section_heights is not None:
            self._heights = section_heights
        if icons is not None:
            self._icons = icons
        self.refresh()

    def action_select(self) -> None:
        self.post_message(self.Selected(self._overview.key))

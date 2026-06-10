"""Shared pull-request row rendering.

A single canonical icon/color mapping and row renderer used by the overview
ticket card and the full PR tab, so both views read the same at-a-glance.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from rich.console import Group
from rich.table import Table
from rich.text import Text

from duct.models import PullRequest

from duct_tui.icons import Icons
from duct_tui.widgets.ticket_badge import render_ticket_badge


# Strip a leading ticket-key prefix like "ERSC-1278: " / "PS-891 - " / "[PS-12]" /
# "(PS-12)" from PR titles — the badge on line 1 already carries the key.
#
# Accepts:
#   "PS-123: foo"        "PS-123 foo"        "PS-123 - foo"
#   "PS-123 — foo"       "PS-123 – foo"      "PS-123 | foo"
#   "[PS-123] foo"       "[PS-123]: foo"     "(PS-123) foo"
_LEADING_TICKET_RE = re.compile(
    r"^\s*[\[\(]?\s*[A-Z][A-Z0-9]+-\d+\s*[\]\)]?\s*[:\-–—|]?\s+",
)


def pr_state_display(pr: PullRequest, icons: Icons) -> tuple[str, str, str]:
    """Return (icon, label, color) for a PR's state column.

    Drafts render as "draft" regardless of the underlying open state.
    """
    if pr.state == "merged":
        return icons.pr_merged, "merged", "magenta"
    if pr.state == "closed":
        return icons.pr_closed, "closed", "red"
    if pr.is_draft:
        return icons.pr_draft, "draft", "bright_black"
    return icons.pr_open, "open", "green"


def review_display(review_status: str, icons: Icons) -> tuple[str, str]:
    """Return (icon, color) for a review-status column."""
    lower = review_status.lower()
    if "approved" in lower:
        return icons.review_approved, "green"
    if "change" in lower:
        return icons.review_changes, "red"
    return icons.pr_open, "bright_black"


def strip_leading_ticket(title: str) -> str:
    """Remove a leading 'KEY-123:' / 'KEY-123 - ' / '[KEY-123]' from a PR title.

    Leaves titles unchanged when they don't begin with a ticket-key prefix.
    """
    return _LEADING_TICKET_RE.sub("", title, count=1)


def format_relative(iso_timestamp: str) -> str:
    """Return a short relative-time string like '2h ago' / '3d ago'.

    Empty string on parse failure or when the timestamp is missing.
    """
    if not iso_timestamp:
        return ""
    try:
        ts = iso_timestamp.rstrip("Z")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ""

    now = datetime.now(timezone.utc)
    seconds = int((now - dt).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 604800:
        return f"{seconds // 86400}d ago"
    return f"{seconds // 604800}w ago"


def _append_status_line(
    t: Text,
    pr: PullRequest,
    icons: Icons,
    *,
    relative_time_str: str = "",
) -> None:
    """Append state + CI + review + reviewers + time (no indent)."""
    state_icon, state_label, state_color = pr_state_display(pr, icons)
    t.append(f"{state_icon} {state_label}", style=state_color)

    if pr.ci_status in ("passing", "success"):
        t.append(f"  {icons.ci_pass} CI", style="green")
    elif pr.ci_status in ("failing", "failure"):
        t.append(f"  {icons.ci_fail} CI", style="red")

    if pr.review_status and pr.review_status != "pending":
        review_icon, review_color = review_display(pr.review_status, icons)
        t.append(
            f"  {review_icon} {pr.review_status.lower().replace('_', ' ')}",
            style=review_color,
        )
    elif pr.reviewers:
        # Pending with reviewers = in review
        t.append("  in review", style="blue")

    if pr.reviewers:
        names = ", ".join(f"@{r.login}" for r in pr.reviewers)
        t.append(f"  {names}", style="dim")

    if relative_time_str:
        t.append(f"  · {relative_time_str}", style="dim")


def _build_expanded_status_line(
    pr: PullRequest,
    icons: Icons,
    *,
    relative_time_str: str = "",
) -> Text:
    """Third-line of the 3-line layout: state, ci, review, age.

    Reviewers are intentionally omitted — line 2 already has `author`, and
    line 3 is meant to be a tight status strip.
    """
    t = Text()
    state_icon, state_label, state_color = pr_state_display(pr, icons)
    t.append(f"{state_icon} {state_label}", style=state_color)

    if pr.ci_status in ("passing", "success"):
        t.append(f"  {icons.ci_pass} CI", style="green")
    elif pr.ci_status in ("failing", "failure"):
        t.append(f"  {icons.ci_fail} CI", style="red")

    if pr.review_status and pr.review_status != "pending":
        review_icon, review_color = review_display(pr.review_status, icons)
        t.append(
            f"  {review_icon} {pr.review_status.lower().replace('_', ' ')}",
            style=review_color,
        )
    elif pr.reviewers:
        t.append("  in review", style="blue")

    if relative_time_str:
        t.append(f"  · {relative_time_str}", style="dim")

    return t


def _render_compact_row(
    pr: PullRequest,
    icons: Icons,
    *,
    ticket_key: str | None,
    show_author: bool,
    relative_time_str: str,
    action_reasons: tuple[str, ...] | list[str],
    condensed: bool,
) -> Text:
    """Original 2-line layout: title line + status line (+ optional reasons).

    Preserved verbatim so existing non-compact callers (e.g. the overview
    ticket card) keep their current look.
    """
    t = Text()

    if ticket_key is not None:
        t.append_text(render_ticket_badge(ticket_key))
        t.append("  ")
    pr_prefix = f"{icons.pr} " if icons.pr else ""
    t.append(f"{pr_prefix}#{pr.number}", style="bold")
    title = strip_leading_ticket(pr.title)
    if title:
        t.append(f"  {title}")
    if pr.repo:
        t.append(f"  {pr.repo.rsplit('/', 1)[-1]}", style="dim")
    if show_author:
        t.append(f"  @{pr.author}", style="dim")
    t.append("\n")

    if condensed:
        state_icon, state_label, state_color = pr_state_display(pr, icons)
        t.append(f"{state_icon} {state_label}", style=state_color)
        if relative_time_str:
            t.append(f"  · {relative_time_str}", style="dim")
        t.append("\n")
    else:
        _append_status_line(t, pr, icons, relative_time_str=relative_time_str)
        t.append("\n")

    if action_reasons:
        severe = any(r in ("merge conflicts", "CI failing") for r in action_reasons)
        color = "red" if severe else "yellow"
        t.append(f"{icons.warning} {', '.join(action_reasons)}", style=color)
        t.append("\n")

    if t.plain.endswith("\n"):
        t.right_crop(1)
    return t


def _render_expanded_row(
    pr: PullRequest,
    icons: Icons,
    *,
    ticket_key: str | None,
    show_author: bool,
    relative_time_str: str,
    action_reasons: tuple[str, ...] | list[str],
) -> Text:
    """Three-line layout used by PR tab.

        Line 1: [badge]  {pr} #{number}  {title}
        Line 2: {repo}   #{number-alt-hidden}  @{author}
        Line 3: {state}  {ci}  {review}  {time}
        Line 4 (optional): {warning} reasons
    """
    t = Text()

    # Line 1: ticket badge + PR icon + number + title
    if ticket_key is not None:
        t.append_text(render_ticket_badge(ticket_key))
        t.append("  ")
    pr_prefix = f"{icons.pr} " if icons.pr else ""
    t.append(f"{pr_prefix}#{pr.number}", style="bold")
    title = strip_leading_ticket(pr.title)
    if title:
        t.append(f"  {title}")
    t.append("\n")

    # Line 2: repo, hash-number (duplicate kept out — put author here alongside repo)
    repo_short = pr.repo.rsplit("/", 1)[-1] if pr.repo else ""
    if repo_short:
        t.append(repo_short, style="dim")
    if show_author and pr.author:
        if repo_short:
            t.append("  ")
        t.append(f"@{pr.author}", style="dim")
    t.append("\n")

    # Line 3: state + ci + review + age
    t.append_text(_build_expanded_status_line(
        pr, icons, relative_time_str=relative_time_str,
    ))
    t.append("\n")

    if action_reasons:
        severe = any(r in ("merge conflicts", "CI failing") for r in action_reasons)
        color = "red" if severe else "yellow"
        t.append(f"{icons.warning} {', '.join(action_reasons)}", style=color)
        t.append("\n")

    if t.plain.endswith("\n"):
        t.right_crop(1)
    return t


# Fixed column widths for the collapsed single-line layout. Fixing the side
# columns is what lets independently-rendered rows line up: the title column
# (ratio) absorbs the slack identically on every row, so the number, repo and
# time columns sit at the same x on each line.
_COLLAPSED_NUM_W = 8  # "{icon} #12345"
_COLLAPSED_REPO_W = 18
_COLLAPSED_TIME_W = 8


def _fit(value: str, width: int) -> str:
    """Truncate-with-ellipsis then pad `value` to exactly `width` cells."""
    if width <= 0:
        return ""
    if len(value) > width:
        return value[: width - 1] + "…" if width > 1 else "…"
    return value.ljust(width)


def render_collapsed_pr_row(
    pr: PullRequest,
    icons: Icons,
    *,
    relative_time_str: str = "",
) -> Table:
    """Single-line collapsed row for OptionList surfaces (PR tab, PR panel).

    An expanding grid with fixed side columns and a flexible title column, so
    rows line up across separately-rendered options and the time sits flush
    right. The state icon + number carry the state colour (magenta merged /
    red closed); everything else is dim, keeping the row low-importance.
    """
    state_icon, _state_label, state_color = pr_state_display(pr, icons)
    num = Text(f"{state_icon} #{pr.number}", style=state_color)
    title = Text(strip_leading_ticket(pr.title))
    repo = Text(pr.repo.rsplit("/", 1)[-1] if pr.repo else "", style="dim")
    rel = Text(relative_time_str, style="dim")

    table = Table.grid(expand=True, padding=(0, 1, 0, 0))
    table.add_column(width=_COLLAPSED_NUM_W, no_wrap=True)
    table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    table.add_column(width=_COLLAPSED_REPO_W, no_wrap=True, overflow="ellipsis")
    table.add_column(width=_COLLAPSED_TIME_W, no_wrap=True, justify="right")
    table.add_row(num, title, repo, rel)
    return table


def render_collapsed_pr_line(
    pr: PullRequest,
    icons: Icons,
    *,
    relative_time_str: str = "",
    title_width: int,
) -> Text:
    """Single fixed-width line for narrow Text surfaces (overview card, summary
    pane): `{icon} #{num}  {title}  {time}`.

    Columns are space-padded to fixed widths so consecutive rows line up. Repo
    is dropped here — these surfaces are narrow and the title is what matters.
    """
    t = Text()
    state_icon, _state_label, state_color = pr_state_display(pr, icons)
    t.append(f"{state_icon} ", style=state_color)
    t.append(f"#{pr.number}".ljust(6), style=state_color)
    t.append("  ")
    t.append(_fit(strip_leading_ticket(pr.title), title_width))
    if relative_time_str:
        t.append(f"  {relative_time_str}", style="dim")
    return t


def _reviewer_state_display(state: str, icons: Icons) -> tuple[str, str, str]:
    """Return (icon, color, label) for an individual reviewer's review state."""
    upper = state.upper()
    if "APPROVED" in upper:
        return icons.review_approved, "green", "approved"
    if "CHANGE" in upper:
        return icons.review_changes, "red", "changes requested"
    if "COMMENT" in upper:
        return icons.review_commented, "blue", "commented"
    return icons.pr_open, "bright_black", state.lower().replace("_", " ")


# Comment bodies are previewed as a single line; anything longer is noise in a
# list surface — the full thread is one Enter (browser) away.
_COMMENT_PREVIEW_WIDTH = 100
# Ticket-slug branch names routinely run to 80+ chars and would wrap the meta
# line; the leading chars carry all the signal.
_BRANCH_DISPLAY_WIDTH = 40


def _ellipsis(value: str, width: int) -> str:
    """Truncate `value` to `width` cells with a trailing ellipsis."""
    return value if len(value) <= width else value[: width - 1] + "…"


def _comment_preview(body: str) -> str:
    """First non-empty line of a comment body, truncated with an ellipsis."""
    for line in body.strip().splitlines():
        line = line.strip()
        if line:
            return _ellipsis(line, _COMMENT_PREVIEW_WIDTH)
    return ""


def render_pr_card(
    pr: PullRequest,
    icons: Icons,
    *,
    relative_time_str: str = "",
    created_time_str: str = "",
    action_reasons: tuple[str, ...] | list[str] = (),
) -> Text:
    """Full-detail card for the ticket-detail PR pane.

        Line 1: {pr} #{number}  {title}
        Line 2: {repo}  {branch → base}  @{author}              (dim meta)
        Line 3: {state}  {ci}  {review}  {+adds -dels · files}  {ages}
        Block:  reviewers, one per line (actual reviews, then requested)
        Line:   {warning} action reasons
        Block:  the two most recent comments (header + first body line)
    """
    t = Text()

    pr_prefix = f"{icons.pr} " if icons.pr else ""
    t.append(f"{pr_prefix}#{pr.number}", style="bold")
    title = strip_leading_ticket(pr.title)
    if title:
        t.append(f"  {title}")
    t.append("\n")

    repo_short = pr.repo.rsplit("/", 1)[-1] if pr.repo else ""
    branch = _ellipsis(pr.branch, _BRANCH_DISPLAY_WIDTH)
    if branch and pr.base_branch:
        branch += f" → {pr.base_branch}"
    meta_parts = [p for p in (repo_short, branch, f"@{pr.author}" if pr.author else "") if p]
    if meta_parts:
        t.append("  ".join(meta_parts), style="dim")
        t.append("\n")

    state_icon, state_label, state_color = pr_state_display(pr, icons)
    t.append(f"{state_icon} {state_label}", style=state_color)
    if pr.ci_status in ("passing", "success"):
        t.append(f"  {icons.ci_pass} CI", style="green")
    elif pr.ci_status in ("failing", "failure"):
        t.append(f"  {icons.ci_fail} CI", style="red")
    elif pr.ci_status == "pending":
        t.append(f"  {icons.pr_draft} CI pending", style="yellow")
    if pr.review_status and pr.review_status != "pending":
        review_icon, review_color = review_display(pr.review_status, icons)
        t.append(
            f"  {review_icon} {pr.review_status.lower().replace('_', ' ')}",
            style=review_color,
        )
    elif pr.reviewers:
        t.append("  in review", style="blue")
    if pr.additions or pr.deletions:
        t.append(f"  +{pr.additions}", style="green")
        t.append(f" -{pr.deletions}", style="red")
        if pr.changed_files:
            plural = "s" if pr.changed_files != 1 else ""
            t.append(f" · {pr.changed_files} file{plural}", style="dim")
    if relative_time_str:
        t.append(f"  · updated {relative_time_str}", style="dim")
    if created_time_str:
        t.append(f" · opened {created_time_str}", style="dim")
    t.append("\n")

    reviewer_lines: list[Text] = []
    for r in pr.reviewers:
        icon, color, label = _reviewer_state_display(r.state, icons)
        line = Text()
        line.append(f"{icon} ", style=color)
        line.append(f"@{r.login}")
        line.append(f" {label}", style=color)
        reviewer_lines.append(line)
    for name in [*pr.requested_reviewers, *pr.requested_teams]:
        reviewer_lines.append(
            Text(f"{icons.pr_open} @{name} requested", style="bright_black"),
        )
    if reviewer_lines:
        label = "reviewers  "
        for i, line in enumerate(reviewer_lines):
            t.append(label if i == 0 else " " * len(label), style="dim")
            t.append_text(line)
            t.append("\n")

    if action_reasons:
        severe = any(r in ("merge conflicts", "CI failing") for r in action_reasons)
        color = "red" if severe else "yellow"
        t.append(f"{icons.warning} {', '.join(action_reasons)}", style=color)
        t.append("\n")

    for c in sorted(pr.comments, key=lambda c: c.created_at)[-2:]:
        header = f"@{c.author}"
        rel = format_relative(c.created_at)
        if rel:
            header += f" · {rel}"
        if c.path:
            loc = c.path.rsplit("/", 1)[-1]
            if c.line:
                loc += f":{c.line}"
            header += f" · {loc}"
        t.append("┆ ", style="bright_black")
        t.append(header, style="dim")
        t.append("\n")
        preview = _comment_preview(c.body)
        if preview:
            t.append("┆ ", style="bright_black")
            t.append(preview)
            t.append("\n")

    if t.plain.endswith("\n"):
        t.right_crop(1)
    return t


def render_pr_row(
    pr: PullRequest,
    icons: Icons,
    *,
    ticket_key: str | None = None,
    show_author: bool = False,
    relative_time_str: str = "",
    action_reasons: tuple[str, ...] | list[str] = (),
    condensed: bool = False,
    compact: bool = True,
    collapsed: bool = False,
    avatar: object | None = None,
) -> Text | Table:
    """Render a PR as a multi-line rich renderable, no continuation indents.

    - `compact=True` (default): 2-line layout (existing look; preserves
      `condensed` variant). Returns a `rich.text.Text`.
    - `compact=False`: 3-line layout used by the PR tab. Returns `Text` unless
      `avatar` is supplied, in which case a two-column `Table` places the
      avatar to the left of the text block.
    - `collapsed=True` (only meaningful when `compact=False`): single-line
      layout for done PRs. Avatars are ignored.

    `avatar` is any Rich renderable (e.g. a `textual_image` halfcell image or a
    small initials badge) — it's positioned to the left of the row so it reads
    like a chat-style avatar.
    """
    if not compact and collapsed:
        return render_collapsed_pr_row(pr, icons, relative_time_str=relative_time_str)

    if compact:
        return _render_compact_row(
            pr,
            icons,
            ticket_key=ticket_key,
            show_author=show_author,
            relative_time_str=relative_time_str,
            action_reasons=action_reasons,
            condensed=condensed,
        )

    text = _render_expanded_row(
        pr,
        icons,
        ticket_key=ticket_key,
        show_author=show_author,
        relative_time_str=relative_time_str,
        action_reasons=action_reasons,
    )

    if avatar is None:
        return text

    # Two-column layout: avatar (fixed 4 cells) + row text (remaining).
    # `show_lines=False`/`box=None`/`pad_edge=False` keeps it visually like a
    # bare row with a sprite on the left.
    table = Table.grid(padding=(0, 1, 0, 0), expand=True)
    table.add_column(width=4, no_wrap=True)
    table.add_column(ratio=1, overflow="fold")
    table.add_row(Group(avatar), text)
    return table

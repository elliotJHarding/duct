"""Shared workspace repo-card rendering.

Pure functions returning Rich renderables (mirrors ``pr_render``), so the card
layout can be unit-tested without a running app. Used by ``WorkspacePanel`` to
render one card per repo, flowed into responsive columns.
"""

from __future__ import annotations

from rich.columns import Columns
from rich.panel import Panel
from rich.text import Text

from duct.models import RepoStatus

from duct_tui.icons import Icons

# How many recent commit subjects to list under each repo.
_MAX_COMMITS = 5


def _truncate(text: str, width: int) -> str:
    """Ellipsise from the left so the distinctive tail of a path stays visible."""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return "…" + text[-(width - 1):]


def _commit_subject(oneline: str) -> str:
    """Drop the leading short hash from a `git log --oneline` entry."""
    parts = oneline.split(" ", 1)
    return parts[1] if len(parts) == 2 else oneline


def render_repo_card(repo: RepoStatus, icons: Icons, *, width: int) -> Panel:
    """A bordered card: name + status title, branch, changed files, recent commits."""
    inner = max(width - 4, 8)  # account for the panel border + padding

    status_color = "yellow" if repo.dirty else "green"
    status_icon = icons.dirty if repo.dirty else "✓"  # △ / ✓
    counts = (
        f"{repo.uncommitted_changes} changes" if repo.dirty else "clean"
    )
    if repo.recent_commits:
        counts += f" · {len(repo.recent_commits)} commits"

    title = Text()
    title.append(repo.name, style="bold")
    title.append(f"  {status_icon} {counts}", style=status_color)

    body = Text()
    if repo.branch:
        body.append(_truncate(repo.branch, inner), style="blue")
    else:
        body.append("(detached / no branch)", style="dim italic")

    if repo.recent_commits:
        for oneline in repo.recent_commits[:_MAX_COMMITS]:
            body.append("\n")
            body.append(" • ", style="bright_black")  # •
            body.append(_truncate(_commit_subject(oneline), inner - 3), style="dim")
        remaining = len(repo.recent_commits) - _MAX_COMMITS
        if remaining > 0:
            body.append(f"\n +{remaining} more", style="dim")
    else:
        body.append("\n no commits on branch", style="dim")

    return Panel(
        body,
        title=title,
        title_align="left",
        border_style=status_color,
        width=width,
        padding=(0, 1),
    )


def render_repo_columns(
    repos: list[RepoStatus], icons: Icons, *, card_width: int = 72,
) -> Columns:
    """Flow one card per repo into columns that auto-fit and wrap by width."""
    cards = [render_repo_card(r, icons, width=card_width) for r in repos]
    return Columns(cards, equal=True, column_first=True, padding=(0, 1))

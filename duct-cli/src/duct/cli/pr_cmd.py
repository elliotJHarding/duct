"""duct pr -- list and inspect pull requests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import click

from duct.api import get_review_prs
from duct.cli.output import Col, error, output, table
from duct.cli.resolve import resolve_root
from duct.config import ConfigError, load_config
from duct.markdown import parse_frontmatter
from duct.models import PullRequest
from duct.pr import derive_status_label, load_ticket_prs, style_status_label
from duct.workspace import enumerate_ticket_dirs, resolve_ticket_dir


def _read_ticket_status(ticket_dir: Path) -> str:
    """Read the Jira status from a ticket's TICKET.md."""
    ticket_md = ticket_dir / "orchestrator" / "TICKET.md"
    if not ticket_md.exists():
        return ""
    content = ticket_md.read_text(encoding="utf-8")
    _meta, body = parse_frontmatter(content)
    # Extract from metadata table
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("| Status |"):
            parts = stripped.split("|")
            if len(parts) >= 3:
                return parts[2].strip()
    return ""


@click.group()
@click.pass_context
def pr(ctx: click.Context) -> None:
    """List and inspect pull requests."""
    pass


@pr.command("list")
@click.argument("key", required=False, default=None)
@click.option("--all", "show_all", is_flag=True, help="Show PRs for all tickets, not just focused.")
@click.option("--closed", "show_closed", is_flag=True, help="Include terminal-status tickets.")
@click.option(
    "--state", "state_filter", default=None,
    type=click.Choice(["open", "merged", "closed"]),
    help="Filter PRs by state.",
)
@click.pass_context
def pr_list(
    ctx: click.Context,
    key: str | None,
    show_all: bool,
    show_closed: bool,
    state_filter: str | None,
) -> None:
    """List pull requests for tracked tickets.

    Shows PRs for focus-status tickets by default, or for a single ticket
    when KEY is given. Use --all to include non-focus tickets, --closed to
    include terminal-status tickets.
    """
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    # Single-ticket mode
    if key:
        ticket_dir = resolve_ticket_dir(root, key)
        if ticket_dir is None:
            error(f"Ticket {key} not found.")
            ctx.exit(1)
            return
        prs = load_ticket_prs(ticket_dir)
        if state_filter:
            prs = [p for p in prs if p.state == state_filter]
        _render_pr_table(prs, state_filter, single_ticket=True)
        return

    # Multi-ticket mode
    cfg = load_config(root)
    focus_statuses = set(cfg.status.focus_statuses)
    terminal_statuses = set(cfg.status.terminal_statuses)

    tickets = enumerate_ticket_dirs(root)
    if not tickets:
        output("No tracked tickets.", data=[])
        return

    rows_data: list[dict] = []
    for ticket_key, ticket_dir in tickets:
        # Filter tickets by status
        status = _read_ticket_status(ticket_dir).lower()
        if show_closed:
            pass
        elif show_all:
            if status in terminal_statuses:
                continue
        else:
            if status not in focus_statuses:
                continue

        for p in load_ticket_prs(ticket_dir):
            if state_filter and p.state != state_filter:
                continue
            label = derive_status_label(p)
            rows_data.append({
                "ticket": ticket_key,
                "number": p.number,
                "repo": p.repo,
                "title": p.title,
                "status": label,
                "state": p.state,
                "url": p.url,
            })

    _render_pr_table_rows(rows_data, single_ticket=False)


def _render_pr_table(prs: list, state_filter: str | None, single_ticket: bool) -> None:
    """Render a list of PullRequest models as a table."""
    rows_data = []
    for p in prs:
        label = derive_status_label(p)
        rows_data.append({
            "ticket": "",
            "number": p.number,
            "repo": p.repo,
            "title": p.title,
            "status": label,
            "state": p.state,
            "url": p.url,
        })
    _render_pr_table_rows(rows_data, single_ticket=single_ticket)


def _render_pr_table_rows(rows_data: list[dict], single_ticket: bool) -> None:
    """Render pre-built row dicts as a table or JSON."""
    if not rows_data:
        output("No pull requests found.", data=[])
        return

    columns: list[str | Col] = []
    if not single_ticket:
        columns.append(Col("Ticket", no_wrap=True))
    columns.extend([
        Col("#", justify="right", no_wrap=True),
        Col("Repo", no_wrap=True),
        Col("Title", max_width=50),
        "Status",
    ])

    rows: list[list[str]] = []
    for entry in rows_data:
        styled = style_status_label(entry["status"])
        row: list[str] = []
        if not single_ticket:
            row.append(entry["ticket"])
        row.extend([
            str(entry["number"]),
            entry["repo"],
            entry["title"],
            styled,
        ])
        rows.append(row)

    table("Pull Requests", columns, rows, data=rows_data)


class _ReviewGroup(click.Group):
    """`pr review`: ``list`` shows the queue; a PR number deep-reviews that PR."""

    def resolve_command(self, ctx, args):
        # `duct pr review <#>` → deep-review that PR (keep the number as the arg).
        if args and args[0].isdigit():
            return pr_review_deep.name, pr_review_deep, args
        return super().resolve_command(ctx, args)


@pr.group("review", cls=_ReviewGroup, invoke_without_command=True)
@click.pass_context
def pr_review(ctx: click.Context) -> None:
    """List PRs needing review, or deep-review one by number.

    \b
    duct pr review list   show PRs awaiting your review
    duct pr review <#>    check out PR #<#> locally and open it in IntelliJ
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(pr_review_list)


@pr_review.command("list")
@click.pass_context
def pr_review_list(ctx: click.Context) -> None:
    """List pull requests that need your review.

    Includes PRs GitHub has requested from you personally and from teams you
    belong to, regardless of whether they match a tracked ticket.
    """
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    prs = get_review_prs(root)
    if not prs:
        output("No pull requests awaiting your review.", data=[])
        return

    rows_data = [
        {
            "number": p.number,
            "repo": p.repo,
            "title": p.title,
            "author": p.author,
            "status": derive_status_label(p),
            "ci": p.ci_status,
            "mergeable": p.mergeable,
            "why": ", ".join(f"@{t}" for t in p.requested_teams) or "you",
            "age": _age(p.updated_at),
            "url": p.url,
        }
        for p in prs
    ]

    columns: list[str | Col] = [
        Col("#", justify="right", no_wrap=True),
        Col("Repo", no_wrap=True),
        Col("Title", max_width=44),
        Col("Author", no_wrap=True),
        "Status",
        Col("CI", no_wrap=True),
        Col("Why", no_wrap=True),
        Col("Age", justify="right", no_wrap=True),
    ]
    rows = [
        [
            str(e["number"]),
            e["repo"],
            e["title"],
            f"@{e['author']}",
            _review_status(e["status"], e["mergeable"]),
            e["ci"],
            e["why"],
            e["age"],
        ]
        for e in rows_data
    ]
    table("Awaiting Your Review", columns, rows, data=rows_data)


@pr.command("open")
@click.argument("number", type=int)
@click.pass_context
def pr_open(ctx: click.Context, number: int) -> None:
    """Open a pull request in the browser.

    Searches tracked tickets and your review queue for a PR matching NUMBER.
    """
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    pr_match = _find_pr(root, number)
    if pr_match is None:
        error(f"PR #{number} not found in any tracked ticket or your review queue.")
        ctx.exit(1)
        return

    if ctx.obj and ctx.obj.get("json"):
        output("", data={"number": pr_match.number, "url": pr_match.url})
    else:
        click.launch(pr_match.url)
        output(f"Opened {pr_match.url}")


@click.command("deep-review")
@click.argument("number", type=int)
@click.pass_context
def pr_review_deep(ctx: click.Context, number: int) -> None:
    """Check out a PR's branch locally and open it in IntelliJ.

    Clones the repo if needed, fetches and checks out the PR's head branch,
    then launches IntelliJ on the working copy. Searches tracked tickets and
    your review queue for a PR matching NUMBER.
    """
    from duct.review import open_in_intellij, prepare_local_review

    try:
        root = resolve_root(ctx)
        cfg = load_config(root)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    pr_match = _find_pr(root, number)
    if pr_match is None:
        error(f"PR #{number} not found in any tracked ticket or your review queue.")
        ctx.exit(1)
        return

    try:
        repo_path = prepare_local_review(cfg, pr_match)
        open_in_intellij(repo_path)
    except RuntimeError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    output(f"Opened {repo_path} in IntelliJ", data={"path": str(repo_path)})


def _find_pr(root: Path, number: int) -> PullRequest | None:
    """Locate a PR by number across tracked tickets and the review queue."""
    for _key, ticket_dir in enumerate_ticket_dirs(root):
        for p in load_ticket_prs(ticket_dir):
            if p.number == number:
                return p
    for p in get_review_prs(root):
        if p.number == number:
            return p
    return None


def _review_status(label: str, mergeable: str) -> str:
    """Status label for the review table, flagging merge conflicts."""
    if mergeable == "CONFLICTING":
        return f"{style_status_label(label)} [red](conflicts)[/red]"
    return style_status_label(label)


def _age(updated_at: str) -> str:
    """Compact 'time since' label from an ISO 8601 timestamp."""
    if not updated_at:
        return ""
    try:
        then = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    delta = datetime.now(timezone.utc) - then
    secs = int(delta.total_seconds())
    if secs < 3600:
        return f"{max(secs // 60, 0)}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"

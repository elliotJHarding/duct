"""duct ticket -- list and show tracked tickets."""

from __future__ import annotations

import re
from pathlib import Path

import click

from duct.cli.output import Col, error, kv, output, section, table
from duct.cli.resolve import resolve_root
from duct.config import ConfigError, load_config
from duct.markdown import extract_table, parse_frontmatter
from duct.workspace import enumerate_ticket_dirs, read_priority_keys, resolve_ticket_dir


def _parse_ticket_md(content: str) -> dict[str, str]:
    """Extract metadata from a TICKET.md file.

    Returns a dict with keys: key, summary, status, category, plus
    any other fields found in the metadata table.
    """
    _meta, body = parse_frontmatter(content)
    info: dict[str, str] = {}

    # Extract key and summary from the first H1 heading: "# KEY: Summary"
    for line in body.splitlines():
        line = line.strip()
        m = re.match(r"^#\s+([A-Z]+-\d+):\s+(.+)$", line)
        if m:
            info["key"] = m.group(1)
            info["summary"] = m.group(2)
            break

    # Extract fields from the metadata table (Field | Value columns).
    rows = extract_table(body)
    for row in rows:
        field_name = row.get("Field", "").strip()
        value = row.get("Value", "").strip()
        if field_name and value:
            info[field_name.lower()] = value

    return info


@click.group()
@click.pass_context
def ticket(ctx: click.Context) -> None:
    """List and inspect tracked tickets."""
    pass


@ticket.command("list")
@click.option(
    "--category", default=None,
    help="Filter by workflow category (e.g. 'Active Development').",
)
@click.option("--status", "status_filter", default=None, help="Filter by Jira status.")
@click.option(
    "--sort",
    "sort_by",
    default=None,
    type=click.Choice(["priority", "key", "status", "category"]),
    help="Sort results.",
)
@click.pass_context
def ticket_list(
    ctx: click.Context,
    category: str | None,
    status_filter: str | None,
    sort_by: str | None,
) -> None:
    """List all tracked tickets."""
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    tickets = enumerate_ticket_dirs(root)
    if not tickets:
        output("No tracked tickets.", data=[])
        return

    # Gather ticket info
    entries: list[dict[str, str]] = []
    for key, path in tickets:
        ticket_md = path / "orchestrator" / "TICKET.md"
        info: dict[str, str] = {}
        if ticket_md.exists():
            try:
                info = _parse_ticket_md(ticket_md.read_text(encoding="utf-8"))
            except Exception:
                pass

        entries.append({
            "key": info.get("key", key),
            "summary": info.get("summary", ""),
            "status": info.get("status", ""),
            "category": info.get("category", ""),
            "path": str(path),
        })

    # Filter
    if category:
        cat_lower = category.lower()
        entries = [e for e in entries if cat_lower in e["category"].lower()]
    if status_filter:
        st_lower = status_filter.lower()
        entries = [e for e in entries if st_lower in e["status"].lower()]

    # Sort
    if sort_by == "priority":
        priority_keys = read_priority_keys(root)
        priority_map = {k: i for i, k in enumerate(priority_keys)}
        entries.sort(key=lambda e: priority_map.get(e["key"], len(priority_keys)))
    elif sort_by == "key":
        entries.sort(key=lambda e: e["key"])
    elif sort_by == "status":
        entries.sort(key=lambda e: e["status"])
    elif sort_by == "category":
        entries.sort(key=lambda e: e["category"])

    if not entries:
        output("No tickets match the filter.", data=[])
        return

    columns: list[str | Col] = [
        Col("Key", no_wrap=True),
        Col("Summary", max_width=50),
        "Status",
        "Category",
    ]
    rows = [[e["key"], e["summary"], e["status"], e["category"]] for e in entries]
    table("Tracked Tickets", columns, rows, data=entries)


@ticket.command("open")
@click.argument("key")
@click.pass_context
def ticket_open(ctx: click.Context, key: str) -> None:
    """Open a ticket's Jira page in the browser."""
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    config = load_config(root)
    if not config.jira_domain:
        error("jira.domain is not configured. Set it in config.yaml.")
        ctx.exit(1)
        return

    ticket_dir = resolve_ticket_dir(root, key)
    if ticket_dir is None:
        error(f"Ticket {key} not found.")
        ctx.exit(1)
        return

    url = f"https://{config.jira_domain}/browse/{key}"

    if ctx.obj and ctx.obj.get("json"):
        output("", data={"key": key, "url": url})
    else:
        click.launch(url)
        output(f"Opened {url}")


@ticket.command("show")
@click.argument("key")
@click.pass_context
def ticket_show(ctx: click.Context, key: str) -> None:
    """Show ticket details and artifact inventory."""
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    ticket_dir = resolve_ticket_dir(root, key)
    if ticket_dir is None:
        error(f"Ticket {key} not found.")
        ctx.exit(1)
        return

    orch_dir = ticket_dir / "orchestrator"
    ticket_md = orch_dir / "TICKET.md"

    # Read TICKET.md content.
    ticket_content = ""
    if ticket_md.exists():
        ticket_content = ticket_md.read_text(encoding="utf-8")

    # Build artifact inventory (files in orchestrator/).
    artifacts: list[str] = []
    if orch_dir.is_dir():
        for f in sorted(orch_dir.iterdir()):
            if f.is_file():
                artifacts.append(f.name)

    # Identify repo worktrees (dirs in ticket_dir that are not orchestrator/).
    repos: list[str] = []
    for child in sorted(ticket_dir.iterdir()):
        if child.is_dir() and child.name != "orchestrator":
            repos.append(child.name)

    json_data = {
        "key": key,
        "path": str(ticket_dir),
        "ticket_md": ticket_content,
        "artifacts": artifacts,
        "repos": repos,
    }

    if ctx.obj and ctx.obj.get("json"):
        output("", data=json_data)
    else:
        if ticket_content:
            _meta, body = parse_frontmatter(ticket_content)
            info = _parse_ticket_md(ticket_content)

            # Heading -- strip the markdown H1 prefix
            heading = info.get("key", key)
            summary = info.get("summary", "")
            if summary:
                heading = f"{heading}: {summary}"
            output(f"[bold]{heading}[/bold]")
            output("")

            # Metadata as aligned key-value pairs
            for field in ("status", "category", "priority", "type", "assignee"):
                val = info.get(field, "")
                if val:
                    kv(field.capitalize(), val)

            # Description body -- strip heading and metadata table
            desc_lines: list[str] = []
            in_table = False
            past_heading = False
            for line in body.splitlines():
                stripped = line.strip()
                if not past_heading and re.match(r"^#\s+[A-Z]+-\d+:", stripped):
                    past_heading = True
                    continue
                if stripped.startswith("| ") or stripped.startswith("|---"):
                    in_table = True
                    continue
                if in_table and not stripped.startswith("|"):
                    in_table = False
                if not in_table and past_heading:
                    desc_lines.append(line)

            desc = "\n".join(desc_lines).strip()
            if desc:
                output("")
                output(desc)
        else:
            output(f"[dim]No TICKET.md found for {key}.[/dim]")

        if artifacts:
            output("")
            section("Artifacts")
            for a in artifacts:
                output(f"  {a}")
        else:
            output("\n[dim]No artifacts.[/dim]")

        if repos:
            output("")
            section("Repo worktrees")
            for r in repos:
                output(f"  {r}")

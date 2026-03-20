"""duct status — unified dashboard showing priority-ordered tickets with context."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import click

from duct.cli.output import Col, error, output, table
from duct.cli.resolve import resolve_root
from duct.config import ConfigError
from duct.markdown import extract_table, parse_frontmatter
from duct.workspace import enumerate_ticket_dirs, read_priority_keys


def _parse_ticket_md(content: str) -> dict[str, str]:
    """Extract metadata from a TICKET.md file."""
    _meta, body = parse_frontmatter(content)
    info: dict[str, str] = {}

    for line in body.splitlines():
        line = line.strip()
        m = re.match(r"^#\s+([A-Z]+-\d+):\s+(.+)$", line)
        if m:
            info["key"] = m.group(1)
            info["summary"] = m.group(2)
            break

    rows = extract_table(body)
    for row in rows:
        field_name = row.get("Field", "").strip()
        value = row.get("Value", "").strip()
        if field_name and value:
            info[field_name.lower()] = value

    return info


def _count_prs(ticket_dir: Path) -> tuple[int, str]:
    """Count PRs and extract CI status from PULL_REQUESTS.md."""
    pr_md = ticket_dir / "orchestrator" / "PULL_REQUESTS.md"
    if not pr_md.exists():
        return 0, ""

    content = pr_md.read_text(encoding="utf-8")
    pr_count = len(re.findall(r"^## #\d+", content, re.MULTILINE))

    # Extract CI statuses
    ci_statuses = re.findall(r"\*\*CI\*\*:\s*(\S+)", content)
    if not ci_statuses:
        ci_summary = ""
    elif all(s == "passing" for s in ci_statuses):
        ci_summary = "passing"
    elif any(s == "failing" for s in ci_statuses):
        ci_summary = "failing"
    else:
        ci_summary = "mixed"

    return pr_count, ci_summary


def _count_active_sessions(ticket_dir: Path) -> int:
    """Count active sessions from CLAUDE_SESSIONS.md."""
    sessions_md = ticket_dir / "orchestrator" / "CLAUDE_SESSIONS.md"
    if not sessions_md.exists():
        return 0
    content = sessions_md.read_text(encoding="utf-8")
    # Count PID headings under Active section
    in_active = False
    count = 0
    for line in content.splitlines():
        if line.strip() == "## Active":
            in_active = True
        elif line.startswith("## ") and in_active:
            break
        elif in_active and line.startswith("### PID"):
            count += 1
    return count


def _check_dirty_repos(ticket_dir: Path) -> int:
    """Count repos with uncommitted changes from cached WORKSPACE.md.

    Falls back to live git status if WORKSPACE.md is not available.
    """
    workspace_md = ticket_dir / "orchestrator" / "WORKSPACE.md"
    if workspace_md.exists():
        try:
            content = workspace_md.read_text(encoding="utf-8")
            # Count repos marked as dirty in WORKSPACE.md
            return content.count("**Status**: dirty")
        except Exception:
            pass

    # Fallback: live git status
    dirty = 0
    for child in ticket_dir.iterdir():
        if not child.is_dir() or child.name == "orchestrator":
            continue
        if not (child / ".git").exists():
            continue
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=child,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.stdout.strip():
                dirty += 1
        except Exception:
            pass
    return dirty


def _sync_age(ticket_dir: Path) -> str:
    """Get the age of the last sync from TICKET.md frontmatter."""
    ticket_md = ticket_dir / "orchestrator" / "TICKET.md"
    if not ticket_md.exists():
        return "none"
    try:
        meta, _ = parse_frontmatter(ticket_md.read_text(encoding="utf-8"))
        synced_at = meta.get("syncedAt", "")
        if not synced_at:
            return "unknown"
        import time
        from datetime import datetime, timezone
        dt = datetime.strptime(synced_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = time.time() - dt.timestamp()
        if age < 60:
            return f"{int(age)}s"
        if age < 3600:
            return f"{int(age / 60)}m"
        return f"{age / 3600:.1f}h"
    except Exception:
        return "?"


_TERMINAL_STATUSES = {"closed", "done"}
_FOCUS_STATUSES = {"in progress", "analysis started"}


@click.command()
@click.option("--all", "show_all", is_flag=True, help="Show all tickets except Closed/Done.")
@click.option("--closed", "show_closed", is_flag=True, help="Include Closed and Done tickets.")
@click.pass_context
def status(ctx: click.Context, show_all: bool, show_closed: bool) -> None:
    """Show a unified dashboard of all tracked work.

    Default: focused view (In Progress and Analysis Started only).
    """
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    tickets = enumerate_ticket_dirs(root)
    if not tickets:
        output("No tracked tickets. Run 'duct sync --force' to get started.")
        return

    # Build priority ordering
    priority_keys = read_priority_keys(root)
    priority_map = {k: i for i, k in enumerate(priority_keys)}

    entries: list[dict] = []
    for key, path in tickets:
        ticket_md = path / "orchestrator" / "TICKET.md"
        info: dict[str, str] = {}
        if ticket_md.exists():
            try:
                info = _parse_ticket_md(ticket_md.read_text(encoding="utf-8"))
            except Exception:
                pass

        pr_count, ci_status = _count_prs(path)
        sessions = _count_active_sessions(path)
        dirty = _check_dirty_repos(path)
        age = _sync_age(path)

        pri_pos = priority_map.get(key)
        pri_str = f"#{pri_pos + 1}" if pri_pos is not None else ""

        entries.append({
            "key": info.get("key", key),
            "summary": info.get("summary", ""),
            "status": info.get("status", ""),
            "category": info.get("category", ""),
            "priority_position": pri_pos if pri_pos is not None else len(priority_keys),
            "priority": pri_str,
            "prs": pr_count,
            "ci": ci_status,
            "sessions": sessions,
            "dirty_repos": dirty,
            "sync_age": age,
            "path": str(path),
        })

    # Sort by priority position (prioritized tickets first, then the rest)
    entries.sort(key=lambda e: e["priority_position"])

    # Filter by status
    if show_closed:
        pass  # show everything
    elif show_all:
        entries = [e for e in entries if e["status"].lower() not in _TERMINAL_STATUSES]
    else:
        entries = [e for e in entries if e["status"].lower() in _FOCUS_STATUSES]

    if not entries:
        output("No tickets match the current filter.")
        return

    columns: list[str | Col] = [
        Col("Pri", justify="right"),
        "Key",
        "Status",
        Col("Category", max_width=20),
        Col("PRs", justify="right"),
        "CI",
        Col("Sessions", justify="right"),
        Col("Dirty", justify="right"),
        Col("Sync", justify="right"),
    ]
    rows: list[list[str]] = []
    for e in entries:
        # CI coloring
        ci_raw = e["ci"] or "-"
        if ci_raw == "passing":
            ci_str = "[green]passing[/green]"
        elif ci_raw == "failing":
            ci_str = "[red]failing[/red]"
        elif ci_raw == "mixed":
            ci_str = "[yellow]mixed[/yellow]"
        else:
            ci_str = ci_raw

        # Sessions coloring
        if e["sessions"] > 0:
            sessions_str = f"[green]{e['sessions']}[/green]"
        else:
            sessions_str = "-"

        # Dirty coloring
        if e["dirty_repos"] > 0:
            dirty_str = f"[yellow]{e['dirty_repos']}[/yellow]"
        else:
            dirty_str = "-"

        # Sync age coloring
        sync_raw = e["sync_age"]
        if sync_raw.endswith("h"):
            try:
                hours = float(sync_raw[:-1])
                if hours >= 2:
                    sync_str = f"[red]{sync_raw}[/red]"
                elif hours >= 1:
                    sync_str = f"[yellow]{sync_raw}[/yellow]"
                else:
                    sync_str = sync_raw
            except ValueError:
                sync_str = sync_raw
        else:
            sync_str = sync_raw

        rows.append([
            e["priority"],
            e["key"],
            e["status"],
            e["category"],
            str(e["prs"]) if e["prs"] > 0 else "-",
            ci_str,
            sessions_str,
            dirty_str,
            sync_str,
        ])

    table("duct Status", columns, rows, data=entries)

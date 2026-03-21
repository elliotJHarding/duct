"""duct archive -- manage archived tickets."""

from __future__ import annotations

import re
from pathlib import Path

import click

from duct.cli.output import error, output, success, table
from duct.cli.resolve import resolve_root
from duct.config import ConfigError
from duct.markdown import TICKET_KEY_PATTERN
from duct.workspace import archive_ticket, resolve_ticket_dir, restore_ticket


def _list_archived(ctx: click.Context) -> None:
    """List all archived tickets."""
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    archive_dir = root / ".archive"
    if not archive_dir.is_dir():
        output("No archived tickets.", data=[])
        return

    entries: list[tuple[str, Path]] = []
    for child in sorted(archive_dir.iterdir()):
        if not child.is_dir():
            continue
        m = re.match(rf"^({TICKET_KEY_PATTERN.pattern})-", child.name)
        if m:
            entries.append((m.group(1), child))

    if not entries:
        output("No archived tickets.", data=[])
        return

    columns = ["Key", "Directory"]
    rows = [[key, path.name] for key, path in entries]
    json_data = [{"key": key, "path": str(path)} for key, path in entries]

    table("Archived Tickets", columns, rows, data=json_data)


@click.group(invoke_without_command=True)
@click.pass_context
def archive(ctx: click.Context) -> None:
    """List and manage archived tickets."""
    if ctx.invoked_subcommand is None:
        _list_archived(ctx)


@archive.command("list")
@click.pass_context
def archive_list(ctx: click.Context) -> None:
    """List archived tickets."""
    _list_archived(ctx)


@archive.command("add")
@click.argument("key")
@click.pass_context
def archive_add(ctx: click.Context, key: str) -> None:
    """Archive a ticket (move it to .archive/)."""
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    ticket_dir = resolve_ticket_dir(root, key)
    if not ticket_dir:
        error(f"Ticket {key} not found in workspace.")
        ctx.exit(1)
        return

    result = archive_ticket(root, key)
    if result is None:
        error(f"Failed to archive {key}.")
        ctx.exit(1)
        return

    success(f"Archived {key} to {result}")


@archive.command("restore")
@click.argument("key")
@click.pass_context
def archive_restore(ctx: click.Context, key: str) -> None:
    """Restore an archived ticket to the workspace."""
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    result = restore_ticket(root, key)
    if result is None:
        error(f"Ticket {key} not found in archive.")
        ctx.exit(1)
        return

    success(f"Restored {key} to {result}")

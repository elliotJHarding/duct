"""duct priority -- view and edit PRIORITY.md."""

from __future__ import annotations

import click

from duct.cli.output import error, output, success, warn
from duct.cli.resolve import resolve_root
from duct.config import ConfigError
from duct.workspace import read_priority_keys


@click.group(invoke_without_command=True)
@click.pass_context
def priority(ctx: click.Context) -> None:
    """View or edit the priority list."""
    if ctx.invoked_subcommand is not None:
        return

    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    priority_file = root / "PRIORITY.md"
    if not priority_file.exists():
        output("No PRIORITY.md found. Run 'duct init' first.")
        return

    content = priority_file.read_text()
    output(content, data={"content": content})


@priority.command("add")
@click.argument("key")
@click.argument("note", nargs=-1)
@click.pass_context
def priority_add(ctx: click.Context, key: str, note: tuple[str, ...]) -> None:
    """Add a ticket to the priority list.

    Appends KEY (with optional NOTE) to the end of PRIORITY.md.
    """
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    priority_file = root / "PRIORITY.md"
    keys = read_priority_keys(root)

    if key in keys:
        warn(f"{key} is already in the priority list (position {keys.index(key) + 1}).")
        return

    line = f"- {key}"
    if note:
        line += f" — {' '.join(note)}"

    # Append to file, ensuring we start on a new line.
    existing = ""
    if priority_file.exists():
        existing = priority_file.read_text()
    if existing and not existing.endswith("\n"):
        existing += "\n"
    priority_file.write_text(existing + line + "\n", encoding="utf-8")
    success(f"Added {key} to priority list.")

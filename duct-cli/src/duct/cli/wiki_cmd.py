"""duct wiki — inspect and maintain the workspace wiki."""

from __future__ import annotations

from pathlib import Path

import click

from duct.cli.output import Col, error, output, success, syntax, table, warn
from duct.cli.resolve import resolve_root
from duct.config import ConfigError, load_config
from duct.wiki import index_path, list_entries, read_entry, spawn_maintainer, wiki_dir


def _warn_if_disabled(root: Path) -> None:
    """One-line notice when the wiki is off — commands still read what's on disk."""
    if not load_config(root).wiki.enabled:
        warn(
            "The workspace wiki is disabled — enable it via `duct setup` "
            "or wiki.enabled in toolkit/config.yaml."
        )


@click.group()
@click.pass_context
def wiki(ctx: click.Context) -> None:
    """Inspect and maintain the workspace wiki."""
    pass


@wiki.command("list")
@click.pass_context
def wiki_list(ctx: click.Context) -> None:
    """List wiki entries."""
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    entries = list_entries(root)
    if not entries:
        output(
            f"No wiki entries. The wiki lives at {wiki_dir(root)}.",
            data=[],
        )
        _warn_if_disabled(root)
        return

    rows = [[e.name, e.type, e.description or "-"] for e in entries]
    json_data = [
        {"name": e.name, "type": e.type, "description": e.description, "tags": list(e.tags)}
        for e in entries
    ]
    table(
        "Wiki entries",
        [Col("Name", no_wrap=True), "Type", "Description"],
        rows,
        data=json_data,
    )
    _warn_if_disabled(root)


@wiki.command("show")
@click.argument("name")
@click.pass_context
def wiki_show(ctx: click.Context, name: str) -> None:
    """Show a wiki entry by name."""
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    _warn_if_disabled(root)
    entry = read_entry(root, name)
    if entry is None:
        error(f"Wiki entry '{name}' not found. Run 'duct wiki list' to see available entries.")
        ctx.exit(1)
        return

    content = entry.path.read_text(encoding="utf-8")
    if "---\n" in content:
        # Split frontmatter from body for nicer formatting in rich mode.
        # parse_frontmatter is already used at parse time; here we just
        # syntax-highlight the raw frontmatter and print the body.
        head, _, body = content.partition("---\n")
        head2, _, body = body.partition("---\n")
        syntax(head2.strip(), lexer="yaml")
        output(body.lstrip("\n"), data={"name": entry.name, "content": content})
    else:
        output(content, data={"name": entry.name, "content": content})


@wiki.command("review")
@click.pass_context
def wiki_review(ctx: click.Context) -> None:
    """Run the wiki-maintainer subagent to dedupe and prune the wiki."""
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    _warn_if_disabled(root)
    success(f"Launching wiki-maintainer in {root}")
    try:
        code = spawn_maintainer(root)
    except FileNotFoundError as exc:
        error(str(exc))
        ctx.exit(1)
        return
    except KeyboardInterrupt:
        output("Maintainer interrupted.")
        return

    if code != 0:
        error(f"wiki-maintainer exited with code {code}")
        ctx.exit(code)


__all__ = ["wiki", "index_path"]

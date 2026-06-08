"""Shared CLI utility: workspace root resolution and completion cache."""

from __future__ import annotations

from pathlib import Path

import click

from duct.config import ConfigError, find_workspace_root
from duct.global_state import load_state


def resolve_root(ctx: click.Context) -> Path:
    """Determine the workspace root.

    Priority:
      1. ``--workspace-root`` on the command line (kept as an escape hatch
         for tests, CI, and one-off scripts).
      2. ``workspace_path`` in ``~/.config/duct/state.yaml`` — the path the
         setup flow recorded for the user.
      3. Walking up from the current working directory looking for
         ``config.yaml`` — back-compat for users who pre-date the setup flow.

    Raises ``ConfigError`` when none of those produce a directory.
    """
    override = ctx.obj.get("workspace_root") if ctx.obj else None
    if override:
        if override.startswith("--"):
            raise click.UsageError(
                f"--workspace-root value {override!r} starts with '--'; "
                "a misplaced flag was likely consumed as its value."
            )
        resolved = Path(override).resolve()
        if not resolved.is_dir():
            raise click.UsageError(
                f"--workspace-root {override!r} does not exist or is not a directory."
            )
        return resolved

    state = load_state()
    if state.workspace_path and (state.workspace_path / "config.yaml").exists():
        return state.workspace_path

    return find_workspace_root()


def require_setup(ctx: click.Context) -> Path:
    """Return the workspace root or exit with a clear "run `duct`" hint.

    Subcommands that need a usable workspace call this on entry. The guard
    deliberately does *not* fall back to interactive prompts — those belong
    to the bare ``duct`` entry point so scripts and pipelines never block on
    a TTY.
    """
    try:
        root = resolve_root(ctx)
    except (ConfigError, click.UsageError) as exc:
        click.echo(
            "duct is not set up — run `duct` to complete setup.",
            err=True,
        )
        click.echo(f"  ({exc})", err=True)
        ctx.exit(1)
        raise SystemExit(1) from exc  # pragma: no cover — keeps type-checkers happy

    if not (root / "config.yaml").exists():
        click.echo(
            "duct is not set up — run `duct` to complete setup.",
            err=True,
        )
        ctx.exit(1)
        raise SystemExit(1)  # pragma: no cover
    return root


def write_repo_completion_cache(root: Path, repo_names: list[str]) -> None:
    """Write repo names to .cache/completions/repos.txt for shell completion."""
    cache_dir = root / ".cache" / "completions"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "repos.txt"
    cache_file.write_text("\n".join(sorted(repo_names)) + "\n", encoding="utf-8")

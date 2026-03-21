"""Shared CLI utility: workspace root resolution and completion cache."""

from __future__ import annotations

from pathlib import Path

import click

from duct.config import find_workspace_root


def resolve_root(ctx: click.Context) -> Path:
    """Determine workspace root from context or by walking up the filesystem."""
    root = ctx.obj.get("workspace_root") if ctx.obj else None
    if root:
        return Path(root).resolve()
    return find_workspace_root()


def write_repo_completion_cache(root: Path, repo_names: list[str]) -> None:
    """Write repo names to .cache/completions/repos.txt for shell completion."""
    cache_dir = root / ".cache" / "completions"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "repos.txt"
    cache_file.write_text("\n".join(sorted(repo_names)) + "\n", encoding="utf-8")

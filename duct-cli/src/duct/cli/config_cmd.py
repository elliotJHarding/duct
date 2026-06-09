"""duct config — view or edit workspace configuration."""

from __future__ import annotations

import click
import yaml

from duct import paths
from duct.cli.output import error, success, syntax
from duct.cli.resolve import resolve_root
from duct.config import (
    ConfigError,
    WorkspaceConfig,
    _sync_intervals_to_dict,
    load_config,
)


def _config_to_full_dict(cfg: WorkspaceConfig) -> dict:
    """Convert the full config to a display dict including trust and intervals."""
    return {
        "root": str(cfg.root),
        "jira_domain": cfg.jira_domain,
        "jira_jql": cfg.jira_jql,
        "repo_paths": [str(p) for p in cfg.repo_paths],
        "sync_intervals": _sync_intervals_to_dict(cfg.sync_intervals),
    }


@click.group(invoke_without_command=True)
@click.pass_context
def config(ctx: click.Context) -> None:
    """View or edit workspace configuration."""
    if ctx.invoked_subcommand is not None:
        return

    try:
        root = resolve_root(ctx)
        cfg = load_config(root)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    data = _config_to_full_dict(cfg)

    json_mode = ctx.obj.get("json", False) if ctx.obj else False
    if json_mode:
        import json
        import sys

        json.dump(data, sys.stdout)
        sys.stdout.write("\n")
    else:
        syntax(yaml.dump(data, default_flow_style=False, sort_keys=False), "yaml")


# Dotted key path -> (yaml section, yaml key) mapping for config set.
_SETTABLE_KEYS = {
    "jira.domain": ("jira", "domain"),
    "jira.jql": ("jira", "jql"),
    "syncIntervals.jira": ("syncIntervals", "jira"),
    "syncIntervals.github": ("syncIntervals", "github"),
    "syncIntervals.sessions": ("syncIntervals", "sessions"),
    "syncIntervals.workspace": ("syncIntervals", "workspace"),
    "syncIntervals.ci": ("syncIntervals", "ci"),
    "display.nerdFont": ("display", "nerdFont"),
    "syncIntervals.activity": ("syncIntervals", "activity"),
    "activity.outlookPdfPath": ("activity", "outlookPdfPath"),
}


@config.command("set")
@click.argument("key")
@click.argument("value")
@click.pass_context
def config_set(ctx: click.Context, key: str, value: str) -> None:
    """Set a configuration value using dotted key paths.

    Examples:
        duct config set jira.domain mycompany.atlassian.net
        duct config set trust.gitCommit auto
        duct config set syncIntervals.jira 7200
    """
    if key not in _SETTABLE_KEYS:
        valid = ", ".join(sorted(_SETTABLE_KEYS))
        error(f"Unknown config key '{key}'. Valid keys: {valid}")
        ctx.exit(1)
        return

    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    # Read raw YAML to preserve structure
    config_path = paths.config_file(root)
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}
    else:
        raw = {}

    section, yaml_key = _SETTABLE_KEYS[key]

    # Validate interval values
    if section == "syncIntervals":
        try:
            value = int(value)  # type: ignore[assignment]
        except ValueError:
            error(f"Interval value must be an integer (seconds), got '{value}'")
            ctx.exit(1)
            return

    # Convert boolean values
    if section == "display":
        if value.lower() in ("true", "1", "yes"):
            value = True  # type: ignore[assignment]
        elif value.lower() in ("false", "0", "no"):
            value = False  # type: ignore[assignment]
        else:
            error(f"Boolean value expected (true/false), got '{value}'")
            ctx.exit(1)
            return

    # Set the value
    if section not in raw:
        raw[section] = {}
    raw[section][yaml_key] = value

    config_path.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))
    success(f"{key} = {value}")


@config.command("add-repo-path")
@click.argument("path")
@click.pass_context
def config_add_repo_path(ctx: click.Context, path: str) -> None:
    """Add a directory to the repoPaths list.

    Examples:
        duct config add-repo-path ~/workspace
        duct config add-repo-path /opt/repos
    """
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    from pathlib import Path as P
    resolved = str(P(path).expanduser().resolve())

    config_path = paths.config_file(root)
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}
    else:
        raw = {}

    repo_paths = raw.get("repoPaths", [])
    # Normalise existing entries for comparison
    existing = [str(P(p).expanduser().resolve()) for p in repo_paths]
    if resolved in existing:
        error(f"'{path}' is already in repoPaths.")
        ctx.exit(1)
        return

    repo_paths.append(path)
    raw["repoPaths"] = repo_paths
    config_path.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))
    success(f"Added '{path}' to repoPaths. Current: {', '.join(repo_paths)}")


@config.command("remove-repo-path")
@click.argument("path")
@click.pass_context
def config_remove_repo_path(ctx: click.Context, path: str) -> None:
    """Remove a directory from the repoPaths list.

    Examples:
        duct config remove-repo-path ~/projects
    """
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    from pathlib import Path as P
    resolved = str(P(path).expanduser().resolve())

    config_path = paths.config_file(root)
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}
    else:
        raw = {}

    repo_paths = raw.get("repoPaths", [])
    # Find and remove by resolved path comparison
    new_paths = []
    removed = False
    for p in repo_paths:
        if str(P(p).expanduser().resolve()) == resolved:
            removed = True
        else:
            new_paths.append(p)

    if not removed:
        error(f"'{path}' not found in repoPaths. Current: {', '.join(repo_paths)}")
        ctx.exit(1)
        return

    raw["repoPaths"] = new_paths
    config_path.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))
    if new_paths:
        success(f"Removed '{path}' from repoPaths. Remaining: {', '.join(new_paths)}")
    else:
        success(f"Removed '{path}' from repoPaths. List is now empty.")

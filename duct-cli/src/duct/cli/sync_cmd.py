"""duct sync — run sync sources."""

from __future__ import annotations

import click

from duct.cli.output import error, output, spinner, success, update_spinner, warn
from duct.cli.resolve import resolve_root
from duct.cli.setup_core import build_sync_sources, sync_intervals
from duct.config import (
    AuthError,
    ConfigError,
    gh_token,
    jira_email,
    jira_token,
    load_config,
)
from duct.sync.base import SyncCoordinator


def _refresh_repo_completion_cache(root, cfg) -> None:
    """Rebuild .cache/completions/repos.txt from configured repo paths."""
    try:
        from duct.cli.resolve import write_repo_completion_cache
        from duct.cli.workspace_cmd import discover_repos

        names = [name for name, _ in discover_repos(cfg)]
        write_repo_completion_cache(root, names)
    except Exception:
        pass  # Best-effort; don't break sync on cache failure


def _report_result(r):
    """Print a single sync result as it completes."""
    if r.errors:
        error_count = len(r.errors)
        ok_count = r.tickets_synced
        if ok_count > 0:
            warn(
                f"{r.source}: synced {ok_count} tickets, "
                f"{error_count} errors in {r.duration_seconds:.1f}s"
            )
        else:
            error(f"{r.source}: {', '.join(r.errors)}")
    else:
        success(f"{r.source}: synced {r.tickets_synced} tickets in {r.duration_seconds:.1f}s")


@click.group(invoke_without_command=True)
@click.option("--force", is_flag=True, help="Bypass staleness checks.")
@click.pass_context
def sync(ctx: click.Context, force: bool) -> None:
    """Run sync sources. Without a subcommand, runs all sources."""
    ctx.ensure_object(dict)
    ctx.obj["force"] = force

    if ctx.invoked_subcommand is not None:
        return

    try:
        root = resolve_root(ctx)
        cfg = load_config(root)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    coordinator = SyncCoordinator(root, sync_intervals(cfg))
    sources, skipped = build_sync_sources(cfg)
    for name, reason in skipped:
        warn(f"{name}: skipped ({reason})")

    with spinner("Syncing...") as status:
        def on_start(name: str) -> None:
            update_spinner(status, f"Syncing {name}...")

        def on_result(r) -> None:
            update_spinner(status, f"Syncing... ({r.source} done)")

        results = coordinator.run(
            sources, force=force, on_result=on_result, on_start=on_start
        )

    _refresh_repo_completion_cache(root, cfg)

    if not results:
        # Show when each source was last synced
        statuses = coordinator.all_source_statuses()
        parts = [f"{s.name}: {s.age_human}" for s in statuses if s.last_sync > 0]
        if parts:
            output(f"Nothing to sync (all sources up to date). Last synced: {', '.join(parts)}")
        else:
            output("Nothing to sync (all sources up to date).")
    else:
        for r in results:
            _report_result(r)

    total_synced = sum(r.tickets_synced for r in results)
    if total_synced == 0 and skipped:
        ctx.exit(1)


def _run_single_source(ctx, source_factory):
    """Helper to run a single sync source."""
    force = ctx.obj.get("force", False)
    try:
        root = resolve_root(ctx)
        cfg = load_config(root)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    try:
        source = source_factory(cfg)
    except AuthError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    coordinator = SyncCoordinator(root, {source.name: 0})

    with spinner(f"Syncing {source.name}..."):
        results = coordinator.run([source], force=force)

    _refresh_repo_completion_cache(root, cfg)

    for r in results:
        _report_result(r)


@sync.command("jira")
@click.pass_context
def sync_jira(ctx: click.Context) -> None:
    """Sync Jira tickets."""
    from duct.sync.jira import JiraSync

    def factory(cfg):
        return JiraSync(
            domain=cfg.jira_domain,
            email=jira_email(),
            token=jira_token(),
            jql=cfg.jira_jql,
            sandbox=cfg.sandbox,
        )

    _run_single_source(ctx, factory)


@sync.command("github")
@click.pass_context
def sync_github(ctx: click.Context) -> None:
    """Sync GitHub pull requests."""
    from duct.config import github_username
    from duct.sync.github import GitHubSync

    def factory(cfg):
        return GitHubSync(token=gh_token(), github_username=github_username())

    _run_single_source(ctx, factory)


@sync.command("ci")
@click.pass_context
def sync_ci(ctx: click.Context) -> None:
    """Sync CI/build status."""
    from duct.sync.ci import CISync

    def factory(_cfg):
        return CISync()

    _run_single_source(ctx, factory)


@sync.command("sessions")
@click.pass_context
def sync_sessions(ctx: click.Context) -> None:
    """Sync Claude session data."""
    from duct.sync.sessions import SessionSync

    def factory(_cfg):
        return SessionSync()

    _run_single_source(ctx, factory)


@sync.command("workspace")
@click.pass_context
def sync_workspace(ctx: click.Context) -> None:
    """Sync local workspace state."""
    from duct.sync.workspace_sync import WorkspaceSync

    def factory(_cfg):
        return WorkspaceSync()

    _run_single_source(ctx, factory)


@sync.command("claude-md")
@click.pass_context
def sync_claude_md(ctx: click.Context) -> None:
    """Refresh per-ticket CLAUDE.md files."""
    from duct.sync.claude_md import ClaudeMdSync

    def factory(_cfg):
        return ClaudeMdSync(wiki_enabled=_cfg.wiki.enabled)

    _run_single_source(ctx, factory)


@sync.command("status")
@click.pass_context
def sync_status(ctx: click.Context) -> None:
    """Show last sync time and staleness per source."""
    try:
        root = resolve_root(ctx)
        cfg = load_config(root)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    coordinator = SyncCoordinator(root, sync_intervals(cfg))
    statuses = coordinator.all_source_statuses()

    from duct.cli.output import Col, table
    columns: list[str | Col] = [
        "Source",
        "Last Sync",
        Col("Interval", justify="right"),
        "Stale",
    ]
    rows = []
    json_data = []
    for s in statuses:
        if s.interval < 60:
            interval_human = f"{s.interval}s"
        elif s.interval < 3600:
            interval_human = f"{s.interval // 60}m"
        else:
            interval_human = f"{s.interval / 3600:.1f}h"
        stale_str = "[red]yes[/red]" if s.is_stale else "[green]no[/green]"
        rows.append([s.name, s.age_human, interval_human, stale_str])
        json_data.append({
            "source": s.name,
            "last_sync": s.last_sync_iso,
            "last_sync_epoch": s.last_sync,
            "interval_seconds": s.interval,
            "is_stale": s.is_stale,
            "age_seconds": s.age_seconds if s.last_sync > 0 else None,
        })

    table("Sync Status", columns, rows, data=json_data)

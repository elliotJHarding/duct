"""Hidden one-time migration to the toolkit/.duct workspace layout.

Relocates a pre-restructure workspace (config + knowledge loose at the root,
runtime state spread across ``.runs/``/``.activity/``/loose dotfiles, and home
state across ``~/.duct``/``~/.cache/duct``) onto the consolidated layout: a
tracked ``toolkit/`` git repo, a single ``.duct/`` state dir, and a single
``~/.config/duct`` home dir.

Hard migrate — no permanent runtime fallback (single-user tool, matching the
``migrate_legacy_credentials`` precedent). Dry-run by default; ``--apply`` moves.
Hidden from ``duct --help`` (see HIDDEN_COMMANDS in main.py) but scriptable.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import click

from duct import daemon_state, paths, run_lock
from duct.cli.init_cmd import ensure_toolkit_repo, materialise_root_claude
from duct.cli.output import error, output, success, warn
from duct.global_state import load_state

# A live daemon younger than this (seconds) blocks migration — it would write to
# old locations mid-move. The user must `duct daemon uninstall` first.
_DAEMON_FRESH_SECONDS = 120.0


def _detect_root(ctx: click.Context) -> Path | None:
    """Resolve the workspace root WITHOUT the new sentinel.

    ``resolve_root`` keys off ``toolkit/config.yaml``, which a pre-migration
    workspace does not have, so we resolve from the explicit override, the
    state.yaml pointer, or cwd instead.
    """
    override = ctx.obj.get("workspace_root") if ctx.obj else None
    if override:
        return Path(override).expanduser().resolve()
    state = load_state()
    if state.workspace_path:
        return state.workspace_path
    cwd = Path.cwd()
    # Accept cwd only if it looks like a workspace under either layout.
    if (cwd / "config.yaml").exists() or paths.is_workspace(cwd):
        return cwd
    return None


def planned_workspace_moves(root: Path) -> list[tuple[Path, Path]]:
    """(src, dst) pairs for relocating workspace files. Only existing srcs."""
    tk = paths.toolkit_dir(root)
    candidates: list[tuple[Path, Path]] = [
        # A — config + knowledge into toolkit/
        (root / "config.yaml", paths.config_file(root)),
        (root / "WORKFLOW.md", paths.workflow_md(root)),
        (root / "wiki", paths.wiki_dir(root)),
        (root / "agents", paths.agents_dir(root)),
        (root / "settings.template.json", paths.settings_template(root)),
        (root / ".claude" / "CLAUDE.md", paths.toolkit_claude_md(root)),
        # D — runtime state into .duct/
        (root / ".runs", paths.runs_dir(root)),
        (root / ".activity", paths.activity_dir(root)),
        (root / ".sync_state.yaml", paths.sync_state_file(root)),
        (root / ".review_prs.md", paths.review_prs_file(root)),
        (root / ".actions.yaml", paths.workspace_actions_file(root)),
        (root / ".cache", paths.cache_dir(root)),
    ]
    # Wiki subagents: root .claude/agents/wiki-*.md → toolkit/subagents/
    old_agents = root / ".claude" / "agents"
    if old_agents.is_dir():
        for src in sorted(old_agents.glob("wiki-*.md")):
            candidates.append((src, paths.subagents_dir(root) / src.name))

    _ = tk  # toolkit dir is created lazily by the move loop
    return [(s, d) for s, d in candidates if s.exists() and not d.exists()]


def planned_home_moves() -> list[tuple[Path, Path]]:
    """(src, dst) pairs for consolidating home state into ~/.config/duct."""
    home = Path.home()
    candidates: list[tuple[Path, Path]] = [
        (home / ".duct" / "perf.jsonl", paths.perf_log()),
        (home / ".duct" / "pane-status-misses.log", paths.pane_status_trace()),
        (home / ".cache" / "duct" / "avatars", paths.avatars_cache_dir()),
        (home / ".cache" / "duct" / "mermaid", paths.mermaid_cache_dir()),
        # daemon pid/logs previously sat directly in ~/.config/duct
        (paths.home_dir() / "daemon.pid", paths.daemon_pidfile()),
        (paths.home_dir() / "daemon.log", paths.daemon_log()),
        (paths.home_dir() / "daemon.err.log", paths.daemon_errlog()),
    ]
    return [(s, d) for s, d in candidates if s.exists() and not d.exists()]


def _apply_moves(moves: list[tuple[Path, Path]]) -> None:
    for src, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))


def migrate_workspace_layout(root: Path, *, apply: bool) -> list[tuple[Path, Path]]:
    """Relocate workspace files and (when applying) rebuild toolkit + .claude.

    Returns the moves that were planned/applied. Idempotent: a workspace already
    on the new layout yields no moves.
    """
    moves = planned_workspace_moves(root)
    if apply:
        paths.toolkit_dir(root).mkdir(parents=True, exist_ok=True)
        _apply_moves(moves)
        ensure_toolkit_repo(root)
        materialise_root_claude(root)
        # Rewrite every ticket's CLAUDE.md so @../toolkit/... imports resolve.
        from duct.sync.claude_md import ClaudeMdSync

        ClaudeMdSync().sync(root)
    return moves


@click.command("migrate-layout", hidden=True)
@click.option("--apply", "apply_", is_flag=True, help="Perform the migration (default: dry-run).")
@click.pass_context
def migrate_layout(ctx: click.Context, apply_: bool) -> None:
    """Migrate a pre-restructure workspace to the toolkit/.duct layout (one-time)."""
    root = _detect_root(ctx)
    if root is None or not root.is_dir():
        error("Could not locate a workspace. Pass --workspace-root or run from inside one.")
        ctx.exit(1)
        return

    already = paths.is_workspace(root) and not (root / "config.yaml").exists()
    if already and not planned_workspace_moves(root):
        success(f"{root} is already on the new layout — nothing to do.")
        return

    ws_moves = planned_workspace_moves(root)
    home_moves = planned_home_moves()

    # Dry-run writes nothing, so it is always safe — preview before the guard.
    if not apply_:
        output(f"Dry run — planned moves for {root} (pass --apply to perform):")
        for src, dst in ws_moves + home_moves:
            output(f"  {src}  ->  {dst}")
        if not (ws_moves or home_moves):
            output("  (nothing to move)")
        output("\nApplying requires the daemon to be stopped first: `duct daemon uninstall`.")
        output("After --apply, reinstall it: `duct daemon install`.")
        return

    # Daemon guard (apply only): a live daemon would race the move. KeepAlive=True
    # means a plain stop is relaunched, so require a full uninstall.
    age = daemon_state.heartbeat_age_seconds(root)
    if run_lock.is_locked(root) or (age is not None and age < _DAEMON_FRESH_SECONDS):
        error(
            "The daemon appears to be running (fresh heartbeat or active run lock).\n"
            "  Run `duct daemon uninstall` first, then re-run this migration."
        )
        ctx.exit(1)
        return

    migrate_workspace_layout(root, apply=True)
    _apply_moves(home_moves)
    # Drop the now-empty legacy ~/.duct if we emptied it.
    legacy_duct = Path.home() / ".duct"
    if legacy_duct.is_dir() and not any(legacy_duct.iterdir()):
        legacy_duct.rmdir()

    success(f"Migrated {root} to the toolkit/.duct layout.")
    warn("Reinstall the daemon to pick up the new paths: `duct daemon install`.")

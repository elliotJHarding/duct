"""duct orchestrate — launch an orchestrator Claude Code session."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import click

from duct.cli.output import error, output, spinner, success
from duct.cli.resolve import resolve_root
from duct.config import ConfigError, load_config
from duct.orchestrator import (
    ALLOWED_TOOLS,
    RunRecorder,
    build_prompt,
    format_stream_event,
)


@click.command()
@click.option("--ticket", "ticket_key", default=None, help="Focus on a specific ticket.")
@click.option("--dry-run", is_flag=True, help="Print the command without executing.")
@click.option("--sync", "pre_sync", is_flag=True, help="Run sync before launching orchestrator.")
@click.option("--skip-permissions", is_flag=True, help="Pass --dangerously-skip-permissions (requires sandbox).")
@click.option("--verbose", "-v", is_flag=True, help="Stream orchestrator activity to the terminal.")
@click.pass_context
def orchestrate(ctx: click.Context, ticket_key: str | None, dry_run: bool, pre_sync: bool, skip_permissions: bool, verbose: bool) -> None:
    """Launch an orchestrator Claude Code session."""
    try:
        root = resolve_root(ctx)
        cfg = load_config(root)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    use_skip_permissions = skip_permissions or cfg.sandbox.skip_permissions

    if use_skip_permissions and not cfg.sandbox.enabled:
        error("--skip-permissions requires sandbox to be enabled. Set sandbox.enabled in config.yaml.")
        ctx.exit(1)
        return

    # Ensure sandbox config at workspace root
    if cfg.sandbox.enabled:
        from duct.sandbox import write_settings

        write_settings(root, cfg.sandbox)

    # Optional pre-flight sync
    if pre_sync:
        from duct.cli.sync_cmd import _build_all_sources, _report_result
        from duct.sync.base import SyncCoordinator

        intervals = {
            "jira": cfg.sync_intervals.jira,
            "github": cfg.sync_intervals.github,
            "sessions": cfg.sync_intervals.sessions,
            "workspace": cfg.sync_intervals.workspace,
            "ci": cfg.sync_intervals.ci,
        }
        coordinator = SyncCoordinator(root, intervals)
        sources, skipped = _build_all_sources(cfg)

        with spinner("Pre-flight sync..."):
            results = coordinator.run(sources, force=False)

        if results:
            for r in results:
                _report_result(r)
        else:
            output("All sources up to date.")

    # Verify claude is available.
    claude_bin = shutil.which("claude")
    if not claude_bin:
        error("'claude' CLI not found on PATH. Install Claude Code first.")
        ctx.exit(1)
        return

    allowed_tools = ALLOWED_TOOLS
    prompt = build_prompt(ticket_key, cfg.orchestrator.fork_model)

    cmd = [
        claude_bin,
        "--add-dir", str(root),
        "-p", prompt,
        "--allowedTools", ",".join(allowed_tools),
    ]

    # Always stream NDJSON internally so we can record a run log.
    # --verbose just toggles whether formatted events are echoed to stdout.
    cmd.extend(["--verbose", "--output-format", "stream-json"])

    if use_skip_permissions:
        cmd.append("--dangerously-skip-permissions")

    if dry_run:
        output(" ".join(cmd), data={"command": cmd})
        return

    success(f"Launching orchestrator session (tools: {', '.join(allowed_tools)})")

    recorder = RunRecorder(root, ticket_key=ticket_key)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
        )
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            recorder.record(raw_line)
            if verbose:
                formatted = format_stream_event(raw_line)
                if formatted:
                    output(formatted)
        proc.wait()
        path = recorder.finalize(proc.returncode)
        success(f"Run log: {path}")
    except KeyboardInterrupt:
        output("Orchestrator session interrupted.")
        recorder.finalize(returncode=-1)
    except Exception as exc:
        error(f"Failed to launch orchestrator: {exc}")
        ctx.exit(1)

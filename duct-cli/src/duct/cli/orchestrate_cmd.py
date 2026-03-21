"""duct orchestrate — launch an orchestrator Claude Code session."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import click

from duct.cli.output import error, output, spinner, success
from duct.cli.resolve import resolve_root
from duct.config import ConfigError, load_config
from duct.prompts import load_prompt

_ALLOWED_TOOLS = ["Read", "Glob", "Grep", "Write", "Edit", "Bash"]


def _build_prompt(ticket_key: str | None) -> str:
    """Build the -p prompt for the orchestrator session."""
    ticket_focus = f"\nFocus this session on ticket {ticket_key}." if ticket_key else ""
    return load_prompt("orchestrator", ticket_focus=ticket_focus)


def _format_tool_use(content_block: dict) -> str | None:
    """Format a tool_use content block into a concise one-liner."""
    name = content_block.get("name", "")
    inp = content_block.get("input", {})

    # Pick the most informative input field for common tools.
    detail = ""
    if name in ("Read", "Write", "Edit"):
        detail = inp.get("file_path", "")
    elif name == "Glob":
        detail = inp.get("pattern", "")
    elif name == "Grep":
        pattern = inp.get("pattern", "")
        path = inp.get("path", "")
        detail = f"{pattern}" + (f" in {path}" if path else "")
    elif name == "Bash":
        cmd = inp.get("command", "")
        detail = cmd[:80] + ("..." if len(cmd) > 80 else "")
    else:
        # Generic: show first string value
        for v in inp.values():
            if isinstance(v, str):
                detail = v[:80]
                break

    return f"[dim]  [tool][/dim] {name}  {detail}"


def _format_stream_event(line: str) -> str | None:
    """Parse one NDJSON line and return a formatted string, or None to skip."""
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    etype = event.get("type")

    if etype == "system" and event.get("subtype") == "init":
        model = event.get("model", "unknown")
        return f"[dim]  [init] model={model}[/dim]"

    if etype == "assistant":
        contents = event.get("message", {}).get("content", [])
        parts: list[str] = []
        for block in contents:
            btype = block.get("type")
            if btype == "tool_use":
                formatted = _format_tool_use(block)
                if formatted:
                    parts.append(formatted)
            elif btype == "text":
                text = block.get("text", "").strip()
                if text:
                    if len(text) > 200:
                        text = text[:200] + "..."
                    parts.append(f"  [text] {text}")
        if parts:
            return "\n".join(parts)

    if etype == "result":
        duration = event.get("duration_seconds", 0)
        cost = event.get("cost_usd", 0)
        turns = event.get("num_turns", 0)
        return f"[bold]  [done] {turns} turns, {duration:.1f}s, ${cost:.2f}[/bold]"

    return None


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

    allowed_tools = _ALLOWED_TOOLS
    prompt = _build_prompt(ticket_key)

    cmd = [
        claude_bin,
        "--add-dir", str(root),
        "-p", prompt,
        "--allowedTools", ",".join(allowed_tools),
    ]

    if verbose:
        cmd.extend(["--verbose", "--output-format", "stream-json"])

    if use_skip_permissions:
        cmd.append("--dangerously-skip-permissions")

    if dry_run:
        output(" ".join(cmd), data={"command": cmd})
        return

    success(f"Launching orchestrator session (tools: {', '.join(allowed_tools)})")

    if verbose:
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
                formatted = _format_stream_event(raw_line)
                if formatted:
                    output(formatted)
            proc.wait()
        except KeyboardInterrupt:
            output("Orchestrator session interrupted.")
        except Exception as exc:
            error(f"Failed to launch orchestrator: {exc}")
            ctx.exit(1)
    else:
        try:
            subprocess.run(cmd, cwd=str(root), check=False)
        except KeyboardInterrupt:
            output("Orchestrator session interrupted.")
        except Exception as exc:
            error(f"Failed to launch orchestrator: {exc}")
            ctx.exit(1)

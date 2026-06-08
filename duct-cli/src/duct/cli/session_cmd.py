"""duct session -- view and manage Claude Code sessions."""

from __future__ import annotations

import shutil
import subprocess

import click
from duct.cli.output import Col, error, kv, output, section, success, table
from duct.cli.resolve import resolve_root
from duct.config import ConfigError, load_config
from duct.session import apply_recency_decoration, discover_sessions, match_session_ticket
from duct.terminal import focus_terminal_tab, get_tty
from duct.workspace import enumerate_ticket_dirs, resolve_ticket_dir


_STATUS_STYLES = {
    "ready": "[cyan]ready[/cyan]",
    "done": "[green]done[/green]",
    "stale": "[dim]stale[/dim]",
    "waiting": "[yellow]waiting[/yellow]",
    "working": "[blue]working[/blue]",
    "terminated": "[dim]terminated[/dim]",
}


@click.group()
@click.pass_context
def session(ctx: click.Context) -> None:
    """View and manage Claude Code sessions."""
    pass


@session.command("list")
@click.option("--all", "show_all", is_flag=True, help="Show terminated sessions too.")
@click.pass_context
def session_list(ctx: click.Context, show_all: bool) -> None:
    """List Claude Code sessions with ticket mapping."""
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    ticket_keys = {key for key, _ in enumerate_ticket_dirs(root)}
    sessions = discover_sessions()
    cfg = load_config(root)
    apply_recency_decoration(
        sessions,
        done_window_seconds=cfg.session_status.done_window_seconds,
        stale_after_seconds=cfg.session_status.stale_after_seconds,
    )

    if not show_all:
        sessions = [s for s in sessions if s.get("alive")]

    if not sessions:
        msg = "No active sessions." if not show_all else "No sessions found."
        output(msg, data=[])
        return

    # Attach ticket to each session and group by ticket
    enriched: list[tuple[str, dict]] = []
    for s in sessions:
        ticket = match_session_ticket(s, ticket_keys) or "-"
        enriched.append((ticket, s))

    # Sort so sessions with the same ticket are adjacent, unmatched ("-") last
    enriched.sort(key=lambda t: (t[0] == "-", t[0]))

    columns: list[str | Col] = [
        "Status",
        Col("PID", justify="right"),
        "Ticket",
        "Topic",
        Col("Session ID", no_wrap=True),
    ]
    rows: list[list[str]] = []
    sections: list[int] = []
    json_data: dict[str, list[dict]] = {}

    prev_ticket: str | None = None
    for ticket, s in enriched:
        if prev_ticket is not None and ticket != prev_ticket:
            sections.append(len(rows))
        prev_ticket = ticket

        pid_str = str(s.get("pid", "-")) if s.get("pid") else "-"
        raw_status = s.get("status", "?")
        status_str = _STATUS_STYLES.get(raw_status, raw_status)
        topic = (s.get("topic", "") or "")[:50]
        sid = s.get("session_id", "")[:12]

        rows.append([status_str, pid_str, ticket, topic, sid])
        json_data.setdefault(ticket, []).append({
            "session_id": s.get("session_id", ""),
            "pid": s.get("pid"),
            "status": raw_status,
            "ticket": ticket,
            "topic": s.get("topic", ""),
            "cwd": s.get("cwd", ""),
            "started_at": s.get("started_at", ""),
            "last_activity": s.get("last_activity", ""),
        })

    table("Claude Sessions", columns, rows, data=json_data, sections=sections)


@session.command("show")
@click.argument("session_id")
@click.pass_context
def session_show(ctx: click.Context, session_id: str) -> None:
    """Show details for a specific session."""
    sessions = discover_sessions()

    # Find by prefix match
    matches = [s for s in sessions if s.get("session_id", "").startswith(session_id)]

    if not matches:
        error(f"No session found matching '{session_id}'.")
        ctx.exit(1)
        return

    if len(matches) > 1:
        error(
            f"Ambiguous session ID '{session_id}' -- "
            f"matches {len(matches)} sessions. Be more specific."
        )
        ctx.exit(1)
        return

    s = matches[0]

    try:
        root = resolve_root(ctx)
        ticket_keys = {key for key, _ in enumerate_ticket_dirs(root)}
        ticket = match_session_ticket(s, ticket_keys) or "none"
    except Exception:
        ticket = "unknown"

    json_data = {
        "session_id": s.get("session_id", ""),
        "pid": s.get("pid"),
        "status": s.get("status", ""),
        "ticket": ticket,
        "cwd": s.get("cwd", ""),
        "topic": s.get("topic", ""),
        "started_at": s.get("started_at", ""),
        "last_activity": s.get("last_activity", ""),
        "recent_messages": s.get("recent_messages", []),
    }

    if ctx.obj and ctx.obj.get("json"):
        output("", data=json_data)
        return

    raw_status = s.get("status", "?")
    status_display = _STATUS_STYLES.get(raw_status, raw_status)

    kv("Session", s.get("session_id", ""))
    kv("Status", status_display)
    if s.get("pid"):
        kv("PID", str(s["pid"]))
    kv("Ticket", ticket)
    kv("CWD", s.get("cwd", ""))
    if s.get("topic"):
        kv("Topic", s["topic"])
    if s.get("started_at"):
        kv("Started", s["started_at"])
    if s.get("last_activity"):
        kv("Last Activity", s["last_activity"], width=16)

    messages = s.get("recent_messages", [])
    if messages:
        output("")
        section("Recent Messages")
        for msg in messages:
            role = msg.get("role", "?")
            text = msg.get("text", "")
            if text:
                output(f"  [dim]{role}:[/dim] {text}")


@session.command("start", context_settings=dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
    allow_interspersed_args=False,
))
@click.argument("key")
@click.option("--prompt", "-p", default=None, help="Initial prompt for the session.")
@click.option("--repo", "-r", default=None, help="Start session in a specific repo worktree.")
@click.option("--skip-permissions", is_flag=True, help="Pass --dangerously-skip-permissions (requires sandbox).")
@click.argument("claude_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def session_start(
    ctx: click.Context, key: str, prompt: str | None, repo: str | None,
    skip_permissions: bool, claude_args: tuple[str, ...],
) -> None:
    """Launch a Claude Code session focused on a specific ticket.

    Extra arguments after -- are passed through to the claude CLI.
    """
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    ticket_dir = resolve_ticket_dir(root, key)
    if not ticket_dir:
        error(f"No workspace found for {key}. Run 'duct sync --force' to create ticket directories.")
        ctx.exit(1)
        return

    claude_bin = shutil.which("claude")
    if not claude_bin:
        error("'claude' CLI not found on PATH. Install Claude Code first.")
        ctx.exit(1)
        return

    # Load config for sandbox settings
    cfg = load_config(root)
    use_skip_permissions = skip_permissions or cfg.sandbox.skip_permissions

    if use_skip_permissions and not cfg.sandbox.enabled:
        error("--skip-permissions requires sandbox to be enabled. Set sandbox.enabled in config.yaml.")
        ctx.exit(1)
        return

    # Determine working directory
    cwd = ticket_dir
    if repo:
        repo_dir = ticket_dir / repo
        if not repo_dir.is_dir() or not (repo_dir / ".git").exists():
            available = [
                d.name for d in sorted(ticket_dir.iterdir())
                if d.is_dir() and d.name != "orchestrator" and (d / ".git").exists()
            ]
            msg = f"Repo worktree '{repo}' not found in {key}."
            if available:
                msg += f" Available: {', '.join(available)}"
            error(msg)
            ctx.exit(1)
            return
        cwd = repo_dir

    # Build context-aware prompt
    orch_dir = ticket_dir / "orchestrator"
    context_parts: list[str] = []
    context_parts.append(f"You are working on ticket {key}.")
    context_parts.append("")
    context_parts.append(f"Read {orch_dir / 'TICKET.md'} for ticket details.")

    # List available artifacts
    if orch_dir.is_dir():
        artifacts = [f.name for f in sorted(orch_dir.iterdir()) if f.is_file()]
        if artifacts:
            context_parts.append(f"Available artifacts in orchestrator/: {', '.join(artifacts)}")

    # List available repos
    repos = [
        d.name for d in sorted(ticket_dir.iterdir())
        if d.is_dir() and d.name != "orchestrator" and (d / ".git").exists()
    ]
    if repos:
        context_parts.append(f"Repo worktrees: {', '.join(repos)}")

    if prompt:
        context_parts.append("")
        context_parts.append(prompt)

    full_prompt = "\n".join(context_parts)

    # Ensure sandbox config in working directory
    if cfg.sandbox.enabled:
        from duct.sandbox import write_settings

        write_settings(cwd, cfg.sandbox)

    cmd = [claude_bin, "--add-dir", str(ticket_dir)]
    if use_skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if cfg.session.extra_args:
        cmd.extend(cfg.session.extra_args)
    if prompt:
        cmd.extend(["-p", full_prompt])
    if claude_args:
        cmd.extend(claude_args)

    success(f"Starting session for {key} in {cwd}")

    try:
        subprocess.run(cmd, cwd=str(cwd), check=False)
    except KeyboardInterrupt:
        output("Session interrupted.")
    except Exception as exc:
        error(f"Failed to launch session: {exc}")
        ctx.exit(1)


@session.command("jump")
@click.argument("session_id")
@click.pass_context
def session_jump(ctx: click.Context, session_id: str) -> None:
    """Jump to the terminal tab running a session."""
    sessions = discover_sessions()

    matches = [s for s in sessions if s.get("session_id", "").startswith(session_id)]

    if not matches:
        error(f"No session found matching '{session_id}'.")
        ctx.exit(1)
        return

    if len(matches) > 1:
        error(
            f"Ambiguous session ID '{session_id}' -- "
            f"matches {len(matches)} sessions. Be more specific."
        )
        ctx.exit(1)
        return

    s = matches[0]

    if not s.get("alive"):
        error(f"Session {s['session_id'][:12]} is not running.")
        ctx.exit(1)
        return

    pid = s.get("pid")
    if not pid:
        error("Session has no PID.")
        ctx.exit(1)
        return

    tty = get_tty(pid)
    if not tty:
        cwd = s.get("cwd", "")
        error(f"Could not determine TTY for PID {pid}.")
        if cwd:
            output(f"Fallback: cd {cwd}")
        ctx.exit(1)
        return

    if focus_terminal_tab(tty):
        success(f"Jumped to session {s['session_id'][:12]} (PID {pid}, TTY {tty})")
    else:
        cwd = s.get("cwd", "")
        error("No supported terminal emulator found (WezTerm or iTerm2 required).")
        if cwd:
            output(f"Fallback: cd {cwd}")
        ctx.exit(1)

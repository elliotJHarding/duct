"""duct session — view and manage Claude Code sessions."""

from __future__ import annotations

import collections
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

import click
from duct.cli.output import Col, error, kv, output, section, success, table
from duct.cli.resolve import resolve_root
from duct.config import ConfigError, load_config
from duct.markdown import TICKET_KEY_PATTERN
from duct.workspace import enumerate_ticket_dirs, resolve_ticket_dir


def _discover_sessions(claude_dir: Path | None = None, lookback_hours: int = 48) -> list[dict]:
    """Find active and recent Claude Code sessions."""
    claude_dir = claude_dir or Path.home() / ".claude"
    sessions: list[dict] = []

    # Active sessions from PID files
    sessions_dir = claude_dir / "sessions"
    if sessions_dir.is_dir():
        for f in sessions_dir.iterdir():
            if f.suffix == ".json":
                try:
                    data = json.loads(f.read_text())
                    pid = int(f.stem)
                    alive = _is_pid_alive(pid)
                    sessions.append({
                        "session_id": data.get("sessionId", ""),
                        "pid": pid,
                        "cwd": data.get("cwd", ""),
                        "started_at": data.get("startTime", ""),
                        "alive": alive,
                        "status": "ready" if alive else "terminated",
                        "topic": "",
                    })
                except (json.JSONDecodeError, ValueError):
                    continue

    # Recent transcripts from projects dir
    projects_dir = claude_dir / "projects"
    if projects_dir.is_dir():
        cutoff = time.time() - (lookback_hours * 3600)
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            for transcript in project_dir.glob("*.jsonl"):
                if transcript.stat().st_mtime < cutoff:
                    continue
                session_id = transcript.stem
                if any(s["session_id"] == session_id for s in sessions):
                    # Merge topic/status into existing entry
                    info = _extract_transcript_info(transcript)
                    for s in sessions:
                        if s["session_id"] == session_id:
                            s["topic"] = info.get("topic", "")
                            s["last_activity"] = info.get("last_activity", "")
                            if s["alive"]:
                                s["status"] = _infer_session_status(transcript)
                                if s["status"] in ("waiting",) and s.get("pid"):
                                    if _has_active_children(s["pid"]):
                                        s["status"] = "working"
                            break
                    continue
                cwd = _decode_project_path(project_dir.name)
                info = _extract_transcript_info(transcript)
                sessions.append({
                    "session_id": session_id,
                    "pid": None,
                    "cwd": cwd,
                    "started_at": info.get("started_at", ""),
                    "alive": False,
                    "status": "terminated",
                    "topic": info.get("topic", ""),
                    "last_activity": info.get("last_activity", ""),
                    "recent_messages": info.get("recent_messages", []),
                })

    # Override topic from terminal tab title for alive sessions
    for s in sessions:
        if s["alive"] and s.get("pid"):
            tty = _get_tty(s["pid"])
            if tty:
                title = _get_terminal_title(tty)
                if title:
                    s["topic"] = title[:100]

    return sessions


def _has_active_children(pid: int) -> bool:
    """Check if a Claude session PID has caffeinate running (indicates active work)."""
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pid), "-lf", "caffeinate"],
            capture_output=True, text=True, timeout=2,
        )
        return result.returncode == 0
    except Exception:
        return False


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _decode_project_path(encoded: str) -> str:
    return "/" + encoded.replace("-", "/")


def _extract_transcript_info(transcript_path: Path) -> dict:
    info: dict = {}
    try:
        lines = transcript_path.read_text().strip().splitlines()
        if not lines:
            return info

        try:
            first = json.loads(lines[0])
            info["started_at"] = first.get("timestamp", "")
        except json.JSONDecodeError:
            pass

        recent_messages: list[dict] = []
        for line in lines[-10:]:
            try:
                msg = json.loads(line)
                role = msg.get("type", msg.get("role", ""))
                if role in ("user", "assistant"):
                    text = ""
                    if isinstance(msg.get("message"), dict):
                        content = msg["message"].get("content", "")
                        if isinstance(content, str):
                            text = content[:200]
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text = block.get("text", "")[:200]
                                    break
                    recent_messages.append({"role": role, "text": text})
            except json.JSONDecodeError:
                continue

        info["recent_messages"] = recent_messages[-6:]

        try:
            last = json.loads(lines[-1])
            info["last_activity"] = last.get("timestamp", "")
        except json.JSONDecodeError:
            pass

        # Extract topic from first user message
        if "topic" not in info:
            for line in lines[:5]:
                try:
                    msg = json.loads(line)
                    if msg.get("type") == "user" or msg.get("role") == "user":
                        text = ""
                        if isinstance(msg.get("message"), dict):
                            content = msg["message"].get("content", "")
                            if isinstance(content, str):
                                text = content
                            elif isinstance(content, list):
                                for block in content:
                                    if isinstance(block, dict) and block.get("type") == "text":
                                        text = block.get("text", "")
                                        break
                        info["topic"] = text[:100]
                        break
                except json.JSONDecodeError:
                    continue

    except Exception:
        pass

    return info


_STATUS_STYLES = {
    "ready": "[green]ready[/green]",
    "waiting": "[yellow]waiting[/yellow]",
    "working": "[blue]working[/blue]",
    "terminated": "[dim]terminated[/dim]",
}


def _infer_session_status(transcript_path: Path) -> str:
    """Infer granular session status from the last assistant message in a transcript."""
    try:
        with open(transcript_path) as f:
            tail = list(collections.deque(f, maxlen=20))
    except Exception:
        return "working"

    # Find last assistant message index and check if user responded after it
    last_assistant_idx = None
    for i, line in enumerate(tail):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") in ("assistant",) or entry.get("role") == "assistant":
            last_assistant_idx = i

    user_responded = False
    if last_assistant_idx is not None:
        for line in tail[last_assistant_idx + 1 :]:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") in ("user", "human") or entry.get("role") in ("user", "human"):
                user_responded = True
                break

    for line in reversed(tail):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        if entry.get("type") != "assistant" and entry.get("role") != "assistant":
            continue

        stop_reason = entry.get("stop_reason")
        if stop_reason is None:
            # Also check nested message
            msg = entry.get("message", {})
            if isinstance(msg, dict):
                stop_reason = msg.get("stop_reason")

        if stop_reason == "end_turn":
            return "ready"

        if stop_reason == "tool_use":
            # Look for tool_use blocks in message content
            msg = entry.get("message", {})
            content = msg.get("content", []) if isinstance(msg, dict) else []
            if isinstance(content, list):
                for block in reversed(content):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name", "")
                        if name == "AskUserQuestion":
                            return "ready" if user_responded else "waiting"
                        if name == "ExitPlanMode":
                            return "ready"
                        if name == "EnterPlanMode":
                            return "planning"
                        return "working"
            return "working"

        # stop_reason is null/missing or something else
        return "working"

    # No assistant message found
    return "working"


def _match_session_ticket(session: dict, known_keys: set[str]) -> str | None:
    """Match a session to a ticket by cwd path."""
    cwd = session.get("cwd", "")
    matches = TICKET_KEY_PATTERN.findall(cwd)
    for match in matches:
        if match in known_keys:
            return match
    return None


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
    sessions = _discover_sessions()

    if not show_all:
        sessions = [s for s in sessions if s.get("alive")]

    if not sessions:
        msg = "No active sessions." if not show_all else "No sessions found."
        output(msg, data=[])
        return

    # Attach ticket to each session and group by ticket
    enriched: list[tuple[str, dict]] = []
    for s in sessions:
        ticket = _match_session_ticket(s, ticket_keys) or "-"
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
    sessions = _discover_sessions()

    # Find by prefix match
    matches = [s for s in sessions if s.get("session_id", "").startswith(session_id)]

    if not matches:
        error(f"No session found matching '{session_id}'.")
        ctx.exit(1)
        return

    if len(matches) > 1:
        error(
            f"Ambiguous session ID '{session_id}' — "
            f"matches {len(matches)} sessions. Be more specific."
        )
        ctx.exit(1)
        return

    s = matches[0]

    try:
        root = resolve_root(ctx)
        ticket_keys = {key for key, _ in enumerate_ticket_dirs(root)}
        ticket = _match_session_ticket(s, ticket_keys) or "none"
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


def _get_tty(pid: int) -> str | None:
    """Get the TTY device name for a given PID via ``ps``."""
    try:
        result = subprocess.run(
            ["ps", "-o", "tty=", "-p", str(pid)],
            capture_output=True,
            text=True,
        )
        tty = result.stdout.strip()
        return tty if tty and tty != "?" else None
    except Exception:
        return None


def _get_terminal_title(tty: str) -> str | None:
    """Read the tab/session title for a given TTY from the terminal emulator."""
    # --- WezTerm ---
    wezterm_bin = shutil.which("wezterm")
    if wezterm_bin:
        try:
            result = subprocess.run(
                [wezterm_bin, "cli", "list", "--format", "json"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                panes = json.loads(result.stdout)
                for pane in panes:
                    if pane.get("tty_name", "").endswith(tty):
                        return pane.get("title") or pane.get("tab_title")
        except Exception:
            pass

    # --- iTerm2 (macOS only) ---
    if platform.system() == "Darwin":
        script = (
            'tell application "iTerm2"\n'
            "    repeat with aWindow in windows\n"
            "        repeat with aTab in tabs of aWindow\n"
            "            repeat with aSession in sessions of aTab\n"
            f'                if tty of aSession ends with "{tty}" then\n'
            "                    return name of aSession\n"
            "                end if\n"
            "            end repeat\n"
            "        end repeat\n"
            "    end repeat\n"
            'end tell\n'
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                title = result.stdout.strip()
                if title:
                    return title
        except Exception:
            pass

    return None


def _focus_terminal_tab(tty: str) -> bool:
    """Try to activate the terminal tab that owns *tty*.

    Attempts WezTerm first, then iTerm2. Returns True on success.
    """
    # --- WezTerm ---
    wezterm_bin = shutil.which("wezterm")
    if wezterm_bin:
        try:
            result = subprocess.run(
                [wezterm_bin, "cli", "list", "--format", "json"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                panes = json.loads(result.stdout)
                for pane in panes:
                    if pane.get("tty_name", "").endswith(tty):
                        subprocess.run(
                            [wezterm_bin, "cli", "activate-pane", "--pane-id", str(pane["pane_id"])],
                            capture_output=True,
                        )
                        return True
        except Exception:
            pass

    # --- iTerm2 (macOS only) ---
    if platform.system() == "Darwin":
        script = (
            'tell application "iTerm2"\n'
            "    repeat with aWindow in windows\n"
            "        repeat with aTab in tabs of aWindow\n"
            "            repeat with aSession in sessions of aTab\n"
            f'                if tty of aSession ends with "{tty}" then\n'
            "                    select aTab\n"
            "                    tell aWindow to select\n"
            "                    return true\n"
            "                end if\n"
            "            end repeat\n"
            "        end repeat\n"
            "    end repeat\n"
            "end tell\n"
            "return false\n"
        )
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and "true" in result.stdout.strip().lower():
                return True
        except Exception:
            pass

    return False


@session.command("jump")
@click.argument("session_id")
@click.pass_context
def session_jump(ctx: click.Context, session_id: str) -> None:
    """Jump to the terminal tab running a session."""
    sessions = _discover_sessions()

    matches = [s for s in sessions if s.get("session_id", "").startswith(session_id)]

    if not matches:
        error(f"No session found matching '{session_id}'.")
        ctx.exit(1)
        return

    if len(matches) > 1:
        error(
            f"Ambiguous session ID '{session_id}' — "
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

    tty = _get_tty(pid)
    if not tty:
        cwd = s.get("cwd", "")
        error(f"Could not determine TTY for PID {pid}.")
        if cwd:
            output(f"Fallback: cd {cwd}")
        ctx.exit(1)
        return

    if _focus_terminal_tab(tty):
        success(f"Jumped to session {s['session_id'][:12]} (PID {pid}, TTY {tty})")
    else:
        cwd = s.get("cwd", "")
        error("No supported terminal emulator found (WezTerm or iTerm2 required).")
        if cwd:
            output(f"Fallback: cd {cwd}")
        ctx.exit(1)

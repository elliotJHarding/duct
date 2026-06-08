"""Session discovery and management for duct."""

from __future__ import annotations

import collections
import json
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from duct.markdown import TICKET_KEY_PATTERN


def discover_sessions(claude_dir: Path | None = None, lookback_hours: int = 48) -> list[dict]:
    """Find active and recent Claude Code sessions.

    Returns list of dicts with keys: session_id, pid, cwd, started_at, alive,
    status, topic, last_activity, recent_messages
    """
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
                    alive = is_pid_alive(pid)
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
                try:
                    if transcript.stat().st_mtime < cutoff:
                        continue
                except FileNotFoundError:
                    continue
                session_id = transcript.stem
                if any(s["session_id"] == session_id for s in sessions):
                    # Merge topic/status into existing entry
                    info = extract_transcript_info(transcript)
                    for s in sessions:
                        if s["session_id"] == session_id:
                            s["topic"] = info.get("topic", "")
                            s["last_activity"] = info.get("last_activity", "")
                            if s["alive"]:
                                s["status"] = infer_session_status(transcript)
                                # A quiet transcript alone doesn't mean "waiting":
                                # Claude only appends between steps, so a single long
                                # step (a long tool run, extended thinking) also looks
                                # idle. Claude Code runs a `caffeinate` keep-awake only
                                # while a turn is active and drops it the moment it
                                # blocks for input, so its presence is the authoritative
                                # "in an active turn" signal. Only demote to "waiting"
                                # when the transcript is quiet AND no turn is active —
                                # otherwise an actively-working session flaps to waiting
                                # whenever the per-tick pane spinner check happens to miss.
                                if s["status"] == "working" and _transcript_is_idle(transcript):
                                    if not (s.get("pid") and _has_active_children(s["pid"])):
                                        s["status"] = "waiting"
                                elif s["status"] == "waiting" and s.get("pid"):
                                    if _has_active_children(s["pid"]):
                                        s["status"] = "working"
                            break
                    continue
                cwd = _decode_project_path(project_dir.name)
                info = extract_transcript_info(transcript)
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

    # Override topic from terminal tab title for alive sessions.
    # Cache the tty on the session dict so apply_overrides doesn't pay a
    # second `ps` round trip per session, and use the batch lookup so
    # all alive ttys are fetched in a single `ps` invocation.
    from duct.terminal import get_terminal_title, get_ttys

    alive_pids = [s["pid"] for s in sessions if s["alive"] and s.get("pid")]
    tty_by_pid = get_ttys(alive_pids)
    for s in sessions:
        pid = s.get("pid")
        if not (s["alive"] and pid):
            continue
        tty = tty_by_pid.get(pid)
        if tty:
            s["tty"] = tty
            title = get_terminal_title(tty)
            if title:
                s["topic"] = title[:100]

    return sessions


def infer_session_status(transcript_path: Path) -> str:
    """Infer session status from the last assistant message in a transcript.

    Returns: "ready", "waiting", "working", "terminated"

    Plan mode is now an orthogonal dimension on `SessionInfo.mode`, derived
    from pane-text inspection — not from the transcript. `EnterPlanMode` tool
    calls are treated as ordinary tool_use events here.
    """
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
        for line in tail[last_assistant_idx + 1:]:
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
                        return "working"
            return "working"

        # stop_reason is null/missing or something else
        return "working"

    # No assistant message found
    return "working"


def apply_recency_status(
    status: str,
    last_activity: str,
    *,
    now: datetime,
    done_window_seconds: int,
    stale_after_seconds: int,
) -> str:
    """Decorate a transcript-derived ``ready`` based on how long ago the session last wrote.

    Returns ``done`` while the session is within the done window, ``stale`` once it's
    past the stale threshold, otherwise ``ready``. Any non-``ready`` input passes
    through untouched — pane-inspection overrides (working/waiting) still win.
    """
    if status != "ready" or not last_activity:
        return status
    try:
        then = datetime.fromisoformat(last_activity)
    except (ValueError, TypeError):
        return status
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    age = (now - then).total_seconds()
    if age <= done_window_seconds:
        return "done"
    if age >= stale_after_seconds:
        return "stale"
    return "ready"


def apply_recency_decoration(
    sessions: list[dict],
    *,
    done_window_seconds: int,
    stale_after_seconds: int,
    now: datetime | None = None,
) -> None:
    """Rewrite ``ready`` statuses on each session dict to ``done`` or ``stale``.

    Mutates ``sessions`` in place. Run after ``pane_status.apply_overrides`` so
    that real-time pane signals (working/waiting) still take precedence.
    """
    moment = now or datetime.now(timezone.utc)
    for s in sessions:
        s["status"] = apply_recency_status(
            s.get("status", ""),
            s.get("last_activity", ""),
            now=moment,
            done_window_seconds=done_window_seconds,
            stale_after_seconds=stale_after_seconds,
        )


def extract_transcript_info(transcript_path: Path) -> dict:
    """Parse a JSONL transcript and extract summary info.

    Returns dict with keys: started_at, last_activity, recent_messages, topic
    """
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


def match_session_ticket(session: dict, known_keys: set[str]) -> str | None:
    """Match a session to a ticket by cwd path."""
    cwd = session.get("cwd", "")
    matches = TICKET_KEY_PATTERN.findall(cwd)
    for match in matches:
        if match in known_keys:
            return match
    return None


def build_session_command(
    prompt: str | None = None,
    add_dir: Path | None = None,
    extra_args: list[str] | None = None,
    skip_permissions: bool = False,
) -> list[str]:
    """Build the claude CLI command list without executing it.

    ``prompt`` is appended as a positional argument when provided. For
    interactive WezTerm-pane launches prefer leaving ``prompt=None`` and
    sending the prompt via the terminal adapter's ``send_text`` after the
    pane is up — claude hangs silently when handed a long multi-line
    positional prompt argv on startup.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise FileNotFoundError("'claude' CLI not found on PATH")

    cmd = [claude_bin]
    if add_dir:
        cmd.extend(["--add-dir", str(add_dir)])
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if extra_args:
        cmd.extend(extra_args)
    if prompt:
        cmd.append(prompt)
    return cmd


def launch_session_in_dir(
    cwd: Path,
    prompt: str | None = None,
    add_dir: Path | None = None,
    extra_args: list[str] | None = None,
    skip_permissions: bool = False,
) -> int:
    """Launch a Claude Code session in the given directory. Returns PID.

    Primitive launcher with no workspace/ticket assumptions — callers own the
    cwd and any context-prompt construction.
    """
    cmd = build_session_command(
        prompt=prompt, add_dir=add_dir, extra_args=extra_args,
        skip_permissions=skip_permissions,
    )
    return subprocess.Popen(cmd, cwd=str(cwd)).pid


def prepare_session(
    root: Path,
    key: str,
    repo: str | None = None,
    prompt: str | None = None,
    extra_args: list[str] | None = None,
) -> tuple[list[str], Path, str | None]:
    """Prepare a Claude Code session for a ticket.

    Resolves directories, builds the context-aware prompt, writes sandbox
    settings, and returns ``(command, cwd, prompt_to_send)`` without
    executing anything. ``command`` never carries the positional prompt
    argv — callers spawning into a WezTerm pane must send
    ``prompt_to_send`` via ``adapter.send_text`` after the pane is ready
    (claude hangs on long multi-line argv prompts). Popen callers that
    want positional-argv behaviour should append ``prompt_to_send`` to
    ``command`` themselves.
    """
    from duct.config import load_config
    from duct.workspace import resolve_ticket_dir

    ticket_dir = resolve_ticket_dir(root, key)
    if not ticket_dir:
        raise FileNotFoundError(f"No workspace found for {key}")

    cfg = load_config(root)

    # Determine working directory
    cwd = ticket_dir
    if repo:
        repo_dir = ticket_dir / repo
        if not repo_dir.is_dir() or not (repo_dir / ".git").exists():
            raise FileNotFoundError(f"Repo worktree '{repo}' not found in {key}")
        cwd = repo_dir

    # Build context-aware prompt — only injected if user provided a prompt
    full_prompt: str | None = None
    if prompt:
        orch_dir = ticket_dir / "orchestrator"
        context_parts: list[str] = [
            f"You are working on ticket {key}.",
            "",
            f"Read {orch_dir / 'TICKET.md'} for ticket details.",
        ]
        if orch_dir.is_dir():
            artifacts = [f.name for f in sorted(orch_dir.iterdir()) if f.is_file()]
            if artifacts:
                context_parts.append(
                    f"Available artifacts in orchestrator/: {', '.join(artifacts)}",
                )
        repos = [
            d.name for d in sorted(ticket_dir.iterdir())
            if d.is_dir() and d.name != "orchestrator" and (d / ".git").exists()
        ]
        if repos:
            context_parts.append(f"Repo worktrees: {', '.join(repos)}")
        context_parts.append("")
        context_parts.append(prompt)
        full_prompt = "\n".join(context_parts)

    # Merge config extra_args with caller's extra_args
    merged_args = list(cfg.session.extra_args)
    if extra_args:
        merged_args.extend(extra_args)

    # Ensure sandbox config
    if cfg.sandbox.enabled:
        from duct.sandbox import write_settings

        write_settings(cwd, cfg.sandbox)

    cmd = build_session_command(
        prompt=None,
        add_dir=ticket_dir,
        extra_args=merged_args or None,
        skip_permissions=cfg.sandbox.skip_permissions,
    )
    return cmd, cwd, full_prompt


def launch_session(
    root: Path,
    key: str,
    repo: str | None = None,
    prompt: str | None = None,
    extra_args: list[str] | None = None,
) -> int:
    """Launch a Claude Code session for a ticket. Returns PID.

    Resolves the ticket key to a workspace dir, builds a context-aware prompt,
    writes sandbox settings, then delegates to launch_session_in_dir.

    Re-appends ``prompt_to_send`` as positional argv since this Popen path
    has no pane to send-text into. Long multi-line prompts can hang claude
    in this codepath — prefer the WezTerm spawn flow when in the TUI.
    """
    cmd, cwd, prompt_to_send = prepare_session(
        root, key, repo=repo, prompt=prompt, extra_args=extra_args,
    )
    if prompt_to_send:
        cmd.append(prompt_to_send)
    return subprocess.Popen(cmd, cwd=str(cwd)).pid


def stop_session(pid: int) -> bool:
    """Send SIGTERM to a session. Returns True if signal sent."""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, ProcessLookupError):
        return False


def is_pid_alive(pid: int) -> bool:
    """Check if a process is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _has_active_children(pid: int) -> bool:
    """Check if a Claude session PID has caffeinate running."""
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pid), "-lf", "caffeinate"],
            capture_output=True, text=True, timeout=2,
        )
        return result.returncode == 0
    except Exception:
        return False


def _transcript_is_idle(transcript_path: Path, idle_seconds: float = 3.0) -> bool:
    """Check if a transcript hasn't been modified recently.

    Used as a heuristic to distinguish an actively-running session from one
    that's paused waiting for user input/approval — Claude Code only appends
    to the transcript while it's actively processing.
    """
    try:
        return (time.time() - transcript_path.stat().st_mtime) > idle_seconds
    except Exception:
        return False


def _decode_project_path(encoded: str) -> str:
    return "/" + encoded.replace("-", "/")

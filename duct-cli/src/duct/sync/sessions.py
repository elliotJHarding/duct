"""Claude Code session sync — reads transcript data from ~/.claude/."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from duct.markdown import TICKET_KEY_PATTERN, atomic_write, generate_frontmatter
from duct.models import SyncResult
from duct.workspace import enumerate_ticket_dirs, orchestrator_dir


class SessionSync:
    name = "sessions"

    def __init__(self, claude_dir: Path | None = None, lookback_hours: int = 48):
        self._claude_dir = claude_dir or Path.home() / ".claude"
        self._lookback_hours = lookback_hours

    def sync(self, root: Path) -> SyncResult:
        start = time.time()
        errors: list[str] = []

        ticket_paths = {key: path for key, path in enumerate_ticket_dirs(root)}
        if not ticket_paths:
            return SyncResult(
                source=self.name,
                tickets_synced=0,
                duration_seconds=time.time() - start,
            )

        sessions = self._discover_sessions()

        # Match sessions to tickets
        ticket_sessions: dict[str, list[dict]] = {k: [] for k in ticket_paths}
        for session in sessions:
            matched = self._match_ticket(session, set(ticket_paths))
            if matched:
                ticket_sessions[matched].append(session)

        # Write CLAUDE_SESSIONS.md per ticket. A ticket with no current sessions
        # but an existing snapshot is rewritten — not skipped — so a stale "active"
        # PID can't outlive the session that produced it. Tickets that have never
        # had a snapshot are left alone to avoid creating empty files everywhere.
        synced = 0
        for key, ticket_path in ticket_paths.items():
            sess_list = ticket_sessions[key]
            snapshot = ticket_path / "orchestrator" / "CLAUDE_SESSIONS.md"
            if not sess_list and not snapshot.exists():
                continue
            try:
                self._write_sessions_md(sess_list, ticket_path)
                synced += 1
            except Exception as exc:
                errors.append(f"{key}: {exc}")

        return SyncResult(
            source=self.name,
            tickets_synced=synced,
            duration_seconds=time.time() - start,
            errors=errors,
        )

    def _discover_sessions(self) -> list[dict]:
        """Find active and recent sessions from ~/.claude/."""
        sessions: list[dict] = []

        # Active sessions from registry (PID files)
        sessions_dir = self._claude_dir / "sessions"
        if sessions_dir.is_dir():
            for f in sessions_dir.iterdir():
                if f.suffix == ".json":
                    try:
                        data = json.loads(f.read_text())
                        pid = int(f.stem)
                        alive = self._is_pid_alive(pid)
                        sessions.append({
                            "session_id": data.get("sessionId", ""),
                            "pid": pid,
                            "cwd": data.get("cwd", ""),
                            "started_at": data.get("startTime", ""),
                            "alive": alive,
                            "status": "active" if alive else "terminated",
                        })
                    except (json.JSONDecodeError, ValueError):
                        continue

        # Recent transcripts from projects dir
        projects_dir = self._claude_dir / "projects"
        if projects_dir.is_dir():
            cutoff = time.time() - (self._lookback_hours * 3600)
            for project_dir in projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                for transcript in project_dir.glob("*.jsonl"):
                    if transcript.stat().st_mtime < cutoff:
                        continue
                    session_id = transcript.stem
                    if any(s["session_id"] == session_id for s in sessions):
                        continue
                    cwd = self._decode_project_path(project_dir.name)
                    info = self._extract_transcript_info(transcript)
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

        return sessions

    def _is_pid_alive(self, pid: int) -> bool:
        """Check if a process is still running."""
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _decode_project_path(self, encoded: str) -> str:
        """Decode ~/.claude/projects/ encoded path: leading / stripped, / replaced with -."""
        return "/" + encoded.replace("-", "/")

    def _match_ticket(self, session: dict, known_keys: set[str]) -> str | None:
        """Extract ticket key from session's working directory."""
        cwd = session.get("cwd", "")
        matches = TICKET_KEY_PATTERN.findall(cwd)
        for match in matches:
            if match in known_keys:
                return match
        return None

    def _extract_transcript_info(self, transcript_path: Path) -> dict:
        """Read a JSONL transcript and extract summary info."""
        info: dict = {}
        recent_messages: list[dict] = []

        try:
            lines = transcript_path.read_text().strip().splitlines()
            if not lines:
                return info

            # First message for start time
            try:
                first = json.loads(lines[0])
                info["started_at"] = first.get("timestamp", "")
            except json.JSONDecodeError:
                pass

            # Last few messages for recent activity
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

            # Last activity from last message timestamp
            try:
                last = json.loads(lines[-1])
                info["last_activity"] = last.get("timestamp", "")
            except json.JSONDecodeError:
                pass

            # Topic from first user message
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

    def _write_sessions_md(self, sessions: list[dict], ticket_dir: Path) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts: list[str] = []

        parts.append(generate_frontmatter(source="sync", synced_at=now))
        parts.append("")
        parts.append("# Claude Sessions")
        parts.append("")

        active = [s for s in sessions if s.get("alive")]
        terminated = [s for s in sessions if not s.get("alive")]

        if not active and not terminated:
            parts.append("No active or recent sessions.")
            parts.append("")

        if active:
            parts.append("## Active")
            parts.append("")
            for s in active:
                parts.append(f"### PID {s.get('pid', '?')} — {s.get('status', 'active')}")
                parts.append("")
                parts.append(f"- **Workspace**: {s.get('cwd', 'unknown')}")
                parts.append(f"- **Session**: {s.get('session_id', '')}")
                if s.get("topic"):
                    parts.append(f"- **Topic**: {s['topic']}")
                if s.get("last_activity"):
                    parts.append(f"- **Last Activity**: {s['last_activity']}")
                parts.append("")

        if terminated:
            parts.append("## Recently Terminated")
            parts.append("")
            for s in terminated:
                parts.append(f"### Session {s.get('session_id', '?')}")
                parts.append("")
                parts.append(f"- **Workspace**: {s.get('cwd', 'unknown')}")
                if s.get("topic"):
                    parts.append(f"- **Topic**: {s['topic']}")
                if s.get("last_activity"):
                    parts.append(f"- **Ended**: ~{s['last_activity']}")
                parts.append("")

                if s.get("recent_messages"):
                    parts.append("**Recent conversation:**")
                    parts.append("")
                    for msg in s["recent_messages"]:
                        role = msg.get("role", "?")
                        text = msg.get("text", "")
                        if text:
                            parts.append(f"> **{role}**: {text}")
                    parts.append("")

        content = "\n".join(parts)
        orch = orchestrator_dir(ticket_dir)
        atomic_write(orch / "CLAUDE_SESSIONS.md", content)

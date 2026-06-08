"""Tests for duct.sync.sessions."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from duct.sync.sessions import SessionSync


def _make_ticket_dir(root: Path, name: str) -> Path:
    """Create a ticket directory with an orchestrator/ subdirectory."""
    ticket = root / name
    ticket.mkdir(parents=True, exist_ok=True)
    (ticket / "orchestrator").mkdir(exist_ok=True)
    return ticket


# ---------------------------------------------------------------------------
# _match_ticket
# ---------------------------------------------------------------------------

class TestMatchTicket:
    def test_matches_key_in_cwd(self):
        ss = SessionSync()
        session = {"cwd": "/workspace/ERSC-100-my-task/my-service"}
        result = ss._match_ticket(session, {"ERSC-100", "ERSC-200"})
        assert result == "ERSC-100"

    def test_no_match_returns_none(self):
        ss = SessionSync()
        session = {"cwd": "/workspace/some-random-dir"}
        result = ss._match_ticket(session, {"ERSC-100"})
        assert result is None

    def test_only_matches_known_keys(self):
        ss = SessionSync()
        session = {"cwd": "/workspace/ERSC-999-unknown/svc"}
        result = ss._match_ticket(session, {"ERSC-100"})
        assert result is None

    def test_empty_cwd(self):
        ss = SessionSync()
        session = {"cwd": ""}
        result = ss._match_ticket(session, {"ERSC-100"})
        assert result is None


# ---------------------------------------------------------------------------
# _decode_project_path
# ---------------------------------------------------------------------------

class TestDecodeProjectPath:
    def test_simple_path(self):
        ss = SessionSync()
        assert ss._decode_project_path("Users-me-workspace") == "/Users/me/workspace"

    def test_deep_path(self):
        ss = SessionSync()
        result = ss._decode_project_path("Users-me-workspace-ERSC-100-task")
        assert result == "/Users/me/workspace/ERSC/100/task"


# ---------------------------------------------------------------------------
# _is_pid_alive
# ---------------------------------------------------------------------------

class TestIsPidAlive:
    def test_current_process_is_alive(self):
        ss = SessionSync()
        assert ss._is_pid_alive(os.getpid()) is True

    def test_dead_pid(self):
        ss = SessionSync()
        # PID 999999999 is almost certainly not running
        assert ss._is_pid_alive(999999999) is False


# ---------------------------------------------------------------------------
# _extract_transcript_info
# ---------------------------------------------------------------------------

class TestExtractTranscriptInfo:
    def test_extracts_timestamps(self, tmp_path: Path):
        transcript = tmp_path / "session.jsonl"
        lines = [
            json.dumps({"type": "user", "timestamp": "2025-01-01T10:00:00Z",
                        "message": {"content": "Hello world"}}),
            json.dumps({"type": "assistant", "timestamp": "2025-01-01T10:00:05Z",
                        "message": {"content": "Hi there"}}),
        ]
        transcript.write_text("\n".join(lines))

        ss = SessionSync()
        info = ss._extract_transcript_info(transcript)

        assert info["started_at"] == "2025-01-01T10:00:00Z"
        assert info["last_activity"] == "2025-01-01T10:00:05Z"

    def test_extracts_topic(self, tmp_path: Path):
        transcript = tmp_path / "session.jsonl"
        lines = [
            json.dumps({"type": "user", "timestamp": "2025-01-01T10:00:00Z",
                        "message": {"content": "Fix the login bug"}}),
        ]
        transcript.write_text("\n".join(lines))

        ss = SessionSync()
        info = ss._extract_transcript_info(transcript)

        assert info["topic"] == "Fix the login bug"

    def test_extracts_recent_messages(self, tmp_path: Path):
        transcript = tmp_path / "session.jsonl"
        lines = [
            json.dumps({"type": "user", "timestamp": "2025-01-01T10:00:00Z",
                        "message": {"content": "Do something"}}),
            json.dumps({"type": "assistant", "timestamp": "2025-01-01T10:00:05Z",
                        "message": {"content": "Done"}}),
        ]
        transcript.write_text("\n".join(lines))

        ss = SessionSync()
        info = ss._extract_transcript_info(transcript)

        assert len(info["recent_messages"]) == 2
        assert info["recent_messages"][0]["role"] == "user"
        assert info["recent_messages"][1]["role"] == "assistant"

    def test_handles_content_blocks(self, tmp_path: Path):
        transcript = tmp_path / "session.jsonl"
        lines = [
            json.dumps({
                "type": "user",
                "timestamp": "2025-01-01T10:00:00Z",
                "message": {
                    "content": [{"type": "text", "text": "Block content here"}],
                },
            }),
        ]
        transcript.write_text("\n".join(lines))

        ss = SessionSync()
        info = ss._extract_transcript_info(transcript)

        assert info["topic"] == "Block content here"

    def test_empty_transcript(self, tmp_path: Path):
        transcript = tmp_path / "session.jsonl"
        transcript.write_text("")

        ss = SessionSync()
        info = ss._extract_transcript_info(transcript)

        assert info == {}

    def test_truncates_long_topic(self, tmp_path: Path):
        transcript = tmp_path / "session.jsonl"
        long_text = "A" * 200
        lines = [
            json.dumps({"type": "user", "timestamp": "2025-01-01T10:00:00Z",
                        "message": {"content": long_text}}),
        ]
        transcript.write_text("\n".join(lines))

        ss = SessionSync()
        info = ss._extract_transcript_info(transcript)

        assert len(info["topic"]) == 100


# ---------------------------------------------------------------------------
# _discover_sessions
# ---------------------------------------------------------------------------

class TestDiscoverSessions:
    def test_finds_pid_sessions(self, tmp_path: Path):
        claude_dir = tmp_path / ".claude"
        sessions_dir = claude_dir / "sessions"
        sessions_dir.mkdir(parents=True)

        # Use current PID so _is_pid_alive returns True
        pid = os.getpid()
        session_data = {
            "sessionId": "abc-123",
            "cwd": "/workspace/ERSC-100-task",
            "startTime": "2025-01-01T10:00:00Z",
        }
        (sessions_dir / f"{pid}.json").write_text(json.dumps(session_data))

        ss = SessionSync(claude_dir=claude_dir)
        sessions = ss._discover_sessions()

        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "abc-123"
        assert sessions[0]["pid"] == pid
        assert sessions[0]["alive"] is True
        assert sessions[0]["status"] == "active"

    def test_finds_transcript_sessions(self, tmp_path: Path):
        claude_dir = tmp_path / ".claude"
        project_dir = claude_dir / "projects" / "Users-me-workspace"
        project_dir.mkdir(parents=True)

        transcript = project_dir / "session-xyz.jsonl"
        lines = [
            json.dumps({"type": "user", "timestamp": "2025-01-01T10:00:00Z",
                        "message": {"content": "Hello"}}),
        ]
        transcript.write_text("\n".join(lines))

        ss = SessionSync(claude_dir=claude_dir, lookback_hours=9999)
        sessions = ss._discover_sessions()

        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "session-xyz"
        assert sessions[0]["alive"] is False

    def test_skips_old_transcripts(self, tmp_path: Path):
        claude_dir = tmp_path / ".claude"
        project_dir = claude_dir / "projects" / "Users-me-workspace"
        project_dir.mkdir(parents=True)

        transcript = project_dir / "old-session.jsonl"
        transcript.write_text(json.dumps({"type": "user", "message": {"content": "old"}}))
        # Set mtime to 1 week ago
        old_time = time.time() - (7 * 24 * 3600)
        os.utime(transcript, (old_time, old_time))

        ss = SessionSync(claude_dir=claude_dir, lookback_hours=24)
        sessions = ss._discover_sessions()

        assert len(sessions) == 0

    def test_handles_missing_dirs(self, tmp_path: Path):
        claude_dir = tmp_path / ".claude"
        # Don't create any dirs

        ss = SessionSync(claude_dir=claude_dir)
        sessions = ss._discover_sessions()

        assert sessions == []

    def test_skips_invalid_json(self, tmp_path: Path):
        claude_dir = tmp_path / ".claude"
        sessions_dir = claude_dir / "sessions"
        sessions_dir.mkdir(parents=True)

        (sessions_dir / "12345.json").write_text("not valid json{{{")

        ss = SessionSync(claude_dir=claude_dir)
        sessions = ss._discover_sessions()

        assert sessions == []


# ---------------------------------------------------------------------------
# _write_sessions_md
# ---------------------------------------------------------------------------

class TestWriteSessionsMd:
    def test_writes_active_sessions(self, tmp_path: Path):
        ticket = _make_ticket_dir(tmp_path, "ERSC-100-task")
        sessions = [
            {
                "session_id": "abc-123",
                "pid": 12345,
                "cwd": "/workspace/ERSC-100-task/svc",
                "alive": True,
                "status": "active",
                "topic": "Fix the bug",
                "last_activity": "2025-01-01T10:30:00Z",
            },
        ]

        ss = SessionSync()
        ss._write_sessions_md(sessions, ticket)

        md_path = ticket / "orchestrator" / "CLAUDE_SESSIONS.md"
        assert md_path.exists()

        content = md_path.read_text()
        assert "# Claude Sessions" in content
        assert "## Active" in content
        assert "PID 12345" in content
        assert "abc-123" in content
        assert "Fix the bug" in content

    def test_writes_terminated_sessions(self, tmp_path: Path):
        ticket = _make_ticket_dir(tmp_path, "ERSC-101-task")
        sessions = [
            {
                "session_id": "def-456",
                "pid": None,
                "cwd": "/workspace/ERSC-101-task/svc",
                "alive": False,
                "status": "terminated",
                "topic": "Refactor auth",
                "last_activity": "2025-01-01T11:00:00Z",
                "recent_messages": [
                    {"role": "user", "text": "Please refactor"},
                    {"role": "assistant", "text": "Done"},
                ],
            },
        ]

        ss = SessionSync()
        ss._write_sessions_md(sessions, ticket)

        content = (ticket / "orchestrator" / "CLAUDE_SESSIONS.md").read_text()
        assert "## Recently Terminated" in content
        assert "Session def-456" in content
        assert "Refactor auth" in content
        assert "**Recent conversation:**" in content
        assert "> **user**: Please refactor" in content

    def test_writes_no_sessions_notice(self, tmp_path: Path):
        ticket = _make_ticket_dir(tmp_path, "ERSC-103-task")

        ss = SessionSync()
        ss._write_sessions_md([], ticket)

        content = (ticket / "orchestrator" / "CLAUDE_SESSIONS.md").read_text()
        assert "No active or recent sessions." in content
        assert "## Active" not in content
        assert "## Recently Terminated" not in content

    def test_has_frontmatter(self, tmp_path: Path):
        ticket = _make_ticket_dir(tmp_path, "ERSC-102-task")
        sessions = [
            {
                "session_id": "xyz",
                "pid": None,
                "cwd": "/w",
                "alive": False,
                "status": "terminated",
            },
        ]

        ss = SessionSync()
        ss._write_sessions_md(sessions, ticket)

        content = (ticket / "orchestrator" / "CLAUDE_SESSIONS.md").read_text()
        assert content.startswith("---\n")
        assert "source: sync" in content


# ---------------------------------------------------------------------------
# Full sync
# ---------------------------------------------------------------------------

class TestFullSync:
    def test_sync_matches_sessions_to_tickets(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()
        ticket = _make_ticket_dir(root, "ERSC-500-task")

        claude_dir = tmp_path / ".claude"
        sessions_dir = claude_dir / "sessions"
        sessions_dir.mkdir(parents=True)

        # Use current PID so it appears alive
        pid = os.getpid()
        session_data = {
            "sessionId": "match-session",
            "cwd": str(ticket / "my-service"),
            "startTime": "2025-01-01T10:00:00Z",
        }
        (sessions_dir / f"{pid}.json").write_text(json.dumps(session_data))

        ss = SessionSync(claude_dir=claude_dir)
        result = ss.sync(root)

        assert result.source == "sessions"
        assert result.tickets_synced == 1
        assert (ticket / "orchestrator" / "CLAUDE_SESSIONS.md").exists()

    def test_sync_no_tickets(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()

        ss = SessionSync(claude_dir=tmp_path / ".claude")
        result = ss.sync(root)

        assert result.tickets_synced == 0
        assert result.errors == []

    def test_sync_no_matching_sessions(self, tmp_path: Path):
        root = tmp_path / "workspace"
        root.mkdir()
        _make_ticket_dir(root, "ERSC-600-task")

        claude_dir = tmp_path / ".claude"
        sessions_dir = claude_dir / "sessions"
        sessions_dir.mkdir(parents=True)

        # Session for a different ticket
        session_data = {
            "sessionId": "other-session",
            "cwd": "/workspace/OTHER-999-unrelated",
            "startTime": "2025-01-01T10:00:00Z",
        }
        (sessions_dir / "99999.json").write_text(json.dumps(session_data))

        ss = SessionSync(claude_dir=claude_dir)
        result = ss.sync(root)

        assert result.tickets_synced == 0
        # No pre-existing snapshot, so none is created for a sessionless ticket.
        assert not (root / "ERSC-600-task" / "orchestrator" / "CLAUDE_SESSIONS.md").exists()

    def test_sync_clears_stale_snapshot(self, tmp_path: Path):
        """A snapshot left over from a now-dead session is rewritten, not frozen.

        Reproduces the observed failure: a CLAUDE_SESSIONS.md saying "PID NNN —
        active" survives long after the process and its registry/transcript are
        gone, because sync used to skip tickets with no current sessions.
        """
        root = tmp_path / "workspace"
        root.mkdir()
        ticket = _make_ticket_dir(root, "ERSC-700-task")

        stale = ticket / "orchestrator" / "CLAUDE_SESSIONS.md"
        stale.write_text(
            "---\nsource: sync\nsyncedAt: 2026-05-26T11:41:58Z\n---\n\n"
            "# Claude Sessions\n\n## Active\n\n### PID 614 — active\n"
        )

        # Empty registry and projects — the session has fully aged out.
        claude_dir = tmp_path / ".claude"
        (claude_dir / "sessions").mkdir(parents=True)

        ss = SessionSync(claude_dir=claude_dir)
        result = ss.sync(root)

        assert result.tickets_synced == 1
        content = stale.read_text()
        assert "PID 614" not in content
        assert "## Active" not in content
        assert "No active or recent sessions." in content

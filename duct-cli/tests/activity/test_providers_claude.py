"""Tests for ClaudeActivityProvider — transcript scanning and ticket inference."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from duct.activity.providers.claude import ClaudeActivityProvider
from duct.config import WorkspaceConfig


def _write_transcript(path: Path, *, started_at: str, last_activity: str, topic: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "type": "user",
                "timestamp": started_at,
                "message": {"content": topic},
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "timestamp": last_activity,
                "message": {"content": "done"},
            }
        ),
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


class TestClaudeProvider:
    def test_emits_session_event_for_recent_transcript(self, tmp_path: Path):
        claude_root = tmp_path / "claude"
        ws_root = tmp_path / "workspace"
        ws_root.mkdir()
        project_dir = claude_root / "projects" / "-Users-alice-ticket-ERSC-1278"
        transcript = project_dir / "sess-42.jsonl"
        _write_transcript(
            transcript,
            started_at="2026-04-21T09:00:00Z",
            last_activity="2026-04-21T09:30:00Z",
            topic="implement auth",
        )
        # Ensure the file's mtime is recent so the provider's mtime filter passes.
        now = time.time()
        os.utime(transcript, (now, now))

        provider = ClaudeActivityProvider(claude_dir=claude_root)
        cfg = WorkspaceConfig(root=ws_root)
        events = list(
            provider.fetch(
                datetime(2026, 4, 21, tzinfo=timezone.utc),
                datetime(2026, 4, 22, tzinfo=timezone.utc),
                cfg,
            )
        )
        assert len(events) == 1
        e = events[0]
        assert e.source == "claude"
        assert e.event_type == "session"
        assert e.duration_seconds == 30 * 60
        assert "implement auth" in e.summary

    def test_ticket_key_inferred_from_cwd(self, tmp_path: Path):
        claude_root = tmp_path / "claude"
        ws_root = tmp_path / "workspace"
        ws_root.mkdir()
        # Create a matching workspace ticket so known_keys is populated.
        (ws_root / "ERSC-1278-work" / "orchestrator").mkdir(parents=True)

        project_dir = claude_root / "projects" / "-Users-alice-workspace-ERSC-1278-work"
        transcript = project_dir / "sess-1.jsonl"
        _write_transcript(
            transcript,
            started_at="2026-04-21T09:00:00Z",
            last_activity="2026-04-21T09:15:00Z",
            topic="no ticket in topic",
        )
        now = time.time()
        os.utime(transcript, (now, now))

        provider = ClaudeActivityProvider(claude_dir=claude_root)
        cfg = WorkspaceConfig(root=ws_root)
        events = list(
            provider.fetch(
                datetime(2026, 4, 21, tzinfo=timezone.utc),
                datetime(2026, 4, 22, tzinfo=timezone.utc),
                cfg,
            )
        )
        assert len(events) == 1
        assert events[0].ticket_key == "ERSC-1278"

    def test_timestamp_unaffected_by_since_window(self, tmp_path: Path):
        """Re-gathering with a different ``since`` must not shift the timestamp.

        Regression: the provider previously clamped the stored timestamp to
        ``max(start, since)``, which moved sessions into different day-file
        buckets on re-runs and bypassed event_id dedup.
        """
        claude_root = tmp_path / "claude"
        ws_root = tmp_path / "workspace"
        ws_root.mkdir()

        transcript = claude_root / "projects" / "-Users-alice-stuff" / "sess.jsonl"
        _write_transcript(
            transcript,
            started_at="2026-04-19T10:00:00Z",
            last_activity="2026-04-19T10:30:00Z",
            topic="work",
        )
        import os
        import time

        os.utime(transcript, (time.time(), time.time()))

        provider = ClaudeActivityProvider(claude_dir=claude_root)

        narrow = list(
            provider.fetch(
                datetime(2026, 4, 20, tzinfo=timezone.utc),
                datetime(2026, 4, 22, tzinfo=timezone.utc),
                WorkspaceConfig(root=ws_root),
            )
        )
        # When `since` is after the session start, the session is dropped
        # entirely by the `end_dt < since` check (which is correct).
        assert narrow == []

        wide = list(
            provider.fetch(
                datetime(2026, 4, 18, tzinfo=timezone.utc),
                datetime(2026, 4, 22, tzinfo=timezone.utc),
                WorkspaceConfig(root=ws_root),
            )
        )
        assert len(wide) == 1
        # Timestamp must be the session's *actual* start, regardless of the
        # gather window. Otherwise same event_id + different timestamp-day
        # escapes the per-day-file dedup.
        assert wide[0].timestamp == "2026-04-19T10:00:00Z"

    def test_skips_transcripts_outside_window(self, tmp_path: Path):
        claude_root = tmp_path / "claude"
        ws_root = tmp_path / "workspace"
        ws_root.mkdir()

        transcript = claude_root / "projects" / "-Users-alice-stuff" / "sess.jsonl"
        _write_transcript(
            transcript,
            started_at="2020-01-01T00:00:00Z",
            last_activity="2020-01-01T00:15:00Z",
            topic="ancient",
        )
        # Set mtime to match the ancient transcript times — mtime cutoff will skip.
        past = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(transcript, (past, past))

        provider = ClaudeActivityProvider(claude_dir=claude_root)
        events = list(
            provider.fetch(
                datetime(2026, 4, 21, tzinfo=timezone.utc),
                datetime(2026, 4, 22, tzinfo=timezone.utc),
                WorkspaceConfig(root=ws_root),
            )
        )
        assert events == []

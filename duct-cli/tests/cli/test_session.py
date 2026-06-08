"""Tests for the duct session command."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from duct.cli.main import cli
from duct.session import (
    apply_recency_decoration,
    apply_recency_status,
    discover_sessions,
    extract_transcript_info,
    infer_session_status,
    is_pid_alive,
    match_session_ticket,
    _decode_project_path,
    _has_active_children,
)
from duct.terminal import (
    focus_terminal_tab,
    get_terminal_title,
    get_tty,
)


def _init_workspace(root: Path) -> None:
    (root / "config.yaml").write_text("workspace:\n  root: .\n")


# ---------------------------------------------------------------------------
# _decode_project_path
# ---------------------------------------------------------------------------


def test_decode_project_path():
    assert _decode_project_path("Users-foo-workspace") == "/Users/foo/workspace"


# ---------------------------------------------------------------------------
# extract_transcript_info
# ---------------------------------------------------------------------------


def test_extract_transcript_info_basic(tmp_path: Path):
    transcript = tmp_path / "session.jsonl"
    lines = [
        json.dumps({"timestamp": "2025-01-01T00:00:00Z", "type": "user", "message": {"content": "Fix the bug"}}),
        json.dumps({"timestamp": "2025-01-01T00:01:00Z", "type": "assistant", "message": {"content": "Sure, fixing now"}}),
    ]
    transcript.write_text("\n".join(lines))

    info = extract_transcript_info(transcript)

    assert info["started_at"] == "2025-01-01T00:00:00Z"
    assert info["topic"] == "Fix the bug"
    assert info["last_activity"] == "2025-01-01T00:01:00Z"
    assert len(info["recent_messages"]) == 2


def test_extract_transcript_info_empty_file(tmp_path: Path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("")

    info = extract_transcript_info(transcript)

    assert info == {}


def test_discover_sessions_uses_terminal_title(tmp_path: Path):
    """Alive sessions should use terminal tab title as topic when available."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    data = {"sessionId": "term-title-sess", "cwd": "/workspace", "startTime": "2025-01-01T00:00:00Z"}
    (sessions_dir / "77777.json").write_text(json.dumps(data))

    project_dir = tmp_path / "projects" / "Users-workspace"
    project_dir.mkdir(parents=True)
    transcript = project_dir / "term-title-sess.jsonl"
    transcript.write_text(json.dumps({
        "timestamp": "2025-01-01T00:00:00Z",
        "type": "user",
        "message": {"content": "Fix the bug"},
    }))

    with (
        patch("duct.session.is_pid_alive", return_value=True),
        patch("duct.terminal.get_ttys", return_value={77777: "ttys042"}),
        patch("duct.terminal.get_terminal_title", return_value="fix-auth-bug"),
    ):
        sessions = discover_sessions(claude_dir=tmp_path, lookback_hours=9999)

    matched = [s for s in sessions if s["session_id"] == "term-title-sess"]
    assert len(matched) == 1
    assert matched[0]["topic"] == "fix-auth-bug"


def test_discover_sessions_falls_back_to_transcript_topic(tmp_path: Path):
    """When terminal title is unavailable, topic should come from first user message."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    data = {"sessionId": "fallback-sess", "cwd": "/workspace", "startTime": "2025-01-01T00:00:00Z"}
    (sessions_dir / "88888.json").write_text(json.dumps(data))

    project_dir = tmp_path / "projects" / "Users-workspace"
    project_dir.mkdir(parents=True)
    transcript = project_dir / "fallback-sess.jsonl"
    transcript.write_text(json.dumps({
        "timestamp": "2025-01-01T00:00:00Z",
        "type": "user",
        "message": {"content": "Fix the bug"},
    }))

    with (
        patch("duct.session.is_pid_alive", return_value=True),
        patch("duct.terminal.get_tty", return_value="ttys042"),
        patch("duct.terminal.get_terminal_title", return_value=None),
    ):
        sessions = discover_sessions(claude_dir=tmp_path, lookback_hours=9999)

    matched = [s for s in sessions if s["session_id"] == "fallback-sess"]
    assert len(matched) == 1
    assert matched[0]["topic"] == "Fix the bug"


def test_get_terminal_title_returns_none_on_error():
    """Error during subprocess calls should return None, not raise."""
    with (
        patch("shutil.which", return_value=None),
        patch("platform.system", return_value="Linux"),
    ):
        assert get_terminal_title("ttys042") is None


def test_extract_transcript_info_content_blocks(tmp_path: Path):
    """Content as a list of blocks rather than a plain string."""
    transcript = tmp_path / "session.jsonl"
    lines = [
        json.dumps({
            "timestamp": "2025-01-01T00:00:00Z",
            "type": "user",
            "message": {"content": [{"type": "text", "text": "Implement feature X"}]},
        }),
        json.dumps({
            "timestamp": "2025-01-01T00:01:00Z",
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Working on it"}]},
        }),
    ]
    transcript.write_text("\n".join(lines))

    info = extract_transcript_info(transcript)

    assert info["topic"] == "Implement feature X"
    assert info["recent_messages"][0]["text"] == "Implement feature X"
    assert info["recent_messages"][1]["text"] == "Working on it"


# ---------------------------------------------------------------------------
# discover_sessions — PID files
# ---------------------------------------------------------------------------


def test_discover_sessions_pid_files(tmp_path: Path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    data = {"sessionId": "abc123", "cwd": "/some/path", "startTime": "2025-01-01T00:00:00Z"}
    (sessions_dir / "12345.json").write_text(json.dumps(data))

    with patch("duct.session.is_pid_alive", return_value=True):
        sessions = discover_sessions(claude_dir=tmp_path)

    assert len(sessions) == 1
    s = sessions[0]
    assert s["session_id"] == "abc123"
    assert s["pid"] == 12345
    assert s["alive"] is True
    assert s["status"] == "ready"


def test_discover_sessions_dead_pid(tmp_path: Path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    data = {"sessionId": "dead1", "cwd": "/path", "startTime": "2025-01-01T00:00:00Z"}
    (sessions_dir / "99999.json").write_text(json.dumps(data))

    with patch("duct.session.is_pid_alive", return_value=False):
        sessions = discover_sessions(claude_dir=tmp_path)

    assert sessions[0]["status"] == "terminated"
    assert sessions[0]["alive"] is False


# ---------------------------------------------------------------------------
# discover_sessions — transcripts
# ---------------------------------------------------------------------------


def test_discover_sessions_transcripts(tmp_path: Path):
    project_dir = tmp_path / "projects" / "Users-foo-workspace"
    project_dir.mkdir(parents=True)
    transcript = project_dir / "sess-001.jsonl"
    lines = [
        json.dumps({"timestamp": "2025-01-01T00:00:00Z", "type": "user", "message": {"content": "Hello"}}),
    ]
    transcript.write_text("\n".join(lines))

    sessions = discover_sessions(claude_dir=tmp_path, lookback_hours=9999)

    assert len(sessions) == 1
    s = sessions[0]
    assert s["session_id"] == "sess-001"
    assert s["cwd"] == "/Users/foo/workspace"
    assert s["topic"] == "Hello"


def test_discover_sessions_merge_pid_and_transcript(tmp_path: Path):
    """When a PID file and transcript share the same session ID, topic is merged."""
    # PID file
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    data = {"sessionId": "shared-id", "cwd": "/workspace", "startTime": "2025-01-01T00:00:00Z"}
    (sessions_dir / "11111.json").write_text(json.dumps(data))

    # Transcript with same session ID
    project_dir = tmp_path / "projects" / "Users-workspace"
    project_dir.mkdir(parents=True)
    transcript = project_dir / "shared-id.jsonl"
    lines = [
        json.dumps({"timestamp": "2025-01-01T00:00:00Z", "type": "user", "message": {"content": "My topic"}}),
    ]
    transcript.write_text("\n".join(lines))

    with patch("duct.session.is_pid_alive", return_value=True):
        sessions = discover_sessions(claude_dir=tmp_path, lookback_hours=9999)

    assert len(sessions) == 1
    assert sessions[0]["topic"] == "My topic"
    assert sessions[0]["pid"] == 11111


def test_discover_sessions_lookback_cutoff(tmp_path: Path):
    """Old transcripts outside lookback window should be excluded."""
    project_dir = tmp_path / "projects" / "Users-old"
    project_dir.mkdir(parents=True)
    transcript = project_dir / "old-session.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"content": "old"}}))
    # Set mtime to 1 week ago
    old_time = time.time() - (7 * 24 * 3600)
    import os
    os.utime(transcript, (old_time, old_time))

    sessions = discover_sessions(claude_dir=tmp_path, lookback_hours=0)

    assert len(sessions) == 0


# ---------------------------------------------------------------------------
# match_session_ticket
# ---------------------------------------------------------------------------


def test_match_session_ticket_found():
    session = {"cwd": "/workspace/PROJ-123-feature"}
    assert match_session_ticket(session, {"PROJ-123", "OTHER-1"}) == "PROJ-123"


def test_match_session_ticket_no_match():
    session = {"cwd": "/workspace/unrelated"}
    assert match_session_ticket(session, {"PROJ-123"}) is None


# ---------------------------------------------------------------------------
# CLI: session list
# ---------------------------------------------------------------------------


def _mock_sessions():
    return [
        {
            "session_id": "active-001",
            "pid": 1001,
            "cwd": "/workspace/PROJ-1-feature",
            "started_at": "2025-01-01T00:00:00Z",
            "alive": True,
            "status": "working",
            "topic": "Working on feature",
            "last_activity": "2025-01-01T01:00:00Z",
            "recent_messages": [],
        },
        {
            "session_id": "dead-002",
            "pid": 2002,
            "cwd": "/workspace/PROJ-2-bugfix",
            "started_at": "2025-01-01T00:00:00Z",
            "alive": False,
            "status": "terminated",
            "topic": "Bug investigation",
            "last_activity": "2025-01-01T00:30:00Z",
            "recent_messages": [],
        },
    ]


def test_session_list_no_sessions(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    with patch("duct.cli.session_cmd.discover_sessions", return_value=[]):
        result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "session", "list"])

    assert result.exit_code == 0, result.output
    assert "No active sessions" in result.output


def test_session_list_json(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    with patch("duct.cli.session_cmd.discover_sessions", return_value=_mock_sessions()):
        result = runner.invoke(
            cli, ["--json", "--workspace-root", str(tmp_path), "session", "list", "--all"]
        )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    # JSON output is grouped by ticket key
    assert isinstance(data, dict)
    all_sessions = [s for group in data.values() for s in group]
    assert len(all_sessions) == 2
    # Verify key fields are present (this catches NameError-class bugs)
    for entry in all_sessions:
        assert "session_id" in entry
        assert "pid" in entry
        assert "status" in entry
        assert "ticket" in entry


def test_session_list_json_grouped_by_ticket(tmp_path: Path):
    """Sessions sharing a ticket should be grouped under the same key."""
    _init_workspace(tmp_path)
    # Create ticket dirs so they can be matched
    (tmp_path / "PROJ-1-feature" / "orchestrator").mkdir(parents=True)
    (tmp_path / "PROJ-2-bugfix" / "orchestrator").mkdir(parents=True)

    sessions = [
        {
            "session_id": "s1", "pid": 1, "cwd": "/workspace/PROJ-1-feature",
            "started_at": "", "alive": True, "status": "working",
            "topic": "a", "last_activity": "", "recent_messages": [],
        },
        {
            "session_id": "s2", "pid": 2, "cwd": "/workspace/PROJ-2-bugfix",
            "started_at": "", "alive": True, "status": "working",
            "topic": "b", "last_activity": "", "recent_messages": [],
        },
        {
            "session_id": "s3", "pid": 3, "cwd": "/workspace/PROJ-1-feature",
            "started_at": "", "alive": True, "status": "working",
            "topic": "c", "last_activity": "", "recent_messages": [],
        },
        {
            "session_id": "s4", "pid": 4, "cwd": "/workspace/unrelated",
            "started_at": "", "alive": True, "status": "working",
            "topic": "d", "last_activity": "", "recent_messages": [],
        },
    ]

    with patch("duct.cli.session_cmd.discover_sessions", return_value=sessions):
        result = runner_invoke_json(
            tmp_path, ["session", "list", "--all"]
        )

    data = json.loads(result.output.strip())
    assert "PROJ-1" in data
    assert len(data["PROJ-1"]) == 2
    assert "PROJ-2" in data
    assert len(data["PROJ-2"]) == 1
    # Unmatched session goes under "-"
    assert "-" in data
    assert len(data["-"]) == 1


def runner_invoke_json(root, args):
    runner = CliRunner()
    return runner.invoke(cli, ["--json", "--workspace-root", str(root)] + args)


def test_session_list_filters_terminated_by_default(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    with patch("duct.cli.session_cmd.discover_sessions", return_value=_mock_sessions()):
        result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "session", "list"])

    assert result.exit_code == 0, result.output
    assert "working" in result.output
    # terminated session should not appear (filtered by alive flag)
    assert "dead-002" not in result.output


def test_session_list_all_shows_terminated(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    with patch("duct.cli.session_cmd.discover_sessions", return_value=_mock_sessions()):
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "session", "list", "--all"]
        )

    assert result.exit_code == 0, result.output
    assert "working" in result.output
    assert "terminated" in result.output


# ---------------------------------------------------------------------------
# CLI: session show
# ---------------------------------------------------------------------------


def test_session_show_found(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    with patch("duct.cli.session_cmd.discover_sessions", return_value=_mock_sessions()):
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "session", "show", "active"]
        )

    assert result.exit_code == 0, result.output
    assert "active-001" in result.output


def test_session_show_not_found(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    with patch("duct.cli.session_cmd.discover_sessions", return_value=_mock_sessions()):
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "session", "show", "nonexistent"]
        )

    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# infer_session_status
# ---------------------------------------------------------------------------


def _assistant_entry(stop_reason, content=None):
    """Helper to build a transcript assistant entry."""
    entry = {
        "type": "assistant",
        "message": {"content": content or "done", "stop_reason": stop_reason},
        "stop_reason": stop_reason,
    }
    return json.dumps(entry)


def test_infer_status_end_turn(tmp_path: Path):
    t = tmp_path / "s.jsonl"
    t.write_text(_assistant_entry("end_turn"))
    assert infer_session_status(t) == "ready"


def test_infer_status_tool_use_ask_user(tmp_path: Path):
    t = tmp_path / "s.jsonl"
    content = [
        {"type": "text", "text": "Let me ask"},
        {"type": "tool_use", "name": "AskUserQuestion", "id": "x", "input": {}},
    ]
    t.write_text(_assistant_entry("tool_use", content))
    assert infer_session_status(t) == "waiting"


def test_infer_status_tool_use_exit_plan(tmp_path: Path):
    t = tmp_path / "s.jsonl"
    content = [
        {"type": "text", "text": "Here is the plan"},
        {"type": "tool_use", "name": "ExitPlanMode", "id": "x", "input": {}},
    ]
    t.write_text(_assistant_entry("tool_use", content))
    assert infer_session_status(t) == "ready"


def test_infer_status_tool_use_enter_plan(tmp_path: Path):
    """EnterPlanMode is an ordinary tool call — status is `working`. The
    orthogonal plan-mode dimension lives on SessionInfo.mode and is sourced
    from pane-text inspection (see duct.pane_status), not from the transcript.
    """
    t = tmp_path / "s.jsonl"
    content = [
        {"type": "text", "text": "Let me plan this out"},
        {"type": "tool_use", "name": "EnterPlanMode", "id": "x", "input": {}},
    ]
    t.write_text(_assistant_entry("tool_use", content))
    assert infer_session_status(t) == "working"


def test_infer_status_tool_use_other(tmp_path: Path):
    t = tmp_path / "s.jsonl"
    content = [
        {"type": "tool_use", "name": "Bash", "id": "x", "input": {"command": "ls"}},
    ]
    t.write_text(_assistant_entry("tool_use", content))
    assert infer_session_status(t) == "working"


# ---------------------------------------------------------------------------
# discover_sessions idle gate (working <-> waiting refinement)
# ---------------------------------------------------------------------------


def _alive_session_with_status(tmp_path: Path, content: list, *, pid: int = 11111) -> Path:
    """Create a PID file + matching transcript whose last assistant message
    carries ``content`` (a tool_use block list), so ``infer_session_status``
    classifies it. Returns the claude_dir to pass to ``discover_sessions``.
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / f"{pid}.json").write_text(
        json.dumps({"sessionId": "sid", "cwd": "/workspace", "startTime": "2025-01-01T00:00:00Z"})
    )
    project_dir = tmp_path / "projects" / "Users-workspace"
    project_dir.mkdir(parents=True)
    (project_dir / "sid.jsonl").write_text(_assistant_entry("tool_use", content))
    return tmp_path


_BASH_TOOL = [{"type": "tool_use", "name": "Bash", "id": "x", "input": {"command": "ls"}}]
_ASK_USER = [{"type": "tool_use", "name": "AskUserQuestion", "id": "x", "input": {}}]


def test_idle_gate_active_turn_keeps_working(tmp_path: Path):
    """A working session whose transcript is momentarily quiet stays `working`
    while its caffeinate keep-awake is alive — the mid-step quiet that used to
    flap it to `waiting`."""
    claude_dir = _alive_session_with_status(tmp_path, _BASH_TOOL)
    with patch("duct.session.is_pid_alive", return_value=True), \
         patch("duct.session._transcript_is_idle", return_value=True), \
         patch("duct.session._has_active_children", return_value=True):
        sessions = discover_sessions(claude_dir=claude_dir, lookback_hours=9999)
    assert sessions[0]["status"] == "working"


def test_idle_gate_quiet_and_no_active_turn_becomes_waiting(tmp_path: Path):
    """A working session that is quiet AND has no active turn (no caffeinate)
    is genuinely blocked for input — demote to `waiting`."""
    claude_dir = _alive_session_with_status(tmp_path, _BASH_TOOL)
    with patch("duct.session.is_pid_alive", return_value=True), \
         patch("duct.session._transcript_is_idle", return_value=True), \
         patch("duct.session._has_active_children", return_value=False):
        sessions = discover_sessions(claude_dir=claude_dir, lookback_hours=9999)
    assert sessions[0]["status"] == "waiting"


def test_idle_gate_fresh_transcript_stays_working(tmp_path: Path):
    """A working session actively writing its transcript stays `working`
    without consulting the active-turn signal."""
    claude_dir = _alive_session_with_status(tmp_path, _BASH_TOOL)
    with patch("duct.session.is_pid_alive", return_value=True), \
         patch("duct.session._transcript_is_idle", return_value=False), \
         patch("duct.session._has_active_children", return_value=False):
        sessions = discover_sessions(claude_dir=claude_dir, lookback_hours=9999)
    assert sessions[0]["status"] == "working"


def test_idle_gate_waiting_with_active_turn_resumes_working(tmp_path: Path):
    """An AskUserQuestion that the user has already answered presents as
    `waiting` from the transcript; an active turn means Claude has resumed,
    so it flips back to `working`."""
    claude_dir = _alive_session_with_status(tmp_path, _ASK_USER)
    with patch("duct.session.is_pid_alive", return_value=True), \
         patch("duct.session._has_active_children", return_value=True):
        sessions = discover_sessions(claude_dir=claude_dir, lookback_hours=9999)
    assert sessions[0]["status"] == "working"


def test_infer_status_ask_user_user_responded(tmp_path: Path):
    """AskUserQuestion followed by a user message should return 'ready'."""
    t = tmp_path / "s.jsonl"
    content = [
        {"type": "text", "text": "Which option?"},
        {"type": "tool_use", "name": "AskUserQuestion", "id": "x", "input": {}},
    ]
    lines = [
        _assistant_entry("tool_use", content),
        json.dumps({"type": "user", "message": {"content": "Option A"}}),
    ]
    t.write_text("\n".join(lines))
    assert infer_session_status(t) == "ready"


def test_infer_status_null_stop_reason(tmp_path: Path):
    t = tmp_path / "s.jsonl"
    entry = {"type": "assistant", "message": {"content": "generating..."}, "stop_reason": None}
    t.write_text(json.dumps(entry))
    assert infer_session_status(t) == "working"


def test_infer_status_no_assistant_message(tmp_path: Path):
    t = tmp_path / "s.jsonl"
    entry = {"type": "user", "message": {"content": "hello"}}
    t.write_text(json.dumps(entry))
    assert infer_session_status(t) == "working"


def test_infer_status_empty_file(tmp_path: Path):
    t = tmp_path / "s.jsonl"
    t.write_text("")
    assert infer_session_status(t) == "working"


# ---------------------------------------------------------------------------
# apply_recency_status / apply_recency_decoration
# ---------------------------------------------------------------------------


from datetime import datetime, timedelta, timezone


_NOW = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


def _iso(offset_seconds: int) -> str:
    return (_NOW - timedelta(seconds=offset_seconds)).isoformat()


def test_recency_status_ready_within_done_window_becomes_done():
    assert apply_recency_status(
        "ready", _iso(60), now=_NOW,
        done_window_seconds=900, stale_after_seconds=14400,
    ) == "done"


def test_recency_status_ready_just_past_done_window_stays_ready():
    assert apply_recency_status(
        "ready", _iso(1800), now=_NOW,
        done_window_seconds=900, stale_after_seconds=14400,
    ) == "ready"


def test_recency_status_ready_past_stale_threshold_becomes_stale():
    assert apply_recency_status(
        "ready", _iso(18000), now=_NOW,
        done_window_seconds=900, stale_after_seconds=14400,
    ) == "stale"


@pytest.mark.parametrize("status", ["working", "waiting", "terminated"])
def test_recency_status_non_ready_passes_through(status: str):
    assert apply_recency_status(
        status, _iso(60), now=_NOW,
        done_window_seconds=900, stale_after_seconds=14400,
    ) == status
    assert apply_recency_status(
        status, _iso(99999), now=_NOW,
        done_window_seconds=900, stale_after_seconds=14400,
    ) == status


def test_recency_status_empty_timestamp_passes_through():
    assert apply_recency_status(
        "ready", "", now=_NOW,
        done_window_seconds=900, stale_after_seconds=14400,
    ) == "ready"


def test_recency_status_malformed_timestamp_passes_through():
    assert apply_recency_status(
        "ready", "not-a-timestamp", now=_NOW,
        done_window_seconds=900, stale_after_seconds=14400,
    ) == "ready"


def test_recency_status_naive_timestamp_assumed_utc():
    naive_iso = (_NOW - timedelta(seconds=60)).replace(tzinfo=None).isoformat()
    assert apply_recency_status(
        "ready", naive_iso, now=_NOW,
        done_window_seconds=900, stale_after_seconds=14400,
    ) == "done"


def test_recency_decoration_mutates_in_place():
    sessions = [
        {"status": "ready", "last_activity": _iso(60)},
        {"status": "ready", "last_activity": _iso(18000)},
        {"status": "working", "last_activity": _iso(60)},
    ]
    apply_recency_decoration(
        sessions, now=_NOW,
        done_window_seconds=900, stale_after_seconds=14400,
    )
    assert [s["status"] for s in sessions] == ["done", "stale", "working"]


# ---------------------------------------------------------------------------
# CLI: session start
# ---------------------------------------------------------------------------


def test_session_start_missing_ticket(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--workspace-root", str(tmp_path), "session", "start", "PROJ-999"]
    )

    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "sync" in result.output.lower()


def test_session_start_passthrough_args(tmp_path: Path):
    _init_workspace(tmp_path)
    ticket_dir = tmp_path / "PROJ-1-feature"
    (ticket_dir / "orchestrator").mkdir(parents=True)

    runner = CliRunner()

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("duct.cli.session_cmd.subprocess.run") as mock_run,
    ):
        result = runner.invoke(
            cli,
            [
                "--workspace-root", str(tmp_path),
                "session", "start", "PROJ-1",
                "-p", "do stuff",
                "--", "--dangerously-skip-permissions", "--add-dir", "/tmp/foo",
            ],
        )

    assert result.exit_code == 0, result.output
    cmd = mock_run.call_args[0][0]
    assert "--dangerously-skip-permissions" in cmd
    assert "--add-dir" in cmd
    assert "/tmp/foo" in cmd
    # The known options should still be handled
    assert "-p" in cmd


def test_session_start_no_extra_args(tmp_path: Path):
    _init_workspace(tmp_path)
    ticket_dir = tmp_path / "PROJ-1-feature"
    (ticket_dir / "orchestrator").mkdir(parents=True)

    runner = CliRunner()

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("duct.cli.session_cmd.subprocess.run") as mock_run,
    ):
        result = runner.invoke(
            cli,
            ["--workspace-root", str(tmp_path), "session", "start", "PROJ-1"],
        )

    assert result.exit_code == 0, result.output
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "/usr/bin/claude"
    assert "--dangerously-skip-permissions" not in cmd


def test_session_start_skip_permissions(tmp_path: Path):
    _init_workspace(tmp_path)
    ticket_dir = tmp_path / "PROJ-1-feature"
    (ticket_dir / "orchestrator").mkdir(parents=True)

    runner = CliRunner()

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("duct.cli.session_cmd.subprocess.run") as mock_run,
    ):
        result = runner.invoke(
            cli,
            [
                "--workspace-root", str(tmp_path),
                "session", "start", "--skip-permissions", "PROJ-1",
            ],
        )

    assert result.exit_code == 0, result.output
    cmd = mock_run.call_args[0][0]
    assert "--dangerously-skip-permissions" in cmd


def test_session_start_skip_permissions_requires_sandbox(tmp_path: Path):
    _init_workspace(tmp_path)
    # Write config with sandbox disabled
    (tmp_path / "config.yaml").write_text(
        "workspace:\n  root: .\nsandbox:\n  enabled: false\n"
    )
    ticket_dir = tmp_path / "PROJ-1-feature"
    (ticket_dir / "orchestrator").mkdir(parents=True)

    runner = CliRunner()

    with patch("shutil.which", return_value="/usr/bin/claude"):
        result = runner.invoke(
            cli,
            [
                "--workspace-root", str(tmp_path),
                "session", "start", "--skip-permissions", "PROJ-1",
            ],
        )

    assert result.exit_code != 0
    assert "sandbox" in result.output.lower()


def test_session_start_uses_config_extra_args(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        "workspace:\n  root: .\nsession:\n  extraArgs:\n    - '--model'\n    - 'sonnet'\n"
    )
    ticket_dir = tmp_path / "PROJ-1-feature"
    (ticket_dir / "orchestrator").mkdir(parents=True)

    runner = CliRunner()

    with (
        patch("shutil.which", return_value="/usr/bin/claude"),
        patch("duct.cli.session_cmd.subprocess.run") as mock_run,
    ):
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "session", "start", "PROJ-1"],
        )

    assert result.exit_code == 0, result.output
    cmd = mock_run.call_args[0][0]
    assert "--model" in cmd
    assert "sonnet" in cmd


def test_session_start_missing_claude_binary(tmp_path: Path):
    _init_workspace(tmp_path)
    # Create a ticket directory so we get past the first check
    ticket_dir = tmp_path / "PROJ-1-feature"
    (ticket_dir / "orchestrator").mkdir(parents=True)

    runner = CliRunner()

    with patch("shutil.which", return_value=None):
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "session", "start", "PROJ-1"]
        )

    assert result.exit_code != 0
    assert "not found" in result.output.lower() or "claude" in result.output.lower()


# ---------------------------------------------------------------------------
# CLI: session jump
# ---------------------------------------------------------------------------


def test_session_jump_activates_tab(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    sessions = [_mock_sessions()[0]]  # alive session

    with (
        patch("duct.cli.session_cmd.discover_sessions", return_value=sessions),
        patch("duct.cli.session_cmd.get_tty", return_value="ttys026"),
        patch("duct.cli.session_cmd.focus_terminal_tab", return_value=True),
    ):
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "session", "jump", "active"]
        )

    assert result.exit_code == 0, result.output
    assert "Jumped to session" in result.output
    assert "ttys026" in result.output


def test_session_jump_not_alive(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    sessions = [_mock_sessions()[1]]  # terminated session

    with patch("duct.cli.session_cmd.discover_sessions", return_value=sessions):
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "session", "jump", "dead"]
        )

    assert result.exit_code != 0
    assert "not running" in result.output


def test_session_jump_no_tty(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    sessions = [_mock_sessions()[0]]

    with (
        patch("duct.cli.session_cmd.discover_sessions", return_value=sessions),
        patch("duct.cli.session_cmd.get_tty", return_value=None),
    ):
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "session", "jump", "active"]
        )

    assert result.exit_code != 0
    assert "Could not determine TTY" in result.output
    assert "Fallback: cd" in result.output


def test_session_jump_no_terminal(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    sessions = [_mock_sessions()[0]]

    with (
        patch("duct.cli.session_cmd.discover_sessions", return_value=sessions),
        patch("duct.cli.session_cmd.get_tty", return_value="ttys026"),
        patch("duct.cli.session_cmd.focus_terminal_tab", return_value=False),
    ):
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "session", "jump", "active"]
        )

    assert result.exit_code != 0
    assert "No supported terminal emulator" in result.output
    assert "Fallback: cd" in result.output


def test_session_jump_not_found(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    with patch("duct.cli.session_cmd.discover_sessions", return_value=_mock_sessions()):
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "session", "jump", "nonexistent"]
        )

    assert result.exit_code != 0
    assert "No session found" in result.output


# ---------------------------------------------------------------------------
# _has_active_children — child process detection
# ---------------------------------------------------------------------------


def test_has_active_children_returns_false_on_error():
    """Error during subprocess call should return False, not raise."""
    with patch("duct.session.subprocess.run", side_effect=OSError("nope")):
        assert _has_active_children(99999) is False

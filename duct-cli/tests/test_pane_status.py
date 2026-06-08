"""Tests for duct.pane_status — the pane-text based session status detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from duct.pane_status import (
    apply_overrides,
    classify_activity,
    detect_mode,
    strip_ansi,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "pane_text"


def _load(name: str) -> str:
    return (FIXTURE_DIR / name).read_text()


# --- strip_ansi ---


def test_strip_ansi_removes_sgr_color_codes():
    colored = "\x1b[1;31mhello\x1b[0m world"
    assert strip_ansi(colored) == "hello world"


def test_strip_ansi_removes_osc_title_sequences():
    titled = "before\x1b]0;tab title\x07after"
    assert strip_ansi(titled) == "beforeafter"


def test_strip_ansi_removes_osc_with_string_terminator():
    titled = "a\x1b]2;title\x1b\\b"
    assert strip_ansi(titled) == "ab"


def test_strip_ansi_preserves_newlines_and_tabs():
    text = "line1\n\tline2\n\x1b[31mline3\x1b[0m\n"
    assert strip_ansi(text) == "line1\n\tline2\nline3\n"


def test_strip_ansi_leaves_plain_text_untouched():
    plain = "no escapes here, just text"
    assert strip_ansi(plain) == plain


# --- classify_activity + detect_mode over fixtures ---


@pytest.mark.parametrize(
    ("fixture", "expected_activity", "expected_mode"),
    [
        # Real captures from a live wezterm + Claude Code session
        ("working_real.txt",                          "working", "default"),
        ("ready_real.txt",                            None,      "default"),
        ("planning_real.txt",                         None,      "plan"),
        # Synthetic fixtures for states not easily captured live
        ("waiting_tool_approval.txt",                 "waiting", "default"),
        ("waiting_edit_approval.txt",                 "waiting", "default"),
        ("planning_thinking.txt",                     "working", "plan"),
        # Regression fixtures: stale anchors in scrollback must not false-trigger
        ("ready_scrollback_with_old_spinner.txt",     None,      "default"),
        ("ready_scrollback_with_old_plan_banner.txt", None,      "default"),
    ],
)
def test_fixture_classification(fixture: str, expected_activity, expected_mode):
    text = _load(fixture)
    assert classify_activity(text) == expected_activity
    assert detect_mode(text) == expected_mode


def test_orthogonality_planning_while_thinking():
    """The critical case: plan mode + working activity must resolve as both.

    Earlier we conflated these into a single `planning` status; the new model
    is that plan mode is persistent UI state, orthogonal to what Claude is
    currently doing. A session thinking inside plan mode is (working, plan).
    """
    text = _load("planning_thinking.txt")
    assert classify_activity(text) == "working"
    assert detect_mode(text) == "plan"


# --- apply_overrides ---


class _FakeAdapter:
    """A test double for WeztermAdapter that returns preloaded pane texts."""

    name = "wezterm"

    def __init__(self, pane_text_by_id: dict[int, str]) -> None:
        self._texts = pane_text_by_id

    def get_pane_text(self, pane_id: int) -> str | None:
        return self._texts.get(pane_id)


@pytest.fixture
def fake_wezterm(monkeypatch):
    """Patch terminal helpers so apply_overrides finds a deterministic pane map."""

    def _fake_list_panes():
        # One pane per pid, pane_id = pid * 10 (arbitrary) — map via tty_name
        return [
            {"pane_id": 1001, "tty_name": "/dev/ttys001"},
            {"pane_id": 1002, "tty_name": "/dev/ttys002"},
            {"pane_id": 1003, "tty_name": "/dev/ttys003"},
        ]

    tty_by_pid = {
        101: "ttys001",
        102: "ttys002",
        103: "ttys003",
    }

    def _fake_get_tty(pid: int):
        return tty_by_pid.get(pid)

    def _fake_get_ttys(pids):
        return {p: tty_by_pid[p] for p in pids if p in tty_by_pid}

    monkeypatch.setattr("duct.terminal._wezterm_list_panes", _fake_list_panes)
    monkeypatch.setattr("duct.terminal.get_tty", _fake_get_tty)
    monkeypatch.setattr("duct.terminal.get_ttys", _fake_get_ttys)
    return {"tty_by_pid": tty_by_pid}


def _session(pid: int, status: str) -> dict:
    return {
        "session_id": f"s{pid}",
        "pid": pid,
        "alive": True,
        "status": status,
    }


def test_apply_overrides_no_adapter_sets_empty_mode():
    sessions = [_session(101, "working")]
    apply_overrides(sessions, adapter=None)
    assert sessions[0]["status"] == "working"
    assert sessions[0]["mode"] == ""


def test_apply_overrides_non_wezterm_adapter_sets_empty_mode():
    sessions = [_session(101, "working")]

    class Other:
        name = "iterm2"

        def get_pane_text(self, pane_id):  # pragma: no cover — not reached
            return None

    apply_overrides(sessions, adapter=Other())
    assert sessions[0]["status"] == "working"
    assert sessions[0]["mode"] == ""


def test_apply_overrides_waiting_overrides_working(fake_wezterm):
    sessions = [_session(101, "working")]
    adapter = _FakeAdapter({1001: _load("waiting_tool_approval.txt")})
    apply_overrides(sessions, adapter=adapter)
    assert sessions[0]["status"] == "waiting"
    assert sessions[0]["mode"] == "default"


def test_apply_overrides_working_overrides_waiting(fake_wezterm):
    sessions = [_session(101, "waiting")]
    adapter = _FakeAdapter({1001: _load("working_real.txt")})
    apply_overrides(sessions, adapter=adapter)
    assert sessions[0]["status"] == "working"


def test_apply_overrides_working_overrides_ready(fake_wezterm):
    sessions = [_session(101, "ready")]
    adapter = _FakeAdapter({1001: _load("working_real.txt")})
    apply_overrides(sessions, adapter=adapter)
    assert sessions[0]["status"] == "working"


def test_apply_overrides_does_not_demote_waiting_to_ready(fake_wezterm):
    """Pane text that doesn't match any anchor must leave status alone."""
    sessions = [_session(101, "waiting")]
    adapter = _FakeAdapter({1001: _load("ready_real.txt")})
    apply_overrides(sessions, adapter=adapter)
    # classify_activity returns None for ready_prompt.txt → no override
    assert sessions[0]["status"] == "waiting"
    assert sessions[0]["mode"] == "default"


def test_apply_overrides_populates_plan_mode_regardless_of_activity(fake_wezterm):
    """mode is orthogonal — a session thinking in plan mode is (working, plan)."""
    sessions = [_session(101, "working")]
    adapter = _FakeAdapter({1001: _load("planning_thinking.txt")})
    apply_overrides(sessions, adapter=adapter)
    assert sessions[0]["status"] == "working"
    assert sessions[0]["mode"] == "plan"


def test_apply_overrides_plan_mode_while_idle(fake_wezterm):
    """Plan mode banner is visible but nothing matches activity — status keeps
    its transcript value, mode becomes 'plan'."""
    sessions = [_session(101, "ready")]
    adapter = _FakeAdapter({1001: _load("planning_real.txt")})
    apply_overrides(sessions, adapter=adapter)
    assert sessions[0]["status"] == "ready"
    assert sessions[0]["mode"] == "plan"


def test_apply_overrides_skips_dead_sessions(fake_wezterm):
    """A session with alive=False must be ignored even if it has a pid."""
    dead = {"session_id": "s999", "pid": 101, "alive": False, "status": "terminated"}
    adapter = _FakeAdapter({1001: _load("working_real.txt")})
    apply_overrides([dead], adapter=adapter)
    assert dead["status"] == "terminated"
    assert dead["mode"] == ""


def test_apply_overrides_multiple_sessions_parallel(fake_wezterm):
    """The batched path correctly routes pane text to the matching pid."""
    sessions = [
        _session(101, "working"),   # → waiting (approval visible)
        _session(102, "ready"),     # → working (spinner visible)
        _session(103, "working"),   # → keeps working + plan mode
    ]
    adapter = _FakeAdapter({
        1001: _load("waiting_tool_approval.txt"),
        1002: _load("working_real.txt"),
        1003: _load("planning_thinking.txt"),
    })
    apply_overrides(sessions, adapter=adapter)

    assert sessions[0]["status"] == "waiting"
    assert sessions[0]["mode"] == "default"
    assert sessions[1]["status"] == "working"
    assert sessions[1]["mode"] == "default"
    assert sessions[2]["status"] == "working"
    assert sessions[2]["mode"] == "plan"

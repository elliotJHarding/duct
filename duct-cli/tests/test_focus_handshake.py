"""Tests for the focused-session handshake in global_state."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from duct import global_state


@pytest.fixture(autouse=True)
def _isolate_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCT_STATE_DIR", str(tmp_path))


def test_set_and_read_roundtrip() -> None:
    global_state.set_focused_session_pid(4242)
    assert global_state.read_focused_session_pid() == 4242


def test_clear_with_none() -> None:
    global_state.set_focused_session_pid(4242)
    global_state.set_focused_session_pid(None)
    assert global_state.read_focused_session_pid() is None


def test_stale_entry_ignored(tmp_path: Path) -> None:
    focus = tmp_path / "focus.json"
    focus.write_text(json.dumps({"pid": 4242, "ts": time.time() - 9999}))
    assert global_state.read_focused_session_pid(max_age_s=120) is None


def test_missing_file_returns_none() -> None:
    assert global_state.read_focused_session_pid() is None

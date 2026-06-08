"""Tests for terminal focus detection used to suppress notifications."""

from __future__ import annotations

import duct.terminal as terminal
from duct.terminal import WeztermAdapter, focused_session_pid


class _FakeWezterm:
    """A WeztermAdapter stand-in with a fixed focused pane."""

    name = "wezterm"

    def __init__(self, pane_id: int | None) -> None:
        self._pane_id = pane_id

    def focused_pane_id(self) -> int | None:
        return self._pane_id


def _wire(monkeypatch, *, frontmost: str | None, panes, ttys) -> None:
    monkeypatch.setattr(terminal, "frontmost_bundle_id", lambda: frontmost)
    monkeypatch.setattr(terminal, "_wezterm_list_panes", lambda: panes)
    monkeypatch.setattr(terminal, "get_ttys", lambda pids: ttys)


_PANES = [
    {"pane_id": 4, "tty_name": "/dev/ttys004"},
    {"pane_id": 7, "tty_name": "/dev/ttys007"},
]
_TTYS = {101: "ttys004", 202: "ttys007"}


def test_focused_pane_maps_to_session_pid_when_wezterm_frontmost(monkeypatch) -> None:
    _wire(monkeypatch, frontmost=terminal._WEZTERM_BUNDLE_ID, panes=_PANES, ttys=_TTYS)
    adapter = _FakeWezterm(pane_id=7)
    assert focused_session_pid(adapter, [101, 202]) == 202


def test_not_focused_when_wezterm_not_frontmost(monkeypatch) -> None:
    # Focused pane resolves to a session, but the user is in another app.
    _wire(monkeypatch, frontmost="com.apple.Safari", panes=_PANES, ttys=_TTYS)
    adapter = _FakeWezterm(pane_id=7)
    assert focused_session_pid(adapter, [101, 202]) is None


def test_none_when_no_client_focus(monkeypatch) -> None:
    _wire(monkeypatch, frontmost=terminal._WEZTERM_BUNDLE_ID, panes=_PANES, ttys=_TTYS)
    assert focused_session_pid(_FakeWezterm(pane_id=None), [101, 202]) is None


def test_none_when_focused_pane_has_no_known_session(monkeypatch) -> None:
    # Focused pane is a non-session terminal (e.g. the TUI itself).
    _wire(monkeypatch, frontmost=terminal._WEZTERM_BUNDLE_ID, panes=_PANES, ttys=_TTYS)
    assert focused_session_pid(_FakeWezterm(pane_id=99), [101, 202]) is None


def test_non_wezterm_adapter_is_never_focused(monkeypatch) -> None:
    class _Other:
        name = "iterm2"

        def focused_pane_id(self):  # pragma: no cover - should not be reached
            raise AssertionError("must not be consulted for non-wezterm adapter")

    assert focused_session_pid(_Other(), [101]) is None
    assert focused_session_pid(None, [101]) is None


def test_iterm2_adapter_reports_no_focus() -> None:
    from duct.terminal import ITerm2Adapter

    assert ITerm2Adapter().focused_pane_id() is None


def test_wezterm_focused_pane_picks_least_idle_client(monkeypatch) -> None:
    captured: dict = {}

    class _Result:
        returncode = 0
        stdout = (
            '[{"focused_pane_id": 4, "idle_time": {"secs": 90}},'
            ' {"focused_pane_id": 7, "idle_time": {"secs": 2}}]'
        )

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(terminal, "_wezterm_bin", lambda: "/usr/bin/wezterm")
    monkeypatch.setattr(terminal, "_timed_run", _fake_run)

    assert WeztermAdapter().focused_pane_id() == 7
    assert "list-clients" in captured["cmd"]

"""Tests for the duct daemon command + launchd lifecycle."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from duct.cli import daemon_cmd


@pytest.fixture(autouse=True)
def _isolate_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUCT_STATE_DIR", str(tmp_path / "state"))


def test_build_plist_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("duct.cli.daemon_cmd.shutil.which", lambda _: "/usr/local/bin/duct")
    plist = daemon_cmd._build_plist()

    assert plist["Label"] == "com.duct.daemon"
    assert plist["ProgramArguments"] == ["/usr/local/bin/duct", "daemon", "run"]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    # The launchd-PATH gotcha mitigation: Homebrew + ~/.local/bin must be present.
    path = plist["EnvironmentVariables"]["PATH"]
    assert "/opt/homebrew/bin" in path
    assert str(Path.home() / ".local" / "bin") in path


def test_build_plist_pins_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("duct.cli.daemon_cmd.shutil.which", lambda _: "/usr/local/bin/duct")
    plist = daemon_cmd._build_plist(tmp_path)

    assert plist["ProgramArguments"] == [
        "/usr/local/bin/duct", "--workspace-root", str(tmp_path), "daemon", "run",
    ]
    assert plist["EnvironmentVariables"]["DUCT_ROOT"] == str(tmp_path)


def test_install_agent_persists_workspace_pointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plist = tmp_path / "com.duct.daemon.plist"
    monkeypatch.setattr("duct.cli.daemon_cmd._plist_path", lambda: plist)
    monkeypatch.setattr("duct.cli.daemon_cmd.shutil.which", lambda _: "/usr/local/bin/duct")
    monkeypatch.setattr(
        "duct.cli.daemon_cmd._launchctl",
        lambda *args: type("R", (), {"returncode": 0})(),
    )
    ws = tmp_path / "ws"
    ws.mkdir()

    daemon_cmd.install_agent(ws)

    from duct.global_state import load_state

    assert load_state().workspace_path == ws.resolve()


def test_is_installed_reflects_plist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plist = tmp_path / "com.duct.daemon.plist"
    monkeypatch.setattr("duct.cli.daemon_cmd._plist_path", lambda: plist)
    assert daemon_cmd.is_installed() is False
    plist.write_text("<plist/>")
    assert daemon_cmd.is_installed() is True


def test_install_agent_writes_plist_and_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plist = tmp_path / "com.duct.daemon.plist"
    monkeypatch.setattr("duct.cli.daemon_cmd._plist_path", lambda: plist)
    monkeypatch.setattr("duct.cli.daemon_cmd.shutil.which", lambda _: "/usr/local/bin/duct")
    calls: list[tuple] = []
    monkeypatch.setattr(
        "duct.cli.daemon_cmd._launchctl",
        lambda *args: calls.append(args) or type("R", (), {"returncode": 0})(),
    )

    daemon_cmd.install_agent()

    assert plist.exists()
    assert calls and calls[0][0] == "bootstrap"


def test_pidfile_guard_blocks_second_instance() -> None:
    assert daemon_cmd._acquire_pidfile() is True
    # A live pid already in the file (our own) blocks a second acquire.
    assert daemon_cmd._acquire_pidfile() is False
    daemon_cmd._release_pidfile()
    assert daemon_cmd._acquire_pidfile() is True
    daemon_cmd._release_pidfile()


def test_pidfile_recovers_from_dead_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    daemon_cmd._pidfile().parent.mkdir(parents=True, exist_ok=True)
    daemon_cmd._pidfile().write_text("999999999")  # not alive
    assert daemon_cmd._acquire_pidfile() is True
    assert daemon_cmd._read_pidfile(daemon_cmd._pidfile()) == os.getpid()
    daemon_cmd._release_pidfile()

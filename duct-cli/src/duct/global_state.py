"""Global state shared across all duct invocations.

State lives under ``~/.config/duct/`` (override via ``DUCT_STATE_DIR``).
Currently holds the workspace path; credentials are kept in a sibling
file by :mod:`duct.credentials`.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

_STATE_FILENAME = "state.yaml"
_FOCUS_FILENAME = "focus.json"


def state_dir() -> Path:
    """Return the directory holding duct's global state.

    Honours ``DUCT_STATE_DIR`` for tests / containers. Defaults to
    ``~/.config/duct``.
    """
    override = os.environ.get("DUCT_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "duct"


def state_file() -> Path:
    return state_dir() / _STATE_FILENAME


@dataclass(frozen=True)
class GlobalState:
    """Pointers into the user's duct setup."""

    workspace_path: Path | None = None


def load_state() -> GlobalState:
    path = state_file()
    if not path.exists():
        return GlobalState()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return GlobalState()
    workspace = raw.get("workspace_path")
    return GlobalState(
        workspace_path=Path(workspace).expanduser() if workspace else None,
    )


def save_state(state: GlobalState) -> None:
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, str] = {}
    if state.workspace_path is not None:
        data["workspace_path"] = str(state.workspace_path)
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")


def set_workspace_path(path: Path) -> None:
    """Persist *path* as the active workspace location."""
    current = load_state()
    save_state(GlobalState(workspace_path=path.expanduser().resolve()))
    _ = current  # currently no other fields, but keeps the merge intent explicit


# ---------------------------------------------------------------------------
# Focused-session handshake
#
# The TUI publishes which session pid it currently has docked so the daemon can
# suppress a notification for the session the user is already watching. File-
# based (no IPC) and best-effort: a stale entry (e.g. crashed TUI) is ignored
# after ``max_age_s`` so it never suppresses forever.
# ---------------------------------------------------------------------------


def _focus_file() -> Path:
    return state_dir() / _FOCUS_FILENAME


def set_focused_session_pid(pid: int | None) -> None:
    """Publish (or clear) the session pid the TUI is currently docked into."""
    path = _focus_file()
    try:
        if pid is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pid": pid, "ts": time.time()}), encoding="utf-8")
    except OSError:
        pass


def read_focused_session_pid(max_age_s: float = 120.0) -> int | None:
    """Return the docked session pid if fresh, else None."""
    path = _focus_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ts = raw.get("ts")
    pid = raw.get("pid")
    if not isinstance(pid, int) or not isinstance(ts, (int, float)):
        return None
    if time.time() - ts > max_age_s:
        return None
    return pid

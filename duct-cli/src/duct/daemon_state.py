"""On-disk daemon state: heartbeat + auto-orchestrate fire-slot.

Published by the daemon under ``{root}/.duct/`` so the TUI can present daemon
health and doctor can report it — all via the filesystem, no IPC. Kept out of
the CLI command module so both duct-cli (doctor) and duct-tui (status
indicator) import the readers without pulling in Click.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from duct import paths


def heartbeat_path(root: Path) -> Path:
    return paths.daemon_heartbeat(root)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_heartbeat(root: Path, **fields: object) -> None:
    """Merge *fields* into the heartbeat file, refreshing the tick timestamp."""
    path = heartbeat_path(root)
    current = read_heartbeat(root) or {}
    current.update(fields)
    current["last_tick_at"] = _now_iso()
    current["last_tick_ts"] = time.time()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    except OSError:
        pass


def read_heartbeat(root: Path) -> dict | None:
    try:
        return json.loads(heartbeat_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def heartbeat_age_seconds(root: Path) -> float | None:
    """Seconds since the daemon's last tick, or None if no heartbeat exists."""
    hb = read_heartbeat(root)
    ts = hb.get("last_tick_ts") if hb else None
    if not isinstance(ts, (int, float)):
        return None
    return max(0.0, time.time() - ts)


# --- auto-orchestrate fire-slot (one run per (date, hour) window) ---

def _orchestrate_state_path(root: Path) -> Path:
    return paths.orchestrate_state(root)


def read_last_orchestrate_slot(root: Path) -> str | None:
    try:
        raw = json.loads(_orchestrate_state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    slot = raw.get("last_slot")
    return slot if isinstance(slot, str) else None


def write_last_orchestrate_slot(root: Path, slot: str) -> None:
    path = _orchestrate_state_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"last_slot": slot, "at": _now_iso()}), encoding="utf-8"
        )
    except OSError:
        pass

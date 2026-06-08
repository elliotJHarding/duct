"""Cross-process orchestrator run lock.

Only one orchestrator should run per workspace at a time, whether launched by
the daemon's schedule or the TUI's manual "run now". The lock is a small JSON
file at ``{root}/.duct/orchestrator.lock`` holding the owning pid; a lock whose
pid is dead is treated as free (stale-lock recovery after a crash).

File-based, no IPC — consistent with duct's disk-as-bus model.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _lock_path(root: Path) -> Path:
    return root / ".duct" / "orchestrator.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False
    return True


def is_locked(root: Path) -> bool:
    """True if a live orchestrator run currently holds the lock."""
    path = _lock_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    pid = raw.get("pid")
    if not isinstance(pid, int):
        return False
    if not _pid_alive(pid):
        # Stale lock from a crashed run — clear it so we don't deadlock forever.
        path.unlink(missing_ok=True)
        return False
    return True


def acquire(root: Path) -> bool:
    """Try to take the lock. Returns False if a live run already holds it."""
    if is_locked(root):
        return False
    path = _lock_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"pid": os.getpid(), "started_at": time.time()}),
            encoding="utf-8",
        )
    except OSError:
        return False
    return True


def release(root: Path) -> None:
    """Release the lock if this process owns it."""
    path = _lock_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if raw.get("pid") == os.getpid():
        path.unlink(missing_ok=True)

"""Tests for the cross-process orchestrator run lock."""

from __future__ import annotations

import json
import os
from pathlib import Path

from duct import run_lock


def test_acquire_then_locked_then_release(tmp_path: Path) -> None:
    assert run_lock.is_locked(tmp_path) is False
    assert run_lock.acquire(tmp_path) is True
    assert run_lock.is_locked(tmp_path) is True
    # A second acquire by the same live owner is refused (already held).
    assert run_lock.acquire(tmp_path) is False
    run_lock.release(tmp_path)
    assert run_lock.is_locked(tmp_path) is False


def test_stale_lock_from_dead_pid_is_recovered(tmp_path: Path) -> None:
    lock = tmp_path / ".duct" / "orchestrator.lock"
    lock.parent.mkdir(parents=True)
    # A pid that is almost certainly not alive.
    lock.write_text(json.dumps({"pid": 999_999_999, "started_at": 0}))

    assert run_lock.is_locked(tmp_path) is False  # treated as free
    assert not lock.exists()  # stale lock cleared
    assert run_lock.acquire(tmp_path) is True
    run_lock.release(tmp_path)


def test_release_only_removes_own_lock(tmp_path: Path) -> None:
    lock = tmp_path / ".duct" / "orchestrator.lock"
    lock.parent.mkdir(parents=True)
    # Owned by a live process that isn't us (the test's parent shell/pytest).
    other = os.getppid()
    lock.write_text(json.dumps({"pid": other, "started_at": 0}))

    run_lock.release(tmp_path)  # not our lock — must not remove it
    assert lock.exists()

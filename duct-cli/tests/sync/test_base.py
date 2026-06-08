"""Tests for duct.sync.base.SyncCoordinator timestamp-on-success semantics."""

from __future__ import annotations

import time
from pathlib import Path

import yaml

from duct.models import SyncResult
from duct.sync.base import SyncCoordinator


class _FakeSource:
    """Minimal SyncSource stand-in with scripted behaviour."""

    def __init__(self, name: str, *, raises: Exception | None = None,
                 result: SyncResult | None = None):
        self.name = name
        self._raises = raises
        self._result = result
        self.call_count = 0

    def sync(self, root: Path) -> SyncResult:
        self.call_count += 1
        if self._raises is not None:
            raise self._raises
        assert self._result is not None
        return self._result


def _read_state(root: Path) -> dict[str, float]:
    state_path = root / ".sync_state.yaml"
    if not state_path.exists():
        return {}
    return yaml.safe_load(state_path.read_text()) or {}


class TestTimestampOnSuccess:
    def test_raising_source_does_not_advance_timestamp(self, tmp_path: Path):
        coordinator = SyncCoordinator(tmp_path, {"jira": 0})
        source = _FakeSource("jira", raises=RuntimeError("boom"))

        results = coordinator.run([source])

        assert source.call_count == 1
        assert _read_state(tmp_path).get("jira", 0.0) == 0.0
        # Failure surfaces as a SyncResult with errors rather than propagating.
        assert len(results) == 1
        assert results[0].source == "jira"
        assert results[0].errors  # non-empty
        assert "boom" in results[0].errors[0]

    def test_result_with_errors_does_not_advance_timestamp(self, tmp_path: Path):
        coordinator = SyncCoordinator(tmp_path, {"github": 0})
        source = _FakeSource(
            "github",
            result=SyncResult(
                source="github",
                tickets_synced=0,
                duration_seconds=0.01,
                errors=["auth failed"],
            ),
        )

        coordinator.run([source])

        assert _read_state(tmp_path).get("github", 0.0) == 0.0

    def test_successful_sync_advances_timestamp(self, tmp_path: Path):
        coordinator = SyncCoordinator(tmp_path, {"jira": 0})
        source = _FakeSource(
            "jira",
            result=SyncResult(
                source="jira",
                tickets_synced=3,
                duration_seconds=0.05,
            ),
        )

        before = time.time()
        coordinator.run([source])
        after = time.time()

        recorded = _read_state(tmp_path).get("jira", 0.0)
        assert before <= recorded <= after

    def test_one_source_failure_does_not_block_peer_success(self, tmp_path: Path):
        coordinator = SyncCoordinator(tmp_path, {"jira": 0, "github": 0})
        failing = _FakeSource("jira", raises=ValueError("api down"))
        succeeding = _FakeSource(
            "github",
            result=SyncResult(
                source="github",
                tickets_synced=1,
                duration_seconds=0.01,
            ),
        )

        results = coordinator.run([failing, succeeding])

        state = _read_state(tmp_path)
        assert state.get("jira", 0.0) == 0.0
        assert state.get("github", 0.0) > 0.0
        # Both sources are represented in results.
        assert {r.source for r in results} == {"jira", "github"}

    def test_preexisting_timestamp_preserved_on_failure(self, tmp_path: Path):
        state_path = tmp_path / ".sync_state.yaml"
        original_ts = 1_700_000_000.0
        state_path.write_text(yaml.dump({"jira": original_ts}))

        coordinator = SyncCoordinator(tmp_path, {"jira": 0})
        source = _FakeSource("jira", raises=RuntimeError("boom"))
        coordinator.run([source])

        assert _read_state(tmp_path).get("jira") == original_ts

"""Tests for orchestrator run discovery helpers."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from duct import paths
from duct.orchestrator import list_runs, read_run_body


def _write_run(ws: Path, name: str, body: str) -> Path:
    runs = paths.runs_dir(ws)
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"{name}.md"
    path.write_text(dedent(body).lstrip())
    return path


def test_list_runs_returns_empty_when_no_runs_dir(tmp_workspace: Path) -> None:
    assert list_runs(tmp_workspace) == []


def test_list_runs_workspace_only_by_default(tmp_workspace: Path) -> None:
    _write_run(
        tmp_workspace,
        "2025-05-15T10-00-00",
        """
        ---
        timestamp: 2025-05-15T10-00-00
        turns: 3
        exit_code: 0
        ---
        # workspace run
        """,
    )
    _write_run(
        tmp_workspace,
        "2025-05-15T11-00-00",
        """
        ---
        timestamp: 2025-05-15T11-00-00
        ticket: PROJ-123
        turns: 2
        exit_code: 0
        ---
        # ticket run
        """,
    )

    runs = list_runs(tmp_workspace)
    assert len(runs) == 1
    assert runs[0].ticket_key is None
    assert runs[0].exit_code == 0


def test_list_runs_sorted_newest_first(tmp_workspace: Path) -> None:
    for name in (
        "2025-05-10T08-00-00",
        "2025-05-15T09-00-00",
        "2025-05-12T14-30-00",
    ):
        _write_run(
            tmp_workspace,
            name,
            f"""
            ---
            timestamp: {name}
            exit_code: 0
            ---
            body
            """,
        )

    runs = list_runs(tmp_workspace)
    stamps = [r.timestamp.strftime("%Y-%m-%dT%H-%M-%S") for r in runs]
    assert stamps == [
        "2025-05-15T09-00-00",
        "2025-05-12T14-30-00",
        "2025-05-10T08-00-00",
    ]


def test_list_runs_honours_limit(tmp_workspace: Path) -> None:
    for i in range(7):
        name = f"2025-05-{10 + i:02d}T09-00-00"
        _write_run(
            tmp_workspace,
            name,
            f"""
            ---
            timestamp: {name}
            exit_code: 0
            ---
            body
            """,
        )

    runs = list_runs(tmp_workspace, limit=5)
    assert len(runs) == 5
    # Newest five: 2025-05-16..12
    assert runs[0].timestamp.day == 16
    assert runs[-1].timestamp.day == 12


def test_list_runs_filters_to_ticket(tmp_workspace: Path) -> None:
    _write_run(
        tmp_workspace,
        "2025-05-15T10-00-00",
        """
        ---
        timestamp: 2025-05-15T10-00-00
        ticket: PROJ-123
        exit_code: 0
        ---
        body
        """,
    )
    _write_run(
        tmp_workspace,
        "2025-05-15T11-00-00",
        """
        ---
        timestamp: 2025-05-15T11-00-00
        ticket: OTHER-1
        exit_code: 0
        ---
        body
        """,
    )

    runs = list_runs(tmp_workspace, ticket_key="PROJ-123")
    assert len(runs) == 1
    assert runs[0].ticket_key == "PROJ-123"


def test_list_runs_skips_malformed_files(tmp_workspace: Path) -> None:
    # Missing closing frontmatter
    _write_run(
        tmp_workspace,
        "2025-05-15T10-00-00",
        """
        ---
        timestamp: 2025-05-15T10-00-00
        body without close
        """,
    )
    # No frontmatter at all
    _write_run(
        tmp_workspace,
        "2025-05-15T11-00-00",
        "# just a markdown body\n",
    )
    # Unparseable timestamp
    _write_run(
        tmp_workspace,
        "2025-05-15T12-00-00",
        """
        ---
        timestamp: not-a-date
        exit_code: 0
        ---
        body
        """,
    )
    # Valid one to prove the rest aren't dropping it
    _write_run(
        tmp_workspace,
        "2025-05-15T13-00-00",
        """
        ---
        timestamp: 2025-05-15T13-00-00
        exit_code: 0
        ---
        body
        """,
    )

    runs = list_runs(tmp_workspace)
    assert len(runs) == 1
    assert runs[0].timestamp.hour == 13


def test_read_run_body_strips_frontmatter(tmp_workspace: Path) -> None:
    path = _write_run(
        tmp_workspace,
        "2025-05-15T10-00-00",
        """
        ---
        timestamp: 2025-05-15T10-00-00
        exit_code: 0
        ---
        # Orchestrator run

        > final text
        """,
    )
    body = read_run_body(path)
    assert body.startswith("# Orchestrator run")
    assert "timestamp:" not in body


def test_read_run_body_returns_full_text_when_no_frontmatter(tmp_workspace: Path) -> None:
    path = _write_run(
        tmp_workspace,
        "2025-05-15T10-00-00",
        "# plain markdown\nbody\n",
    )
    assert read_run_body(path).startswith("# plain markdown")

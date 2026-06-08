"""Tests for per-ticket task CRUD."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import yaml

from duct.tasks import (
    add_task,
    delete_task,
    edit_task,
    get_tasks,
    reorder_task,
    toggle_task,
)


def _ticket_dir(root: Path, key: str = "PROJ-1") -> Path:
    ticket_dir = root / f"{key}-feature"
    (ticket_dir / "orchestrator").mkdir(parents=True, exist_ok=True)
    return ticket_dir


def _write_tasks(root: Path, key: str, tasks: list[dict]) -> Path:
    ticket_dir = _ticket_dir(root, key)
    path = ticket_dir / "orchestrator" / "tasks.yaml"
    path.write_text(yaml.dump({"tasks": tasks}, sort_keys=False))
    return path


class TestGetTasks:
    def test_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        _ticket_dir(tmp_path)
        assert get_tasks(tmp_path, "PROJ-1") == []

    def test_loads_and_sorts_by_position(self, tmp_path: Path) -> None:
        _write_tasks(tmp_path, "PROJ-1", [
            {"id": "t-2", "description": "second", "status": "todo", "position": 1, "created_at": ""},
            {"id": "t-1", "description": "first", "status": "todo", "position": 0, "created_at": ""},
        ])

        tasks = get_tasks(tmp_path, "PROJ-1")

        assert [t.id for t in tasks] == ["t-1", "t-2"]

    def test_tolerates_bare_list(self, tmp_path: Path) -> None:
        ticket_dir = _ticket_dir(tmp_path)
        path = ticket_dir / "orchestrator" / "tasks.yaml"
        path.write_text(dedent("""\
            - id: t-1
              description: bare list task
              status: todo
              created_at: ""
        """))

        tasks = get_tasks(tmp_path, "PROJ-1")

        assert len(tasks) == 1
        assert tasks[0].description == "bare list task"

    def test_tolerates_malformed_yaml(self, tmp_path: Path) -> None:
        ticket_dir = _ticket_dir(tmp_path)
        path = ticket_dir / "orchestrator" / "tasks.yaml"
        path.write_text("not: yaml: [unbalanced")

        assert get_tasks(tmp_path, "PROJ-1") == []

    def test_missing_position_uses_index(self, tmp_path: Path) -> None:
        _write_tasks(tmp_path, "PROJ-1", [
            {"id": "t-1", "description": "first", "status": "todo", "created_at": ""},
            {"id": "t-2", "description": "second", "status": "todo", "created_at": ""},
        ])

        tasks = get_tasks(tmp_path, "PROJ-1")

        assert tasks[0].position == 0
        assert tasks[1].position == 1

    def test_returns_empty_for_unknown_ticket(self, tmp_path: Path) -> None:
        assert get_tasks(tmp_path, "NOPE-999") == []


class TestAddTask:
    def test_creates_file_and_returns_task(self, tmp_path: Path) -> None:
        _ticket_dir(tmp_path)

        task = add_task(tmp_path, "PROJ-1", "implement feature")

        assert task.description == "implement feature"
        assert task.status == "todo"
        assert task.id.startswith("task-")
        assert task.source == "local"

    def test_appends_to_existing(self, tmp_path: Path) -> None:
        _write_tasks(tmp_path, "PROJ-1", [
            {"id": "t-1", "description": "existing", "status": "todo", "position": 0, "created_at": ""},
        ])

        add_task(tmp_path, "PROJ-1", "new task")

        tasks = get_tasks(tmp_path, "PROJ-1")
        assert len(tasks) == 2
        assert tasks[1].description == "new task"
        assert tasks[1].position > tasks[0].position

    def test_persists_to_yaml(self, tmp_path: Path) -> None:
        _ticket_dir(tmp_path)

        add_task(tmp_path, "PROJ-1", "check persistence")

        path = tmp_path / "PROJ-1-feature" / "orchestrator" / "tasks.yaml"
        raw = yaml.safe_load(path.read_text())
        assert len(raw["tasks"]) == 1
        assert raw["tasks"][0]["description"] == "check persistence"


class TestToggleTask:
    def test_toggles_todo_to_done(self, tmp_path: Path) -> None:
        _write_tasks(tmp_path, "PROJ-1", [
            {"id": "t-1", "description": "x", "status": "todo", "position": 0, "created_at": ""},
        ])

        toggle_task(tmp_path, "PROJ-1", "t-1")

        tasks = get_tasks(tmp_path, "PROJ-1")
        assert tasks[0].status == "done"
        assert tasks[0].completed_at is not None

    def test_toggles_done_to_todo(self, tmp_path: Path) -> None:
        _write_tasks(tmp_path, "PROJ-1", [
            {"id": "t-1", "description": "x", "status": "done", "position": 0,
             "created_at": "", "completed_at": "2026-04-17T10:00:00Z"},
        ])

        toggle_task(tmp_path, "PROJ-1", "t-1")

        tasks = get_tasks(tmp_path, "PROJ-1")
        assert tasks[0].status == "todo"
        assert tasks[0].completed_at is None


class TestDeleteTask:
    def test_removes_task(self, tmp_path: Path) -> None:
        _write_tasks(tmp_path, "PROJ-1", [
            {"id": "t-1", "description": "keep", "status": "todo", "position": 0, "created_at": ""},
            {"id": "t-2", "description": "remove", "status": "todo", "position": 1, "created_at": ""},
        ])

        delete_task(tmp_path, "PROJ-1", "t-2")

        tasks = get_tasks(tmp_path, "PROJ-1")
        assert len(tasks) == 1
        assert tasks[0].id == "t-1"


class TestReorderTask:
    def test_move_down_swaps_positions(self, tmp_path: Path) -> None:
        _write_tasks(tmp_path, "PROJ-1", [
            {"id": "t-1", "description": "first", "status": "todo", "position": 0, "created_at": ""},
            {"id": "t-2", "description": "second", "status": "todo", "position": 1, "created_at": ""},
        ])

        reorder_task(tmp_path, "PROJ-1", "t-1", direction=1)

        tasks = get_tasks(tmp_path, "PROJ-1")
        assert [t.id for t in tasks] == ["t-2", "t-1"]

    def test_move_up_swaps_positions(self, tmp_path: Path) -> None:
        _write_tasks(tmp_path, "PROJ-1", [
            {"id": "t-1", "description": "first", "status": "todo", "position": 0, "created_at": ""},
            {"id": "t-2", "description": "second", "status": "todo", "position": 1, "created_at": ""},
        ])

        reorder_task(tmp_path, "PROJ-1", "t-2", direction=-1)

        tasks = get_tasks(tmp_path, "PROJ-1")
        assert [t.id for t in tasks] == ["t-2", "t-1"]

    def test_move_past_boundary_is_noop(self, tmp_path: Path) -> None:
        _write_tasks(tmp_path, "PROJ-1", [
            {"id": "t-1", "description": "only", "status": "todo", "position": 0, "created_at": ""},
        ])

        reorder_task(tmp_path, "PROJ-1", "t-1", direction=-1)

        tasks = get_tasks(tmp_path, "PROJ-1")
        assert tasks[0].position == 0


class TestEditTask:
    def test_updates_description(self, tmp_path: Path) -> None:
        _write_tasks(tmp_path, "PROJ-1", [
            {"id": "t-1", "description": "old", "status": "todo", "position": 0, "created_at": ""},
        ])

        edit_task(tmp_path, "PROJ-1", "t-1", "updated description")

        tasks = get_tasks(tmp_path, "PROJ-1")
        assert tasks[0].description == "updated description"

    def test_preserves_status(self, tmp_path: Path) -> None:
        _write_tasks(tmp_path, "PROJ-1", [
            {"id": "t-1", "description": "done task", "status": "done", "position": 0,
             "created_at": "", "completed_at": "2026-04-17T10:00:00Z"},
        ])

        edit_task(tmp_path, "PROJ-1", "t-1", "still done")

        tasks = get_tasks(tmp_path, "PROJ-1")
        assert tasks[0].status == "done"
        assert tasks[0].completed_at == "2026-04-17T10:00:00Z"

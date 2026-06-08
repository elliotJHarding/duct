"""Task management for duct tickets.

Tasks are per-ticket checklists stored at
``{ticket}/orchestrator/tasks.yaml``. The orchestrator can also write
this file directly (same convention as ``actions.yaml``).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from duct.models import Task
from duct.workspace import resolve_ticket_dir


def _tasks_file(root: Path, key: str, ticket_dir: Path | None = None) -> Path:
    if ticket_dir is None:
        ticket_dir = resolve_ticket_dir(root, key)
    if not ticket_dir:
        raise FileNotFoundError(f"No ticket directory for {key}")
    return ticket_dir / "orchestrator" / "tasks.yaml"


def _load_tasks_file(tasks_file: Path) -> list[Task]:
    if not tasks_file.exists():
        return []
    try:
        raw = yaml.safe_load(tasks_file.read_text())
    except yaml.YAMLError:
        return []
    if isinstance(raw, list):
        items = [i for i in raw if isinstance(i, dict)]
    elif isinstance(raw, dict):
        items = raw.get("tasks", [])
        items = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    else:
        return []
    return [
        Task(
            id=item.get("id", ""),
            description=item.get("description", ""),
            status=item.get("status", "todo"),
            created_at=item.get("created_at", ""),
            completed_at=item.get("completed_at"),
            position=item.get("position", idx),
            source=item.get("source", "local"),
        )
        for idx, item in enumerate(items)
    ]


def _save_tasks_file(tasks_file: Path, tasks: list[Task]) -> None:
    data = [
        {
            "id": t.id,
            "description": t.description,
            "status": t.status,
            "created_at": t.created_at,
            "completed_at": t.completed_at,
            "position": t.position,
            "source": t.source,
        }
        for t in tasks
    ]
    tasks_file.parent.mkdir(parents=True, exist_ok=True)
    tasks_file.write_text(yaml.dump({"tasks": data}, default_flow_style=False, sort_keys=False))


def _generate_id() -> str:
    return f"task-{int(time.time())}-{uuid4().hex[:6]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_tasks(root: Path, key: str, ticket_dir: Path | None = None) -> list[Task]:
    """Load tasks for a ticket, sorted by position."""
    try:
        path = _tasks_file(root, key, ticket_dir)
    except FileNotFoundError:
        return []
    tasks = _load_tasks_file(path)
    tasks.sort(key=lambda t: t.position)
    return tasks


def add_task(root: Path, key: str, description: str) -> Task:
    """Append a new task and return it."""
    path = _tasks_file(root, key)
    tasks = _load_tasks_file(path)
    max_pos = max((t.position for t in tasks), default=-1)
    task = Task(
        id=_generate_id(),
        description=description,
        status="todo",
        created_at=_now_iso(),
        position=max_pos + 1,
    )
    tasks.append(task)
    _save_tasks_file(path, tasks)
    return task


def toggle_task(root: Path, key: str, task_id: str) -> None:
    """Flip a task between todo and done."""
    path = _tasks_file(root, key)
    tasks = _load_tasks_file(path)
    for task in tasks:
        if task.id == task_id:
            if task.status == "todo":
                task.status = "done"
                task.completed_at = _now_iso()
            else:
                task.status = "todo"
                task.completed_at = None
            break
    _save_tasks_file(path, tasks)


def delete_task(root: Path, key: str, task_id: str) -> None:
    """Remove a task."""
    path = _tasks_file(root, key)
    tasks = _load_tasks_file(path)
    tasks = [t for t in tasks if t.id != task_id]
    _save_tasks_file(path, tasks)


def reorder_task(root: Path, key: str, task_id: str, direction: int) -> None:
    """Move a task up (-1) or down (+1) by swapping position with its neighbour."""
    path = _tasks_file(root, key)
    tasks = _load_tasks_file(path)
    tasks.sort(key=lambda t: t.position)
    idx = next((i for i, t in enumerate(tasks) if t.id == task_id), None)
    if idx is None:
        return
    swap_idx = idx + direction
    if swap_idx < 0 or swap_idx >= len(tasks):
        return
    tasks[idx].position, tasks[swap_idx].position = tasks[swap_idx].position, tasks[idx].position
    _save_tasks_file(path, tasks)


def edit_task(root: Path, key: str, task_id: str, description: str) -> None:
    """Update a task's description."""
    path = _tasks_file(root, key)
    tasks = _load_tasks_file(path)
    for task in tasks:
        if task.id == task_id:
            task.description = description
            break
    _save_tasks_file(path, tasks)

"""TaskPanel -- per-ticket task checklist with inline editing."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from duct.models import Task
from duct_tui.icons import Icons, UNICODE
from duct_tui.widgets.vim_mixin import VimListMixin


class _TaskList(VimListMixin, OptionList):
    """The task option list (separated so inline input can sit alongside it)."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("space", "toggle_task", "Toggle", show=False),
        Binding("o", "add_task", "Add", show=False),
        Binding("d", "delete_task", "Delete", show=False),
        Binding("e", "edit_task", "Edit", show=False),
        Binding("K", "move_up", "Move up", show=False),
        Binding("J", "move_down", "Move down", show=False),
        *VimListMixin.VIM_BINDINGS,
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._entries: list[Task | None] = []
        self._skip_direction: int = 1
        self._sep_seq: int = 0
        self._icons: Icons = UNICODE

    def on_mount(self) -> None:
        self._icons = getattr(self.app, "icons", UNICODE)

    def update_tasks(self, tasks: list[Task]) -> None:
        previous_id = self._highlighted_option_id()
        self._entries = []
        self._sep_seq = 0
        self.clear_options()

        ic = self._icons
        todo = [t for t in tasks if t.status == "todo"]
        done = [t for t in tasks if t.status == "done"]

        if not todo and not done:
            self._add_empty_hint()
            return

        if todo:
            self._add_section_header("Todo", len(todo))
            self._add_separator()
            for i, task in enumerate(todo):
                if i > 0:
                    self._add_separator()
                text = Text()
                text.append(f"{ic.task_todo} ", style="bright_black")
                text.append(task.description)
                self.add_option(Option(text, id=task.id))
                self._entries.append(task)

        if done:
            if todo:
                self._add_separator()
            self._add_section_header("Done", len(done))
            self._add_separator()
            for i, task in enumerate(done):
                if i > 0:
                    self._add_separator()
                text = Text()
                text.append(f"{ic.task_done} ", style="green")
                text.append(task.description, style="dim strike")
                self.add_option(Option(text, id=task.id))
                self._entries.append(task)

        self._restore_highlight(previous_id)

    def _add_empty_hint(self) -> None:
        text = Text()
        text.append("No tasks yet", style="dim italic")
        text.append("\n")
        text.append("Press ", style="dim")
        text.append("o", style="bold")
        text.append(" to add one", style="dim")
        self.add_option(Option(text, id="empty-hint"))
        self._entries.append(None)

    def _add_section_header(self, label: str, count: int) -> None:
        self._sep_seq += 1
        text = Text()
        text.append(f"-- {label}  ({count})", style="bold dim")
        self.add_option(Option(text, id=f"header-{self._sep_seq}", disabled=True))
        self._entries.append(None)

    def _add_separator(self) -> None:
        self._sep_seq += 1
        self.add_option(Option(Text(""), id=f"sep-{self._sep_seq}", disabled=True))
        self._entries.append(None)

    def _restore_highlight(self, option_id: str | None) -> None:
        if option_id is None:
            return
        for idx in range(self.option_count):
            if self.get_option_at_index(idx).id == option_id:
                self.highlighted = idx
                return

    def _highlighted_option_id(self) -> str | None:
        idx = self.highlighted
        if idx is None or idx >= self.option_count:
            return None
        return self.get_option_at_index(idx).id

    def _get_selected_task(self) -> Task | None:
        idx = self.highlighted
        if idx is None or idx >= len(self._entries):
            return None
        return self._entries[idx]

    # -- Navigation (skip headers/separators) --

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted,
    ) -> None:
        if event.option is None:
            return
        option_id = event.option.id or ""
        if not (option_id.startswith("header-") or option_id.startswith("sep-")):
            return
        idx = self.highlighted
        if idx is None:
            return
        target = idx + self._skip_direction
        if target < 0 or target >= self.option_count:
            return
        self.highlighted = target

    def action_cursor_down(self) -> None:
        self._skip_direction = 1
        super().action_cursor_down()

    def action_cursor_up(self) -> None:
        self._skip_direction = -1
        super().action_cursor_up()

    def _vim_goto_first(self) -> None:
        if self.option_count:
            self._skip_direction = 1
            self.highlighted = 0

    def _vim_goto_last(self) -> None:
        if self.option_count:
            self._skip_direction = -1
            self.highlighted = self.option_count - 1

    # -- Actions (bubble up to TaskPanel) --

    def action_toggle_task(self) -> None:
        task = self._get_selected_task()
        if task:
            self.post_message(TaskPanel.TaskToggled(task.id))

    def action_add_task(self) -> None:
        self.post_message(TaskPanel._StartInput("__new__"))

    def action_delete_task(self) -> None:
        task = self._get_selected_task()
        if task:
            self.post_message(TaskPanel.TaskDeleted(task.id))

    def action_edit_task(self) -> None:
        task = self._get_selected_task()
        if task:
            self.post_message(TaskPanel._StartInput(task.id, task.description))

    def action_move_up(self) -> None:
        task = self._get_selected_task()
        if task:
            self.post_message(TaskPanel.TaskMoved(task.id, -1))

    def action_move_down(self) -> None:
        task = self._get_selected_task()
        if task:
            self.post_message(TaskPanel.TaskMoved(task.id, 1))


class TaskPanel(Widget):
    """Task checklist with inline add/edit input."""

    class TaskToggled(Message):
        def __init__(self, task_id: str) -> None:
            super().__init__()
            self.task_id = task_id

    class TaskAdded(Message):
        def __init__(self, description: str) -> None:
            super().__init__()
            self.description = description

    class TaskDeleted(Message):
        def __init__(self, task_id: str) -> None:
            super().__init__()
            self.task_id = task_id

    class TaskMoved(Message):
        def __init__(self, task_id: str, direction: int) -> None:
            super().__init__()
            self.task_id = task_id
            self.direction = direction

    class TaskEdited(Message):
        def __init__(self, task_id: str, description: str) -> None:
            super().__init__()
            self.task_id = task_id
            self.description = description

    class _StartInput(Message):
        """Internal: request to show inline input."""
        def __init__(self, task_id: str, initial: str = "") -> None:
            super().__init__()
            self.task_id = task_id
            self.initial = initial

    def __init__(self, ticket_key: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ticket_key = ticket_key
        self._editing: str | None = None
        self.border_title = "Tasks"

    def compose(self) -> ComposeResult:
        yield _TaskList(id="task-list")
        with Vertical(id="task-input-area", classes="hidden"):
            yield Input(placeholder="Task description...", id="task-input")
            yield Static("[dim]Enter[/] save  [dim]Escape[/] cancel", id="task-input-hint")

    def update_tasks(self, tasks: list[Task]) -> None:
        try:
            self.query_one("#task-list", _TaskList).update_tasks(tasks)
        except Exception:
            pass

    def on_task_panel__start_input(self, event: _StartInput) -> None:
        event.stop()
        self._editing = event.task_id
        inp = self.query_one("#task-input", Input)
        inp.value = event.initial
        self.query_one("#task-list").display = False
        self.query_one("#task-input-area").remove_class("hidden")
        inp.focus()

    def _close_input(self) -> None:
        self._editing = None
        self.query_one("#task-input-area").add_class("hidden")
        self.query_one("#task-list").display = True
        self.query_one("#task-list").focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        description = event.value.strip()
        if not description or self._editing is None:
            self._close_input()
            return
        if self._editing == "__new__":
            self.post_message(self.TaskAdded(description))
        else:
            self.post_message(self.TaskEdited(self._editing, description))
        self._close_input()

    def key_escape(self) -> None:
        if self._editing is not None:
            self._close_input()

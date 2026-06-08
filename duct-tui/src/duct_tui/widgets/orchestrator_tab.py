"""OrchestratorTab -- orchestrator log + cross-ticket actions."""

from __future__ import annotations

import datetime as _dt
import subprocess

from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, OptionList, RichLog, Static, Tab, Tabs
from textual.widgets.option_list import Option

from duct.models import Action
from duct.orchestrator import RunSummary, list_runs, read_run_body
from duct_tui.icons import Icons, UNICODE
from duct_tui.widgets.action_render import (
    _row_label,
    render_action_row,
    render_section_header,
)
from duct_tui.widgets.vim_mixin import VimListMixin


class _AllActionsList(VimListMixin, OptionList):
    """Inner cross-ticket action list. Bubbles approve/reject-start to the panel."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("y", "approve", "Approve"),
        Binding("Y", "approve_background", "Approve (bg)"),
        Binding("n", "reject", "Reject"),
        *VimListMixin.VIM_BINDINGS,
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._entries: list[tuple[str, Action] | None] = []
        self._sep_seq: int = 0
        self._skip_direction: int = 1
        self._icons: Icons = UNICODE

    def on_mount(self) -> None:
        self._icons = getattr(self.app, "icons", UNICODE)

    def update_actions(self, actions: list[tuple[str, Action]]) -> None:
        self._entries = []
        self._sep_seq = 0
        self.clear_options()

        icons = self._icons
        pending = [(k, a) for k, a in actions if a.status == "pending"]
        resolved = [(k, a) for k, a in actions if a.status != "pending"]

        if not pending and not resolved:
            self.add_option(Option(Text("No actions", style="dim"), id="empty", disabled=True))
            self._entries.append(None)
            return

        if pending:
            self._add_section_header("Pending", len(pending))
            self._add_separator()
            for i, (k, a) in enumerate(pending):
                if i > 0:
                    self._add_separator()
                text = render_action_row(a, icons, ticket_key=k)
                self.add_option(Option(text, id=a.id))
                self._entries.append((k, a))

        if resolved:
            if pending:
                self._add_separator()
            self._add_section_header("Resolved", len(resolved))
            self._add_separator()
            for i, (k, a) in enumerate(resolved):
                if i > 0:
                    self._add_separator()
                text = render_action_row(a, icons, ticket_key=k)
                self.add_option(Option(text, id=a.id, disabled=True))
                self._entries.append(None)

    def _add_section_header(self, label: str, count: int) -> None:
        self._sep_seq += 1
        self.add_option(
            Option(render_section_header(label, count),
                   id=f"header-{self._sep_seq}", disabled=True),
        )
        self._entries.append(None)

    def _add_separator(self) -> None:
        self._sep_seq += 1
        self.add_option(Option(Text(""), id=f"sep-{self._sep_seq}", disabled=True))
        self._entries.append(None)

    # ------------------------------------------------------------------ actions

    def _selected_entry(self) -> tuple[str, Action] | None:
        idx = self.highlighted
        if idx is None or idx >= len(self._entries):
            return None
        return self._entries[idx]

    def action_approve(self) -> None:
        entry = self._selected_entry()
        if entry:
            self.post_message(
                AllActionsPanel._Approve(entry[0], entry[1].id, background=False),
            )

    def action_approve_background(self) -> None:
        entry = self._selected_entry()
        if entry:
            self.post_message(
                AllActionsPanel._Approve(entry[0], entry[1].id, background=True),
            )

    def action_reject(self) -> None:
        entry = self._selected_entry()
        if entry:
            self.post_message(AllActionsPanel._StartReject(entry[0], entry[1].id))

    # ------------------------------------------------------------------ skip

    def _vim_goto_first(self) -> None:
        if self.option_count:
            self._skip_direction = 1
            self.highlighted = 0

    def _vim_goto_last(self) -> None:
        if self.option_count:
            self._skip_direction = -1
            self.highlighted = self.option_count - 1

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted,
    ) -> None:
        if event.option is None:
            return
        option_id = event.option.id or ""
        if not (option_id.startswith("header-") or option_id.startswith("sep-") or option_id == "empty"):
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


class AllActionsPanel(Widget):
    """Cross-ticket action list wrapper with inline reject-feedback composer."""

    class ActionResolved(Message):
        def __init__(
            self,
            ticket_key: str,
            action_id: str,
            approved: bool,
            feedback: str | None = None,
            background: bool = False,
        ) -> None:
            super().__init__()
            self.ticket_key = ticket_key
            self.action_id = action_id
            self.approved = approved
            self.feedback = feedback
            self.background = background

    class _Approve(Message):
        def __init__(
            self, ticket_key: str, action_id: str, background: bool = False,
        ) -> None:
            super().__init__()
            self.ticket_key = ticket_key
            self.action_id = action_id
            self.background = background

    class _StartReject(Message):
        def __init__(self, ticket_key: str, action_id: str) -> None:
            super().__init__()
            self.ticket_key = ticket_key
            self.action_id = action_id

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rejecting: tuple[str, str] | None = None
        self.border_title = "Actions (all tickets)"

    @property
    def is_composing(self) -> bool:
        """True while the inline reject-feedback composer is open.

        A periodic refresh must not rebuild the list underneath an
        in-progress rejection.
        """
        return self._rejecting is not None

    def compose(self) -> ComposeResult:
        yield _AllActionsList(id="all-actions-list")
        with Vertical(id="all-actions-feedback-area", classes="hidden"):
            yield Input(placeholder="Reason for rejecting (Enter to skip)...",
                        id="all-actions-feedback-input")
            yield Static("[dim]Enter[/] reject  [dim]Escape[/] cancel",
                         id="all-actions-feedback-hint")
        yield Static(
            r"[dim]\[y] approve  \[Y] approve bg  \[n] reject  \[j/k] move[/dim]",
            id="all-actions-footer-hint",
        )

    def update_actions(self, actions: list[tuple[str, Action]]) -> None:
        try:
            self.query_one("#all-actions-list", _AllActionsList).update_actions(actions)
        except Exception:
            pass

    def on_all_actions_panel__approve(self, event: _Approve) -> None:
        event.stop()
        self.post_message(
            self.ActionResolved(
                event.ticket_key, event.action_id,
                approved=True, background=event.background,
            ),
        )

    def on_all_actions_panel__start_reject(self, event: _StartReject) -> None:
        event.stop()
        self._rejecting = (event.ticket_key, event.action_id)
        inp = self.query_one("#all-actions-feedback-input", Input)
        inp.value = ""
        self.query_one("#all-actions-list").display = False
        self.query_one("#all-actions-feedback-area").remove_class("hidden")
        inp.focus()

    def _close_input(self) -> None:
        self._rejecting = None
        self.query_one("#all-actions-feedback-area").add_class("hidden")
        self.query_one("#all-actions-list").display = True
        self.query_one("#all-actions-list").focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "all-actions-feedback-input" or self._rejecting is None:
            return
        event.stop()
        ticket_key, action_id = self._rejecting
        feedback = event.value.strip() or None
        self._close_input()
        self.post_message(
            self.ActionResolved(ticket_key, action_id, approved=False, feedback=feedback),
        )

    def key_escape(self) -> None:
        if self._rejecting is not None:
            self._close_input()


class OrchestratorTab(Widget):
    """Orchestrator tab with log output and cross-ticket actions."""

    DEFAULT_CSS = """
    OrchestratorTab {
        height: 1fr;
    }
    #orchestrator-panels {
        height: 1fr;
    }
    #conductor-log {
        width: 1fr;
        height: 1fr;
    }
    /* Slim header: mascot on the left, "Next auto-run" badge on the right. */
    #conductor {
        height: auto;
        padding: 0 2;
    }
    #conductor-mascot {
        width: auto;
        padding-right: 2;
    }
    #conductor-schedule {
        width: 1fr;
        color: $text-muted;
        text-align: right;
    }
    #conductor-schedule.hidden {
        display: none;
    }
    /* Run output fills the rest of the right side. Shows the selected run's
       rendered conclusion; swapped for the live RichLog while a run streams. */
    #conductor-message-container {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
        border: round $panel;
        overflow-y: auto;
    }
    #conductor-message-container:focus {
        border: round $accent;
    }
    #conductor-message-container.hidden {
        display: none;
    }
    #conductor-message {
        width: 1fr;
        height: auto;
    }
    OrchestratorTab RichLog {
        width: 1fr;
        height: 1fr;
        min-height: 8;
        border: round $panel;
    }
    OrchestratorTab RichLog:focus {
        border: round $accent;
    }
    OrchestratorTab RichLog.hidden {
        display: none;
    }
    /* Run selector pinned to the bottom of the right side. Tabs is the last
       flow sibling, and the 1fr run-output above fills the remaining height.
       An explicit height is required: Tabs' own `height: auto` expands to fill
       the column rather than sizing to the strip. */
    #run-tabs {
        height: 3;
    }
    #run-tabs.hidden {
        display: none;
    }
    OrchestratorTab AllActionsPanel {
        width: 30%;
        min-width: 28;
        height: 1fr;
        border: round $panel;
    }
    OrchestratorTab AllActionsPanel:focus-within {
        border: round $accent;
    }
    """

    BINDINGS = [
        Binding("h", "focus_left", "Left pane", show=False),
        Binding("l", "focus_right", "Right pane", show=False),
        Binding("r", "run_orchestrator", "Run"),
        Binding("c", "launch_root_session", "Root session"),
    ]

    class RootSessionLaunch(Message):
        """Request to launch a Claude Code session at workspace root.

        Optional ``prompt`` is fed to the spawned session as its initial
        input — used when an approved ``type: prompt`` workspace-scoped
        action carries an agent body to run. ``background=True`` spawns
        the session without docking it or switching the active TUI tab.
        """

        def __init__(
            self, prompt: str | None = None, background: bool = False,
        ) -> None:
            super().__init__()
            self.prompt = prompt
            self.background = background

    class TicketSessionLaunch(Message):
        """Request to launch a Claude Code session for a ticket.

        Used by approved ``prompt`` and ``concrete: launch_session`` actions
        whose ``detail.ticket`` points at a specific ticket. The handler in
        DuctApp spawns a fresh WezTerm pane scoped to the ticket directory
        and either docks it + switches to the Sessions tab (foreground) or
        leaves it as a free pane (``background=True``).
        """

        def __init__(
            self,
            ticket_key: str,
            repo: str | None = None,
            prompt: str | None = None,
            background: bool = False,
        ) -> None:
            super().__init__()
            self.ticket_key = ticket_key
            self.repo = repo
            self.prompt = prompt
            self.background = background

    _LIVE_TAB_ID = "run-live"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._proc: subprocess.Popen | None = None
        self._last_text: str = ""
        self._runs_by_tab_id: dict[str, RunSummary] = {}
        self._has_live_tab: bool = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="orchestrator-panels"):
            yield AllActionsPanel(id="all-actions")
            with Vertical(id="conductor-log"):
                with Horizontal(id="conductor"):
                    yield Static(
                        "        [bold]♪[/bold]\n"
                        " ▐▛███▜▌╱\n"
                        "▝▜█████▛▘\n"
                        "  ▘▘ ▝▝",
                        id="conductor-mascot",
                    )
                    yield Static("", id="conductor-schedule", classes="hidden")
                # Run output fills the right side: the selected run's rendered
                # conclusion by default, swapped for the live RichLog (below,
                # initially hidden) only while a manual run is streaming.
                yield VerticalScroll(
                    Static(id="conductor-message"),
                    id="conductor-message-container",
                )
                yield RichLog(
                    id="orchestrator-log", wrap=True, markup=True, classes="hidden",
                )
                yield Tabs(id="run-tabs")

    def on_mount(self) -> None:
        self.query_one("#orchestrator-log", RichLog).border_title = "Log"
        self._show_live_log(False)
        self._set_conductor("idle")
        self._refresh_run_tabs()
        self._refresh_actions()
        self.refresh_schedule_indicator()
        # The daemon writes runs/actions to disk out-of-process; poll so they
        # appear without a restart (mirrors TicketTab's self-owned refresh).
        self.set_interval(10.0, self._poll_runs)

    # ------------------------------------------------------------------ conductor

    def _show_live_log(self, show: bool) -> None:
        """Swap the right side between the live raw stream and the run output.

        While a manual run streams, the RichLog is shown so the user can watch
        the raw tool-by-tool timeline; otherwise — idle, finished, or any
        daemon auto-run selected from the tabs — the rendered run conclusion
        fills the pane.
        """
        try:
            self.query_one("#orchestrator-log", RichLog).set_class(not show, "hidden")
            self.query_one("#conductor-message-container").set_class(show, "hidden")
        except Exception:
            pass

    def _set_conductor(self, state: str, summary: str = "") -> None:
        """Update the conductor message alongside the clawd mascot.

        Idle/running render as plain Static markup. The "done" state with a
        summary delegates to ``_set_conductor_markdown`` so it follows the
        same Static+RichMarkdown rendering used for past-run summaries —
        Textual's Markdown widget has ``height: auto; overflow-y: hidden``
        which fights every sizing strategy in this column.
        """
        if state == "done" and summary:
            self._set_conductor_markdown(summary)
            return

        container = self.query_one("#conductor-message-container", VerticalScroll)
        existing = container.query(Static).first() if container.children else None

        if state == "idle":
            content = "[dim]Idle — press [bold]r[/bold] to start a run[/dim]"
        elif state == "running":
            content = "[bold]Conducting...[/bold]"
        elif state == "done":
            content = "[dim]Run complete[/dim]"
        else:
            content = ""

        # _set_conductor_markdown leaves a Static behind, so a plain Static
        # is always the existing widget when we reach this path.
        if isinstance(existing, Static):
            existing.update(content)
        else:
            self._replace_conductor(container, Static(content))

    @staticmethod
    def _replace_conductor(container: VerticalScroll, widget: Widget) -> None:
        for child in list(container.children):
            child.remove()
        container.mount(widget)

    def _set_conductor_markdown(self, body: str) -> None:
        """Render a markdown body into the conductor area via Static+RichMarkdown.

        Avoids Textual's ``Markdown`` widget — its ``height: auto;
        overflow-y: hidden`` defaults push surrounding fr-sized siblings
        around and clip content on its own. A Static wrapping a
        ``rich.markdown.Markdown`` renderable behaves like any other
        block of text: it sizes to content, scrolls inside an auto-
        overflow container, and leaves the RichLog alone.
        """
        container = self.query_one("#conductor-message-container", VerticalScroll)
        existing = container.query(Static).first() if container.children else None
        rendered = RichMarkdown(body)
        if isinstance(existing, Static):
            existing.update(rendered)
            return
        self._replace_conductor(container, Static(rendered))

    # ------------------------------------------------------------------ run tabs

    @staticmethod
    def _format_run_tab_label(ts: _dt.datetime) -> str:
        """Minimal tab label — HH:MM if today, else 'Mon HH:MM'.

        Matches the formatting precedent used by
        ``refresh_schedule_indicator`` so dates render consistently across
        the conductor page.
        """
        if ts.date() == _dt.date.today():
            return ts.strftime("%H:%M")
        return ts.strftime("%a %H:%M")

    def _poll_runs(self) -> None:
        """Pick up runs and actions the daemon wrote out-of-process.

        Fires on a timer. The run-tab refresh preserves the active tab, so it
        never yanks the user off the run they're viewing. The actions refresh
        is skipped while the reject composer is open so a rebuild can't clobber
        an in-progress rejection. While a manual run streams, the run lifecycle
        owns refreshes — and `run_lock` prevents a daemon run landing then — so
        skip entirely.
        """
        if self._orchestrator_running:
            return
        self._refresh_run_tabs()
        try:
            panel = self.query_one("#all-actions", AllActionsPanel)
        except Exception:
            return
        if not panel.is_composing:
            self._refresh_actions()

    def _refresh_run_tabs(self) -> None:
        """Sync the run tab strip with the .runs/ directory.

        Computes the desired tabs (live tab if running + 5 most recent
        workspace runs), then diffs against existing tabs — removing
        unwanted ones and adding only what isn't already there. We don't
        call ``Tabs.clear()`` because its removal is async; a follow-up
        ``add_tab`` runs before the old tabs leave the DOM, raising
        ``DuplicateIds``.
        """
        tabs = self.query_one("#run-tabs", Tabs)
        try:
            runs = list_runs(self.app.data.root, limit=5)
        except Exception:
            runs = []

        desired: list[tuple[str, str]] = []  # (tab_id, label) in display order
        if self._has_live_tab:
            desired.append(
                (self._LIVE_TAB_ID, self._format_run_tab_label(_dt.datetime.now())),
            )
        new_runs_by_tab_id: dict[str, RunSummary] = {}
        for run in runs:
            tab_id = f"run-{run.timestamp.strftime('%Y%m%dT%H%M%S')}"
            new_runs_by_tab_id[tab_id] = run
            desired.append((tab_id, self._format_run_tab_label(run.timestamp)))

        self._runs_by_tab_id = new_runs_by_tab_id
        desired_ids = {tid for tid, _ in desired}

        existing_tabs = list(tabs.query(Tab))
        existing_ids = {t.id for t in existing_tabs if t.id}

        for tab in existing_tabs:
            if tab.id and tab.id not in desired_ids:
                try:
                    tabs.remove_tab(tab.id)
                except Exception:
                    pass

        for tab_id, label in desired:
            if tab_id in existing_ids:
                continue
            try:
                tabs.add_tab(Tab(label, id=tab_id))
            except Exception:
                pass

        empty = not self._has_live_tab and not runs
        tabs.set_class(empty, "hidden")

        if empty:
            return

        active = tabs.active
        if active and active in desired_ids:
            return
        target = self._LIVE_TAB_ID if self._has_live_tab else next(iter(new_runs_by_tab_id))
        try:
            tabs.active = target
        except Exception:
            pass

    @staticmethod
    def _extract_conclusion(body: str) -> str:
        """Pull the rendered Conclusion section out of a run markdown body.

        The persisted file is structured as ``# Orchestrator run …`` /
        ``## Conclusion`` / ``## Timeline``; the conductor area only
        wants the Conclusion prose. Blockquote ``>`` prefixes are
        stripped so the text reads as the orchestrator's final note
        rather than a quoted block.
        """
        lines = body.splitlines()
        in_section = False
        out: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not in_section:
                if stripped == "## Conclusion":
                    in_section = True
                continue
            if stripped.startswith("## "):
                break
            if line.startswith(">"):
                line = line[1:].lstrip(" ")
            out.append(line)
        return "\n".join(out).strip()

    def _render_run_in_conductor(self, run: RunSummary) -> None:
        body = read_run_body(run.path)
        conclusion = self._extract_conclusion(body) if body else ""
        self._set_conductor_markdown(conclusion or "_(no final output)_")

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        tab_id = event.tab.id if event.tab else None
        if not tab_id:
            return
        if tab_id == self._LIVE_TAB_ID:
            # Live run still streaming — show the raw log and leave whatever the
            # run lifecycle put there untouched.
            self._show_live_log(True)
            return
        run = self._runs_by_tab_id.get(tab_id)
        if run is None:
            return
        self._show_live_log(False)
        self._render_run_in_conductor(run)

    # ------------------------------------------------------------------ run

    _RUN_WORKER_NAMES = ("_start_orchestrator",)

    @property
    def _orchestrator_running(self) -> bool:
        return any(
            w.is_running
            for w in self.workers
            if w.name in self._RUN_WORKER_NAMES
        )

    def action_run_orchestrator(self) -> None:
        if self._orchestrator_running:
            self.app.notify("Orchestrator already running", severity="warning")
            return
        from duct import run_lock
        if run_lock.is_locked(self.app.data.root):
            self.app.notify(
                "The daemon is running an orchestrator pass — try again shortly.",
                severity="warning",
            )
            return
        log = self.query_one("#orchestrator-log", RichLog)
        log.clear()
        self._show_live_log(True)
        log.write("[dim]Starting orchestrator...[/dim]")
        self._last_text = ""
        self._set_conductor("running")
        self._add_live_tab()
        self._start_orchestrator()

    def refresh_schedule_indicator(self) -> None:
        """Update the "Next auto: HH:MM" badge based on the current config + clock."""
        try:
            badge = self.query_one("#conductor-schedule", Static)
        except Exception:
            return
        cfg = getattr(self.app, "_auto_orchestrate_cfg", None)
        if cfg is None or not cfg.enabled:
            badge.add_class("hidden")
            badge.update("")
            return
        nxt = cfg.next_fire_time(_dt.datetime.now())
        if nxt is None:
            badge.add_class("hidden")
            badge.update("")
            return
        badge.remove_class("hidden")
        same_day = nxt.date() == _dt.date.today()
        when = nxt.strftime("%H:%M") if same_day else nxt.strftime("%a %H:%M")
        badge.update(f"Next auto-run\n[bold]{when}[/bold]")

    @work(thread=True)
    def _start_orchestrator(self) -> None:
        self._run_orchestrator_blocking()

    def _run_orchestrator_blocking(self) -> None:
        """Body of the orchestrator launch. Blocking — call from a worker thread.

        Takes the cross-process run lock so a manual run and the daemon's
        scheduled run never overlap.
        """
        from duct import run_lock

        root = self.app.data.root
        if not run_lock.acquire(root):
            self.app.call_from_thread(
                self._append_log,
                "[yellow]The daemon is already running an orchestrator pass.[/yellow]",
            )
            self.app.call_from_thread(self._on_run_complete)
            return
        try:
            from duct.orchestrator import RunRecorder, format_stream_event

            recorder = RunRecorder(root)
            proc = self.app.data.launch_orchestrator()
            self._proc = proc
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                recorder.record(raw_line)
                formatted = format_stream_event(raw_line, root=root)
                if formatted:
                    self.app.call_from_thread(self._append_log, formatted)
            proc.wait()
            rc = proc.returncode
            path = recorder.finalize(rc)
            self._last_text = recorder.last_assistant_text()
            self.app.call_from_thread(
                self._append_log,
                f"\n[bold]Exited (code {rc})[/bold]  [dim]log: {path.name}[/dim]",
            )
        except Exception as exc:
            self.app.call_from_thread(
                self._append_log,
                f"[bold red]Error: {exc}[/bold red]",
            )
        finally:
            run_lock.release(root)
            self._proc = None
            self.app.call_from_thread(self._on_run_complete)

    def _append_log(self, text: str) -> None:
        try:
            self.query_one("#orchestrator-log", RichLog).write(text)
        except Exception:
            pass

    def _on_run_complete(self) -> None:
        summary = self._last_text.strip() if self._last_text else ""
        self._show_live_log(False)
        self._set_conductor("done", summary)
        # The live tab's underlying .md is now on disk — drop the synthetic
        # entry and rebuild so the freshly persisted run shows up properly.
        self._has_live_tab = False
        self._refresh_run_tabs()
        self._refresh_actions()
        # Trigger a data refresh so other tabs pick up orchestrator changes
        if hasattr(self.app, "_load_initial_data"):
            self.app._load_initial_data()

    def _add_live_tab(self) -> None:
        """Insert a synthetic 'live run' tab and activate it.

        The run file is only written when ``RunRecorder.finalize()`` runs,
        so until then we represent the active run with a transient tab.
        ``_on_run_complete`` swaps it for the persisted entry.
        """
        self._has_live_tab = True
        self._refresh_run_tabs()
        try:
            tabs = self.query_one("#run-tabs", Tabs)
            tabs.active = self._LIVE_TAB_ID
        except Exception:
            pass

    # ------------------------------------------------------------------ actions

    @work(thread=True)
    def _refresh_actions(self) -> None:
        actions = self.app.data.load_all_actions()
        self.app.call_from_thread(self._apply_actions, actions)

    def _apply_actions(self, actions: list[tuple[str, Action]]) -> None:
        try:
            self.query_one("#all-actions", AllActionsPanel).update_actions(actions)
        except Exception:
            pass

    def on_all_actions_panel_action_resolved(
        self, event: AllActionsPanel.ActionResolved,
    ) -> None:
        event.stop()
        self._resolve_action(
            event.ticket_key, event.action_id,
            event.approved, event.feedback, event.background,
        )

    @work(thread=True)
    def _resolve_action(
        self,
        ticket_key: str,
        action_id: str,
        approved: bool,
        feedback: str | None = None,
        background: bool = False,
    ) -> None:
        from duct.actions import get_actions, get_workspace_actions
        from duct_tui.widgets.action_execute import execute_approved_action

        # Read the action before mutating status so we still have its detail.
        actions_pre = (
            get_workspace_actions(self.app.data.root)
            if not ticket_key
            else get_actions(self.app.data.root, ticket_key)
        )
        action = next((a for a in actions_pre if a.id == action_id), None)

        self.app.data.resolve_action(ticket_key, action_id, approved, feedback)
        status = "approved" if approved else "rejected"
        self.app.call_from_thread(self.app.notify, f"Action {status}")

        if approved and action:
            execute_approved_action(
                self.app, self, action, ticket_key or None, background=background,
            )

        self._refresh_actions()

    # ------------------------------------------------------------------ session

    def action_launch_root_session(self) -> None:
        self.post_message(self.RootSessionLaunch())

    # ------------------------------------------------------------------ focus

    def action_focus_left(self) -> None:
        self.query_one("#all-actions").focus()

    def action_focus_right(self) -> None:
        # Focus whichever right-side surface is currently visible — the live
        # log while a run streams, otherwise the rendered run output.
        try:
            log = self.query_one("#orchestrator-log", RichLog)
            target = log if not log.has_class("hidden") else self.query_one(
                "#conductor-message-container",
            )
            target.focus()
        except Exception:
            pass

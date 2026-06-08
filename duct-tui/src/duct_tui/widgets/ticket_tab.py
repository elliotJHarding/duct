"""TicketTab -- summary + detail pane ticket view."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import ContentSwitcher

from duct_tui.widgets.action_panel import ActionPanel
from duct_tui.widgets.artifact_view import ArtifactView
from duct_tui.widgets.pr_panel import PRPanel
from duct_tui.widgets.session_panel import SessionPanel
from duct_tui.widgets.task_panel import TaskPanel
from duct_tui.widgets.ticket_summary_pane import TicketSummaryPane
from duct_tui.widgets.workspace_panel import WorkspacePanel

_SECTION_TO_PANEL = {
    "ticket": "detail-ticket",
    "artifact": "detail-artifact",
    "workspace": "detail-workspace",
    "pr": "detail-prs",
    "session": "detail-sessions",
    "task": "detail-tasks",
    "action": "detail-actions",
}


class TicketTab(Widget):
    BINDINGS = [
        Binding("h", "focus_left", "Left pane", show=False),
        Binding("l", "focus_right", "Right pane", show=False),
        Binding("a", "launch_agent", "Launch agent"),
        Binding("r", "focus_add_repo", "Add repo"),
    ]

    def __init__(self, ticket_key: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ticket_key = ticket_key
        self._detail = None
        self._current_section: str | None = None
        self._current_artifact: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="ticket-panels"):
            yield TicketSummaryPane(id="summary-pane")
            with ContentSwitcher(id="detail-switcher", initial="detail-ticket"):
                # Highlighting the "ticket" summary row shows TICKET.md via
                # ArtifactView (reuses the fast_markdown / mermaid path).
                yield ArtifactView(id="detail-ticket")
                yield ArtifactView(id="detail-artifact")
                yield WorkspacePanel(ticket_key=self._ticket_key, id="detail-workspace")
                yield PRPanel(id="detail-prs")
                yield SessionPanel(ticket_key=self._ticket_key, id="detail-sessions")
                yield TaskPanel(self._ticket_key, id="detail-tasks")
                yield ActionPanel(self._ticket_key, id="detail-actions")

    def on_mount(self) -> None:
        self._load_data()
        self.set_interval(10.0, self._load_data)

    @work(thread=True)
    def _load_data(self) -> None:
        detail = self.app.data.load_ticket_detail(self._ticket_key)
        if detail:
            # TICKET.md lives at {ticket-dir}/orchestrator/TICKET.md. The
            # artifact list excludes it, so load it explicitly here (off the
            # UI thread) and hand the content to _apply_data.
            ticket_md = self.app.data.load_artifact_content(
                self._ticket_key, "TICKET",
            )
            artifact_content: str | None = None
            if self._current_artifact:
                artifact_content = self.app.data.load_artifact_content(
                    self._ticket_key, self._current_artifact,
                )
            self.app.call_from_thread(
                self._apply_data, detail, ticket_md, artifact_content,
            )

    def _apply_data(
        self,
        detail,
        ticket_md: str | None = None,
        artifact_content: str | None = None,
    ) -> None:
        self._detail = detail

        # Update left summary pane
        try:
            self.query_one("#summary-pane", TicketSummaryPane).update_data(
                detail, detail.artifacts,
            )
        except Exception:
            pass

        # Update right pane: TICKET.md (reuses ArtifactView's markdown path).
        try:
            body = ticket_md if ticket_md is not None else "*No TICKET.md found.*"
            self.query_one("#detail-ticket", ArtifactView).update_artifact(
                "TICKET.md", body,
            )
        except Exception:
            pass
        # Refresh #detail-artifact if the user is currently viewing one — without
        # this, sync writes new artifact content to disk but the pane stays stale
        # until the user re-selects the artifact.
        if self._current_artifact and artifact_content is not None:
            try:
                self.query_one("#detail-artifact", ArtifactView).update_artifact(
                    self._current_artifact, artifact_content,
                )
            except Exception:
                pass
        try:
            self.query_one("#detail-prs", PRPanel).update_prs(detail.prs)
        except Exception:
            pass
        try:
            sessions = [s for s in self.app.sessions if s.ticket_key == self._ticket_key]
            self.query_one("#detail-sessions", SessionPanel).update_sessions(sessions)
        except Exception:
            pass
        try:
            self.query_one("#detail-workspace", WorkspacePanel).update_repos(detail.repos)
        except Exception:
            pass
        try:
            self.query_one("#detail-tasks", TaskPanel).update_tasks(detail.tasks)
        except Exception:
            pass
        try:
            self.query_one("#detail-actions", ActionPanel).update_actions(detail.actions)
        except Exception:
            pass

    # -- Section switching (driven by left pane highlight) --

    def on_ticket_summary_pane_section_changed(
        self, event: TicketSummaryPane.SectionChanged,
    ) -> None:
        if event.section == "session":
            self._select_session(event.item_id)
            self._current_section = "session"
            self._current_artifact = None
            return

        # Only tear down session preview when transitioning *from* a session row.
        # Non-session -> non-session navigation skips this (layout churn + state reset).
        if self._current_section == "session":
            self._show_detail_switcher()
            if hasattr(self.app, "_reset_session_state"):
                self.app._reset_session_state()

        panel_id = _SECTION_TO_PANEL.get(event.section)
        if not panel_id:
            return

        switcher = self.query_one("#detail-switcher", ContentSwitcher)
        if switcher.current != panel_id:
            switcher.current = panel_id

        if event.section == "artifact" and event.item_id != self._current_artifact:
            self._load_artifact(event.item_id)
            self._current_artifact = event.item_id
        elif event.section != "artifact":
            self._current_artifact = None

        self._current_section = event.section

    def on_ticket_summary_pane_focus_right_requested(
        self, event: TicketSummaryPane.FocusRightRequested,
    ) -> None:
        self.action_focus_right()

    def _select_session(self, session_id: str) -> None:
        """Hide the right panel and trigger session preview/dock."""
        self._hide_detail_switcher()
        if not self._detail:
            return
        session = next(
            (s for s in self._detail.sessions if s.session_id == session_id),
            None,
        )
        if not (session and session.pid and session.status != "terminated"):
            return
        # Call the app's preview fetch directly — bubbling a SessionSelect message
        # from TicketTab doesn't always reach the handler reliably, so we take the
        # same code path that the handler would.
        app = self.app
        if hasattr(app, "_fetch_preview"):
            from duct_tui.app import _SessionState
            if getattr(app, "_session_state", None) != _SessionState.DOCKED:
                app._fetch_preview(session.pid)

    def _hide_detail_switcher(self) -> None:
        try:
            self.query_one("#detail-switcher", ContentSwitcher).display = False
        except Exception:
            pass

    def _show_detail_switcher(self) -> None:
        try:
            self.query_one("#detail-switcher", ContentSwitcher).display = True
        except Exception:
            pass

    @work(thread=True, exclusive=True)
    def _load_artifact(self, name: str) -> None:
        content = self.app.data.load_artifact_content(self._ticket_key, name)
        if content is not None:
            self.app.call_from_thread(
                self.query_one("#detail-artifact", ArtifactView).update_artifact,
                name, content,
            )

    # -- Pane focus switching --

    def action_focus_left(self) -> None:
        self.query_one("#summary-pane").focus()

    def action_focus_right(self) -> None:
        switcher = self.query_one("#detail-switcher", ContentSwitcher)
        if switcher.current:
            target = self.query_one(f"#{switcher.current}")
            if target.can_focus:
                target.focus()
            else:
                focusable = target.query("*:can-focus")
                if focusable:
                    focusable.first().focus()

    # -- Action resolution --

    def on_action_panel_action_resolved(self, event: ActionPanel.ActionResolved) -> None:
        self._resolve_action(event.action_id, event.approved, event.feedback)

    @work(thread=True)
    def _resolve_action(
        self, action_id: str, approved: bool, feedback: str | None = None,
    ) -> None:
        from duct_tui.widgets.action_execute import execute_approved_action

        self.app.data.resolve_action(self._ticket_key, action_id, approved, feedback)
        status = "approved" if approved else "rejected"
        self.app.call_from_thread(self.app.notify, f"Action {status}")
        if approved:
            from duct.actions import get_actions
            actions = get_actions(self.app.data.root, self._ticket_key)
            action = next((a for a in actions if a.id == action_id), None)
            if action:
                execute_approved_action(self.app, self, action, self._ticket_key)
        self._load_data()

    # -- Task handlers --

    def on_task_panel_task_toggled(self, event: TaskPanel.TaskToggled) -> None:
        self._toggle_task(event.task_id)

    @work(thread=True)
    def _toggle_task(self, task_id: str) -> None:
        self.app.data.toggle_task(self._ticket_key, task_id)
        self._load_data()

    def on_task_panel_task_added(self, event: TaskPanel.TaskAdded) -> None:
        self._add_task(event.description)

    @work(thread=True)
    def _add_task(self, description: str) -> None:
        self.app.data.add_task(self._ticket_key, description)
        self._load_data()

    def on_task_panel_task_deleted(self, event: TaskPanel.TaskDeleted) -> None:
        self._delete_task(event.task_id)

    @work(thread=True)
    def _delete_task(self, task_id: str) -> None:
        self.app.data.delete_task(self._ticket_key, task_id)
        self._load_data()

    def on_task_panel_task_moved(self, event: TaskPanel.TaskMoved) -> None:
        self._move_task(event.task_id, event.direction)

    @work(thread=True)
    def _move_task(self, task_id: str, direction: int) -> None:
        self.app.data.reorder_task(self._ticket_key, task_id, direction)
        self._load_data()

    def on_task_panel_task_edited(self, event: TaskPanel.TaskEdited) -> None:
        self._edit_task(event.task_id, event.description)

    @work(thread=True)
    def _edit_task(self, task_id: str, description: str) -> None:
        self.app.data.edit_task(self._ticket_key, task_id, description)
        self._load_data()

    # -- Session handlers --
    # SessionSelect and SessionDeselect bubble up to DuctApp for pane management.

    def on_session_panel_session_launch(self, event: SessionPanel.SessionLaunch) -> None:
        # Stop bubbling — FullScreen has its own handler for un-scoped launches
        # from the main Sessions tab.
        event.stop()
        from duct_tui.modals.launch_session import LaunchConfig, LaunchSessionModal

        def on_result(cfg: LaunchConfig | None) -> None:
            if cfg is None:
                return
            self._do_launch(cfg.repo, cfg.prompt)

        self.app.push_screen(LaunchSessionModal(self._ticket_key), on_result)

    @work(thread=True)
    def _do_launch(self, repo: str | None = None, prompt: str | None = None) -> None:
        self._launch_with(prompt=prompt, repo=repo, notify_label="Session")

    # -- Add repo --

    def action_focus_add_repo(self) -> None:
        """Switch the right pane to Workspace and focus the add-repo form."""
        switcher = self.query_one("#detail-switcher", ContentSwitcher)
        if switcher.current != "detail-workspace":
            switcher.current = "detail-workspace"
        self._current_section = "workspace"
        self.query_one("#detail-workspace", WorkspacePanel).action_focus_form()

    def on_workspace_panel_add_repo_requested(
        self, event: WorkspacePanel.AddRepoRequested,
    ) -> None:
        self._do_add_repo(
            event.repo_name, event.base_branch, event.feature_branch,
            event.clone_from,
        )

    @work(thread=True)
    def _do_add_repo(
        self,
        repo_name: str,
        base_branch: str,
        feature_branch: str,
        clone_from: str | None = None,
    ) -> None:
        panel = self.query_one("#detail-workspace", WorkspacePanel)
        try:
            path = self.app.data.do_add_repo(
                self._ticket_key, repo_name, base_branch, feature_branch,
                clone_from=clone_from,
            )
        except Exception as exc:
            self.app.call_from_thread(
                panel.notify_add_result, f"Failed: {exc}", "error",
            )
            self.app.call_from_thread(
                self.app.notify, f"Add repo failed: {exc}", severity="error",
            )
            return
        self.app.call_from_thread(
            panel.notify_add_result, f"Added {repo_name} at {path.name}", "info",
        )
        self.app.call_from_thread(
            self.app.notify,
            f"Worktree created for {repo_name} (branch: {feature_branch})",
        )
        self._load_data()

    # -- Agent launch --

    def action_launch_agent(self) -> None:
        from duct_tui.modals.launch_agent import LaunchAgentModal, LaunchAgentConfig

        def on_result(cfg: LaunchAgentConfig | None) -> None:
            if cfg is None:
                return
            self._do_launch_agent(cfg.agent_name, cfg.repo)

        self.app.push_screen(LaunchAgentModal(self._ticket_key), on_result)

    @work(thread=True)
    def _do_launch_agent(self, agent_name: str, repo: str | None) -> None:
        body = self.app.data.load_agent_body(agent_name)
        if body is None:
            self.app.call_from_thread(
                self.app.notify,
                f"Agent '{agent_name}' not found",
                severity="error",
            )
            return
        self._launch_with(prompt=body, repo=repo, notify_label=f"Agent {agent_name}")

    def _launch_with(
        self, prompt: str | None, repo: str | None, notify_label: str,
    ) -> None:
        """Spawn (or Popen) a session with the given prompt. Shared by session and agent launches."""
        from duct_tui.app import _SessionState

        app = self.app
        adapter = getattr(app, "_terminal_adapter", None)
        tui_pane = getattr(app, "_tui_pane_id", None)

        try:
            if adapter and tui_pane is not None:
                pane_id = app.data.do_spawn_session(
                    adapter, self._ticket_key, repo=repo, prompt=prompt,
                )
                if pane_id is None:
                    self.app.call_from_thread(
                        self.app.notify, "Failed to spawn session pane", severity="error",
                    )
                    return

                self.app.call_from_thread(
                    self.app.notify, f"{notify_label} launched for {self._ticket_key}",
                )

                if getattr(app, "_docked_pane_id", None) is not None:
                    adapter.undock_pane(app._docked_pane_id)
                    app._docked_pane_id = None
                    app._docked_session_pid = None

                if adapter.dock_pane(tui_pane, pane_id):
                    app._docked_pane_id = pane_id
                    app._docked_session_pid = None
                    app._session_state = _SessionState.DOCKED
                    self.app.call_from_thread(app._sync_split_class)
                    adapter.activate_pane(pane_id)
            else:
                app.data.do_launch_session(self._ticket_key, repo, prompt)
                self.app.call_from_thread(
                    self.app.notify, f"{notify_label} launched for {self._ticket_key}",
                )

            self.app.call_from_thread(self.app.request_session_refresh)
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify, f"Launch failed: {exc}", severity="error",
            )

    # SessionStop bubbles up to FullScreen which handles it for all panels.

"""DuctApp -- main application entry point."""

from __future__ import annotations

import atexit
import enum
import os
import sys
import time
from pathlib import Path

from textual import work
from textual.app import App
from textual.binding import Binding
from textual.reactive import reactive

from duct_tui.theme import create_duct_theme
from duct_tui.data import DataManager
from duct_tui.icons import Icons, get_icons
from duct_tui.screens.full import FullScreen
from duct import perf
from duct.config import AutoOrchestrateConfig, load_config
from duct.exceptions import ConfigError
from duct.models import SessionInfo, TicketOverview
from duct.terminal import PaneContext, get_terminal_adapter


class _SessionState(enum.Enum):
    BROWSING = "browsing"      # Session list only, no preview, no dock
    PREVIEWING = "previewing"  # Preview panel visible with terminal snapshot
    DOCKED = "docked"          # Real WezTerm pane docked, focus on session


def _resolve_workspace_root(cli_root: str | None = None) -> Path:
    """Find the workspace root from CLI arg, env var, or toolkit/config.yaml walk."""
    if cli_root:
        return Path(cli_root).resolve()
    env_root = os.environ.get("DUCT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    from duct.api import find_workspace_root
    return find_workspace_root()


class DuctApp(App):
    TITLE = "duct"
    CSS_PATH = ["styles/app.tcss", "styles/full.tcss"]
    MODES = {"full": FullScreen}
    BINDINGS = [
        Binding("f", "cycle_filter", "Filter"),
        Binding("a", "next_attention", "Attention"),
        Binding("s", "sync", "Sync"),
        Binding("N", "show_notifications", "Notifications"),
        Binding("question_mark", "show_help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    sessions: reactive[list[SessionInfo]] = reactive(list, recompose=False)
    ticket_overviews: reactive[list[TicketOverview]] = reactive(list, recompose=False)

    def __init__(self, root: Path | None = None):
        self._cli_root = root
        super().__init__()
        self.register_theme(create_duct_theme())
        self.theme = "duct"
        try:
            self._terminal_adapter = get_terminal_adapter("wezterm")
        except Exception:
            self._terminal_adapter = None
        self._tui_pane_id: int | None = None
        self._docked_pane_id: int | None = None
        self._docked_session_pid: int | None = None
        self._previewing_pid: int | None = None
        self._session_state: _SessionState = _SessionState.BROWSING
        self._pane_context: PaneContext | None = None
        # Pane id of a session spawned in foreground mode that we want to
        # highlight in the SessionPanel as soon as the session list
        # refresh picks it up. ``_pending_session_select_expires_at`` is a
        # wall-clock deadline after which we give up matching (so a
        # spawn-then-immediately-killed session doesn't leave a stale
        # pending highlight that mis-fires on the next unrelated session).
        self._pending_session_select_pane: int | None = None
        self._pending_session_select_expires_at: float = 0.0
        # Set briefly while we programmatically switch the active TUI tab
        # to "sessions-tab" as part of a foreground launch — suppresses the
        # ``on_tabbed_content_tab_activated`` undock-and-reset, which
        # would otherwise immediately tear down the dock we're about to
        # create.
        self._suppress_next_tab_reset: bool = False
        # Notifications and the auto-orchestrate schedule are owned by the
        # `duct daemon` background service now, not the TUI. The TUI keeps
        # _auto_orchestrate_cfg only so its manual "run now" can honour
        # sync_first.
        self._auto_orchestrate_cfg: AutoOrchestrateConfig = AutoOrchestrateConfig()
        # Skip-when-busy guards. Each is set by the worker on entry and
        # cleared on exit (best-effort, see comments at use sites). Without
        # these, a 2 s interval timer can pile up multiple parallel
        # ``_refresh_sessions`` workers when each refresh is taking longer
        # than the interval — they all hit `wezterm cli` simultaneously and
        # serialize on the wezterm IPC daemon, making the next refresh even
        # slower.
        self._refresh_in_progress = False
        self._initial_load_done = False
        # Timestamp of the user's most recent preview/dock action. Used by
        # ``_refresh_sessions`` to defer the 2 s background refresh while the
        # user is actively interacting with the session list — the refresh
        # batch otherwise contends with the user's preview/dock on the
        # wezterm IPC daemon and produces visible lag.
        self._last_user_action_at: float = 0.0

    def _watch_theme(self, theme_name: str) -> None:
        super()._watch_theme(theme_name)
        self.ansi_color = True
        root = self._cli_root or _resolve_workspace_root()
        self.data = DataManager(root)
        self.fast_markdown: bool = False
        try:
            cfg = load_config(root)
            self.icons: Icons = get_icons(cfg.display.nerd_font)
            self.fast_markdown = cfg.display.fast_markdown
            self._auto_orchestrate_cfg = cfg.auto_orchestrate
        except Exception:
            self.icons = get_icons()

    _ALL_SOURCES = frozenset({"jira", "github", "workspace", "sessions", "ci"})

    def on_mount(self) -> None:
        if self._terminal_adapter:
            self._tui_pane_id = self._terminal_adapter.get_own_pane_id()
            self._refresh_pane_context()
        atexit.register(self._undock_sync)
        self.switch_mode("full")
        self._load_initial_data()
        self.set_interval(2.0, self._refresh_sessions)
        self.set_interval(15.0, self._tick_sync_status)
        # Periodic sync and the auto-orchestrate schedule are owned by the
        # `duct daemon`; the TUI reads synced data from disk and offers manual
        # force-sync on `s`.
        self.set_interval(30.0, self._tick_daemon_status)

    @work(thread=True)
    def _load_initial_data(self) -> None:
        # Stage the load: push sessions to the UI as soon as session
        # discovery completes, then push ticket overviews when the
        # workspace walk + parallel git status finishes. This lets the
        # session sidebar render in ~hundreds of ms even when the ticket
        # overview phase takes several seconds.
        try:
            with perf.Timer("tui.load_initial"):
                with perf.Timer("tui.load_initial.sessions"):
                    raw_sessions, sessions = self.data.load_sessions_staged(
                        adapter=self._terminal_adapter,
                    )
                self.call_from_thread(self._set_sessions, sessions)

                with perf.Timer("tui.load_initial.overviews"):
                    overviews = self.data.load_ticket_overviews_staged(
                        adapter=self._terminal_adapter,
                        raw_sessions=raw_sessions,
                    )
                self.call_from_thread(
                    self._update_state, sessions, ticket_overviews=overviews,
                )
            self._refresh_sync_statuses()
        finally:
            self._initial_load_done = True

    _USER_ACTION_DEFER_S = 1.5

    @work(thread=True)
    def _refresh_sessions(self) -> None:
        # Skip if a refresh (or the initial load) is still in flight. The
        # 2 s tick fires regardless; without this guard, slow wezterm IPC
        # produced 5+ parallel workers all hammering `wezterm cli` and
        # serialising on the daemon — the contention itself was making the
        # next refresh even slower.
        if self._refresh_in_progress or not self._initial_load_done:
            return
        # Defer if the user just interacted with the session list. The
        # refresh fires parallel `wezterm cli get-text` calls that contend
        # with the user's preview/dock on the wezterm IPC daemon; pausing
        # for ~1.5 s after each user action gives interactive operations
        # exclusive use of the daemon.
        if time.monotonic() - self._last_user_action_at < self._USER_ACTION_DEFER_S:
            return
        self._refresh_in_progress = True
        try:
            with perf.Timer("tui.refresh_sessions"):
                sessions = self.data.load_sessions(adapter=self._terminal_adapter)
            self.call_from_thread(self._set_sessions, sessions)
        finally:
            self._refresh_in_progress = False

    def request_session_refresh(self, delay: float = 1.5) -> None:
        """Schedule an early session refresh after a state-changing action."""
        self.set_timer(delay, self._refresh_sessions)

    def _update_state(
        self,
        sessions: list[SessionInfo],
        ticket_overviews: list[TicketOverview] | None = None,
    ) -> None:
        self.sessions = sessions
        if ticket_overviews is not None:
            self.ticket_overviews = ticket_overviews

    def _set_sessions(self, sessions: list[SessionInfo]) -> None:
        self.sessions = sessions
        self._try_highlight_pending_session()

    def _try_highlight_pending_session(self) -> None:
        """If a foreground launch is awaiting a session-list match, resolve it.

        Matching is by pane id: iterate the current sessions, ask the
        terminal adapter for each session's pane, and if one matches the
        pending pane id, highlight that session in any visible
        SessionPanel. Times out after
        ``_pending_session_select_expires_at`` so a session that's killed
        before discovery doesn't leave a stale highlight target that
        mis-fires on the next unrelated session.
        """
        if self._pending_session_select_pane is None:
            return
        import time as _time
        if _time.monotonic() > self._pending_session_select_expires_at:
            self._pending_session_select_pane = None
            return

        adapter = self._terminal_adapter
        if adapter is None:
            return

        target_pane = self._pending_session_select_pane
        for s in self.sessions:
            if s.pid is None:
                continue
            try:
                pane = adapter.find_pane_for_pid(s.pid)
            except Exception:
                continue
            if pane == target_pane:
                self._pending_session_select_pane = None
                self._highlight_session_in_panel(s.session_id)
                return

    def _highlight_session_in_panel(self, session_id: str) -> None:
        """Move the SessionPanel cursor onto the session with ``session_id``.

        No-op if no SessionPanel is currently mounted (e.g. screen not
        ready yet). Suppresses the highlight side effect that would
        otherwise trigger a preview fetch — the dock is already happening
        for this session.
        """
        from duct_tui.widgets.session_panel import SessionPanel
        for panel in self.screen.query(SessionPanel):
            panel.highlight_session(session_id)


    def watch_tickets(self) -> None:
        self._push_to_screen()

    def watch_sessions(self) -> None:
        self._push_to_screen(tickets=False)

    def watch_ticket_overviews(self) -> None:
        self._push_to_screen(sessions=False)

    def _apply_sync_statuses(self, statuses, syncing: frozenset[str]) -> None:
        from duct_tui.widgets.sync_status import SyncStatusBar
        try:
            bar = self.screen.query_one(SyncStatusBar)
            bar.update_statuses(statuses, syncing)
        except Exception:
            pass

    @work(thread=True)
    def _refresh_sync_statuses(
        self, syncing: frozenset[str] = frozenset(),
    ) -> None:
        try:
            statuses = self.data.get_sync_status()
        except Exception:
            statuses = []
        self.call_from_thread(self._apply_sync_statuses, statuses, syncing)

    def _tick_sync_status(self) -> None:
        """Re-render the bar so relative ages stay current."""
        self._refresh_sync_statuses()

    def _tick_daemon_status(self) -> None:
        """Reflect daemon health + the orchestrator schedule hint into the UI.

        The `duct daemon` owns periodic sync and the auto-orchestrate schedule
        now; the TUI only presents their state. Daemon liveness is read from
        the heartbeat the daemon publishes at ``.duct/daemon.json``.
        """
        try:
            from duct_tui.widgets.orchestrator_tab import OrchestratorTab
            tab = self.screen.query_one(OrchestratorTab)
        except Exception:
            tab = None
        if tab is not None:
            tab.refresh_schedule_indicator()

        from duct.daemon_state import heartbeat_age_seconds
        from duct_tui.widgets.sync_status import SyncStatusBar
        try:
            age = heartbeat_age_seconds(self.data.root)
            bar = self.screen.query_one(SyncStatusBar)
        except Exception:
            return
        bar.update_daemon_status(age)

    def _push_to_screen(self, *, tickets: bool = True, sessions: bool = True) -> None:
        screen = self.screen
        if hasattr(screen, "_update_widgets"):
            screen._update_widgets(tickets=tickets, sessions=sessions)

    def action_sync(self) -> None:
        self._do_sync(force=True)

    @work(thread=True)
    def _do_sync(self, force: bool) -> None:
        self._refresh_sync_statuses(syncing=self._ALL_SOURCES)
        try:
            with perf.Timer("tui.do_sync"):
                results = self.data.run_sync(force=force)
                sessions, ticket_overviews = self.data.load_initial(
                    adapter=self._terminal_adapter,
                )
            self.call_from_thread(
                self._update_state, sessions, ticket_overviews=ticket_overviews,
            )
            if results:
                count = sum(r.tickets_synced for r in results)
                self.call_from_thread(self.notify, f"Synced {count} tickets")
            else:
                self.call_from_thread(self.notify, "All sources up to date")
        except Exception as exc:
            self.call_from_thread(self.notify, f"Sync error: {exc}", severity="error")
        finally:
            self._refresh_sync_statuses()

    def action_cycle_filter(self) -> None:
        try:
            from duct_tui.widgets.ticket_filter_bar import TicketFilterBar
            bar = self.screen.query_one(TicketFilterBar)
            bar.action_cycle_filter()
        except Exception:
            pass

    def action_next_attention(self) -> None:
        screen = self.screen
        if hasattr(screen, "focus_next_attention"):
            screen.focus_next_attention()

    def action_show_help(self) -> None:
        from duct_tui.modals.help import HelpModal
        self.push_screen(HelpModal())

    def action_show_notifications(self) -> None:
        from duct_tui.modals.notifications import NotificationsModal
        self.push_screen(NotificationsModal())

    # --- Session preview and docking ---
    #
    # Three states: BROWSING → PREVIEWING → DOCKED
    # See plan file for full state machine spec.

    def on_session_panel_session_select(self, event) -> None:
        """j/k highlight: BROWSING/PREVIEWING → PREVIEWING."""
        event.stop()
        self._last_user_action_at = time.monotonic()
        if self._session_state == _SessionState.DOCKED:
            return
        self._fetch_preview(event.pid)

    @work(thread=True, exclusive=True)
    def _fetch_preview(self, pid: int) -> None:
        if not self._terminal_adapter:
            return
        self._previewing_pid = pid
        with perf.Timer("tui.fetch_preview", pid=pid):
            text = self.data.get_session_preview(self._terminal_adapter, pid)
        if text:
            self.call_from_thread(self._show_preview, text)

    def _show_preview(self, text: str) -> None:
        if self._session_state == _SessionState.DOCKED:
            return
        if self._previewing_pid is None:
            # _reset_session_state ran after the fetch started; drop the stale result.
            return
        from duct_tui.widgets.session_preview import SessionPreview
        for preview in self.screen.query(SessionPreview):
            preview.update_content(text)
            preview.display = True
            self._session_state = _SessionState.PREVIEWING
            self._sync_split_class()
            return

    def _hide_preview(self) -> None:
        from duct_tui.widgets.session_preview import SessionPreview
        for preview in self.screen.query(SessionPreview):
            preview.clear()
            preview.display = False
            return

    def _sync_split_class(self) -> None:
        """Toggle the `session-split` class on #main-layout based on state.

        When a session is selected (PREVIEWING or DOCKED), the ticket tab's
        detail pane is squeezed to ~30% of the screen (because SessionPreview
        or the wezterm dock steals 70%). The class lets CSS collapse the
        detail switcher and give the summary column the full tab width.
        """
        try:
            main = self.screen.query_one("#main-layout")
        except Exception:
            return
        if self._session_state in (_SessionState.PREVIEWING, _SessionState.DOCKED):
            main.add_class("session-split")
        else:
            main.remove_class("session-split")

    def on_session_panel_session_focus(self, event) -> None:
        """l/Enter: PREVIEWING → DOCKED."""
        event.stop()
        self._last_user_action_at = time.monotonic()
        self._activate_session(event.pid)

    def on_session_preview_activate(self, event) -> None:
        """Click on the preview pane: PREVIEWING → DOCKED for the current preview."""
        event.stop()
        self._last_user_action_at = time.monotonic()
        if self._previewing_pid is not None:
            self._activate_session(self._previewing_pid)

    def _activate_session(self, pid: int) -> None:
        if self._tui_pane_id is None or not self._terminal_adapter:
            self.notify("Not running in WezTerm — cannot dock session", severity="warning")
            return
        self._session_state = _SessionState.DOCKED
        self._sync_split_class()
        self._hide_preview()
        self._dock_session(pid)

    @work(thread=True)
    def _dock_session(self, pid: int) -> None:
        with perf.Timer("tui.dock_session", pid=pid):
            pane_id = self.data.do_dock_session(
                self._terminal_adapter,
                self._tui_pane_id,
                pid,
                self._docked_pane_id,
            )
        if pane_id is not None:
            self._docked_pane_id = pane_id
            self._docked_session_pid = pid
            self.call_from_thread(self._on_dock_complete, pid)
        else:
            self._docked_pane_id = None
            self._docked_session_pid = None
            self.call_from_thread(
                self.notify, "Could not find session pane to dock", severity="error",
            )

    def _on_dock_complete(self, pid: int) -> None:
        self._refresh_pane_context()
        self._update_docked_indicator(pid)
        # Tell the daemon which session we're watching so it suppresses a
        # redundant notification for it.
        from duct.global_state import set_focused_session_pid
        set_focused_session_pid(pid)

    def on_session_panel_session_deselect(self, event) -> None:
        """Escape: PREVIEWING → BROWSING, or DOCKED → PREVIEWING."""
        event.stop()
        if self._session_state == _SessionState.DOCKED:
            self._undock_session()
        elif self._session_state == _SessionState.PREVIEWING:
            self._session_state = _SessionState.BROWSING
            self._previewing_pid = None
            self._sync_split_class()
            self._hide_preview()

    @work(thread=True)
    def _undock_session(self) -> None:
        pid = self._docked_session_pid
        self._undock_sync()
        self.call_from_thread(self._on_undock_complete, pid)

    def _on_undock_complete(self, pid: int | None) -> None:
        self._refresh_pane_context()
        self._update_docked_indicator(None)
        if self._session_state != _SessionState.DOCKED:
            # A reset (e.g. tab switch) landed while the undock worker was in flight.
            # Don't resurrect the preview on top of whatever the user switched to.
            return
        self._session_state = _SessionState.PREVIEWING
        self._sync_split_class()
        if pid and self._terminal_adapter:
            self._fetch_preview(pid)

    def _undock_sync(self) -> None:
        """Synchronously undock the current session pane. Safe to call from any thread.

        Also the ``atexit`` handler, so clearing the daemon focus marker here
        covers both undock and app exit.
        """
        if self._docked_pane_id and self._terminal_adapter:
            self.data.do_undock_session(self._terminal_adapter, self._docked_pane_id)
        self._docked_pane_id = None
        self._docked_session_pid = None
        from duct.global_state import set_focused_session_pid
        set_focused_session_pid(None)

    def _update_docked_indicator(self, pid: int | None) -> None:
        """Update all SessionPanel widgets to show which session is docked."""
        from duct_tui.widgets.session_panel import SessionPanel
        for panel in self.screen.query(SessionPanel):
            panel.set_docked_pid(pid)

    def _reset_session_state(self) -> None:
        """Undock if docked, hide preview, clear highlight, return to BROWSING."""
        if self._docked_pane_id:
            self._undock_sync()
            self._update_docked_indicator(None)
        self._hide_preview()
        self._previewing_pid = None
        self._session_state = _SessionState.BROWSING
        self._sync_split_class()
        from duct_tui.widgets.session_panel import SessionPanel
        for panel in self.screen.query(SessionPanel):
            panel.clear_highlight()

    # --- Orchestrator root session ---

    def on_orchestrator_tab_root_session_launch(self, event) -> None:
        """Launch a Claude Code session at workspace root.

        Foreground (default): switches to the Sessions tab, docks the new
        pane, and highlights the new session in the list.
        Background (``event.background``): spawn-only — no tab switch, no
        dock, no highlight. The session shows up in the Sessions tab on
        the next refresh.
        """
        event.stop()
        if event.background:
            self._launch_root_session_background(prompt=event.prompt)
        else:
            self._launch_and_dock_root_session(prompt=event.prompt)

    def on_orchestrator_tab_ticket_session_launch(self, event) -> None:
        """Launch a Claude Code session scoped to a ticket.

        Foreground (default): switches to the Sessions tab, docks the new
        pane, and highlights the new session in the list.
        Background (``event.background``): spawn-only — no tab switch, no
        dock, no highlight. Posted by ``execute_approved_action`` for
        approved ``prompt`` or ``concrete: launch_session`` actions.
        """
        event.stop()
        if event.background:
            self._launch_ticket_session_background(
                ticket_key=event.ticket_key,
                repo=event.repo,
                prompt=event.prompt,
            )
        else:
            self._launch_and_dock_ticket_session(
                ticket_key=event.ticket_key,
                repo=event.repo,
                prompt=event.prompt,
            )

    @work(thread=True)
    def _launch_and_dock_root_session(self, prompt: str | None = None) -> None:
        try:
            if self._tui_pane_id is None or not self._terminal_adapter:
                self.call_from_thread(
                    self.notify, "Not running in WezTerm — cannot spawn session",
                    severity="warning",
                )
                return

            from duct.api import _send_prompt_to_pane
            from duct.session import build_session_command
            cmd = build_session_command()
            pane_id = self._terminal_adapter.spawn_pane(self.data.root, cmd)
            if pane_id is not None and prompt:
                # Send the prompt via bracketed paste rather than positional
                # argv — claude hangs silently on long multi-line prompt argv.
                _send_prompt_to_pane(self._terminal_adapter, pane_id, prompt, submit=True)
            self._dock_spawned_pane(pane_id, label="Root session")
        except Exception as exc:
            self.call_from_thread(
                self.notify, f"Launch failed: {exc}", severity="error",
            )

    @work(thread=True)
    def _launch_and_dock_ticket_session(
        self, ticket_key: str, repo: str | None, prompt: str | None,
    ) -> None:
        try:
            if self._tui_pane_id is None or not self._terminal_adapter:
                self.call_from_thread(
                    self.notify,
                    "Not running in WezTerm — cannot spawn session",
                    severity="warning",
                )
                return

            pane_id = self.data.do_spawn_session(
                self._terminal_adapter, ticket_key, repo=repo, prompt=prompt,
            )
            self._dock_spawned_pane(pane_id, label=f"Session for {ticket_key}")
        except Exception as exc:
            self.call_from_thread(
                self.notify, f"Launch failed: {exc}", severity="error",
            )

    @work(thread=True)
    def _launch_root_session_background(
        self, prompt: str | None = None,
    ) -> None:
        """Workspace-root spawn that intentionally skips docking and tab-switching.

        User stays on whichever tab they triggered the launch from (Conduct,
        in practice). The new session appears in the Sessions tab list on
        the next refresh.
        """
        try:
            if self._tui_pane_id is None or not self._terminal_adapter:
                self.call_from_thread(
                    self.notify, "Not running in WezTerm — cannot spawn session",
                    severity="warning",
                )
                return

            from duct.api import _send_prompt_to_pane
            from duct.session import build_session_command
            cmd = build_session_command()
            pane_id = self._terminal_adapter.spawn_pane(self.data.root, cmd)
            if pane_id is None:
                self.call_from_thread(
                    self.notify, "Failed to spawn session pane", severity="error",
                )
                return
            if prompt:
                _send_prompt_to_pane(
                    self._terminal_adapter, pane_id, prompt, submit=True,
                )
            self.call_from_thread(self.notify, "Root session launched (bg)")
            self.call_from_thread(self.request_session_refresh)
        except Exception as exc:
            self.call_from_thread(
                self.notify, f"Launch failed: {exc}", severity="error",
            )

    @work(thread=True)
    def _launch_ticket_session_background(
        self, ticket_key: str, repo: str | None, prompt: str | None,
    ) -> None:
        """Ticket-scoped spawn that intentionally skips docking and tab-switching."""
        try:
            if self._tui_pane_id is None or not self._terminal_adapter:
                self.call_from_thread(
                    self.notify,
                    "Not running in WezTerm — cannot spawn session",
                    severity="warning",
                )
                return

            pane_id = self.data.do_spawn_session(
                self._terminal_adapter, ticket_key, repo=repo, prompt=prompt,
            )
            if pane_id is None:
                self.call_from_thread(
                    self.notify, "Failed to spawn session pane", severity="error",
                )
                return
            self.call_from_thread(
                self.notify, f"Session for {ticket_key} launched (bg)",
            )
            self.call_from_thread(self.request_session_refresh)
        except Exception as exc:
            self.call_from_thread(
                self.notify, f"Launch failed: {exc}", severity="error",
            )

    def _dock_spawned_pane(self, pane_id: int | None, label: str) -> None:
        """Common tail of the launch-and-dock workers (foreground only).

        Spawning produces a pane id; this method:
        - Notifies the user.
        - Switches the active TUI tab to the Sessions tab so the dock
          sits next to the session list, not the conductor or ticket pane
          we launched from.
        - Records ``pane_id`` as the pending-highlight target so the next
          session-list refresh highlights the new session.
        - Undocks any previously docked pane.
        - Docks the new pane next to the TUI.

        Called from within the worker (``_launch_and_dock_*``) so blocking
        calls are fine; UI touches still go through ``call_from_thread``.
        """
        if pane_id is None:
            self.call_from_thread(
                self.notify, "Failed to spawn session pane", severity="error",
            )
            return

        self.call_from_thread(self.notify, f"{label} launched")

        # Mark a pending highlight for the new pane and switch the TUI tab
        # to Sessions before the dock lands — the suppression flag stops
        # ``on_tabbed_content_tab_activated`` from immediately resetting
        # the session state we are about to enter.
        import time as _time
        self._pending_session_select_pane = pane_id
        self._pending_session_select_expires_at = _time.monotonic() + 15.0
        self._suppress_next_tab_reset = True
        self.call_from_thread(self._switch_to_sessions_tab)
        self.call_from_thread(self.request_session_refresh)

        adapter = self._terminal_adapter
        tui_pane = self._tui_pane_id
        if adapter is None or tui_pane is None:
            return

        if self._docked_pane_id is not None:
            adapter.undock_pane(self._docked_pane_id)
            self._docked_pane_id = None
            self._docked_session_pid = None

        if adapter.dock_pane(tui_pane, pane_id):
            self._docked_pane_id = pane_id
            self._docked_session_pid = None
            self._session_state = _SessionState.DOCKED
            self.call_from_thread(self._sync_split_class)
            adapter.activate_pane(pane_id)
        else:
            self.call_from_thread(
                self.notify, "Could not dock session pane", severity="warning",
            )

    def _switch_to_sessions_tab(self) -> None:
        """Activate the Sessions TabPane in the FullScreen TabbedContent.

        No-op if the current screen isn't FullScreen (e.g. a modal is up)
        or the tab isn't present. ``_suppress_next_tab_reset`` is consumed
        by ``on_tabbed_content_tab_activated`` so the docked state isn't
        immediately torn down.
        """
        from textual.widgets import TabbedContent
        from duct_tui.screens.full import FullScreen
        screen = self.screen
        if not isinstance(screen, FullScreen):
            return
        try:
            tabs = screen.query_one(TabbedContent)
        except Exception:
            return
        if tabs.active != "sessions-tab":
            tabs.active = "sessions-tab"

    # --- Navigation away from sessions ---

    def on_tabbed_content_tab_activated(self, event) -> None:
        """Undock when switching tabs — the new tab won't have the same session context.

        Suppressed for a single activation when a foreground launch is
        programmatically switching to the Sessions tab; otherwise the
        pane we just docked would be undocked before the user even sees
        it.
        """
        if self._suppress_next_tab_reset:
            self._suppress_next_tab_reset = False
            return
        self._reset_session_state()

    def on_app_focus(self) -> None:
        """DOCKED → PREVIEWING when focus returns to TUI; while the Sessions
        tab is active, restore focus to the SessionPanel so j/k navigate the
        list without an extra click."""
        if self._session_state == _SessionState.DOCKED and self._docked_pane_id is not None:
            self._undock_session()
        self._refocus_sessions_panel_if_active()

    def _refocus_sessions_panel_if_active(self) -> None:
        from textual.widgets import TabbedContent, TabPane
        from duct_tui.screens.full import FullScreen
        from duct_tui.widgets.session_panel import SessionPanel
        screen = self.screen
        if not isinstance(screen, FullScreen):
            return
        try:
            tabs = screen.query_one(TabbedContent)
        except Exception:
            return
        if tabs.active != "sessions-tab":
            return
        try:
            pane = tabs.query_one("#sessions-tab", TabPane)
            panel = pane.query_one(SessionPanel)
        except Exception:
            return
        panel.focus()

    def on_unmount(self) -> None:
        """Undock on app shutdown so we don't leave a stale split."""
        self._undock_sync()
        atexit.unregister(self._undock_sync)

    # --- Layout awareness ---

    def _refresh_pane_context(self) -> None:
        """Query the terminal adapter for current pane layout context."""
        if self._terminal_adapter and self._tui_pane_id is not None:
            try:
                self._pane_context = self._terminal_adapter.get_pane_context(
                    self._tui_pane_id,
                )
            except Exception:
                self._pane_context = None
        else:
            self._pane_context = None

    def on_resize(self, event) -> None:
        self._refresh_pane_context()


def main():
    sys.stdout.write("\033]0;duct\007")
    sys.stdout.flush()
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    try:
        if root is None:
            root = _resolve_workspace_root()
    except ConfigError as exc:
        sys.stderr.write(f"duct-tui: {exc}\n\n")
        sys.stderr.write(
            "duct-tui must be started from inside a duct workspace.\n"
            "Resolution options:\n"
            "  - cd into a duct workspace (a directory containing toolkit/config.yaml) and re-run\n"
            "  - pass the workspace path as an argument: duct-tui /path/to/workspace\n"
            "  - set the DUCT_ROOT environment variable to the workspace path\n"
            "  - create a new workspace with: duct init\n"
        )
        sys.exit(1)
    app = DuctApp(root=root)
    app.run()


if __name__ == "__main__":
    main()

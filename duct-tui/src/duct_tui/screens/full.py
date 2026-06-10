"""FullScreen -- tabbed workspace view with overview and ticket tabs."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Static, Tabs, TabbedContent, TabPane

from duct_tui.widgets.orchestrator_tab import OrchestratorTab
from duct_tui.widgets.pr_tab import PRListPanel, PRTab
from duct_tui.widgets.session_panel import SessionPanel
from duct_tui.widgets.session_preview import SessionPreview
from duct_tui.widgets.sync_status import SyncStatusBar
from duct_tui.widgets.ticket_card_list import TicketCardList
from duct_tui.widgets.ticket_filter_bar import TicketFilterBar
from duct_tui.widgets.ticket_tab import TicketTab


class FullScreen(Screen):
    BINDINGS = [
        Binding("tab", "next_tab", "Next tab", priority=True),
        Binding("shift+tab", "prev_tab", "Prev tab", priority=True),
        Binding("right_square_bracket", "next_tab", "]", key_display="]", show=False),
        Binding("left_square_bracket", "prev_tab", "[", key_display="[", show=False),
        Binding("w", "close_tab", "Clear ticket"),
        *[Binding(str(n), f"focus_tab({n})", f"Tab {n}", show=False) for n in range(1, 6)],
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield SyncStatusBar()
        with Horizontal(id="main-layout"):
            with TabbedContent(id="tabs"):
                with TabPane("Overview", id="overview"):
                    yield TicketFilterBar()
                    yield TicketCardList()
                with TabPane("PRs", id="prs-tab"):
                    yield PRTab()
                with TabPane("Sessions", id="sessions-tab"):
                    yield SessionPanel()
                with TabPane("Conduct", id="orchestrator-tab"):
                    yield OrchestratorTab()
                with TabPane("Ticket", id="ticket-detail"):
                    yield Static(
                        "No ticket selected — press Ctrl+K to find one",
                        id="ticket-placeholder",
                    )
            yield SessionPreview(id="session-preview")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(Tabs).can_focus = False
        # Show a placeholder right away so the empty card area doesn't
        # look broken while the ticket-overview phase of `load_initial`
        # finishes. The first `update_tickets` call clears it.
        try:
            self.query_one(TicketCardList).show_loading()
        except Exception:
            pass
        self._update_widgets()

    def _update_widgets(self, *, tickets: bool = True, sessions: bool = True) -> None:
        app = self.app
        if tickets:
            try:
                card_list = self.query_one(TicketCardList)
                if hasattr(app, 'ticket_overviews'):
                    card_list.update_tickets(app.ticket_overviews)
            except Exception:
                pass
            try:
                self.query_one(PRTab).refresh_data()
            except Exception:
                pass
            for tab in self.query(TicketTab):
                try:
                    tab._load_data()
                except Exception:
                    pass
        if sessions:
            for panel in self.query(SessionPanel):
                try:
                    panel.update_sessions(app.sessions)
                except Exception:
                    pass

    def on_ticket_filter_bar_filter_changed(self, event: TicketFilterBar.FilterChanged) -> None:
        self._reload_with_filter(event.mode)

    @work(thread=True)
    def _reload_with_filter(self, mode: str) -> None:
        overviews = self.app.data.load_ticket_overviews(filter_mode=mode)
        self.app.call_from_thread(self._apply_overviews, overviews)

    def _apply_overviews(self, overviews) -> None:
        self.app.ticket_overviews = overviews
        try:
            card_list = self.query_one(TicketCardList)
            card_list.update_tickets(overviews)
        except Exception:
            pass

    def on_ticket_card_list_ticket_selected(self, event: TicketCardList.TicketSelected) -> None:
        self._open_ticket(event.ticket_key)

    def _category_for_key(self, key: str) -> str:
        for o in getattr(self.app, "ticket_overviews", []):
            if o.key == key:
                return o.category
        return ""

    def _assigned_to_me_for_key(self, key: str) -> bool:
        for o in getattr(self.app, "ticket_overviews", []):
            if o.key == key:
                return bool(getattr(o, "assigned_to_me", True))
        return True

    def _open_ticket(self, key: str) -> None:
        """Render a ticket's detail in the single persistent "Ticket" tab.

        Swaps the pane's content for a fresh ``TicketTab`` and tints the tab
        label with the ticket's phase colour, mirroring how the overview card
        border is coloured. Focus is deferred until after the mount lands.
        """
        from duct_tui.phases import PHASE_COLORS, get_phase_icon, phase_for_category
        from duct_tui.widgets.ticket_tab import TicketTab

        tabs = self.query_one(TabbedContent)
        pane = tabs.get_pane("ticket-detail")
        for child in list(pane.children):
            child.remove()
        pane.mount(TicketTab(key))

        category = self._category_for_key(key)
        assigned_to_me = self._assigned_to_me_for_key(key)
        phase = phase_for_category(category)
        color = PHASE_COLORS.get(phase, "")
        icon = get_phase_icon(self.app.icons, phase) if hasattr(self.app, "icons") else ""
        if not assigned_to_me:
            label = f"[dim]{icon} {key}[/dim]" if icon else f"[dim]{key}[/dim]"
        else:
            label = f"[{color}]{icon}[/{color}] {key}" if color else key
        tabs.get_tab("ticket-detail").label = label

        self.call_after_refresh(self._switch_to_pane, pane)

    def _switch_to_pane(self, pane: TabPane) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = pane.id or ""
        if pane.id != "sessions-tab":
            app = self.app
            if hasattr(app, "_reset_session_state"):
                app._reset_session_state()
        focusable = pane.query("*:can-focus")
        if focusable:
            focusable.first().focus()

    def action_next_tab(self) -> None:
        tabs = self.query_one(TabbedContent)
        panes = list(tabs.query(TabPane))
        if not panes:
            return
        current_idx = next((i for i, p in enumerate(panes) if p.id == tabs.active), 0)
        self._switch_to_pane(panes[(current_idx + 1) % len(panes)])

    def action_prev_tab(self) -> None:
        tabs = self.query_one(TabbedContent)
        panes = list(tabs.query(TabPane))
        if not panes:
            return
        current_idx = next((i for i, p in enumerate(panes) if p.id == tabs.active), 0)
        self._switch_to_pane(panes[(current_idx - 1) % len(panes)])

    def action_focus_tab(self, index: int) -> None:
        tabs = self.query_one(TabbedContent)
        panes = list(tabs.query(TabPane))
        if 0 < index <= len(panes):
            self._switch_to_pane(panes[index - 1])

    def action_close_tab(self) -> None:
        """Clear the Ticket tab back to its placeholder (only when it's active)."""
        tabs = self.query_one(TabbedContent)
        if tabs.active != "ticket-detail":
            return
        pane = tabs.get_pane("ticket-detail")
        for child in list(pane.children):
            child.remove()
        pane.mount(
            Static(
                "No ticket selected — press Ctrl+K to find one",
                id="ticket-placeholder",
            )
        )
        tabs.get_tab("ticket-detail").label = "Ticket"
        self._switch_to_pane(tabs.get_pane("overview"))

    def on_pr_list_panel_pr_opened(self, event: PRListPanel.PROpened) -> None:
        import webbrowser
        webbrowser.open(event.url)

    def on_pr_list_panel_ticket_jump(self, event: PRListPanel.TicketJump) -> None:
        import webbrowser

        from duct.api import resolve_ticket_dir
        from duct.config import load_config

        key = event.ticket_key
        # If the ticket exists in the workspace, open it in the Ticket tab.
        if resolve_ticket_dir(self.app.data.root, key) is not None:
            self._open_ticket(key)
            return
        # Orphan review PR — open the Jira ticket in the browser instead.
        cfg = load_config(self.app.data.root)
        if cfg.jira_domain:
            webbrowser.open(f"https://{cfg.jira_domain}/browse/{key}")
        else:
            self.app.notify(
                "jira.domain not configured — cannot open ticket",
                severity="warning",
            )

    def on_session_panel_session_stop(self, event) -> None:
        """Stop a session from any SessionPanel (global Sessions tab or ticket tab)."""
        from duct_tui.modals.confirm import ConfirmModal
        event.stop()
        self.app.push_screen(
            ConfirmModal(f"Stop session PID {event.pid}?"),
            callback=lambda ok: self._do_stop(event.pid) if ok else None,
        )

    @work(thread=True)
    def _do_stop(self, pid: int) -> None:
        self.app.data.do_stop_session(pid)
        self.app.call_from_thread(self.app.notify, f"Session {pid} stopped")
        self.app.call_from_thread(self.app.request_session_refresh, 0.5)

    def on_session_panel_session_launch(self, event) -> None:
        """Launch a new session from the main Sessions tab (no ticket context).

        TicketTab has its own handler for ticket-scoped launches; Textual delivers
        messages to the nearest ancestor with a matching handler first, so this
        only fires for the un-scoped SessionPanel in the Sessions tab.
        """
        from duct_tui.modals.launch_directory import LaunchDirectoryModal
        event.stop()
        self.app.push_screen(
            LaunchDirectoryModal(default_cwd=self.app.data.root),
            callback=self._on_launch_directory_result,
        )

    def _on_launch_directory_result(self, result) -> None:
        if result:
            self._do_launch_in_dir(result)

    @work(thread=True)
    def _do_launch_in_dir(self, config) -> None:
        try:
            self.app.data.do_launch_session_in_dir(config.cwd, config.prompt)
            self.app.call_from_thread(
                self.app.notify, f"Session launched in {config.cwd}",
            )
            self.app.call_from_thread(self.app.request_session_refresh)
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify, f"Launch failed: {exc}", severity="error",
            )

    def focus_next_attention(self) -> None:
        try:
            card_list = self.query_one(TicketCardList)
            card_list.focus_next_attention()
        except Exception:
            pass

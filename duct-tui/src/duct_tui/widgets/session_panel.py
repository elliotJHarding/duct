"""SessionPanel -- session list with rich status cards."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from duct.models import SessionInfo
from duct_tui.icons import Icons, UNICODE
from duct_tui.widgets.ticket_badge import render_ticket_badge
from duct_tui.widgets.vim_mixin import VimListMixin


# Anthropic Claude "working" orange — keep in sync with theme.py agent-working var
_WORKING_COLOR = "#d97757"

# Claude plan-mode teal — verbatim from Claude Code's SGR sequence for the
# "⏸ plan mode on" banner (ESC[38:2::72:150:140m = RGB(72, 150, 140)).
_PLAN_MODE_COLOR = "#48968c"

# Default (implement) mode — a red with a faint warm undertone, matching
# Claude's own red accent (less pink than the previous #ff6b8a).
_DEFAULT_MODE_COLOR = "#dc4a54"

# Braille spinner frames (⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏) for animated "working" indicator
_SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"

_STATUS_COLORS = {
    "working":    _WORKING_COLOR,
    "ready":      "cyan",
    "done":       "green",
    "stale":      "bright_black",
    "waiting":    "yellow",
    "terminated": "bright_black",
}

_LAUNCH_ROW_ID = "__launch__"


def _status_icon(icons: Icons, status: str) -> str:
    return {
        "ready": icons.session_ready,
        "done": icons.session_ready,
        "stale": icons.session_stale,
        "waiting": icons.session_waiting,
        "terminated": icons.session_terminated,
    }.get(status, icons.session)


def _mode_icon(icons: Icons, mode: str) -> str:
    return icons.mode_plan if mode == "plan" else icons.mode_default


def _mode_colour(mode: str, is_working: bool) -> str:
    """Colour for the top row — working overrides plan, plan overrides default."""
    if is_working:
        return _WORKING_COLOR
    if mode == "plan":
        return _PLAN_MODE_COLOR
    return _DEFAULT_MODE_COLOR


def _trunc(value: str, width: int) -> str:
    """Truncate `value` to fit in `width` cells, adding an ellipsis if shortened."""
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width == 1:
        return "\u2026"
    return value[: width - 1] + "\u2026"


def render_session_card(
    s: SessionInfo,
    *,
    icons: Icons,
    spinner_frame: int,
    docked_pid: int | None = None,
    topic_width: int = 60,
    show_ticket: bool = True,
) -> Text:
    """Render a session card. Shared by SessionPanel, TicketSummaryPane, and TicketCard.

    Layout:
      Line 1: <mode-icon> <ticket>  <topic>
              Coloured orange when working, blue when in plan mode, dim otherwise.
      Line 2:   <status-icon> <status-word>  ·  <rel>  ·  <cwd>  [·  docked]
                The status icon + word share the status colour; metadata is dim.

    Pass ``show_ticket=False`` when the render context already scopes to one
    ticket (overview card, ticket summary pane) so the badge isn't duplicated.
    """
    # Tolerate both SessionInfo (sessions/ticket tabs) and SessionSummary
    # (overview cards) — SessionSummary omits cwd/pid/ticket_key.
    is_working = s.status == "working"
    rel = _relative_time(s.last_activity)
    cwd = getattr(s, "cwd", "") or ""
    cwd_short = _short_cwd(cwd) if cwd else ""
    pid = getattr(s, "pid", None)
    ticket_key = getattr(s, "ticket_key", None)
    docked = bool(pid and docked_pid is not None and pid == docked_pid)

    line1_colour = _mode_colour(s.mode, is_working)
    mode_symbol = _mode_icon(icons, s.mode)

    t = Text()
    # Line 1: mode icon + ticket + topic. The mode icon sits where the status
    # symbol used to be; the whole row turns orange when Claude is working.
    t.append(f"{mode_symbol}  ", style=line1_colour)
    # Working tints the whole title row orange. Other states render the
    # ticket key as the shared badge (see ticket_badge.render_ticket_badge)
    # so it reads as a distinct identifier without the harshness of a
    # full-contrast reverse.
    topic_style = line1_colour if is_working else ""
    if show_ticket and ticket_key:
        if is_working:
            t.append(ticket_key, style=f"bold {line1_colour}")
        else:
            t.append_text(render_ticket_badge(ticket_key))
        if s.topic:
            t.append("  ")
            t.append(_trunc(s.topic, topic_width), style=topic_style)
    elif s.topic:
        t.append(_trunc(s.topic, topic_width), style=topic_style or "bold")
    else:
        fallback = cwd_short or "(session)"
        t.append(_trunc(fallback, topic_width), style="bold bright_black")
    t.append("\n")

    # Line 2: status icon + status word (both coloured), then dim metadata.
    if is_working:
        status_symbol = _SPINNER_FRAMES[spinner_frame % len(_SPINNER_FRAMES)]
    else:
        status_symbol = _status_icon(icons, s.status)
    status_colour = _STATUS_COLORS.get(s.status, "bright_black")

    t.append(f"{status_symbol}  ", style=status_colour)
    t.append(s.status, style=status_colour)

    sep = " \u00b7 "
    extras: list[str] = []
    # "unlinked" is only meaningful when the row is expected to carry a ticket
    # (i.e. we'd otherwise be rendering a badge). On overview/ticket panes the
    # ticket is implicit, so skip it.
    if show_ticket and not ticket_key:
        extras.append("unlinked")
    if rel:
        extras.append(rel)
    trailing: list[str] = ["docked"] if docked else []

    meta_parts = list(extras)
    if cwd_short and ticket_key:
        status_width = len(status_symbol) + 1 + len(s.status)  # "X status"
        fixed_width = sum(len(p) for p in extras) + len(sep) * len(extras)
        trailing_width = sum(len(p) for p in trailing) + len(sep) * len(trailing)
        cwd_budget = max(6, topic_width - status_width - fixed_width - trailing_width - len(sep))
        meta_parts.append(_trunc(cwd_short, cwd_budget))
    meta_parts.extend(trailing)

    if meta_parts:
        t.append(sep + sep.join(meta_parts), style="dim")
    # Blank trailing line for visual separation between rows
    t.append("\n")
    return t


_ACCENT_COLOR = "#bb9af7"  # matches theme.py accent var


def render_launch_row(icons: Icons) -> Text:
    """Render the 'Launch new session' action row that lives at the end of each list."""
    t = Text()
    t.append(f"{icons.session_launch} ", style=_ACCENT_COLOR)
    t.append("Launch new session", style=f"bold {_ACCENT_COLOR}")
    t.append("  [L]", style="dim")
    t.append("\n")
    return t


def _relative_time(iso_timestamp: str) -> str:
    """Convert an ISO 8601 timestamp to a human-friendly relative string."""
    try:
        then = datetime.fromisoformat(iso_timestamp)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - then
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"
    except (ValueError, TypeError):
        return ""


def _short_cwd(cwd: str) -> str:
    """Extract a readable short path from a working directory."""
    parts = cwd.rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else cwd


class SessionPanel(VimListMixin, OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("enter", "focus_docked", "Focus"),
        Binding("l", "focus_docked", "Focus", show=False),
        Binding("escape", "deselect", "Undock", show=False),
        *VimListMixin.VIM_BINDINGS,
    ]

    class SessionSelect(Message):
        """Emitted when user selects a session to dock alongside the TUI."""
        def __init__(self, pid: int) -> None:
            super().__init__()
            self.pid = pid

    class SessionDeselect(Message):
        """Emitted when user wants to undock the current session pane."""
        pass

    class SessionFocus(Message):
        """Emitted when user wants to dock and focus a session pane."""
        def __init__(self, pid: int) -> None:
            super().__init__()
            self.pid = pid

    class SessionLaunch(Message):
        pass

    class SessionStop(Message):
        def __init__(self, pid: int) -> None:
            super().__init__()
            self.pid = pid

    def __init__(self, ticket_key: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._sessions: list[SessionInfo] = []
        self._ticket_key = ticket_key
        self._docked_pid: int | None = None
        self._suppress_highlight = False
        self._spinner_frame: int = 0
        self._icons: Icons = UNICODE
        self.border_title = "Sessions"
        self._bindings.bind("L", "launch", "Launch")
        self._bindings.bind("x", "stop", "Stop")

    def on_mount(self) -> None:
        self._icons = getattr(self.app, "icons", UNICODE)
        self.set_interval(0.1, self._tick_spinner)

    def _tick_spinner(self) -> None:
        working_indices = [i for i, s in enumerate(self._sessions) if s.status == "working"]
        if not working_indices:
            return
        self._spinner_frame = (self._spinner_frame + 1) % len(_SPINNER_FRAMES)
        for i in working_indices:
            if i < self.option_count:
                self.replace_option_prompt_at_index(i, self._render_card(self._sessions[i]))


    def clear_highlight(self) -> None:
        """Remove the cursor highlight from the session list."""
        self._suppress_highlight = True
        self.highlighted = None
        self._suppress_highlight = False

    def highlight_session(self, session_id: str) -> None:
        """Move the cursor onto the row for ``session_id`` if present.

        Highlight events are suppressed during the move so the panel
        doesn't fire ``SessionSelect`` (which would kick off a preview
        fetch). Used by the foreground-launch flow to point at the newly
        spawned session — the dock is already in place by then.
        """
        for i, s in enumerate(self._sessions):
            if s.session_id == session_id:
                self._suppress_highlight = True
                self.highlighted = i
                self._suppress_highlight = False
                return

    def set_docked_pid(self, pid: int | None) -> None:
        """Update which session is currently docked and refresh the display."""
        if self._docked_pid != pid:
            self._docked_pid = pid
            self._rebuild_options()

    def update_sessions(self, sessions: list[SessionInfo]) -> None:
        if self._ticket_key:
            sessions = [s for s in sessions if s.ticket_key == self._ticket_key]
        self._sessions = sorted(
            (s for s in sessions if s.status != "terminated"),
            key=lambda s: (s.ticket_key is None, s.ticket_key or ""),
        )
        self._rebuild_options()

    def _rebuild_options(self) -> None:
        prev_highlighted = self.highlighted
        self._suppress_highlight = True
        self.clear_options()
        for s in self._sessions:
            self.add_option(Option(self._render_card(s), id=s.session_id))
        self.add_option(Option(render_launch_row(self._icons), id=_LAUNCH_ROW_ID))
        if prev_highlighted is not None and prev_highlighted < self.option_count:
            self.highlighted = prev_highlighted
        self._suppress_highlight = False

    def _render_card(self, s: SessionInfo) -> Text:
        return render_session_card(
            s,
            icons=self._icons,
            spinner_frame=self._spinner_frame,
            docked_pid=self._docked_pid,
            topic_width=60,
        )

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Show a terminal preview for the highlighted session."""
        event.stop()
        if self._suppress_highlight:
            return
        idx = self.highlighted
        if idx is None or idx >= len(self._sessions):
            # Launch row or out of bounds — nothing to preview
            return
        s = self._sessions[idx]
        if s.pid and s.status != "terminated":
            self.post_message(self.SessionSelect(s.pid))

    def action_focus_docked(self) -> None:
        idx = self.highlighted
        if idx is None:
            return
        if idx == len(self._sessions):
            # Enter / l on the launch row triggers a new session launch
            self.post_message(self.SessionLaunch())
            return
        if idx < len(self._sessions):
            s = self._sessions[idx]
            if s.pid and s.status != "terminated":
                self.post_message(self.SessionFocus(s.pid))

    def action_deselect(self) -> None:
        self.post_message(self.SessionDeselect())

    def _vim_goto_first(self) -> None:
        if self.option_count:
            self.highlighted = 0

    def _vim_goto_last(self) -> None:
        if self.option_count:
            self.highlighted = self.option_count - 1

    def action_launch(self) -> None:
        self.post_message(self.SessionLaunch())

    def action_stop(self) -> None:
        idx = self.highlighted
        if idx is not None and idx < len(self._sessions):
            s = self._sessions[idx]
            if s.pid:
                self.post_message(self.SessionStop(s.pid))

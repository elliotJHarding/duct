"""Notification decision logic + on-disk feed.

Pure, framework-free translation of session/ticket snapshots into the
notifications that should fire. The daemon owns the polling loop and the actual
``MacNotifier`` call; this module only decides *what* to notify and records the
agent-readable feed. Extracted from the TUI's old ``_fire_attention_notifications``.

Two entry points, one per daemon tick, sharing the tracker's state:

- ``diff_sessions`` (fast tick): fires on a session leaving "working" for a
  terminal/blocked state.
- ``diff_actions`` (slow tick): edge-triggers when a ticket gains pending actions.

Each has a one-shot seed guard so nothing fires for state that already existed
when the daemon started.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from duct import paths
from duct.models import Action, SessionInfo, TicketOverview

# Human-readable labels for action types, shown in the notification title.
_ACTION_TYPE_LABELS = {
    "prompt": "Prompt",
    "jira_comment": "Jira comment",
    "improve_workflow": "Workflow improvement",
}


def _action_type_label(action_type: str) -> str:
    return _ACTION_TYPE_LABELS.get(action_type, action_type)


@dataclass(frozen=True)
class NotificationEvent:
    """A single notification the daemon should fire (and append to the feed)."""

    # "done" | "waiting" | "pending-action" | "orchestrator" are daemon-diffed
    # kinds (filtered by config event_kinds). "orchestrator-action" is a
    # deliberate push from `duct notify` and bypasses that filter.
    kind: str
    title: str
    body: str
    group: str
    subtitle: str | None = None
    # Per-notification attached image; None lets the notifier attach the brand.
    content_image: str | None = None
    # Click-to-open target (Jira/GitHub ticket URL) when resolvable.
    open_url: str | None = None
    # Shell command run on click (takes precedence over open_url). Session
    # events use this to focus the session's terminal.
    execute: str | None = None

    def to_feed_dict(self, *, at: str) -> dict:
        return {
            "at": at,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "group": self.group,
            "subtitle": self.subtitle,
            "open_url": self.open_url,
        }


class NotificationTracker:
    """Stateful diff of successive snapshots into notification events.

    Holds the previous session statuses and the set of tickets already alerted
    for pending actions. Construct one per daemon process; call ``diff_*`` each
    tick. ``jira_domain`` (from config) is used to build click-to-open URLs.
    """

    def __init__(self, jira_domain: str = "", duct_bin: str | None = None) -> None:
        self._jira_domain = jira_domain
        self._duct_bin = duct_bin
        self._prev_statuses: dict[str, str] = {}
        # Sessions that left "working" last tick and are awaiting one more tick
        # of confirmation before we fire (session_id -> candidate terminal status).
        self._pending: dict[str, str] = {}
        self._alerted_keys: set[str] = set()
        self._seeded_sessions = False
        self._seeded_actions = False

    # --- session transitions (fast tick) ---

    def diff_sessions(
        self,
        sessions: list[SessionInfo],
        *,
        suppress_pids: Collection[int] = (),
    ) -> list[NotificationEvent]:
        """Events for sessions leaving "working" for ready/done/waiting.

        The first call seeds previous statuses and fires nothing, so sessions
        already terminal at daemon start don't notify. Sessions whose pid is in
        ``suppress_pids`` (the session the TUI is docked into, and the session
        whose terminal the user has in front) are skipped — the user is already
        watching them.

        A ``working → terminal`` transition is confirmed across one extra tick
        before firing: pane-text "working" detection occasionally misses a tick
        for a session that is still working, which would otherwise read as a
        spurious ``working → done`` edge and fire a duplicate notification. We
        therefore hold the candidate in ``_pending`` and only fire once the
        session is still non-working on the following tick; a dip back to
        "working" discards it as flicker.
        """
        if not self._seeded_sessions:
            self._prev_statuses = {s.session_id: s.status for s in sessions}
            self._seeded_sessions = True
            return []

        events: list[NotificationEvent] = []
        for s in sessions:
            prev = self._prev_statuses.get(s.session_id)
            if s.status == "working":
                # Working cancels a pending confirmation — the dip was flicker.
                self._pending.pop(s.session_id, None)
            elif prev == "working":
                # Just left working: arm a confirmation, don't fire yet.
                self._pending[s.session_id] = s.status
            elif s.session_id in self._pending:
                # Still non-working a tick later: the transition is real. Fire
                # for the current state (it may have settled done -> waiting).
                self._pending.pop(s.session_id)
                if s.pid is not None and s.pid in suppress_pids:
                    continue
                if s.status in ("ready", "done"):
                    events.append(self._done_event(s))
                elif s.status == "waiting":
                    events.append(self._waiting_event(s))
        self._prev_statuses = {s.session_id: s.status for s in sessions}
        return events

    def _done_event(self, s: SessionInfo) -> NotificationEvent:
        return NotificationEvent(
            kind="done",
            title=f"{self._label(s)}: done",
            body=s.topic or s.cwd or "Ready for next instruction",
            group=f"session-ready:{s.session_id}",
            subtitle="Plan ready for review" if s.mode == "plan" else None,
            execute=self._jump_cmd(s.session_id),
        )

    def _waiting_event(self, s: SessionInfo) -> NotificationEvent:
        return NotificationEvent(
            kind="waiting",
            title=f"{self._label(s)}: waiting",
            body=s.topic or s.cwd or "Agent is asking for input",
            group=f"session-waiting:{s.session_id}",
            execute=self._jump_cmd(s.session_id),
        )

    @staticmethod
    def _label(s: SessionInfo) -> str:
        """A human identifier for the session: ticket key, else its directory, else id."""
        if s.ticket_key:
            return s.ticket_key
        if s.cwd:
            return Path(s.cwd).name
        return s.session_id[:8]

    def _jump_cmd(self, session_id: str) -> str | None:
        """Shell command that focuses the session's terminal tab, for -execute."""
        if not self._duct_bin:
            return None
        return f"{shlex.quote(self._duct_bin)} session jump {shlex.quote(session_id)}"

    # --- pending actions (slow tick) ---

    def diff_actions(self, overviews: list[TicketOverview]) -> list[NotificationEvent]:
        """Events for newly-pending actions, one notification per action.

        Edge-triggered per action (keyed by ticket + action id): each fires once
        when it enters the pending set and re-arms only after that action leaves.
        Action ids are stable UUIDs, so a still-pending action stays silent across
        ticks. The first call seeds the alerted set and fires nothing.
        """
        current: dict[str, tuple[TicketOverview, Action]] = {
            f"pending-action:{o.key}:{a.id}": (o, a)
            for o in overviews
            for a in o.pending_actions
        }
        if not self._seeded_actions:
            self._alerted_keys = set(current)
            self._seeded_actions = True
            return []

        events: list[NotificationEvent] = []
        for key, (o, a) in current.items():
            if key not in self._alerted_keys:
                label = _action_type_label(a.type)
                events.append(
                    NotificationEvent(
                        kind="pending-action",
                        title=f"{o.key} · {label}",
                        body=a.description or label,
                        group=key,
                        open_url=self._url_for(o.key),
                    )
                )
        self._alerted_keys = set(current)
        return events

    # --- helpers ---

    def _url_for(self, key: str | None) -> str | None:
        if key and self._jira_domain:
            return f"https://{self._jira_domain}/browse/{key}"
        return None


def orchestrator_event(action_count: int) -> NotificationEvent:
    """Run-complete notification fired after a scheduled headless orchestrator run."""
    return NotificationEvent(
        kind="orchestrator",
        title="Orchestrator run complete",
        body=(
            f"{action_count} action{'s' if action_count != 1 else ''} proposed"
            if action_count
            else "No new actions proposed"
        ),
        group="orchestrator-run",
    )


def feed_path(root: Path) -> Path:
    return paths.notifications_feed(root)


def append_feed(root: Path, event: NotificationEvent, *, at: str | None = None) -> None:
    """Append one JSON line to the workspace's agent-readable notification feed."""
    stamp = at or datetime.now(timezone.utc).isoformat()
    path = feed_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event.to_feed_dict(at=stamp)) + "\n")


def fire_event(
    notifier, root: Path, event: NotificationEvent, *, sender: str | None = None
) -> None:
    """Fire one notification and record it to the feed.

    The single fire+record primitive shared by the daemon loop and the
    ``duct notify`` command. ``notifier`` is duck-typed (a ``MacNotifier``) so
    this module stays free of the firing layer — whether the popup actually
    shows is the notifier's concern (it no-ops when disabled); the feed is
    always written so the TUI has a record either way.
    """
    notifier.notify(
        event.title,
        event.body,
        subtitle=event.subtitle,
        group=event.group,
        open_url=event.open_url,
        execute=event.execute,
        content_image=event.content_image,
        sender=sender,
        kind=event.kind,
    )
    append_feed(root, event)

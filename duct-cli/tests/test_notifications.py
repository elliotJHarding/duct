"""Tests for duct.notifications."""

from __future__ import annotations

import json
from pathlib import Path

from duct.models import Action, SessionInfo, TicketOverview
from duct.notifications import (
    NotificationEvent,
    NotificationTracker,
    append_feed,
    fire_event,
    orchestrator_event,
)


def _session(session_id: str, status: str, *, pid: int | None = 1, ticket="ABC-1",
             mode="default", topic="topic") -> SessionInfo:
    return SessionInfo(
        session_id=session_id,
        pid=pid,
        cwd="/tmp",
        ticket_key=ticket,
        status=status,
        mode=mode,
        topic=topic,
        started_at="",
        last_activity="",
    )


def _confirm(tracker, working, after, **kwargs):
    """Drive a confirmed ``working -> terminal`` transition through the tracker.

    The tracker debounces the transition over one extra tick (absorbing
    transient pane-text "working" misses), so a real transition needs: a
    working seed, an arming tick, then a confirming tick. Returns the events
    from the confirming tick.
    """
    tracker.diff_sessions(working)        # seed previous statuses
    tracker.diff_sessions(after, **kwargs)  # arm the one-tick confirmation
    return tracker.diff_sessions(after, **kwargs)  # confirm -> fires


def _overview(
    key: str, *, pending: int = 0, actions: list[Action] | None = None
) -> TicketOverview:
    if actions is None:
        actions = [
            Action(id=str(i), type="prompt", description="d", status="pending")
            for i in range(pending)
        ]
    return TicketOverview(
        key=key,
        summary="s",
        status="In Progress",
        category="",
        priority="",
        path=Path("/tmp"),
        artifacts=[],
        pending_actions=actions,
    )


# --- session transitions ---

def test_seed_fires_nothing() -> None:
    tracker = NotificationTracker()
    assert tracker.diff_sessions([_session("s", "done")]) == []


def test_working_to_done_fires_once() -> None:
    tracker = NotificationTracker(jira_domain="ex.atlassian.net")
    tracker.diff_sessions([_session("s", "working")])  # seed

    # Leaving working only arms a one-tick confirmation; nothing fires yet.
    assert tracker.diff_sessions([_session("s", "done")]) == []

    # Still done on the next tick -> confirmed, fires exactly once.
    events = tracker.diff_sessions([_session("s", "done")])
    assert len(events) == 1
    assert events[0].kind == "done"
    assert events[0].title == "ABC-1: done"
    # Session events focus the terminal on click (execute), not a URL.
    assert events[0].open_url is None

    # No re-fire while it stays done.
    assert tracker.diff_sessions([_session("s", "done")]) == []


def test_working_to_done_flicker_is_absorbed() -> None:
    tracker = NotificationTracker()
    tracker.diff_sessions([_session("s", "working")])  # seed

    # A single-tick dip to done (a pane-text "working" miss) must not fire...
    assert tracker.diff_sessions([_session("s", "done")]) == []
    # ...because the next tick reads working again -> discarded as flicker.
    assert tracker.diff_sessions([_session("s", "working")]) == []

    # A genuinely sustained done then fires exactly once (arm, then confirm).
    assert tracker.diff_sessions([_session("s", "done")]) == []
    events = tracker.diff_sessions([_session("s", "done")])
    assert len(events) == 1
    assert events[0].kind == "done"


def test_session_click_runs_jump_when_duct_bin_known() -> None:
    tracker = NotificationTracker(duct_bin="/usr/local/bin/duct")
    events = _confirm(tracker, [_session("s", "working")], [_session("s", "waiting")])
    assert events[0].execute == "/usr/local/bin/duct session jump s"


def test_label_falls_back_to_cwd_then_id() -> None:
    tracker = NotificationTracker()
    # No ticket_key, cwd present -> directory name identifies the session.
    events = _confirm(
        tracker,
        [_session("sess123", "working", ticket=None)],
        [_session("sess123", "waiting", ticket=None)],
    )
    assert events[0].title == "tmp: waiting"  # Path("/tmp").name


def test_working_to_waiting_fires() -> None:
    tracker = NotificationTracker()
    events = _confirm(tracker, [_session("s", "working")], [_session("s", "waiting")])
    assert len(events) == 1
    assert events[0].kind == "waiting"
    assert events[0].group == "session-waiting:s"


def test_plan_mode_done_has_subtitle() -> None:
    tracker = NotificationTracker()
    events = _confirm(
        tracker,
        [_session("s", "working", mode="plan")],
        [_session("s", "ready", mode="plan")],
    )
    assert events[0].subtitle == "Plan ready for review"


def test_suppress_pid_skips_session() -> None:
    tracker = NotificationTracker()
    events = _confirm(
        tracker,
        [_session("s", "working", pid=4242)],
        [_session("s", "done", pid=4242)],
        suppress_pids={4242},
    )
    assert events == []


def test_suppress_pids_only_skips_matching_session() -> None:
    tracker = NotificationTracker()
    events = _confirm(
        tracker,
        [
            _session("a", "working", pid=1, ticket="FOC-1"),
            _session("b", "working", pid=2, ticket="BG-2"),
        ],
        [
            _session("a", "done", pid=1, ticket="FOC-1"),
            _session("b", "done", pid=2, ticket="BG-2"),
        ],
        suppress_pids={1},
    )
    # Only the focused/docked pid (1) is skipped; the backgrounded one fires.
    assert [e.title for e in events] == ["BG-2: done"]


def test_non_working_transition_does_not_fire() -> None:
    tracker = NotificationTracker()
    # waiting -> done is not a working->terminal transition; it never arms,
    # so even across a confirming tick it stays silent.
    events = _confirm(tracker, [_session("s", "waiting")], [_session("s", "done")])
    assert events == []


def test_unmatched_session_uses_cwd_label_and_no_url() -> None:
    tracker = NotificationTracker(jira_domain="ex.atlassian.net")
    events = _confirm(
        tracker,
        [_session("s", "working", ticket=None)],
        [_session("s", "done", ticket=None)],
    )
    assert events[0].title == "tmp: done"  # Path("/tmp").name
    assert events[0].open_url is None


# --- pending actions ---

def test_actions_seed_fires_nothing() -> None:
    tracker = NotificationTracker()
    assert tracker.diff_actions([_overview("ABC-1", pending=2)]) == []


def test_action_appears_fires_once_per_action_then_silent_then_rearms() -> None:
    tracker = NotificationTracker(jira_domain="ex.atlassian.net")
    tracker.diff_actions([])  # seed empty

    events = tracker.diff_actions([_overview("ABC-1", pending=2)])
    assert len(events) == 2  # one notification per pending action
    assert {e.title for e in events} == {"ABC-1 · Prompt"}
    assert {e.body for e in events} == {"d"}
    assert {e.group for e in events} == {"pending-action:ABC-1:0", "pending-action:ABC-1:1"}
    assert {e.open_url for e in events} == {"https://ex.atlassian.net/browse/ABC-1"}

    # Same actions still pending -> silent (edge-triggered per action id).
    assert tracker.diff_actions([_overview("ABC-1", pending=2)]) == []

    # Cleared, then one reappears -> re-arms and fires again, once.
    assert tracker.diff_actions([_overview("ABC-1", pending=0)]) == []
    events = tracker.diff_actions([_overview("ABC-1", pending=1)])
    assert len(events) == 1
    assert events[0].title == "ABC-1 · Prompt"


def test_action_notification_shows_type_label_and_summary() -> None:
    tracker = NotificationTracker(jira_domain="ex.atlassian.net")
    tracker.diff_actions([])  # seed empty

    comment = Action(
        id="x1",
        type="jira_comment",
        description="Chase reporter for missing AC",
        status="pending",
    )
    events = tracker.diff_actions([_overview("ABC-1", actions=[comment])])
    assert len(events) == 1
    assert events[0].title == "ABC-1 · Jira comment"
    assert events[0].body == "Chase reporter for missing AC"

    # A newly-added action on an already-alerted ticket still fires (per-action).
    extra = Action(id="x2", type="improve_workflow", description="Codify CI rule", status="pending")
    events = tracker.diff_actions([_overview("ABC-1", actions=[comment, extra])])
    assert len(events) == 1
    assert events[0].title == "ABC-1 · Workflow improvement"
    assert events[0].body == "Codify CI rule"


# --- orchestrator + feed ---

def test_orchestrator_event_pluralisation() -> None:
    assert orchestrator_event(0).body == "No new actions proposed"
    assert orchestrator_event(1).body == "1 action proposed"
    assert orchestrator_event(3).body == "3 actions proposed"


def test_append_feed_writes_jsonl(tmp_path: Path) -> None:
    tracker = NotificationTracker()
    event = _confirm(tracker, [_session("s", "working")], [_session("s", "done")])[0]

    append_feed(tmp_path, event, at="2026-05-30T10:00:00+00:00")
    append_feed(tmp_path, event, at="2026-05-30T10:01:00+00:00")

    lines = (tmp_path / ".duct" / "notifications.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record["kind"] == "done"
    assert record["at"] == "2026-05-30T10:00:00+00:00"
    assert record["title"] == "ABC-1: done"


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def notify(self, title: str, body: str, **kwargs) -> bool:
        self.calls.append((title, body, kwargs))
        return True


def test_fire_event_notifies_and_records(tmp_path: Path) -> None:
    event = NotificationEvent(
        kind="orchestrator-action",
        title="t",
        body="b",
        group="g",
        open_url="https://x/browse/ABC-1",
    )
    notifier = _RecordingNotifier()

    fire_event(notifier, tmp_path, event, sender="com.example")

    # Fired through the notifier, passing every event field plus the sender.
    title, body, kwargs = notifier.calls[0]
    assert (title, body) == ("t", "b")
    assert kwargs["group"] == "g"
    assert kwargs["open_url"] == "https://x/browse/ABC-1"
    assert kwargs["sender"] == "com.example"

    # And recorded to the feed.
    line = (tmp_path / ".duct" / "notifications.jsonl").read_text().strip()
    assert json.loads(line)["kind"] == "orchestrator-action"

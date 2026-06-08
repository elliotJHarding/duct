"""duct notify — the orchestrator's actuating surface for notifications.

A deliberate push: it fires through the same mechanism the daemon uses
(``MacNotifier`` + the agent-readable feed), so the orchestrator reuses the
daemon's notification path rather than writing feed files itself.

Unlike the daemon's auto-diffed events, a ``duct notify`` call is an explicit
choice to surface something, so it bypasses the ``event_kinds`` mute-list (that
filter is for silencing *categories* of automatic notifications). It still
respects ``notifications.enabled`` for the OS popup, and always records to the
feed so the TUI has a visible entry either way.
"""

from __future__ import annotations

import uuid

import click

from duct.cli.output import error, success
from duct.cli.resolve import require_setup
from duct.config import load_config
from duct.notifications import NotificationEvent, fire_event
from duct.notifier import MacNotifier

NOTIFY_KIND = "orchestrator-action"


@click.command()
@click.option("--title", required=True, help="Notification title.")
@click.option("--body", required=True, help="Notification body.")
@click.option("--ticket", default=None, help="Ticket key this relates to (sets the Jira open URL).")
@click.option("--url", "open_url", default=None, help="Click-to-open URL (overrides ticket URL).")
@click.pass_context
def notify(
    ctx: click.Context,
    title: str,
    body: str,
    ticket: str | None,
    open_url: str | None,
) -> None:
    """Fire a notification to the user, using the daemon's notification mechanism.

    Intended for the orchestrator to keep the user in the loop when it takes an
    autonomous action. Fires a macOS notification (when notifications are
    enabled) and records the event to the workspace notification feed.
    """
    root = require_setup(ctx)
    cfg = load_config(root)

    if open_url is None and ticket and cfg.jira_domain:
        open_url = f"https://{cfg.jira_domain}/browse/{ticket}"

    # Each deliberate push is a distinct event, so the group is unique per call:
    # terminal-notifier removes any prior notification sharing a -group, and the
    # orchestrator fires several pushes per run. A per-call uuid stops them
    # evicting each other. The ticket/workspace prefix is purely for feed
    # readability — uniqueness comes from the uuid.
    event = NotificationEvent(
        kind=NOTIFY_KIND,
        title=title,
        body=body,
        group=f"{NOTIFY_KIND}:{ticket or 'workspace'}:{uuid.uuid4().hex[:8]}",
        open_url=open_url,
    )

    notifier = MacNotifier(enabled=cfg.notifications.enabled)
    try:
        fire_event(notifier, root, event, sender=cfg.notifications.sender_bundle_id or None)
    except Exception as exc:  # noqa: BLE001 — never let a notification failure break a run
        error(f"Failed to notify: {type(exc).__name__}: {exc}")
        ctx.exit(1)
        return

    success(f"Notified: {title}")

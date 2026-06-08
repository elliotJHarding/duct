"""Activity provider protocol and shared helpers."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from duct.markdown import TICKET_KEY_PATTERN
from duct.models import ActivityEvent

if TYPE_CHECKING:
    from duct.config import WorkspaceConfig


@runtime_checkable
class ActivityProvider(Protocol):
    """A source of activity events.

    Providers are pure fetchers — they don't persist, dedupe, or know about
    the store. The coordinator is responsible for those concerns.
    """

    name: str

    def fetch(
        self,
        since: datetime,
        until: datetime,
        cfg: "WorkspaceConfig",
    ) -> Iterator[ActivityEvent]:
        """Yield events in `[since, until)` for this source."""
        ...


@dataclass(frozen=True)
class ProviderResult:
    """Outcome of a single provider's fetch pass, recorded by the coordinator."""

    name: str
    events_fetched: int
    events_new: int  # after dedup against existing JSONL
    duration_seconds: float
    errors: list[str] = field(default_factory=list)


def infer_ticket_key(text: str, known_keys: set[str]) -> str | None:
    """Scan *text* for a ticket key (``[A-Z]+-\\d+``).

    Prefers the first match that appears in *known_keys* (the workspace's
    tickets) so a commit mentioning both a workspace ticket and an
    unrelated reference resolves to the workspace one. Falls back to the
    first regex match otherwise — the user wanted events linked to any
    ticket when possible, not only workspace-local ones.
    """
    if not text:
        return None
    matches = TICKET_KEY_PATTERN.findall(text)
    if not matches:
        return None
    for m in matches:
        if m in known_keys:
            return m
    return matches[0]

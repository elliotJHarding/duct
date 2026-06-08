"""Claude Code transcript activity.

Walks ``~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`` files whose
activity window intersects ``[since, until)`` and emits one event per
transcript summarising the session: topic, duration, and — when the cwd
references a ticket key — the ticket link.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from duct.activity.base import infer_ticket_key
from duct.config import WorkspaceConfig
from duct.models import ActivityEvent
from duct.session import _decode_project_path, extract_transcript_info
from duct.workspace import enumerate_ticket_dirs


class ClaudeActivityProvider:
    name = "claude"

    def __init__(self, claude_dir: Path | None = None):
        self._claude_dir = claude_dir or Path.home() / ".claude"

    def fetch(
        self,
        since: datetime,
        until: datetime,
        cfg: WorkspaceConfig,
    ) -> Iterator[ActivityEvent]:
        projects_dir = self._claude_dir / "projects"
        if not projects_dir.is_dir():
            return

        known_keys = {key for key, _ in enumerate_ticket_dirs(cfg.root)}
        since_epoch = since.timestamp()
        until_epoch = until.timestamp()

        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            cwd = _decode_project_path(project_dir.name)
            # The decoded cwd collapses hyphens in ticket keys (dir names
            # encode every ``/`` as ``-``). Keep the raw encoded form too
            # so ticket inference can still see ``ERSC-1278`` patterns.
            encoded_name = project_dir.name
            for transcript in project_dir.glob("*.jsonl"):
                try:
                    mtime = transcript.stat().st_mtime
                except OSError:
                    continue
                if mtime < since_epoch:
                    continue
                info = extract_transcript_info(transcript)
                started_at = info.get("started_at", "")
                last_activity = info.get("last_activity", "")
                # Prefer the transcript's own timestamps; fall back to mtime
                # when the JSONL is malformed.
                start_dt = _parse(started_at) or datetime.fromtimestamp(
                    mtime, tz=timezone.utc
                )
                end_dt = _parse(last_activity) or start_dt
                if end_dt < since or start_dt >= until:
                    continue
                duration = max(0, int((end_dt - start_dt).total_seconds()))
                topic = (info.get("topic", "") or "").strip()
                ticket = infer_ticket_key(f"{encoded_name} {cwd} {topic}", known_keys)
                session_id = transcript.stem
                # Always store at the session's actual start time — clamping
                # to ``since`` would move the event into a different day file
                # on re-runs with a wider window, escaping dedup.
                yield ActivityEvent(
                    event_id=f"claude:{session_id}",
                    timestamp=_iso(start_dt),
                    source=self.name,
                    event_type="session",
                    actor="self",
                    summary=f"Claude session: {topic[:140] or '(no topic)'}",
                    ticket_key=ticket,
                    url=None,
                    duration_seconds=duration,
                    detail={
                        "session_id": session_id,
                        "cwd": cwd,
                        "started_at": _iso(start_dt),
                        "last_activity": _iso(end_dt),
                        "topic": topic,
                    },
                )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

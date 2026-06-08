"""Jira activity provider — changelog transitions + comments authored by self."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import datetime, timezone

import httpx

from duct.activity.base import infer_ticket_key
from duct.config import WorkspaceConfig
from duct.exceptions import AuthError, SyncError
from duct.models import ActivityEvent
from duct.sync.adf import adf_to_markdown
from duct.workspace import enumerate_ticket_dirs


class JiraActivityProvider:
    """Fetch Jira issue changelog + comment events authored by the current user."""

    name = "jira"

    def __init__(self, domain: str, email: str, token: str):
        if not domain:
            raise AuthError("Jira domain is not configured")
        if not email:
            raise AuthError("JIRA_EMAIL is not set")
        if not token:
            raise AuthError("JIRA_TOKEN is not set")

        self._domain = domain
        self._email = email
        self._base_url = f"https://{domain}/rest/api/3"
        credentials = base64.b64encode(f"{email}:{token}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
        }
        self._account_id: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(
        self,
        since: datetime,
        until: datetime,
        cfg: WorkspaceConfig,
    ) -> Iterator[ActivityEvent]:
        known_keys = {key for key, _ in enumerate_ticket_dirs(cfg.root)}
        jql = (
            "(assignee = currentUser() OR comment ~ currentUser() "
            "OR worklogAuthor = currentUser()) "
            f"AND updated >= \"{_jql_time(since)}\" ORDER BY updated DESC"
        )
        issues = self._search(jql)
        for issue in issues:
            key = issue.get("key", "")
            for event in self._events_for_issue(issue, since, until, known_keys):
                # Provider may receive tickets outside the workspace; keep
                # the ticket_key present (it's the Jira-issue key) even if
                # it isn't in the local workspace.
                yield event
            _ = key  # silence unused

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def _resolve_account_id(self) -> str | None:
        """Resolve the authenticated user's accountId via /myself (cached)."""
        if self._account_id is not None:
            return self._account_id or None
        try:
            response = httpx.get(
                f"{self._base_url}/myself", headers=self._headers, timeout=10
            )
            if response.status_code == 200:
                self._account_id = response.json().get("accountId", "") or ""
            else:
                self._account_id = ""
        except httpx.HTTPError:
            self._account_id = ""
        return self._account_id or None

    def _search(self, jql: str) -> list[dict]:
        issues: list[dict] = []
        start_at = 0
        while True:
            response = httpx.get(
                f"{self._base_url}/search/jql",
                headers=self._headers,
                params={
                    "jql": jql,
                    "fields": "summary,comment",
                    "expand": "changelog",
                    "startAt": start_at,
                    "maxResults": 50,
                },
                timeout=30,
            )
            if response.status_code in (401, 403):
                raise AuthError(f"Jira authentication failed ({response.status_code})")
            if response.status_code != 200:
                raise SyncError(
                    f"Jira search failed with status {response.status_code}: "
                    f"{response.text[:200]}"
                )
            data = response.json()
            batch = data.get("issues", [])
            issues.extend(batch)
            total = data.get("total", 0)
            start_at += len(batch)
            if start_at >= total or not batch:
                break

            # When the search endpoint didn't include a changelog in the
            # result (older Jira Cloud instances), fall through to a
            # per-issue fetch below.
        return [self._ensure_changelog(issue) for issue in issues]

    def _ensure_changelog(self, issue: dict) -> dict:
        if issue.get("changelog"):
            return issue
        key = issue.get("key", "")
        if not key:
            return issue
        try:
            response = httpx.get(
                f"{self._base_url}/issue/{key}",
                headers=self._headers,
                params={"expand": "changelog", "fields": "summary,comment"},
                timeout=15,
            )
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            pass
        return issue

    # ------------------------------------------------------------------
    # Event extraction
    # ------------------------------------------------------------------

    def _events_for_issue(
        self,
        issue: dict,
        since: datetime,
        until: datetime,
        known_keys: set[str],
    ) -> Iterator[ActivityEvent]:
        key = issue.get("key", "")
        summary = issue.get("fields", {}).get("summary", "")
        url = f"https://{self._domain}/browse/{key}"
        ticket_key = infer_ticket_key(key, known_keys) or key

        yield from self._changelog_events(issue, key, summary, url, ticket_key, since, until)
        yield from self._comment_events(issue, key, summary, url, ticket_key, since, until)

    def _is_self(self, author: dict | None) -> bool:
        if not isinstance(author, dict):
            return False
        if author.get("emailAddress") == self._email:
            return True
        account_id = self._resolve_account_id()
        if account_id and author.get("accountId") == account_id:
            return True
        return False

    def _changelog_events(
        self,
        issue: dict,
        key: str,
        summary: str,
        url: str,
        ticket_key: str,
        since: datetime,
        until: datetime,
    ) -> Iterator[ActivityEvent]:
        changelog = issue.get("changelog", {}) or {}
        for history in changelog.get("histories", []) or []:
            if not self._is_self(history.get("author")):
                continue
            created = history.get("created", "")
            if not _in_window(created, since, until):
                continue
            items = history.get("items", []) or []
            for item in items:
                field = item.get("field", "")
                from_s = item.get("fromString") or item.get("from") or ""
                to_s = item.get("toString") or item.get("to") or ""
                if field.lower() == "status":
                    event_type = "status_change"
                    text = f"{key}: {from_s} → {to_s}"
                elif field.lower() == "assignee":
                    event_type = "assignee_change"
                    text = f"{key}: assignee {from_s or '∅'} → {to_s or '∅'}"
                else:
                    event_type = "field_change"
                    text = f"{key}: {field} {from_s or '∅'} → {to_s or '∅'}"
                yield ActivityEvent(
                    event_id=f"jira:{key}:history:{history.get('id', '')}:{field}",
                    timestamp=_normalise_ts(created),
                    source=self.name,
                    event_type=event_type,
                    actor=_actor_of(history.get("author")),
                    summary=text,
                    ticket_key=ticket_key,
                    url=url,
                    detail={"issue_summary": summary, "field": field},
                )

    def _comment_events(
        self,
        issue: dict,
        key: str,
        summary: str,
        url: str,
        ticket_key: str,
        since: datetime,
        until: datetime,
    ) -> Iterator[ActivityEvent]:
        comment_field = issue.get("fields", {}).get("comment", {}) or {}
        for comment in comment_field.get("comments", []) or []:
            if not self._is_self(comment.get("author")):
                continue
            created = comment.get("created", "")
            if not _in_window(created, since, until):
                continue
            body_adf = comment.get("body")
            body = adf_to_markdown(body_adf) if body_adf else ""
            one_line = (body or "").strip().splitlines()[0] if body else ""
            yield ActivityEvent(
                event_id=f"jira:{key}:comment:{comment.get('id', '')}",
                timestamp=_normalise_ts(created),
                source=self.name,
                event_type="comment",
                actor=_actor_of(comment.get("author")),
                summary=f"{key}: commented — {one_line[:140]}",
                ticket_key=ticket_key,
                url=url,
                detail={"issue_summary": summary, "body": body},
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _jql_time(dt: datetime) -> str:
    """Format *dt* as a JQL-compatible ``yyyy-MM-dd HH:mm`` string in UTC."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _normalise_ts(ts: str) -> str:
    """Normalise a Jira timestamp (e.g. ``2026-04-20T09:30:00.000+0100``) to UTC ISO Z."""
    if not ts:
        return ""
    try:
        cleaned = ts
        if len(cleaned) >= 5 and (cleaned[-5] in "+-") and cleaned[-3] != ":":
            # "+0100" -> "+01:00" for fromisoformat.
            cleaned = cleaned[:-2] + ":" + cleaned[-2:]
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ts


def _in_window(ts: str, since: datetime, until: datetime) -> bool:
    iso = _normalise_ts(ts)
    if not iso:
        return False
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return since <= dt < until


def _actor_of(author: dict | None) -> str:
    if not isinstance(author, dict):
        return "unknown"
    return (
        author.get("emailAddress")
        or author.get("displayName")
        or author.get("accountId")
        or "unknown"
    )

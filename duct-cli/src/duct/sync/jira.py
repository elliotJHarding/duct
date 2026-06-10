"""Jira REST API sync source for duct."""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from duct import paths
from duct.config import SandboxConfig
from duct.exceptions import AuthError, SyncError
from duct.markdown import atomic_write, generate_frontmatter
from duct.models import Comment, SyncResult, Ticket
from duct.sandbox import write_settings
from duct.sync.adf import adf_to_markdown
from duct.workspace import (
    archive_ticket,
    ensure_epic_link,
    ensure_ticket_dir,
    enumerate_ticket_dirs,
)

_SEARCH_FIELDS = (
    "summary,status,priority,issuetype,assignee,project,parent,"
    "customfield_10014,customfield_10020,customfield_11704,fixVersions,components,labels,"
    "comment,description"
)

_STATUS_CATEGORIES: dict[str, str] = {
    "IN PROGRESS": "Active Development",
    "READY TO DEPLOY": "Awaiting Action",
    "DEPLOYED": "Awaiting Action",
    "AWAITING EXTERNAL DEVELOPMENT": "Awaiting Action",
    "REQUESTED": "Awaiting Action",
    "TESTING": "In Test",
    "CUSTOMER TESTING": "In Test",
    "READY TO TEST": "In Test",
    "PENDING": "Pre-Development",
    "TO DO": "Pre-Development",
    "ANALYSIS STARTED": "Pre-Development",
    "READY FOR DEVELOPMENT": "Pre-Development",
    "DONE": "Done",
    "CLOSED": "Done",
    "RESOLVED": "Done",
}

_DEFAULT_CATEGORY = "Pre-Development"



def _status_category(status: str) -> str:
    """Map a Jira status name to a workflow category."""
    return _STATUS_CATEGORIES.get(status.upper(), _DEFAULT_CATEGORY)


def _identity_cache_path(root: Path) -> Path:
    return paths.jira_identity_cache(root)


def _write_identity_cache(root: Path, account_id: str) -> None:
    """Persist the authenticated user's Jira accountId for the API layer."""
    path = _identity_cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"jira_account_id": account_id}))


def read_identity_cache(root: Path) -> str:
    """Return the cached Jira accountId, or "" if not yet written."""
    path = _identity_cache_path(root)
    if not path.exists():
        return ""
    try:
        return json.loads(path.read_text()).get("jira_account_id", "") or ""
    except (OSError, ValueError):
        return ""


class JiraSync:
    """Sync source that fetches issues from Jira REST API v3."""

    name = "jira"

    def __init__(self, domain: str, email: str, token: str, jql: str, sandbox: SandboxConfig | None = None):
        if not domain:
            raise AuthError("Jira domain is not configured")
        if not email:
            raise AuthError("JIRA_EMAIL is not set")
        if not token:
            raise AuthError("JIRA_TOKEN is not set")

        self._domain = domain
        self._jql = jql
        self._sandbox = sandbox
        self._base_url = f"https://{domain}/rest/api/3"

        credentials = base64.b64encode(f"{email}:{token}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
        }

    def sync(self, root: Path) -> SyncResult:
        """Run a full Jira sync cycle."""
        start = time.time()
        errors: list[str] = []

        # Collect previously-known ticket keys for archive detection.
        previous_keys = {key for key, _ in enumerate_ticket_dirs(root)}

        try:
            issues = self._search_issues()
        except (SyncError, AuthError) as exc:
            return SyncResult(
                source=self.name,
                tickets_synced=0,
                duration_seconds=time.time() - start,
                errors=[str(exc)],
            )

        # Resolve and cache the current user's accountId so the overview
        # can flag tickets assigned to other people without recomparing
        # display names. Best-effort — a missing /myself response leaves
        # the cache untouched, and api.get_ticket_overviews then treats
        # every ticket as "mine" until the next successful sync.
        account_id = self._resolve_account_id()
        if account_id:
            _write_identity_cache(root, account_id)

        # First pass: build epic map (ticket_key -> epic_key).
        epic_map: dict[str, str] = {}
        epic_summaries: dict[str, str] = {}
        for issue in issues:
            key = issue["key"]
            fields = issue["fields"]
            epic_key = self._resolve_epic(key, fields)
            if epic_key:
                epic_map[key] = epic_key
            # Record summary for any issue that might be an epic parent.
            epic_summaries[key] = fields.get("summary", key)

        # Second pass: extract tickets, create dirs, write TICKET.md.
        current_keys: set[str] = set()
        synced = 0
        for issue in issues:
            key = issue["key"]
            current_keys.add(key)

            try:
                ticket = self._extract_ticket(issue, epic_map)
            except Exception as exc:
                errors.append(f"{key}: failed to extract ticket - {exc}")
                continue

            # Archive any ticket that reached a terminal status, even when the
            # configured JQL still returns it (e.g. a name-based "status != Done"
            # filter that lets Closed/Resolved through). Keeps the archive
            # decision consistent with _status_category regardless of the query.
            # key stays in current_keys (it *was* returned by the query) so the
            # stale branch below doesn't re-process the now-archived ticket.
            if ticket.category == "Done":
                archive_ticket(root, key)
                continue

            try:
                transitions = self._fetch_transitions(key)
                ticket = Ticket(
                    key=ticket.key,
                    summary=ticket.summary,
                    status=ticket.status,
                    category=ticket.category,
                    priority=ticket.priority,
                    issue_type=ticket.issue_type,
                    assignee=ticket.assignee,
                    url=ticket.url,
                    assignee_account_id=ticket.assignee_account_id,
                    description=ticket.description,
                    epic_key=ticket.epic_key,
                    sprint=ticket.sprint,
                    fix_versions=ticket.fix_versions,
                    components=ticket.components,
                    labels=ticket.labels,
                    transitions=transitions,
                    comments=ticket.comments,
                    linked_issues=ticket.linked_issues,
                    subtasks=ticket.subtasks,
                )
            except Exception as exc:
                errors.append(f"{key}: failed to fetch transitions - {exc}")

            epic_key = epic_map.get(key)
            epic_summary = epic_summaries.get(epic_key, epic_key) if epic_key else None

            try:
                ticket_dir = ensure_ticket_dir(root, key, ticket.summary)
                if self._sandbox and self._sandbox.enabled:
                    write_settings(ticket_dir, self._sandbox)
                if epic_key:
                    ensure_epic_link(root, ticket_dir, epic_key, epic_summary)
                self._write_ticket_md(ticket, ticket_dir)
                synced += 1
            except Exception as exc:
                errors.append(f"{key}: failed to write ticket - {exc}")

        # Refresh tickets that disappeared from query results (e.g.
        # reassigned for testing). Archive those in a terminal status;
        # re-sync the rest so on-disk status stays current.
        stale_keys = previous_keys - current_keys
        for key in stale_keys:
            try:
                issue = self._fetch_issue(key)
                if not issue:
                    continue
                status_name = issue.get("fields", {}).get("status", {}).get("name", "")
                if _status_category(status_name) == "Done":
                    archive_ticket(root, key)
                    continue
                ticket = self._extract_ticket(issue, epic_map)
                ticket_dir = ensure_ticket_dir(root, key, ticket.summary)
                self._write_ticket_md(ticket, ticket_dir)
                synced += 1
            except Exception as exc:
                errors.append(f"{key}: failed to refresh stale ticket - {exc}")

        return SyncResult(
            source=self.name,
            tickets_synced=synced,
            duration_seconds=time.time() - start,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # API interaction
    # ------------------------------------------------------------------

    def _search_issues(self) -> list[dict]:
        """Fetch all matching issues via paginated JQL search."""
        issues: list[dict] = []
        start_at = 0
        max_results = 50

        while True:
            url = f"{self._base_url}/search/jql"
            params = {
                "jql": self._jql,
                "fields": _SEARCH_FIELDS,
                "startAt": start_at,
                "maxResults": max_results,
            }

            response = httpx.get(url, headers=self._headers, params=params, timeout=30)

            if response.status_code == 401:
                raise AuthError("Jira authentication failed (401)")
            if response.status_code == 403:
                raise AuthError("Jira access forbidden (403)")
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

        return issues

    def _fetch_transitions(self, key: str) -> list[str]:
        """Fetch available workflow transitions for an issue."""
        url = f"{self._base_url}/issue/{key}/transitions"
        response = httpx.get(url, headers=self._headers, timeout=15)

        if response.status_code != 200:
            return []

        data = response.json()
        return [t["name"] for t in data.get("transitions", [])]

    def _fetch_issue(self, key: str) -> dict | None:
        """Fetch a single issue with the standard field set, or None on failure."""
        url = f"{self._base_url}/issue/{key}"
        params = {"fields": _SEARCH_FIELDS}
        response = httpx.get(url, headers=self._headers, params=params, timeout=15)
        if response.status_code != 200:
            return None
        return response.json()

    def post_comment(self, key: str, body: str) -> None:
        """Post a plain-text comment to a Jira issue.

        Wraps *body* in minimal ADF (one paragraph per line) and POSTs to
        the v3 comment endpoint.  Raises SyncError on failure, AuthError on
        401/403.
        """
        paragraphs = [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": line}],
            }
            for line in body.splitlines()
            if line.strip()
        ]
        if not paragraphs:
            return

        adf_body = {"type": "doc", "version": 1, "content": paragraphs}

        url = f"{self._base_url}/issue/{key}/comment"
        response = httpx.post(
            url,
            headers={**self._headers, "Content-Type": "application/json"},
            json={"body": adf_body},
            timeout=15,
        )

        if response.status_code == 401:
            raise AuthError("Jira authentication failed (401)")
        if response.status_code == 403:
            raise AuthError("Jira access forbidden (403)")
        if response.status_code not in (200, 201):
            raise SyncError(
                f"Failed to post comment to {key} "
                f"(status {response.status_code}): {response.text[:200]}"
            )

    def _resolve_account_id(self) -> str:
        """Fetch the authenticated user's accountId via /myself.

        Returns "" on any failure — the caller should treat empty as
        "unknown" and skip the identity cache update.
        """
        url = f"{self._base_url}/myself"
        try:
            response = httpx.get(url, headers=self._headers, timeout=10)
        except httpx.HTTPError:
            return ""
        if response.status_code != 200:
            return ""
        try:
            return response.json().get("accountId", "") or ""
        except ValueError:
            return ""

    def _fetch_status(self, key: str) -> str | None:
        """Fetch the current status name for an issue, or None on failure."""
        issue = self._fetch_issue(key)
        if not issue:
            return None
        try:
            return issue["fields"]["status"]["name"]
        except (KeyError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Field extraction
    # ------------------------------------------------------------------

    def _resolve_epic(self, key: str, fields: dict) -> str | None:
        """Determine the epic key for an issue, if any."""
        # Priority 1: customfield_10014 (legacy epic link).
        epic_link = fields.get("customfield_10014")
        if epic_link and epic_link != key:
            return epic_link

        # Priority 2: parent whose type is "Epic".
        parent = fields.get("parent")
        if parent and isinstance(parent, dict):
            parent_type = (
                parent.get("fields", {}).get("issuetype", {}).get("name", "")
            )
            parent_key = parent.get("key", "")
            if parent_type == "Epic" and parent_key != key:
                return parent_key

        return None

    def _extract_ticket(self, issue: dict, epic_map: dict[str, str]) -> Ticket:
        """Parse a Jira API issue dict into a Ticket dataclass."""
        key = issue["key"]
        fields = issue["fields"]

        summary = fields.get("summary", "")
        status = fields.get("status", {}).get("name", "Unknown")
        priority = fields.get("priority", {}).get("name", "None")
        issue_type = fields.get("issuetype", {}).get("name", "Task")

        assignee_field = fields.get("assignee")
        if assignee_field and isinstance(assignee_field, dict):
            assignee = assignee_field.get("displayName", "Unassigned")
            assignee_account_id = assignee_field.get("accountId", "") or ""
        else:
            assignee = "Unassigned"
            assignee_account_id = ""

        fix_versions = [v["name"] for v in (fields.get("fixVersions") or [])]
        components = [c["name"] for c in (fields.get("components") or [])]
        labels = fields.get("labels", [])

        # Sprint: take the last (most recent/active) sprint.
        sprint_field = fields.get("customfield_10020") or []
        sprint_name = None
        if sprint_field and isinstance(sprint_field, list) and sprint_field:
            last_sprint = sprint_field[-1]
            if isinstance(last_sprint, dict):
                sprint_name = last_sprint.get("name")
            elif isinstance(last_sprint, str):
                sprint_name = last_sprint

        # Customer Name (PS project single-select option; only the value is human-readable).
        customer_field = fields.get("customfield_11704")
        customer_name = customer_field.get("value") if isinstance(customer_field, dict) else None

        # Description (ADF -> markdown).
        description_adf = fields.get("description")
        description = adf_to_markdown(description_adf) if description_adf else ""

        # Comments (last 20, newest first).
        comment_field = fields.get("comment", {})
        raw_comments = comment_field.get("comments", []) if isinstance(comment_field, dict) else []
        comments: list[Comment] = []
        for raw in raw_comments:
            author = raw.get("author", {}).get("displayName", "Unknown")
            created = raw.get("created", "")
            body_adf = raw.get("body")
            body = adf_to_markdown(body_adf) if body_adf else ""
            comments.append(Comment(author=author, created=created, body=body))
        # Sort newest first, keep last 20.
        comments.sort(key=lambda c: c.created, reverse=True)
        comments = comments[:20]

        epic_key = epic_map.get(key)

        return Ticket(
            key=key,
            summary=summary,
            status=status,
            category=_status_category(status),
            priority=priority,
            issue_type=issue_type,
            assignee=assignee,
            url=f"https://{self._domain}/browse/{key}",
            assignee_account_id=assignee_account_id,
            description=description,
            epic_key=epic_key,
            sprint=sprint_name,
            customer_name=customer_name,
            fix_versions=fix_versions,
            components=components,
            labels=labels,
            transitions=[],
            comments=comments,
        )

    # ------------------------------------------------------------------
    # File output
    # ------------------------------------------------------------------

    def _write_ticket_md(self, ticket: Ticket, ticket_dir: Path) -> None:
        """Generate and write TICKET.md for a single ticket."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts: list[str] = []

        # Frontmatter.
        parts.append(generate_frontmatter(source="sync", synced_at=now))
        parts.append("")

        # Title.
        parts.append(f"# {ticket.key}: {ticket.summary}")
        parts.append("")

        # Metadata table.
        fix_version_str = ", ".join(ticket.fix_versions) if ticket.fix_versions else "\u2014"
        components_str = ", ".join(ticket.components) if ticket.components else "\u2014"
        labels_str = ", ".join(ticket.labels) if ticket.labels else "\u2014"

        parts.append("| Field | Value |")
        parts.append("|-------|-------|")
        parts.append(f"| Status | {ticket.status} |")
        parts.append(f"| Category | {ticket.category} |")
        parts.append(f"| Priority | {ticket.priority} |")
        parts.append(f"| Type | {ticket.issue_type} |")
        if ticket.customer_name:
            parts.append(f"| Customer Name | {ticket.customer_name} |")
        parts.append(f"| Assignee | {ticket.assignee} |")
        if ticket.assignee_account_id:
            parts.append(f"| Assignee ID | {ticket.assignee_account_id} |")
        epic_str = ticket.epic_key or "\u2014"
        parts.append(f"| Epic | {epic_str} |")
        parts.append(f"| Fix Version | {fix_version_str} |")
        parts.append(f"| Components | {components_str} |")
        parts.append(f"| Labels | {labels_str} |")
        parts.append("")
        parts.append(f"[View in Jira](https://{self._domain}/browse/{ticket.key})")
        parts.append("")

        # Description section (omit if empty).
        if ticket.description:
            parts.append("## Description")
            parts.append("")
            parts.append(ticket.description)
            parts.append("")

        # Transitions section (omit if empty).
        if ticket.transitions:
            parts.append("## Available Transitions")
            parts.append("")
            for t in ticket.transitions:
                parts.append(f"- {t}")
            parts.append("")

        # Comments section (omit if empty).
        if ticket.comments:
            parts.append("## Comments")
            parts.append("")
            for comment in ticket.comments:
                parts.append(f"### {comment.author} ({comment.created})")
                parts.append("")
                parts.append(comment.body)
                parts.append("")

        content = "\n".join(parts)
        ticket_md = ticket_dir / "orchestrator" / "TICKET.md"
        atomic_write(ticket_md, content)

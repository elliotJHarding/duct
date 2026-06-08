"""GitHub activity provider — events authored by the authenticated user.

Uses the REST `/users/{self}/events` endpoint (GraphQL does not expose a
comparable cross-repo event stream). Limited to ~90 days / 300 events by
GitHub; the coordinator's "since last run" scheduling keeps us well inside
that window in practice.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import httpx

from duct.activity.base import infer_ticket_key
from duct.config import WorkspaceConfig
from duct.exceptions import AuthError, SyncError
from duct.models import ActivityEvent
from duct.workspace import enumerate_ticket_dirs

_BASE_URL = "https://api.github.com"
_MAX_PAGES = 10  # safety cap; GitHub returns ~100 events per page.


class GitHubActivityProvider:
    name = "github"

    def __init__(self, token: str, username: str | None = None):
        if not token:
            raise AuthError("GH_TOKEN is not set")
        self._token = token
        self._username = username
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch(
        self,
        since: datetime,
        until: datetime,
        cfg: WorkspaceConfig,
    ) -> Iterator[ActivityEvent]:
        user = self._username or self._resolve_username()
        if not user:
            raise SyncError("unable to resolve GitHub username")
        known_keys = {key for key, _ in enumerate_ticket_dirs(cfg.root)}
        for raw in self._iter_user_events(user, since):
            created = raw.get("created_at", "")
            if not _in_window(created, since, until):
                # Events are in reverse-chronological order, so as soon as
                # we drop below `since` we can stop paging.
                if _before(created, since):
                    return
                continue
            for event in self._translate(raw, user, known_keys):
                yield event

    # ------------------------------------------------------------------
    # Auth + paging
    # ------------------------------------------------------------------

    def _resolve_username(self) -> str | None:
        try:
            response = httpx.get(
                f"{_BASE_URL}/user", headers=self._headers, timeout=15
            )
            if response.status_code == 200:
                self._username = response.json().get("login")
                return self._username
        except httpx.HTTPError:
            return None
        return None

    def _iter_user_events(self, user: str, since: datetime) -> Iterator[dict]:
        for page in range(1, _MAX_PAGES + 1):
            response = httpx.get(
                f"{_BASE_URL}/users/{user}/events",
                headers=self._headers,
                params={"per_page": 100, "page": page},
                timeout=30,
            )
            if response.status_code in (401, 403):
                raise AuthError(f"GitHub authentication failed ({response.status_code})")
            if response.status_code != 200:
                raise SyncError(
                    f"GitHub events failed with status {response.status_code}: "
                    f"{response.text[:200]}"
                )
            batch = response.json()
            if not isinstance(batch, list) or not batch:
                return
            yield from batch
            # Stop paging once the oldest event in this page predates `since`.
            oldest = batch[-1].get("created_at", "")
            if _before(oldest, since):
                return

    # ------------------------------------------------------------------
    # Translation
    # ------------------------------------------------------------------

    def _translate(
        self,
        raw: dict,
        user: str,
        known_keys: set[str],
    ) -> Iterator[ActivityEvent]:
        event_id_base = f"github:{raw.get('id', '')}"
        ts = _normalise_ts(raw.get("created_at", ""))
        actor = raw.get("actor", {}).get("login", user)
        repo = raw.get("repo", {}).get("name", "")
        payload = raw.get("payload", {}) or {}
        event_type = raw.get("type", "")

        if event_type == "PushEvent":
            ref = payload.get("ref", "")
            branch = ref.rsplit("/", 1)[-1] if ref else ""
            commits = payload.get("commits", []) or []
            for commit in commits:
                sha = commit.get("sha", "")
                message = (commit.get("message") or "").splitlines()[0]
                ticket = infer_ticket_key(f"{message} {branch}", known_keys)
                yield ActivityEvent(
                    event_id=f"github:{raw.get('id', '')}:{sha}",
                    timestamp=ts,
                    source=self.name,
                    event_type="commit_pushed",
                    actor=actor,
                    summary=f"{repo}: pushed {sha[:7]} — {message[:120]}",
                    ticket_key=ticket,
                    url=f"https://github.com/{repo}/commit/{sha}",
                    detail={"repo": repo, "branch": branch, "sha": sha, "message": message},
                )
            return

        if event_type == "PullRequestEvent":
            pr = payload.get("pull_request", {}) or {}
            action = payload.get("action", "")
            merged = bool(pr.get("merged"))
            if action == "closed" and merged:
                sub_type = "pr_merged"
                verb = "merged"
            elif action == "closed":
                sub_type = "pr_closed"
                verb = "closed"
            elif action == "opened":
                sub_type = "pr_opened"
                verb = "opened"
            elif action == "reopened":
                sub_type = "pr_reopened"
                verb = "reopened"
            else:
                sub_type = f"pr_{action}"
                verb = action
            number = pr.get("number", "")
            title = pr.get("title", "")
            branch = pr.get("head", {}).get("ref", "") or ""
            ticket = infer_ticket_key(f"{title} {branch} {pr.get('body') or ''}", known_keys)
            yield ActivityEvent(
                event_id=f"{event_id_base}:{sub_type}",
                timestamp=ts,
                source=self.name,
                event_type=sub_type,
                actor=actor,
                summary=f"{repo}#{number}: {verb} — {title[:140]}",
                ticket_key=ticket,
                url=pr.get("html_url") or f"https://github.com/{repo}/pull/{number}",
                detail={"repo": repo, "number": number, "branch": branch, "title": title},
            )
            return

        if event_type == "PullRequestReviewEvent":
            pr = payload.get("pull_request", {}) or {}
            review = payload.get("review", {}) or {}
            number = pr.get("number", "")
            title = pr.get("title", "")
            state = (review.get("state") or "").lower()
            ticket = infer_ticket_key(
                f"{title} {pr.get('head', {}).get('ref', '')} {pr.get('body') or ''}",
                known_keys,
            )
            yield ActivityEvent(
                event_id=f"{event_id_base}:review",
                timestamp=ts,
                source=self.name,
                event_type="pr_review",
                actor=actor,
                summary=f"{repo}#{number}: reviewed ({state}) — {title[:140]}",
                ticket_key=ticket,
                url=review.get("html_url") or pr.get("html_url"),
                detail={"repo": repo, "number": number, "state": state, "title": title},
            )
            return

        if event_type == "PullRequestReviewCommentEvent":
            pr = payload.get("pull_request", {}) or {}
            comment = payload.get("comment", {}) or {}
            number = pr.get("number", "")
            title = pr.get("title", "")
            body = (comment.get("body") or "").strip().splitlines()
            body_first = body[0] if body else ""
            ticket = infer_ticket_key(
                f"{title} {pr.get('head', {}).get('ref', '')}",
                known_keys,
            )
            yield ActivityEvent(
                event_id=f"{event_id_base}:rc:{comment.get('id', '')}",
                timestamp=ts,
                source=self.name,
                event_type="pr_review_comment",
                actor=actor,
                summary=f"{repo}#{number}: review comment — {body_first[:140]}",
                ticket_key=ticket,
                url=comment.get("html_url") or pr.get("html_url"),
                detail={
                    "repo": repo,
                    "number": number,
                    "path": comment.get("path"),
                    "body": comment.get("body"),
                },
            )
            return

        if event_type == "IssueCommentEvent":
            issue = payload.get("issue", {}) or {}
            comment = payload.get("comment", {}) or {}
            number = issue.get("number", "")
            title = issue.get("title", "")
            is_pr = "pull_request" in issue
            body = (comment.get("body") or "").strip().splitlines()
            body_first = body[0] if body else ""
            ticket = infer_ticket_key(f"{title}", known_keys)
            kind = "pr_comment" if is_pr else "issue_comment"
            yield ActivityEvent(
                event_id=f"{event_id_base}:ic:{comment.get('id', '')}",
                timestamp=ts,
                source=self.name,
                event_type=kind,
                actor=actor,
                summary=f"{repo}#{number}: commented — {body_first[:140]}",
                ticket_key=ticket,
                url=comment.get("html_url") or issue.get("html_url"),
                detail={
                    "repo": repo,
                    "number": number,
                    "is_pr": is_pr,
                    "body": comment.get("body"),
                },
            )
            return

        # Ignore other event types (ForkEvent, WatchEvent, CreateEvent on
        # branches we don't care about, etc.) — if we ever need them, add
        # handlers here.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_ts(ts: str) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ts


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _in_window(ts: str, since: datetime, until: datetime) -> bool:
    dt = _parse(ts)
    return dt is not None and since <= dt < until


def _before(ts: str, since: datetime) -> bool:
    dt = _parse(ts)
    return dt is not None and dt < since

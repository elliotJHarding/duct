"""GitHub GraphQL sync source for duct."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import httpx

from duct.exceptions import AuthError, SyncError
from duct.markdown import TICKET_KEY_PATTERN, atomic_write, generate_frontmatter
from duct.models import PRComment, PullRequest, Reviewer, SyncResult
from duct.workspace import enumerate_ticket_dirs, orchestrator_dir

_GRAPHQL_URL = "https://api.github.com/graphql"

_PR_FIELDS = """
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number
        title
        state
        isDraft
        mergeable
        url
        createdAt
        updatedAt
        mergedAt
        headRefName
        repository { nameWithOwner }
        author { login ... on User { avatarUrl } }
        reviews(last: 10) {
          nodes { state author { login } }
        }
        reviewRequests(first: 10) {
          nodes {
            requestedReviewer {
              ... on User { login }
              ... on Team { slug organization { login } }
            }
          }
        }
        commits(last: 1) {
          nodes {
            commit {
              statusCheckRollup { state }
            }
          }
        }
        comments(last: 20) {
          nodes {
            author { login }
            body
            createdAt
          }
        }
        reviewThreads(last: 20) {
          nodes {
            comments(first: 1) {
              nodes {
                author { login }
                body
                createdAt
                path
                line
              }
            }
          }
        }
      }
    }
"""

_PR_SEARCH_QUERY = """
query($query: String!, $cursor: String) {
  search(query: $query, type: ISSUE, first: 50, after: $cursor) {
""" + _PR_FIELDS + """
  }
}
"""


class GitHubSync:
    name = "github"

    def __init__(self, token: str, github_username: str | None = None):
        if not token:
            raise AuthError("GH_TOKEN is not set")
        self._token = token
        self._username = github_username
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _post_with_retry(
        self, *, json: dict, retries: int = 2
    ) -> tuple[httpx.Response, float]:
        """POST to the GitHub GraphQL endpoint with retry on transient errors.

        Returns (response, elapsed_seconds) for the final attempt. Elapsed is
        exposed so callers can include it in diagnostic error messages — useful
        for telling whether 502s are happening at a consistent timeout.
        """
        for attempt in range(retries + 1):
            start = time.monotonic()
            try:
                response = httpx.post(
                    _GRAPHQL_URL,
                    headers=self._headers,
                    json=json,
                    timeout=30,
                )
                return response, time.monotonic() - start
            except httpx.RemoteProtocolError:
                if attempt == retries:
                    raise
                time.sleep(1)
        raise RuntimeError("unreachable")  # keeps type-checker happy

    def sync(self, root: Path) -> SyncResult:
        start = time.time()
        errors: list[str] = []

        ticket_keys = {key for key, _ in enumerate_ticket_dirs(root)}
        if not ticket_keys:
            return SyncResult(
                source=self.name, tickets_synced=0, duration_seconds=time.time() - start
            )

        try:
            all_prs = self._search_prs()
        except (AuthError, SyncError, httpx.RemoteProtocolError) as exc:
            return SyncResult(
                source=self.name,
                tickets_synced=0,
                duration_seconds=time.time() - start,
                errors=[str(exc)],
            )

        # Match PRs to tickets
        ticket_prs: dict[str, list[PullRequest]] = {k: [] for k in ticket_keys}
        for pr in all_prs:
            for key in self._match_ticket_keys(pr, ticket_keys):
                ticket_prs[key].append(pr)

        # Write PULL_REQUESTS.md for each ticket that has PRs
        synced = 0
        for key, prs in ticket_prs.items():
            if not prs:
                continue
            ticket_dirs = [(k, p) for k, p in enumerate_ticket_dirs(root) if k == key]
            if not ticket_dirs:
                continue
            _, ticket_path = ticket_dirs[0]
            try:
                self._write_pull_requests_md(prs, ticket_path)
                synced += 1
            except Exception as exc:
                errors.append(f"{key}: failed to write PR data - {exc}")

        # Review PRs: every still-open PR GitHub says needs the current user's
        # review (personally or via a team they belong to), regardless of
        # whether it matched a workspace ticket. `needs_my_review` carries that
        # signal from the review-requested search query. Merged/closed PRs and
        # the user's own PRs are excluded — nothing to review there.
        review_prs = [
            pr for pr in all_prs
            if pr.state == "open"
            and pr.needs_my_review
            and pr.author != self._username
        ]
        try:
            self._write_review_prs_md(review_prs, root)
        except Exception as exc:
            errors.append(f"review_prs: failed to write - {exc}")

        return SyncResult(
            source=self.name,
            tickets_synced=synced,
            duration_seconds=time.time() - start,
            errors=errors,
        )

    def _search_prs(self) -> list[PullRequest]:
        """Search GitHub for PRs across all relevant queries, dedup by repo#number.

        Each query is sent as its own GraphQL request rather than batched into a
        single aliased query. Batching 3 searches × 50 PRs × deep nested fields
        into one request reliably tripped GitHub's ~10s edge timeout and surfaced
        as a 502 Bad Gateway. Per-query requests stay within budget; the trade
        is N round-trips instead of 1.
        """
        who = self._username or "@me"
        review_query = f"type:pr review-requested:{who}"
        if self._username:
            queries = [
                f"type:pr author:{self._username}",
                f"type:pr assignee:{self._username}",
                review_query,
            ]
        else:
            queries = [
                "type:pr author:@me",
                review_query,
            ]

        # The review-requested query is GitHub's authoritative "needs my review"
        # signal — it includes PRs requested from a team the user belongs to,
        # which `requested_reviewers` (User-only) can't see. Stamp those PRs so
        # the flag survives independent of who the listed reviewers are.
        seen: dict[str, PullRequest] = {}
        for query in queries:
            needs_review = query == review_query
            for pr in self._graphql_search(query, needs_review=needs_review):
                dedup_key = f"{pr.repo}#{pr.number}"
                existing = seen.get(dedup_key)
                if existing is None:
                    seen[dedup_key] = pr
                elif needs_review and not existing.needs_my_review:
                    seen[dedup_key] = replace(existing, needs_my_review=True)
        return list(seen.values())

    def _graphql_search(
        self, query: str, *, needs_review: bool = False
    ) -> list[PullRequest]:
        """Execute a paginated GraphQL search and return PullRequest models.

        ``needs_review`` stamps each parsed PR's ``needs_my_review`` flag — set
        for the review-requested query so the signal isn't re-derived downstream.
        """
        prs: list[PullRequest] = []
        cursor = None

        while True:
            variables: dict[str, str] = {"query": query}
            if cursor:
                variables["cursor"] = cursor

            response, elapsed = self._post_with_retry(
                json={"query": _PR_SEARCH_QUERY, "variables": variables},
            )

            if response.status_code == 401:
                raise AuthError("GitHub authentication failed (401)")
            if response.status_code != 200:
                body = response.text[:500] if response.text else "<empty body>"
                raise SyncError(
                    f"GitHub API error: {response.status_code} after {elapsed:.1f}s\n"
                    f"body: {body}"
                )

            data = response.json()
            if "errors" in data:
                raise SyncError(
                    f"GitHub GraphQL errors after {elapsed:.1f}s: {data['errors']}"
                )

            search = data.get("data", {}).get("search", {})
            nodes = search.get("nodes", [])

            for node in nodes:
                if not node or "number" not in node:
                    continue
                pr = self._parse_pr_node(node, needs_review=needs_review)
                prs.append(pr)

            page_info = search.get("pageInfo", {})
            if page_info.get("hasNextPage") and page_info.get("endCursor"):
                cursor = page_info["endCursor"]
            else:
                break

        return prs

    def _parse_pr_node(self, node: dict, *, needs_review: bool = False) -> PullRequest:
        """Parse a GraphQL PR node into a PullRequest model."""
        if node.get("mergedAt"):
            state = "merged"
        else:
            state = node.get("state", "OPEN").lower()

        reviews = node.get("reviews", {}).get("nodes", [])
        review_status = self._derive_review_status(reviews)

        commits = node.get("commits", {}).get("nodes", [])
        ci_status = "unknown"
        if commits:
            rollup = commits[-1].get("commit", {}).get("statusCheckRollup")
            if rollup:
                ci_state = rollup.get("state", "").lower()
                ci_status = (
                    {"success": "passing", "failure": "failing", "pending": "pending"}
                    .get(ci_state, ci_state)
                )

        # Build reviewer map (last review per author wins)
        reviewer_map: dict[str, str] = {}
        for r in reviews:
            login = r.get("author", {}).get("login", "")
            if login:
                reviewer_map[login] = r.get("state", "")
        reviewers = [Reviewer(login=lg, state=st) for lg, st in reviewer_map.items()]

        # Reviewers requested but who haven't yet posted a review. A
        # requestedReviewer is either a User (has `login`) or a Team (has
        # `slug` + `organization`). Dedup users against the reviewers list — if
        # someone's already reviewed, they belong only in `reviewers`.
        requested_reviewers: list[str] = []
        requested_teams: list[str] = []
        for rr in node.get("reviewRequests", {}).get("nodes", []) or []:
            requested = rr.get("requestedReviewer") or {}
            login = requested.get("login", "")
            if login:
                if login not in reviewer_map:
                    requested_reviewers.append(login)
                continue
            slug = requested.get("slug", "")
            if slug:
                org = (requested.get("organization") or {}).get("login", "")
                requested_teams.append(f"{org}/{slug}" if org else slug)

        # Collect comments from both regular comments and review threads
        comments: list[PRComment] = []
        for c in node.get("comments", {}).get("nodes", []):
            if c and c.get("body"):
                comments.append(PRComment(
                    author=c.get("author", {}).get("login", "unknown"),
                    created_at=c.get("createdAt", ""),
                    body=c.get("body", ""),
                ))
        for thread in node.get("reviewThreads", {}).get("nodes", []):
            thread_comments = thread.get("comments", {}).get("nodes", [])
            for c in thread_comments:
                if c and c.get("body"):
                    comments.append(PRComment(
                        author=c.get("author", {}).get("login", "unknown"),
                        created_at=c.get("createdAt", ""),
                        body=c.get("body", ""),
                        path=c.get("path"),
                        line=c.get("line"),
                    ))

        author = node.get("author") or {}
        return PullRequest(
            number=node["number"],
            title=node.get("title", ""),
            repo=node.get("repository", {}).get("nameWithOwner", ""),
            state=state,
            author=author.get("login", "unknown"),
            is_draft=node.get("isDraft", False),
            review_status=review_status,
            ci_status=ci_status,
            url=node.get("url", ""),
            created_at=node.get("createdAt", ""),
            updated_at=node.get("updatedAt", ""),
            branch=node.get("headRefName", ""),
            reviewers=reviewers,
            comments=comments,
            requested_reviewers=requested_reviewers,
            mergeable=node.get("mergeable") or "UNKNOWN",
            author_avatar_url=author.get("avatarUrl") or None,
            needs_my_review=needs_review,
            requested_teams=requested_teams,
        )

    def _derive_review_status(self, reviews: list[dict]) -> str:
        """Derive the overall review status from a list of review nodes."""
        if not reviews:
            return "pending"
        for review in reversed(reviews):
            state = review.get("state", "")
            if state in ("APPROVED", "CHANGES_REQUESTED"):
                return state
        return "pending"

    def _match_ticket_keys(self, pr: PullRequest, known_keys: set[str]) -> set[str]:
        """Extract ticket keys from PR title and branch name."""
        text = f"{pr.title} {pr.branch}"
        matches = set(TICKET_KEY_PATTERN.findall(text))
        return matches & known_keys

    def _write_review_prs_md(self, prs: list[PullRequest], root: Path) -> None:
        """Write orphan review-requested PRs to `.review_prs.md` at root.

        Always written (even when the list is empty) so stale state is cleared
        on every sync.
        """
        content = self._format_prs_md(prs)
        atomic_write(root / ".review_prs.md", content)

    def _write_pull_requests_md(self, prs: list[PullRequest], ticket_dir: Path) -> None:
        """Write PULL_REQUESTS.md into the orchestrator directory for a ticket."""
        content = self._format_prs_md(prs)
        orch = orchestrator_dir(ticket_dir)
        atomic_write(orch / "PULL_REQUESTS.md", content)

    def _format_prs_md(self, prs: list[PullRequest]) -> str:
        """Render a list of PRs as a PULL_REQUESTS.md document."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts: list[str] = []

        parts.append(generate_frontmatter(source="sync", synced_at=now))
        parts.append("")
        parts.append("# Pull Requests")
        parts.append("")

        for pr in prs:
            draft = " (DRAFT)" if pr.is_draft else ""
            parts.append(f"## #{pr.number} - {pr.title}{draft}")
            parts.append("")
            parts.append(f"- **Repo**: {pr.repo}")
            if pr.branch:
                parts.append(f"- **Branch**: {pr.branch}")
            parts.append(f"- **State**: {pr.state}")
            parts.append(f"- **Author**: @{pr.author}")
            if pr.author_avatar_url:
                parts.append(f"- **Author Avatar**: {pr.author_avatar_url}")
            parts.append(f"- **Review**: {pr.review_status}")
            parts.append(f"- **CI**: {pr.ci_status}")
            parts.append(f"- **Mergeable**: {pr.mergeable}")
            parts.append(f"- **Created**: {pr.created_at}")
            parts.append(f"- **Updated**: {pr.updated_at}")
            if pr.requested_reviewers:
                requested = ", ".join(f"@{login}" for login in pr.requested_reviewers)
                parts.append(f"- **Requested Reviewers**: {requested}")
            if pr.requested_teams:
                teams = ", ".join(f"@{slug}" for slug in pr.requested_teams)
                parts.append(f"- **Requested Teams**: {teams}")
            if pr.needs_my_review:
                parts.append("- **Needs Review**: true")
            parts.append(f"- [View on GitHub]({pr.url})")
            parts.append("")

            if pr.reviewers:
                parts.append("### Reviewers")
                parts.append("")
                for r in pr.reviewers:
                    parts.append(f"- @{r.login}: {r.state}")
                parts.append("")

            review_comments = [c for c in pr.comments if c.path]
            if review_comments:
                parts.append("### Outstanding Comments")
                parts.append("")
                for c in review_comments:
                    loc = f"`{c.path}:{c.line}`" if c.line else f"`{c.path}`"
                    parts.append(f"> **@{c.author}** on {loc} ({c.created_at})")
                    for line in c.body.splitlines():
                        parts.append(f"> {line}")
                    parts.append("")

        return "\n".join(parts)

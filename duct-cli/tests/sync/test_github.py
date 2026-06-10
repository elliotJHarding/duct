"""Tests for the GitHub sync source."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from duct import paths
from duct.exceptions import AuthError, SyncError
from duct.models import PRComment, PullRequest, Reviewer
from duct.sync.github import _GRAPHQL_URL, GitHubSync

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def graphql_response() -> dict:
    return json.loads((FIXTURES / "github_graphql_response.json").read_text())


@pytest.fixture
def gh() -> GitHubSync:
    return GitHubSync(token="fake-token", github_username="alice")


def _make_ticket_dir(workspace: Path, key: str, slug: str) -> Path:
    """Helper to create a ticket directory with an orchestrator subdirectory."""
    d = workspace / f"{key}-{slug}"
    d.mkdir(parents=True)
    (d / "orchestrator").mkdir()
    return d


# ---------------------------------------------------------------------------
# Construction / auth validation
# ---------------------------------------------------------------------------


class TestGitHubSyncInit:
    def test_missing_token_raises(self):
        with pytest.raises(AuthError, match="GH_TOKEN"):
            GitHubSync(token="")

    def test_valid_construction(self, gh: GitHubSync):
        assert gh.name == "github"

    def test_username_optional(self):
        sync = GitHubSync(token="tok")
        assert sync._username is None


# ---------------------------------------------------------------------------
# _parse_pr_node
# ---------------------------------------------------------------------------


class TestParsePrNode:
    def test_open_pr(self, gh: GitHubSync, graphql_response: dict):
        node = graphql_response["data"]["search"]["nodes"][0]
        pr = gh._parse_pr_node(node)

        assert pr.number == 42
        assert pr.title == "ERSC-1278: Fix authentication middleware"
        assert pr.repo == "acme/backend"
        assert pr.state == "open"
        assert pr.author == "alice"
        assert pr.is_draft is False
        assert pr.url == "https://github.com/acme/backend/pull/42"
        assert pr.created_at == "2026-03-10T10:00:00Z"
        assert pr.updated_at == "2026-03-15T14:30:00Z"
        assert pr.branch == "feature/ERSC-1278-fix-auth"
        assert pr.ci_status == "passing"
        assert pr.review_status == "APPROVED"

    def test_merged_pr(self, gh: GitHubSync, graphql_response: dict):
        node = graphql_response["data"]["search"]["nodes"][1]
        pr = gh._parse_pr_node(node)

        assert pr.state == "merged"
        assert pr.number == 99
        assert pr.review_status == "APPROVED"
        assert pr.ci_status == "passing"

    def test_draft_pr(self, gh: GitHubSync, graphql_response: dict):
        node = graphql_response["data"]["search"]["nodes"][2]
        pr = gh._parse_pr_node(node)

        assert pr.is_draft is True
        assert pr.review_status == "pending"
        assert pr.ci_status == "unknown"

    def test_reviewers_extracted(self, gh: GitHubSync, graphql_response: dict):
        node = graphql_response["data"]["search"]["nodes"][0]
        pr = gh._parse_pr_node(node)

        assert len(pr.reviewers) == 1
        assert pr.reviewers[0].login == "bob"
        # Last review state wins
        assert pr.reviewers[0].state == "APPROVED"

    def test_comments_extracted(self, gh: GitHubSync, graphql_response: dict):
        node = graphql_response["data"]["search"]["nodes"][0]
        pr = gh._parse_pr_node(node)

        # 1 regular comment + 1 review thread comment
        assert len(pr.comments) == 2

        regular = [c for c in pr.comments if c.path is None]
        assert len(regular) == 1
        assert regular[0].author == "charlie"
        assert "Looks good" in regular[0].body

        review = [c for c in pr.comments if c.path is not None]
        assert len(review) == 1
        assert review[0].path == "src/auth/middleware.py"
        assert review[0].line == 45
        assert review[0].author == "bob"

    def test_mergeable_parsed(self, gh: GitHubSync, graphql_response: dict):
        node = graphql_response["data"]["search"]["nodes"][0]
        assert gh._parse_pr_node(node).mergeable == "MERGEABLE"

    def test_base_branch_and_diffstat_parsed(
        self, gh: GitHubSync, graphql_response: dict,
    ):
        node = graphql_response["data"]["search"]["nodes"][0]
        pr = gh._parse_pr_node(node)
        assert pr.base_branch == "main"
        assert pr.additions == 412
        assert pr.deletions == 36
        assert pr.changed_files == 12

    def test_base_branch_and_diffstat_default_when_missing(
        self, gh: GitHubSync, graphql_response: dict,
    ):
        # The second fixture node predates these query fields.
        node = graphql_response["data"]["search"]["nodes"][1]
        pr = gh._parse_pr_node(node)
        assert pr.base_branch == ""
        assert pr.additions == 0
        assert pr.deletions == 0
        assert pr.changed_files == 0

    def test_mergeable_defaults_to_unknown_when_missing(self, gh: GitHubSync):
        node = {
            "number": 1, "title": "t", "state": "OPEN", "isDraft": False,
            "url": "", "createdAt": "", "updatedAt": "", "mergedAt": None,
            "headRefName": "", "repository": {"nameWithOwner": "a/b"},
            "author": {"login": "alice"},
            "reviews": {"nodes": []}, "reviewRequests": {"nodes": []},
            "commits": {"nodes": []}, "comments": {"nodes": []},
            "reviewThreads": {"nodes": []},
        }
        assert gh._parse_pr_node(node).mergeable == "UNKNOWN"

    def test_requested_reviewers_extracted(self, gh: GitHubSync, graphql_response: dict):
        # PR #7 in the fixture has a requested reviewer (bob) and no reviews.
        node = graphql_response["data"]["search"]["nodes"][2]
        pr = gh._parse_pr_node(node)
        assert pr.requested_reviewers == ["bob"]
        assert pr.reviewers == []

    def test_requested_reviewer_deduped_against_existing_reviewers(
        self, gh: GitHubSync,
    ):
        """If a user both reviewed and is re-requested, they belong only in reviewers."""
        node = {
            "number": 1, "title": "t", "state": "OPEN", "isDraft": False,
            "mergeable": "MERGEABLE",
            "url": "", "createdAt": "", "updatedAt": "", "mergedAt": None,
            "headRefName": "", "repository": {"nameWithOwner": "a/b"},
            "author": {"login": "alice"},
            "reviews": {
                "nodes": [{"state": "APPROVED", "author": {"login": "bob"}}],
            },
            "reviewRequests": {
                "nodes": [
                    {"requestedReviewer": {"login": "bob"}},
                    {"requestedReviewer": {"login": "carol"}},
                ],
            },
            "commits": {"nodes": []}, "comments": {"nodes": []},
            "reviewThreads": {"nodes": []},
        }
        pr = gh._parse_pr_node(node)
        assert [r.login for r in pr.reviewers] == ["bob"]
        assert pr.requested_reviewers == ["carol"]

    def test_team_review_request_extracted(self, gh: GitHubSync, graphql_response: dict):
        # PR #7 in the fixture is requested from user bob and team acme/claims-dev.
        node = graphql_response["data"]["search"]["nodes"][2]
        pr = gh._parse_pr_node(node)
        assert pr.requested_reviewers == ["bob"]
        assert pr.requested_teams == ["acme/claims-dev"]

    def test_team_slug_without_org(self, gh: GitHubSync):
        node = {
            "number": 1, "title": "t", "state": "OPEN", "isDraft": False,
            "url": "", "createdAt": "", "updatedAt": "", "mergedAt": None,
            "headRefName": "", "repository": {"nameWithOwner": "a/b"},
            "author": {"login": "alice"},
            "reviews": {"nodes": []},
            "reviewRequests": {
                "nodes": [{"requestedReviewer": {"slug": "platform"}}],
            },
            "commits": {"nodes": []}, "comments": {"nodes": []},
            "reviewThreads": {"nodes": []},
        }
        assert gh._parse_pr_node(node).requested_teams == ["platform"]

    def test_needs_my_review_flag_set_when_requested(
        self, gh: GitHubSync, graphql_response: dict,
    ):
        node = graphql_response["data"]["search"]["nodes"][0]
        assert gh._parse_pr_node(node, needs_review=True).needs_my_review is True
        assert gh._parse_pr_node(node).needs_my_review is False

    def test_requested_reviewer_ignores_null_entries(self, gh: GitHubSync):
        """GraphQL returns null requestedReviewer for team-only requests; skip them."""
        node = {
            "number": 1, "title": "t", "state": "OPEN", "isDraft": False,
            "url": "", "createdAt": "", "updatedAt": "", "mergedAt": None,
            "headRefName": "", "repository": {"nameWithOwner": "a/b"},
            "author": {"login": "alice"},
            "reviews": {"nodes": []},
            "reviewRequests": {
                "nodes": [
                    {"requestedReviewer": None},
                    {"requestedReviewer": {"login": "carol"}},
                ],
            },
            "commits": {"nodes": []}, "comments": {"nodes": []},
            "reviewThreads": {"nodes": []},
        }
        assert gh._parse_pr_node(node).requested_reviewers == ["carol"]


# ---------------------------------------------------------------------------
# _derive_review_status
# ---------------------------------------------------------------------------


class TestDeriveReviewStatus:
    def test_no_reviews(self, gh: GitHubSync):
        assert gh._derive_review_status([]) == "pending"

    def test_approved(self, gh: GitHubSync):
        reviews = [{"state": "APPROVED", "author": {"login": "bob"}}]
        assert gh._derive_review_status(reviews) == "APPROVED"

    def test_changes_requested(self, gh: GitHubSync):
        reviews = [{"state": "CHANGES_REQUESTED", "author": {"login": "bob"}}]
        assert gh._derive_review_status(reviews) == "CHANGES_REQUESTED"

    def test_most_recent_wins(self, gh: GitHubSync):
        reviews = [
            {"state": "CHANGES_REQUESTED", "author": {"login": "bob"}},
            {"state": "APPROVED", "author": {"login": "bob"}},
        ]
        assert gh._derive_review_status(reviews) == "APPROVED"

    def test_commented_only_is_pending(self, gh: GitHubSync):
        reviews = [{"state": "COMMENTED", "author": {"login": "bob"}}]
        assert gh._derive_review_status(reviews) == "pending"


# ---------------------------------------------------------------------------
# _match_ticket_keys
# ---------------------------------------------------------------------------


class TestMatchTicketKeys:
    def test_matches_from_title(self, gh: GitHubSync):
        pr = PullRequest(
            number=1, title="ERSC-1278: Fix bug", repo="acme/backend",
            state="open", author="alice", is_draft=False,
            review_status="pending", ci_status="unknown",
            url="", created_at="", updated_at="", branch="main",
        )
        known = {"ERSC-1278", "PROJ-100"}
        assert gh._match_ticket_keys(pr, known) == {"ERSC-1278"}

    def test_matches_from_branch(self, gh: GitHubSync):
        pr = PullRequest(
            number=1, title="Fix some bug", repo="acme/backend",
            state="open", author="alice", is_draft=False,
            review_status="pending", ci_status="unknown",
            url="", created_at="", updated_at="",
            branch="feature/PROJ-100-fix-bug",
        )
        known = {"PROJ-100", "ERSC-1278"}
        assert gh._match_ticket_keys(pr, known) == {"PROJ-100"}

    def test_matches_from_both(self, gh: GitHubSync):
        pr = PullRequest(
            number=1, title="ERSC-1278: Fix bug", repo="acme/backend",
            state="open", author="alice", is_draft=False,
            review_status="pending", ci_status="unknown",
            url="", created_at="", updated_at="",
            branch="feature/PROJ-100-related",
        )
        known = {"ERSC-1278", "PROJ-100"}
        assert gh._match_ticket_keys(pr, known) == {"ERSC-1278", "PROJ-100"}

    def test_no_match(self, gh: GitHubSync):
        pr = PullRequest(
            number=1, title="Random fix", repo="acme/backend",
            state="open", author="alice", is_draft=False,
            review_status="pending", ci_status="unknown",
            url="", created_at="", updated_at="", branch="main",
        )
        known = {"ERSC-1278"}
        assert gh._match_ticket_keys(pr, known) == set()

    def test_unknown_key_not_returned(self, gh: GitHubSync):
        pr = PullRequest(
            number=1, title="UNKNOWN-999: Something", repo="acme/backend",
            state="open", author="alice", is_draft=False,
            review_status="pending", ci_status="unknown",
            url="", created_at="", updated_at="", branch="main",
        )
        known = {"ERSC-1278"}
        assert gh._match_ticket_keys(pr, known) == set()


# ---------------------------------------------------------------------------
# _write_pull_requests_md
# ---------------------------------------------------------------------------


class TestWritePullRequestsMd:
    def test_format(self, gh: GitHubSync, tmp_path: Path):
        ticket_dir = tmp_path / "ERSC-1278-fix-auth"
        ticket_dir.mkdir()
        (ticket_dir / "orchestrator").mkdir()

        prs = [
            PullRequest(
                number=42,
                title="ERSC-1278: Fix authentication middleware",
                repo="acme/backend",
                state="open",
                author="alice",
                is_draft=False,
                review_status="APPROVED",
                ci_status="passing",
                url="https://github.com/acme/backend/pull/42",
                created_at="2026-03-10T10:00:00Z",
                updated_at="2026-03-15T14:30:00Z",
                branch="feature/ERSC-1278-fix-auth",
                base_branch="main",
                additions=412,
                deletions=36,
                changed_files=12,
                reviewers=[Reviewer(login="bob", state="APPROVED")],
                comments=[
                    PRComment(
                        author="bob",
                        created_at="2026-03-13T11:00:00Z",
                        body="Consider using a constant here.",
                        path="src/auth/middleware.py",
                        line=45,
                    ),
                ],
            ),
        ]

        gh._write_pull_requests_md(prs, ticket_dir)

        md_path = ticket_dir / "orchestrator" / "PULL_REQUESTS.md"
        assert md_path.exists()
        content = md_path.read_text()

        # Frontmatter
        assert content.startswith("---\n")
        assert "source: sync" in content
        assert "syncedAt:" in content

        # PR heading
        assert "## #42 - ERSC-1278: Fix authentication middleware" in content

        # Metadata
        assert "- **Repo**: acme/backend" in content
        assert "- **Branch**: feature/ERSC-1278-fix-auth" in content
        assert "- **Base Branch**: main" in content
        assert "- **Additions**: 412" in content
        assert "- **Deletions**: 36" in content
        assert "- **Changed Files**: 12" in content
        assert "- **State**: open" in content
        assert "- **Author**: @alice" in content
        assert "- **Review**: APPROVED" in content
        assert "- **CI**: passing" in content
        assert "- **Mergeable**: UNKNOWN" in content  # default for this fixture
        assert "[View on GitHub](https://github.com/acme/backend/pull/42)" in content

        # Reviewers
        assert "### Reviewers" in content
        assert "- @bob: APPROVED" in content

        # Review comments
        assert "### Outstanding Comments" in content
        assert "`src/auth/middleware.py:45`" in content
        assert "Consider using a constant here." in content

    def test_draft_indicator(self, gh: GitHubSync, tmp_path: Path):
        ticket_dir = tmp_path / "ERSC-100-task"
        ticket_dir.mkdir()
        (ticket_dir / "orchestrator").mkdir()

        prs = [
            PullRequest(
                number=7, title="WIP experiment", repo="acme/frontend",
                state="open", author="alice", is_draft=True,
                review_status="pending", ci_status="unknown",
                url="https://github.com/acme/frontend/pull/7",
                created_at="2026-03-14T12:00:00Z",
                updated_at="2026-03-14T12:00:00Z",
                branch="experiment/ERSC-100-parser",
            ),
        ]

        gh._write_pull_requests_md(prs, ticket_dir)
        content = (ticket_dir / "orchestrator" / "PULL_REQUESTS.md").read_text()

        assert "(DRAFT)" in content

    def test_no_reviewers_or_comments(self, gh: GitHubSync, tmp_path: Path):
        ticket_dir = tmp_path / "ERSC-100-task"
        ticket_dir.mkdir()
        (ticket_dir / "orchestrator").mkdir()

        prs = [
            PullRequest(
                number=1, title="Simple fix", repo="acme/backend",
                state="open", author="alice", is_draft=False,
                review_status="pending", ci_status="unknown",
                url="https://github.com/acme/backend/pull/1",
                created_at="2026-03-14T12:00:00Z",
                updated_at="2026-03-14T12:00:00Z",
                branch="fix/ERSC-100",
            ),
        ]

        gh._write_pull_requests_md(prs, ticket_dir)
        content = (ticket_dir / "orchestrator" / "PULL_REQUESTS.md").read_text()

        assert "### Reviewers" not in content
        assert "### Outstanding Comments" not in content

    def test_zero_diffstat_and_empty_base_omitted(
        self, gh: GitHubSync, tmp_path: Path,
    ):
        ticket_dir = tmp_path / "ERSC-100-task"
        ticket_dir.mkdir()
        (ticket_dir / "orchestrator").mkdir()

        prs = [
            PullRequest(
                number=1, title="Simple fix", repo="acme/backend",
                state="open", author="alice", is_draft=False,
                review_status="pending", ci_status="unknown",
                url="https://github.com/acme/backend/pull/1",
                created_at="2026-03-14T12:00:00Z",
                updated_at="2026-03-14T12:00:00Z",
                branch="fix/ERSC-100",
            ),
        ]

        gh._write_pull_requests_md(prs, ticket_dir)
        content = (ticket_dir / "orchestrator" / "PULL_REQUESTS.md").read_text()

        assert "- **Base Branch**:" not in content
        assert "- **Additions**:" not in content
        assert "- **Deletions**:" not in content
        assert "- **Changed Files**:" not in content

    def test_mergeable_and_requested_reviewers_roundtrip(
        self, gh: GitHubSync, tmp_path: Path,
    ):
        """New fields written by the sync are recovered by the parser."""
        from duct.pr import parse_pull_requests_md

        ticket_dir = tmp_path / "ERSC-100-task"
        ticket_dir.mkdir()
        (ticket_dir / "orchestrator").mkdir()

        prs = [
            PullRequest(
                number=9, title="Conflict demo", repo="acme/backend",
                state="open", author="alice", is_draft=False,
                review_status="pending", ci_status="passing",
                url="https://github.com/acme/backend/pull/9",
                created_at="2026-03-14T12:00:00Z",
                updated_at="2026-03-14T12:00:00Z",
                branch="fix/ERSC-100",
                requested_reviewers=["bob", "carol"],
                mergeable="CONFLICTING",
                author_avatar_url="https://avatars.githubusercontent.com/u/1?v=4",
            ),
        ]

        gh._write_pull_requests_md(prs, ticket_dir)
        content = (ticket_dir / "orchestrator" / "PULL_REQUESTS.md").read_text()

        assert "- **Mergeable**: CONFLICTING" in content
        assert "- **Requested Reviewers**: @bob, @carol" in content
        assert (
            "- **Author Avatar**: https://avatars.githubusercontent.com/u/1?v=4"
            in content
        )

        parsed = parse_pull_requests_md(content)
        assert len(parsed) == 1
        assert parsed[0].mergeable == "CONFLICTING"
        assert parsed[0].requested_reviewers == ["bob", "carol"]
        assert (
            parsed[0].author_avatar_url
            == "https://avatars.githubusercontent.com/u/1?v=4"
        )

    def test_needs_review_and_teams_roundtrip(self, gh: GitHubSync, tmp_path: Path):
        """needs_my_review and requested_teams survive write -> parse."""
        from duct.pr import parse_pull_requests_md

        ticket_dir = tmp_path / "ERSC-100-task"
        ticket_dir.mkdir()
        (ticket_dir / "orchestrator").mkdir()

        prs = [
            PullRequest(
                number=7, title="Team-requested", repo="acme/backend",
                state="open", author="bob", is_draft=False,
                review_status="pending", ci_status="passing",
                url="https://github.com/acme/backend/pull/7",
                created_at="2026-03-14T12:00:00Z",
                updated_at="2026-03-14T12:00:00Z", branch="fix/x",
                needs_my_review=True,
                requested_teams=["acme/claims-dev", "acme/claims-tech-leads"],
            ),
        ]
        gh._write_pull_requests_md(prs, ticket_dir)
        content = (ticket_dir / "orchestrator" / "PULL_REQUESTS.md").read_text()

        assert "- **Needs Review**: true" in content
        assert "- **Requested Teams**: @acme/claims-dev, @acme/claims-tech-leads" in content

        parsed = parse_pull_requests_md(content)[0]
        assert parsed.needs_my_review is True
        assert parsed.requested_teams == ["acme/claims-dev", "acme/claims-tech-leads"]

    def test_needs_review_omitted_when_false(self, gh: GitHubSync, tmp_path: Path):
        ticket_dir = tmp_path / "ERSC-100-task"
        ticket_dir.mkdir()
        (ticket_dir / "orchestrator").mkdir()
        prs = [
            PullRequest(
                number=1, title="Mine", repo="acme/backend",
                state="open", author="alice", is_draft=False,
                review_status="pending", ci_status="unknown",
                url="", created_at="", updated_at="", branch="main",
            ),
        ]
        gh._write_pull_requests_md(prs, ticket_dir)
        content = (ticket_dir / "orchestrator" / "PULL_REQUESTS.md").read_text()
        assert "Needs Review" not in content
        assert "Requested Teams" not in content

    def test_empty_requested_reviewers_omits_line(
        self, gh: GitHubSync, tmp_path: Path,
    ):
        ticket_dir = tmp_path / "ERSC-100-task"
        ticket_dir.mkdir()
        (ticket_dir / "orchestrator").mkdir()

        prs = [
            PullRequest(
                number=1, title="Simple", repo="acme/backend",
                state="open", author="alice", is_draft=False,
                review_status="pending", ci_status="unknown",
                url="", created_at="", updated_at="", branch="main",
            ),
        ]
        gh._write_pull_requests_md(prs, ticket_dir)
        content = (ticket_dir / "orchestrator" / "PULL_REQUESTS.md").read_text()
        assert "Requested Reviewers" not in content


# ---------------------------------------------------------------------------
# _search_prs provenance (needs_my_review)
# ---------------------------------------------------------------------------


def _pr_node(number: int, *, author: str = "alice", repo: str = "acme/repo") -> dict:
    return {
        "number": number, "title": f"PR {number}", "state": "OPEN",
        "isDraft": False, "url": f"https://github.com/{repo}/pull/{number}",
        "createdAt": "2026-03-01T00:00:00Z", "updatedAt": "2026-03-01T00:00:00Z",
        "mergedAt": None, "headRefName": "main",
        "repository": {"nameWithOwner": repo}, "author": {"login": author},
        "reviews": {"nodes": []}, "reviewRequests": {"nodes": []},
        "commits": {"nodes": []}, "comments": {"nodes": []},
        "reviewThreads": {"nodes": []},
    }


def _page(*nodes: dict) -> dict:
    return {
        "data": {
            "search": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": list(nodes),
            }
        }
    }


class TestSearchPrsProvenance:
    def test_flag_only_on_review_requested_query(self, gh: GitHubSync, httpx_mock):
        # Order of queries: author, assignee, review-requested.
        httpx_mock.add_response(url=_GRAPHQL_URL, json=_page(_pr_node(42)))
        httpx_mock.add_response(url=_GRAPHQL_URL, json=_page())
        httpx_mock.add_response(url=_GRAPHQL_URL, json=_page(_pr_node(7)))

        by_number = {pr.number: pr for pr in gh._search_prs()}
        assert by_number[42].needs_my_review is False
        assert by_number[7].needs_my_review is True

    def test_flag_ored_when_pr_in_multiple_queries(self, gh: GitHubSync, httpx_mock):
        # PR 42 surfaces from the author query first, then again from the
        # review-requested query — the flag must end up True.
        httpx_mock.add_response(url=_GRAPHQL_URL, json=_page(_pr_node(42)))
        httpx_mock.add_response(url=_GRAPHQL_URL, json=_page())
        httpx_mock.add_response(url=_GRAPHQL_URL, json=_page(_pr_node(42)))

        by_number = {pr.number: pr for pr in gh._search_prs()}
        assert by_number[42].needs_my_review is True


# ---------------------------------------------------------------------------
# _graphql_search (HTTP mocking)
# ---------------------------------------------------------------------------


class TestGraphqlSearch:
    def test_single_page(self, gh: GitHubSync, httpx_mock, graphql_response: dict):
        httpx_mock.add_response(
            url=_GRAPHQL_URL,
            json=graphql_response,
        )

        prs = gh._graphql_search("type:pr author:alice")
        assert len(prs) == 3
        assert prs[0].number == 42
        assert prs[1].number == 99
        assert prs[2].number == 7

    def test_pagination(self, gh: GitHubSync, httpx_mock):
        page1 = {
            "data": {
                "search": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"},
                    "nodes": [
                        {
                            "number": 1,
                            "title": "First PR",
                            "state": "OPEN",
                            "isDraft": False,
                            "url": "https://github.com/acme/repo/pull/1",
                            "createdAt": "2026-03-01T00:00:00Z",
                            "updatedAt": "2026-03-01T00:00:00Z",
                            "mergedAt": None,
                            "headRefName": "feature/first",
                            "repository": {"nameWithOwner": "acme/repo"},
                            "author": {"login": "alice"},
                            "reviews": {"nodes": []},
                            "reviewRequests": {"nodes": []},
                            "commits": {"nodes": []},
                            "comments": {"nodes": []},
                            "reviewThreads": {"nodes": []},
                        }
                    ],
                }
            }
        }
        page2 = {
            "data": {
                "search": {
                    "pageInfo": {"hasNextPage": False, "endCursor": "cursor2"},
                    "nodes": [
                        {
                            "number": 2,
                            "title": "Second PR",
                            "state": "OPEN",
                            "isDraft": False,
                            "url": "https://github.com/acme/repo/pull/2",
                            "createdAt": "2026-03-02T00:00:00Z",
                            "updatedAt": "2026-03-02T00:00:00Z",
                            "mergedAt": None,
                            "headRefName": "feature/second",
                            "repository": {"nameWithOwner": "acme/repo"},
                            "author": {"login": "alice"},
                            "reviews": {"nodes": []},
                            "reviewRequests": {"nodes": []},
                            "commits": {"nodes": []},
                            "comments": {"nodes": []},
                            "reviewThreads": {"nodes": []},
                        }
                    ],
                }
            }
        }

        httpx_mock.add_response(url=_GRAPHQL_URL, json=page1)
        httpx_mock.add_response(url=_GRAPHQL_URL, json=page2)

        prs = gh._graphql_search("type:pr author:alice")
        assert len(prs) == 2
        assert prs[0].number == 1
        assert prs[1].number == 2

    def test_auth_failure_raises(self, gh: GitHubSync, httpx_mock):
        httpx_mock.add_response(url=_GRAPHQL_URL, status_code=401, text="Unauthorized")

        with pytest.raises(AuthError, match="401"):
            gh._graphql_search("type:pr author:alice")

    def test_server_error_raises_with_body(self, gh: GitHubSync, httpx_mock):
        httpx_mock.add_response(
            url=_GRAPHQL_URL, status_code=500, text="upstream timeout"
        )

        with pytest.raises(SyncError) as exc_info:
            gh._graphql_search("type:pr author:alice")

        msg = str(exc_info.value)
        assert "500" in msg
        assert "upstream timeout" in msg

    def test_non_200_with_empty_body_marks_it(self, gh: GitHubSync, httpx_mock):
        httpx_mock.add_response(url=_GRAPHQL_URL, status_code=503, text="")

        with pytest.raises(SyncError) as exc_info:
            gh._graphql_search("type:pr author:alice")

        msg = str(exc_info.value)
        assert "503" in msg
        assert "<empty body>" in msg

    def test_non_200_body_is_truncated(self, gh: GitHubSync, httpx_mock):
        long_body = "x" * 2000
        httpx_mock.add_response(url=_GRAPHQL_URL, status_code=500, text=long_body)

        with pytest.raises(SyncError) as exc_info:
            gh._graphql_search("type:pr author:alice")

        body_part = str(exc_info.value).split("body: ", 1)[1]
        assert len(body_part) == 500

    def test_graphql_errors_raises(self, gh: GitHubSync, httpx_mock):
        httpx_mock.add_response(
            url=_GRAPHQL_URL,
            json={"errors": [{"message": "Bad query"}]},
        )

        with pytest.raises(SyncError, match="GraphQL errors"):
            gh._graphql_search("type:pr author:alice")

    def test_skips_empty_nodes(self, gh: GitHubSync, httpx_mock):
        response = {
            "data": {
                "search": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [None, {}, {"number": 10, "title": "Valid",
                        "state": "OPEN", "isDraft": False,
                        "url": "https://github.com/a/b/pull/10",
                        "createdAt": "2026-03-01T00:00:00Z",
                        "updatedAt": "2026-03-01T00:00:00Z",
                        "mergedAt": None, "headRefName": "main",
                        "repository": {"nameWithOwner": "a/b"},
                        "author": {"login": "alice"},
                        "reviews": {"nodes": []},
                        "reviewRequests": {"nodes": []},
                        "commits": {"nodes": []},
                        "comments": {"nodes": []},
                        "reviewThreads": {"nodes": []},
                    }],
                }
            }
        }
        httpx_mock.add_response(url=_GRAPHQL_URL, json=response)

        prs = gh._graphql_search("type:pr author:alice")
        assert len(prs) == 1
        assert prs[0].number == 10


# ---------------------------------------------------------------------------
# Full sync cycle
# ---------------------------------------------------------------------------


class TestFullSync:
    def test_sync_writes_pull_requests_md(
        self, gh: GitHubSync, httpx_mock, graphql_response: dict, tmp_workspace: Path,
    ):
        # Create ticket directory that matches PR #42 (ERSC-1278 in title and branch)
        _make_ticket_dir(tmp_workspace, "ERSC-1278", "fix-auth")

        # One response per query (author / assignee / review-requested)
        httpx_mock.add_response(
            url=_GRAPHQL_URL, json=graphql_response, is_reusable=True,
        )

        result = gh.sync(tmp_workspace)

        assert result.source == "github"
        assert result.tickets_synced == 1
        assert result.errors == []
        assert result.duration_seconds > 0

        md_path = tmp_workspace / "ERSC-1278-fix-auth" / "orchestrator" / "PULL_REQUESTS.md"
        assert md_path.exists()
        content = md_path.read_text()
        assert "## #42" in content
        # PR #7 also matches ERSC-1278 via branch name
        assert "## #7" in content

    def test_sync_writes_review_prs_md(
        self, gh: GitHubSync, httpx_mock, tmp_workspace: Path,
    ):
        """A team-requested PR by another author lands in .review_prs.md even
        though it matches no tracked ticket."""
        _make_ticket_dir(tmp_workspace, "ERSC-1278", "fix-auth")

        node = _pr_node(31, author="bob", repo="acme/api")
        node["reviewRequests"]["nodes"] = [
            {"requestedReviewer": {
                "slug": "claims-dev", "organization": {"login": "acme"}},
            },
        ]
        httpx_mock.add_response(url=_GRAPHQL_URL, json=_page(node), is_reusable=True)

        result = gh.sync(tmp_workspace)
        assert result.errors == []

        review_md = paths.review_prs_file(tmp_workspace)
        assert review_md.exists()
        content = review_md.read_text()
        assert "## #31" in content
        assert "- **Needs Review**: true" in content
        assert "- **Requested Teams**: @acme/claims-dev" in content

    def test_sync_excludes_own_prs_from_review(
        self, gh: GitHubSync, httpx_mock, tmp_workspace: Path,
    ):
        """My own PRs are never review work, even if returned by the query."""
        _make_ticket_dir(tmp_workspace, "ERSC-1278", "fix-auth")
        node = _pr_node(50, author="alice")  # alice is the configured username
        httpx_mock.add_response(url=_GRAPHQL_URL, json=_page(node), is_reusable=True)

        gh.sync(tmp_workspace)
        content = paths.review_prs_file(tmp_workspace).read_text()
        assert "## #50" not in content

    def test_sync_no_ticket_dirs(self, gh: GitHubSync, tmp_workspace: Path):
        result = gh.sync(tmp_workspace)
        assert result.tickets_synced == 0
        assert result.errors == []

    def test_sync_no_matching_prs(
        self, gh: GitHubSync, httpx_mock, tmp_workspace: Path,
    ):
        _make_ticket_dir(tmp_workspace, "NOMATCH-999", "unrelated")

        empty_response = {
            "data": {
                "search": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [],
                }
            }
        }
        httpx_mock.add_response(url=_GRAPHQL_URL, json=empty_response, is_reusable=True)

        result = gh.sync(tmp_workspace)
        assert result.tickets_synced == 0
        assert result.errors == []

    def test_sync_auth_failure(
        self, gh: GitHubSync, httpx_mock, tmp_workspace: Path,
    ):
        _make_ticket_dir(tmp_workspace, "ERSC-1278", "fix-auth")

        httpx_mock.add_response(url=_GRAPHQL_URL, status_code=401, text="Unauthorized")

        result = gh.sync(tmp_workspace)
        assert result.tickets_synced == 0
        assert len(result.errors) == 1
        assert "401" in result.errors[0]

    def test_sync_502_captures_response_body_and_timing(
        self, gh: GitHubSync, httpx_mock, tmp_workspace: Path,
    ):
        """A 502 from GitHub's edge proxy surfaces with the HTML body and the
        elapsed request time, so the user can tell whether 502s are happening
        at a consistent timeout."""
        _make_ticket_dir(tmp_workspace, "ERSC-1278", "fix-auth")

        edge_html = "<html><head><title>502</title></head><body>Bad Gateway</body></html>"
        httpx_mock.add_response(url=_GRAPHQL_URL, status_code=502, text=edge_html)

        result = gh.sync(tmp_workspace)
        assert result.tickets_synced == 0
        assert len(result.errors) == 1
        err = result.errors[0]
        assert "502" in err
        assert "Bad Gateway" in err
        # Timing is rendered as "after X.Xs"
        assert re.search(r"after \d+\.\ds", err)

    def test_sync_deduplicates_prs(
        self, gh: GitHubSync, httpx_mock, graphql_response: dict, tmp_workspace: Path,
    ):
        _make_ticket_dir(tmp_workspace, "ERSC-1278", "fix-auth")

        # Each of the 3 per-query requests returns the same PRs -- dedup
        # by repo#number should prevent duplicates in the rendered output.
        httpx_mock.add_response(
            url=_GRAPHQL_URL, json=graphql_response, is_reusable=True,
        )

        result = gh.sync(tmp_workspace)
        assert result.tickets_synced == 1

        content = (
            tmp_workspace / "ERSC-1278-fix-auth" / "orchestrator" / "PULL_REQUESTS.md"
        ).read_text()

        # PR #42 should appear only once despite being returned by all 3 queries
        assert content.count("## #42") == 1

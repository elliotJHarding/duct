"""Tests for the PR markdown parser and status derivation."""

from __future__ import annotations

from duct.models import PRComment, PullRequest, Reviewer
from duct.pr import (
    categorize_my_pr,
    derive_status_label,
    parse_pull_requests_md,
    pr_action_reasons,
    style_status_label,
)


MINIMAL_PR_MD = """\
---
source: sync
syncedAt: 2026-03-27T10:00:00Z
---

# Pull Requests

## #42 - ERSC-100: Fix authentication middleware

- **Repo**: org/backend
- **State**: open
- **Author**: @alice
- **Review**: pending
- **CI**: passing
- **Created**: 2026-03-20T10:00:00Z
- **Updated**: 2026-03-27T10:00:00Z
- [View on GitHub](https://github.com/org/backend/pull/42)
"""

DRAFT_PR_MD = """\
---
source: sync
syncedAt: 2026-03-27T10:00:00Z
---

# Pull Requests

## #38 - ERSC-100: Add logging (DRAFT)

- **Repo**: org/backend
- **State**: open
- **Author**: @alice
- **Review**: pending
- **CI**: pending
- **Created**: 2026-03-25T10:00:00Z
- **Updated**: 2026-03-27T10:00:00Z
- [View on GitHub](https://github.com/org/backend/pull/38)
"""

FULL_PR_MD = """\
---
source: sync
syncedAt: 2026-03-27T10:00:00Z
---

# Pull Requests

## #42 - ERSC-100: Fix authentication middleware

- **Repo**: org/backend
- **State**: open
- **Author**: @alice
- **Review**: CHANGES_REQUESTED
- **CI**: passing
- **Created**: 2026-03-20T10:00:00Z
- **Updated**: 2026-03-27T10:00:00Z
- [View on GitHub](https://github.com/org/backend/pull/42)

### Reviewers

- @bob: CHANGES_REQUESTED
- @carol: APPROVED

### Outstanding Comments

> **@bob** on `src/auth.py:45` (2026-03-26T15:00:00Z)
> This needs error handling for the token expiry case

## #15 - ERSC-100: Refactor components

- **Repo**: org/frontend
- **State**: merged
- **Author**: @alice
- **Review**: APPROVED
- **CI**: passing
- **Created**: 2026-03-10T10:00:00Z
- **Updated**: 2026-03-15T10:00:00Z
- [View on GitHub](https://github.com/org/frontend/pull/15)

### Reviewers

- @bob: APPROVED
"""

NEW_FIELDS_PR_MD = """\
---
source: sync
syncedAt: 2026-03-27T10:00:00Z
---

# Pull Requests

## #77 - ERSC-200: Sync fix

- **Repo**: org/backend
- **State**: open
- **Author**: @alice
- **Review**: pending
- **CI**: passing
- **Mergeable**: CONFLICTING
- **Created**: 2026-03-20T10:00:00Z
- **Updated**: 2026-03-27T10:00:00Z
- **Requested Reviewers**: @bob, @carol
- **Requested Teams**: @org/claims-dev, @org/claims-tech-leads
- **Needs Review**: true
- [View on GitHub](https://github.com/org/backend/pull/77)
"""


class TestParsePullRequestsMd:
    def test_minimal_pr(self) -> None:
        prs = parse_pull_requests_md(MINIMAL_PR_MD)
        assert len(prs) == 1
        pr = prs[0]
        assert pr.number == 42
        assert pr.title == "ERSC-100: Fix authentication middleware"
        assert pr.repo == "org/backend"
        assert pr.state == "open"
        assert pr.author == "alice"
        assert pr.is_draft is False
        assert pr.review_status == "pending"
        assert pr.url == "https://github.com/org/backend/pull/42"
        assert pr.reviewers == []
        assert pr.comments == []

    def test_draft_pr(self) -> None:
        prs = parse_pull_requests_md(DRAFT_PR_MD)
        assert len(prs) == 1
        assert prs[0].is_draft is True
        assert prs[0].title == "ERSC-100: Add logging"

    def test_multiple_prs_with_reviewers_and_comments(self) -> None:
        prs = parse_pull_requests_md(FULL_PR_MD)
        assert len(prs) == 2

        first = prs[0]
        assert first.number == 42
        assert first.review_status == "CHANGES_REQUESTED"
        assert len(first.reviewers) == 2
        assert first.reviewers[0] == Reviewer(login="bob", state="CHANGES_REQUESTED")
        assert first.reviewers[1] == Reviewer(login="carol", state="APPROVED")
        assert len(first.comments) == 1
        assert first.comments[0].author == "bob"
        assert first.comments[0].path == "src/auth.py"
        assert first.comments[0].line == 45
        assert "error handling" in first.comments[0].body

        second = prs[1]
        assert second.number == 15
        assert second.state == "merged"
        assert len(second.reviewers) == 1
        assert second.comments == []

    def test_empty_content(self) -> None:
        prs = parse_pull_requests_md("")
        assert prs == []

    def test_no_frontmatter(self) -> None:
        content = "# Pull Requests\n\n## #1 - Test\n\n- **State**: open\n- **Author**: @x\n- **Review**: pending\n"
        prs = parse_pull_requests_md(content)
        assert len(prs) == 1
        assert prs[0].number == 1

    def test_mergeable_and_requested_reviewers_parse(self) -> None:
        prs = parse_pull_requests_md(NEW_FIELDS_PR_MD)
        assert len(prs) == 1
        pr = prs[0]
        assert pr.mergeable == "CONFLICTING"
        assert pr.requested_reviewers == ["bob", "carol"]

    def test_needs_review_and_teams_parse(self) -> None:
        pr = parse_pull_requests_md(NEW_FIELDS_PR_MD)[0]
        assert pr.needs_my_review is True
        assert pr.requested_teams == ["org/claims-dev", "org/claims-tech-leads"]

    def test_backwards_compat_defaults_when_fields_absent(self) -> None:
        """PR markdown without the new fields gets default values."""
        prs = parse_pull_requests_md(MINIMAL_PR_MD)
        assert prs[0].mergeable == "UNKNOWN"
        assert prs[0].requested_reviewers == []
        assert prs[0].requested_teams == []
        assert prs[0].needs_my_review is False
        assert prs[0].branch == ""
        assert prs[0].base_branch == ""
        assert prs[0].additions == 0
        assert prs[0].deletions == 0
        assert prs[0].changed_files == 0

    def test_branch_field_parses(self) -> None:
        content = MINIMAL_PR_MD.replace(
            "- **Repo**: org/backend\n",
            "- **Repo**: org/backend\n- **Branch**: feature-x\n",
        )
        prs = parse_pull_requests_md(content)
        assert prs[0].branch == "feature-x"

    def test_base_branch_and_diffstat_parse(self) -> None:
        content = MINIMAL_PR_MD.replace(
            "- **Repo**: org/backend\n",
            "- **Repo**: org/backend\n"
            "- **Base Branch**: main\n"
            "- **Additions**: 412\n"
            "- **Deletions**: 36\n"
            "- **Changed Files**: 12\n",
        )
        pr = parse_pull_requests_md(content)[0]
        assert pr.base_branch == "main"
        assert pr.additions == 412
        assert pr.deletions == 36
        assert pr.changed_files == 12

    def test_malformed_diffstat_falls_back_to_zero(self) -> None:
        content = MINIMAL_PR_MD.replace(
            "- **Repo**: org/backend\n",
            "- **Repo**: org/backend\n- **Additions**: lots\n",
        )
        assert parse_pull_requests_md(content)[0].additions == 0


class TestDeriveStatusLabel:
    def _pr(self, **overrides) -> PullRequest:
        defaults = dict(
            number=1, title="t", repo="r", state="open", author="a",
            is_draft=False, review_status="pending", ci_status="passing",
            url="", created_at="", updated_at="",
        )
        defaults.update(overrides)
        return PullRequest(**defaults)

    def test_merged(self) -> None:
        assert derive_status_label(self._pr(state="merged")) == "merged"

    def test_closed(self) -> None:
        assert derive_status_label(self._pr(state="closed")) == "closed"

    def test_draft(self) -> None:
        assert derive_status_label(self._pr(is_draft=True)) == "draft"

    def test_changes_requested(self) -> None:
        assert derive_status_label(self._pr(review_status="CHANGES_REQUESTED")) == "changes requested"

    def test_has_comments(self) -> None:
        pr = self._pr(
            review_status="pending",
            comments=[PRComment(author="x", created_at="", body="fix", path="a.py", line=1)],
        )
        assert derive_status_label(pr) == "has comments"

    def test_approved(self) -> None:
        assert derive_status_label(self._pr(review_status="APPROVED")) == "approved"

    def test_in_review(self) -> None:
        pr = self._pr(
            review_status="pending",
            reviewers=[Reviewer(login="bob", state="PENDING")],
        )
        assert derive_status_label(pr) == "in review"

    def test_open_fallback(self) -> None:
        assert derive_status_label(self._pr()) == "open"

    def test_priority_order_draft_over_changes_requested(self) -> None:
        """Draft takes priority over review status."""
        pr = self._pr(is_draft=True, review_status="CHANGES_REQUESTED")
        assert derive_status_label(pr) == "draft"

    def test_priority_order_changes_requested_over_comments(self) -> None:
        pr = self._pr(
            review_status="CHANGES_REQUESTED",
            comments=[PRComment(author="x", created_at="", body="fix", path="a.py", line=1)],
        )
        assert derive_status_label(pr) == "changes requested"


class TestStyleStatusLabel:
    def test_known_labels_get_markup(self) -> None:
        assert "[green]" in style_status_label("approved")
        assert "[magenta]" in style_status_label("merged")
        assert "[yellow]" in style_status_label("changes requested")

    def test_unknown_label_returned_as_is(self) -> None:
        assert style_status_label("unknown") == "unknown"


def _pr(**overrides) -> PullRequest:
    defaults = dict(
        number=1, title="t", repo="r", state="open", author="a",
        is_draft=False, review_status="pending", ci_status="passing",
        url="", created_at="", updated_at="",
    )
    defaults.update(overrides)
    return PullRequest(**defaults)


class TestPrActionReasons:
    def test_merged_returns_empty(self) -> None:
        assert pr_action_reasons(_pr(state="merged")) == []

    def test_closed_returns_empty(self) -> None:
        assert pr_action_reasons(_pr(state="closed")) == []

    def test_clean_open_pr_returns_empty(self) -> None:
        assert pr_action_reasons(_pr()) == []

    def test_merge_conflicts(self) -> None:
        assert pr_action_reasons(_pr(mergeable="CONFLICTING")) == ["merge conflicts"]

    def test_changes_requested(self) -> None:
        reasons = pr_action_reasons(_pr(review_status="CHANGES_REQUESTED"))
        assert reasons == ["changes requested"]

    def test_ci_failing(self) -> None:
        assert pr_action_reasons(_pr(ci_status="failing")) == ["CI failing"]

    def test_unresolved_comments_counts(self) -> None:
        pr = _pr(comments=[
            PRComment(author="b", created_at="", body="fix", path="a.py", line=1),
            PRComment(author="b", created_at="", body="also", path="b.py", line=2),
            PRComment(author="b", created_at="", body="general"),  # no path
        ])
        assert pr_action_reasons(pr) == ["unresolved comments (2)"]

    def test_draft(self) -> None:
        assert pr_action_reasons(_pr(is_draft=True)) == ["draft"]

    def test_priority_order(self) -> None:
        """All reasons stack in the documented priority order."""
        pr = _pr(
            mergeable="CONFLICTING",
            review_status="CHANGES_REQUESTED",
            ci_status="failing",
            comments=[PRComment(author="b", created_at="", body="x", path="a.py", line=1)],
            is_draft=True,
        )
        assert pr_action_reasons(pr) == [
            "merge conflicts",
            "changes requested",
            "CI failing",
            "unresolved comments (1)",
            "draft",
        ]


class TestCategorizeMyPr:
    def test_merged_is_done(self) -> None:
        assert categorize_my_pr(_pr(state="merged")) == "done"

    def test_closed_is_done(self) -> None:
        assert categorize_my_pr(_pr(state="closed")) == "done"

    def test_conflicts_is_needs_action(self) -> None:
        assert categorize_my_pr(_pr(mergeable="CONFLICTING")) == "needs_action"

    def test_changes_requested_is_needs_action(self) -> None:
        pr = _pr(review_status="CHANGES_REQUESTED")
        assert categorize_my_pr(pr) == "needs_action"

    def test_draft_is_needs_action(self) -> None:
        assert categorize_my_pr(_pr(is_draft=True)) == "needs_action"

    def test_clean_open_pr_is_waiting(self) -> None:
        assert categorize_my_pr(_pr()) == "waiting_for_review"

    def test_open_with_reviewer_is_waiting(self) -> None:
        pr = _pr(reviewers=[Reviewer(login="bob", state="PENDING")])
        assert categorize_my_pr(pr) == "waiting_for_review"

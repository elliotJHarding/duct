"""Tests for pr_render shared rendering helpers.

Focus is on the ticket-key stripping regex and the compact/expanded layout
selection. Rendered-text assertions use `.plain` so they're independent of
style metadata.
"""

from __future__ import annotations

import pytest
from rich.table import Table
from rich.text import Text

from duct.models import PullRequest, Reviewer
from duct_tui.icons import UNICODE
from duct_tui.widgets.pr_render import (
    render_pr_row,
    strip_leading_ticket,
)


# ---------------------------------------------------------------------------
# strip_leading_ticket
# ---------------------------------------------------------------------------


class TestStripLeadingTicket:
    """The regex needs to handle every PR-title convention we encounter."""

    @pytest.mark.parametrize(
        "title, expected",
        [
            ("PS-123: foo", "foo"),
            ("PS-123 foo", "foo"),
            ("PS-123 - foo", "foo"),
            ("PS-123 — foo", "foo"),
            ("PS-123 – foo", "foo"),
            ("PS-123 | foo", "foo"),
            ("[PS-123] foo", "foo"),
            ("[PS-123]: foo", "foo"),
            ("(PS-123) foo", "foo"),
            ("ERSC-1278: Fix authentication middleware",
             "Fix authentication middleware"),
            # Leading whitespace should be tolerated.
            ("  PS-123: foo", "foo"),
        ],
    )
    def test_strips_known_prefixes(self, title: str, expected: str) -> None:
        assert strip_leading_ticket(title) == expected

    def test_title_without_ticket_key_unchanged(self) -> None:
        assert strip_leading_ticket("Refactor payment flow") == "Refactor payment flow"

    def test_lowercase_key_not_treated_as_ticket(self) -> None:
        # Ticket keys are always upper-case; a lowercase match would be a
        # false positive.
        assert strip_leading_ticket("ps-123: foo") == "ps-123: foo"

    def test_mid_string_key_untouched(self) -> None:
        assert (
            strip_leading_ticket("Fix bug related to PS-123")
            == "Fix bug related to PS-123"
        )


# ---------------------------------------------------------------------------
# render_pr_row layout selection
# ---------------------------------------------------------------------------


def _make_pr(**overrides) -> PullRequest:
    base = dict(
        number=42,
        title="PS-123: Fix thing",
        repo="acme/backend",
        state="open",
        author="alice",
        is_draft=False,
        review_status="pending",
        ci_status="passing",
        url="https://github.com/acme/backend/pull/42",
        created_at="2026-04-01T10:00:00Z",
        updated_at="2026-04-10T10:00:00Z",
        branch="feature/PS-123",
        reviewers=[Reviewer(login="bob", state="APPROVED")],
    )
    base.update(overrides)
    return PullRequest(**base)


class TestRenderPrRowCompact:
    """compact=True (default) preserves the existing 2-line layout."""

    def test_returns_text_renderable(self) -> None:
        result = render_pr_row(_make_pr(), UNICODE, ticket_key="PS-123")
        assert isinstance(result, Text)

    def test_title_ticket_prefix_stripped(self) -> None:
        result = render_pr_row(_make_pr(), UNICODE, ticket_key="PS-123")
        assert "PS-123: Fix thing" not in result.plain
        assert "Fix thing" in result.plain


class TestRenderPrRowExpanded:
    """compact=False produces the 3-line PR-tab layout."""

    def test_returns_text_when_no_avatar(self) -> None:
        result = render_pr_row(
            _make_pr(),
            UNICODE,
            ticket_key="PS-123",
            show_author=True,
            compact=False,
        )
        assert isinstance(result, Text)

    def test_expanded_has_three_content_lines(self) -> None:
        result = render_pr_row(
            _make_pr(),
            UNICODE,
            ticket_key="PS-123",
            show_author=True,
            relative_time_str="2h ago",
            compact=False,
        )
        # Line 1: title, Line 2: repo+author, Line 3: status strip.
        lines = result.plain.split("\n")
        assert len(lines) == 3
        assert "Fix thing" in lines[0]
        assert "backend" in lines[1]
        assert "@alice" in lines[1]
        assert "open" in lines[2]
        assert "2h ago" in lines[2]

    def test_avatar_returned_as_table(self) -> None:
        avatar = Text("AL", style="bold")
        result = render_pr_row(
            _make_pr(),
            UNICODE,
            ticket_key="PS-123",
            show_author=True,
            compact=False,
            avatar=avatar,
        )
        assert isinstance(result, Table)

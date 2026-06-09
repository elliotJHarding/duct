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
    render_collapsed_pr_line,
    render_collapsed_pr_row,
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


class TestRenderCollapsedPrRow:
    """render_collapsed_pr_row: the expanding grid used by OptionList surfaces.

    render_pr_row(compact=False, collapsed=True) routes here too.
    """

    def test_returns_table(self) -> None:
        result = render_collapsed_pr_row(_make_pr(state="merged"), UNICODE)
        assert isinstance(result, Table)

    def test_render_pr_row_collapsed_routes_to_table(self) -> None:
        result = render_pr_row(
            _make_pr(state="merged"),
            UNICODE,
            compact=False,
            collapsed=True,
        )
        assert isinstance(result, Table)

    def _cells(self, **overrides) -> list[Text]:
        # The grid is built with one add_row(num, title, repo, time); the column
        # cells are the Text renderables we passed in.
        table = render_collapsed_pr_row(
            _make_pr(**overrides), UNICODE, relative_time_str="2d ago",
        )
        return [col._cells[0] for col in table.columns]

    def test_columns_carry_number_title_repo_time(self) -> None:
        num, title, repo, rel = self._cells(state="merged")
        assert "#42" in num.plain
        # Leading ticket prefix stripped from the title cell.
        assert "PS-123: Fix thing" not in title.plain
        assert "Fix thing" in title.plain
        assert "backend" in repo.plain
        assert "2d ago" in rel.plain

    def test_merged_number_cell_is_magenta(self) -> None:
        num, *_ = self._cells(state="merged")
        assert "magenta" in str(num.style)

    def test_closed_number_cell_is_red(self) -> None:
        num, *_ = self._cells(state="closed")
        assert "red" in str(num.style)


class TestRenderCollapsedPrLine:
    """render_collapsed_pr_line: the fixed-width single line for card/summary."""

    def _line(self, **overrides) -> Text:
        return render_collapsed_pr_line(
            _make_pr(**overrides),
            UNICODE,
            relative_time_str="2d ago",
            title_width=20,
        )

    def test_single_line(self) -> None:
        assert "\n" not in self._line(state="merged").plain

    def test_shows_number_title_time(self) -> None:
        plain = self._line(state="merged").plain
        assert "#42" in plain
        assert "Fix thing" in plain
        assert "2d ago" in plain

    def test_title_padded_to_fixed_width_for_alignment(self) -> None:
        # A short and a long title must place the trailing time at the same
        # column, so consecutive rows line up.
        short = self._line(state="merged", title="PS-1: Hi")
        long = self._line(
            state="merged", title="PS-1: A considerably longer title than fits",
        )
        assert short.plain.index("2d ago") == long.plain.index("2d ago")

    def test_merged_uses_magenta(self) -> None:
        assert any("magenta" in str(s.style) for s in self._line(state="merged").spans)

    def test_closed_uses_red(self) -> None:
        assert any("red" in str(s.style) for s in self._line(state="closed").spans)

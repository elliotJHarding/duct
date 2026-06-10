"""Tests for pr_render shared rendering helpers.

Focus is on the ticket-key stripping regex and the compact/expanded layout
selection. Rendered-text assertions use `.plain` so they're independent of
style metadata.
"""

from __future__ import annotations

import pytest
from rich.table import Table
from rich.text import Text

from duct.models import PRComment, PullRequest, Reviewer
from duct_tui.icons import UNICODE
from duct_tui.widgets.pr_render import (
    render_collapsed_pr_line,
    render_collapsed_pr_row,
    render_pr_card,
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


class TestRenderPrCard:
    """render_pr_card: the full-detail card used by the ticket-detail pane."""

    def _card(self, **overrides) -> Text:
        kwargs = {
            "relative_time_str": overrides.pop("relative_time_str", "8h ago"),
            "created_time_str": overrides.pop("created_time_str", "3d ago"),
            "action_reasons": overrides.pop("action_reasons", ()),
        }
        return render_pr_card(_make_pr(**overrides), UNICODE, **kwargs)

    def test_title_line_keeps_full_title(self) -> None:
        long_title = "PS-123: A title well past the old forty character cut-off point"
        title_line = self._card(title=long_title).plain.split("\n")[0]
        assert "A title well past the old forty character cut-off point" in title_line

    def test_meta_line_has_repo_branch_arrow_base_and_author(self) -> None:
        plain = self._card(base_branch="main").plain
        meta = plain.split("\n")[1]
        assert "backend" in meta
        assert "feature/PS-123 → main" in meta
        assert "@alice" in meta

    def test_branch_arrow_omitted_without_base_branch(self) -> None:
        assert "→" not in self._card(base_branch="").plain

    def test_long_branch_truncated_but_base_kept(self) -> None:
        plain = self._card(
            branch="feature/AZIE-1593-" + "x" * 80, base_branch="main",
        ).plain
        meta = plain.split("\n")[1]
        assert "… → main" in meta
        assert "x" * 60 not in meta

    def test_status_strip_shows_state_ci_and_ages(self) -> None:
        plain = self._card().plain
        status = plain.split("\n")[2]
        assert "open" in status
        assert "CI" in status
        assert "updated 8h ago" in status
        assert "opened 3d ago" in status

    def test_pending_ci_is_shown(self) -> None:
        assert "CI pending" in self._card(ci_status="pending").plain

    def test_unknown_ci_is_hidden(self) -> None:
        assert "CI" not in self._card(ci_status="unknown", reviewers=[]).plain

    def test_diffstat_rendered_when_present(self) -> None:
        plain = self._card(additions=412, deletions=36, changed_files=12).plain
        assert "+412" in plain
        assert "-36" in plain
        assert "12 files" in plain

    def test_diffstat_omitted_when_zero(self) -> None:
        plain = self._card().plain
        assert "+0" not in plain
        assert "files" not in plain

    def test_single_changed_file_not_pluralised(self) -> None:
        plain = self._card(additions=1, deletions=0, changed_files=1).plain
        assert "1 file" in plain
        assert "1 files" not in plain

    def test_reviewers_block_lists_states_and_requests(self) -> None:
        plain = self._card(
            reviewers=[
                Reviewer(login="bob", state="APPROVED"),
                Reviewer(login="carol", state="COMMENTED"),
            ],
            requested_reviewers=["dave"],
            requested_teams=["org/claims-dev"],
        ).plain
        assert "reviewers" in plain
        assert "@bob approved" in plain
        assert "@carol commented" in plain
        assert "@dave requested" in plain
        assert "@org/claims-dev requested" in plain

    def test_reviewers_block_omitted_when_empty(self) -> None:
        plain = self._card(
            reviewers=[], requested_reviewers=[], requested_teams=[],
        ).plain
        assert "reviewers" not in plain

    def test_action_reasons_rendered_as_warning(self) -> None:
        plain = self._card(
            action_reasons=("merge conflicts", "unresolved comments (2)"),
        ).plain
        assert "merge conflicts, unresolved comments (2)" in plain

    def test_comments_preview_shows_two_most_recent(self) -> None:
        comments = [
            PRComment(author="old", created_at="2026-04-01T10:00:00Z", body="ancient"),
            PRComment(author="bob", created_at="2026-04-09T10:00:00Z", body="middle"),
            PRComment(
                author="carol",
                created_at="2026-04-10T10:00:00Z",
                body="Should this also fire on removal?\nSecond paragraph.",
                path="src/feed/ClaimFeedTrigger.java",
                line=42,
            ),
        ]
        plain = self._card(comments=comments).plain
        assert "ancient" not in plain
        assert "middle" in plain
        assert "@carol" in plain
        # Review comments carry filename:line; body previews only the first line.
        assert "ClaimFeedTrigger.java:42" in plain
        assert "Should this also fire on removal?" in plain
        assert "Second paragraph." not in plain

    def test_long_comment_body_truncated_with_ellipsis(self) -> None:
        comments = [
            PRComment(author="bob", created_at="2026-04-10T10:00:00Z", body="x" * 300),
        ]
        plain = self._card(comments=comments).plain
        assert "…" in plain
        assert "x" * 150 not in plain

    def test_comments_omitted_when_none(self) -> None:
        assert "┆" not in self._card(comments=[]).plain

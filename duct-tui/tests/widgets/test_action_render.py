"""Tests for action_render shared rendering helpers.

The layout pins a coloured ``▎`` rail on pending rows, with a small
type-word tag (``session`` / ``comment`` / ``workflow`` / ``script``) on
the header line. Resolved rows drop the rail and add a status word
(``approved`` / ``rejected`` / ``withdrawn``). Approve/reject hints live
in the panel footer, not on per-row content.
"""

from __future__ import annotations

import pytest

from duct.models import Action
from duct_tui.icons import UNICODE, NERD
from duct_tui.widgets.action_render import (
    _row_label,
    action_type_icon,
    render_action_row,
    render_section_header,
)


def _make_action(**overrides) -> Action:
    defaults = dict(
        id="a-1",
        type="prompt",
        description="Launch review agent",
        status="pending",
        detail={},
        created_at="2026-04-10T10:00:00Z",
        resolved_at=None,
    )
    defaults.update(overrides)
    return Action(**defaults)


class TestRowLabel:
    def test_empty_key_returns_workflow_badge(self):
        label, style = _row_label("")
        assert label == "[workflow]"
        assert "magenta" in style

    def test_ticket_key_returns_key_with_cyan(self):
        label, style = _row_label("PROJ-1")
        assert label == "PROJ-1"
        assert "cyan" in style


class TestActionTypeIconLegacy:
    """``action_type_icon`` is retained for backwards compatibility but is no
    longer consulted by the in-tree renderer. Coverage stays minimal."""

    def test_unknown_type_returns_none(self):
        assert action_type_icon(UNICODE, "unknown") is None
        assert action_type_icon(UNICODE, "") is None

    def test_known_type_returns_glyph_and_colour(self):
        for action_type in ("concrete", "prompt", "improve_workflow"):
            assert action_type_icon(UNICODE, action_type) is not None


class TestRenderSectionHeader:
    def test_contains_label_and_count(self):
        text = render_section_header("Pending", 3)
        plain = text.plain
        assert "Pending" in plain
        assert "3" in plain

    def test_count_styled_bold(self):
        text = render_section_header("Pending", 7)
        plain = text.plain
        for span in text._spans:
            if plain[span.start:span.end].strip() == "7":
                assert "bold" in str(span.style)
                return
        pytest.fail("Count span not found or not styled bold")

    def test_header_uses_long_dash_separator(self):
        text = render_section_header("Resolved", 1)
        plain = text.plain
        assert "── " in plain  # box-drawing dash, not ASCII --


class TestPendingLayout:
    def test_pending_row_without_ticket_starts_with_type_icon(self):
        action = _make_action(type="prompt", status="pending")
        text = render_action_row(action, UNICODE)
        # No ticket pill — the icon is the first thing.
        assert text.plain.startswith(UNICODE.action_prompt)

    def test_pending_header_carries_pill_and_type_word(self):
        action = _make_action(type="prompt", status="pending")
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        # Header is line 1; description goes on line 2+
        header = text.plain.split("\n", 1)[0]
        assert "PROJ-1" in header
        assert UNICODE.action_prompt in header
        assert "session" in header

    @pytest.mark.parametrize("action_type,icon_attr", [
        ("prompt", "action_prompt"),
        ("jira_comment", "action_jira_comment"),
        ("improve_workflow", "action_workflow"),
        ("concrete", "action_concrete"),
    ])
    def test_type_icon_per_type(self, action_type, icon_attr):
        action = _make_action(type=action_type, status="pending")
        for icons in (UNICODE, NERD):
            text = render_action_row(action, icons, ticket_key="PROJ-1")
            assert getattr(icons, icon_attr) in text.plain

    def test_icon_coloured_by_type(self):
        action = _make_action(type="jira_comment", status="pending")
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        # The icon glyph carries the type colour; the pill anchors the row
        # so the icon sits after the pill, not at offset 0.
        glyph = UNICODE.action_jira_comment
        for span in text._spans:
            if glyph in text.plain[span.start:span.end]:
                assert "#7dcfff" in str(span.style)
                return
        pytest.fail("Type-icon span not found")

    def test_description_on_separate_line_from_header(self):
        action = _make_action(
            type="prompt", status="pending", description="Do the thing",
        )
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        lines = text.plain.split("\n")
        assert len(lines) >= 2
        assert "Do the thing" in lines[1]

    def test_pending_row_omits_approve_reject_hint(self):
        """Hint moved to panel footer; per-row content stays clean."""
        action = _make_action(status="pending")
        text = render_action_row(action, UNICODE)
        plain = text.plain
        assert "[y] approve" not in plain
        assert "[n] reject" not in plain

    def test_unknown_type_omits_icon(self):
        action = _make_action(type="plant_a_tree", status="pending")
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        header = text.plain.split("\n", 1)[0]
        # No type-icon glyph for unknown type — header starts with the pill.
        for icon in (
            UNICODE.action_prompt,
            UNICODE.action_jira_comment,
            UNICODE.action_workflow,
            UNICODE.action_concrete,
        ):
            assert icon not in header


class TestColumnPresence:
    def test_ticket_key_none_omits_column(self):
        action = _make_action()
        text = render_action_row(action, UNICODE, ticket_key=None)
        assert "PROJ" not in text.plain
        assert "[workflow]" not in text.plain

    def test_ticket_key_empty_shows_workflow_pill(self):
        action = _make_action()
        text = render_action_row(action, UNICODE, ticket_key="")
        # Workspace pill renders as " workflow " (matches the shape of the
        # canonical ticket badge from render_ticket_badge).
        assert "workflow" in text.plain
        # And it's styled as a pill — bold bright_white on a magenta-ish bg.
        for span in text._spans:
            if "workflow" in text.plain[span.start:span.end]:
                style = str(span.style)
                assert "bright_white" in style and "on" in style
                return
        pytest.fail("workflow pill span not found")

    def test_ticket_key_present_renders_canonical_badge(self):
        """Ticket-key uses the shared badge helper so it matches the rest of the UI."""
        from duct_tui.widgets.ticket_badge import render_ticket_badge
        action = _make_action()
        text = render_action_row(action, UNICODE, ticket_key="PROJ-42")
        badge = render_ticket_badge("PROJ-42")
        # The badge's plain text (with single-space padding) appears verbatim.
        assert badge.plain in text.plain

    def test_ticket_key_present_shows_key(self):
        action = _make_action()
        text = render_action_row(action, UNICODE, ticket_key="PROJ-42")
        assert "PROJ-42" in text.plain


class TestRedundantPrefixStripping:
    def test_leading_em_dash_prefix_stripped(self):
        action = _make_action(description="PROJ-1 — close out the PR")
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        # Body line should not start with "PROJ-1 — "
        body = text.plain.split("\n", 1)[1] if "\n" in text.plain else ""
        assert body.startswith("close out the PR")

    def test_leading_hyphen_prefix_stripped(self):
        action = _make_action(description="PROJ-1 - resume the fix")
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        body = text.plain.split("\n", 1)[1]
        assert body.startswith("resume the fix")

    def test_leading_colon_prefix_stripped(self):
        action = _make_action(description="PROJ-1: post merged-PR comment")
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        body = text.plain.split("\n", 1)[1]
        assert body.startswith("post merged-PR comment")

    def test_description_without_prefix_kept_as_is(self):
        action = _make_action(description="Top up timesheet draft")
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        body = text.plain.split("\n", 1)[1]
        assert body == "Top up timesheet draft"

    def test_workspace_scoped_skips_stripping(self):
        """ticket_key='' (workflow badge) shouldn't strip anything."""
        action = _make_action(description="PROJ-1 — body")
        text = render_action_row(action, UNICODE, ticket_key="")
        assert "PROJ-1 — body" in text.plain


class TestResolvedLayout:
    def test_approved_row_has_no_rail(self):
        action = _make_action(status="approved")
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        assert not text.plain.startswith("▎")

    def test_approved_row_shows_status_word(self):
        action = _make_action(status="approved")
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        assert "approved" in text.plain

    def test_rejected_row_shows_status_word(self):
        action = _make_action(status="rejected")
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        assert "rejected" in text.plain

    def test_resolved_row_description_styled_dim(self):
        action = _make_action(status="approved")
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        for span in text._spans:
            span_text = text.plain[span.start:span.end]
            if "Launch review agent" in span_text:
                assert "dim" in str(span.style)
                return
        pytest.fail("Description span not found or not styled dim")

    def test_rejected_row_shows_feedback_secondary_line(self):
        action = _make_action(status="rejected", feedback="Already handled")
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        assert "Already handled" in text.plain
        assert "↳" in text.plain

    def test_rejected_row_without_feedback_has_no_secondary_line(self):
        action = _make_action(status="rejected")
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        assert "↳" not in text.plain

    def test_withdrawn_row_shows_reason_from_detail(self):
        action = _make_action(
            status="withdrawn",
            detail={"withdrawal_reason": "Ticket closed"},
        )
        text = render_action_row(action, UNICODE, ticket_key="PROJ-1")
        assert "Ticket closed" in text.plain
        assert "withdrawn" in text.plain

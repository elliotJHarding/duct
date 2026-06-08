"""Tests for the FuzzyCombobox filter logic."""

from __future__ import annotations

from duct_tui.widgets.fuzzy_combobox import _subsequence_match


class TestSubsequenceMatch:
    def test_empty_query_matches(self):
        matched, span, positions = _subsequence_match("", "anything")
        assert matched
        assert span == 0
        assert positions == []

    def test_prefix_matches(self):
        matched, _, positions = _subsequence_match("aa", "aa-mocks")
        assert matched
        assert positions == [0, 1]

    def test_subsequence_matches_non_contiguous(self):
        matched, span, positions = _subsequence_match("mk", "aa-mocks")
        assert matched
        # 'm' at 3, 'k' at 6
        assert positions == [3, 6]
        assert span == 3

    def test_case_insensitive(self):
        matched, _, _ = _subsequence_match("ERSC", "ersc-claims")
        assert matched

    def test_no_match(self):
        matched, span, positions = _subsequence_match("xyz", "aa-mocks")
        assert matched is False
        assert span == 0
        assert positions == []

    def test_ordering_matters(self):
        matched, _, _ = _subsequence_match("ab", "ba")
        assert matched is False

    def test_secondary_haystack_reach(self):
        """A query that only appears in the secondary portion still matches."""
        haystack = "aa-mocks acme/aa-mocks"
        matched, _, _ = _subsequence_match("acme", haystack)
        assert matched


class TestComboOption:
    def test_haystack_combines_label_and_secondary(self):
        from duct_tui.widgets.fuzzy_combobox import ComboOption
        opt = ComboOption(value="aa", label="aa-mocks", secondary="acme/aa-mocks")
        assert "aa-mocks" in opt.haystack
        assert "acme" in opt.haystack

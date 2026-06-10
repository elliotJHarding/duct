"""Tests for the FuzzyCombobox filter logic and dropdown visibility."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList

from duct_tui.widgets.fuzzy_combobox import ComboOption, FuzzyCombobox, _subsequence_match


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
        opt = ComboOption(value="aa", label="aa-mocks", secondary="acme/aa-mocks")
        assert "aa-mocks" in opt.haystack
        assert "acme" in opt.haystack


class _ComboApp(App):
    """Combobox next to a plain Input so tests can move focus away."""

    def __init__(self) -> None:
        super().__init__()
        self.selected: list[str] = []

    def compose(self) -> ComposeResult:
        yield FuzzyCombobox(placeholder="repo", id="combo")
        yield Input(id="other")

    def on_fuzzy_combobox_selected(self, event: FuzzyCombobox.Selected) -> None:
        self.selected.append(event.value)


_OPTIONS = [
    ComboOption(value="aa-mocks", label="aa-mocks", secondary="acme/aa-mocks"),
    ComboOption(value="ice-claims", label="ice-claims"),
]


class TestDropdownVisibility:
    """The option list behaves like a dropdown: only open while focused."""

    @pytest.mark.asyncio
    async def test_hidden_until_focused_and_open_while_focused(self):
        app = _ComboApp()
        async with app.run_test() as pilot:
            combo = app.query_one("#combo", FuzzyCombobox)
            combo.set_options(_OPTIONS)
            await pilot.pause()
            assert not combo.query_one(OptionList).display

            combo.focus_input()
            await pilot.pause()
            assert combo.query_one(OptionList).display

    @pytest.mark.asyncio
    async def test_hidden_while_focused_when_nothing_matches(self):
        app = _ComboApp()
        async with app.run_test() as pilot:
            combo = app.query_one("#combo", FuzzyCombobox)
            combo.set_options(_OPTIONS)
            combo.focus_input()
            await pilot.press("x", "y", "z")
            await pilot.pause()
            assert not combo.query_one(OptionList).display

    @pytest.mark.asyncio
    async def test_closes_when_focus_leaves_the_combobox(self):
        app = _ComboApp()
        async with app.run_test() as pilot:
            combo = app.query_one("#combo", FuzzyCombobox)
            combo.set_options(_OPTIONS)
            combo.focus_input()
            await pilot.pause()
            assert combo.query_one(OptionList).display

            app.query_one("#other", Input).focus()
            await pilot.pause()
            assert not combo.query_one(OptionList).display

    @pytest.mark.asyncio
    async def test_enter_commits_highlighted_option(self):
        app = _ComboApp()
        async with app.run_test() as pilot:
            combo = app.query_one("#combo", FuzzyCombobox)
            combo.set_options(_OPTIONS)
            combo.focus_input()
            await pilot.press("i", "c", "e", "enter")
            await pilot.pause()
            assert app.selected == ["ice-claims"]
            assert combo.value == "ice-claims"

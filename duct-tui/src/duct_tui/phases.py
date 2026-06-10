"""Phase mapping — category to visual phase for colour-coding and grouping."""

from __future__ import annotations

from duct_tui.icons import Icons


CATEGORY_TO_PHASE: dict[str, str] = {
    "Active Development": "active",
    "Awaiting Action": "post",
    "In Test": "post",
    "Pre-Development": "pre",
    "Other": "other",
}

DEFAULT_PHASE = "other"

PHASE_SORT_ORDER: dict[str, int] = {"active": 0, "post": 1, "pre": 2, "other": 3}

PHASE_COLORS: dict[str, str] = {
    "active": "#7aa2f7",
    "post": "#e0af68",
    "pre": "#9ece6a",
    "other": "#737aa2",
}


def phase_for_category(category: str) -> str:
    """Map a ticket category string to a phase key."""
    return CATEGORY_TO_PHASE.get(category, DEFAULT_PHASE)


def phase_sort_key(category: str) -> int:
    """Sort order for a category — lower is earlier in the overview."""
    return PHASE_SORT_ORDER.get(phase_for_category(category), 2)


def get_phase_icon(icons: Icons, phase: str) -> str:
    """Return the icon glyph for a phase, respecting the active icon set."""
    return {
        "active": icons.phase_active,
        "post": icons.phase_post,
        "pre": icons.phase_pre,
        "other": icons.phase_other,
    }.get(phase, icons.phase_other)

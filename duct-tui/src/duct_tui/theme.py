"""Custom theme and semantic CSS variables for duct TUI.

Terminal-native approach: inherit the user's terminal colors for backgrounds,
foregrounds, and base UI. Inject duct identity through a single brand accent.
"""

from textual.theme import Theme


def create_duct_theme() -> Theme:
    return Theme(
        name="duct",
        primary="ansi_blue",
        secondary="ansi_cyan",
        accent="#bb9af7",              # Duct brand lavender — the ONE hex color
        foreground="ansi_default",
        background="ansi_default",
        surface="ansi_default",
        panel="ansi_bright_black",     # Subtle border color from terminal palette
        boost="ansi_default",
        success="ansi_green",
        warning="ansi_yellow",
        error="ansi_red",
        dark=True,
        variables={
            "agent-working": "#d97757",
            "agent-ready": "ansi_green",
            "agent-waiting": "ansi_yellow",
            "agent-terminated": "ansi_bright_black",
            "agent-mode-plan": "#48968c",
            "ci-passing": "ansi_green",
            "ci-failing": "ansi_red",
            "ci-pending": "ansi_yellow",
            "attention": "ansi_bright_red",
            "phase-active": "#7aa2f7",
            "phase-post": "#e0af68",
            "phase-pre": "#9ece6a",
            "phase-other": "#737aa2",
            "footer-background": "ansi_default",
            "footer-foreground": "ansi_default",
            "footer-key-foreground": "ansi_magenta",
            "footer-key-background": "transparent",
            "footer-description-foreground": "ansi_default",
            "footer-description-background": "transparent",
            "footer-item-background": "transparent",
        },
    )

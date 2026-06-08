"""Shared renderer for action rows.

Used by both the conduct-tab AllActionsPanel (cross-ticket view, includes a
ticket-key column) and the ticket-tab ActionPanel (implicit ticket context,
no column). Output is ``rich.text.Text`` so the OptionList panels can wrap
it directly in an ``Option``.

Layout — pending rows::

    {ticket-pill | workflow-pill}  {coloured-icon} {type-word}
    {description, possibly wrapped}
      ↳ {optional secondary line}

The pill (canonical ticket-key badge from ``render_ticket_badge``, or a
magenta-bg ``workflow`` pill for workspace-scope) is the row anchor.
A coloured ``Icons.action_*`` glyph + small type-word follows — both
carry the type signal so the user can distinguish ``session`` /
``comment`` / ``workflow`` at a glance, with the icon redundantly
encoding the type for fast pattern recognition.

Layout — resolved rows::

    {ticket-pill}  {status-word}
    {description, dim}
      ↳ {optional secondary line}

No type-icon on resolved rows; the status word (``approved`` /
``rejected`` / ``withdrawn``) carries the per-row state where it varies.
"""

from __future__ import annotations

from rich.text import Text

from duct.models import Action
from duct_tui.icons import Icons
from duct_tui.widgets.ticket_badge import render_ticket_badge


_TYPE_COLOUR: dict[str, str] = {
    "prompt": "#bb9af7",          # duct accent lavender
    "jira_comment": "#7dcfff",    # tokyonight cyan
    "improve_workflow": "magenta",
    "concrete": "cyan",
}

_TYPE_ICON_ATTR: dict[str, str] = {
    "prompt": "action_prompt",
    "jira_comment": "action_jira_comment",
    "improve_workflow": "action_workflow",
    "concrete": "action_concrete",
}

_TYPE_WORD: dict[str, str] = {
    "prompt": "session",
    "jira_comment": "comment",
    "improve_workflow": "workflow",
    "concrete": "script",
}

_STATUS_WORD: dict[str, tuple[str, str]] = {
    # status -> (word, colour)
    "approved": ("approved", "green"),
    "rejected": ("rejected", "red"),
    "withdrawn": ("withdrawn", "bright_black"),
}


# Workspace-scope pill — same shape as render_ticket_badge but a different
# background so a glance distinguishes ticket-scoped from workspace-scoped.
_WORKFLOW_PILL_STYLE = "bold bright_white on #6a2d6a"


def _render_label_pill(key: str) -> Text:
    """Header pill for an action row.

    Non-empty ``key`` → the canonical ticket-key badge (matches the badge
    used in session panels, ticket cards, PR rows). Empty ``key`` → a
    workspace-scope ``workflow`` pill in the same shape.
    """
    if not key:
        return Text(" workflow ", style=_WORKFLOW_PILL_STYLE)
    return render_ticket_badge(key)


def _ticket_column(key: str) -> tuple[str, str]:
    """Legacy label+style tuple. Retained so external callers and tests that
    still want ``[workflow]`` / bold-cyan strings keep working; the in-tree
    renderer now uses ``_render_label_pill`` for actual rendering.
    """
    if not key:
        return "[workflow]", "bold magenta"
    return key, "bold cyan"


_row_label = _ticket_column


def action_type_icon(icons: Icons, action_type: str) -> tuple[str, str] | None:
    """Return ``(glyph, colour)`` for a type, or ``None`` for unknown types."""
    icon_attr = _TYPE_ICON_ATTR.get(action_type)
    if icon_attr is None:
        return None
    return getattr(icons, icon_attr), _TYPE_COLOUR.get(action_type, "")


def render_section_header(label: str, count: int) -> Text:
    """``── Pending  3 ──`` style header. Count rendered bold to draw the eye."""
    text = Text()
    text.append("── ", style="dim cyan")
    text.append(label, style="bold cyan")
    text.append("  ")
    text.append(str(count), style="bold")
    text.append(" ──", style="dim cyan")
    return text


def render_action_row(
    action: Action,
    icons: Icons,
    *,
    ticket_key: str | None = None,
    include_hints: bool = False,  # deprecated; hint lives in the panel footer
) -> Text:
    """Render one styled action row.

    ``ticket_key=None`` suppresses the ticket-key header column (ticket-tab
    panel — the ticket context is implicit). ``ticket_key=""`` renders the
    workspace-scope pill. Any non-empty string renders that key as a pill.
    """
    del include_hints  # kept for API compat

    if action.status == "pending":
        return _render_pending(action, icons, ticket_key)
    return _render_resolved(action, ticket_key)


# ---------------------------------------------------------------- pending


def _render_pending(action: Action, icons: Icons, ticket_key: str | None) -> Text:
    text = Text()

    if ticket_key is not None:
        pill = _render_label_pill(ticket_key)
        text.append_text(pill)
    else:
        pill = None

    icon_info = action_type_icon(icons, action.type)
    type_word = _TYPE_WORD.get(action.type, "")
    # Skip the type-word when the pill literally says the same word —
    # the workspace pill is "workflow" so doubling it as "workflow workflow"
    # is verbal noise. The icon still appears.
    if pill is not None and type_word and type_word.strip() == pill.plain.strip():
        type_word = ""

    if icon_info is not None or type_word:
        if text.plain:
            text.append("  ")
        if icon_info is not None:
            glyph, colour = icon_info
            text.append(glyph, style=colour)
            if type_word:
                text.append(" ")
        if type_word:
            colour = _TYPE_COLOUR.get(action.type, "dim")
            text.append(type_word, style=colour)

    body = _strip_redundant_ticket_prefix(action.description, ticket_key)
    if body:
        if text.plain:
            text.append("\n")
        text.append(body)

    secondary = _secondary_note(action)
    if secondary:
        text.append("\n  ")
        text.append(secondary, style="dim italic")

    return text


# ---------------------------------------------------------------- resolved


def _render_resolved(action: Action, ticket_key: str | None) -> Text:
    text = Text()

    status_word, status_colour = _STATUS_WORD.get(
        action.status, ("", "bright_black"),
    )

    if ticket_key is not None:
        text.append_text(_render_label_pill(ticket_key))
        if status_word:
            text.append("  ")
            text.append(status_word, style=status_colour)
    elif status_word:
        text.append(status_word, style=status_colour)

    body = _strip_redundant_ticket_prefix(action.description, ticket_key)
    if body:
        if text.plain:
            text.append("\n")
        text.append(body, style="dim")

    secondary = _secondary_note(action)
    if secondary:
        text.append("\n  ")
        text.append(secondary, style="dim italic")

    return text


# ---------------------------------------------------------------- helpers


def _strip_redundant_ticket_prefix(description: str, ticket_key: str | None) -> str:
    """Strip a leading ``{KEY} (— | – | - | :) `` if ticket-key is shown as a column.

    The orchestrator emits descriptions like ``AIICE-211 — Jira close-out …``;
    the cross-ticket view already prints the key on the header line. Repeating
    it in the body is verbal noise.
    """
    if not ticket_key:
        return description
    for sep in (" — ", " – ", " - ", ": "):
        prefix = f"{ticket_key}{sep}"
        if description.startswith(prefix):
            return description[len(prefix):]
    return description


def _secondary_note(action: Action) -> str:
    """Short dim caption under a resolved row (rejection or withdrawal reason)."""
    if action.status == "rejected" and action.feedback:
        return f"↳ {action.feedback}"
    if action.status == "withdrawn":
        reason = (action.detail or {}).get("withdrawal_reason")
        if reason:
            return f"↳ withdrawn: {reason}"
        return "↳ withdrawn"
    return ""

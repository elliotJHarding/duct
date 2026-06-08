"""Icon sets — standard Unicode with optional Nerd Font upgrade."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Icons:
    """Icon glyphs used throughout the TUI."""

    # Per-item type indicators
    artifact: str = "\u00b7"         # ·
    repo: str = "\u25aa"             # ▪
    session: str = "\u25cf"          # ●
    pr: str = ""                     # (empty — # prefix is distinctive)

    # Session states (working is covered by the spinner animation)
    session_working: str = "\u25cf"     # ● (fallback — spinner renders for working)
    session_ready: str = "\u25cf"       # ●
    session_waiting: str = "\u25cc"     # ◌
    session_stale: str = "\u231b"       # ⌛ (idle past the stale threshold)
    session_terminated: str = "\u25cb"  # ○
    session_launch: str = "+"           # + (launch new session row)

    # Claude mode indicator (always rendered — one of two)
    mode_plan: str = "\u25c6"           # ◆ (plan mode)
    mode_default: str = "\u2692"        # ⚒ (default / implementing — hammer-and-pick, closest BMP glyph to a wrench)

    # Repo status
    dirty: str = "\u25b3"            # △

    # PR state
    pr_merged: str = "\u2713"        # ✓
    pr_draft: str = "\u25cc"         # ◌
    pr_open: str = "\u25cb"          # ○
    pr_closed: str = "\u2717"        # ✗

    # CI
    ci_pass: str = "\u2713"          # ✓
    ci_fail: str = "\u2717"          # ✗

    # Review
    review_approved: str = "\u2713"  # ✓
    review_changes: str = "\u2717"   # ✗

    # Attention
    warning: str = "\u26a0"          # ⚠

    # Actions — status markers
    action_pending: str = "\u25cf"     # ● (solid dot)
    action_approved: str = "\u2713"    # ✓
    action_rejected: str = "\u2717"    # ✗

    # Actions — type markers
    action_concrete: str = "\u2692"    # ⚒ (scripted action)
    action_prompt: str = "\u276f"      # ❯ (terminal prompt — agent dispatch)
    action_workflow: str = "\u25c7"    # ◇ (workflow improvement)
    action_jira_comment: str = "\u2709"  # ✉ (jira comment)

    # Tasks
    task_todo: str = "\u25cb"        # ○
    task_done: str = "\u2713"        # ✓

    # Workflow phase
    phase_active: str = "\u25b6"     # ▶
    phase_post: str = "\u25a0"       # ■
    phase_pre: str = "\u25cb"        # ○

    # Sync sources (rendered in the top bar)
    source_jira: str = "J"
    source_github: str = "G"
    source_workspace: str = "W"
    source_sessions: str = "S"
    source_ci: str = "C"


UNICODE = Icons()

NERD = Icons(
    artifact="\uf15b",               # nf-fa-file
    repo="\uf07b",                   # nf-fa-folder
    session="\uf489",                # nf-dev-terminal
    pr="\uf407",                     # nf-oct-git_pull_request
    session_working="\uf013",        # nf-fa-cog (fallback — spinner renders for working)
    session_ready="\uf058",          # nf-fa-check_circle
    session_waiting="\uf28c",        # nf-fa-pause_circle
    session_stale="\uf017",          # nf-fa-clock_o
    session_terminated="\uf057",     # nf-fa-times_circle
    session_launch="\uf067",         # nf-fa-plus
    mode_plan="\uf0eb",              # nf-fa-lightbulb_o
    mode_default="\uf0ad",           # nf-fa-wrench
    dirty="\uf044",                  # nf-fa-pencil_square_o
    pr_merged="\uf408",              # nf-oct-git_merge
    pr_draft="\uf040",               # nf-fa-pencil
    pr_open="\u25cb",                # ○ (same as unicode)
    pr_closed="\uf00d",              # nf-fa-times
    ci_pass="\uf00c",                # nf-fa-check
    ci_fail="\uf00d",                # nf-fa-times
    review_approved="\uf00c",        # nf-fa-check
    review_changes="\uf00d",         # nf-fa-times
    warning="\uf071",                # nf-fa-warning
    action_pending="\uf111",         # nf-fa-circle (solid)
    action_approved="\uf00c",        # nf-fa-check
    action_rejected="\uf00d",        # nf-fa-times
    action_concrete="\uf0ad",        # nf-fa-wrench
    action_prompt="\uf120",          # nf-fa-terminal (classic FA range)
    action_workflow="\uf02d",        # nf-fa-book
    action_jira_comment="\uf075",    # nf-fa-comment
    task_todo="\uf10c",              # nf-fa-circle_o
    task_done="\uf00c",              # nf-fa-check
    phase_active="\uf04b",           # nf-fa-play
    phase_post="\uf04d",             # nf-fa-stop
    phase_pre="\uf10c",              # nf-fa-circle_o
    source_jira="\uf188",            # nf-fa-bug (Jira has no BMP glyph)
    source_github="\uf09b",          # nf-fa-github
    source_workspace="\uf07b",       # nf-fa-folder
    source_sessions="\uf489",        # nf-dev-terminal
    source_ci="\uf085",              # nf-fa-cogs
)


def get_icons(nerd_font: bool = False) -> Icons:
    return NERD if nerd_font else UNICODE

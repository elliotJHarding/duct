"""Get-started chapters — the tail of first-time setup.

Three chapters, all built from the user's own live workspace: the first
sync populating the tree (SyncPhase, defined with the setup phases), the
anatomy of one real ticket folder, and adding a repo to a ticket using
the real ``duct workspace`` command output.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from string import Template
from typing import ClassVar

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal
from textual.widgets import Button, Markdown, Static

from duct import paths
from duct.cli.setup_wizard.base import Phase
from duct.cli.setup_wizard.phases import SyncPhase, _explain, _title
from duct.global_state import load_state
from duct.templates import load_template
from duct.workspace import enumerate_ticket_dirs


class TourChapter(Phase):
    """One chapter: rendered template + Back/Continue navigation."""

    group = "Get started"
    template: ClassVar[str] = ""
    continue_label: ClassVar[str] = "Continue"

    @classmethod
    def is_complete(cls, app) -> bool:
        return load_state().tutorial_completed

    def compose(self) -> ComposeResult:
        yield Markdown(self.render_template(self.build_context()), id="chapter-body")
        yield Horizontal(
            Button(self.continue_label, variant="primary", id="continue"),
            Button("Back", id="back"),
            classes="nav-buttons",
        )

    def render_template(self, context: dict[str, str]) -> str:
        text = Template(load_template(f"tutorial/{self.template}"))
        return text.safe_substitute(context)

    def build_context(self) -> dict[str, str]:
        return {}

    def first_ticket(self) -> tuple[str, Path] | None:
        """The synced ticket with the most sync artifacts — the best demo."""
        if self.root is None:
            return None
        best: tuple[str, Path] | None = None
        best_score = -1
        for key, ticket_dir in enumerate_ticket_dirs(self.root):
            orchestrator = ticket_dir / "orchestrator"
            if not (orchestrator / "TICKET.md").exists():
                continue
            score = sum(1 for name in _SYNC_FILES if (orchestrator / name).exists())
            if score > best_score:
                best, best_score = (key, ticket_dir), score
        return best

    @on(Button.Pressed, "#continue")
    def _continue(self) -> None:
        self.advance()

    @on(Button.Pressed, "#back")
    def _back(self) -> None:
        self.go_back()


# ---------------------------------------------------------------------------
# The toolkit folder.
# ---------------------------------------------------------------------------

# One-liners for the files duct maintains in toolkit/. Anything else there
# was authored by the user and travels with the repo.
_TOOLKIT_FILES = {
    "config.yaml": "the settings this wizard just wrote",
    "WORKFLOW.md": "standing policy — the orchestrator reads it every run",
    "CLAUDE.md": "orientation imported into every session",
    "agents/": "reusable session prompts (`duct agent list`)",
    "subagents/": "helper agents, copied into .claude/agents/",
    "wiki/": "curated knowledge base — lessons, conventions, quirks",
    "settings.template.json": "sandbox settings template",
}

# Toolkit entries worth showing even when this workspace doesn't have them.
_PENDING_TOOLKIT_FILES = {
    "agents/": "appears when you save reusable session prompts",
    "wiki/": "not here — wiki disabled; re-run `duct setup` to enable it",
}


def toolkit_anatomy(toolkit: Path) -> str:
    """Annotated real listing of ``toolkit/`` for the tour chapter."""
    known_order = list(_TOOLKIT_FILES)

    def display_name(entry: Path) -> str:
        return entry.name + ("/" if entry.is_dir() else "")

    def rank(entry: Path) -> tuple[int, str]:
        name = display_name(entry)
        position = known_order.index(name) if name in known_order else len(known_order)
        return position, name

    lines = [f"{toolkit.name}/"]
    try:
        entries = sorted(toolkit.iterdir(), key=rank)
    except OSError:
        entries = []
    entries = [e for e in entries if not e.name.startswith(".")]
    rows: list[tuple[str, str]] = []
    for entry in entries:
        name = display_name(entry)
        origin = _TOOLKIT_FILES.get(name, "yours — tracked alongside duct's files")
        rows.append((name, origin))
    present = {name for name, _ in rows}
    for name, when in _PENDING_TOOLKIT_FILES.items():
        if name not in present:
            rows.append((name, f"({when})"))
    # Dotfiles are hidden above, but .git/ is the point of the chapter:
    # toolkit/ is its own repo, tracked and shareable.
    rows.append((".git/", "toolkit/ is its own git repo — tracked and shareable"))
    width = max((len(name) for name, _ in rows), default=0) + 2
    for position, (name, origin) in enumerate(rows):
        branch = "└── " if position == len(rows) - 1 else "├── "
        lines.append(f"{branch}{name:<{width}} {origin}")
    return "\n".join(lines)


class ToolkitTourChapter(TourChapter):
    phase_id = "tour-toolkit"
    title = "The toolkit"
    rail_label = "The toolkit"
    template = "toolkit.md"

    def build_context(self) -> dict[str, str]:
        wiki_enabled = bool(self.cfg and self.cfg.wiki.enabled)
        if wiki_enabled:
            wiki_note = (
                "You enabled the wiki, so `wiki/` will fill with entries as "
                "sessions learn things — corrections, conventions, environment "
                "quirks. `wiki/INDEX.md` lists them all."
            )
        else:
            wiki_note = (
                "You left the wiki off — sessions won't read or write it. If "
                "you later want them to accumulate shared lessons and "
                "conventions, re-run `duct setup` and enable it."
            )
        if self.root is None:
            return {"anatomy": "toolkit/", "wiki_note": wiki_note}
        return {
            "anatomy": toolkit_anatomy(paths.toolkit_dir(self.root)),
            "wiki_note": wiki_note,
        }


# ---------------------------------------------------------------------------
# A ticket, up close.
# ---------------------------------------------------------------------------

# One-liners for the files sync maintains. Anything else in a ticket
# folder was authored by the user or their agents.
_SYNC_FILES = {
    "TICKET.md": "the Jira ticket — status, description, comments",
    "PULL_REQUESTS.md": "PRs that reference this key",
    "CI.md": "CI runs on those PRs",
    "CLAUDE_SESSIONS.md": "Claude Code sessions run on this ticket",
    "WORKSPACE.md": "worktrees and commits in this folder",
    "CLAUDE.md": "ticket-scoped instructions for Claude",
    "EPIC.md": "epic notes, shared by the epic's tickets",
    "attachments": "Jira attachments",
}

# Sync artifacts worth showing even when this ticket doesn't have them yet.
_PENDING_SYNC_FILES = {
    "PULL_REQUESTS.md": "appears when a PR references this key",
    "CI.md": "appears when those PRs run CI",
    "WORKSPACE.md": "appears when you add a repo to this ticket",
    "CLAUDE_SESSIONS.md": "appears when you run Claude here",
}

_TICKET_EXCERPT_LINES = 14


class TicketTourChapter(TourChapter):
    phase_id = "tour-ticket"
    title = "A ticket"
    rail_label = "A ticket"
    template = "ticket.md"

    def build_context(self) -> dict[str, str]:
        found = self.first_ticket()
        if found is None:
            return {
                "intro": (
                    "Sync matched no tickets yet, so this is the shape every "
                    "ticket folder takes (skeleton, not real data):"
                ),
                "anatomy": (
                    "PROJ-123-example-ticket/\n"
                    "└── orchestrator/\n"
                    "    ├── TICKET.md             sync · the Jira ticket\n"
                    "    ├── PULL_REQUESTS.md      sync · PRs that reference this key\n"
                    "    └── RESEARCH.md           yours — sync never touches it"
                ),
                "excerpt": (
                    "---\nsource: sync\n---\n\n# PROJ-123: Example ticket\n\n"
                    "| Status | In Progress |"
                ),
            }
        key, ticket_dir = found
        excerpt_lines = (
            (ticket_dir / "orchestrator" / "TICKET.md")
            .read_text(encoding="utf-8")
            .splitlines()[:_TICKET_EXCERPT_LINES]
        )
        return {
            "intro": f"This is **{key}**, exactly as it sits on disk right now:",
            "anatomy": self._anatomy(ticket_dir),
            "excerpt": "\n".join(excerpt_lines) + "\n…",
        }

    def _anatomy(self, ticket_dir: Path) -> str:
        lines = [f"{ticket_dir.name}/", "└── orchestrator/"]
        orchestrator = ticket_dir / "orchestrator"
        entries = sorted(
            orchestrator.iterdir(), key=lambda p: (p.name not in _SYNC_FILES, p.name),
        )
        entries = [e for e in entries if not e.name.startswith(".")]
        present = {e.name for e in entries}
        rows: list[tuple[str, str]] = []
        for entry in entries:
            name = entry.name + ("/" if entry.is_dir() else "")
            note = _SYNC_FILES.get(entry.name)
            origin = f"sync · {note}" if note else "yours — sync never touches it"
            rows.append((name, origin))
        # Sync artifacts this ticket hasn't earned yet — show what's coming.
        for name, when in _PENDING_SYNC_FILES.items():
            if name not in present:
                rows.append((name, f"(not here yet — {when})"))
        width = max((len(name) for name, _ in rows), default=0) + 2
        for position, (name, origin) in enumerate(rows):
            branch = "└── " if position == len(rows) - 1 else "├── "
            lines.append(f"    {branch}{name:<{width}} {origin}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Add a repo to a ticket.
# ---------------------------------------------------------------------------

_MAX_REPO_ROWS = 14
_MAX_BRANCH_ROWS = 10


def load_repo_data(cfg) -> tuple[list, object | None, list[str]]:
    """(candidates, picked repo, its branches) — the add-repo chapter's data.

    Slow on a cold cache (GitHub org listing + a git fetch for the branch
    list), which is why the app pre-warms it in the background while earlier
    phases run. May raise; callers handle.
    """
    from duct.cli.workspace_cmd import (
        list_branches,
        list_remote_branches,
        list_repo_candidates,
    )

    candidates = list_repo_candidates(cfg)
    if not candidates:
        return [], None, []
    # Prefer a local repo: branch listing works offline.
    pick = next((c for c in candidates if not c.is_remote_only), candidates[0])
    try:
        if pick.local_path is not None:
            with contextlib.redirect_stdout(io.StringIO()):
                branches = list_branches(pick.local_path)
        elif pick.slug:
            branches = list_remote_branches(pick.slug)
        else:
            branches = []
    except Exception:
        branches = []
    return candidates, pick, branches


class AddRepoTourChapter(TourChapter):
    phase_id = "tour-add-repo"
    title = "Add a repo"
    rail_label = "Add a repo"
    template = "add-repo.md"

    def build_context(self) -> dict[str, str]:
        found = self.first_ticket()
        return {
            "example_key": found[0] if found else "PROJ-123",
            "repo_listing": "loading — scanning repoPaths and GitHub orgs…",
            "repo_name": "<repo>",
            "branch_listing": "loading…",
            "base_branch": "<branch>",
            "feature_branch": "feature/<KEY>-<slug>",
        }

    def on_mount(self) -> None:
        self._load_live_data()

    @work(thread=True)
    def _load_live_data(self) -> None:
        from duct.workspace import branch_name

        if self.cfg is None:
            return
        context = self.build_context()
        data = self.wizard.repo_preview
        if data is None:
            try:
                data = load_repo_data(self.cfg)
                self.wizard.repo_preview = data
            except Exception as exc:
                context["repo_listing"] = f"(repo discovery failed: {exc})"
                context["branch_listing"] = "(no repo to list)"
                self.app.call_from_thread(self._rendered, context)
                return
        candidates, pick, branches = data
        if candidates:
            shown = candidates[:_MAX_REPO_ROWS]
            name_width = max(len(c.name) for c in shown)
            rows = []
            for c in shown:
                kind = "remote" if c.is_remote_only else "local "
                location = c.slug if c.is_remote_only else str(c.local_path or "")
                rows.append(
                    f"{kind}  {c.name:<{name_width}}  "
                    f"{c.default_branch or '-':<12}  {location}"
                )
            if len(candidates) > len(shown):
                rows.append(f"… {len(candidates) - len(shown)} more")
            context["repo_listing"] = "\n".join(rows)
            context["repo_name"] = pick.name
            context["base_branch"] = pick.default_branch or "main"
            if branches:
                listing = branches[:_MAX_BRANCH_ROWS]
                if len(branches) > len(listing):
                    listing.append(f"… {len(branches) - len(listing)} more")
                context["branch_listing"] = "\n".join(listing)
            else:
                context["branch_listing"] = "(no branches visible)"
        else:
            context["repo_listing"] = (
                "(no repos discovered — configure repoPaths or githubOrgs)"
            )
            context["branch_listing"] = "(no repo to list)"
        context["feature_branch"] = branch_name(
            context["example_key"], "short-summary", "Story",
        )
        self.app.call_from_thread(self._rendered, context)

    def _rendered(self, context: dict[str, str]) -> None:
        self.query_one("#chapter-body", Markdown).update(self.render_template(context))


# ---------------------------------------------------------------------------
# Command cheat sheet.
# ---------------------------------------------------------------------------

# (section title, [(command, args, one-liner), ...]) — matches `duct --help`.
_COMMAND_SECTIONS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Every day", [
        ("status", "", "dashboard of all tracked work"),
        ("sync", "", "refresh every source now"),
        ("ticket list", "", "the tickets duct tracks"),
        ("pr list", "", "PRs on those tickets"),
    ]),
    ("Work a ticket", [
        ("workspace add-repo", "KEY", "worktree inside the ticket folder"),
        ("session start", "KEY", "Claude Code session on a ticket"),
        ("ticket open", "KEY", "the Jira page in your browser"),
        ("pr review", "", "PRs waiting on your review"),
    ]),
    ("Upkeep", [
        ("doctor", "", "verify config, credentials, tools"),
        ("daemon status", "", "background sync + notifications"),
        ("archive list", "", "finished tickets in .archive/"),
        ("setup", "", "revisit any phase of this wizard"),
    ]),
    ("Explore", [
        ("activity log", "", "your work across Jira/GitHub/git/Claude"),
        ("wiki list", "", "the workspace wiki"),
        ("agent list", "", "workflow agents you can launch"),
        ("orchestrate", "", "orchestrator session (experimental)"),
    ]),
]


class CommandsTourChapter(TourChapter):
    phase_id = "tour-commands"
    title = "Commands"
    rail_label = "Commands"
    continue_label = "Finish"

    def compose(self) -> ComposeResult:
        yield _title("The commands you'll actually use")
        yield _explain(
            "[$primary]duct --help[/] lists the rest, and shell completion "
            "knows them all.",
        )
        with Grid(id="commands-grid"):
            for section_title, commands in _COMMAND_SECTIONS:
                panel = Static(self._section_body(commands), classes="command-panel")
                panel.border_title = section_title
                yield panel
        yield Horizontal(
            Button(self.continue_label, variant="primary", id="continue"),
            Button("Back", id="back"),
            classes="nav-buttons",
        )

    @staticmethod
    def _section_body(commands: list[tuple[str, str, str]]) -> str:
        width = max(len(f"duct {cmd} {args}".rstrip()) for cmd, args, _ in commands) + 2
        lines = []
        for cmd, args, blurb in commands:
            plain = f"duct {cmd} {args}".rstrip()
            padding = " " * (width - len(plain))
            shown = f"[$primary]duct {cmd}[/]" + (f" [dim]{args}[/]" if args else "")
            lines.append(f"{shown}{padding}[$text-muted]{blurb}[/]")
        return "\n".join(lines)


TUTORIAL_PHASES: list[type[Phase]] = [
    SyncPhase,
    ToolkitTourChapter,
    TicketTourChapter,
    AddRepoTourChapter,
    CommandsTourChapter,
]

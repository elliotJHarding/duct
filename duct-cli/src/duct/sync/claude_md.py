"""Per-ticket CLAUDE.md sync — orients Claude Code sessions inside ticket dirs.

Writes ``{ticket_dir}/CLAUDE.md`` for every active ticket. The file uses
Claude Code's ``@file`` import to inline ``orchestrator/TICKET.md`` and lists
the sync-managed artifacts plus any user-created files in ``orchestrator/``
and any cloned repos. The managed region is bounded by markers so user notes
written below the end marker are preserved across syncs.
"""

from __future__ import annotations

import time
from pathlib import Path

from duct.markdown import (
    MANAGED_BLOCK_END,
    MANAGED_BLOCK_START,
    ManagedBlockReseeded,
    update_managed_block,
)
from duct.models import SyncResult
from duct.workspace import enumerate_ticket_dirs, find_repo_dirs

KNOWN_SYNC_ARTIFACTS = (
    ("TICKET.md", "Jira ticket (loaded above via @-import)"),
    ("WORKSPACE.md", "repos in this directory + git status"),
    ("PULL_REQUESTS.md", "open/recent PRs"),
    ("CI.md", "recent CI runs"),
    ("CLAUDE_SESSIONS.md", "prior Claude Code sessions"),
    ("EPIC.md", "parent epic (symlink)"),
)
_KNOWN_NAMES = {name for name, _ in KNOWN_SYNC_ARTIFACTS}

_SEED_TAIL = (
    "\n<!-- Anything below this line is preserved across syncs."
    " Use it for working notes. -->\n"
)


class ClaudeMdSync:
    name = "claude_md"

    def __init__(self, wiki_enabled: bool = False) -> None:
        self.wiki_enabled = wiki_enabled

    def sync(self, root: Path) -> SyncResult:
        start = time.time()
        errors: list[str] = []
        synced = 0

        for key, ticket_dir in enumerate_ticket_dirs(root):
            try:
                self._refresh(ticket_dir)
                synced += 1
            except ManagedBlockReseeded as reseed:
                errors.append(
                    f"{key}: CLAUDE.md backed up to {reseed.backup.name} "
                    "(incomplete managed block); fresh seed written"
                )
                synced += 1
            except Exception as exc:
                errors.append(f"{key}: {exc}")

        return SyncResult(
            source=self.name,
            tickets_synced=synced,
            duration_seconds=time.time() - start,
            errors=errors,
        )

    def _refresh(self, ticket_dir: Path) -> None:
        orch = ticket_dir / "orchestrator"
        working_notes = self._working_notes(orch)
        repos = [p.name for p in find_repo_dirs(ticket_dir)]
        managed = _render_managed(working_notes, repos, self.wiki_enabled)
        update_managed_block(ticket_dir / "CLAUDE.md", managed, seed_tail=_SEED_TAIL)

    @staticmethod
    def _working_notes(orchestrator: Path) -> list[str]:
        if not orchestrator.is_dir():
            return []
        return sorted(
            entry.name
            for entry in orchestrator.iterdir()
            if entry.is_file()
            and entry.suffix == ".md"
            and entry.name not in _KNOWN_NAMES
        )


def _render_managed(
    working_notes: list[str], repos: list[str], wiki_enabled: bool,
) -> str:
    """Render the managed block content (including start/end markers)."""
    lines: list[str] = [
        MANAGED_BLOCK_START,
        "@orchestrator/TICKET.md",
        *(["@../toolkit/wiki/INDEX.md"] if wiki_enabled else []),
        "",
        "# duct ticket workspace",
        "",
        "Sync-managed context (in `orchestrator/`, do not edit — `duct sync` regenerates them):",
        "",
    ]
    lines.extend(f"- `{name}` — {desc}" for name, desc in KNOWN_SYNC_ARTIFACTS)
    lines += [
        "",
        "Any of the above may be absent if the relevant sync hasn't run yet.",
        "",
    ]

    if working_notes:
        lines += [
            "Working notes / artifacts in `orchestrator/` (created during development):",
            "",
        ]
        lines.extend(f"- `{name}`" for name in working_notes)
        lines.append("")

    if repos:
        lines += [
            "Repos in this ticket workspace (branch + status in `orchestrator/WORKSPACE.md`):",
            "",
        ]
        lines.extend(f"- `{name}/`" for name in repos)
        lines.append("")

    lines += _code_and_repos_section(wiki_enabled)
    if wiki_enabled:
        lines += _wiki_section()
    lines += [
        "@../toolkit/WORKFLOW.md",
        MANAGED_BLOCK_END,
    ]
    return "\n".join(lines) + "\n"


def _code_and_repos_section(wiki_enabled: bool) -> list[str]:
    """Lines for the Code & repos section embedded in every managed block."""
    convention_lines = (
        [
            "    2. Translate the fixVersion to a branch name using the team's",
            "       naming convention — consult `../toolkit/wiki/` for client-specific",
            "       conventions (commonly `release/X.Y` or similar).",
        ]
        if wiki_enabled
        else [
            "    2. Translate the fixVersion to a branch name using the team's",
            "       naming convention (commonly `release/X.Y` or similar).",
        ]
    )
    return [
        "## Code & repos",
        "",
        "Any repos listed above are git worktrees attached to this ticket — open",
        "them directly. If the code you need is not in this workspace yet:",
        "",
        "- **See what could be added.** `duct list-repos` shows every repo",
        "  available to this workspace — local (already cloned under a configured",
        "  `repoPaths` entry) and remote (discoverable via configured `githubOrgs`",
        "  and `gh`). Each row includes the repo's default branch.",
        "- **Decide which base branch to fork from.** Do not assume the default",
        "  branch is right — many teams ship from release branches and treat the",
        "  default as integration/trunk. Derive the branch in this order:",
        "    1. Read `fixVersion(s)` on this ticket in `orchestrator/TICKET.md`",
        "       (fall back to `EPIC.md` if the ticket itself has none).",
        *convention_lines,
        "    3. Cross-check `orchestrator/WORKSPACE.md`: sibling repos already",
        "       added to this ticket record the branch they were forked from,",
        "       and a ticket usually keeps branches aligned across its repos.",
        "    4. Run `duct list-branches <repo-name>` to see what's available",
        "       and pick the closest match to the fixVersion.",
        "  Only fall back to the default branch when steps 1–3 yield nothing.",
        "  If still unsure, ask the user — a guessed branch is expensive to",
        "  unwind once work has landed on it.",
        "- **Add a local repo as a worktree.**",
        "  `duct add-repo {KEY} <repo-name> <base-branch>`",
        "- **Add a repo that isn't cloned locally yet.**",
        "  `duct add-repo {KEY} <repo-name> <base-branch> --clone-from <org/repo>`",
        "",
        "Substitute `{KEY}` with the ticket key (the prefix of this directory's",
        "name). After `add-repo` finishes, the new worktree appears as a sibling",
        "directory and shows up in `orchestrator/WORKSPACE.md` after the next",
        "`duct sync`.",
        "",
    ]


def _wiki_section() -> list[str]:
    """Lines for the Wiki section embedded in every managed block."""
    return [
        "## Wiki",
        "",
        "This workspace has a curated wiki at `../toolkit/wiki/` capturing lessons,",
        "conventions, domain knowledge, and environment quirks across sessions.",
        "",
        "**Consult it.** The wiki index is loaded above via `@`-import, so you",
        "already see every entry's name, type, and description.",
        "",
        "Invoke the `wiki-reader` subagent via the Task tool",
        "(`subagent_type: \"wiki-reader\"`) immediately when any of these",
        "happen, before you start the work:",
        "",
        "- **An index entry's name or description looks plausibly relevant.**",
        "  Don't grep — call the reader with a one-line task description. The",
        "  reader deep-reads candidate entry bodies and returns the rules they",
        "  contain.",
        "- You're about to open, retarget, rebase, or backport a PR.",
        "- You're about to write or modify tests.",
        "- You're about to debug a stack trace, an error message, or a failing",
        "  workflow.",
        "- You're about to touch a database migration or schema change.",
        "- You're about to work across multiple repos.",
        "- The user asks \"how does X work?\" / \"why does Y happen?\" about the",
        "  codebase or a client-specific behaviour.",
        "- The user mentions a Jira ticket key for the first time in the session.",
        "",
        "The reader returns a curated briefing in ~300 words drawn from the",
        "relevant entry bodies.",
        "",
        "**Bias toward calling.** Skip only on a literal one-line tweak (typo,",
        "formatting, single value bump). When uncertain, call. A \"no relevant",
        "wiki context\" reply costs ~3s and a few tokens; skipping when the wiki",
        "had the answer costs a multi-day investigation.",
        "",
        "**Do not substitute by greping the index above.** The index shows only",
        "one-line descriptions; the rules live in the entry bodies the reader",
        "deep-reads.",
        "",
        "**If you've already started without consulting — pause and consult now.**",
        "Don't double down.",
        "",
        "**Contribute to it.** Invoke the `wiki-contributor` subagent via the",
        "Task tool (`subagent_type: \"wiki-contributor\"`) immediately when any",
        "of these happen, before the lesson slips out of context:",
        "",
        "- The user corrects you (any \"no, that's not how X works\", \"actually",
        "  we use Y\", etc.).",
        "- You address a PR review comment.",
        "- The user shares non-obvious context up front.",
        "- You discover and fix a build / test / environment issue.",
        "",
        "Also invoke it once before declaring substantive work done — a final",
        "pass to capture anything missed.",
        "",
        "The contributor captures eagerly; most invocations write at least one",
        "entry. The `wiki-maintainer` dedupes and prunes periodically. Do not",
        "edit files in `toolkit/wiki/` yourself — always go through the subagents.",
        "",
    ]

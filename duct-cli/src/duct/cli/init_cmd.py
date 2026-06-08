"""Workspace scaffolding.

The bare ``duct init`` command remains as a hidden, scriptable entry
point so tests and CI can populate a workspace directory without going
through the interactive setup flow. End users hit
:func:`bootstrap_workspace` indirectly via ``duct setup``.
"""

from __future__ import annotations

from pathlib import Path

import click

from duct.cli.output import output, success
from duct.config import SandboxConfig, WorkspaceConfig, load_config, save_config
from duct.sandbox import write_settings
from duct.templates import load_template

_CLAUDE_MD_TEMPLATE = """\
# duct Workspace

This is a duct workspace. See WORKFLOW.md for development lifecycle guidance.

## Structure

- Each ticket has a directory named {KEY}-{slug}/
- Ticket artifacts live in the orchestrator/ subdirectory
- Files with `source: sync` frontmatter are overwritten by sync — do not edit them

## Wiki

This workspace has a curated wiki at `../wiki/` capturing lessons,
conventions, domain knowledge, and environment quirks across sessions.

**Consult it.** At the start of substantive work, glance at
`../wiki/INDEX.md`. For non-trivial work (anything beyond a one-line tweak),
invoke the `wiki-reader` subagent via the Task tool
(`subagent_type: "wiki-reader"`) with a one-line description of your task.
It returns a curated briefing in ~300 words.

**Contribute to it.** Invoke the `wiki-contributor` subagent via the Task
tool (`subagent_type: "wiki-contributor"`) immediately when any of these
happen, before the lesson slips out of context:

- The user corrects you ("no, that's not how X works", "actually we use Y",
  etc.).
- You address a PR review comment.
- The user shares non-obvious context up front.
- You discover and fix a build / test / environment issue.

Also invoke it once before declaring substantive work done — a final pass
to capture anything missed.

The contributor captures eagerly; most invocations write at least one entry.
The `wiki-maintainer` dedupes and prunes periodically. Do not edit files in
`wiki/` yourself — always go through the subagents.
"""

_WIKI_INDEX_TEMPLATE = """\
# Wiki Index

Workspace knowledge base. Entries are written by the `wiki-contributor`
subagent during sessions and consulted by the `wiki-reader` subagent.
The `wiki-maintainer` subagent dedupes and prunes periodically.

## Format

Each entry is `wiki/<name>.md` with frontmatter: `name`, `type` (one of
`lesson` / `convention` / `domain` / `env`), `description`, optional `tags`.
Body sections: Rule, Why, How to apply.

This index lists every entry below. Keep under 200 lines — past that, run
the maintainer (`duct wiki review`).

## Entries

| Name | Type | Description |
|------|------|-------------|
"""

_WIKI_SUBAGENTS = ("wiki-reader", "wiki-contributor", "wiki-maintainer")


def _resolve_root(ctx: click.Context) -> Path:
    root = ctx.obj.get("workspace_root") if ctx.obj else None
    if root:
        if root.startswith("--"):
            raise click.UsageError(
                f"--workspace-root value {root!r} starts with '--'; "
                "a misplaced flag was likely consumed as its value."
            )
        return Path(root).resolve()
    return Path.cwd().resolve()


def _create_if_missing(path: Path, content: str) -> bool:
    """Write *content* to *path* only if the file does not already exist.

    Returns True if the file was created, False if it already existed.
    """
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def bootstrap_workspace(root: Path) -> tuple[list[str], list[str]]:
    """Create the workspace skeleton at *root*. Returns (created, existed)."""
    root.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    existed: list[str] = []

    config_path = root / "config.yaml"
    if not config_path.exists():
        save_config(WorkspaceConfig(root=root), root)
        created.append("config.yaml")
    else:
        existed.append("config.yaml")

    if _create_if_missing(root / "WORKFLOW.md", load_template("WORKFLOW.md")):
        created.append("WORKFLOW.md")
    else:
        existed.append("WORKFLOW.md")

    if _create_if_missing(root / ".claude" / "CLAUDE.md", _CLAUDE_MD_TEMPLATE):
        created.append(".claude/CLAUDE.md")
    else:
        existed.append(".claude/CLAUDE.md")

    for name in _WIKI_SUBAGENTS:
        rel = f".claude/agents/{name}.md"
        body = load_template(f"claude_agents/{name}.md")
        if _create_if_missing(root / rel, body):
            created.append(rel)
        else:
            existed.append(rel)

    if _create_if_missing(root / "wiki" / "INDEX.md", _WIKI_INDEX_TEMPLATE):
        created.append("wiki/INDEX.md")
    else:
        existed.append("wiki/INDEX.md")

    # .claude/settings.json (sandbox config — always written/refreshed)
    write_settings(root, SandboxConfig())
    if ".claude/settings.json" not in created:
        created.append(".claude/settings.json")

    # Bootstrap repo completion cache if repo paths are configured
    try:
        from duct.cli.resolve import write_repo_completion_cache
        from duct.cli.workspace_cmd import discover_repos

        cfg = load_config(root) if config_path.exists() else WorkspaceConfig(root=root)
        names = [name for name, _ in discover_repos(cfg)]
        if names:
            write_repo_completion_cache(root, names)
    except Exception:
        pass

    return created, existed


@click.command(hidden=True)
@click.pass_context
def init(ctx: click.Context) -> None:
    """Create the workspace skeleton without prompts (used by tests and CI).

    Most users should run ``duct`` and follow the guided flow instead.
    """
    root = _resolve_root(ctx)
    created, existed = bootstrap_workspace(root)

    if created:
        output(
            f"Created: {', '.join(created)}",
            data={"created": created, "existed": existed},
        )
    if existed:
        output(
            f"Already existed: {', '.join(existed)}",
            data={"created": created, "existed": existed},
        )

    if created:
        success(f"Workspace initialised at {root}")
    else:
        success("Workspace already fully initialised — nothing to do.")

"""Workspace scaffolding.

The bare ``duct init`` command remains as a hidden, scriptable entry
point so tests and CI can populate a workspace directory without going
through the interactive setup flow. End users hit
:func:`bootstrap_workspace` indirectly via ``duct setup``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import click

from duct import paths
from duct.cli.output import output, success
from duct.config import SandboxConfig, WorkspaceConfig, load_config, save_config
from duct.sandbox import write_settings
from duct.templates import load_template

_CLAUDE_MD_TEMPLATE = """\
# duct Workspace

This is a duct workspace. See `toolkit/WORKFLOW.md` for development lifecycle guidance.

## Structure

- Tracked config + knowledge lives in `toolkit/` (its own git repo)
- Each ticket has a directory named {KEY}-{slug}/ at the workspace root
- Ticket artifacts live in the orchestrator/ subdirectory
- Files with `source: sync` frontmatter are overwritten by sync — do not edit them

## Wiki

This workspace has a curated wiki at `toolkit/wiki/` capturing lessons,
conventions, domain knowledge, and environment quirks across sessions.

**Consult it.** At the start of substantive work, glance at
`toolkit/wiki/INDEX.md`. For non-trivial work (anything beyond a one-line tweak),
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
`toolkit/wiki/` yourself — always go through the subagents.
"""

_WIKI_INDEX_TEMPLATE = """\
# Wiki Index

Workspace knowledge base. Entries are written by the `wiki-contributor`
subagent during sessions and consulted by the `wiki-reader` subagent.
The `wiki-maintainer` subagent dedupes and prunes periodically.

## Format

Each entry is `toolkit/wiki/<name>.md` with frontmatter: `name`, `type` (one of
`lesson` / `convention` / `domain` / `env`), `description`, optional `tags`.
Body sections: Rule, Why, How to apply.

This index lists every entry below. Keep under 200 lines — past that, run
the maintainer (`duct wiki review`).

## Entries

| Name | Type | Description |
|------|------|-------------|
"""

_TOOLKIT_GITIGNORE = ".DS_Store\n"

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


def ensure_toolkit_repo(root: Path) -> bool:
    """Ensure ``toolkit/`` is a git repo with an initial commit.

    Idempotent: a no-op when ``toolkit/.git`` already exists. Shared by
    ``init``, ``setup``, and ``migrate-layout`` so the tracked config repo is
    always present without a separate user step. Best-effort — a missing ``git``
    binary or absent identity never blocks bootstrap.
    """
    toolkit = paths.toolkit_dir(root)
    if (toolkit / ".git").exists():
        return False
    _create_if_missing(toolkit / ".gitignore", _TOOLKIT_GITIGNORE)
    try:
        subprocess.run(["git", "init", "-q"], cwd=toolkit, check=True)
        subprocess.run(["git", "add", "-A"], cwd=toolkit, check=True)
        subprocess.run(
            [
                "git",
                "-c", "user.email=duct@localhost",
                "-c", "user.name=duct",
                "-c", "commit.gpgsign=false",
                "commit", "-q", "-m", "Initialise duct toolkit",
            ],
            cwd=toolkit,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def materialise_root_claude(root: Path) -> None:
    """Regenerate ``{root}/.claude/`` from the tracked ``toolkit/``.

    Claude Code discovers ``CLAUDE.md`` and ``.claude/agents/`` from the
    session launch cwd (the workspace root and ticket-dir ancestors), but the
    canonical copies live in ``toolkit/`` which is not on that path. So we
    materialise generated copies here: a ``CLAUDE.md`` that ``@``-imports the
    toolkit orientation + wiki index, and copies of the wiki subagents.
    Idempotent — safe to re-run on every setup/doctor.
    """
    claude_md = paths.root_claude_md(root)
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    tk = paths.TOOLKIT_DIRNAME
    claude_md.write_text(
        f"@../{tk}/{paths.CLAUDE_MD_FILENAME}\n"
        f"@../{tk}/{paths.WIKI_DIRNAME}/{paths.WIKI_INDEX_FILENAME}\n",
        encoding="utf-8",
    )

    agents_dst = paths.root_claude_agents_dir(root)
    agents_dst.mkdir(parents=True, exist_ok=True)
    for src in sorted(paths.subagents_dir(root).glob("*.md")):
        shutil.copyfile(src, agents_dst / src.name)


def bootstrap_workspace(root: Path) -> tuple[list[str], list[str]]:
    """Create the workspace skeleton at *root*. Returns (created, existed).

    Canonical config + knowledge is written into ``toolkit/`` (a git repo);
    the workspace root then carries a generated ``.claude/`` materialised from
    it. Paths in the returned lists are workspace-root relative.
    """
    root.mkdir(parents=True, exist_ok=True)
    paths.toolkit_dir(root).mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    existed: list[str] = []

    config_path = paths.config_file(root)
    if not config_path.exists():
        save_config(WorkspaceConfig(root=root), root)
        created.append("toolkit/config.yaml")
    else:
        existed.append("toolkit/config.yaml")

    if _create_if_missing(paths.workflow_md(root), load_template("WORKFLOW.md")):
        created.append("toolkit/WORKFLOW.md")
    else:
        existed.append("toolkit/WORKFLOW.md")

    if _create_if_missing(paths.toolkit_claude_md(root), _CLAUDE_MD_TEMPLATE):
        created.append("toolkit/CLAUDE.md")
    else:
        existed.append("toolkit/CLAUDE.md")

    for name in _WIKI_SUBAGENTS:
        body = load_template(f"claude_agents/{name}.md")
        rel = f"toolkit/subagents/{name}.md"
        if _create_if_missing(paths.subagents_dir(root) / f"{name}.md", body):
            created.append(rel)
        else:
            existed.append(rel)

    if _create_if_missing(paths.wiki_index(root), _WIKI_INDEX_TEMPLATE):
        created.append("toolkit/wiki/INDEX.md")
    else:
        existed.append("toolkit/wiki/INDEX.md")

    # Tracked toolkit becomes its own git repo (idempotent, best-effort).
    ensure_toolkit_repo(root)

    # Generated root .claude/ materialised from toolkit (CLAUDE.md + subagents)
    # so Claude Code's cwd-based discovery still finds them.
    materialise_root_claude(root)
    created.append(".claude/CLAUDE.md")

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

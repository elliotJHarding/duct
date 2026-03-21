"""duct init — create workspace skeleton."""

from __future__ import annotations

from pathlib import Path

import click

from duct.cli.output import output, success
from duct.config import SandboxConfig, WorkspaceConfig, load_config, save_config
from duct.sandbox import write_settings
from duct.templates import load_template

_PRIORITY_TEMPLATE = """\
# Priority

<!-- List ticket keys in priority order, one per line. -->
<!-- Both the developer and orchestrator can edit this file. -->
"""

_CLAUDE_MD_TEMPLATE = """\
# duct Workspace

This is a duct workspace. See WORKFLOW.md for development lifecycle guidance.

## Structure

- Each ticket has a directory named {KEY}-{slug}/
- Ticket artifacts live in the orchestrator/ subdirectory
- Files with `source: sync` frontmatter are overwritten by sync — do not edit them
- PRIORITY.md at the workspace root indicates current focus
"""


def _resolve_root(ctx: click.Context) -> Path:
    root = ctx.obj.get("workspace_root") if ctx.obj else None
    return Path(root).resolve() if root else Path.cwd().resolve()


def _create_if_missing(path: Path, content: str) -> bool:
    """Write *content* to *path* only if the file does not already exist.

    Returns True if the file was created, False if it already existed.
    """
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


@click.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Create config.yaml, PRIORITY.md, WORKFLOW.md, and .claude/ directory."""
    root = _resolve_root(ctx)
    root.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    existed: list[str] = []

    # config.yaml
    config_path = root / "config.yaml"
    if not config_path.exists():
        cfg = WorkspaceConfig(root=root)
        save_config(cfg, root)
        created.append("config.yaml")
    else:
        existed.append("config.yaml")

    # PRIORITY.md
    if _create_if_missing(root / "PRIORITY.md", _PRIORITY_TEMPLATE):
        created.append("PRIORITY.md")
    else:
        existed.append("PRIORITY.md")

    # WORKFLOW.md
    if _create_if_missing(root / "WORKFLOW.md", load_template("WORKFLOW.md")):
        created.append("WORKFLOW.md")
    else:
        existed.append("WORKFLOW.md")

    # .claude/CLAUDE.md
    if _create_if_missing(root / ".claude" / "CLAUDE.md", _CLAUDE_MD_TEMPLATE):
        created.append(".claude/CLAUDE.md")
    else:
        existed.append(".claude/CLAUDE.md")

    # .claude/settings.json (sandbox config — always written/refreshed)
    write_settings(root, SandboxConfig())
    if ".claude/settings.json" not in created:
        created.append(".claude/settings.json")

    # Report results
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

    # Post-init guidance
    output("")
    output("[bold]Next steps:[/bold]")
    output("  1. Set your Jira domain:  duct config set jira.domain YOUR_DOMAIN.atlassian.net")
    output("  2. Set environment variables:  export JIRA_EMAIL=... JIRA_TOKEN=...")
    output("  3. (Optional) Set GH_TOKEN or run 'gh auth login' for GitHub sync")
    output("  4. Run your first sync:  duct sync --force")
    output("  5. Check setup:  duct doctor")

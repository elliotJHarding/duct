"""duct agent — list and run workflow agents."""

from __future__ import annotations

import subprocess

import click

from duct.agents import list_agents, load_agent
from duct.cli.output import Col, error, output, success, table
from duct.cli.resolve import resolve_root
from duct.config import ConfigError, load_config
from duct.session import prepare_session


@click.group()
@click.pass_context
def agent(ctx: click.Context) -> None:
    """Manage and launch workflow agents."""
    pass


@agent.command("list")
@click.pass_context
def agent_list(ctx: click.Context) -> None:
    """List available agents."""
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    agents = list_agents(root)
    if not agents:
        output("No agents defined. Add markdown files with frontmatter to agents/.", data=[])
        return

    rows = [[a.name, a.description or "-"] for a in agents]
    json_data = [{"name": a.name, "description": a.description} for a in agents]
    table(
        "Agents",
        [Col("Name", no_wrap=True), "Description"],
        rows,
        data=json_data,
    )


@agent.command("run")
@click.argument("name")
@click.option("--ticket", "-t", required=True, help="Ticket key to scope the agent session to.")
@click.option("--repo", "-r", default=None, help="Run inside a specific repo worktree.")
@click.option(
    "--skip-permissions", is_flag=True,
    help="Pass --dangerously-skip-permissions (requires sandbox).",
)
@click.pass_context
def agent_run(
    ctx: click.Context, name: str, ticket: str, repo: str | None,
    skip_permissions: bool,
) -> None:
    """Launch a Claude Code session using the named agent as its prompt."""
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    target = load_agent(root, name)
    if target is None:
        error(f"Agent '{name}' not found. Run 'duct agent list' to see available agents.")
        ctx.exit(1)
        return

    cfg = load_config(root)
    if skip_permissions and not cfg.sandbox.enabled:
        error("--skip-permissions requires sandbox to be enabled. Set sandbox.enabled in config.yaml.")
        ctx.exit(1)
        return

    try:
        cmd, cwd, prompt_to_send = prepare_session(
            root, ticket, repo=repo, prompt=target.body,
        )
    except FileNotFoundError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    if skip_permissions and "--dangerously-skip-permissions" not in cmd:
        # prepare_session respects cfg.sandbox.skip_permissions; honour the
        # explicit flag too so the CLI flag matches session_start's behaviour.
        cmd.insert(1, "--dangerously-skip-permissions")

    # CLI launch has no pane to send-text into, so fall back to positional
    # argv. Long multi-line prompts can hang claude here — known limitation
    # for the CLI subcommand; the TUI flows use the send-text path instead.
    if prompt_to_send:
        cmd.append(prompt_to_send)

    success(f"Running agent '{name}' for {ticket} in {cwd}")

    try:
        subprocess.run(cmd, cwd=str(cwd), check=False)
    except KeyboardInterrupt:
        output("Session interrupted.")
    except Exception as exc:
        error(f"Failed to launch session: {exc}")
        ctx.exit(1)

"""CLI entry point for duct."""

import importlib

import click

COMMANDS = {
    "activity": "duct.cli.activity_cmd:activity",
    "agent": "duct.cli.agent_cmd:agent",
    "archive": "duct.cli.archive_cmd:archive",
    "completion": "duct.cli.completion_cmd:completion",
    "config": "duct.cli.config_cmd:config",
    "daemon": "duct.cli.daemon_cmd:daemon",
    "doctor": "duct.cli.doctor_cmd:doctor",
    "init": "duct.cli.init_cmd:init",
    "migrate-layout": "duct.cli.migrate_cmd:migrate_layout",
    "orchestrate": "duct.cli.orchestrate_cmd:orchestrate",
    "pr": "duct.cli.pr_cmd:pr",
    "session": "duct.cli.session_cmd:session",
    "setup": "duct.cli.setup_cmd:setup",
    "status": "duct.cli.status_cmd:status",
    "sync": "duct.cli.sync_cmd:sync",
    "ticket": "duct.cli.ticket_cmd:ticket",
    "wiki": "duct.cli.wiki_cmd:wiki",
    "workspace": "duct.cli.workspace_cmd:workspace",
}

# Commands that should not appear in tab completion or `--help` output.
# ``init`` is preserved as a hidden, scriptable entry point so tests and CI
# can scaffold a workspace without going through the interactive flow;
# ``migrate-layout`` is a one-time internal migration, not user-facing.
HIDDEN_COMMANDS = frozenset({"init", "migrate-layout"})


class LazyGroup(click.Group):
    """A click.Group that defers command imports until they are needed.

    During tab completion, only list_commands() is called (returning plain
    strings), so none of the heavy command modules are imported. When an
    actual command is invoked, get_command() imports just that one module.
    """

    def list_commands(self, ctx):
        visible = [c for c in COMMANDS if c not in HIDDEN_COMMANDS]
        return sorted(visible)

    def get_command(self, ctx, cmd_name):
        if cmd_name not in COMMANDS:
            return None
        module_path, attr = COMMANDS[cmd_name].rsplit(":", 1)
        mod = importlib.import_module(module_path)
        return getattr(mod, attr)


def _migrate_credentials_once() -> None:
    """Carry pre-keychain secrets into the OS keychain, once, from the shell.

    Runs on every CLI invocation but is a cheap no-op after the first migration
    (a single keychain read). This must happen in the user's shell — where the
    legacy ``JIRA_*`` env vars and old ``credentials.yaml`` are visible — so the
    launchd daemon can subsequently read the keychain the shell populated.
    """
    import os

    if os.environ.get("_DUCT_COMPLETE"):
        return  # Don't touch the keychain during shell completion.
    try:
        from duct.credentials import migrate_legacy_credentials

        migrate_legacy_credentials()
    except Exception:
        pass  # Best-effort; never block the CLI on migration.


def _state_is_ready() -> bool:
    """True when duct has a workspace and the Jira credentials it needs."""
    from duct.cli.setup_core import state_is_ready

    return state_is_ready()


def _print_bare_status(ctx: click.Context) -> None:
    """Show a short status block + suggested commands when state is ready."""
    from duct.cli.output import output, section, success
    from duct.global_state import load_state

    state = load_state()
    section("duct")
    success(f"Workspace: {state.workspace_path}")
    output("")
    output("Suggested commands:")
    output("  [bold]duct sync[/bold]         refresh tickets, PRs, and CI")
    output("  [bold]duct status[/bold]       workspace overview")
    output("  [bold]duct doctor[/bold]       health check")
    output("  [bold]duct-tui[/bold]          launch the TUI")
    output("  [bold]duct setup[/bold]        re-run the guided flow")


@click.group(cls=LazyGroup, invoke_without_command=True)
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
@click.option("--debug", is_flag=True, help="Show debug information.")
@click.option(
    "--workspace-root",
    type=click.Path(),
    default=None,
    hidden=True,
    help="Override workspace root (escape hatch for tests/CI).",
)
@click.pass_context
def cli(ctx: click.Context, json_output: bool, debug: bool, workspace_root: str | None) -> None:
    """duct — Developer workflow orchestration."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output
    ctx.obj["debug"] = debug
    ctx.obj["workspace_root"] = workspace_root

    _migrate_credentials_once()

    if ctx.invoked_subcommand is not None:
        return

    # Bare `duct` — set up if anything's missing, otherwise show status.
    if _state_is_ready():
        _print_bare_status(ctx)
    else:
        from duct.cli.setup_cmd import run_setup
        run_setup(ctx)

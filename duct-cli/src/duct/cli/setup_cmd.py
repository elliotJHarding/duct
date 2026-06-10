"""duct setup — guided, interactive onboarding.

Two front-ends share the logic in :mod:`duct.cli.setup_core`:

- The full-screen Textual wizard (:mod:`duct.cli.setup_wizard`) — the
  default on an interactive terminal. Configures duct with live previews
  and ends with a workflow tutorial.
- The plain prompt flow in this module — the fallback for unusual
  terminals, forced with ``duct setup --plain``.

The same entry point doubles as the bare-``duct`` first-run path: when
state is incomplete, ``main.cli`` calls :func:`run_setup` directly.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import click

from duct.cli import setup_core
from duct.cli.init_cmd import bootstrap_workspace
from duct.cli.output import output, section, success, warn
from duct.config import WorkspaceConfig, load_config
from duct.credentials import (
    Credentials,
    load_credentials,
    resolve_gh_token,
    resolve_jira_email,
    resolve_jira_token,
    save_credentials,
)
from duct.global_state import set_workspace_path, state_dir

# ---------------------------------------------------------------------------
# Small UI helpers — local to setup so we can tweak phrasing without touching
# the broader output module.
# ---------------------------------------------------------------------------


def _ok(label: str, detail: str = "") -> None:
    suffix = f" [dim]({detail})[/dim]" if detail else ""
    output(f"  [green]✓[/green] {label}{suffix}")


def _fail(label: str, detail: str = "") -> None:
    suffix = f" [dim]-- {detail}[/dim]" if detail else ""
    output(f"  [red]✗[/red] {label}{suffix}")


def _skip(label: str, detail: str = "") -> None:
    suffix = f" [dim]({detail})[/dim]" if detail else ""
    output(f"  [dim]·[/dim] {label}{suffix}")


def _explain(*paragraphs: str) -> None:
    """Print one or more dim paragraphs explaining what the next step does."""
    for para in paragraphs:
        output(f"[dim]{para}[/dim]")
    output("")


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


# ---------------------------------------------------------------------------
# Step: workspace path
# ---------------------------------------------------------------------------


def _step_workspace(default: Path) -> Path:
    section("Step 1 of 8 · Workspace")
    _explain(
        "The workspace is where duct mirrors your Jira tickets as local "
        "folders, one per ticket. We'll create the directory if it doesn't "
        "exist and seed a toolkit/ folder holding config.yaml, WORKFLOW.md, "
        "and agents/; a root .claude/ folder is generated from it.",
        "Press Enter to accept the default, or type a different path.",
    )
    prompt = "Workspace path"
    raw = click.prompt(prompt, default=str(default), show_default=True)
    root = Path(raw).expanduser().resolve()
    created, _existed = bootstrap_workspace(root)
    if created:
        _ok("workspace scaffold created", f"{len(created)} files at {root}")
    else:
        _ok("workspace scaffold ready", str(root))
    set_workspace_path(root)
    return root


# ---------------------------------------------------------------------------
# Step: Jira domain + credentials + live check
# ---------------------------------------------------------------------------


def _step_jira(cfg: WorkspaceConfig, root: Path) -> WorkspaceConfig:
    section("Step 2 of 8 · Jira")
    _explain(
        "duct fetches your tickets via the Jira REST API. We need your "
        "Atlassian domain, your login email, and an API token. The token "
        "stays on this machine — duct stores it in your OS keychain, where "
        "both the shell and the background daemon can read it.",
        f"Create a token at: {setup_core.JIRA_TOKEN_URL}",
    )

    creds = load_credentials()
    existing_email = creds.jira_email or resolve_jira_email()
    existing_token = creds.jira_token or resolve_jira_token()

    if cfg.jira_domain and existing_email and existing_token:
        ok, detail = setup_core.jira_user(cfg.jira_domain, existing_email, existing_token)
        if ok:
            _ok("Jira already configured", f"authenticated as {detail}")
            # Make sure the keychain carries the values forward.
            save_credentials(Credentials(
                jira_email=existing_email,
                jira_token=existing_token,
                gh_token=creds.gh_token,
            ))
            return cfg

    domain = cfg.jira_domain
    email = existing_email or ""
    token = existing_token or ""

    while True:
        domain = click.prompt(
            "Jira domain", default=domain or None,
            show_default=bool(domain),
        ).strip().lower()
        email_default = email or setup_core.git_email_default()
        email = click.prompt(
            "Jira email", default=email_default,
            show_default=bool(email_default),
        ).strip()

        # Token prompt — never echo the existing value as a default.
        if token and click.confirm(
            "  Reuse the saved Jira API token?", default=True,
        ):
            pass  # keep `token` as-is
        else:
            token = click.prompt(
                "Jira API token (input hidden)",
                hide_input=True, default="", show_default=False,
            ).strip()

        ok, detail = setup_core.jira_user(domain, email, token)
        if ok:
            _ok("Jira reachable", f"authenticated as {detail}")
            break
        _fail("Jira auth failed", detail)
        if not click.confirm("  Re-enter Jira details?", default=True):
            warn("Continuing with unverified Jira credentials.")
            break

    cfg = setup_core.update_config(root, jira_domain=domain)
    save_credentials(Credentials(
        jira_email=email, jira_token=token, gh_token=creds.gh_token,
    ))
    return cfg


# ---------------------------------------------------------------------------
# Step: JQL with live count preview
# ---------------------------------------------------------------------------


def _step_jql(cfg: WorkspaceConfig, root: Path) -> WorkspaceConfig:
    section("Step 3 of 8 · Ticket filter (JQL)")
    _explain(
        "JQL controls which tickets duct syncs into your workspace. The "
        "default picks every ticket currently assigned to you that isn't "
        "Done, ordered by most-recently updated. Almost everyone keeps "
        "the default — edit only if you also want to track other "
        "people's tickets or filter by project.",
    )
    output(f"  current: [dim]{cfg.jira_jql}[/dim]")
    if not click.confirm("  Keep this JQL?", default=True):
        new_jql = click.prompt("JQL", default=cfg.jira_jql).strip()
    else:
        new_jql = cfg.jira_jql

    email = resolve_jira_email()
    token = resolve_jira_token()
    if cfg.jira_domain and email and token:
        count = setup_core.jql_count(cfg.jira_domain, email, token, new_jql)
        if count is None:
            warn("Could not verify JQL — the syntax may be wrong; sync will retry.")
        else:
            _ok("JQL valid", f"{count} matching issues")

    if new_jql != cfg.jira_jql:
        cfg = setup_core.update_config(root, jira_jql=new_jql)
    return cfg


# ---------------------------------------------------------------------------
# Step: GitHub auth
# ---------------------------------------------------------------------------


def _step_github(cfg: WorkspaceConfig, root: Path) -> WorkspaceConfig:
    section("Step 4 of 8 · GitHub")
    _explain(
        "duct tracks PRs and CI runs against each ticket by polling the "
        "GitHub API. If you've already run `gh auth login`, we'll borrow "
        "its token. Otherwise paste a personal access token (classic or "
        "fine-grained both work; needs `repo` and `read:org`). Leave "
        "blank to skip — GitHub sync is optional.",
    )

    token = resolve_gh_token()
    if token:
        ok, login, orgs = setup_core.github_user(token)
        if ok:
            _ok("GitHub reachable", f"authenticated as {login}")
        else:
            _fail("Stored GitHub token rejected", login)
            token = ""

    if not token:
        if shutil.which("gh") and click.confirm(
            "  Run `gh auth login` to authenticate GitHub now?", default=True,
        ):
            subprocess.run(["gh", "auth", "login"], check=False)
            token = resolve_gh_token()

    if not token:
        entered = click.prompt(
            "GitHub PAT (leave blank to skip GitHub sync)",
            default="", show_default=False, hide_input=True,
        ).strip()
        if entered:
            token = entered

    if not token:
        warn("GitHub sync is disabled until a token is provided.")
        return cfg

    ok, login, orgs = setup_core.github_user(token)
    if not ok:
        _fail("GitHub auth failed", login)
        return cfg

    _ok("GitHub reachable", f"authenticated as {login}")

    # Persist the token to the keychain only if it came from a prompt (or our
    # `gh auth login` shell-out). Tokens fetched live from `gh auth token` stay
    # there — they're already managed by the gh CLI's own keychain entry.
    creds = load_credentials()
    if not creds.gh_token:
        # Check whether the token came from gh CLI rather than from the keychain/env.
        gh_cli_present = bool(shutil.which("gh"))
        if not gh_cli_present or click.confirm(
            "  Save GitHub token to your OS keychain?", default=True,
        ):
            save_credentials(Credentials(
                jira_email=creds.jira_email,
                jira_token=creds.jira_token,
                gh_token=token,
            ))

    if not orgs:
        _skip("GitHub orgs", "no memberships visible to this token")
        return cfg

    output("")
    output("[bold]Which GitHub orgs should duct watch for PRs?[/bold]")
    output(
        "  [dim]duct will only fetch PRs from the orgs you pick here. "
        "A `*` marks orgs already selected.[/dim]"
    )
    for idx, org in enumerate(orgs, 1):
        marker = "[green]*[/green]" if org in cfg.github_orgs else " "
        output(f"  {marker} {idx}. {org}")
    prompt = "Org numbers to sync (comma-separated; blank keeps current)"
    raw = click.prompt(prompt, default="", show_default=False).strip()
    if raw:
        picked: list[str] = []
        for piece in raw.split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                picked.append(orgs[int(piece) - 1])
            except (ValueError, IndexError):
                warn(f"  ignoring '{piece}'")
        if picked:
            cfg = setup_core.update_config(root, github_orgs=tuple(picked))
            _ok("GitHub orgs set", ", ".join(picked))
    elif cfg.github_orgs:
        _skip("GitHub orgs unchanged", ", ".join(cfg.github_orgs))
    return cfg


# ---------------------------------------------------------------------------
# Step: repo paths
# ---------------------------------------------------------------------------


def _step_repo_paths(cfg: WorkspaceConfig, root: Path) -> WorkspaceConfig:
    section("Step 5 of 8 · Repo paths")
    _explain(
        "duct scans these directories to find local clones of git repos "
        "referenced by tickets. When a ticket touches github.com/acme/foo, "
        "duct expects to find your clone of `foo` under one of these "
        "paths so it can create a worktree for the ticket.",
        "The current defaults are ~/workspace and ~/projects. Drop any "
        "you don't use; add any others you keep code in.",
    )
    current = list(cfg.repo_paths)
    kept: list[Path] = []

    for path in current:
        exists = path.is_dir()
        marker = "[green]✓[/green]" if exists else "[yellow]?[/yellow]"
        prompt = f"  {marker} Keep {path}?" + ("" if exists else " (does not exist)")
        if click.confirm(prompt, default=exists):
            kept.append(path)

    while click.confirm("  Add another repo path?", default=False):
        raw = click.prompt("    Path").strip()
        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            warn(f"    {path} does not exist — added anyway, fix with `duct config add-repo-path`")
        kept.append(path)

    if tuple(str(p) for p in kept) != tuple(str(p) for p in cfg.repo_paths):
        cfg = setup_core.update_config(root, repo_paths=kept)
    _ok("repo paths set", ", ".join(str(p) for p in kept) or "(none)")
    return cfg


# ---------------------------------------------------------------------------
# Step: tools on PATH (non-interactive)
# ---------------------------------------------------------------------------


def _step_tools() -> None:
    section("Step 6 of 8 · External tools")
    _explain(
        "duct shells out to a few external CLIs. `claude` and `git` are "
        "required; `gh` and `mmdc` are optional polish. This step is "
        "read-only — we don't install anything for you.",
    )
    for tool in setup_core.tool_statuses():
        if tool.present:
            _ok(f"{tool.name} on PATH")
        elif tool.required:
            _fail(f"{tool.name} on PATH", tool.hint)
        else:
            _skip(f"{tool.name} on PATH", tool.hint)


# ---------------------------------------------------------------------------
# Step: shell completion (reuse doctor's auto-fix pattern)
# ---------------------------------------------------------------------------


def _step_wiki(cfg: WorkspaceConfig, root: Path) -> WorkspaceConfig:
    section("Step 7 of 8 · Workspace wiki")
    _explain(
        "duct can keep a curated knowledge base in toolkit/wiki/ — lessons "
        "from corrections, project conventions, domain notes, and "
        "environment quirks — written and consulted by three Claude Code "
        "subagents that sessions invoke automatically. Off by default; "
        "sessions run leaner without it. Change it any time by re-running "
        "`duct setup`.",
    )
    enabled = click.confirm(
        "  Enable the workspace wiki?", default=cfg.wiki.enabled,
    )
    cfg = setup_core.set_wiki(root, enabled)
    if enabled:
        _ok("workspace wiki enabled", "toolkit/wiki/")
    else:
        _skip("workspace wiki", "disabled — enable later via `duct setup`")
        if setup_core.toolkit_claude_mentions_wiki(root):
            warn(
                "toolkit/CLAUDE.md still mentions the wiki — duct never edits "
                "user files, so remove that section yourself."
            )
    return cfg


def _step_shell_completion() -> None:
    section("Step 8 of 8 · Shell completion")
    _explain(
        "duct ships with tab-completion for ticket keys, repo names, "
        "session IDs, and agent names. We add a one-line activation hook "
        "to your shell rc so completion is available in new shells.",
    )
    completion = setup_core.shell_completion_status()
    if completion is None:
        _skip("shell completion", "unknown shell")
        return

    if completion.enabled:
        _ok(f"shell completion ({completion.shell_name})", "already enabled")
        return

    output(f"  [yellow]?[/yellow] shell completion ({completion.shell_name}) not enabled")
    output(f"         [dim]would append to {completion.rc_path}:[/dim]")
    output(f"         [dim]{completion.activation}[/dim]")
    if click.confirm("         Apply this?", default=True):
        setup_core.enable_shell_completion(completion)
        _ok(f"shell completion ({completion.shell_name})", f"added to {completion.rc_path.name}")
    else:
        _skip(f"shell completion ({completion.shell_name})", "skipped")


# ---------------------------------------------------------------------------
# Step: background daemon (macOS only)
# ---------------------------------------------------------------------------


def _step_daemon(root: Path) -> None:
    if not setup_core.daemon_supported():
        return

    section("Optional · Background daemon")
    _explain(
        "The duct daemon runs in the background to keep your data fresh, fire "
        "macOS notifications when sessions finish or need input, and run "
        "scheduled orchestrator passes — all without the TUI open. It installs "
        "as a launchd agent that starts automatically at login.",
    )
    if setup_core.daemon_installed():
        _ok("daemon", "already installed")
        return
    if click.confirm("         Install the background daemon now?", default=True):
        try:
            setup_core.install_daemon(root)
            _ok("daemon", "installed and started")
        except Exception as exc:  # noqa: BLE001 — report and continue setup
            _fail("daemon", str(exc))
    else:
        _skip("daemon", "install later with `duct daemon install`")


# ---------------------------------------------------------------------------
# Step: first sync
# ---------------------------------------------------------------------------


def _step_first_sync(ctx: click.Context, cfg: WorkspaceConfig) -> None:
    section("Final · First sync")
    _explain(
        "Sync hits Jira and GitHub now to populate your workspace with "
        "ticket folders, ticket snapshots, and PR/CI data. The duration "
        "scales with how many tickets your JQL matches — usually 30-60s.",
        "Skip to run sync later with `duct sync --force`.",
    )
    if not click.confirm("  Run `duct sync --force` now?", default=True):
        _skip("first sync", "skipped — run `duct sync` when ready")
        return
    from duct.cli.sync_cmd import sync as sync_cmd

    ctx.invoke(sync_cmd, force=True)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def run_setup(ctx: click.Context, plain: bool = False) -> None:
    """Dispatch to the wizard (TTY default) or the plain prompt flow."""
    if not _is_interactive():
        click.echo(
            "duct setup needs an interactive terminal. Re-run from a TTY, "
            "or pre-populate the keychain (via `keyring set duct jira_token`) "
            "and toolkit/config.yaml.",
            err=True,
        )
        ctx.exit(1)
        return

    if not plain:
        from duct.cli.setup_wizard.app import run_wizard

        exit_code = run_wizard()
        if exit_code:
            ctx.exit(exit_code)
        if setup_core.state_is_ready():
            success("duct is ready. Try `duct status` or launch the TUI with `duct-tui`.")
        return

    _run_plain_setup(ctx)


def _run_plain_setup(ctx: click.Context) -> None:
    """Drive the prompt-based flow from start to finish.

    Safe to call when state is partially complete: every step's first action
    is to look at the current world and either skip itself or pick up where
    the last attempt left off.
    """
    output("")
    output("[bold]Welcome to duct.[/bold]")
    output("")
    output(
        "[dim]duct mirrors your Jira tickets as local folders, tracks PRs "
        "and CI runs against each one, and orchestrates Claude Code "
        "sessions per ticket.[/dim]"
    )
    output("")
    output(
        "[dim]This setup has 8 steps plus an optional first sync. Each "
        "prompt has a sensible default — press Enter to accept it. You "
        "can quit at any time; re-run `duct` and we'll pick up where you "
        "left off.[/dim]"
    )
    output("")
    output(f"[dim]State will live in {state_dir()}/.[/dim]")
    output("")

    root = _step_workspace(setup_core.default_workspace())

    cfg = load_config(root)

    cfg = _step_jira(cfg, root)
    cfg = _step_jql(cfg, root)
    cfg = _step_github(cfg, root)
    cfg = _step_repo_paths(cfg, root)
    _step_tools()
    cfg = _step_wiki(cfg, root)
    _step_shell_completion()
    _step_daemon(root)
    _step_first_sync(ctx, cfg)

    section("All set")
    success("duct is ready. Try `duct status` or launch the TUI with `duct-tui`.")


@click.command(name="setup")
@click.option(
    "--plain", is_flag=True,
    help="Use the prompt-based flow instead of the full-screen wizard.",
)
@click.pass_context
def setup(ctx: click.Context, plain: bool) -> None:
    """Walk through every prerequisite duct needs to run.

    Re-runnable — when duct is already configured the wizard opens a phase
    picker so you can revisit any step or re-take the workflow tour.
    """
    run_setup(ctx, plain=plain)

"""duct setup — guided, interactive onboarding.

The flow walks the user through every configuration step the rest of duct
relies on: workspace location, Jira domain + credentials, JQL, GitHub
authentication and orgs, repo paths, tool availability, shell completion,
and an optional first sync. Each step validates against a live source of
truth (an HTTP call, a filesystem check, ``shutil.which``) so the user
sees a green tick the moment a value is good.

The same entry point doubles as the bare-``duct`` first-run path: when
state is incomplete, ``main.cli`` calls :func:`run_setup` directly.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

import click

from duct.cli.init_cmd import bootstrap_workspace
from duct.cli.output import output, section, success, warn
from duct.config import (
    SyncIntervals,
    WorkspaceConfig,
    load_config,
    save_config,
)
from duct.credentials import (
    Credentials,
    load_credentials,
    resolve_gh_token,
    resolve_jira_email,
    resolve_jira_token,
    save_credentials,
)
from duct.global_state import set_workspace_path, state_dir

_JIRA_TOKEN_URL = "https://id.atlassian.com/manage-profile/security/api-tokens"


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
    section("Step 1 of 7 · Workspace")
    _explain(
        "The workspace is where duct mirrors your Jira tickets as local "
        "folders, one per ticket. We'll create the directory if it doesn't "
        "exist and seed a toolkit/ folder holding config.yaml, WORKFLOW.md, "
        "agents/, wiki/, and subagents/; a root .claude/ folder is generated "
        "from it.",
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


def _jira_user(domain: str, email: str, token: str) -> tuple[bool, str]:
    """Probe ``GET /myself`` and return (ok, detail).

    ``detail`` is the display name on success or a short error code on
    failure. Imported lazily so import-time cost stays cheap.
    """
    import httpx

    try:
        credentials = base64.b64encode(f"{email}:{token}".encode()).decode()
        response = httpx.get(
            f"https://{domain}/rest/api/3/myself",
            headers={
                "Authorization": f"Basic {credentials}",
                "Accept": "application/json",
            },
            timeout=10,
        )
    except httpx.HTTPError as exc:
        return False, f"network: {exc.__class__.__name__}"

    if response.status_code == 200:
        return True, response.json().get("displayName") or "unknown"
    if response.status_code == 401:
        return False, "401 — wrong email or token"
    if response.status_code == 404:
        return False, "404 — domain not found"
    return False, f"HTTP {response.status_code}"


def _git_email_default() -> str | None:
    try:
        result = subprocess.run(
            ["git", "config", "--global", "user.email"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _step_jira(cfg: WorkspaceConfig, root: Path) -> WorkspaceConfig:
    section("Step 2 of 7 · Jira")
    _explain(
        "duct fetches your tickets via the Jira REST API. We need your "
        "Atlassian domain, your login email, and an API token. The token "
        "stays on this machine — duct stores it in your OS keychain, where "
        "both the shell and the background daemon can read it.",
        f"Create a token at: {_JIRA_TOKEN_URL}",
    )

    creds = load_credentials()
    existing_email = creds.jira_email or resolve_jira_email()
    existing_token = creds.jira_token or resolve_jira_token()

    if cfg.jira_domain and existing_email and existing_token:
        ok, detail = _jira_user(cfg.jira_domain, existing_email, existing_token)
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
        email_default = email or _git_email_default()
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

        ok, detail = _jira_user(domain, email, token)
        if ok:
            _ok("Jira reachable", f"authenticated as {detail}")
            break
        _fail("Jira auth failed", detail)
        if not click.confirm("  Re-enter Jira details?", default=True):
            warn("Continuing with unverified Jira credentials.")
            break

    cfg = WorkspaceConfig(
        root=cfg.root,
        jira_jql=cfg.jira_jql,
        jira_domain=domain,
        repo_paths=cfg.repo_paths,
        github_orgs=cfg.github_orgs,
        sync_intervals=cfg.sync_intervals,
        sandbox=cfg.sandbox,
        session=cfg.session,
        status=cfg.status,
        session_status=cfg.session_status,
        display=cfg.display,
        notifications=cfg.notifications,
        activity=cfg.activity,
        auto_orchestrate=cfg.auto_orchestrate,
    )
    save_config(cfg, root)
    save_credentials(Credentials(
        jira_email=email, jira_token=token, gh_token=creds.gh_token,
    ))
    return cfg


# ---------------------------------------------------------------------------
# Step: JQL with live count preview
# ---------------------------------------------------------------------------


def _jql_count(domain: str, email: str, token: str, jql: str) -> int | None:
    import httpx

    try:
        credentials = base64.b64encode(f"{email}:{token}".encode()).decode()
        response = httpx.post(
            f"https://{domain}/rest/api/3/search/approximate-count",
            headers={
                "Authorization": f"Basic {credentials}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={"jql": jql},
            timeout=10,
        )
    except httpx.HTTPError:
        return None
    if response.status_code == 200:
        return response.json().get("count")
    return None


def _step_jql(cfg: WorkspaceConfig, root: Path) -> WorkspaceConfig:
    section("Step 3 of 7 · Ticket filter (JQL)")
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
        count = _jql_count(cfg.jira_domain, email, token, new_jql)
        if count is None:
            warn("Could not verify JQL — the syntax may be wrong; sync will retry.")
        else:
            _ok("JQL valid", f"{count} matching issues")

    if new_jql != cfg.jira_jql:
        cfg = WorkspaceConfig(
            root=cfg.root,
            jira_jql=new_jql,
            jira_domain=cfg.jira_domain,
            repo_paths=cfg.repo_paths,
            github_orgs=cfg.github_orgs,
            sync_intervals=cfg.sync_intervals,
            sandbox=cfg.sandbox,
            session=cfg.session,
            status=cfg.status,
            session_status=cfg.session_status,
            display=cfg.display,
            notifications=cfg.notifications,
            activity=cfg.activity,
            auto_orchestrate=cfg.auto_orchestrate,
        )
        save_config(cfg, root)
    return cfg


# ---------------------------------------------------------------------------
# Step: GitHub auth
# ---------------------------------------------------------------------------


def _github_user(token: str) -> tuple[bool, str, list[str]]:
    """Return (ok, login, orgs). ``orgs`` empty on failure."""
    import httpx

    try:
        user_resp = httpx.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        return False, f"network: {exc.__class__.__name__}", []
    if user_resp.status_code != 200:
        return False, f"HTTP {user_resp.status_code}", []

    login = user_resp.json().get("login") or "unknown"
    orgs: list[str] = []
    try:
        org_resp = httpx.get(
            "https://api.github.com/user/orgs",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=10,
        )
        if org_resp.status_code == 200:
            orgs = [o.get("login") for o in org_resp.json() if o.get("login")]
    except httpx.HTTPError:
        pass
    return True, login, orgs


def _step_github(cfg: WorkspaceConfig, root: Path) -> WorkspaceConfig:
    section("Step 4 of 7 · GitHub")
    _explain(
        "duct tracks PRs and CI runs against each ticket by polling the "
        "GitHub API. If you've already run `gh auth login`, we'll borrow "
        "its token. Otherwise paste a personal access token (classic or "
        "fine-grained both work; needs `repo` and `read:org`). Leave "
        "blank to skip — GitHub sync is optional.",
    )

    token = resolve_gh_token()
    if token:
        ok, login, orgs = _github_user(token)
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

    ok, login, orgs = _github_user(token)
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
            cfg = WorkspaceConfig(
                root=cfg.root,
                jira_jql=cfg.jira_jql,
                jira_domain=cfg.jira_domain,
                repo_paths=cfg.repo_paths,
                github_orgs=tuple(picked),
                sync_intervals=cfg.sync_intervals,
                sandbox=cfg.sandbox,
                session=cfg.session,
                status=cfg.status,
                session_status=cfg.session_status,
                display=cfg.display,
                notifications=cfg.notifications,
                activity=cfg.activity,
                auto_orchestrate=cfg.auto_orchestrate,
            )
            save_config(cfg, root)
            _ok("GitHub orgs set", ", ".join(picked))
    elif cfg.github_orgs:
        _skip("GitHub orgs unchanged", ", ".join(cfg.github_orgs))
    return cfg


# ---------------------------------------------------------------------------
# Step: repo paths
# ---------------------------------------------------------------------------


def _step_repo_paths(cfg: WorkspaceConfig, root: Path) -> WorkspaceConfig:
    section("Step 5 of 7 · Repo paths")
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
        cfg = WorkspaceConfig(
            root=cfg.root,
            jira_jql=cfg.jira_jql,
            jira_domain=cfg.jira_domain,
            repo_paths=kept,
            github_orgs=cfg.github_orgs,
            sync_intervals=cfg.sync_intervals,
            sandbox=cfg.sandbox,
            session=cfg.session,
            status=cfg.status,
            session_status=cfg.session_status,
            display=cfg.display,
            notifications=cfg.notifications,
            activity=cfg.activity,
            auto_orchestrate=cfg.auto_orchestrate,
        )
        save_config(cfg, root)
    _ok("repo paths set", ", ".join(str(p) for p in kept) or "(none)")
    return cfg


# ---------------------------------------------------------------------------
# Step: tools on PATH (non-interactive)
# ---------------------------------------------------------------------------


def _step_tools() -> None:
    section("Step 6 of 7 · External tools")
    _explain(
        "duct shells out to a few external CLIs. `claude` and `git` are "
        "required; `gh` and `mmdc` are optional polish. This step is "
        "read-only — we don't install anything for you.",
    )
    if shutil.which("claude"):
        _ok("claude CLI on PATH")
    else:
        _fail("claude CLI on PATH", "install from https://docs.claude.com/claude-code")
    if shutil.which("git"):
        _ok("git on PATH")
    else:
        _fail("git on PATH", "install git before running sync")
    if shutil.which("gh"):
        _ok("gh CLI on PATH")
    else:
        _skip("gh CLI on PATH", "optional — install for the easiest GitHub auth")
    if shutil.which("mmdc"):
        _ok("mmdc on PATH")
    else:
        _skip("mmdc on PATH", "optional — `npm i -g @mermaid-js/mermaid-cli` for mermaid diagrams")


# ---------------------------------------------------------------------------
# Step: shell completion (reuse doctor's auto-fix pattern)
# ---------------------------------------------------------------------------


def _step_shell_completion() -> None:
    section("Step 7 of 7 · Shell completion")
    _explain(
        "duct ships with tab-completion for ticket keys, repo names, "
        "session IDs, and agent names. We add a one-line activation hook "
        "to your shell rc so completion is available in new shells.",
    )
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        shell_name, rc_path = "zsh", Path.home() / ".zshrc"
        activation = 'autoload -Uz compinit && compinit -C 2>/dev/null; eval "$(_DUCT_COMPLETE=zsh_source duct)"'
    elif "bash" in shell:
        shell_name, rc_path = "bash", Path.home() / ".bashrc"
        activation = 'eval "$(_DUCT_COMPLETE=bash_source duct)"'
    elif "fish" in shell:
        shell_name, rc_path = "fish", Path.home() / ".config" / "fish" / "config.fish"
        activation = '_DUCT_COMPLETE=fish_source duct | source'
    else:
        _skip("shell completion", "unknown shell")
        return

    rc_content = rc_path.read_text() if rc_path.exists() else ""
    if "_DUCT_COMPLETE" in rc_content:
        _ok(f"shell completion ({shell_name})", "already enabled")
        return

    output(f"  [yellow]?[/yellow] shell completion ({shell_name}) not enabled")
    output(f"         [dim]would append to {rc_path}:[/dim]")
    output(f"         [dim]{activation}[/dim]")
    if click.confirm("         Apply this?", default=True):
        rc_path.parent.mkdir(parents=True, exist_ok=True)
        with open(rc_path, "a") as f:
            f.write(f"\n{activation}\n")
        _ok(f"shell completion ({shell_name})", f"added to {rc_path.name}")
    else:
        _skip(f"shell completion ({shell_name})", "skipped")


# ---------------------------------------------------------------------------
# Step: background daemon (macOS only)
# ---------------------------------------------------------------------------


def _step_daemon(root: Path) -> None:
    if sys.platform != "darwin":
        return
    from duct.cli.daemon_cmd import install_agent, is_installed

    section("Optional · Background daemon")
    _explain(
        "The duct daemon runs in the background to keep your data fresh, fire "
        "macOS notifications when sessions finish or need input, and run "
        "scheduled orchestrator passes — all without the TUI open. It installs "
        "as a launchd agent that starts automatically at login.",
    )
    if is_installed():
        _ok("daemon", "already installed")
        return
    if click.confirm("         Install the background daemon now?", default=True):
        try:
            install_agent(root)
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
# Entry point
# ---------------------------------------------------------------------------


def run_setup(ctx: click.Context) -> None:
    """Drive the guided flow from start to finish.

    Safe to call when state is partially complete: every step's first action
    is to look at the current world and either skip itself or pick up where
    the last attempt left off.
    """
    if not _is_interactive():
        click.echo(
            "duct setup needs an interactive terminal. Re-run from a TTY, "
            "or pre-populate the keychain (via `keyring set duct jira_token`) "
            "and toolkit/config.yaml.",
            err=True,
        )
        ctx.exit(1)
        return

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
        "[dim]This setup has 7 steps plus an optional first sync. Each "
        "prompt has a sensible default — press Enter to accept it. You "
        "can quit at any time; re-run `duct` and we'll pick up where you "
        "left off.[/dim]"
    )
    output("")
    output(f"[dim]State will live in {state_dir()}/.[/dim]")
    output("")

    default_workspace = Path.home() / "workspace" / "duct"
    root = _step_workspace(default_workspace)

    cfg = load_config(root)
    if cfg.sync_intervals is None:  # pragma: no cover — defensive
        cfg = WorkspaceConfig(
            root=cfg.root, jira_domain=cfg.jira_domain,
            sync_intervals=SyncIntervals(),
        )

    cfg = _step_jira(cfg, root)
    cfg = _step_jql(cfg, root)
    cfg = _step_github(cfg, root)
    cfg = _step_repo_paths(cfg, root)
    _step_tools()
    _step_shell_completion()
    _step_daemon(root)
    _step_first_sync(ctx, cfg)

    section("All set")
    success("duct is ready. Try `duct status` or launch the TUI with `duct-tui`.")


@click.command(name="setup")
@click.pass_context
def setup(ctx: click.Context) -> None:
    """Walk through every prerequisite duct needs to run.

    Re-runnable — already-configured steps are auto-skipped.
    """
    run_setup(ctx)

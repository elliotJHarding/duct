"""duct workspace -- manage ticket workspaces."""

from __future__ import annotations

import subprocess
from pathlib import Path

import click

from duct.cli.output import Col, error, output, success, table
from duct.cli.resolve import resolve_root, write_repo_completion_cache
from duct.config import ConfigError, WorkspaceConfig, load_config
from duct.workspace import (
    branch_name,
    enumerate_ticket_dirs,
    read_issue_type,
    resolve_ticket_dir,
)


# ---------------------------------------------------------------------------
# Repo discovery helpers
# ---------------------------------------------------------------------------


def _scan_for_repos(
    path: Path, repos: dict[str, Path], depth: int, max_depth: int
) -> None:
    if depth >= max_depth:
        return
    try:
        for child in path.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            if (child / ".git").is_dir():
                repos.setdefault(child.name, child)
            elif not (child / ".git").exists():
                _scan_for_repos(child, repos, depth + 1, max_depth)
    except PermissionError:
        pass


def discover_repos(cfg: WorkspaceConfig, max_depth: int = 3) -> list[tuple[str, Path]]:
    """Scan repoPaths recursively for git repos. Returns sorted (name, path) pairs."""
    repos: dict[str, Path] = {}
    for search_path in cfg.repo_paths:
        if not search_path.is_dir():
            continue
        _scan_for_repos(search_path, repos, depth=0, max_depth=max_depth)
    return sorted(repos.items())


def find_repo(cfg: WorkspaceConfig, repo_name: str) -> Path | None:
    """Find a single repo by name in configured repoPaths."""
    for name, path in discover_repos(cfg):
        if name == repo_name:
            return path
    return None


# ---------------------------------------------------------------------------
# Branch listing
# ---------------------------------------------------------------------------


def list_branches(repo_path: Path) -> list[str]:
    """Return deduplicated branch names (local + remote, stripped of origin/ prefix)."""
    result = subprocess.run(
        ["git", "branch", "-a", "--format=%(refname:short)"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    branches: set[str] = set()
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("origin/"):
            line = line[len("origin/"):]
        if line and line != "HEAD":
            branches.add(line)
    return sorted(branches)


# ---------------------------------------------------------------------------
# Interactive fuzzy selection
# ---------------------------------------------------------------------------


def _fuzzy_select(prompt: str, choices: list[str]) -> str | None:
    """Prompt user with a fuzzy-searchable autocomplete list."""
    import questionary
    from prompt_toolkit.completion import FuzzyWordCompleter
    from prompt_toolkit.styles import Style as PtStyle, merge_styles

    # prompt_toolkit styles keyed to the exact class names used by the
    # completion menu and its fuzzy-match highlighting.
    pt_style = PtStyle.from_dict({
        # Menu background and normal items
        "completion-menu": "bg:#1a1a2e #e0e0e0",
        "completion-menu.completion": "bg:#1a1a2e #e0e0e0",
        # Currently selected item
        "completion-menu.completion.current": "bg:#0097a7 #ffffff bold",
        # Fuzzy-match: non-matched characters in normal items
        "completion-menu.completion fuzzymatch.outside": "fg:#a0a0a0",
        # Fuzzy-match: matched characters in normal items
        "completion-menu.completion fuzzymatch.inside": "fg:#ffffff bold",
        "completion-menu.completion fuzzymatch.inside.character": "fg:#ffffff bold underline",
        # Fuzzy-match: characters in the selected item
        "completion-menu.completion.current fuzzymatch.outside": "fg:#e0e0e0",
        "completion-menu.completion.current fuzzymatch.inside": "fg:#ffffff bold",
        # Scrollbar
        "scrollbar.background": "bg:#1a1a2e",
        "scrollbar.button": "bg:#0097a7",
    })

    # questionary styles for the prompt line itself
    q_style = questionary.Style([
        ("qmark", "fg:ansicyan bold"),
        ("question", "bold"),
        ("answer", "fg:ansicyan"),
    ])

    style = merge_styles([q_style, pt_style])
    completer = FuzzyWordCompleter(choices, WORD=True)

    result = questionary.autocomplete(
        f"{prompt}:",
        choices=choices,
        completer=completer,
        style=style,
        validate=lambda val: val in choices or "Please select from the list",
    ).ask()
    return result


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
@click.pass_context
def workspace(ctx: click.Context) -> None:
    """Manage ticket workspaces."""
    pass


@click.command("add-repo")
@click.argument("key", required=False)
@click.argument("repo_name", required=False)
@click.argument("basebranch", required=False)
@click.option("--branch", default=None, help="Override auto-generated feature branch name.")
@click.pass_context
def add_repo(
    ctx: click.Context,
    key: str | None,
    repo_name: str | None,
    basebranch: str | None,
    branch: str | None,
) -> None:
    """Add a repo worktree to a ticket workspace.

    All arguments are optional. Missing arguments trigger interactive fuzzy
    search prompts.

    \b
    Examples:
        duct add-repo                           # fully interactive
        duct add-repo ERSC-1278                 # prompts for repo and base branch
        duct add-repo ERSC-1278 ice-claims main # no prompts
    """
    try:
        root = resolve_root(ctx)
        cfg = load_config(root)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    # -- Resolve KEY interactively if missing --
    if not key:
        tickets = enumerate_ticket_dirs(root)
        if not tickets:
            error("No ticket directories found. Run 'duct sync' first.")
            ctx.exit(1)
            return
        choices = [f"{k} {p.name.partition('-')[2]}" for k, p in tickets]
        selection = _fuzzy_select("Ticket", choices)
        if not selection:
            ctx.exit(1)
            return
        key = selection.split()[0]

    ticket_dir = resolve_ticket_dir(root, key)
    if not ticket_dir:
        error(f"No workspace found for {key}. Run 'duct sync --force' to create ticket directories.")
        ctx.exit(1)
        return

    # -- Resolve REPO interactively if missing --
    repos = discover_repos(cfg)
    repo_names = [name for name, _ in repos]

    if not repo_name:
        if not repos:
            error("No git repositories found in configured repoPaths.")
            ctx.exit(1)
            return
        repo_name = _fuzzy_select("Repository", repo_names)
        if not repo_name:
            ctx.exit(1)
            return

    repo_path = find_repo(cfg, repo_name)
    if not repo_path:
        msg = f"Repository '{repo_name}' not found."
        if repo_names:
            msg += f"\nAvailable repos: {', '.join(repo_names)}"
        msg += f"\nSearch paths (repoPaths): {', '.join(str(p) for p in cfg.repo_paths)}"
        msg += "\nAdd a search path: duct config add-repo-path <dir>"
        error(msg)
        ctx.exit(1)
        return

    # -- Resolve BASEBRANCH interactively if missing --
    if not basebranch:
        branches = list_branches(repo_path)
        if not branches:
            error(f"No branches found in {repo_path}.")
            ctx.exit(1)
            return
        basebranch = _fuzzy_select("Base branch", branches)
        if not basebranch:
            ctx.exit(1)
            return

    # -- Create worktree --
    worktree_path = ticket_dir / repo_name
    if branch:
        feature_branch = branch
    else:
        # Extract summary slug from ticket dir name (already formatted as KEY-slug)
        summary_slug = ticket_dir.name[len(key) + 1:]
        issue_type = read_issue_type(ticket_dir)
        feature_branch = branch_name(key, summary_slug, issue_type)

    try:
        result = subprocess.run(
            [
                "git", "worktree", "add",
                str(worktree_path), "-b", feature_branch,
                basebranch, "--no-track",
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            # Branch may already exist — try without -b
            result = subprocess.run(
                ["git", "worktree", "add", str(worktree_path), feature_branch],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                error(f"Failed to create worktree: {result.stderr}")
                ctx.exit(1)
                return

        if cfg.sandbox.enabled:
            from duct.sandbox import write_settings

            write_settings(worktree_path, cfg.sandbox)

        # Refresh repo completion cache
        try:
            names = [name for name, _ in discover_repos(cfg)]
            write_repo_completion_cache(root, names)
        except Exception:
            pass

        success(
            f"Added worktree for {repo_name} at {worktree_path} "
            f"(branch: {feature_branch} from {basebranch})"
        )
    except Exception as exc:
        error(f"Failed to create worktree: {exc}")
        ctx.exit(1)


# Register add-repo under the workspace group as well (alias)
workspace.add_command(add_repo, "add-repo")


@workspace.command("status")
@click.pass_context
def workspace_status(ctx: click.Context) -> None:
    """Show workspace health across all tickets."""
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    tickets = enumerate_ticket_dirs(root)
    if not tickets:
        output("No tickets found in workspace.")
        return

    rows = []
    data_list = []
    for key, path in tickets:
        orch = path / "orchestrator"
        artifacts = []
        if orch.is_dir():
            artifacts = [f.name for f in sorted(orch.iterdir()) if f.is_file()]

        repos = [
            d.name
            for d in sorted(path.iterdir())
            if d.is_dir() and d.name != "orchestrator" and (d / ".git").exists()
        ]

        rows.append([key, str(len(artifacts)), str(len(repos)), str(path)])
        data_list.append({
            "key": key,
            "artifacts": artifacts,
            "repos": repos,
            "path": str(path),
        })

    columns: list[str | Col] = [
        "Key",
        Col("Artifacts", justify="right"),
        Col("Repos", justify="right"),
        Col("Path", max_width=50),
    ]
    table("Workspace Status", columns, rows, data=data_list)


@workspace.command("path")
@click.argument("key")
@click.pass_context
def workspace_path(ctx: click.Context, key: str) -> None:
    """Print the workspace path for a ticket. Useful for shell integration:
    cd $(duct workspace path KEY)
    """
    try:
        root = resolve_root(ctx)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    ticket_dir = resolve_ticket_dir(root, key)
    if not ticket_dir:
        error(f"No workspace found for {key}.")
        ctx.exit(1)
        return

    # Print raw path (no formatting) for shell consumption
    if ctx.obj and ctx.obj.get("json"):
        output("", data={"key": key, "path": str(ticket_dir)})
    else:
        click.echo(str(ticket_dir))

"""duct workspace -- manage ticket workspaces."""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import click

from duct import paths
from duct.cli.output import Col, error, get_json_mode, output, success, table
from duct.cli.resolve import resolve_root, write_repo_completion_cache
from duct.config import ConfigError, WorkspaceConfig, load_config
from duct.workspace import (
    branch_name,
    create_worktree,
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


def _local_default_branch(repo_path: Path) -> str | None:
    """Return the upstream HEAD branch for a local repo, or None if not set.

    Reads ``refs/remotes/origin/HEAD`` -- the symref ``git clone`` writes to
    record the remote's default branch. Returns ``None`` for repos without a
    remote or whose HEAD symref hasn't been initialised.
    """
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    if branch.startswith("origin/"):
        branch = branch[len("origin/"):]
    return branch or None


# ---------------------------------------------------------------------------
# Remote repo candidates (github orgs)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoCandidate:
    """A repo that can be added to a ticket workspace.

    ``slug`` is set for repos discovered from a configured GitHub org
    (``owner/name``) so the caller knows a clone is required when
    ``local_path`` is None.
    """

    name: str
    slug: str | None = None
    local_path: Path | None = None
    default_branch: str | None = None

    @property
    def is_remote_only(self) -> bool:
        return self.local_path is None


_ORG_REPOS_TTL_SECONDS = 6 * 60 * 60  # 6h: org repo lists are stable
_ORG_REPOS_FETCH_TIMEOUT = 180  # 862-repo org takes ~30s; leave headroom


def _org_cache_filename(org: str) -> str:
    return f"{org.replace('/', '_')}.json"


def _org_cache_path(workspace_root: Path, org: str) -> Path:
    return paths.gh_org_cache_dir(workspace_root) / _org_cache_filename(org)


def _org_cache_dir(workspace_root: Path) -> Path:
    return paths.gh_org_cache_dir(workspace_root)


def _read_org_cache(
    cache_path: Path, ttl_seconds: int,
) -> list[RepoCandidate] | None:
    """Return cached candidates if the file exists and is within TTL."""
    if not cache_path.is_file():
        return None
    try:
        payload = json.loads(cache_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    fetched_at = payload.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return None
    if time.time() - fetched_at > ttl_seconds:
        return None
    return [
        RepoCandidate(
            name=entry["name"],
            slug=entry.get("slug"),
            default_branch=entry.get("default_branch"),
        )
        for entry in payload.get("candidates", [])
        if entry.get("name")
    ]


def _write_org_cache(cache_path: Path, candidates: list[RepoCandidate]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": time.time(),
        "candidates": [
            {"name": c.name, "slug": c.slug, "default_branch": c.default_branch}
            for c in candidates
        ],
    }
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(cache_path)


def _fetch_org_repos_live(org: str) -> list[RepoCandidate] | None:
    """Fetch every repo in ``org`` via paginated ``gh api``.

    Returns ``None`` if ``gh`` is missing, unauthenticated, or the call fails
    -- callers should fall back to whatever they have (stale cache, empty list).
    The REST endpoint paginates reliably for large orgs where
    ``gh repo list --limit N`` 502s out.
    """
    try:
        result = subprocess.run(
            [
                "gh", "api",
                f"orgs/{org}/repos?per_page=100&type=all",
                "--paginate",
                "--jq", ".[] | {name: .name, slug: .full_name, default_branch: .default_branch}",
            ],
            capture_output=True,
            text=True,
            timeout=_ORG_REPOS_FETCH_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    candidates: list[RepoCandidate] = []
    # --jq emits one JSON object per line.
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = entry.get("name")
        slug = entry.get("slug")
        if not name or not slug:
            continue
        candidates.append(RepoCandidate(
            name=name, slug=slug, default_branch=entry.get("default_branch"),
        ))
    return candidates


def list_org_repos(
    orgs: tuple[str, ...] | list[str],
    cache_dir: Path | None = None,
    refresh: bool = False,
    ttl_seconds: int = _ORG_REPOS_TTL_SECONDS,
) -> list[RepoCandidate]:
    """List repos accessible to the current ``gh`` user in each configured org.

    Returns an empty list if ``gh`` is missing or unauthenticated; warnings
    are swallowed so this never blocks the local-repo path.

    When ``cache_dir`` is provided, results are cached per-org as JSON under
    ``cache_dir/<org>.json`` with a ``ttl_seconds`` lifetime. Pass
    ``refresh=True`` to force a live re-fetch even when a fresh cache exists.
    A stale cache is preferred over an empty list when the live fetch fails
    (better to show yesterday's repos than nothing).
    """
    candidates: list[RepoCandidate] = []
    for org in orgs:
        cache_path = cache_dir / _org_cache_filename(org) if cache_dir else None

        cached: list[RepoCandidate] | None = None
        if cache_path is not None and not refresh:
            cached = _read_org_cache(cache_path, ttl_seconds)
            if cached is not None:
                candidates.extend(cached)
                continue

        live = _fetch_org_repos_live(org)
        if live is not None:
            candidates.extend(live)
            if cache_path is not None:
                try:
                    _write_org_cache(cache_path, live)
                except OSError:
                    pass
            continue

        # Live fetch failed; fall back to whatever cache we have, even if stale.
        if cache_path is not None and cache_path.is_file():
            stale = _read_org_cache(cache_path, ttl_seconds=10**12)
            if stale is not None:
                candidates.extend(stale)

    return candidates


def list_repo_candidates(
    cfg: WorkspaceConfig, refresh: bool = False,
) -> list[RepoCandidate]:
    """Merge local repos (from repoPaths) with remote repos (from githubOrgs).

    Local entries win when the short name collides -- the user can already
    see / use that repo locally, so we preserve the path.

    Remote results are cached per-org under ``<cfg.root>/.cache/gh-org-repos/``
    with a 6h TTL. ``refresh=True`` bypasses the cache.
    """
    locals_by_name: dict[str, RepoCandidate] = {
        name: RepoCandidate(
            name=name,
            local_path=path,
            default_branch=_local_default_branch(path),
        )
        for name, path in discover_repos(cfg)
    }
    cache_dir = _org_cache_dir(cfg.root) if cfg.github_orgs else None
    for remote in list_org_repos(cfg.github_orgs, cache_dir=cache_dir, refresh=refresh):
        if remote.name in locals_by_name:
            # Local already exists; attach the slug so clone-aware callers can
            # still resolve the remote origin if they want to. Prefer the
            # local default_branch (truth on disk) and fall back to the remote.
            existing = locals_by_name[remote.name]
            locals_by_name[remote.name] = RepoCandidate(
                name=existing.name,
                slug=remote.slug,
                local_path=existing.local_path,
                default_branch=existing.default_branch or remote.default_branch,
            )
        else:
            locals_by_name[remote.name] = remote
    return sorted(locals_by_name.values(), key=lambda c: c.name.lower())


def list_remote_branches(slug: str) -> list[str]:
    """Return branch names for ``owner/name`` via ``gh api``.

    Falls back to an empty list on error so callers can degrade gracefully.
    """
    try:
        result = subprocess.run(
            [
                "gh", "api",
                f"repos/{slug}/branches",
                "--paginate",
                "--jq", ".[].name",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    branches = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return sorted(branches)


# ---------------------------------------------------------------------------
# Branch listing
# ---------------------------------------------------------------------------


def _fetch_origin(repo_path: Path) -> str | None:
    """Fetch (with prune) from origin so the branch list reflects the remote.

    Returns ``None`` when there is nothing to report — either the fetch
    succeeded or the repo has no ``origin`` remote (a purely local repo, for
    which a fetch would be meaningless). Returns an error detail string when an
    ``origin`` remote exists but the fetch fails, so the caller can warn that
    the listing may be stale.
    """
    remotes = subprocess.run(
        ["git", "remote"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if "origin" not in remotes.stdout.split():
        return None

    try:
        result = subprocess.run(
            ["git", "fetch", "--prune", "origin"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "fetch timed out"
    if result.returncode == 0:
        return None
    return (result.stderr or result.stdout).strip() or "unknown error"


def list_branches(repo_path: Path) -> list[str]:
    """Return deduplicated branch names (local + remote, stripped of origin/ prefix).

    Fetches with prune from origin first so newly-pushed branches appear and
    branches deleted on origin disappear. Degrades gracefully when offline or
    when the repo has no origin remote.
    """
    fetch_error = _fetch_origin(repo_path)
    if fetch_error is not None:
        click.echo(
            f"warning: could not fetch origin ({fetch_error}); "
            f"branch list may be stale",
            err=True,
        )
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
# Clone helper
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")


def clone_repo(clone_from: str, dest_parent: Path) -> Path:
    """Clone a repo under dest_parent.

    ``clone_from`` is either an ``org/repo`` slug (cloned via ``gh repo clone``)
    or a URL understood by ``git clone``. Returns the path to the cloned repo.
    Raises ``RuntimeError`` on any failure.
    """
    is_slug = bool(_SLUG_RE.match(clone_from))
    if is_slug:
        repo_name = clone_from.split("/", 1)[1]
    else:
        repo_name = clone_from.rstrip("/").rsplit("/", 1)[-1]
        if repo_name.endswith(".git"):
            repo_name = repo_name[: -len(".git")]
    if not repo_name:
        raise RuntimeError(f"Could not derive repo name from '{clone_from}'")

    dest = dest_parent / repo_name
    if dest.exists():
        raise RuntimeError(
            f"Cannot clone into {dest}: directory already exists. "
            f"Remove it or add its parent to repoPaths so it can be discovered."
        )

    cmd = (
        ["gh", "repo", "clone", clone_from, str(dest)]
        if is_slug
        else ["git", "clone", clone_from, str(dest)]
    )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Clone failed ({' '.join(cmd[:2])}): {detail}")
    return dest


# ---------------------------------------------------------------------------
# Interactive fuzzy selection
# ---------------------------------------------------------------------------


def _fuzzy_select(prompt: str, choices: list[str]) -> str | None:
    """Prompt user with a fuzzy-searchable autocomplete list."""
    import questionary
    from prompt_toolkit.completion import FuzzyWordCompleter
    from prompt_toolkit.styles import Style as PtStyle
    from prompt_toolkit.styles import merge_styles

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
@click.option(
    "--clone-from",
    "clone_from",
    default=None,
    metavar="URL_OR_SLUG",
    help=(
        "Clone the repo into the first configured repoPath before adding it, "
        "if it is not already present locally. Accepts 'org/repo' (uses "
        "'gh repo clone') or a git URL (uses 'git clone')."
    ),
)
@click.pass_context
def add_repo(
    ctx: click.Context,
    key: str | None,
    repo_name: str | None,
    basebranch: str | None,
    branch: str | None,
    clone_from: str | None,
) -> None:
    """Add a repo worktree to a ticket workspace.

    All positional arguments are optional. Missing arguments trigger interactive
    fuzzy search prompts.

    \b
    Examples:
        duct add-repo                                                    # fully interactive
        duct add-repo ERSC-1278                                          # prompts for repo and base branch
        duct add-repo ERSC-1278 ice-claims main                          # no prompts
        duct add-repo ERSC-1278 ice-claims main --clone-from org/ice-claims  # clone if missing
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
    if repo_path and clone_from:
        output(
            f"--clone-from ignored: '{repo_name}' is already present at {repo_path}."
        )
    elif not repo_path and clone_from:
        if not cfg.repo_paths:
            error(
                "Cannot clone: no repoPaths configured.\n"
                "Add a search path: duct config add-repo-path <dir>"
            )
            ctx.exit(1)
            return
        dest_parent = cfg.repo_paths[0]
        if not dest_parent.is_dir():
            error(f"Cannot clone: configured repoPath does not exist: {dest_parent}")
            ctx.exit(1)
            return
        output(
            f"Repo '{repo_name}' not found locally; cloning from {clone_from} "
            f"into {dest_parent}..."
        )
        try:
            cloned = clone_repo(clone_from, dest_parent)
        except RuntimeError as exc:
            error(str(exc))
            ctx.exit(1)
            return
        if cloned.name != repo_name:
            error(
                f"Clone produced directory '{cloned.name}' but repo argument was "
                f"'{repo_name}'. Either rename the argument or adjust --clone-from "
                f"so the final path segment matches."
            )
            ctx.exit(1)
            return
        repo_path = find_repo(cfg, repo_name)

    if not repo_path:
        msg = f"Repository '{repo_name}' not found."
        if repo_names:
            msg += f"\nAvailable repos: {', '.join(repo_names)}"
        msg += f"\nSearch paths (repoPaths): {', '.join(str(p) for p in cfg.repo_paths)}"
        msg += "\nAdd a search path: duct config add-repo-path <dir>"
        msg += "\nOr pass --clone-from org/repo to clone it into the first repoPath."
        error(msg)
        ctx.exit(1)
        return

    # -- Resolve BASEBRANCH interactively if missing --
    if not basebranch:
        click.echo("Fetching branches from origin...", err=True)
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
    if branch:
        feature_branch = branch
    else:
        # Extract summary slug from ticket dir name (already formatted as KEY-slug)
        summary_slug = ticket_dir.name[len(key) + 1:]
        issue_type = read_issue_type(ticket_dir)
        feature_branch = branch_name(key, summary_slug, issue_type)

    try:
        worktree_path = create_worktree(
            ticket_dir=ticket_dir,
            repo_path=repo_path,
            repo_name=repo_name,
            base_branch=basebranch,
            feature_branch=feature_branch,
            sandbox=cfg.sandbox,
        )
    except RuntimeError as exc:
        error(f"Failed to create worktree: {exc}")
        ctx.exit(1)
        return

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


# Register add-repo under the workspace group as well (alias)
workspace.add_command(add_repo, "add-repo")


@click.command("list-repos")
@click.option(
    "--refresh", is_flag=True,
    help="Bypass the GitHub-org repo cache and re-fetch from gh.",
)
@click.pass_context
def list_repos(ctx: click.Context, refresh: bool) -> None:
    """List every repo available to this workspace.

    Shows both local repos (already cloned under a configured ``repoPaths``
    entry) and remote-only repos (discoverable via configured ``githubOrgs``
    and ``gh``). Each row includes the repo's default branch when known.

    GitHub-org results are cached under ``<workspace>/.cache/gh-org-repos/``
    for 6 hours so subsequent calls are instant. Pass ``--refresh`` to
    bypass the cache.

    Suitable for non-interactive use -- the interactive ``add-repo`` picker
    requires a TTY.
    """
    try:
        root = resolve_root(ctx)
        cfg = load_config(root)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    candidates = list_repo_candidates(cfg, refresh=refresh)

    if get_json_mode():
        import sys
        payload = [
            {
                "kind": "remote" if c.is_remote_only else "local",
                "name": c.name,
                "default_branch": c.default_branch,
                "local_path": str(c.local_path) if c.local_path else None,
                "slug": c.slug,
            }
            for c in candidates
        ]
        json.dump(payload, sys.stdout)
        sys.stdout.write("\n")
        return

    if not candidates:
        output("(no repos discovered -- configure repoPaths or githubOrgs)")
        return

    name_width = max(len(c.name) for c in candidates)
    branch_width = max(len(c.default_branch or "-") for c in candidates)
    for c in candidates:
        kind = "remote" if c.is_remote_only else "local "
        location = c.slug if c.is_remote_only else str(c.local_path or "")
        # click.echo bypasses Rich's soft-wrap so each row stays on one line
        # even when the combined width exceeds the terminal.
        click.echo(
            f"{kind}  {c.name:<{name_width}}  "
            f"{(c.default_branch or '-'):<{branch_width}}  {location}"
        )


workspace.add_command(list_repos, "list-repos")


@click.command("list-branches")
@click.argument("repo_name")
@click.pass_context
def list_branches_cmd(ctx: click.Context, repo_name: str) -> None:
    """List branches for REPO_NAME (local or from a configured GitHub org).

    Use this to pick a base branch before calling ``duct add-repo`` when the
    default branch isn't right (e.g. the ticket targets a maintenance release).
    """
    try:
        root = resolve_root(ctx)
        cfg = load_config(root)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    local_path = find_repo(cfg, repo_name)
    if local_path is not None:
        branches = list_branches(local_path)
    else:
        remote_match = next(
            (r for r in list_org_repos(cfg.github_orgs) if r.name == repo_name),
            None,
        )
        if remote_match is None or not remote_match.slug:
            msg = f"Repository '{repo_name}' not found."
            msg += f"\nSearch paths (repoPaths): {', '.join(str(p) for p in cfg.repo_paths)}"
            msg += f"\nGitHub orgs: {', '.join(cfg.github_orgs) if cfg.github_orgs else '(none)'}"
            msg += "\nAdd a search path: duct config add-repo-path <dir>"
            error(msg)
            ctx.exit(1)
            return
        branches = list_remote_branches(remote_match.slug)

    if get_json_mode():
        import sys
        json.dump({"repo": repo_name, "branches": branches}, sys.stdout)
        sys.stdout.write("\n")
    else:
        for branch in branches:
            output(branch)


workspace.add_command(list_branches_cmd, "list-branches")


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

"""UI-agnostic setup logic shared by the prompt flow and the Textual wizard.

Everything here is presentation-free: live probes against Jira/GitHub,
phase-completeness predicates, config writers, and small action helpers
(shell completion, tools, daemon, sync sources). Both ``duct setup``
front-ends — the full-screen wizard and the ``--plain`` prompt flow —
call these so their behaviour cannot drift.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from duct import paths
from duct.config import WorkspaceConfig, load_config, save_config
from duct.credentials import (
    resolve_gh_token,
    resolve_jira_email,
    resolve_jira_token,
)
from duct.global_state import load_state

JIRA_TOKEN_URL = "https://id.atlassian.com/manage-profile/security/api-tokens"
GITHUB_TOKEN_URL = "https://github.com/settings/tokens"


def default_workspace() -> Path:
    """Default workspace path offered by setup.

    ``DUCT_DEFAULT_WORKSPACE`` overrides it (sandbox/test environments),
    mirroring how ``DUCT_STATE_DIR`` redirects global state.
    """
    override = os.environ.get("DUCT_DEFAULT_WORKSPACE")
    if override:
        return Path(override).expanduser()
    return Path.home() / "workspace" / "duct"


# ---------------------------------------------------------------------------
# Live probes — each hits a real source of truth and returns plain data.
# httpx is imported lazily so CLI start-up stays cheap.
# ---------------------------------------------------------------------------


def jira_user(domain: str, email: str, token: str) -> tuple[bool, str]:
    """Probe ``GET /myself`` and return (ok, detail).

    ``detail`` is the display name on success or a short error code on
    failure.
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


def jql_count(domain: str, email: str, token: str, jql: str) -> int | None:
    """Approximate issue count for *jql*, or None when it can't be verified."""
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


@dataclass(frozen=True)
class JqlIssue:
    """One row of a JQL preview."""

    key: str
    summary: str
    status: str
    updated: str


def jql_preview(
    domain: str, email: str, token: str, jql: str, limit: int = 500,
) -> tuple[list[JqlIssue] | None, str]:
    """All issues matching *jql* (up to *limit*), paginated the way sync is.

    Returns (issues, error). ``issues`` is None when the query failed, in
    which case ``error`` carries a short human-readable reason (e.g. the
    JQL syntax error Jira reported).
    """
    import httpx

    credentials = base64.b64encode(f"{email}:{token}".encode()).decode()
    issues: list[JqlIssue] = []
    start_at = 0
    while len(issues) < limit:
        try:
            response = httpx.get(
                f"https://{domain}/rest/api/3/search/jql",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Accept": "application/json",
                },
                params={
                    "jql": jql,
                    "fields": "summary,status,updated",
                    "startAt": start_at,
                    "maxResults": min(50, limit - len(issues)),
                },
                timeout=15,
            )
        except httpx.HTTPError as exc:
            return None, f"network: {exc.__class__.__name__}"

        if response.status_code != 200:
            detail = ""
            try:
                messages = response.json().get("errorMessages") or []
                detail = messages[0] if messages else ""
            except ValueError:
                pass
            return None, detail or f"HTTP {response.status_code}"

        data = response.json()
        batch = data.get("issues", [])
        for raw in batch:
            fields = raw.get("fields") or {}
            issues.append(JqlIssue(
                key=raw.get("key") or "?",
                summary=fields.get("summary") or "",
                status=(fields.get("status") or {}).get("name") or "",
                updated=(fields.get("updated") or "")[:10],
            ))
        start_at += len(batch)
        if not batch or start_at >= data.get("total", 0):
            break
    return issues, ""


def github_user(token: str) -> tuple[bool, str, list[str]]:
    """Return (ok, login-or-error, orgs). ``orgs`` empty on failure."""
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


def org_repo_count(token: str, org: str) -> int | None:
    """Total repos visible in *org* (public + private the token can see)."""
    import httpx

    try:
        response = httpx.get(
            f"https://api.github.com/orgs/{org}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=10,
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    data = response.json()
    return (data.get("public_repos") or 0) + (data.get("total_private_repos") or 0)


def git_email_default() -> str | None:
    """The user's global git email — a good default for the Jira login."""
    try:
        result = subprocess.run(
            ["git", "config", "--global", "user.email"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def repos_under(path: Path, max_depth: int = 3) -> list[str]:
    """Names of git repos under one repo path (for live previews)."""
    from duct.cli.workspace_cmd import discover_repos

    probe_cfg = WorkspaceConfig(repo_paths=[path])
    return [name for name, _ in discover_repos(probe_cfg, max_depth=max_depth)]


# ---------------------------------------------------------------------------
# Completeness predicates — cheap, local checks that drive wizard resume,
# the progress rail, and the bare-`duct` dispatch.
# ---------------------------------------------------------------------------


def workspace_root() -> Path | None:
    """The configured workspace root, or None when setup hasn't created one."""
    state = load_state()
    if state.workspace_path and paths.is_workspace(state.workspace_path):
        return state.workspace_path
    return None


def jira_configured(cfg: WorkspaceConfig) -> bool:
    return bool(cfg.jira_domain and resolve_jira_email() and resolve_jira_token())


def github_token_available() -> bool:
    return bool(resolve_gh_token())


def first_sync_done(root: Path) -> bool:
    return paths.sync_state_file(root).exists()


def state_is_ready() -> bool:
    """True when duct has a workspace and the Jira credentials it needs.

    The single definition of "set up" — bare-``duct`` dispatch and the
    wizard's jump-menu mode both use it.
    """
    root = workspace_root()
    if root is None:
        return False
    return bool(resolve_jira_email() and resolve_jira_token())


# ---------------------------------------------------------------------------
# Config writers — load/replace/save so callers never hand-rebuild the
# frozen WorkspaceConfig field-by-field.
# ---------------------------------------------------------------------------


def update_config(root: Path, **changes) -> WorkspaceConfig:
    """Apply *changes* to the stored config and return the new value."""
    cfg = replace(load_config(root), **changes)
    save_config(cfg, root)
    return cfg


def set_notifications(root: Path, enabled: bool, event_kinds: tuple[str, ...]) -> WorkspaceConfig:
    cfg = load_config(root)
    notifications = replace(cfg.notifications, enabled=enabled, event_kinds=event_kinds)
    return update_config(root, notifications=notifications)


def set_wiki(root: Path, enabled: bool) -> WorkspaceConfig:
    """Persist the wiki choice and rewire everything generated from it.

    Enabling creates any missing scaffolding (index + subagents); disabling
    never deletes anything under ``toolkit/`` — it only strips the wiki from
    the generated root ``.claude/`` and per-ticket CLAUDE.md managed blocks.
    """
    from duct.cli.init_cmd import ensure_wiki_scaffolding, materialise_root_claude
    from duct.sync.claude_md import ClaudeMdSync

    cfg = load_config(root)
    new_cfg = update_config(root, wiki=replace(cfg.wiki, enabled=enabled))
    if enabled:
        ensure_wiki_scaffolding(root)
    materialise_root_claude(root, wiki_enabled=enabled)
    # Per-ticket CLAUDE.md carries wiki guidance in its managed block —
    # refresh now so the toggle doesn't wait for the next sync.
    ClaudeMdSync(wiki_enabled=enabled).sync(root)
    return new_cfg


def toolkit_claude_mentions_wiki(root: Path) -> bool:
    """True when the user-owned ``toolkit/CLAUDE.md`` still references the wiki.

    Workspaces created before the wiki became opt-in carry wiki guidance in
    that file; duct never edits user files, so the setup front-ends use this
    to tell the user to remove it themselves after disabling.
    """
    toolkit_claude = paths.toolkit_claude_md(root)
    if not toolkit_claude.exists():
        return False
    return "wiki" in toolkit_claude.read_text(encoding="utf-8").lower()


# ---------------------------------------------------------------------------
# External tools.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolStatus:
    name: str
    present: bool
    required: bool
    hint: str


def tool_statuses() -> list[ToolStatus]:
    """PATH checks for the CLIs duct shells out to."""
    return [
        ToolStatus(
            "claude", bool(shutil.which("claude")), required=True,
            hint="install from https://docs.claude.com/claude-code",
        ),
        ToolStatus(
            "git", bool(shutil.which("git")), required=True,
            hint="install git before running sync",
        ),
        ToolStatus(
            "gh", bool(shutil.which("gh")), required=False,
            hint="optional — easiest GitHub auth via `gh auth login`",
        ),
        ToolStatus(
            "mmdc", bool(shutil.which("mmdc")), required=False,
            hint="optional — `npm i -g @mermaid-js/mermaid-cli` for mermaid diagrams",
        ),
    ]


# ---------------------------------------------------------------------------
# Shell completion.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShellCompletion:
    shell_name: str
    rc_path: Path
    activation: str

    @property
    def enabled(self) -> bool:
        content = self.rc_path.read_text() if self.rc_path.exists() else ""
        return "_DUCT_COMPLETE" in content


def shell_completion_status() -> ShellCompletion | None:
    """Completion state for the user's shell, or None for unknown shells."""
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return ShellCompletion(
            "zsh", Path.home() / ".zshrc",
            'autoload -Uz compinit && compinit -C 2>/dev/null; '
            'eval "$(_DUCT_COMPLETE=zsh_source duct)"',
        )
    if "bash" in shell:
        return ShellCompletion(
            "bash", Path.home() / ".bashrc",
            'eval "$(_DUCT_COMPLETE=bash_source duct)"',
        )
    if "fish" in shell:
        return ShellCompletion(
            "fish", Path.home() / ".config" / "fish" / "config.fish",
            '_DUCT_COMPLETE=fish_source duct | source',
        )
    return None


def enable_shell_completion(completion: ShellCompletion) -> None:
    completion.rc_path.parent.mkdir(parents=True, exist_ok=True)
    with open(completion.rc_path, "a") as f:
        f.write(f"\n{completion.activation}\n")


# ---------------------------------------------------------------------------
# Daemon (macOS launchd agent).
# ---------------------------------------------------------------------------


def daemon_supported() -> bool:
    return sys.platform == "darwin"


def daemon_installed() -> bool:
    from duct.cli.daemon_cmd import is_installed

    return is_installed()


def install_daemon(root: Path) -> None:
    from duct.cli.daemon_cmd import install_agent

    install_agent(root)


# ---------------------------------------------------------------------------
# Sync sources — the one place that knows how to build every source.
# (Moved from sync_cmd so the wizard's mandatory first sync uses the same
# construction and skip-on-missing-auth behaviour as `duct sync`.)
# ---------------------------------------------------------------------------


def build_sync_sources(cfg: WorkspaceConfig) -> tuple[list, list[tuple[str, str]]]:
    """Build all available sync sources, skipping those with missing auth."""
    from duct.cli.output import debug
    from duct.config import AuthError, gh_token, jira_email, jira_token

    sources = []
    skipped: list[tuple[str, str]] = []

    # Jira
    try:
        from duct.sync.jira import JiraSync

        debug(f"jira: JQL = {cfg.jira_jql}")
        sources.append(JiraSync(
            domain=cfg.jira_domain,
            email=jira_email(),
            token=jira_token(),
            jql=cfg.jira_jql,
            sandbox=cfg.sandbox,
        ))
    except AuthError as exc:
        skipped.append(("jira", str(exc)))

    # GitHub
    try:
        from duct.config import github_username
        from duct.sync.github import GitHubSync

        sources.append(GitHubSync(token=gh_token(), github_username=github_username()))
    except AuthError as exc:
        skipped.append(("github", str(exc)))

    # CI (no auth required)
    from duct.sync.ci import CISync
    sources.append(CISync())

    # Sessions (no auth required)
    from duct.sync.sessions import SessionSync
    sources.append(SessionSync())

    # Workspace (no auth required)
    from duct.sync.workspace_sync import WorkspaceSync
    sources.append(WorkspaceSync())

    # Per-ticket CLAUDE.md (must run last; depends on the artifacts other sources produce)
    from duct.sync.claude_md import ClaudeMdSync
    sources.append(ClaudeMdSync(wiki_enabled=cfg.wiki.enabled))

    return sources, skipped


def sync_intervals(cfg: WorkspaceConfig) -> dict[str, int]:
    """Per-source staleness intervals for the SyncCoordinator."""
    return {
        "jira": cfg.sync_intervals.jira,
        "github": cfg.sync_intervals.github,
        "sessions": cfg.sync_intervals.sessions,
        "workspace": cfg.sync_intervals.workspace,
        "ci": cfg.sync_intervals.ci,
        "claude_md": cfg.sync_intervals.claude_md,
    }

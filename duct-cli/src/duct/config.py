"""Configuration loading, saving, and workspace discovery."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from duct.exceptions import AuthError, ConfigError

_DEFAULT_JQL = "assignee = currentUser() AND status != Done ORDER BY updated DESC"
_CONFIG_FILENAME = "config.yaml"


@dataclass(frozen=True)
class SyncIntervals:
    """Sync polling intervals in seconds."""

    jira: int = 10800
    github: int = 10800
    sessions: int = 900
    workspace: int = 1800
    ci: int = 10800


@dataclass(frozen=True)
class SandboxConfig:
    """Sandbox restrictions for Claude Code sessions."""

    enabled: bool = True
    auto_allow_bash: bool = True
    skip_permissions: bool = False
    allow_write: tuple[str, ...] = (
        ".",
        "~/.m2",
        "~/.gradle",
        "~/.cache/pip",
        "~/.local",
        "~/.npm",
        "~/.cargo",
        "~/.swiftly",
        "~/.docker",
        "~/.bin",
        "~/mssql",
        "/tmp",
        "/private/tmp",
        "~/.config/gh",
        "~/.config/git",
    )
    deny_read: tuple[str, ...] = ("~/.ssh", "~/.aws", "~/.gnupg")
    allowed_domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkspaceConfig:
    """Immutable workspace configuration."""

    root: Path = field(default_factory=lambda: Path.home() / "workspace" / "duct")
    jira_jql: str = _DEFAULT_JQL
    jira_domain: str = ""
    repo_paths: list[Path] = field(
        default_factory=lambda: [Path.home() / "workspace", Path.home() / "projects"]
    )
    sync_intervals: SyncIntervals = field(default_factory=SyncIntervals)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)


# ---------------------------------------------------------------------------
# YAML camelCase <-> Python snake_case mapping
# ---------------------------------------------------------------------------

_SANDBOX_YAML_TO_PY = {
    "autoAllowBashIfSandboxed": "auto_allow_bash",
    "skipPermissions": "skip_permissions",
    "allowWrite": "allow_write",
    "denyRead": "deny_read",
    "allowedDomains": "allowed_domains",
}
_SANDBOX_PY_TO_YAML = {v: k for k, v in _SANDBOX_YAML_TO_PY.items()}


def _parse_sandbox(raw: dict[str, Any]) -> SandboxConfig:
    kwargs: dict[str, Any] = {}
    if "enabled" in raw:
        kwargs["enabled"] = raw["enabled"]
    for yaml_key, py_key in _SANDBOX_YAML_TO_PY.items():
        if yaml_key in raw:
            val = raw[yaml_key]
            # Convert lists to tuples for frozen dataclass fields.
            if isinstance(val, list):
                val = tuple(val)
            kwargs[py_key] = val
    return SandboxConfig(**kwargs)


def _sandbox_to_dict(sandbox: SandboxConfig) -> dict[str, Any]:
    result: dict[str, Any] = {"enabled": sandbox.enabled}
    for f in fields(sandbox):
        if f.name == "enabled":
            continue
        yaml_key = _SANDBOX_PY_TO_YAML[f.name]
        val = getattr(sandbox, f.name)
        if isinstance(val, tuple):
            val = list(val)
        result[yaml_key] = val
    return result


def _parse_sync_intervals(raw: dict[str, Any]) -> SyncIntervals:
    known = {f.name for f in fields(SyncIntervals)}
    kwargs = {k: v for k, v in raw.items() if k in known}
    return SyncIntervals(**kwargs)


def _sync_intervals_to_dict(intervals: SyncIntervals) -> dict[str, int]:
    return {f.name: getattr(intervals, f.name) for f in fields(intervals)}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(root: Path) -> WorkspaceConfig:
    """Load configuration from *root*/config.yaml, falling back to defaults."""
    config_path = root / _CONFIG_FILENAME
    if not config_path.exists():
        return WorkspaceConfig(root=root)

    import yaml

    try:
        raw = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc

    workspace_section = raw.get("workspace", {})
    jira_section = raw.get("jira", {})

    ws_root_str = workspace_section.get("root")
    ws_root = Path(ws_root_str).expanduser() if ws_root_str else root

    repo_paths_raw = raw.get("repoPaths")
    if repo_paths_raw is not None:
        repo_paths = [Path(p).expanduser() for p in repo_paths_raw]
    else:
        repo_paths = WorkspaceConfig().repo_paths

    sync_intervals = _parse_sync_intervals(raw.get("syncIntervals", {}))
    sandbox = _parse_sandbox(raw.get("sandbox", {}))

    return WorkspaceConfig(
        root=ws_root,
        jira_jql=jira_section.get("jql", _DEFAULT_JQL),
        jira_domain=jira_section.get("domain", ""),
        repo_paths=repo_paths,
        sync_intervals=sync_intervals,
        sandbox=sandbox,
    )


def save_config(config: WorkspaceConfig, root: Path) -> None:
    """Write *config* as config.yaml inside *root*."""
    import yaml

    root.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "workspace": {
            "root": str(config.root),
        },
        "jira": {
            "domain": config.jira_domain,
            "jql": config.jira_jql,
        },
        "repoPaths": [str(p) for p in config.repo_paths],
        "syncIntervals": _sync_intervals_to_dict(config.sync_intervals),
        "sandbox": _sandbox_to_dict(config.sandbox),
    }
    config_path = root / _CONFIG_FILENAME
    config_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def find_workspace_root(start: Path | None = None) -> Path:
    """Walk up from *start* (default: cwd) looking for config.yaml.

    Returns the directory containing config.yaml, or raises ConfigError.
    """
    current = (start or Path.cwd()).resolve()
    while True:
        if (current / _CONFIG_FILENAME).exists():
            return current
        parent = current.parent
        if parent == current:
            raise ConfigError(
                f"No {_CONFIG_FILENAME} found in {start or Path.cwd()} or any parent directory"
            )
        current = parent


# ---------------------------------------------------------------------------
# Auth helpers — read credentials from environment variables
# ---------------------------------------------------------------------------


def jira_email() -> str:
    """Return JIRA_EMAIL from the environment, or raise AuthError."""
    value = os.environ.get("JIRA_EMAIL")
    if not value:
        raise AuthError("JIRA_EMAIL environment variable is not set")
    return value


def jira_token() -> str:
    """Return JIRA_TOKEN from the environment, or raise AuthError."""
    value = os.environ.get("JIRA_TOKEN")
    if not value:
        raise AuthError("JIRA_TOKEN environment variable is not set")
    return value


def gh_token() -> str:
    """Return a GitHub token from env vars or ``gh auth token``, or raise AuthError."""
    value = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if value:
        return value

    # Fall back to the gh CLI's stored token.
    import shutil
    import subprocess

    if shutil.which("gh"):
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass

    raise AuthError("No GitHub token found (checked GH_TOKEN, GITHUB_TOKEN, gh auth token)")

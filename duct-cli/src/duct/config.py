"""Configuration loading, saving, and workspace discovery."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from duct import paths
from duct.exceptions import AuthError, ConfigError

_DEFAULT_JQL = "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"


@dataclass(frozen=True)
class SyncIntervals:
    """Sync polling intervals in seconds."""

    jira: int = 600  # 10 min
    github: int = 600  # 10 min
    sessions: int = 900  # 15 min
    workspace: int = 1800  # 30 min
    ci: int = 600  # 10 min
    activity: int = 900  # 15 min
    claude_md: int = 0  # always run; cheap and depends on other sources' output


@dataclass(frozen=True)
class ActivityConfig:
    """Activity log preferences."""

    providers_enabled: tuple[str, ...] = (
        "jira",
        "github",
        "git",
        "claude",
        "outlook",
        "outlook_pdf",
    )
    # Path to an Outlook agenda PDF (File → Print → Save as PDF from Outlook)
    # used by the ``outlook_pdf`` provider. Tilde-expanded. When unset, the
    # provider does nothing.
    outlook_pdf_path: str = ""
    # Cap, in days, for how far beyond ``since`` the outlook provider will
    # expand recurring-meeting occurrences. Guards against accidental
    # ``--until`` windows producing years of daily-standup rows.
    outlook_recurrence_max_days: int = 60


@dataclass(frozen=True)
class StatusConfig:
    """Status filtering for the status command."""

    focus_statuses: tuple[str, ...] = (
        "in progress",
        "analysis started",
        "testing failed",
        "customer testing",
        "testing",
        "ready to test",
        "ready to deploy",
        "deployed",
    )
    terminal_statuses: tuple[str, ...] = ("closed", "done")


@dataclass(frozen=True)
class SessionStatusConfig:
    """Time-based decoration of Claude Code session ``ready`` statuses.

    A session that finished work recently shows as ``done`` (the user may still
    want to review it). One that has been idle for longer than the stale
    threshold shows as ``stale``.
    """

    done_window_seconds: int = 900     # 15 min
    stale_after_seconds: int = 14400   # 4 h


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
class SessionConfig:
    """Session launch settings."""

    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class OrchestratorConfig:
    """Orchestrator session settings."""

    # Model alias passed to the read-only per-ticket fan-out forks' Agent calls.
    # The parent orchestrator keeps the session's inherited model (Opus); the
    # forks run cheaper. Empty string => forks inherit the parent model.
    fork_model: str = "sonnet"


@dataclass(frozen=True)
class DisplayConfig:
    """Display preferences for the TUI."""

    nerd_font: bool = False
    # When True, render artifact markdown via Rich into a single Static widget
    # instead of Textual's Markdown widget. The Textual widget mounts one
    # MarkdownBlock per heading/paragraph/code block, which makes panel hide/show
    # scale with document size; the Rich path is constant-cost at the expense
    # of clickable links and the (unused) TOC feature.
    fast_markdown: bool = False


@dataclass(frozen=True)
class NotificationsConfig:
    """macOS notification preferences, consumed by the duct daemon."""

    enabled: bool = False
    # Daemon poll cadences (seconds): sessions are cheap and polled often;
    # ticket overviews are heavy (workspace walk + git) so polled slower.
    session_poll_seconds: int = 4
    overview_poll_seconds: int = 45
    # Which notification kinds the daemon is allowed to fire.
    event_kinds: tuple[str, ...] = ("done", "waiting", "pending-action", "orchestrator")
    # Bundle id passed to terminal-notifier's -sender to rebrand the corner
    # icon/name; "" leaves the terminal-notifier default.
    sender_bundle_id: str = ""
    # Skip session notifications when that session's terminal is in front of
    # the user (WezTerm frontmost + the session's pane focused). They're already
    # looking at it. Degrades to notifying when focus can't be determined.
    suppress_focused_terminal: bool = True


@dataclass(frozen=True)
class AutoOrchestrateConfig:
    """Schedule that fires the orchestrator while the TUI is open."""

    enabled: bool = False
    # ``datetime.weekday()`` indices, Monday=0..Sunday=6.
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    # Inclusive on both ends — a window of 9..17 fires nine times (09:00..17:00).
    start_hour: int = 9
    end_hour: int = 17
    sync_first: bool = True

    def next_fire_time(self, now: datetime) -> datetime | None:
        """Strict next fire instant after ``now``, or None if never."""
        if not self.enabled or not self.weekdays:
            return None
        candidate = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        # Walk forward at most 8 days; an empty window can't fire so we stop.
        for _ in range(24 * 8):
            if (
                candidate.weekday() in self.weekdays
                and self.start_hour <= candidate.hour <= self.end_hour
            ):
                return candidate
            candidate += timedelta(hours=1)
        return None


@dataclass(frozen=True)
class WorkspaceConfig:
    """Immutable workspace configuration."""

    root: Path = field(default_factory=lambda: Path.home() / "workspace" / "duct")
    jira_jql: str = _DEFAULT_JQL
    jira_domain: str = ""
    repo_paths: list[Path] = field(
        default_factory=lambda: [Path.home() / "workspace", Path.home() / "projects"]
    )
    github_orgs: tuple[str, ...] = ()
    sync_intervals: SyncIntervals = field(default_factory=SyncIntervals)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    status: StatusConfig = field(default_factory=StatusConfig)
    session_status: SessionStatusConfig = field(default_factory=SessionStatusConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    activity: ActivityConfig = field(default_factory=ActivityConfig)
    auto_orchestrate: AutoOrchestrateConfig = field(default_factory=AutoOrchestrateConfig)


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


_STATUS_YAML_TO_PY = {
    "focusStatuses": "focus_statuses",
    "terminalStatuses": "terminal_statuses",
}
_STATUS_PY_TO_YAML = {v: k for k, v in _STATUS_YAML_TO_PY.items()}


def _parse_status(raw: dict[str, Any]) -> StatusConfig:
    kwargs: dict[str, Any] = {}
    for yaml_key, py_key in _STATUS_YAML_TO_PY.items():
        if yaml_key in raw:
            val = raw[yaml_key]
            if isinstance(val, list):
                val = tuple(v.lower() for v in val)
            kwargs[py_key] = val
    return StatusConfig(**kwargs)


def _status_to_dict(status: StatusConfig) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for f in fields(status):
        yaml_key = _STATUS_PY_TO_YAML[f.name]
        val = getattr(status, f.name)
        if isinstance(val, tuple):
            val = list(val)
        result[yaml_key] = val
    return result


_SESSION_STATUS_YAML_TO_PY = {
    "doneWindowSeconds": "done_window_seconds",
    "staleAfterSeconds": "stale_after_seconds",
}
_SESSION_STATUS_PY_TO_YAML = {v: k for k, v in _SESSION_STATUS_YAML_TO_PY.items()}


def _parse_session_status(raw: dict[str, Any]) -> SessionStatusConfig:
    kwargs: dict[str, Any] = {}
    for yaml_key, py_key in _SESSION_STATUS_YAML_TO_PY.items():
        if yaml_key in raw:
            try:
                kwargs[py_key] = int(raw[yaml_key])
            except (TypeError, ValueError) as exc:
                raise ConfigError(
                    f"sessionStatus.{yaml_key}: must be an integer number of seconds"
                ) from exc
    return SessionStatusConfig(**kwargs)


def _session_status_to_dict(cfg: SessionStatusConfig) -> dict[str, Any]:
    return {
        _SESSION_STATUS_PY_TO_YAML[f.name]: getattr(cfg, f.name)
        for f in fields(cfg)
    }


def _parse_session(raw: dict[str, Any]) -> SessionConfig:
    kwargs: dict[str, Any] = {}
    if "extraArgs" in raw:
        val = raw["extraArgs"]
        if isinstance(val, list):
            kwargs["extra_args"] = tuple(str(a) for a in val)
    return SessionConfig(**kwargs)


def _session_to_dict(session: SessionConfig) -> dict[str, Any]:
    return {"extraArgs": list(session.extra_args)}


def _parse_orchestrator(raw: dict[str, Any]) -> OrchestratorConfig:
    kwargs: dict[str, Any] = {}
    if "forkModel" in raw:
        kwargs["fork_model"] = str(raw["forkModel"])
    return OrchestratorConfig(**kwargs)


def _orchestrator_to_dict(orchestrator: OrchestratorConfig) -> dict[str, Any]:
    return {"forkModel": orchestrator.fork_model}


_DISPLAY_YAML_TO_PY = {
    "nerdFont": "nerd_font",
    "fastMarkdown": "fast_markdown",
}
_DISPLAY_PY_TO_YAML = {v: k for k, v in _DISPLAY_YAML_TO_PY.items()}


def _parse_display(raw: dict[str, Any]) -> DisplayConfig:
    kwargs: dict[str, Any] = {}
    for yaml_key, py_key in _DISPLAY_YAML_TO_PY.items():
        if yaml_key in raw:
            kwargs[py_key] = raw[yaml_key]
    return DisplayConfig(**kwargs)


def _display_to_dict(display: DisplayConfig) -> dict[str, Any]:
    return {
        _DISPLAY_PY_TO_YAML[f.name]: getattr(display, f.name)
        for f in fields(display)
    }


def _parse_notifications(raw: dict[str, Any]) -> NotificationsConfig:
    kwargs: dict[str, Any] = {}
    if "enabled" in raw:
        kwargs["enabled"] = raw["enabled"]
    if "sessionPollSeconds" in raw:
        kwargs["session_poll_seconds"] = int(raw["sessionPollSeconds"])
    if "overviewPollSeconds" in raw:
        kwargs["overview_poll_seconds"] = int(raw["overviewPollSeconds"])
    if "eventKinds" in raw and isinstance(raw["eventKinds"], list):
        kwargs["event_kinds"] = tuple(str(k) for k in raw["eventKinds"])
    if "senderBundleId" in raw and raw["senderBundleId"]:
        kwargs["sender_bundle_id"] = str(raw["senderBundleId"])
    if "suppressFocusedTerminal" in raw:
        kwargs["suppress_focused_terminal"] = bool(raw["suppressFocusedTerminal"])
    return NotificationsConfig(**kwargs)


def _notifications_to_dict(notifications: NotificationsConfig) -> dict[str, Any]:
    return {
        "enabled": notifications.enabled,
        "sessionPollSeconds": notifications.session_poll_seconds,
        "overviewPollSeconds": notifications.overview_poll_seconds,
        "eventKinds": list(notifications.event_kinds),
        "senderBundleId": notifications.sender_bundle_id,
        "suppressFocusedTerminal": notifications.suppress_focused_terminal,
    }


def _parse_activity(raw: dict[str, Any]) -> ActivityConfig:
    kwargs: dict[str, Any] = {}
    if "providersEnabled" in raw:
        val = raw["providersEnabled"]
        if isinstance(val, list):
            kwargs["providers_enabled"] = tuple(str(p) for p in val)
    if "outlookPdfPath" in raw and raw["outlookPdfPath"]:
        kwargs["outlook_pdf_path"] = str(raw["outlookPdfPath"])
    if "outlookRecurrenceMaxDays" in raw:
        try:
            kwargs["outlook_recurrence_max_days"] = int(raw["outlookRecurrenceMaxDays"])
        except (TypeError, ValueError):
            pass
    return ActivityConfig(**kwargs)


def _activity_to_dict(activity: ActivityConfig) -> dict[str, Any]:
    return {
        "providersEnabled": list(activity.providers_enabled),
        "outlookPdfPath": activity.outlook_pdf_path,
        "outlookRecurrenceMaxDays": activity.outlook_recurrence_max_days,
    }


_WEEKDAY_ALIASES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def _coerce_weekday(value: Any) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"autoOrchestrate.weekdays: invalid weekday {value!r}")
    if isinstance(value, int):
        if 0 <= value <= 6:
            return value
        raise ConfigError(f"autoOrchestrate.weekdays: int {value} out of range 0..6")
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _WEEKDAY_ALIASES:
            return _WEEKDAY_ALIASES[key]
    raise ConfigError(f"autoOrchestrate.weekdays: invalid weekday {value!r}")


def _coerce_hour(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"autoOrchestrate.{key}: must be an integer hour, got {value!r}")
    if not 0 <= value <= 23:
        raise ConfigError(f"autoOrchestrate.{key}: {value} out of range 0..23")
    return value


def _parse_auto_orchestrate(raw: dict[str, Any]) -> AutoOrchestrateConfig:
    kwargs: dict[str, Any] = {}
    if "enabled" in raw:
        kwargs["enabled"] = bool(raw["enabled"])
    if "weekdays" in raw:
        days = raw["weekdays"]
        if not isinstance(days, list):
            raise ConfigError("autoOrchestrate.weekdays: must be a list")
        kwargs["weekdays"] = tuple(sorted({_coerce_weekday(d) for d in days}))
    if "startHour" in raw:
        kwargs["start_hour"] = _coerce_hour(raw["startHour"], "startHour")
    if "endHour" in raw:
        kwargs["end_hour"] = _coerce_hour(raw["endHour"], "endHour")
    if "syncFirst" in raw:
        kwargs["sync_first"] = bool(raw["syncFirst"])
    cfg = AutoOrchestrateConfig(**kwargs)
    if cfg.start_hour > cfg.end_hour:
        raise ConfigError(
            f"autoOrchestrate.startHour ({cfg.start_hour}) must be <= endHour ({cfg.end_hour})"
        )
    return cfg


def _auto_orchestrate_to_dict(cfg: AutoOrchestrateConfig) -> dict[str, Any]:
    return {
        "enabled": cfg.enabled,
        "weekdays": list(cfg.weekdays),
        "startHour": cfg.start_hour,
        "endHour": cfg.end_hour,
        "syncFirst": cfg.sync_first,
    }


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
    """Load configuration from *root*/toolkit/config.yaml, falling back to defaults."""
    config_path = paths.config_file(root)
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

    github_orgs_raw = raw.get("githubOrgs") or []
    github_orgs = tuple(str(o) for o in github_orgs_raw if str(o).strip())

    sync_intervals = _parse_sync_intervals(raw.get("syncIntervals", {}))
    sandbox = _parse_sandbox(raw.get("sandbox", {}))
    session = _parse_session(raw.get("session", {}))
    orchestrator = _parse_orchestrator(raw.get("orchestrator", {}))
    status = _parse_status(raw.get("status", {}))
    session_status = _parse_session_status(raw.get("sessionStatus", {}))
    display = _parse_display(raw.get("display", {}))
    notifications = _parse_notifications(raw.get("notifications", {}))
    activity = _parse_activity(raw.get("activity", {}))
    auto_orchestrate = _parse_auto_orchestrate(raw.get("autoOrchestrate", {}))

    return WorkspaceConfig(
        root=ws_root,
        jira_jql=jira_section.get("jql", _DEFAULT_JQL),
        jira_domain=jira_section.get("domain", ""),
        repo_paths=repo_paths,
        github_orgs=github_orgs,
        sync_intervals=sync_intervals,
        sandbox=sandbox,
        session=session,
        orchestrator=orchestrator,
        status=status,
        session_status=session_status,
        display=display,
        notifications=notifications,
        activity=activity,
        auto_orchestrate=auto_orchestrate,
    )


def save_config(config: WorkspaceConfig, root: Path) -> None:
    """Write *config* as toolkit/config.yaml inside *root*."""
    import yaml

    paths.toolkit_dir(root).mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "workspace": {
            "root": str(config.root),
        },
        "jira": {
            "domain": config.jira_domain,
            "jql": config.jira_jql,
        },
        "repoPaths": [str(p) for p in config.repo_paths],
        "githubOrgs": list(config.github_orgs),
        "syncIntervals": _sync_intervals_to_dict(config.sync_intervals),
        "sandbox": _sandbox_to_dict(config.sandbox),
        "session": _session_to_dict(config.session),
        "orchestrator": _orchestrator_to_dict(config.orchestrator),
        "status": _status_to_dict(config.status),
        "sessionStatus": _session_status_to_dict(config.session_status),
        "display": _display_to_dict(config.display),
        "notifications": _notifications_to_dict(config.notifications),
        "activity": _activity_to_dict(config.activity),
        "autoOrchestrate": _auto_orchestrate_to_dict(config.auto_orchestrate),
    }
    config_path = paths.config_file(root)
    config_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


def find_workspace_root(start: Path | None = None) -> Path:
    """Walk up from *start* (default: cwd) looking for the workspace sentinel.

    Returns the directory holding ``toolkit/config.yaml``, or raises ConfigError.
    """
    root = paths.find_workspace_root(start)
    if root is None:
        raise ConfigError(
            f"No {paths.TOOLKIT_DIRNAME}/{paths.CONFIG_FILENAME} found in "
            f"{start or Path.cwd()} or any parent directory"
        )
    return root


# ---------------------------------------------------------------------------
# Auth helpers — read credentials from the OS keychain (see duct.credentials).
# ---------------------------------------------------------------------------


def jira_email() -> str:
    """Return the Jira email, or raise AuthError."""
    from duct.credentials import resolve_jira_email

    value = resolve_jira_email()
    if not value:
        raise AuthError("Jira email is not set (run `duct` to set up credentials)")
    return value


def jira_token() -> str:
    """Return the Jira API token, or raise AuthError."""
    from duct.credentials import resolve_jira_token

    value = resolve_jira_token()
    if not value:
        raise AuthError("Jira token is not set (run `duct` to set up credentials)")
    return value


def gh_token() -> str:
    """Return a GitHub token, or raise AuthError."""
    from duct.credentials import resolve_gh_token

    value = resolve_gh_token()
    if not value:
        raise AuthError(
            "No GitHub token found (run `duct` to set up credentials, "
            "or run `gh auth login`)"
        )
    return value


# ---------------------------------------------------------------------------
# GitHub username resolution
# ---------------------------------------------------------------------------

_cached_github_username: str | None = None
_github_username_resolved = False


def github_username() -> str | None:
    """Return the authenticated GitHub username, or None if unavailable."""
    global _cached_github_username, _github_username_resolved
    if _github_username_resolved:
        return _cached_github_username

    import shutil
    import subprocess

    _github_username_resolved = True
    if shutil.which("gh"):
        try:
            result = subprocess.run(
                ["gh", "api", "user", "-q", ".login"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                _cached_github_username = result.stdout.strip()
        except Exception:
            pass
    return _cached_github_username

"""Central path resolution for duct — the single source of truth for layout.

Everything that builds a workspace- or home-relative path goes through here so
the on-disk layout can change in one place. Three families:

* **Tracked toolkit** (``root`` arg) — the ``toolkit/`` subfolder is a git repo
  holding config + knowledge + subagents (``config.yaml``, ``WORKFLOW.md``,
  ``CLAUDE.md``, ``agents/``, ``wiki/``, ``subagents/``, ``settings.template.json``).
* **Runtime state** (``root`` arg) — the consolidated ``.duct/`` dir holds daemon
  state, run logs, activity, sync state, caches, and workspace actions.
* **Home state** (no arg) — global state under ``~/.config/duct`` (override via
  ``DUCT_STATE_DIR``), with ``logs/`` and ``cache/`` subdirs.

Plus workspace-root resolution: the sentinel (``toolkit/config.yaml``) and the
walk-up used to locate ``root``.
"""

from __future__ import annotations

from pathlib import Path

from duct.global_state import load_state, state_dir

# --- names -----------------------------------------------------------------
TOOLKIT_DIRNAME = "toolkit"
CONFIG_FILENAME = "config.yaml"
WORKFLOW_FILENAME = "WORKFLOW.md"
CLAUDE_MD_FILENAME = "CLAUDE.md"
SETTINGS_TEMPLATE_FILENAME = "settings.template.json"
AGENTS_DIRNAME = "agents"
WIKI_DIRNAME = "wiki"
WIKI_INDEX_FILENAME = "INDEX.md"
SUBAGENTS_DIRNAME = "subagents"

STATE_DIRNAME = ".duct"
CLAUDE_DIRNAME = ".claude"


# === A — tracked toolkit (config + knowledge) ==============================
def toolkit_dir(root: Path) -> Path:
    return root / TOOLKIT_DIRNAME


def config_file(root: Path) -> Path:
    return toolkit_dir(root) / CONFIG_FILENAME


def workflow_md(root: Path) -> Path:
    return toolkit_dir(root) / WORKFLOW_FILENAME


def toolkit_claude_md(root: Path) -> Path:
    return toolkit_dir(root) / CLAUDE_MD_FILENAME


def settings_template(root: Path) -> Path:
    return toolkit_dir(root) / SETTINGS_TEMPLATE_FILENAME


def agents_dir(root: Path) -> Path:
    return toolkit_dir(root) / AGENTS_DIRNAME


def wiki_dir(root: Path) -> Path:
    return toolkit_dir(root) / WIKI_DIRNAME


def wiki_index(root: Path) -> Path:
    return wiki_dir(root) / WIKI_INDEX_FILENAME


def subagents_dir(root: Path) -> Path:
    return toolkit_dir(root) / SUBAGENTS_DIRNAME


# === Generated root .claude (materialised from toolkit; not tracked) =======
def root_claude_dir(root: Path) -> Path:
    return root / CLAUDE_DIRNAME


def root_claude_md(root: Path) -> Path:
    return root_claude_dir(root) / CLAUDE_MD_FILENAME


def root_claude_agents_dir(root: Path) -> Path:
    return root_claude_dir(root) / "agents"


# === D — consolidated runtime state (.duct/) ===============================
def state_root(root: Path) -> Path:
    return root / STATE_DIRNAME


def daemon_heartbeat(root: Path) -> Path:
    return state_root(root) / "daemon.json"


def orchestrate_state(root: Path) -> Path:
    return state_root(root) / "orchestrate_state.json"


def run_lock(root: Path) -> Path:
    return state_root(root) / "orchestrator.lock"


def notifications_feed(root: Path) -> Path:
    return state_root(root) / "notifications.jsonl"


def runs_dir(root: Path) -> Path:
    return state_root(root) / "runs"


def activity_dir(root: Path) -> Path:
    return state_root(root) / "activity"


def sync_state_file(root: Path) -> Path:
    return state_root(root) / "sync_state.yaml"


def review_prs_file(root: Path) -> Path:
    return state_root(root) / "review_prs.md"


def workspace_actions_file(root: Path) -> Path:
    return state_root(root) / "actions.yaml"


def cache_dir(root: Path) -> Path:
    return state_root(root) / "cache"


def completions_cache(root: Path) -> Path:
    return cache_dir(root) / "completions" / "repos.txt"


def gh_org_cache_dir(root: Path) -> Path:
    return cache_dir(root) / "gh-org-repos"


def jira_identity_cache(root: Path) -> Path:
    return cache_dir(root) / "jira_identity.json"


# === E — home state (~/.config/duct, governed by DUCT_STATE_DIR) ===========
def home_dir() -> Path:
    return state_dir()


def home_logs_dir() -> Path:
    return state_dir() / "logs"


def home_cache_dir() -> Path:
    return state_dir() / "cache"


def daemon_pidfile() -> Path:
    return home_logs_dir() / "daemon.pid"


def daemon_log() -> Path:
    return home_logs_dir() / "daemon.log"


def daemon_errlog() -> Path:
    return home_logs_dir() / "daemon.err.log"


def perf_log() -> Path:
    return home_logs_dir() / "perf.jsonl"


def pane_status_trace() -> Path:
    return home_logs_dir() / "pane-status-misses.log"


def avatars_cache_dir() -> Path:
    return home_cache_dir() / "avatars"


def mermaid_cache_dir() -> Path:
    return home_cache_dir() / "mermaid"


# === Workspace-root resolution =============================================
def is_workspace(path: Path) -> bool:
    """True when *path* is a duct workspace root (holds ``toolkit/config.yaml``)."""
    return config_file(path).exists()


def find_workspace_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) for the workspace sentinel.

    Returns the workspace root, or ``None`` if none is found. Callers that need
    a hard failure should raise their own error (see ``config.find_workspace_root``).
    """
    current = (start or Path.cwd()).resolve()
    while True:
        if is_workspace(current):
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def resolve_root(override: Path | None = None) -> Path | None:
    """Resolve the workspace root: explicit *override* → state pointer → walk-up.

    Returns ``None`` when nothing resolves. ``override`` is trusted as-is (the
    CLI/TUI validate it is a real directory before passing it here).
    """
    if override is not None:
        return override

    state = load_state()
    if state.workspace_path and is_workspace(state.workspace_path):
        return state.workspace_path

    return find_workspace_root()

"""Async data layer wrapping duct API."""

from __future__ import annotations

import subprocess
from pathlib import Path

import duct.api as api
from duct.agents import Agent, list_agents, load_agent
from duct.config import WorkspaceConfig, load_config
from duct.models import (
    Action, PullRequest, SessionInfo, SourceStatus, SyncResult,
    Task, TicketDetail, TicketOverview, TicketSummary,
)
from duct.review import open_in_intellij, prepare_local_review
from duct.terminal import TerminalAdapter


_LoadInitialResult = tuple[list[SessionInfo], list[TicketOverview]]


class DataManager:
    """Async wrapper around duct API. All methods use @work(thread=True)."""

    def __init__(self, root: Path):
        self.root = root

    def load_tickets(self) -> list[TicketSummary]:
        return api.get_tickets(self.root)

    def load_initial(
        self,
        adapter: TerminalAdapter | None = None,
        filter_mode: str = "focus",
    ) -> _LoadInitialResult:
        """Single-pass startup load — sessions and ticket overviews together.

        Avoids the duplicate session-discovery and per-repo git-status
        sweeps the historical three-call pattern triggered.
        """
        return api.load_initial(self.root, adapter=adapter, filter_mode=filter_mode)

    def load_sessions_staged(
        self, adapter: TerminalAdapter | None = None,
    ) -> tuple[list[dict], list[SessionInfo]]:
        """Phase 1 of the staged load: discover + enrich sessions.

        Returns ``(raw_sessions, sessions)``. The raw dicts are threaded
        through to ``load_ticket_overviews_staged`` so the second phase
        skips re-running ``discover_sessions`` and ``apply_overrides``.
        """
        from duct import pane_status
        from duct.session import discover_sessions
        from duct.terminal import _wezterm_list_panes

        if adapter is not None and getattr(adapter, "name", "") == "wezterm":
            _wezterm_list_panes()
        raw_sessions = discover_sessions()
        pane_status.apply_overrides(raw_sessions, adapter)
        api._apply_recency(raw_sessions, self.root)
        sessions = api._build_session_infos(raw_sessions, self.root)
        return raw_sessions, sessions

    def load_ticket_overviews_staged(
        self,
        adapter: TerminalAdapter | None = None,
        raw_sessions: list[dict] | None = None,
        filter_mode: str = "focus",
    ) -> list[TicketOverview]:
        """Phase 2 of the staged load: ticket overviews using already-
        discovered sessions to avoid duplicate work."""
        return api.get_ticket_overviews(
            self.root,
            filter_mode=filter_mode,
            adapter=adapter,
            _raw_sessions=raw_sessions,
        )

    def load_ticket_index(self, filter_mode: str = "all") -> list[TicketOverview]:
        """Metadata-only ticket list (no git/sessions/PRs) for the switcher."""
        return api.get_ticket_index(self.root, filter_mode=filter_mode)

    def load_ticket_detail(self, key: str) -> TicketDetail | None:
        return api.get_ticket_detail(self.root, key)

    def load_sessions(self, adapter: TerminalAdapter | None = None) -> list[SessionInfo]:
        return api.get_sessions(self.root, adapter=adapter)

    def load_actions(self, key: str) -> list[Action]:
        return api.get_actions(self.root, key)

    def run_sync(self, force: bool = False) -> list[SyncResult]:
        return api.trigger_sync(self.root, force=force)

    def get_sync_status(self) -> list[SourceStatus]:
        return api.get_sync_status(self.root)

    def resolve_action(
        self,
        key: str,
        action_id: str,
        approved: bool,
        feedback: str | None = None,
    ) -> None:
        api.resolve_action(self.root, key, action_id, approved, feedback)

    def do_launch_session(self, key: str, repo: str | None, prompt: str | None) -> int:
        return api.launch_session(self.root, key, repo=repo, prompt=prompt)

    def do_spawn_session(
        self, adapter: TerminalAdapter, key: str,
        repo: str | None = None, prompt: str | None = None,
    ) -> int | None:
        """Spawn a session in a new terminal pane. Returns pane ID."""
        return api.spawn_session(adapter, self.root, key, repo=repo, prompt=prompt)

    def do_launch_session_in_dir(self, cwd: Path, prompt: str | None) -> int:
        return api.launch_session_in_dir(cwd, prompt=prompt)

    def load_ticket_overviews(
        self,
        filter_mode: str = "focus",
        adapter: TerminalAdapter | None = None,
    ) -> list[TicketOverview]:
        return api.get_ticket_overviews(self.root, filter_mode=filter_mode, adapter=adapter)

    def do_focus_session(self, pid: int) -> bool:
        return api.focus_session(pid)

    def do_stop_session(self, pid: int) -> bool:
        return api.stop_session(pid)

    def do_dock_session(
        self, adapter: TerminalAdapter, tui_pane_id: int, session_pid: int,
        current_docked_pane: int | None = None,
    ) -> int | None:
        return api.dock_session(adapter, tui_pane_id, session_pid, current_docked_pane)

    def do_undock_session(self, adapter: TerminalAdapter, pane_id: int) -> bool:
        return api.undock_session(adapter, pane_id)

    def get_session_preview(self, adapter: TerminalAdapter, session_pid: int) -> str | None:
        return api.get_session_preview(adapter, session_pid)

    def load_all_actions(self) -> list[tuple[str, Action]]:
        return api.get_all_actions(self.root)

    def launch_orchestrator(self, ticket_key: str | None = None) -> subprocess.Popen:
        return api.launch_orchestrator(self.root, ticket_key=ticket_key)

    def load_all_prs(self, filter_mode: str = "focus") -> list[tuple[str, PullRequest]]:
        return api.get_all_prs(self.root, filter_mode=filter_mode)

    def get_github_username(self) -> str | None:
        return api.github_username()

    def get_workspace_config(self) -> WorkspaceConfig:
        return load_config(self.root)

    def do_deep_review(self, pr: PullRequest) -> Path:
        """Clone-if-needed, check out PR branch, open in IntelliJ. Returns repo path."""
        cfg = self.get_workspace_config()
        repo_path = prepare_local_review(cfg, pr)
        open_in_intellij(repo_path)
        return repo_path

    def list_agents(self) -> list[Agent]:
        return list_agents(self.root)

    def load_agent_body(self, name: str) -> str | None:
        agent = load_agent(self.root, name)
        return agent.body if agent else None

    def do_post_jira_comment(self, key: str, body: str) -> None:
        api.post_jira_comment(self.root, key, body)

    def add_task(self, key: str, description: str) -> Task:
        return api.add_task(self.root, key, description)

    def toggle_task(self, key: str, task_id: str) -> None:
        api.toggle_task(self.root, key, task_id)

    def delete_task(self, key: str, task_id: str) -> None:
        api.delete_task(self.root, key, task_id)

    def reorder_task(self, key: str, task_id: str, direction: int) -> None:
        api.reorder_task(self.root, key, task_id, direction)

    def edit_task(self, key: str, task_id: str, description: str) -> None:
        api.edit_task(self.root, key, task_id, description)

    def discover_repos(self) -> list[str]:
        """Return sorted repo names available under configured repoPaths."""
        return [name for name, _ in api.discover_repos(self.root)]

    def get_repo_candidates(self):
        """Return local + remote-org RepoCandidate entries."""
        return api.get_repo_candidates(self.root)

    def list_repo_branches(
        self, repo_name: str, *, slug: str | None = None,
    ) -> list[str]:
        return api.list_repo_branches(self.root, repo_name, slug=slug)

    def suggest_feature_branch(self, key: str) -> str:
        return api.suggest_feature_branch(self.root, key)

    def do_add_repo(
        self,
        key: str,
        repo_name: str,
        base_branch: str,
        feature_branch: str,
        *,
        clone_from: str | None = None,
    ) -> Path:
        return api.add_repo(
            self.root, key, repo_name, base_branch, feature_branch,
            clone_from=clone_from,
        )

    def load_artifact_content(self, key: str, artifact_name: str) -> str | None:
        ticket_dir = api.resolve_ticket_dir(self.root, key)
        if not ticket_dir:
            return None
        path = ticket_dir / "orchestrator" / f"{artifact_name}.md"
        if path.is_file():
            return path.read_text(errors="ignore")
        return None

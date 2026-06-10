"""Public API surface for duct library consumers."""

from __future__ import annotations

import concurrent.futures as cf
import importlib
import subprocess
import sys
from pathlib import Path

from duct import pane_status, paths, perf
from duct.actions import get_actions, get_all_actions, resolve_action
from duct.config import (
    WorkspaceConfig,
    find_workspace_root,
    gh_token,
    github_username,
    jira_email,
    jira_token,
    load_config,
    save_config,
)
from duct.exceptions import AuthError, ConfigError, DuctError, SyncError, WorkspaceError
from duct.models import (
    Action,
    Comment,
    PRComment,
    PRSummary,
    PullRequest,
    RepoStatus,
    RepoSummary,
    Reviewer,
    SessionInfo,
    SourceStatus,
    SyncResult,
    Task,
    TaskSummary,
    Ticket,
    TicketDetail,
    TicketOverview,
    TicketSummary,
)
from duct.orchestrator import launch as launch_orchestrator
from duct.pr import load_ticket_prs
from duct.session import (
    apply_recency_decoration,
    discover_sessions,
    match_session_ticket,
)
from duct.session import (
    launch_session as _launch_session,
)
from duct.session import (
    launch_session_in_dir as _launch_session_in_dir,
)
from duct.session import (
    prepare_session as _prepare_session,
)
from duct.session import (
    stop_session as _stop_session,
)
from duct.tasks import (
    add_task,
    delete_task,
    edit_task,
    get_tasks,
    reorder_task,
    toggle_task,
)
from duct.terminal import TerminalAdapter, focus_terminal_tab, get_terminal_adapter, get_tty
from duct.workspace import (
    archive_ticket,
    branch_name,
    create_worktree,
    ensure_epic_link,
    ensure_ticket_dir,
    enumerate_ticket_dirs,
    orchestrator_dir,
    read_issue_type,
    resolve_ticket_dir,
    restore_ticket,
    slug,
    ticket_dir_name,
)

__all__ = [
    "Action",
    "AuthError",
    "Comment",
    "ConfigError",
    "PRComment",
    "PRSummary",
    "PullRequest",
    "RepoStatus",
    "RepoSummary",
    "Reviewer",
    "SessionInfo",
    "SourceStatus",
    "SyncError",
    "SyncResult",
    "Task",
    "TaskSummary",
    "Ticket",
    "TicketDetail",
    "TicketOverview",
    "TicketSummary",
    "DuctError",
    "WorkspaceConfig",
    "WorkspaceError",
    "TerminalAdapter",
    "add_repo",
    "archive_ticket",
    "discover_repos",
    "dock_session",
    "ensure_epic_link",
    "ensure_ticket_dir",
    "enumerate_ticket_dirs",
    "find_workspace_root",
    "focus_session",
    "get_actions",
    "get_all_actions",
    "get_all_prs",
    "get_sessions",
    "get_sync_status",
    "github_username",
    "get_terminal_adapter",
    "get_ticket_detail",
    "get_ticket_index",
    "get_ticket_overviews",
    "get_tickets",
    "load_initial",
    "gh_token",
    "jira_email",
    "jira_token",
    "launch_orchestrator",
    "launch_session",
    "launch_session_in_dir",
    "list_repo_branches",
    "load_config",
    "orchestrator_dir",
    "post_jira_comment",
    "add_task",
    "delete_task",
    "edit_task",
    "get_tasks",
    "reorder_task",
    "resolve_action",
    "resolve_ticket_dir",
    "restore_ticket",
    "save_config",
    "slug",
    "stop_session",
    "suggest_feature_branch",
    "ticket_dir_name",
    "toggle_task",
    "trigger_sync",
    "undock_session",
]


def _status_group_rank(status: str, cfg: WorkspaceConfig) -> int:
    """0 = focus statuses, 1 = other non-terminal, 2 = terminal statuses.

    Drives the default ordering of tickets in ``get_tickets`` and
    ``get_ticket_overviews`` now that the user-maintained priority list is
    gone. Reuses ``cfg.status.focus_statuses`` / ``terminal_statuses`` so
    the same configuration the user already controls keeps governing the
    visible order.
    """
    s = status.lower().strip()
    if s in cfg.status.focus_statuses:
        return 0
    if s in cfg.status.terminal_statuses:
        return 2
    return 1


def get_tickets(root: Path) -> list[TicketSummary]:
    """All tracked tickets, ordered by status group then activity."""
    from duct.markdown import extract_table, parse_frontmatter

    cfg = load_config(root)
    ticket_dirs = enumerate_ticket_dirs(root)

    # Get session info
    sessions = discover_sessions()
    ticket_keys = {key for key, _ in ticket_dirs}
    session_tickets: dict[str, int] = {}
    for s in sessions:
        if s.get("alive"):
            tk = match_session_ticket(s, ticket_keys)
            if tk:
                session_tickets[tk] = session_tickets.get(tk, 0) + 1

    # Pre-fetch dirty status for every repo across every ticket in one
    # parallel batch — sequential git invocations were the dominant cost
    # on cold loads.
    all_repo_paths: list[Path] = []
    for _key, _path in ticket_dirs:
        for child in _path.iterdir():
            if child.is_dir() and child.name != "orchestrator" and (child / ".git").exists():
                all_repo_paths.append(child)
    _ticket_dirty_lookup: dict[Path, bool] = {
        p: dirty for p, (_, dirty) in _repo_statuses_parallel(all_repo_paths).items()
    }

    summaries = []
    for key, path in ticket_dirs:
        # Read TICKET.md for metadata
        ticket_md = path / "orchestrator" / "TICKET.md"
        summary = key
        status = ""
        category = ""
        priority = ""

        if ticket_md.exists():
            try:
                meta, body = parse_frontmatter(ticket_md.read_text())
                rows = extract_table(body)
                for row in rows:
                    field = row.get("Field", "")
                    value = row.get("Value", "")
                    if field == "Summary":
                        summary = value
                    elif field == "Status":
                        status = value
                    elif field == "Category":
                        category = value
                    elif field == "Priority":
                        priority = value
            except Exception:
                pass
            # Also try to get summary from the first heading
            try:
                for line in ticket_md.read_text().splitlines():
                    if line.startswith("# ") and ":" in line:
                        summary = line.split(":", 1)[1].strip()
                        break
            except Exception:
                pass

        # Count PRs
        pr_count = 0
        ci_status = ""
        parsed_prs = load_ticket_prs(path)
        pr_count = len(parsed_prs)
        ci_statuses = [p.ci_status for p in parsed_prs if p.ci_status]
        if ci_statuses:
            if all(s in ("passing", "success") for s in ci_statuses):
                ci_status = "passing"
            elif any(s in ("failing", "failure") for s in ci_statuses):
                ci_status = "failing"
            else:
                ci_status = "mixed"

        # Count dirty repos — parallelised across the workspace below.
        dirty_repos = sum(
            1 for child in path.iterdir()
            if child.is_dir() and child.name != "orchestrator"
            and (child / ".git").exists()
            and _ticket_dirty_lookup.get(child, False)
        )

        # Count pending actions
        from duct.actions import get_actions as _get_actions

        actions = _get_actions(root, key)
        pending_count = sum(1 for a in actions if a.status == "pending")

        summaries.append(TicketSummary(
            key=key,
            summary=summary,
            status=status,
            category=category,
            priority=priority,
            pr_count=pr_count,
            ci_status=ci_status,
            active_sessions=session_tickets.get(key, 0),
            dirty_repos=dirty_repos,
            pending_action_count=pending_count,
            path=path,
        ))

    # Sort by status group (focus → other → terminal), then activity desc, then key.
    summaries.sort(key=lambda t: (
        _status_group_rank(t.status, cfg),
        -(t.active_sessions + t.dirty_repos + t.pr_count + t.pending_action_count),
        t.key,
    ))

    return summaries


def _repo_status(repo_path: Path) -> tuple[str, bool]:
    """Return ``(branch, dirty)`` for a single git working tree.

    Combined into one ``git status --porcelain --branch`` invocation so
    we don't pay the git-process startup cost twice per repo. The
    ``--branch`` line at the top of the output gives us the current
    branch; any subsequent line means the tree is dirty.
    """
    try:
        with perf.Timer("git.status_branch", repo=repo_path.name):
            result = subprocess.run(
                ["git", "status", "--porcelain", "--branch"],
                cwd=str(repo_path), capture_output=True, text=True, timeout=5,
            )
    except Exception:
        return "", False

    if result.returncode != 0:
        return "", False

    lines = result.stdout.splitlines()
    branch = ""
    dirty = False
    for line in lines:
        if line.startswith("## "):
            # "## main", "## feature...origin/feature [ahead 2]", etc.
            tail = line[3:]
            branch = tail.split("...", 1)[0].split(" ", 1)[0].strip()
            if branch == "HEAD (no branch)":
                branch = ""
        else:
            # Any non-branch line means there's a dirty entry.
            dirty = True
    return branch, dirty


def _repo_statuses_parallel(
    repo_paths: list[Path], *, max_workers: int = 16,
) -> dict[Path, tuple[str, bool]]:
    """Fetch ``(branch, dirty)`` for many repos in parallel.

    git is single-threaded per invocation but cheap to fork; the gating
    cost on a typical workspace is the per-process startup, not git work.
    Running 16 in parallel collapses N×~50ms sequential calls into
    ~ceil(N/16)×~50ms. The thread cap is conservative so we don't fork
    bomb on workspaces with hundreds of repos.
    """
    if not repo_paths:
        return {}
    workers = max(1, min(max_workers, len(repo_paths)))
    out: dict[Path, tuple[str, bool]] = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_repo_status, p): p for p in repo_paths}
        for fut in cf.as_completed(futures):
            out[futures[fut]] = fut.result()
    return out


def _discover_artifacts(orch_dir: Path) -> list[str]:
    """Discover artifact markdown files in an orchestrator directory.

    Excludes TICKET.md, ORCHESTRATOR.md, and sync-generated files.
    """
    artifacts: list[str] = []
    if not orch_dir.is_dir():
        return artifacts
    for f in sorted(orch_dir.iterdir()):
        if not f.is_file() or f.suffix != ".md":
            continue
        if f.name in ("TICKET.md", "ORCHESTRATOR.md"):
            continue
        try:
            head = f.read_text(errors="ignore")[:200]
            if "source: sync" in head:
                continue
        except OSError:
            continue
        artifacts.append(f.stem)
    return artifacts


def get_ticket_overviews(
    root: Path,
    filter_mode: str = "focus",
    adapter: TerminalAdapter | None = None,
    *,
    _raw_sessions: list[dict] | None = None,
) -> list[TicketOverview]:
    """Ticket overviews with artifact/repo/PR detail, filtered by status.

    filter_mode: "focus" (default) — only focus_statuses
                 "all" — everything except terminal_statuses
                 "closed" — all tickets including terminal

    When `adapter` is provided, per-ticket session summaries are enriched
    with pane-text-derived `mode` and status overrides (see get_sessions).

    `_raw_sessions` is an internal optimisation: when provided, the
    function reuses the caller's already-discovered + pane-enriched
    session list instead of re-running `discover_sessions` and
    `apply_overrides`. Used by `load_initial` to avoid doubling the
    session-discovery cost on TUI startup.
    """
    from duct.actions import get_actions as _get_actions
    from duct.markdown import extract_table, parse_frontmatter
    from duct.session import discover_sessions, match_session_ticket
    from duct.sync.jira import read_identity_cache
    from duct.workspace import enumerate_ticket_dirs

    cfg = load_config(root)
    ticket_dirs = enumerate_ticket_dirs(root)
    my_account_id = read_identity_cache(root)

    if _raw_sessions is None:
        raw_sessions = discover_sessions()
        pane_status.apply_overrides(raw_sessions, adapter)
        _apply_recency(raw_sessions, root)
    else:
        raw_sessions = _raw_sessions

    # Collect every git repo across every ticket up front and fetch all
    # branch/dirty pairs in one parallel batch — historically this was
    # 2 sequential git invocations per repo, which dominates cold load
    # latency on workspaces with 30+ tickets.
    all_repo_paths: list[Path] = []
    for _key, _path in ticket_dirs:
        for child in sorted(_path.iterdir()):
            if child.is_dir() and child.name != "orchestrator" and (child / ".git").exists():
                all_repo_paths.append(child)
    with perf.Timer("api.repo_statuses_parallel", repos=len(all_repo_paths)):
        repo_statuses = _repo_statuses_parallel(all_repo_paths)
    ticket_keys = {key for key, _ in ticket_dirs}
    # Build per-ticket session summaries
    from duct.models import SessionSummary
    ticket_sessions: dict[str, list[SessionSummary]] = {}
    for s in raw_sessions:
        if s.get("alive"):
            tk = match_session_ticket(s, ticket_keys)
            if tk:
                ticket_sessions.setdefault(tk, []).append(SessionSummary(
                    status=s.get("status", "working"),
                    mode=s.get("mode", ""),
                    topic=s.get("topic", ""),
                    last_activity=s.get("last_activity", ""),
                ))

    overviews = []
    for key, path in ticket_dirs:
        orch = path / "orchestrator"

        # Read TICKET.md for metadata
        ticket_md = orch / "TICKET.md"
        summary = key
        status = ""
        category = ""
        priority = ""
        assignee = ""
        assignee_account_id = ""

        if ticket_md.exists():
            try:
                meta, body = parse_frontmatter(ticket_md.read_text())
                rows = extract_table(body)
                for row in rows:
                    field = row.get("Field", "")
                    value = row.get("Value", "")
                    if field == "Summary":
                        summary = value
                    elif field == "Status":
                        status = value
                    elif field == "Category":
                        category = value
                    elif field == "Priority":
                        priority = value
                    elif field == "Assignee":
                        assignee = value
                    elif field == "Assignee ID":
                        assignee_account_id = value
            except Exception:
                pass
            try:
                for line in ticket_md.read_text().splitlines():
                    if line.startswith("# ") and ":" in line:
                        summary = line.split(":", 1)[1].strip()
                        break
            except Exception:
                pass

        # Apply filter
        status_lower = status.lower()
        if filter_mode == "focus":
            if status_lower not in cfg.status.focus_statuses:
                continue
        elif filter_mode == "all":
            if status_lower in cfg.status.terminal_statuses:
                continue
        # "closed" shows everything

        artifacts = _discover_artifacts(orch)

        # Repos — branch + dirty already fetched in parallel above.
        repos = []
        for child in sorted(path.iterdir()):
            if child.is_dir() and child.name != "orchestrator" and (child / ".git").exists():
                branch, dirty = repo_statuses.get(child, ("", False))
                repos.append(RepoSummary(name=child.name, branch=branch, dirty=dirty))

        # PRs
        prs = [
            PRSummary(
                number=p.number,
                repo=p.repo,
                state=p.state,
                ci_status=p.ci_status,
                review_status=p.review_status,
                is_draft=p.is_draft,
            )
            for p in load_ticket_prs(path)
        ]

        # Pending actions
        actions = _get_actions(root, key)
        pending_actions = [a for a in actions if a.status == "pending"]

        # Tasks
        raw_tasks = get_tasks(root, key, ticket_dir=path)
        task_summaries = [TaskSummary(description=t.description, status=t.status) for t in raw_tasks]

        # If we don't yet know the user's accountId (first sync hasn't
        # written the identity cache, or the request failed) treat every
        # ticket as mine — better to under-demote than to demote everything.
        # An empty stored accountId on a ticket also gets the benefit of
        # the doubt for the same reason.
        assigned_to_me = (
            not my_account_id
            or not assignee_account_id
            or assignee_account_id == my_account_id
        )

        overviews.append(TicketOverview(
            key=key,
            summary=summary,
            status=status,
            category=category,
            priority=priority,
            path=path,
            artifacts=artifacts,
            assignee=assignee,
            assignee_account_id=assignee_account_id,
            assigned_to_me=assigned_to_me,
            repos=repos,
            prs=prs,
            sessions=ticket_sessions.get(key, []),
            pending_actions=pending_actions,
            tasks=task_summaries,
        ))

    # Phase grouping: active dev first, post-dev second, pre-dev last.
    # Within each phase, group by status (focus → other → terminal) then
    # by activity (desc) then key.
    _phase_order = {
        "Active Development": 0,
        "Awaiting Action": 1,
        "In Test": 1,
        "Pre-Development": 2,
        "Other": 3,
    }
    overviews.sort(key=lambda t: (
        not t.assigned_to_me,
        _phase_order.get(t.category, 3),
        _status_group_rank(t.status, cfg),
        -(
            len(t.sessions)
            + sum(1 for r in t.repos if r.dirty)
            + sum(1 for p in t.prs if p.state == "open")
            + len(t.pending_actions)
        ),
        t.key,
    ))

    return overviews


def get_ticket_index(root: Path, filter_mode: str = "all") -> list[TicketOverview]:
    """Fast, metadata-only ticket list — no git, sessions, PRs, actions or tasks.

    Reads each ticket's ``TICKET.md`` frontmatter table only, so it returns in
    file-read time even on large workspaces. Used by the Ctrl+K switcher, which
    needs just key/summary/status/category/assigned_to_me. Returned objects are
    ``TicketOverview`` instances with the enriched collections left empty.
    """
    from duct.markdown import extract_table, parse_frontmatter
    from duct.sync.jira import read_identity_cache
    from duct.workspace import enumerate_ticket_dirs

    cfg = load_config(root)
    my_account_id = read_identity_cache(root)
    _phase_order = {
        "Active Development": 0,
        "Awaiting Action": 1,
        "In Test": 1,
        "Pre-Development": 2,
        "Other": 3,
    }

    entries: list[TicketOverview] = []
    for key, path in enumerate_ticket_dirs(root):
        ticket_md = path / "orchestrator" / "TICKET.md"
        summary = key
        status = ""
        category = ""
        assignee = ""
        assignee_account_id = ""
        if ticket_md.exists():
            text = ticket_md.read_text()
            try:
                _meta, body = parse_frontmatter(text)
                for row in extract_table(body):
                    field_name = row.get("Field", "")
                    value = row.get("Value", "")
                    if field_name == "Summary":
                        summary = value
                    elif field_name == "Status":
                        status = value
                    elif field_name == "Category":
                        category = value
                    elif field_name == "Assignee":
                        assignee = value
                    elif field_name == "Assignee ID":
                        assignee_account_id = value
            except Exception:
                pass
            try:
                for line in text.splitlines():
                    if line.startswith("# ") and ":" in line:
                        summary = line.split(":", 1)[1].strip()
                        break
            except Exception:
                pass

        status_lower = status.lower()
        if filter_mode == "focus":
            if status_lower not in cfg.status.focus_statuses:
                continue
        elif filter_mode == "all":
            if status_lower in cfg.status.terminal_statuses:
                continue

        assigned_to_me = (
            not my_account_id
            or not assignee_account_id
            or assignee_account_id == my_account_id
        )
        entries.append(TicketOverview(
            key=key,
            summary=summary,
            status=status,
            category=category,
            priority="",
            path=path,
            artifacts=[],
            assignee=assignee,
            assignee_account_id=assignee_account_id,
            assigned_to_me=assigned_to_me,
        ))

    entries.sort(key=lambda t: (
        not t.assigned_to_me,
        _phase_order.get(t.category, 3),
        t.key,
    ))
    return entries


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo),
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()


def _base_candidates(repo: Path, branch: str) -> list[str]:
    """Plausible fork-point refs for ``branch``, best-known first.

    No single source is reliable across worktrees (measured): the persisted
    base is authoritative but only set on newer worktrees; ``@{upstream}`` is
    often unset (``--no-track``) or points at the branch's own pushed copy; the
    reflog fork point can be a stale ref the branch has long since moved past.
    We collect whatever resolves and let the caller pick the tightest.
    """
    candidates: list[str] = []

    persisted = _git_out(repo, "config", f"branch.{branch}.duct-base")
    if persisted:
        candidates.append(persisted)

    upstream = _git_out(repo, "rev-parse", "--abbrev-ref", "@{upstream}")
    # Skip the branch's own remote copy (origin/<branch>) — useless as a base.
    if upstream and "@{" not in upstream and upstream.split("/", 1)[-1] != branch:
        candidates.append(upstream)

    for line in _git_out(repo, "reflog", "show", branch).splitlines():
        marker = "branch: Created from "
        if marker in line:
            candidates.append(line.split(marker, 1)[1].strip())
            break

    seen: set[str] = set()
    return [
        c for c in candidates
        if c and c != "HEAD" and not (c in seen or seen.add(c))
    ]


def _branch_commits(repo: Path, branch: str) -> list[str]:
    """Oneline commits made on ``branch`` itself (``<base>..HEAD``).

    Picks the base that yields the fewest commits among the resolvable
    candidates — the tightest fork point, which best isolates this branch's own
    work from inherited release history. Falls back to the five most recent
    commits when no fork point resolves.
    """
    def _last_commits() -> list[str]:
        out = _git_out(repo, "log", "--oneline", "-5")
        return [l.strip() for l in out.splitlines() if l.strip()]

    if not branch:
        return _last_commits()

    best_base: str | None = None
    best_count = -1
    for base in _base_candidates(repo, branch):
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{base}..HEAD"],
            cwd=str(repo), capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            continue
        try:
            count = int(result.stdout.strip())
        except ValueError:
            continue
        if best_base is None or count < best_count:
            best_base, best_count = base, count

    if best_base is None:
        return _last_commits()
    out = _git_out(repo, "log", "--oneline", "--max-count=99", f"{best_base}..HEAD")
    return [l.strip() for l in out.splitlines() if l.strip()]


def get_ticket_detail(root: Path, key: str) -> TicketDetail | None:
    """Full detail for a single ticket."""
    from duct.markdown import extract_table, parse_frontmatter

    ticket_dir = resolve_ticket_dir(root, key)
    if not ticket_dir:
        return None

    # Read ticket
    ticket_md = ticket_dir / "orchestrator" / "TICKET.md"
    ticket = Ticket(
        key=key, summary=key, status="", category="", priority="",
        issue_type="", assignee="", url="",
    )
    if ticket_md.exists():
        try:
            meta, body = parse_frontmatter(ticket_md.read_text())
            rows = extract_table(body)
            fields = {}
            for row in rows:
                fields.update(row)
            # Get summary from heading
            summary = key
            for line in ticket_md.read_text().splitlines():
                if line.startswith("# ") and ":" in line:
                    summary = line.split(":", 1)[1].strip()
                    break
            ticket = Ticket(
                key=key,
                summary=summary,
                status=fields.get("Status", ""),
                category=fields.get("Category", ""),
                priority=fields.get("Priority", ""),
                issue_type=fields.get("Type", ""),
                assignee=fields.get("Assignee", ""),
                url=fields.get("URL", ""),
            )
        except Exception:
            pass

    # Read PRs
    prs = load_ticket_prs(ticket_dir)

    # Get repos
    repos: list[RepoStatus] = []
    for child in sorted(ticket_dir.iterdir()):
        if child.is_dir() and child.name != "orchestrator" and (child / ".git").exists():
            branch = ""
            dirty = False
            uncommitted = 0
            recent_commits: list[str] = []
            try:
                result = subprocess.run(
                    ["git", "branch", "--show-current"],
                    cwd=str(child), capture_output=True, text=True, timeout=5,
                )
                branch = result.stdout.strip()
            except Exception:
                pass
            try:
                result = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(child), capture_output=True, text=True, timeout=5,
                )
                lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
                uncommitted = len(lines)
                dirty = uncommitted > 0
            except Exception:
                pass
            try:
                recent_commits = _branch_commits(child, branch)
            except Exception:
                pass
            repos.append(RepoStatus(
                name=child.name,
                path=child,
                branch=branch,
                dirty=dirty,
                uncommitted_changes=uncommitted,
                recent_commits=recent_commits,
            ))

    # Get sessions for this ticket
    all_sessions = discover_sessions()
    _apply_recency(all_sessions, root)
    ticket_keys = {key}
    sessions: list[SessionInfo] = []
    for s in all_sessions:
        matched = match_session_ticket(s, ticket_keys)
        if matched:
            sessions.append(SessionInfo(
                session_id=s.get("session_id", ""),
                pid=s.get("pid"),
                cwd=s.get("cwd", ""),
                ticket_key=key,
                status=s.get("status", "terminated"),
                mode=s.get("mode", ""),
                topic=s.get("topic", ""),
                started_at=s.get("started_at", ""),
                last_activity=s.get("last_activity", ""),
            ))

    # Get actions
    from duct.actions import get_actions as _get_actions

    actions = _get_actions(root, key)

    # Get tasks
    tasks = get_tasks(root, key, ticket_dir=ticket_dir)

    artifacts = _discover_artifacts(ticket_dir / "orchestrator")

    return TicketDetail(
        ticket=ticket,
        prs=prs,
        repos=repos,
        sessions=sessions,
        actions=actions,
        artifacts=artifacts,
        tasks=tasks,
    )


def get_all_prs(
    root: Path, filter_mode: str = "focus",
) -> list[tuple[str, PullRequest]]:
    """All PRs across tracked tickets as (ticket_key, PullRequest) pairs.

    Also includes orphan review PRs from `.review_prs.md` at the workspace
    root (PRs where the current user is a requested/active reviewer but
    which don't match any workspace ticket). These come back with an empty
    string ticket_key so the TUI can render them without a ticket badge.

    Deduplicates by (repo, number) -- keeps the first ticket association.
    """
    from duct.markdown import TICKET_KEY_PATTERN, parse_frontmatter
    from duct.pr import parse_pull_requests_md

    cfg = load_config(root)
    focus_statuses = set(cfg.status.focus_statuses)
    terminal_statuses = set(cfg.status.terminal_statuses)

    seen: set[tuple[str, int]] = set()
    results: list[tuple[str, PullRequest]] = []

    for key, path in enumerate_ticket_dirs(root):
        ticket_md = path / "orchestrator" / "TICKET.md"
        status = ""
        if ticket_md.exists():
            try:
                _, body = parse_frontmatter(ticket_md.read_text())
                for line in body.splitlines():
                    if line.strip().startswith("| Status |"):
                        parts = line.split("|")
                        if len(parts) >= 3:
                            status = parts[2].strip().lower()
                        break
            except Exception:
                pass

        if filter_mode == "focus" and status not in focus_statuses:
            continue
        if filter_mode == "all" and status in terminal_statuses:
            continue

        for pr in load_ticket_prs(path):
            dedup_key = (pr.repo, pr.number)
            if dedup_key not in seen:
                seen.add(dedup_key)
                results.append((key, pr))

    # Orphan review PRs (.duct/review_prs.md)
    review_md = paths.review_prs_file(root)
    if review_md.exists():
        try:
            for pr in parse_pull_requests_md(review_md.read_text(encoding="utf-8")):
                dedup_key = (pr.repo, pr.number)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                # Extract a display ticket key from the PR title if possible
                # so the badge still carries context. Not clickable.
                match = TICKET_KEY_PATTERN.search(pr.title)
                display_key = match.group(0) if match else ""
                results.append((display_key, pr))
        except Exception:
            pass

    return results


def get_review_prs(root: Path) -> list[PullRequest]:
    """Open PRs that need the current user's review (personally or via a team).

    Reads the complete review set written by the GitHub sync to
    ``.duct/review_prs.md``. This is the authoritative "needs my review" list and
    is independent of which tickets are tracked. Sorted by `updated_at` desc.
    """
    from duct.pr import parse_pull_requests_md

    review_md = paths.review_prs_file(root)
    if not review_md.exists():
        return []
    try:
        prs = parse_pull_requests_md(review_md.read_text(encoding="utf-8"))
    except Exception:
        return []
    prs = [p for p in prs if p.state == "open"]
    prs.sort(key=lambda p: p.updated_at, reverse=True)
    return prs


def get_sessions(
    root: Path,
    adapter: TerminalAdapter | None = None,
) -> list[SessionInfo]:
    """All Claude Code sessions on the machine, linked and unlinked.

    When `adapter` is provided (typically the TUI's wezterm adapter), the
    returned sessions are enriched from pane-text inspection: `mode` is set
    to `plan`/`default` and `status` is corrected against what Claude is
    actually rendering on screen. Without an adapter, sessions fall back to
    transcript-only inference and `mode` is empty.
    """
    raw_sessions = discover_sessions()
    pane_status.apply_overrides(raw_sessions, adapter)
    _apply_recency(raw_sessions, root)
    return _build_session_infos(raw_sessions, root)


def _apply_recency(raw_sessions: list[dict], root: Path) -> None:
    """Rewrite ``ready`` statuses to ``done`` / ``stale`` per the workspace config thresholds."""
    cfg = load_config(root)
    apply_recency_decoration(
        raw_sessions,
        done_window_seconds=cfg.session_status.done_window_seconds,
        stale_after_seconds=cfg.session_status.stale_after_seconds,
    )


def _build_session_infos(raw_sessions: list[dict], root: Path) -> list[SessionInfo]:
    """Project enriched raw session dicts into `SessionInfo` objects."""
    ticket_keys = {key for key, _ in enumerate_ticket_dirs(root)}
    result = []
    for s in raw_sessions:
        ticket = match_session_ticket(s, ticket_keys)
        result.append(SessionInfo(
            session_id=s.get("session_id", ""),
            pid=s.get("pid"),
            cwd=s.get("cwd", ""),
            ticket_key=ticket,
            status=s.get("status", "terminated"),
            mode=s.get("mode", ""),
            topic=s.get("topic", ""),
            started_at=s.get("started_at", ""),
            last_activity=s.get("last_activity", ""),
        ))
    return result


def load_initial(
    root: Path,
    adapter: TerminalAdapter | None = None,
    filter_mode: str = "focus",
) -> tuple[list[SessionInfo], list[TicketOverview]]:
    """Single-pass startup load for the TUI.

    Replaces the historical three-call pattern (get_tickets +
    get_sessions + get_ticket_overviews) which independently re-walked the
    workspace and re-discovered sessions for each consumer. Session
    discovery and pane inspection happen exactly once; the result is
    threaded through to ticket-overview enrichment.

    Returns `(sessions, ticket_overviews)`. Callers that need ticket
    summaries should derive them from the overviews via
    ``TicketSummary.from_overview``.
    """
    from duct.terminal import _wezterm_list_panes

    # Pre-warm the wezterm pane-list cache so every downstream consumer
    # (apply_overrides, find_pane_for_pid, get_terminal_title) hits the
    # cache instead of racing the 1-call-per-second TTL window.
    if adapter is not None and getattr(adapter, "name", "") == "wezterm":
        with perf.Timer("api.prewarm_pane_list"):
            _wezterm_list_panes()

    with perf.Timer("api.discover_sessions"):
        raw_sessions = discover_sessions()
    with perf.Timer("api.apply_overrides", n=len(raw_sessions)):
        pane_status.apply_overrides(raw_sessions, adapter)
    _apply_recency(raw_sessions, root)

    sessions = _build_session_infos(raw_sessions, root)
    with perf.Timer("api.ticket_overviews", filter_mode=filter_mode):
        overviews = get_ticket_overviews(
            root,
            filter_mode=filter_mode,
            adapter=adapter,
            _raw_sessions=raw_sessions,
        )
    return sessions, overviews


def get_sync_status(root: Path) -> list[SourceStatus]:
    """Staleness info for all sync sources."""
    cfg = load_config(root)
    intervals = {
        "jira": cfg.sync_intervals.jira,
        "github": cfg.sync_intervals.github,
        "sessions": cfg.sync_intervals.sessions,
        "workspace": cfg.sync_intervals.workspace,
        "ci": cfg.sync_intervals.ci,
    }
    from duct.sync.base import SyncCoordinator

    coordinator = SyncCoordinator(root, intervals)
    statuses = coordinator.all_source_statuses()

    return [
        SourceStatus(
            name=s.name,
            last_synced=s.last_sync_iso if s.last_sync > 0 else None,
            stale=s.is_stale,
            interval_seconds=s.interval,
        )
        for s in statuses
    ]


def _import(module: str, attr: str):
    """Import *attr* from *module* lazily (keeps sync deps off the hot path)."""
    return getattr(importlib.import_module(module), attr)


def trigger_sync(root: Path, force: bool = False) -> list[SyncResult]:
    """Run sync coordinator. Returns results for sources that ran."""
    cfg = load_config(root)
    intervals = {
        "jira": cfg.sync_intervals.jira,
        "github": cfg.sync_intervals.github,
        "sessions": cfg.sync_intervals.sessions,
        "workspace": cfg.sync_intervals.workspace,
        "ci": cfg.sync_intervals.ci,
        "claude_md": cfg.sync_intervals.claude_md,
    }
    from duct.sync.base import SyncCoordinator

    coordinator = SyncCoordinator(root, intervals)

    def _build(name: str, factory):
        """Append a source, logging (not swallowing) any build failure.

        Auth gaps are the common case for the background daemon — its launchd
        environment differs from a shell — so a skipped source must be visible
        in the daemon log rather than vanishing silently.
        """
        try:
            sources.append(factory())
        except AuthError as exc:
            print(f"[sync] {name} skipped: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — one bad source can't abort the run
            print(f"[sync] {name} unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)

    sources: list = []
    _build("sessions", lambda: _import("duct.sync.sessions", "SessionSync")())
    _build("workspace", lambda: _import("duct.sync.workspace_sync", "WorkspaceSync")())
    _build("ci", lambda: _import("duct.sync.ci", "CISync")())
    _build("jira", lambda: _import("duct.sync.jira", "JiraSync")(
        domain=cfg.jira_domain,
        email=jira_email(),
        token=jira_token(),
        jql=cfg.jira_jql,
        sandbox=cfg.sandbox,
    ))
    _build("github", lambda: _import("duct.sync.github", "GitHubSync")(
        token=gh_token(), github_username=github_username(),
    ))
    # claude_md must run last: it stitches together the artifacts the other
    # sources write into each ticket's CLAUDE.md.
    _build("claude_md", lambda: _import("duct.sync.claude_md", "ClaudeMdSync")())

    return coordinator.run(sources, force=force)


def post_jira_comment(root: Path, key: str, body: str) -> None:
    """Post a comment to a Jira issue. Raises on auth or API failure."""
    cfg = load_config(root)
    from duct.sync.jira import JiraSync

    jira = JiraSync(
        domain=cfg.jira_domain,
        email=jira_email(),
        token=jira_token(),
        jql="",
    )
    jira.post_comment(key, body)


def discover_repos(root: Path) -> list[tuple[str, Path]]:
    """Return sorted (name, path) pairs of git repos under configured repoPaths."""
    from duct.cli.workspace_cmd import discover_repos as _discover_repos
    return _discover_repos(load_config(root))


def get_repo_candidates(root: Path):
    """Return local + remote (from githubOrgs) repo candidates."""
    from duct.cli.workspace_cmd import list_repo_candidates
    return list_repo_candidates(load_config(root))


def list_repo_branches(
    root: Path, repo_name: str, *, slug: str | None = None,
) -> list[str]:
    """Return deduplicated branch names for the named repo, or [] if not found.

    For a locally-cloned repo, this fetches (with prune) from origin first so
    the list reflects the remote — newly-pushed branches appear and deleted
    ones drop out — degrading gracefully when offline.

    When ``slug`` is provided and the repo is not cloned locally, branches
    are fetched via ``gh api`` so a remote-only repo can still populate the
    base-branch picker before being cloned.
    """
    from duct.cli.workspace_cmd import find_repo, list_branches, list_remote_branches
    repo_path = find_repo(load_config(root), repo_name)
    if repo_path:
        return list_branches(repo_path)
    if slug:
        return list_remote_branches(slug)
    return []


def suggest_feature_branch(root: Path, key: str) -> str:
    """Default feature branch name for a ticket, matching `duct workspace add-repo`."""
    ticket_dir = resolve_ticket_dir(root, key)
    if not ticket_dir:
        raise WorkspaceError(f"No workspace found for {key}.")
    summary_slug = ticket_dir.name[len(key) + 1:]
    return branch_name(key, summary_slug, read_issue_type(ticket_dir))


def add_repo(
    root: Path,
    key: str,
    repo_name: str,
    base_branch: str,
    feature_branch: str,
    *,
    clone_from: str | None = None,
) -> Path:
    """Create a worktree for ``repo_name`` under the ticket's workspace.

    When ``clone_from`` is set and the repo is not already present in one of
    the configured ``repoPaths``, the repo is cloned into the first repoPath
    (via ``gh repo clone`` or ``git clone``) before the worktree is created.
    """
    from duct.cli.workspace_cmd import clone_repo, find_repo

    cfg = load_config(root)
    ticket_dir = resolve_ticket_dir(root, key)
    if not ticket_dir:
        raise WorkspaceError(f"No workspace found for {key}.")
    repo_path = find_repo(cfg, repo_name)
    if not repo_path and clone_from:
        if not cfg.repo_paths:
            raise WorkspaceError(
                "Cannot clone: no repoPaths configured. "
                "Add a search path with `duct config add-repo-path <dir>`."
            )
        dest_parent = cfg.repo_paths[0]
        if not dest_parent.is_dir():
            raise WorkspaceError(
                f"Cannot clone: configured repoPath does not exist: {dest_parent}"
            )
        cloned = clone_repo(clone_from, dest_parent)
        if cloned.name != repo_name:
            raise WorkspaceError(
                f"Clone produced directory '{cloned.name}' but repo name was "
                f"'{repo_name}'. Check that --clone-from and repo_name match."
            )
        repo_path = find_repo(cfg, repo_name)
    if not repo_path:
        raise WorkspaceError(f"Repository '{repo_name}' not found in repoPaths.")

    return create_worktree(
        ticket_dir=ticket_dir,
        repo_path=repo_path,
        repo_name=repo_name,
        base_branch=base_branch,
        feature_branch=feature_branch,
        sandbox=cfg.sandbox,
    )


def launch_session(
    root: Path,
    key: str,
    repo: str | None = None,
    prompt: str | None = None,
    extra_args: list[str] | None = None,
) -> int:
    """Launch a Claude Code session for a ticket. Returns PID."""
    return _launch_session(root, key, repo=repo, prompt=prompt, extra_args=extra_args)


def launch_session_in_dir(
    cwd: Path,
    prompt: str | None = None,
    add_dir: Path | None = None,
    extra_args: list[str] | None = None,
    skip_permissions: bool = False,
) -> int:
    """Launch a Claude Code session in an arbitrary directory. Returns PID."""
    return _launch_session_in_dir(
        cwd,
        prompt=prompt,
        add_dir=add_dir,
        extra_args=extra_args,
        skip_permissions=skip_permissions,
    )


def spawn_session(
    adapter: TerminalAdapter,
    root: Path,
    key: str,
    repo: str | None = None,
    prompt: str | None = None,
    submit_prompt: bool = True,
) -> int | None:
    """Spawn a Claude session in a new terminal pane. Returns pane ID.

    Use this instead of launch_session when calling from a TUI — it spawns
    via the terminal adapter rather than bare Popen, avoiding terminal conflicts.

    When ``prompt`` is provided, the prompt is sent to the new pane via
    ``adapter.send_text`` after a short delay (so claude's TUI is ready to
    receive the bracketed paste). Claude hangs silently when handed a long
    multi-line positional prompt argv on startup, so we never pass it that
    way. With ``submit_prompt=True`` (the default), an Enter keystroke is
    sent after the paste so claude starts processing immediately.
    """
    cmd, cwd, prompt_to_send = _prepare_session(root, key, repo=repo, prompt=prompt)
    pane_id = adapter.spawn_pane(cwd, cmd)
    if pane_id is None or not prompt_to_send:
        return pane_id
    _send_prompt_to_pane(adapter, pane_id, prompt_to_send, submit_prompt)
    return pane_id


def _send_prompt_to_pane(
    adapter: TerminalAdapter,
    pane_id: int,
    prompt: str,
    submit: bool,
) -> None:
    """Paste ``prompt`` into ``pane_id`` once claude's TUI is up.

    Waits up to ~3s for the claude banner to appear by polling
    ``get_pane_text``. If it never appears (e.g. claude failed to start)
    the send-text call is still attempted — the pane will queue keystrokes
    in its line-discipline buffer if it's a plain shell, so nothing is
    lost. After the paste, an Enter is optionally sent to submit.
    """
    import time

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        text = adapter.get_pane_text(pane_id) or ""
        if "Claude Code" in text:
            break
        time.sleep(0.1)

    adapter.send_text(pane_id, prompt, paste=True)
    if submit:
        adapter.send_text(pane_id, "\r", paste=False)


def stop_session(pid: int) -> bool:
    """Send SIGTERM to a session. Returns True if signal sent."""
    return _stop_session(pid)


def focus_session(pid: int) -> bool:
    """Switch terminal focus to the tab running the given PID."""
    tty = get_tty(pid)
    if not tty:
        return False
    return focus_terminal_tab(tty)


def dock_session(
    adapter: TerminalAdapter,
    tui_pane_id: int,
    session_pid: int,
    current_docked_pane: int | None = None,
) -> int | None:
    """Dock a session's terminal pane next to the TUI pane.

    Undocks any currently docked pane first, then finds and docks the new one.
    Returns the newly docked pane ID, or None if the session pane wasn't found.
    """
    if current_docked_pane is not None:
        adapter.undock_pane(current_docked_pane)

    session_pane = adapter.find_pane_for_pid(session_pid)
    if session_pane is None:
        return None

    if adapter.dock_pane(tui_pane_id, session_pane):
        return session_pane
    return None


def undock_session(adapter: TerminalAdapter, pane_id: int) -> bool:
    """Undock a session pane (return to its own tab). Does not change focus."""
    return adapter.undock_pane(pane_id)


def get_session_preview(adapter: TerminalAdapter, session_pid: int) -> str | None:
    """Get a terminal text snapshot (with ANSI escapes) for a session.

    Serves any cached pane text — fresh or stale — from the cache that
    ``pane_status.apply_overrides`` populates. Stale text is fine for
    preview, and a live ``wezterm cli get-text`` queues behind the next
    refresh batch on the wezterm IPC daemon, producing visible lag.
    Falls through to a live capture only for sessions that have no cache
    entry at all (e.g. a brand-new session that appeared after launch).
    """
    cached = pane_status.get_any_cached_pane_text(session_pid)
    if cached is not None:
        return cached
    pane_id = adapter.find_pane_for_pid(session_pid)
    if pane_id is None:
        return None
    return adapter.get_pane_text(pane_id)

"""Domain models for duct."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Comment:
    """A Jira issue comment."""

    author: str
    created: str  # ISO 8601
    body: str


@dataclass(frozen=True)
class Ticket:
    """A Jira issue."""

    key: str
    summary: str
    status: str
    category: str
    priority: str
    issue_type: str
    assignee: str
    url: str
    assignee_account_id: str = ""
    description: str = ""
    epic_key: str | None = None
    sprint: str | None = None
    customer_name: str | None = None
    fix_versions: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    transitions: list[str] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    linked_issues: list[str] = field(default_factory=list)
    subtasks: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Reviewer:
    """A pull request reviewer."""

    login: str
    state: str


@dataclass(frozen=True)
class PRComment:
    """A pull request comment."""

    author: str
    created_at: str  # ISO 8601
    body: str
    path: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class PullRequest:
    """A GitHub pull request."""

    number: int
    title: str
    repo: str
    state: str  # "open" | "closed" | "merged"
    author: str
    is_draft: bool
    review_status: str
    ci_status: str
    url: str
    created_at: str  # ISO 8601
    updated_at: str  # ISO 8601
    branch: str = ""
    reviewers: list[Reviewer] = field(default_factory=list)
    comments: list[PRComment] = field(default_factory=list)
    # Reviewers requested but who haven't posted a review yet. Distinct from
    # `reviewers` (which is people who have actually reviewed).
    requested_reviewers: list[str] = field(default_factory=list)
    # GitHub's mergeable enum: "MERGEABLE" | "CONFLICTING" | "UNKNOWN".
    mergeable: str = "UNKNOWN"
    # Author's GitHub avatar URL (populated by the GitHub GraphQL sync).
    # Optional so older cached payloads still deserialise.
    author_avatar_url: str | None = None
    # True when GitHub's review-requested search returned this PR, i.e. it needs
    # review from the current user — either personally or via a team they belong
    # to. Captured from the search query's provenance rather than re-derived from
    # `requested_reviewers`, which can't see team requests.
    needs_my_review: bool = False
    # Teams requested as reviewers, as "org/slug" strings. Display context for
    # why a PR needs review (e.g. "via @org/claims-dev").
    requested_teams: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SyncResult:
    """Result of a sync operation."""

    source: str
    tickets_synced: int
    duration_seconds: float
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TicketSummary:
    """Summary view of a ticket for TUI display."""

    key: str
    summary: str
    status: str
    category: str
    priority: str  # Jira's intrinsic priority field (e.g. "Major"), not a duct concept.
    pr_count: int
    ci_status: str  # "passing" | "failing" | "mixed" | ""
    active_sessions: int
    dirty_repos: int
    pending_action_count: int
    path: Path


@dataclass(frozen=True)
class RepoStatus:
    """Git repository status within a ticket workspace."""

    name: str
    path: Path
    branch: str
    dirty: bool
    uncommitted_changes: int
    recent_commits: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SessionInfo:
    """Claude Code session information.

    `status` and `mode` are orthogonal dimensions: a session can be `working`
    while `mode='plan'` (Claude thinking inside plan mode), or `ready` while
    `mode='plan'` (plan presented, awaiting user). `mode` is sourced from
    wezterm pane inspection and is empty when unavailable.
    """

    session_id: str
    pid: int | None
    cwd: str
    ticket_key: str | None
    status: str  # "working" | "waiting" | "ready" | "done" | "stale" | "terminated"
    mode: str    # "plan" | "default" | ""  (empty = unknown / not inspected)
    topic: str
    started_at: str  # ISO 8601
    last_activity: str  # ISO 8601


@dataclass
class Action:
    """An orchestrator action (concrete or prompt dispatch)."""

    id: str
    type: str  # "concrete" | "prompt"
    description: str
    status: str  # "pending" | "approved" | "rejected" | "withdrawn"
    detail: dict = field(default_factory=dict)
    created_at: str = ""  # ISO 8601
    resolved_at: str | None = None
    feedback: str | None = None  # user rationale captured on reject


@dataclass
class Task:
    """A per-ticket task (local checklist item)."""

    id: str
    description: str
    status: str  # "todo" | "done"
    created_at: str  # ISO 8601
    completed_at: str | None = None
    position: int = 0
    source: str = "local"  # "local" | "orchestrator"


@dataclass(frozen=True)
class TaskSummary:
    """Lightweight task info for overview cards."""

    description: str
    status: str  # "todo" | "done"


@dataclass(frozen=True)
class SourceStatus:
    """Staleness info for a sync source (TUI-friendly wrapper)."""

    name: str
    last_synced: str | None  # ISO 8601
    stale: bool
    interval_seconds: int


@dataclass(frozen=True)
class TicketDetail:
    """Full detail for a single ticket."""

    ticket: Ticket
    prs: list[PullRequest]
    repos: list[RepoStatus]
    sessions: list[SessionInfo]
    actions: list[Action]
    artifacts: list[str] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)


@dataclass(frozen=True)
class RepoSummary:
    """Lightweight git repo status."""
    name: str
    branch: str
    dirty: bool


@dataclass(frozen=True)
class PRSummary:
    """Lightweight pull request info."""
    number: int
    repo: str
    state: str
    ci_status: str
    review_status: str
    is_draft: bool = False


@dataclass(frozen=True)
class SessionSummary:
    """Lightweight session info for overview cards."""
    status: str  # "working" | "waiting" | "ready" | "done" | "stale" | "terminated"
    mode: str  # "plan" | "default" | "" (empty = unknown / not inspected)
    topic: str
    last_activity: str  # ISO 8601


@dataclass(frozen=True)
class ActivityEvent:
    """A single event on the user's activity timeline.

    Sourced from one of several providers (Jira, GitHub, git, Claude, Outlook)
    and persisted as one JSONL line under `{workspace}/.activity/YYYY-MM-DD.jsonl`.
    `event_id` is a deterministic `{source}:{source_specific_key}` used to
    dedupe re-runs of the gather.
    """

    event_id: str
    timestamp: str  # ISO 8601 UTC
    source: str  # "jira" | "github" | "git" | "claude" | "outlook"
    event_type: str  # "comment" | "status_change" | "commit" | "pr_opened" | ...
    actor: str
    summary: str
    ticket_key: str | None = None
    url: str | None = None
    duration_seconds: int | None = None
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TicketOverview:
    """Enriched ticket summary for TUI card display."""
    key: str
    summary: str
    status: str
    category: str
    priority: str  # Jira's intrinsic priority field, kept for TUI header display.
    path: Path
    artifacts: list[str]
    assignee: str = ""
    assignee_account_id: str = ""
    assigned_to_me: bool = True
    repos: list[RepoSummary] = field(default_factory=list)
    prs: list[PRSummary] = field(default_factory=list)
    sessions: list[SessionSummary] = field(default_factory=list)
    pending_actions: list[Action] = field(default_factory=list)
    tasks: list[TaskSummary] = field(default_factory=list)
